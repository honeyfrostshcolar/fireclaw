"""Unified event schema, in-process event bus, and telemetry tracker for FireClaw realtime streams.

Adapted from OpenClaw's agent-events.ts (in-process pub/sub with run-scoped sequencing)
and diagnostic-events.ts (telemetry metrics). Uses SSE text/event-stream wire format.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# StreamEvent — unified envelope
# ---------------------------------------------------------------------------

@dataclass
class StreamEvent:
    """Unified event envelope for all FireClaw realtime streams.

    Every event flowing through the system — task lifecycle, action feedback,
    mission transitions, heartbeat, telemetry — uses this schema.
    """
    event_type: str
    source: str
    event_id: str = field(default_factory=lambda: f"evt-{uuid4().hex}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sequence: int = 0
    mission_id: str | None = None
    robot_id: str | None = None
    task_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "timestamp": self.timestamp,
            "sequence": self.sequence,
            "mission_id": self.mission_id,
            "robot_id": self.robot_id,
            "task_id": self.task_id,
            "payload": self.payload,
        }

    def to_sse_format(self) -> str:
        """Format as SSE text/event-stream wire format: 'event: <type>\nid: <sequence>\ndata: <json>\n\n'."""
        return f"event: {self.event_type}\nid: {self.sequence}\ndata: {json.dumps(self.to_dict(), ensure_ascii=False)}\n\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StreamEvent:
        return cls(
            event_type=data["event_type"],
            source=data["source"],
            event_id=data.get("event_id", f"evt-{uuid4().hex}"),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            sequence=data.get("sequence", 0),
            mission_id=data.get("mission_id"),
            robot_id=data.get("robot_id"),
            task_id=data.get("task_id"),
            payload=data.get("payload", {}),
        )


def stream_event_from_ledger_record(
    record: dict[str, Any],
    *,
    source: str,
    robot_id: str | None = None,
) -> StreamEvent:
    """Convert a ledger event record to a StreamEvent.

    Bridges the EventLedger's dict-based schema to the unified StreamEvent
    envelope used by EventBus and TelemetryTracker.
    """
    kwargs: dict[str, Any] = {
        "event_type": record.get("type", "unknown"),
        "source": source,
        "robot_id": robot_id,
        "task_id": record.get("task_id"),
        "mission_id": record.get("session_id"),
        "payload": record.get("payload", {}),
    }
    timestamp = record.get("timestamp")
    if timestamp is not None:
        kwargs["timestamp"] = timestamp
    return StreamEvent(**kwargs)


# ---------------------------------------------------------------------------
# EventBus — in-process pub/sub with filtering
# ---------------------------------------------------------------------------

_Subscriber = Callable[[StreamEvent], None]


@dataclass
class _Subscription:
    handler: _Subscriber
    event_types: frozenset[str] | None = None


class EventBus:
    """Thread-safe in-process event bus.

    Adapted from OpenClaw's emitAgentEvent/onAgentEvent pattern.
    Supports optional event_type filtering per subscriber.
    Auto-increments sequence numbers per published event.
    Maintains a bounded ring buffer of recent events for cursor replay.
    """

    _MAX_RECENT = 1000

    def __init__(self) -> None:
        self._subscriptions: dict[str, _Subscription] = {}
        self._lock = threading.Lock()
        self._sequence = 0
        self._recent: list[StreamEvent] = []

    def subscribe(
        self,
        handler: _Subscriber,
        *,
        event_types: set[str] | None = None,
    ) -> str:
        """Register a subscriber. Returns a token for unsubscribe."""
        token = uuid4().hex
        sub = _Subscription(
            handler=handler,
            event_types=frozenset(event_types) if event_types else None,
        )
        with self._lock:
            self._subscriptions[token] = sub
        return token

    def unsubscribe(self, token: str) -> None:
        """Remove a subscriber by token."""
        with self._lock:
            self._subscriptions.pop(token, None)

    def publish(self, event: StreamEvent) -> None:
        """Publish an event to all matching subscribers. Thread-safe."""
        with self._lock:
            self._sequence += 1
            event.sequence = self._sequence
            self._recent.append(event)
            if len(self._recent) > self._MAX_RECENT:
                self._recent = self._recent[-self._MAX_RECENT:]
            subs = list(self._subscriptions.values())

        for sub in subs:
            if sub.event_types is not None and event.event_type not in sub.event_types:
                continue
            try:
                sub.handler(event)
            except Exception:
                _logger.exception("EventBus subscriber error")

    def get_recent_events(
        self,
        after_sequence: int = 0,
        *,
        limit: int = 200,
    ) -> list[StreamEvent]:
        """Return events with sequence > after_sequence, up to *limit*.

        Thread-safe snapshot for cursor replay in SSE handlers.
        """
        with self._lock:
            candidates = [e for e in self._recent if e.sequence > after_sequence]
            if len(candidates) > limit:
                candidates = candidates[-limit:]
            return list(candidates)

    @property
    def current_sequence(self) -> int:
        with self._lock:
            return self._sequence


# ---------------------------------------------------------------------------
# TelemetryTracker — derived metrics from event stream
# ---------------------------------------------------------------------------

def _parse_ts(iso: str) -> float:
    """Parse ISO timestamp to epoch seconds. Returns 0 on failure."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


