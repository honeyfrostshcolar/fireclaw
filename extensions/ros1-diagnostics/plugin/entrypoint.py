"""Built-in, read-only ROS1 diagnostic Tool Plugin."""

from __future__ import annotations

from typing import Any

from fireclaw_plugin_sdk import PluginApi, ToolSpec


def _topic_schema(*, description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "string",
        "minLength": 2,
        "maxLength": 256,
    }
    if description is not None:
        schema["description"] = description
    return schema


def _frame_schema() -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "maxLength": 128}


def _timeout_schema(maximum: float) -> dict[str, Any]:
    return {"type": "number", "minimum": 0.1, "maximum": maximum}


def _policy_hook(backend: Any, tool_name: str):
    def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            backend.validate_tool_call(tool_name, payload.get("arguments"))
        except (TypeError, ValueError) as exc:
            return {
                "block": True,
                "reason_code": "ros_diagnostic_request_denied",
                "message": str(exc)[:500],
            }
        return None

    return validate


def _tools(backend: Any) -> tuple[ToolSpec, ...]:
    max_timeout = backend.policy.max_timeout_seconds
    max_samples = backend.policy.max_samples
    max_topics = backend.policy.max_topics
    common = {
        "effect": "read",
        "roles": ("robot_agent",),
        "modes": ("simulation", "real"),
        "requires_sandbox": False,
        "metadata": {
            "family": "ros1_diagnostics",
            "read_only": True,
            "bounded_sampling": True,
            "extension_owned": True,
        },
    }
    return (
        ToolSpec(
            name="ros_topic_list",
            description="List only policy-allowed ROS1 topics and message types.",
            input_schema={
                "type": "object",
                "properties": {
                    "contains": {"type": "string", "maxLength": 128},
                    "limit": {"type": "integer", "minimum": 1, "maximum": max_topics},
                },
                "additionalProperties": False,
            },
            handler=lambda arguments: backend.execute("ros_topic_list", arguments),
            **common,
        ),
        ToolSpec(
            name="ros_topic_info",
            description="Inspect one policy-allowed ROS1 topic without changing the graph.",
            input_schema={
                "type": "object",
                "properties": {"topic": _topic_schema()},
                "required": ["topic"],
                "additionalProperties": False,
            },
            handler=lambda arguments: backend.execute("ros_topic_info", arguments),
            **common,
        ),
        ToolSpec(
            name="ros_topic_sample",
            description="Read a finite sample from one policy-allowed ROS1 topic.",
            input_schema={
                "type": "object",
                "properties": {
                    "topic": _topic_schema(),
                    "sample_count": {"type": "integer", "minimum": 1, "maximum": max_samples},
                    "timeout_seconds": _timeout_schema(max_timeout),
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
            handler=lambda arguments: backend.execute("ros_topic_sample", arguments),
            **common,
        ),
        ToolSpec(
            name="ros_topic_rate",
            description="Measure a topic publication rate during a bounded window.",
            input_schema={
                "type": "object",
                "properties": {
                    "topic": _topic_schema(),
                    "window_seconds": _timeout_schema(max_timeout),
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
            handler=lambda arguments: backend.execute("ros_topic_rate", arguments),
            **common,
        ),
        ToolSpec(
            name="tf_lookup",
            description="Read one policy-allowed ROS1 transform during a bounded lookup.",
            input_schema={
                "type": "object",
                "properties": {
                    "reference_frame": _frame_schema(),
                    "target_frame": _frame_schema(),
                    "timeout_seconds": _timeout_schema(max_timeout),
                },
                "required": ["reference_frame", "target_frame"],
                "additionalProperties": False,
            },
            handler=lambda arguments: backend.execute("tf_lookup", arguments),
            **common,
        ),
        ToolSpec(
            name="move_base_status",
            description="Read bounded status from an allowed ROS1 move_base instance.",
            input_schema={
                "type": "object",
                "properties": {
                    "action_name": _topic_schema(description="Allowed move_base action namespace."),
                    "timeout_seconds": _timeout_schema(max_timeout),
                },
                "additionalProperties": False,
            },
            handler=lambda arguments: backend.execute("move_base_status", arguments),
            **common,
        ),
        ToolSpec(
            name="navigation_diagnostics",
            description=(
                "Run one bounded read-only navigation triage covering move_base, "
                "laser, odometry, velocity commands and TF."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action_name": _topic_schema(),
                    "global_frame": _frame_schema(),
                    "robot_frame": _frame_schema(),
                    "scan_topic": _topic_schema(),
                    "odom_topic": _topic_schema(),
                    "cmd_vel_topic": _topic_schema(),
                    "timeout_seconds": {
                        **_timeout_schema(max_timeout),
                        "description": "Per-check observation limit.",
                    },
                },
                "additionalProperties": False,
            },
            handler=lambda arguments: backend.execute("navigation_diagnostics", arguments),
            **common,
        ),
    )


def register(api: PluginApi) -> None:
    backend = api.services.get("ros_diagnostics_backend")
    if backend is None or not bool(api.config.get("enabled", True)):
        return
    for tool in _tools(backend):
        api.register_tool(tool, metadata=dict(tool.metadata))
        api.register_hook("before_tool_call", tool.name, _policy_hook(backend, tool.name))
