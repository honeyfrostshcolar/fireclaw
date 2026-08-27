from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

ROS1_INTERFACES = ("topic", "service", "action")
DEFAULT_ROS1_DIAGNOSTIC_TOPIC_ALLOWLIST = (
    "/scan",
    "/scan_filtered",
    "/odom",
    "/tf",
    "/tf_static",
    "/cmd_vel",
    "/amcl_pose",
    "/map",
    "/map_metadata",
    "/diagnostics",
    "/diagnostics_agg",
    "/joint_states",
    "/move_base",
    "/move_base/*",
    "/camera/*",
    "/camera_*",
    "/camera_*/*",
    "/thermal/*",
)
DEFAULT_ROS1_DIAGNOSTIC_FRAME_ALLOWLIST = (
    "map",
    "odom",
    "base_link",
    "base_footprint",
    "laser",
    "base_scan",
    "*_link",
    "camera_*",
)
DEFAULT_ROS1_DIAGNOSTIC_ACTION_ALLOWLIST = ("/move_base",)
@dataclass(frozen=True)
class Ros1EndpointConfig:
    interface: str
    name: str
    type: str
    cancel_supported: bool = False
    feedback_supported: bool = False
    profile: str | None = None
    goal_template: dict[str, Any] = field(default_factory=dict)
    request_template: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Ros1EmergencyStopConfig:
    interface: str
    name: str
    type: str


@dataclass(frozen=True)
class Ros1TransportConfig:
    enabled: bool = False
    wait_for_server_seconds: float = 5.0
    wait_for_result_seconds: float = 360.0


@dataclass(frozen=True)
class Ros1DiagnosticsConfig:
    enabled: bool = True
    topic_allowlist: tuple[str, ...] = (
        DEFAULT_ROS1_DIAGNOSTIC_TOPIC_ALLOWLIST
    )
    frame_allowlist: tuple[str, ...] = (
        DEFAULT_ROS1_DIAGNOSTIC_FRAME_ALLOWLIST
    )
    action_allowlist: tuple[str, ...] = (
        DEFAULT_ROS1_DIAGNOSTIC_ACTION_ALLOWLIST
    )
    max_topics: int = 100
    max_samples: int = 3
    max_timeout_seconds: float = 3.0
    max_output_bytes: int = 32_768


@dataclass(frozen=True)
class Ros1AdapterConfig:
    robot_id: str
    namespace: str | None = None
    emergency_stop: Ros1EmergencyStopConfig | None = None
    transport: Ros1TransportConfig = field(default_factory=Ros1TransportConfig)
    diagnostics: Ros1DiagnosticsConfig = field(
        default_factory=Ros1DiagnosticsConfig
    )


