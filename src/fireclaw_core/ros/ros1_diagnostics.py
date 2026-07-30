from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from math import hypot
from typing import Any, Protocol, Sequence

import yaml

from fireclaw_core.ros.ros1_config import Ros1DiagnosticsConfig


ROS1_DIAGNOSTIC_TOOL_NAMES = (
    "ros_topic_list",
    "ros_topic_info",
    "ros_topic_sample",
    "ros_topic_rate",
    "tf_lookup",
    "move_base_status",
    "navigation_diagnostics",
)

_TOPIC_RE = re.compile(r"^/[A-Za-z0-9_][A-Za-z0-9_/]*$")
_FRAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_/.-]*$")
_FLOAT_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_GOAL_STATUS_NAMES = {
    0: "pending",
    1: "active",
    2: "preempted",
    3: "succeeded",
    4: "aborted",
    5: "rejected",
    6: "preempting",
    7: "recalling",
    8: "recalled",
    9: "lost",
}


@dataclass(frozen=True)
class BoundedCommandResult:
    argv: tuple[str, ...]
    exit_code: int | None
    output: str
    duration_seconds: float
    timeout_seconds: float
    max_output_bytes: int
    timed_out: bool = False
    truncated: bool = False
    error_code: str | None = None
    error_message: str | None = None


class Ros1CommandRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> BoundedCommandResult:
        ...


class SubprocessRos1CommandRunner:
    """Run backend-owned ROS argv without a shell and with a hard byte cap."""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> BoundedCommandResult:
        command = tuple(str(part) for part in argv)
        started = time.monotonic()
        environment = dict(os.environ)
        environment["PYTHONUNBUFFERED"] = "1"
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=environment,
                start_new_session=True,
            )
        except FileNotFoundError:
            return BoundedCommandResult(
                argv=command,
                exit_code=None,
                output="",
                duration_seconds=time.monotonic() - started,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                error_code="ros_executable_not_found",
                error_message=f"ROS executable is unavailable: {command[0]}",
            )
        except OSError as exc:
            return BoundedCommandResult(
                argv=command,
                exit_code=None,
                output="",
                duration_seconds=time.monotonic() - started,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                error_code="ros_process_start_failed",
                error_message=f"ROS process could not start: {type(exc).__name__}",
            )

        assert process.stdout is not None
        output = bytearray()
        timed_out = False
        truncated = False
        stream_open = True
        selector = selectors.DefaultSelector()
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = started + timeout_seconds
        try:
            while stream_open:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    _terminate_process(process)
                    break
                events = selector.select(timeout=min(0.05, remaining))
                for key, _ in events:
                    room = max_output_bytes - len(output)
                    try:
                        chunk = os.read(
                            key.fileobj.fileno(),
                            min(4096, room + 1),
                        )
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        stream_open = False
                        break
                    output.extend(chunk[:room])
                    if len(chunk) > room:
                        truncated = True
                        _terminate_process(process)
                        stream_open = False
                        break
                if process.poll() is not None and not events:
                    try:
                        chunk = os.read(
                            process.stdout.fileno(),
                            max_output_bytes - len(output) + 1,
                        )
                    except BlockingIOError:
                        continue
                    if not chunk:
                        stream_open = False
                    else:
                        room = max_output_bytes - len(output)
                        output.extend(chunk[:room])
                        if len(chunk) > room:
                            truncated = True
                            stream_open = False
            if process.poll() is None:
                _terminate_process(process)
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            _kill_process(process)
            process.wait()
        finally:
            selector.close()
            process.stdout.close()

        return BoundedCommandResult(
            argv=command,
            exit_code=process.returncode,
            output=output.decode("utf-8", errors="replace"),
            duration_seconds=time.monotonic() - started,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            timed_out=timed_out,
            truncated=truncated,
        )


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        _kill_process(process)


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


@dataclass(frozen=True)
class Ros1DiagnosticPolicy:
    topic_allowlist: tuple[str, ...]
    frame_allowlist: tuple[str, ...]
    action_allowlist: tuple[str, ...]
    max_topics: int = 100
    max_samples: int = 3
    max_timeout_seconds: float = 3.0
    max_output_bytes: int = 32_768

    @classmethod
    def from_config(
        cls,
        config: Ros1DiagnosticsConfig,
    ) -> "Ros1DiagnosticPolicy":
        return cls(
            topic_allowlist=config.topic_allowlist,
            frame_allowlist=config.frame_allowlist,
            action_allowlist=config.action_allowlist,
            max_topics=config.max_topics,
            max_samples=config.max_samples,
            max_timeout_seconds=config.max_timeout_seconds,
            max_output_bytes=config.max_output_bytes,
        )

    def allows_topic(self, topic: str) -> bool:
        return _matches_allowlist(topic, self.topic_allowlist)

    def allows_frame(self, frame: str) -> bool:
        return _matches_allowlist(frame, self.frame_allowlist)

    def allows_action(self, action: str) -> bool:
        return _matches_allowlist(action, self.action_allowlist)


