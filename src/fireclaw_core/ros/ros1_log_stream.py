from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib import import_module
import logging
from queue import Empty, Full, Queue
import re
import threading
import time
from typing import Any


logger = logging.getLogger(__name__)


_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_ROS_LEVEL_NAMES = {
    1: "DEBUG",
    2: "INFO",
    4: "WARN",
    8: "ERROR",
    16: "FATAL",
}


@dataclass(frozen=True)
class Ros1LogStreamSnapshot:
    schema_version: int
    status: str
    reason_code: str
    message: str
    topic: str
    minimum_level: str
    received_count: int
    emitted_count: int
    dropped_count: int
    started_at: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Ros1LogStream:
    """Non-blocking bridge from ROS1 ``/rosout_agg`` to FireClaw events.

    The ROS callback only normalizes a bounded record and performs
    ``put_nowait``. Persistence and event publication happen on a dedicated
    worker so ROS logging can never extend an Agent planning/tool call.
    """

    def __init__(
        self,
        event_sink: Callable[[dict[str, Any]], None],
        *,
        context_provider: Callable[[], Mapping[str, Any] | None] | None = None,
        topic: str = "/rosout_agg",
        minimum_level: int = 4,
        queue_capacity: int = 512,
        maximum_message_characters: int = 4000,
        rospy_loader: Callable[[], Any] | None = None,
        log_message_loader: Callable[[], Any] | None = None,
    ) -> None:
        if not callable(event_sink):
            raise TypeError("event_sink must be callable")
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("ROS log topic must be non-empty")
        if minimum_level not in _ROS_LEVEL_NAMES:
            raise ValueError("minimum_level must be a ROS log level")
        if isinstance(queue_capacity, bool) or queue_capacity <= 0:
            raise ValueError("queue_capacity must be a positive integer")
        if (
            isinstance(maximum_message_characters, bool)
            or maximum_message_characters <= 0
        ):
            raise ValueError(
                "maximum_message_characters must be a positive integer"
            )

        self.event_sink = event_sink
        self.context_provider = context_provider
        self.topic = topic.strip()
        self.minimum_level = int(minimum_level)
        self.queue_capacity = int(queue_capacity)
        self.maximum_message_characters = int(maximum_message_characters)
        self._rospy_loader = rospy_loader or (lambda: import_module("rospy"))
        self._log_message_loader = log_message_loader or (
            lambda: import_module("rosgraph_msgs.msg").Log
        )
        self._queue: Queue[dict[str, Any]] = Queue(maxsize=self.queue_capacity)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._subscriber: Any | None = None
        self._received_count = 0
        self._emitted_count = 0
        self._dropped_count = 0
        self._pending_dropped_count = 0
        self._started_at: str | None = None
        now = _timestamp()
        self._snapshot = Ros1LogStreamSnapshot(
            schema_version=1,
            status="not_started",
            reason_code="ros_log_stream_not_started",
            message="ROS log streaming has not started.",
            topic=self.topic,
            minimum_level=_ROS_LEVEL_NAMES[self.minimum_level],
            received_count=0,
            emitted_count=0,
            dropped_count=0,
            started_at=None,
            updated_at=now,
        )

    def start(self) -> Ros1LogStreamSnapshot:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return self._snapshot

        try:
            rospy = self._rospy_loader()
            if not _rospy_initialized(rospy):
                return self._set_snapshot(
                    status="degraded",
                    reason_code="ros_node_not_initialized",
                    message=(
                        "ROS log streaming requires the process-local ROS node "
                        "to be initialized first."
                    ),
                )
            log_message_type = self._log_message_loader()
        except Exception as exc:
            return self._set_snapshot(
                status="degraded",
                reason_code="ros_log_client_unavailable",
                message=f"ROS log client is unavailable: {exc}",
            )

        self._stop_event.clear()
        started_at = _timestamp()
        worker = threading.Thread(
            target=self._run,
            daemon=True,
            name="fireclaw-ros1-log-stream",
        )
        with self._lock:
            self._worker = worker
            self._started_at = started_at
        worker.start()
        try:
            subscriber = rospy.Subscriber(
                self.topic,
                log_message_type,
                self._on_message,
                queue_size=min(self.queue_capacity, 100),
            )
        except Exception as exc:
            self._stop_event.set()
            worker.join(timeout=1.0)
            with self._lock:
                self._worker = None
            return self._set_snapshot(
                status="degraded",
                reason_code="ros_log_subscription_failed",
                message=f"Failed to subscribe to {self.topic}: {exc}",
            )

        with self._lock:
            self._subscriber = subscriber
        return self._set_snapshot(
            status="ready",
            reason_code="ros_log_stream_ready",
            message=(
                f"Streaming {_ROS_LEVEL_NAMES[self.minimum_level]} and higher "
                f"ROS logs from {self.topic}."
            ),
        )

    def stop(self) -> Ros1LogStreamSnapshot:
        with self._lock:
            subscriber = self._subscriber
            worker = self._worker
            self._subscriber = None
        if subscriber is not None:
            try:
                subscriber.unregister()
            except Exception:
                logger.debug("Failed to unregister ROS log subscriber", exc_info=True)
        self._stop_event.set()
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)
        with self._lock:
            self._worker = None
        return self._set_snapshot(
            status="stopped",
            reason_code="ros_log_stream_stopped",
            message="ROS log streaming is stopped.",
        )

    def snapshot(self) -> Ros1LogStreamSnapshot:
        with self._lock:
            return self._snapshot

    def flush(
        self,
        *,
        timeout_seconds: float = 0.2,
        quiet_period_seconds: float = 0.02,
    ) -> bool:
        """Wait briefly for already-arriving logs before terminal publication.

        This is an observability barrier used only after the Agent/tool has
        returned. It never runs in the ROS callback or the planning/action
        path. The short quiet period covers rosout delivery immediately after
        an action server reports its terminal result.
        """

        timeout = max(0.0, float(timeout_seconds))
        quiet_period = max(0.0, float(quiet_period_seconds))
        deadline = time.monotonic() + timeout
        stable_received_count: int | None = None
        stable_since: float | None = None
        while True:
            now = time.monotonic()
            with self._lock:
                received_count = self._received_count
                pending_count = max(
                    0,
                    received_count - self._dropped_count - self._emitted_count,
                )
            idle = pending_count == 0
            if idle:
                if stable_received_count != received_count:
                    stable_received_count = received_count
                    stable_since = now
                elif stable_since is not None and now - stable_since >= quiet_period:
                    return True
            else:
                stable_received_count = None
                stable_since = None
            remaining = deadline - now
            if remaining <= 0:
                return False
            time.sleep(min(0.005, remaining))

    def _on_message(self, message: Any) -> None:
        try:
            level = int(getattr(message, "level", 0))
        except (TypeError, ValueError):
            return
        if level < self.minimum_level:
            return

        context: dict[str, Any] = {}
        if self.context_provider is not None:
            try:
                raw_context = self.context_provider()
                if isinstance(raw_context, Mapping):
                    context = dict(raw_context)
            except Exception:
                logger.debug("Failed to capture ROS log task context", exc_info=True)

        record = {
            "schema_version": 1,
            "severity": _ROS_LEVEL_NAMES.get(level, f"LEVEL_{level}"),
            "level": level,
            "node": _bounded_text(getattr(message, "name", "unknown"), 256),
            "message": _bounded_text(
                getattr(message, "msg", ""),
                self.maximum_message_characters,
                preserve_newlines=True,
            ),
            "source_topic": self.topic,
            "ros_timestamp": _ros_timestamp(message),
            "received_at": _timestamp(),
            "file": _bounded_text(getattr(message, "file", ""), 512),
            "function": _bounded_text(getattr(message, "function", ""), 256),
            "line": _optional_int(getattr(message, "line", None)),
            "topics": _bounded_topics(getattr(message, "topics", None)),
            "context": context,
        }
        with self._lock:
            self._received_count += 1
        try:
            self._queue.put_nowait(record)
        except Full:
            with self._lock:
                self._dropped_count += 1
                self._pending_dropped_count += 1
                self._refresh_snapshot_locked()

    def _run(self) -> None:
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                record = self._queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                with self._lock:
                    dropped_before = self._pending_dropped_count
                    self._pending_dropped_count = 0
                if dropped_before:
                    record["dropped_before"] = dropped_before
                self.event_sink(record)
                with self._lock:
                    self._emitted_count += 1
            except Exception:
                logger.warning("Failed to emit ROS log event", exc_info=True)
            finally:
                with self._lock:
                    self._refresh_snapshot_locked()
                self._queue.task_done()

    def _set_snapshot(
        self,
        *,
        status: str,
        reason_code: str,
        message: str,
    ) -> Ros1LogStreamSnapshot:
        with self._lock:
            self._snapshot = Ros1LogStreamSnapshot(
                schema_version=1,
                status=status,
                reason_code=reason_code,
                message=message,
                topic=self.topic,
                minimum_level=_ROS_LEVEL_NAMES[self.minimum_level],
                received_count=self._received_count,
                emitted_count=self._emitted_count,
                dropped_count=self._dropped_count,
                started_at=self._started_at,
                updated_at=_timestamp(),
            )
            return self._snapshot

    def _refresh_snapshot_locked(self) -> None:
        self._snapshot = Ros1LogStreamSnapshot(
            schema_version=self._snapshot.schema_version,
            status=self._snapshot.status,
            reason_code=self._snapshot.reason_code,
            message=self._snapshot.message,
            topic=self.topic,
            minimum_level=_ROS_LEVEL_NAMES[self.minimum_level],
            received_count=self._received_count,
            emitted_count=self._emitted_count,
            dropped_count=self._dropped_count,
            started_at=self._started_at,
            updated_at=_timestamp(),
        )


def _rospy_initialized(rospy: Any) -> bool:
    try:
        return bool(rospy.core.is_initialized())
    except Exception:
        return False


def _bounded_text(
    value: Any,
    maximum_characters: int,
    *,
    preserve_newlines: bool = False,
) -> str:
    text = _ANSI_ESCAPE_RE.sub("", str(value or ""))
    normalized: list[str] = []
    for character in text:
        code = ord(character)
        if character == "\n" and preserve_newlines:
            normalized.append(character)
        elif character == "\t":
            normalized.append(" ")
        elif code < 32 or code == 127:
            normalized.append(" ")
        else:
            normalized.append(character)
    result = "".join(normalized).strip()
    if len(result) <= maximum_characters:
        return result
    return result[: max(0, maximum_characters - 1)] + "…"


def _bounded_topics(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [_bounded_text(item, 256) for item in value[:32]]


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _ros_timestamp(message: Any) -> float | None:
    stamp = getattr(getattr(message, "header", None), "stamp", None)
    if stamp is None:
        return None
    try:
        return float(stamp.to_sec())
    except Exception:
        pass
    try:
        return float(stamp.secs) + float(stamp.nsecs) / 1_000_000_000.0
    except Exception:
        return None


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