def load_ros1_adapter_config(path: str | Path) -> Ros1AdapterConfig:
    target = Path(path)
    try:
        content = target.read_text(encoding="utf-8")
        if target.suffix.lower() in {".yaml", ".yml"}:
            raw = _parse_simple_yaml(content)
        else:
            raw = json.loads(content)
    except Exception as exc:
        raise ValueError(f"Failed to load ROS1 adapter config {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("ROS1 adapter config must be a JSON object.")
    return parse_ros1_adapter_config(raw)


def parse_ros1_adapter_config(raw: dict[str, Any]) -> Ros1AdapterConfig:
    robot_id = _required_string(raw, "robot_id")
    namespace = raw.get("namespace")
    if namespace is not None and not isinstance(namespace, str):
        raise ValueError("namespace must be a string when provided.")

    if "endpoints" in raw or "remap" in raw or "targets" in raw:
        raise ValueError(
            "ROS1 adapter domain endpoints were removed; configure runtime "
            "endpoints in the owning Plugin instead."
        )

    emergency_stop = None
    emergency_stop_raw = raw.get("emergency_stop")
    if emergency_stop_raw is not None:
        emergency_stop = _parse_emergency_stop_config(emergency_stop_raw)

    transport = _parse_transport_config(raw.get("transport", {}))
    diagnostics = _parse_diagnostics_config(raw.get("diagnostics", {}))

    return Ros1AdapterConfig(
        robot_id=robot_id,
        namespace=namespace,
        emergency_stop=emergency_stop,
        transport=transport,
        diagnostics=diagnostics,
    )


def _parse_emergency_stop_config(raw: Any) -> Ros1EmergencyStopConfig:
    if not isinstance(raw, dict):
        raise ValueError("emergency_stop must be an object.")
    interface = _required_string(raw, "interface", prefix="emergency_stop")
    if interface not in ROS1_INTERFACES:
        raise ValueError(f"emergency_stop.interface must be one of {', '.join(ROS1_INTERFACES)}.")
    return Ros1EmergencyStopConfig(
        interface=interface,
        name=_required_string(raw, "name", prefix="emergency_stop"),
        type=_required_string(raw, "type", prefix="emergency_stop"),
    )


def _parse_transport_config(raw: Any) -> Ros1TransportConfig:
    if not isinstance(raw, dict):
        raise ValueError("transport must be an object.")
    wait_for_server_seconds = float(raw.get("wait_for_server_seconds", 5.0))
    wait_for_result_seconds = float(raw.get("wait_for_result_seconds", 360.0))
    if wait_for_server_seconds < 0:
        raise ValueError("transport.wait_for_server_seconds must be non-negative.")
    if wait_for_result_seconds < 0:
        raise ValueError("transport.wait_for_result_seconds must be non-negative.")
    return Ros1TransportConfig(
        enabled=bool(raw.get("enabled", False)),
        wait_for_server_seconds=wait_for_server_seconds,
        wait_for_result_seconds=wait_for_result_seconds,
    )


def _parse_diagnostics_config(raw: Any) -> Ros1DiagnosticsConfig:
    if not isinstance(raw, dict):
        raise ValueError("diagnostics must be an object.")
    max_topics = int(raw.get("max_topics", 100))
    max_samples = int(raw.get("max_samples", 3))
    max_timeout_seconds = float(
        raw.get("max_timeout_seconds", 3.0)
    )
    max_output_bytes = int(raw.get("max_output_bytes", 32_768))
    if not 1 <= max_topics <= 1_000:
        raise ValueError("diagnostics.max_topics must be between 1 and 1000.")
    if not 1 <= max_samples <= 10:
        raise ValueError("diagnostics.max_samples must be between 1 and 10.")
    if not 0.1 <= max_timeout_seconds <= 30.0:
        raise ValueError(
            "diagnostics.max_timeout_seconds must be between 0.1 and 30."
        )
    if not 1_024 <= max_output_bytes <= 1_000_000:
        raise ValueError(
            "diagnostics.max_output_bytes must be between 1024 and 1000000."
        )
    return Ros1DiagnosticsConfig(
        enabled=bool(raw.get("enabled", True)),
        topic_allowlist=_diagnostic_string_tuple(
            raw,
            "topic_allowlist",
            DEFAULT_ROS1_DIAGNOSTIC_TOPIC_ALLOWLIST,
        ),
        frame_allowlist=_diagnostic_string_tuple(
            raw,
            "frame_allowlist",
            DEFAULT_ROS1_DIAGNOSTIC_FRAME_ALLOWLIST,
        ),
        action_allowlist=_diagnostic_string_tuple(
            raw,
            "action_allowlist",
            DEFAULT_ROS1_DIAGNOSTIC_ACTION_ALLOWLIST,
        ),
        max_topics=max_topics,
        max_samples=max_samples,
        max_timeout_seconds=max_timeout_seconds,
        max_output_bytes=max_output_bytes,
    )


def _diagnostic_string_tuple(
    raw: dict[str, Any],
    key: str,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    value = raw.get(key, default)
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",")]
    if (
        not isinstance(value, (list, tuple))
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(
            f"diagnostics.{key} must contain non-empty strings."
        )
    return tuple(item.strip() for item in value)


def _required_string(raw: dict[str, Any], key: str, *, prefix: str | None = None) -> str:
    value = raw.get(key)
    label = f"{prefix}.{key}" if prefix else key
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string.")
    return value


def _parse_simple_yaml(content: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for line_number, original_line in enumerate(content.splitlines(), start=1):
        line = original_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent % 2 != 0:
            raise ValueError(f"YAML line {line_number}: indentation must use multiples of two spaces.")
        stripped = line.strip()
        if ":" not in stripped:
            raise ValueError(f"YAML line {line_number}: expected key: value mapping.")
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"YAML line {line_number}: empty key.")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError(f"YAML line {line_number}: invalid indentation.")
        parent = stack[-1][1]
        value = value.strip()
        if value == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_simple_yaml_scalar(value)
    return root


def _parse_simple_yaml_scalar(value: str) -> Any:
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