class TelemetryTracker:
    """Computes derived telemetry metrics from StreamEvents.

    Tracks: task latency, action duration, cancel latency, failure reasons,
    and heartbeat age. Designed for robotics operator dashboards.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # task_id -> timestamp of first seen event
        self._task_start: dict[str, float] = {}
        # task_id -> timestamp of terminal event
        self._task_end: dict[str, float] = {}
        # action_id -> start timestamp
        self._action_start: dict[str, float] = {}
        # action_id -> end timestamp
        self._action_end: dict[str, float] = {}
        # task_id -> cancel request timestamp
        self._cancel_request: dict[str, float] = {}
        # task_id -> cancel completion timestamp
        self._cancel_done: dict[str, float] = {}
        # task_id or action_id -> failure reason
        self._failure_reasons: dict[str, str] = {}
        # robot_id -> last heartbeat epoch
        self._heartbeats: dict[str, float] = {}

    def record_event(self, event: StreamEvent) -> None:
        """Ingest a StreamEvent and update telemetry state."""
        ts = _parse_ts(event.timestamp)
        with self._lock:
            if event.event_type == "task.received" and event.task_id:
                self._task_start.setdefault(event.task_id, ts)
            elif event.event_type in {
                "task.completed",
                "task.blocked",
                "task.escalated",
                "task.failed",
                "task.timed_out",
                "task.cancelled",
                "task.lost",
            } and event.task_id:
                self._task_end[event.task_id] = ts
                self._task_start.setdefault(event.task_id, ts)
            elif event.event_type == "action.started":
                aid = event.payload.get("action_id", "")
                if aid:
                    self._action_start.setdefault(aid, ts)
            elif event.event_type in ("action.succeeded", "action.failed"):
                aid = event.payload.get("action_id", "")
                if aid:
                    self._action_end[aid] = ts
                    self._action_start.setdefault(aid, ts)
            if event.event_type == "task.cancel_requested" and event.task_id:
                self._cancel_request.setdefault(event.task_id, ts)
            elif event.event_type == "task.cancelled" and event.task_id:
                self._cancel_done[event.task_id] = ts
                self._cancel_request.setdefault(event.task_id, ts)
            if event.event_type in (
                "task.blocked",
                "task.escalated",
                "task.failed",
                "task.timed_out",
                "task.lost",
                "action.failed",
            ):
                eid = event.payload.get("error", "")
                if eid:
                    key = event.payload.get("action_id") or event.task_id or ""
                    if key:
                        self._failure_reasons[key] = eid
            elif event.event_type == "heartbeat" and event.robot_id:
                self._heartbeats[event.robot_id] = ts

    def update_heartbeat(self, robot_id: str, epoch: float) -> None:
        """Directly update a robot's heartbeat timestamp."""
        with self._lock:
            self._heartbeats[robot_id] = epoch

    def task_latencies(self) -> dict[str, float]:
        """Return task_id -> latency (seconds) for completed tasks."""
        with self._lock:
            result: dict[str, float] = {}
            for tid in set(self._task_start) & set(self._task_end):
                result[tid] = self._task_end[tid] - self._task_start[tid]
            return result

    def action_durations(self) -> dict[str, float]:
        """Return action_id -> duration (seconds) for completed actions."""
        with self._lock:
            result: dict[str, float] = {}
            for aid in set(self._action_start) & set(self._action_end):
                result[aid] = self._action_end[aid] - self._action_start[aid]
            return result

    def cancel_latencies(self) -> dict[str, float]:
        """Return task_id -> cancel latency (seconds)."""
        with self._lock:
            result: dict[str, float] = {}
            for tid in set(self._cancel_request) & set(self._cancel_done):
                result[tid] = self._cancel_done[tid] - self._cancel_request[tid]
            return result

    def failure_reasons(self) -> dict[str, str]:
        """Return entity_id -> failure reason string."""
        with self._lock:
            return dict(self._failure_reasons)

    def heartbeat_ages(self) -> dict[str, float]:
        """Return robot_id -> seconds since last heartbeat."""
        with self._lock:
            now = time.time()
            return {rid: now - ts for rid, ts in self._heartbeats.items()}
