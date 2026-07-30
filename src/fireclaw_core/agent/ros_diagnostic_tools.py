from __future__ import annotations

from typing import Any

from fireclaw_core.agent.tool_runtime import AgentTool
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.ros.ros1_diagnostics import Ros1DiagnosticsBackend


ROS1_DIAGNOSTIC_TOOL_PLUGIN_ID = (
    "fireclaw.agent-tools.ros1-diagnostics"
)


def register_ros1_diagnostic_tool_plugin(
    host: FireClawPluginHost,
    backend: Ros1DiagnosticsBackend,
    *,
    plugin_id: str = ROS1_DIAGNOSTIC_TOOL_PLUGIN_ID,
) -> None:
    tools = ros1_diagnostic_agent_tools(backend)

    def register(api) -> None:
        for tool in tools:
            api.register_tool(
                tool,
                metadata={
                    "tool_class": "agent_tool",
                    "effect": tool.effect,
                    "roles": list(tool.roles),
                    "modes": list(tool.modes),
                    "requires_sandbox": tool.requires_sandbox,
                    "family": "ros1_diagnostics",
                    "read_only": True,
                    "bounded_sampling": True,
                },
            )
            api.register_hook(
                "before_tool_call",
                tool.name,
                _policy_hook(backend, tool.name),
            )

    host.activate(
        plugin_id,
        register,
        name="ROS1 diagnostic tools",
        description=(
            "Typed, read-only and bounded ROS1 observations for the Robot "
            "Agent."
        ),
        source="builtin_agent_tool_plugin",
        trust_level="builtin",
    )


def ros1_diagnostic_agent_tools(
    backend: Ros1DiagnosticsBackend,
) -> tuple[AgentTool, ...]:
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
        },
    }
    return (
        AgentTool(
            name="ros_topic_list",
            description=(
                "List only policy-allowed ROS1 topics and message types. "
                "Results are bounded and advisory."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "contains": {
                        "type": "string",
                        "maxLength": 128,
                        "description": (
                            "Optional literal substring used to filter topic "
                            "names."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": max_topics,
                    },
                },
                "additionalProperties": False,
            },
            handler=lambda arguments: backend.execute(
                "ros_topic_list",
                arguments,
            ),
            **common,
        ),
        AgentTool(
            name="ros_topic_info",
            description=(
                "Inspect the type, publishers and subscribers of one "
                "policy-allowed ROS1 topic without changing the graph."
            ),
            input_schema={
                "type": "object",
                "properties": {"topic": _topic_schema()},
                "required": ["topic"],
                "additionalProperties": False,
            },
            handler=lambda arguments: backend.execute(
                "ros_topic_info",
                arguments,
            ),
            **common,
        ),
        AgentTool(
            name="ros_topic_sample",
            description=(
                "Read a small finite sample from one policy-allowed ROS1 "
                "topic. Large arrays and values are omitted or truncated."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "topic": _topic_schema(),
                    "sample_count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": max_samples,
                    },
                    "timeout_seconds": _timeout_schema(max_timeout),
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
            handler=lambda arguments: backend.execute(
                "ros_topic_sample",
                arguments,
            ),
            **common,
        ),
        AgentTool(
            name="ros_topic_rate",
            description=(
                "Measure a ROS1 topic's publication rate during a short "
                "bounded observation window."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "topic": _topic_schema(),
                    "window_seconds": {
                        **_timeout_schema(max_timeout),
                        "description": (
                            "Maximum observation window; the backend stops "
                            "sampling when it expires."
                        ),
                    },
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
            handler=lambda arguments: backend.execute(
                "ros_topic_rate",
                arguments,
            ),
            **common,
        ),
        AgentTool(
            name="tf_lookup",
            description=(
                "Read one policy-allowed ROS1 transform during a bounded "
                "lookup window; never publishes or modifies TF."
            ),
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
            handler=lambda arguments: backend.execute(
                "tf_lookup",
                arguments,
            ),
            **common,
        ),
        AgentTool(
            name="move_base_status",
            description=(
                "Read the bounded action status of an allowed ROS1 "
                "move_base instance and return structured goal states."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action_name": _topic_schema(
                        description=(
                            "Allowed move_base action namespace, normally "
                            "/move_base."
                        )
                    ),
                    "timeout_seconds": _timeout_schema(max_timeout),
                },
                "additionalProperties": False,
            },
            handler=lambda arguments: backend.execute(
                "move_base_status",
                arguments,
            ),
            **common,
        ),
        AgentTool(
            name="navigation_diagnostics",
            description=(
                "Run one bounded read-only navigation triage: move_base "
                "status, laser, odometry, velocity commands and TF. Use this "
                "first when navigation is stuck or times out."
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
                        "description": (
                            "Per-check observation limit; checks run in "
                            "parallel."
                        ),
                    },
                },
                "additionalProperties": False,
            },
            handler=lambda arguments: backend.execute(
                "navigation_diagnostics",
                arguments,
            ),
            **common,
        ),
    )


def _policy_hook(
    backend: Ros1DiagnosticsBackend,
    tool_name: str,
):
    def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
        arguments = payload.get("arguments")
        try:
            backend.validate_tool_call(tool_name, arguments)
        except (TypeError, ValueError) as exc:
            return {
                "block": True,
                "reason_code": "ros_diagnostic_request_denied",
                "message": str(exc)[:500],
            }
        return None

    return validate


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
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
    }


def _timeout_schema(maximum: float) -> dict[str, Any]:
    return {
        "type": "number",
        "minimum": 0.1,
        "maximum": maximum,
    }
