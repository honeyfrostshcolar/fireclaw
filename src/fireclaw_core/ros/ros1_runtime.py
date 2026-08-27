"""Explicit process-owned ROS1 runtime lifecycle for Robot Gateway.

ROS graph processes started by ``roslaunch`` do not initialize ``rospy`` in
the independently running Robot Gateway process.  This owner performs a
bounded ROS Master preflight, initializes the process-local node before HTTP
request intake, and shuts down only nodes that it created itself.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import http.client
from importlib import import_module
import math
import os
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit
import xmlrpc.client


MasterProbe = Callable[[str, float], tuple[bool, str | None]]
RospyLoader = Callable[[], Any]


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout_seconds: float) -> None:
        super().__init__()
        self.timeout_seconds = timeout_seconds

    def make_connection(self, host: Any) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(
            host,
            timeout=self.timeout_seconds,
        )


@dataclass(frozen=True)
class Ros1RuntimeSnapshot:
    schema_version: int
    status: str
    reason_code: str
    message: str
    master_uri: str
    node_name: str
    node_initialized: bool
    owns_node: bool
    started_at: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Ros1RuntimeLifecycle:
    """Own one process-local ``rospy`` node with bounded startup semantics."""

    def __init__(
        self,
        *,
        node_name: str = "fireclaw_gateway",
        master_uri: str | None = None,
        startup_timeout_seconds: float = 5.0,
        master_probe: MasterProbe | None = None,
        rospy_loader: RospyLoader | None = None,
    ) -> None:
        if not isinstance(node_name, str) or not node_name.strip():
            raise ValueError("ROS1 runtime node_name must be non-empty")
        if (
            isinstance(startup_timeout_seconds, bool)
            or not isinstance(startup_timeout_seconds, (int, float))
            or not math.isfinite(float(startup_timeout_seconds))
            or float(startup_timeout_seconds) <= 0
        ):
            raise ValueError(
                "ROS1 runtime startup_timeout_seconds must be positive and finite"
            )
        self.node_name = node_name.strip()
        self.master_uri = (
            master_uri.strip()
            if isinstance(master_uri, str) and master_uri.strip()
            else os.environ.get("ROS_MASTER_URI", "http://localhost:11311")
        )
        self._public_master_uri = _redact_uri_userinfo(self.master_uri)
        self.startup_timeout_seconds = float(startup_timeout_seconds)
        self._master_probe = master_probe or _probe_ros_master
        self._rospy_loader = rospy_loader or (
            lambda: import_module("rospy")
        )
        self._lock = threading.RLock()
        self._initialization_thread: threading.Thread | None = None
        self._rospy: Any | None = None
        self._stop_requested = False
        now = _timestamp()
        self._snapshot = Ros1RuntimeSnapshot(
            schema_version=1,
            status="not_started",
            reason_code="ros_runtime_not_started",
            message="ROS1 runtime has not started.",
            master_uri=self._public_master_uri,
            node_name=self.node_name,
            node_initialized=False,
            owns_node=False,
            started_at=None,
            updated_at=now,
        )

    def start(self) -> Ros1RuntimeSnapshot:
        started_monotonic = time.monotonic()
        with self._lock:
            if self._snapshot.status in {"ready", "starting"}:
                return self._snapshot
            self._stop_requested = False
            started_at = _timestamp()
            self._snapshot = Ros1RuntimeSnapshot(
                schema_version=1,
                status="starting",
                reason_code="ros_runtime_starting",
                message="ROS1 runtime initialization is in progress.",
                master_uri=self._public_master_uri,
                node_name=self.node_name,
                node_initialized=False,
                owns_node=False,
                started_at=started_at,
                updated_at=started_at,
            )

        try:
            rospy = self._rospy_loader()
        except Exception as exc:
            return self._degraded(
                reason_code="ros_client_unavailable",
                message=f"ROS1 Python client is unavailable: {exc}",
            )
        self._rospy = rospy
        if _is_initialized(rospy):
            return self._ready(
                owns_node=False,
                reason_code="ros_node_adopted",
                message="Adopted the already initialized process-local ROS1 node.",
            )

        available, error = self._master_probe(
            self.master_uri,
            self.startup_timeout_seconds,
        )
        if not available:
            return self._degraded(
                reason_code="ros_master_unavailable",
                message=(
                    "ROS Master preflight failed"
                    + (f": {error}" if error else ".")
                ),
            )

        completed = threading.Event()

        def initialize() -> None:
            error_message: str | None = None
            try:
                rospy.init_node(
                    self.node_name,
                    anonymous=True,
                    disable_signals=True,
                )
                if not _is_initialized(rospy):
                    error_message = (
                        "rospy.init_node returned without an initialized node."
                    )
            except Exception as exc:  # pragma: no cover - exact rospy failures vary
                error_message = str(exc)

            with self._lock:
                if error_message is not None:
                    self._snapshot = self._snapshot_value(
                        status="degraded",
                        reason_code="ros_node_initialization_failed",
                        message=(
                            "ROS1 node initialization failed: "
                            f"{error_message}"
                        ),
                        node_initialized=False,
                        owns_node=False,
                    )
                elif self._stop_requested:
                    _signal_shutdown(rospy, "Robot Gateway stopped during ROS startup")
                    self._snapshot = self._snapshot_value(
                        status="stopped",
                        reason_code="ros_runtime_stopped",
                        message="ROS1 runtime stopped during initialization.",
                        node_initialized=False,
                        owns_node=False,
                    )
                else:
                    self._snapshot = self._snapshot_value(
                        status="ready",
                        reason_code="ros_node_initialized",
                        message="Robot Gateway initialized its process-local ROS1 node.",
                        node_initialized=True,
                        owns_node=True,
                    )
            completed.set()

        thread = threading.Thread(
            target=initialize,
            name="fireclaw-ros1-runtime-startup",
            daemon=True,
        )
        with self._lock:
            self._initialization_thread = thread
        thread.start()
        remaining = max(
            0.0,
            self.startup_timeout_seconds
            - (time.monotonic() - started_monotonic),
        )
        if not completed.wait(remaining):
            return self._degraded(
                reason_code="ros_node_initialization_timeout",
                message=(
                    "ROS1 node initialization exceeded the bounded startup "
                    f"timeout ({self.startup_timeout_seconds:g}s)."
                ),
            )
        return self.snapshot()

    def stop(self) -> Ros1RuntimeSnapshot:
        with self._lock:
            self._stop_requested = True
            snapshot = self._snapshot
            rospy = self._rospy
            owns_node = snapshot.owns_node
        if owns_node and rospy is not None and _is_initialized(rospy):
            _signal_shutdown(rospy, "Robot Gateway stopped")
        with self._lock:
            self._snapshot = self._snapshot_value(
                status="stopped",
                reason_code="ros_runtime_stopped",
                message="ROS1 runtime is stopped.",
                node_initialized=False,
                owns_node=False,
            )
            return self._snapshot

    def snapshot(self) -> Ros1RuntimeSnapshot:
        with self._lock:
            return self._snapshot

    def _ready(
        self,
        *,
        owns_node: bool,
        reason_code: str,
        message: str,
    ) -> Ros1RuntimeSnapshot:
        with self._lock:
            self._snapshot = self._snapshot_value(
                status="ready",
                reason_code=reason_code,
                message=message,
                node_initialized=True,
                owns_node=owns_node,
            )
            return self._snapshot

    def _degraded(
        self,
        *,
        reason_code: str,
        message: str,
    ) -> Ros1RuntimeSnapshot:
        with self._lock:
            # A timed-out initialization worker may complete immediately after
            # the caller's wait. Never overwrite that newer ready state.
            if self._snapshot.status == "ready":
                return self._snapshot
            self._snapshot = self._snapshot_value(
                status="degraded",
                reason_code=reason_code,
                message=message,
                node_initialized=False,
                owns_node=False,
            )
            return self._snapshot

    def _snapshot_value(
        self,
        *,
        status: str,
        reason_code: str,
        message: str,
        node_initialized: bool,
        owns_node: bool,
    ) -> Ros1RuntimeSnapshot:
        return Ros1RuntimeSnapshot(
            schema_version=1,
            status=status,
            reason_code=reason_code,
            message=message,
            master_uri=self._public_master_uri,
            node_name=self.node_name,
            node_initialized=node_initialized,
            owns_node=owns_node,
            started_at=self._snapshot.started_at,
            updated_at=_timestamp(),
        )


def _probe_ros_master(
    master_uri: str,
    timeout_seconds: float,
) -> tuple[bool, str | None]:
    try:
        transport = _TimeoutTransport(timeout_seconds)
        master = xmlrpc.client.ServerProxy(master_uri, transport=transport)
        response = master.getPid("/fireclaw_gateway_preflight")
        if (
            not isinstance(response, (list, tuple))
            or len(response) < 3
            or response[0] != 1
        ):
            return False, f"unexpected getPid response: {response!r}"
        return True, None
    except Exception as exc:
        return False, str(exc)


def _is_initialized(rospy: Any) -> bool:
    try:
        return bool(rospy.core.is_initialized())
    except Exception:
        return False


def _signal_shutdown(rospy: Any, reason: str) -> None:
    try:
        rospy.signal_shutdown(reason)
    except Exception:
        return


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_uri_userinfo(value: str) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        return urlunsplit((
            parsed.scheme,
            f"{hostname}{port}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        ))
    except (TypeError, ValueError):
        return "redacted://invalid-ros-master-uri"
