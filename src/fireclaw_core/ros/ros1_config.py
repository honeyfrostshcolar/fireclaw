from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

# Compatibility endpoint names for the legacy ROS1 adapter configuration.
# Plugin-owned physical Tools provide their own runtime endpoint contract and
# are not imported while this low-level config module is initializing.
ROS1_ACTION_NAMES = (
    "navigate_to_floor",
    "search_for_victims",
    "assess_victim",
    "report_status",
    "return_to_safe_zone",
    "emergency_stop",
)
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
ROS1_ENDPOINT_PROFILES: dict[str, dict[str, Any]] = {
    "move_base": {
        "interface": "action",
        "type": "move_base_msgs/MoveBaseAction",
        "cancel_supported": True,
        "feedback_supported": True,
    },
    "trigger_service": {
        "interface": "service",
        "type": "std_srvs/Trigger",
        "cancel_supported": False,
        "feedback_supported": False,
    },
    "string_topic": {
        "interface": "topic",
        "type": "std_msgs/String",
        "cancel_supported": False,
        "feedback_supported": False,
    },
}


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
class Ros1TimeoutConfig:
    default_seconds: float = 30.0


@dataclass(frozen=True)
class Ros1TransportConfig:
    enabled: bool = False
    wait_for_server_seconds: float = 5.0
    wait_for_result_seconds: float = 30.0


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
    endpoints: dict[str, Ros1EndpointConfig] = field(default_factory=dict)
    emergency_stop: Ros1EmergencyStopConfig | None = None
    timeouts: Ros1TimeoutConfig = field(default_factory=Ros1TimeoutConfig)
    targets: dict[str, Any] = field(default_factory=dict)
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

    endpoints_raw = raw.get("remap", raw.get("endpoints", {}))
    if not isinstance(endpoints_raw, dict):
        raise ValueError("endpoints/remap must be an object.")
    endpoints_source = dict(endpoints_raw)
    emergency_stop_from_remap = endpoints_source.pop("emergency_stop", None)
    endpoints = {
        action_name: _parse_endpoint_config(action_name, endpoint_raw)
        for action_name, endpoint_raw in endpoints_source.items()
    }

    emergency_stop = None
    emergency_stop_raw = raw.get("emergency_stop", emergency_stop_from_remap)
    if emergency_stop_raw is not None:
        emergency_stop = _parse_emergency_stop_config(emergency_stop_raw)

    timeouts_raw = raw.get("timeouts", {})
    if not isinstance(timeouts_raw, dict):
        raise ValueError("timeouts must be an object.")
    timeouts = Ros1TimeoutConfig(default_seconds=float(timeouts_raw.get("default_seconds", 30.0)))
    if timeouts.default_seconds <= 0:
        raise ValueError("timeouts.default_seconds must be greater than zero.")
    targets = raw.get("targets", {})
    if not isinstance(targets, dict):
        raise ValueError("targets must be an object.")
    transport = _parse_transport_config(raw.get("transport", {}))
    diagnostics = _parse_diagnostics_config(raw.get("diagnostics", {}))

    return Ros1AdapterConfig(
        robot_id=robot_id,
        namespace=namespace,
        endpoints=endpoints,
        emergency_stop=emergency_stop,
        timeouts=timeouts,
        targets=targets,
        transport=transport,
        diagnostics=diagnostics,
    )


def _parse_endpoint_config(action_name: str, raw: Any) -> Ros1EndpointConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"{action_name} endpoint must be an object.")
    expanded = _expand_endpoint_profile(action_name, raw)
    interface = _required_string(expanded, "interface", prefix=action_name)
    if interface not in ROS1_INTERFACES:
        raise ValueError(f"{action_name}.interface must be one of {', '.join(ROS1_INTERFACES)}.")
    return Ros1EndpointConfig(
        interface=interface,
        name=_required_string(expanded, "name", prefix=action_name),
        type=_required_string(expanded, "type", prefix=action_name),
        cancel_supported=bool(expanded.get("cancel_supported", False)),
        feedback_supported=bool(expanded.get("feedback_supported", False)),
        profile=expanded.get("profile"),
        goal_template=_optional_mapping(expanded, "goal_template", prefix=action_name),
        request_template=_optional_mapping(expanded, "request_template", prefix=action_name),
    )


def _parse_emergency_stop_config(raw: Any) -> Ros1EmergencyStopConfig:
    if not isinstance(raw, dict):
        raise ValueError("emergency_stop must be an object.")
    expanded = _expand_endpoint_profile("emergency_stop", raw)
    interface = _required_string(expanded, "interface", prefix="emergency_stop")
    if interface not in ROS1_INTERFACES:
        raise ValueError(f"emergency_stop.interface must be one of {', '.join(ROS1_INTERFACES)}.")
    return Ros1EmergencyStopConfig(
        interface=interface,
        name=_required_string(expanded, "name", prefix="emergency_stop"),
        type=_required_string(expanded, "type", prefix="emergency_stop"),
    )


def _parse_transport_config(raw: Any) -> Ros1TransportConfig:
    if not isinstance(raw, dict):
        raise ValueError("transport must be an object.")
    wait_for_server_seconds = float(raw.get("wait_for_server_seconds", 5.0))
    wait_for_result_seconds = float(raw.get("wait_for_result_seconds", 30.0))
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


def _expand_endpoint_profile(action_name: str, raw: dict[str, Any]) -> dict[str, Any]:
    profile = raw.get("profile")
    if profile is None:
        return dict(raw)
    if not isinstance(profile, str) or not profile:
        raise ValueError(f"{action_name}.profile must be a non-empty string.")
    if profile not in ROS1_ENDPOINT_PROFILES:
        raise ValueError(f"{action_name}.profile is unknown: {profile}")
    return {**ROS1_ENDPOINT_PROFILES[profile], **raw}


def _optional_mapping(raw: dict[str, Any], key: str, *, prefix: str) -> dict[str, Any]:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{prefix}.{key} must be an object.")
    return value


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