class Ros1DiagnosticsBackend:
    """Typed, read-only and bounded ROS1 diagnostic operations."""

    source = "ros1_cli_diagnostics"

    def __init__(
        self,
        *,
        robot_id: str,
        policy: Ros1DiagnosticPolicy,
        runner: Ros1CommandRunner | None = None,
        rostopic_executable: str = "rostopic",
        rosrun_executable: str = "rosrun",
    ) -> None:
        if not isinstance(robot_id, str) or not robot_id.strip():
            raise ValueError(
                "ROS1 diagnostics backend requires a robot_id."
            )
        self.robot_id = robot_id.strip()
        self.policy = policy
        self.runner = runner or SubprocessRos1CommandRunner()
        self.rostopic_executable = rostopic_executable
        self.rosrun_executable = rosrun_executable

    @classmethod
    def from_config(
        cls,
        config: Ros1DiagnosticsConfig,
        *,
        robot_id: str,
        runner: Ros1CommandRunner | None = None,
    ) -> "Ros1DiagnosticsBackend | None":
        if not config.enabled:
            return None
        return cls(
            robot_id=robot_id,
            policy=Ros1DiagnosticPolicy.from_config(config),
            runner=runner,
        )

    def validate_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        if tool_name not in ROS1_DIAGNOSTIC_TOOL_NAMES:
            raise ValueError(f"Unknown ROS diagnostic tool: {tool_name}")
        if not isinstance(arguments, dict):
            raise ValueError("ROS diagnostic arguments must be an object.")

        topic_keys: tuple[str, ...] = ()
        frame_keys: tuple[str, ...] = ()
        action_keys: tuple[str, ...] = ()
        if tool_name in {
            "ros_topic_info",
            "ros_topic_sample",
            "ros_topic_rate",
        }:
            topic_keys = ("topic",)
        elif tool_name == "tf_lookup":
            frame_keys = ("reference_frame", "target_frame")
        elif tool_name == "move_base_status":
            action_keys = ("action_name",)
        elif tool_name == "navigation_diagnostics":
            topic_keys = ("scan_topic", "odom_topic", "cmd_vel_topic")
            frame_keys = ("global_frame", "robot_frame")
            action_keys = ("action_name",)

        for key in topic_keys:
            value = arguments.get(key)
            if value is None and tool_name == "navigation_diagnostics":
                value = _NAVIGATION_DEFAULTS[key]
            self._require_allowed_topic(value, key=key)
        for key in frame_keys:
            value = arguments.get(key)
            if value is None and tool_name == "navigation_diagnostics":
                value = _NAVIGATION_DEFAULTS[key]
            self._require_allowed_frame(value, key=key)
        for key in action_keys:
            value = arguments.get(key, "/move_base")
            self._require_allowed_action(value, key=key)

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.validate_tool_call(tool_name, arguments)
        if tool_name == "ros_topic_list":
            return self.list_topics(
                contains=arguments.get("contains"),
                limit=int(arguments.get("limit", 50)),
            )
        if tool_name == "ros_topic_info":
            return self.topic_info(str(arguments["topic"]))
        if tool_name == "ros_topic_sample":
            return self.sample_topic(
                str(arguments["topic"]),
                sample_count=int(arguments.get("sample_count", 1)),
                timeout_seconds=float(
                    arguments.get("timeout_seconds", 1.0)
                ),
            )
        if tool_name == "ros_topic_rate":
            return self.topic_rate(
                str(arguments["topic"]),
                window_seconds=float(
                    arguments.get("window_seconds", 2.0)
                ),
            )
        if tool_name == "tf_lookup":
            return self.lookup_transform(
                reference_frame=str(arguments["reference_frame"]),
                target_frame=str(arguments["target_frame"]),
                timeout_seconds=float(
                    arguments.get("timeout_seconds", 1.0)
                ),
            )
        if tool_name == "move_base_status":
            return self.move_base_status(
                action_name=str(
                    arguments.get("action_name", "/move_base")
                ),
                timeout_seconds=float(
                    arguments.get("timeout_seconds", 1.0)
                ),
            )
        return self.navigation_diagnostics(
            action_name=str(arguments.get("action_name", "/move_base")),
            global_frame=str(arguments.get("global_frame", "map")),
            robot_frame=str(arguments.get("robot_frame", "base_link")),
            scan_topic=str(arguments.get("scan_topic", "/scan")),
            odom_topic=str(arguments.get("odom_topic", "/odom")),
            cmd_vel_topic=str(
                arguments.get("cmd_vel_topic", "/cmd_vel")
            ),
            timeout_seconds=float(
                arguments.get("timeout_seconds", 1.0)
            ),
        )

    def list_topics(
        self,
        *,
        contains: Any = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        started = time.monotonic()
        limit = max(1, min(limit, self.policy.max_topics))
        result = self._run_rostopic(
            ("list", "-v"),
            timeout_seconds=self.policy.max_timeout_seconds,
        )
        if result.error_code or (
            result.exit_code not in {0, None} and not result.timed_out
        ):
            return self._command_error(
                "ros_topic_list",
                started,
                result,
                "ros_graph_unavailable",
            )
        topic_types = _parse_verbose_topic_list(result.output)
        filtered = [
            {"topic": topic, "message_type": message_type}
            for topic, message_type in sorted(topic_types.items())
            if self.policy.allows_topic(topic)
            and (
                not isinstance(contains, str)
                or not contains
                or contains in topic
            )
        ]
        return self._envelope(
            operation="ros_topic_list",
            status="ok",
            started=started,
            payload={
                "topics": filtered[:limit],
                "returned_count": min(len(filtered), limit),
                "matched_count": len(filtered),
                "truncated": result.truncated or len(filtered) > limit,
                "policy_filtered": len(topic_types) - len(filtered),
                "bounded": _bounded_metadata(result),
            },
        )

    def topic_info(self, topic: str) -> dict[str, Any]:
        self._require_allowed_topic(topic)
        started = time.monotonic()
        result = self._run_rostopic(
            ("info", topic),
            timeout_seconds=self.policy.max_timeout_seconds,
        )
        if result.error_code or result.exit_code != 0:
            return self._command_error(
                "ros_topic_info",
                started,
                result,
                "ros_topic_info_unavailable",
                extra={"topic": topic},
            )
        parsed = _parse_topic_info(result.output)
        return self._envelope(
            operation="ros_topic_info",
            status="ok",
            started=started,
            payload={
                "topic": topic,
                **parsed,
                "bounded": _bounded_metadata(result),
            },
        )

    def sample_topic(
        self,
        topic: str,
        *,
        sample_count: int = 1,
        timeout_seconds: float = 1.0,
        include_arrays: bool = False,
    ) -> dict[str, Any]:
        self._require_allowed_topic(topic)
        started = time.monotonic()
        sample_count = max(
            1,
            min(int(sample_count), self.policy.max_samples),
        )
        timeout_seconds = self._bounded_timeout(timeout_seconds)
        args = ["echo", "-n", str(sample_count)]
        if not include_arrays:
            args.append("--noarr")
        args.append(topic)
        result = self._run_rostopic(
            tuple(args),
            timeout_seconds=timeout_seconds,
        )
        samples, parse_error, value_truncated = _parse_topic_samples(
            result.output,
            limit=sample_count,
        )
        if not samples:
            status = "error" if result.error_code else "no_data"
            error_code = (
                result.error_code
                or ("ros_sample_unparseable" if parse_error else "ros_topic_no_data")
            )
            return self._envelope(
                operation="ros_topic_sample",
                status=status,
                started=started,
                payload={
                    "topic": topic,
                    "sample_count_requested": sample_count,
                    "sample_count_received": 0,
                    "error_code": error_code,
                    "message": (
                        result.error_message
                        or parse_error
                        or "No message arrived within the bounded sample window."
                    ),
                    "bounded": _bounded_metadata(result),
                },
            )
        frames, stamps = _sample_metadata(samples)
        return self._envelope(
            operation="ros_topic_sample",
            status="ok",
            started=started,
            payload={
                "topic": topic,
                "sample_count_requested": sample_count,
                "sample_count_received": len(samples),
                "samples": samples,
                "frame_ids": frames,
                "source_stamps": stamps,
                "freshness": {
                    "basis": "local_receipt",
                    "message_observed": True,
                    "sample_window_seconds": round(
                        result.duration_seconds,
                        6,
                    ),
                },
                "truncated": result.truncated or value_truncated,
                "bounded": _bounded_metadata(result),
            },
        )

    def topic_rate(
        self,
        topic: str,
        *,
        window_seconds: float = 2.0,
    ) -> dict[str, Any]:
        self._require_allowed_topic(topic)
        started = time.monotonic()
        window_seconds = self._bounded_timeout(window_seconds)
        result = self._run_rostopic(
            ("hz", "--wall-time", "-w", "100", topic),
            timeout_seconds=window_seconds,
        )
        rate = _parse_topic_rate(result.output)
        if rate is None:
            return self._envelope(
                operation="ros_topic_rate",
                status=(
                    "error" if result.error_code else "no_data"
                ),
                started=started,
                payload={
                    "topic": topic,
                    "error_code": (
                        result.error_code or "ros_topic_rate_unavailable"
                    ),
                    "message": (
                        result.error_message
                        or "No rate estimate was produced in the bounded window."
                    ),
                    "bounded": _bounded_metadata(result),
                },
            )
        return self._envelope(
            operation="ros_topic_rate",
            status="ok",
            started=started,
            payload={
                "topic": topic,
                **rate,
                "observation_window_seconds": round(
                    result.duration_seconds,
                    6,
                ),
                "bounded": _bounded_metadata(result),
            },
        )

    def lookup_transform(
        self,
        *,
        reference_frame: str,
        target_frame: str,
        timeout_seconds: float = 1.0,
    ) -> dict[str, Any]:
        self._require_allowed_frame(reference_frame, key="reference_frame")
        self._require_allowed_frame(target_frame, key="target_frame")
        started = time.monotonic()
        timeout_seconds = self._bounded_timeout(timeout_seconds)
        result = self.runner.run(
            (
                self.rosrun_executable,
                "tf",
                "tf_echo",
                reference_frame,
                target_frame,
            ),
            timeout_seconds=timeout_seconds,
            max_output_bytes=self.policy.max_output_bytes,
        )
        transform = _parse_tf_echo(result.output)
        if transform is None:
            return self._envelope(
                operation="tf_lookup",
                status=(
                    "error" if result.error_code else "no_data"
                ),
                started=started,
                payload={
                    "reference_frame": reference_frame,
                    "target_frame": target_frame,
                    "error_code": result.error_code or "tf_unavailable",
                    "message": (
                        result.error_message
                        or "No transform arrived in the bounded lookup window."
                    ),
                    "bounded": _bounded_metadata(result),
                },
            )
        return self._envelope(
            operation="tf_lookup",
            status="ok",
            started=started,
            payload={
                "reference_frame": reference_frame,
                "target_frame": target_frame,
                "transform": transform,
                "bounded": _bounded_metadata(result),
            },
        )

    def move_base_status(
        self,
        *,
        action_name: str = "/move_base",
        timeout_seconds: float = 1.0,
    ) -> dict[str, Any]:
        self._require_allowed_action(action_name)
        status_topic = f"{action_name.rstrip('/')}/status"
        self._require_allowed_topic(status_topic)
        started = time.monotonic()
        sample = self.sample_topic(
            status_topic,
            sample_count=1,
            timeout_seconds=timeout_seconds,
            include_arrays=True,
        )
        if sample["status"] != "ok":
            return self._envelope(
                operation="move_base_status",
                status=sample["status"],
                started=started,
                payload={
                    "action_name": action_name,
                    "status_topic": status_topic,
                    "navigation_state": "unknown",
                    "error_code": sample.get("error_code"),
                    "message": sample.get("message"),
                    "sample_evidence_id": sample["evidence_id"],
                },
            )
        payloads = sample.get("samples")
        first = (
            payloads[0]
            if isinstance(payloads, list) and payloads
            else {}
        )
        status_list = (
            first.get("status_list", [])
            if isinstance(first, dict)
            else []
        )
        statuses = [
            _structured_goal_status(item)
            for item in status_list[:20]
            if isinstance(item, dict)
        ]
        navigation_state = _navigation_state(statuses)
        return self._envelope(
            operation="move_base_status",
            status="ok",
            started=started,
            payload={
                "action_name": action_name,
                "status_topic": status_topic,
                "navigation_state": navigation_state,
                "goals": statuses,
                "goal_count": len(statuses),
                "sample_evidence_id": sample["evidence_id"],
            },
        )

    def navigation_diagnostics(
        self,
        *,
        action_name: str = "/move_base",
        global_frame: str = "map",
        robot_frame: str = "base_link",
        scan_topic: str = "/scan",
        odom_topic: str = "/odom",
        cmd_vel_topic: str = "/cmd_vel",
        timeout_seconds: float = 1.0,
    ) -> dict[str, Any]:
        call = {
            "action_name": action_name,
            "global_frame": global_frame,
            "robot_frame": robot_frame,
            "scan_topic": scan_topic,
            "odom_topic": odom_topic,
            "cmd_vel_topic": cmd_vel_topic,
        }
        self.validate_tool_call("navigation_diagnostics", call)
        started = time.monotonic()
        timeout_seconds = self._bounded_timeout(timeout_seconds)
        with ThreadPoolExecutor(
            max_workers=5,
            thread_name_prefix="ros-diagnostics",
        ) as executor:
            futures = {
                "move_base": executor.submit(
                    self.move_base_status,
                    action_name=action_name,
                    timeout_seconds=timeout_seconds,
                ),
                "scan": executor.submit(
                    self.sample_topic,
                    scan_topic,
                    sample_count=1,
                    timeout_seconds=timeout_seconds,
                ),
                "odom": executor.submit(
                    self.sample_topic,
                    odom_topic,
                    sample_count=min(2, self.policy.max_samples),
                    timeout_seconds=timeout_seconds,
                ),
                "cmd_vel": executor.submit(
                    self.sample_topic,
                    cmd_vel_topic,
                    sample_count=min(2, self.policy.max_samples),
                    timeout_seconds=timeout_seconds,
                ),
                "tf": executor.submit(
                    self.lookup_transform,
                    reference_frame=global_frame,
                    target_frame=robot_frame,
                    timeout_seconds=timeout_seconds,
                ),
            }
            checks = {
                name: future.result()
                for name, future in futures.items()
            }

        findings = _navigation_findings(checks)
        overall_status = "healthy" if not findings else "degraded"
        if all(check["status"] != "ok" for check in checks.values()):
            overall_status = "unavailable"
        return self._envelope(
            operation="navigation_diagnostics",
            status="ok" if overall_status != "unavailable" else "no_data",
            started=started,
            payload={
                "overall_status": overall_status,
                "findings": findings,
                "checks": {
                    "move_base": _move_base_summary(checks["move_base"]),
                    "scan": _stream_summary(checks["scan"]),
                    "odom": _stream_summary(checks["odom"]),
                    "cmd_vel": _stream_summary(checks["cmd_vel"]),
                    "tf": _tf_summary(checks["tf"]),
                },
                "observation_scope": {
                    **call,
                    "timeout_seconds_per_check": timeout_seconds,
                },
                "advisory_warning": (
                    "Finite samples can identify evidence of a fault but do "
                    "not prove that an intermittent fault is absent."
                ),
            },
        )

    def _run_rostopic(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> BoundedCommandResult:
        return self.runner.run(
            (self.rostopic_executable, *args),
            timeout_seconds=self._bounded_timeout(timeout_seconds),
            max_output_bytes=self.policy.max_output_bytes,
        )

    def _bounded_timeout(self, timeout_seconds: float) -> float:
        return max(
            0.1,
            min(float(timeout_seconds), self.policy.max_timeout_seconds),
        )

    def _require_allowed_topic(
        self,
        topic: Any,
        *,
        key: str = "topic",
    ) -> str:
        if not isinstance(topic, str) or not _TOPIC_RE.fullmatch(topic):
            raise ValueError(f"{key} must be a valid absolute ROS topic.")
        if "//" in topic or len(topic) > 256:
            raise ValueError(f"{key} is not a canonical ROS topic.")
        if not self.policy.allows_topic(topic):
            raise ValueError(
                f"{key} {topic!r} is outside the ROS diagnostic allowlist."
            )
        return topic

    def _require_allowed_frame(
        self,
        frame: Any,
        *,
        key: str = "frame",
    ) -> str:
        if (
            not isinstance(frame, str)
            or len(frame) > 128
            or not _FRAME_RE.fullmatch(frame)
            or "//" in frame
            or ".." in frame
        ):
            raise ValueError(f"{key} must be a canonical ROS frame.")
        if not self.policy.allows_frame(frame):
            raise ValueError(
                f"{key} {frame!r} is outside the ROS diagnostic allowlist."
            )
        return frame

    def _require_allowed_action(
        self,
        action: Any,
        *,
        key: str = "action_name",
    ) -> str:
        if (
            not isinstance(action, str)
            or not _TOPIC_RE.fullmatch(action)
            or "//" in action
            or len(action) > 256
        ):
            raise ValueError(
                f"{key} must be a canonical absolute ROS action namespace."
            )
        if not self.policy.allows_action(action):
            raise ValueError(
                f"{key} {action!r} is outside the ROS diagnostic allowlist."
            )
        return action

    def _command_error(
        self,
        operation: str,
        started: float,
        result: BoundedCommandResult,
        error_code: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._envelope(
            operation=operation,
            status="error",
            started=started,
            payload={
                **(extra or {}),
                "error_code": result.error_code or error_code,
                "message": (
                    result.error_message
                    or _safe_command_message(result.output)
                    or "ROS diagnostic command failed."
                ),
                "bounded": _bounded_metadata(result),
            },
        )

    def _envelope(
        self,
        *,
        operation: str,
        status: str,
        started: float,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        observed_at = datetime.now(timezone.utc).isoformat()
        result = {
            "operation": operation,
            "status": status,
            "robot_id": self.robot_id,
            "source": self.source,
            "authority": "advisory",
            "read_only": True,
            "observed_at": observed_at,
            "duration_seconds": round(time.monotonic() - started, 6),
            **payload,
        }
        evidence_hash = hashlib.sha256(
            json.dumps(
                result,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        result["evidence_id"] = f"rosdiag-{evidence_hash[:24]}"
        return result


_NAVIGATION_DEFAULTS = {
    "action_name": "/move_base",
    "global_frame": "map",
    "robot_frame": "base_link",
    "scan_topic": "/scan",
    "odom_topic": "/odom",
    "cmd_vel_topic": "/cmd_vel",
}


def _matches_allowlist(value: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


def _bounded_metadata(result: BoundedCommandResult) -> dict[str, Any]:
    return {
        "timeout_seconds": result.timeout_seconds,
        "max_output_bytes": result.max_output_bytes,
        "timed_out": result.timed_out,
        "output_truncated": result.truncated,
        "exit_code": result.exit_code,
    }


def _safe_command_message(output: str) -> str | None:
    message = " ".join(output.strip().split())
    if not message:
        return None
    return message[:500]


def _parse_verbose_topic_list(output: str) -> dict[str, str]:
    topics: dict[str, str] = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("* "):
            continue
        parts = stripped.split()
        if len(parts) < 3:
            continue
        topic = parts[1]
        message_type = parts[2].strip("[]")
        if _TOPIC_RE.fullmatch(topic) and message_type:
            topics[topic] = message_type
    return topics


def _parse_topic_info(output: str) -> dict[str, Any]:
    message_type: str | None = None
    publishers: list[str] = []
    subscribers: list[str] = []
    section: str | None = None
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Type:"):
            message_type = stripped.split(":", 1)[1].strip() or None
            continue
        if stripped.startswith("Publishers:"):
            section = "publishers"
            continue
        if stripped.startswith("Subscribers:"):
            section = "subscribers"
            continue
        if not stripped.startswith("* "):
            continue
        node = stripped[2:].split(" (", 1)[0].strip()
        if not node:
            continue
        if section == "publishers":
            publishers.append(node)
        elif section == "subscribers":
            subscribers.append(node)
    return {
        "message_type": message_type,
        "publishers": publishers[:100],
        "subscribers": subscribers[:100],
        "publisher_count": len(publishers),
        "subscriber_count": len(subscribers),
    }


def _parse_topic_samples(
    output: str,
    *,
    limit: int,
) -> tuple[list[Any], str | None, bool]:
    samples: list[Any] = []
    parse_error: str | None = None
    value_truncated = False
    documents = re.split(r"(?m)^---\s*$", output)
    for document in documents:
        if len(samples) >= limit:
            break
        if not document.strip():
            continue
        try:
            value = yaml.load(document, Loader=_NoAliasSafeLoader)
        except yaml.YAMLError as exc:
            parse_error = f"ROS YAML payload could not be parsed: {type(exc).__name__}"
            continue
        if value is None:
            continue
        sanitized, was_truncated = _sanitize_value(value)
        samples.append(sanitized)
        value_truncated = value_truncated or was_truncated
    return samples, parse_error, value_truncated


class _NoAliasSafeLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("YAML aliases are not allowed.")
        return super().compose_node(parent, index)


def _sanitize_value(
    value: Any,
    *,
    depth: int = 0,
) -> tuple[Any, bool]:
    if depth >= 8:
        return "<max-depth>", True
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    if isinstance(value, str):
        return (value[:512], len(value) > 512)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>", True
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        truncated = len(value) > 64
        for key, item in list(value.items())[:64]:
            clean, child_truncated = _sanitize_value(
                item,
                depth=depth + 1,
            )
            result[str(key)[:128]] = clean
            truncated = truncated or child_truncated
        return result, truncated
    if isinstance(value, (list, tuple)):
        result = []
        truncated = len(value) > 32
        for item in value[:32]:
            clean, child_truncated = _sanitize_value(
                item,
                depth=depth + 1,
            )
            result.append(clean)
            truncated = truncated or child_truncated
        return result, truncated
    return str(value)[:512], True


def _sample_metadata(
    samples: list[Any],
) -> tuple[list[str], list[dict[str, int]]]:
    frames: list[str] = []
    stamps: list[dict[str, int]] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        header = sample.get("header")
        if not isinstance(header, dict):
            continue
        frame = header.get("frame_id")
        if isinstance(frame, str) and frame and frame not in frames:
            frames.append(frame)
        stamp = header.get("stamp")
        if not isinstance(stamp, dict):
            continue
        secs = stamp.get("secs")
        nsecs = stamp.get("nsecs")
        if isinstance(secs, int) and isinstance(nsecs, int):
            stamps.append({"secs": secs, "nsecs": nsecs})
    return frames, stamps


def _parse_topic_rate(output: str) -> dict[str, Any] | None:
    rates = re.findall(rf"average rate:\s*({_FLOAT_RE})", output)
    if not rates:
        return None
    result: dict[str, Any] = {"average_hz": float(rates[-1])}
    stats = re.findall(
        rf"min:\s*({_FLOAT_RE})s\s+max:\s*({_FLOAT_RE})s\s+"
        rf"std dev:\s*({_FLOAT_RE})s\s+window:\s*(\d+)",
        output,
    )
    if stats:
        minimum, maximum, stddev, window = stats[-1]
        result.update(
            {
                "min_period_seconds": float(minimum),
                "max_period_seconds": float(maximum),
                "stddev_period_seconds": float(stddev),
                "sample_window_messages": int(window),
            }
        )
    return result


def _parse_tf_echo(output: str) -> dict[str, Any] | None:
    translations = re.findall(
        rf"Translation:\s*\[\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*\]",
        output,
    )
    quaternions = re.findall(
        rf"Quaternion[^\[]*\[\s*({_FLOAT_RE})\s*,\s*"
        rf"({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*,\s*"
        rf"({_FLOAT_RE})\s*\]",
        output,
    )
    if not translations or not quaternions:
        return None
    translation = [float(value) for value in translations[-1]]
    quaternion = [float(value) for value in quaternions[-1]]
    result: dict[str, Any] = {
        "translation": {
            "x": translation[0],
            "y": translation[1],
            "z": translation[2],
        },
        "rotation": {
            "x": quaternion[0],
            "y": quaternion[1],
            "z": quaternion[2],
            "w": quaternion[3],
        },
    }
    rpy_matches = re.findall(
        rf"RPY[^\[]*\[\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*\]",
        output,
    )
    if rpy_matches:
        roll, pitch, yaw = (float(value) for value in rpy_matches[-1])
        result["rpy_radians"] = {
            "roll": roll,
            "pitch": pitch,
            "yaw": yaw,
        }
    time_matches = re.findall(rf"At time\s+({_FLOAT_RE})", output)
    if time_matches:
        result["ros_time_seconds"] = float(time_matches[-1])
    return result


def _structured_goal_status(value: dict[str, Any]) -> dict[str, Any]:
    code = value.get("status")
    code = code if isinstance(code, int) else -1
    goal_id = value.get("goal_id")
    goal_id = goal_id if isinstance(goal_id, dict) else {}
    text = value.get("text")
    return {
        "goal_id": str(goal_id.get("id") or "")[:256],
        "status_code": code,
        "status_name": _GOAL_STATUS_NAMES.get(code, "unknown"),
        "text": str(text or "")[:512],
    }


def _navigation_state(statuses: list[dict[str, Any]]) -> str:
    if not statuses:
        return "idle"
    name = str(statuses[-1].get("status_name") or "unknown")
    if name in {"pending", "active", "preempting", "recalling"}:
        return "active"
    if name == "succeeded":
        return "succeeded"
    if name in {"preempted", "aborted", "rejected", "recalled", "lost"}:
        return "failed"
    return "unknown"


def _navigation_findings(
    checks: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    _append_unavailable_finding(
        findings,
        checks["scan"],
        code="laser_stream_unavailable",
        message="No bounded laser sample was observed.",
    )
    _append_unavailable_finding(
        findings,
        checks["odom"],
        code="odometry_stream_unavailable",
        message="No bounded odometry sample was observed.",
    )
    _append_unavailable_finding(
        findings,
        checks["cmd_vel"],
        code="velocity_command_stream_unavailable",
        message="No bounded velocity command sample was observed.",
        severity="warning",
    )
    _append_unavailable_finding(
        findings,
        checks["tf"],
        code="robot_transform_unavailable",
        message="The global-to-robot transform was not observed.",
    )
    move_base = checks["move_base"]
    if move_base.get("status") != "ok":
        findings.append(
            _finding(
                "move_base_status_unavailable",
                "error",
                "move_base status was not observed.",
                move_base,
            )
        )
        return findings
    state = move_base.get("navigation_state")
    if state == "failed":
        findings.append(
            _finding(
                "navigation_action_failed",
                "error",
                "move_base reports a terminal failure state.",
                move_base,
            )
        )
    if state != "active":
        return findings

    command = _max_command_magnitude(checks["cmd_vel"])
    movement = _odometry_position_span(checks["odom"])
    if command is None or movement is None:
        return findings
    if command >= 0.03 and movement < 0.01:
        findings.append(
            {
                "code": "possible_navigation_stall",
                "severity": "warning",
                "message": (
                    "move_base is active and non-zero velocity was commanded, "
                    "but the bounded odometry samples show negligible motion."
                ),
                "confidence": 0.55,
                "evidence": {
                    "command_magnitude": command,
                    "odometry_position_span_m": movement,
                    "cmd_vel_evidence_id": checks["cmd_vel"].get(
                        "evidence_id"
                    ),
                    "odom_evidence_id": checks["odom"].get("evidence_id"),
                },
            }
        )
    elif command < 0.03:
        findings.append(
            {
                "code": "planner_not_commanding_motion",
                "severity": "warning",
                "message": (
                    "move_base is active but the bounded velocity samples are "
                    "near zero; inspect the local planner and obstacles."
                ),
                "confidence": 0.5,
                "evidence": {
                    "command_magnitude": command,
                    "cmd_vel_evidence_id": checks["cmd_vel"].get(
                        "evidence_id"
                    ),
                },
            }
        )
    return findings


def _append_unavailable_finding(
    findings: list[dict[str, Any]],
    check: dict[str, Any],
    *,
    code: str,
    message: str,
    severity: str = "error",
) -> None:
    if check.get("status") == "ok":
        return
    findings.append(_finding(code, severity, message, check))


def _finding(
    code: str,
    severity: str,
    message: str,
    check: dict[str, Any],
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "confidence": 0.9,
        "evidence": {
            "evidence_id": check.get("evidence_id"),
            "status": check.get("status"),
            "error_code": check.get("error_code"),
        },
    }


def _max_command_magnitude(check: dict[str, Any]) -> float | None:
    samples = check.get("samples")
    if not isinstance(samples, list) or not samples:
        return None
    values: list[float] = []
    for sample in samples:
        linear = _nested_float(sample, "linear", "x") or 0.0
        angular = _nested_float(sample, "angular", "z") or 0.0
        values.append(max(abs(linear), abs(angular)))
    return max(values) if values else None


def _odometry_position_span(check: dict[str, Any]) -> float | None:
    samples = check.get("samples")
    if not isinstance(samples, list) or len(samples) < 2:
        return None
    positions: list[tuple[float, float]] = []
    for sample in samples:
        x = _nested_float(sample, "pose", "pose", "position", "x")
        y = _nested_float(sample, "pose", "pose", "position", "y")
        if x is not None and y is not None:
            positions.append((x, y))
    if len(positions) < 2:
        return None
    first_x, first_y = positions[0]
    return max(
        hypot(x - first_x, y - first_y)
        for x, y in positions[1:]
    )


def _nested_float(value: Any, *path: str) -> float | None:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        return float(current)
    return None


def _move_base_summary(check: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": check.get("status"),
        "navigation_state": check.get("navigation_state"),
        "goals": check.get("goals", []),
        "error_code": check.get("error_code"),
        "evidence_id": check.get("evidence_id"),
    }


def _stream_summary(check: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": check.get("status"),
        "topic": check.get("topic"),
        "sample_count_received": check.get("sample_count_received", 0),
        "frame_ids": check.get("frame_ids", []),
        "freshness": check.get("freshness"),
        "error_code": check.get("error_code"),
        "evidence_id": check.get("evidence_id"),
    }


def _tf_summary(check: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": check.get("status"),
        "reference_frame": check.get("reference_frame"),
        "target_frame": check.get("target_frame"),
        "transform": check.get("transform"),
        "error_code": check.get("error_code"),
        "evidence_id": check.get("evidence_id"),
    }
