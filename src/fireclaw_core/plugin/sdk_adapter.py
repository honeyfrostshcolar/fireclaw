"""Adapter from the public Plugin SDK to FireClaw's internal Tool model."""

from __future__ import annotations

from inspect import Parameter, signature
from typing import Any

from fireclaw_plugin_sdk import PhysicalToolSpec, TaskInputBindingSpec, ToolSpec


def normalize_registered_tool(value: Any) -> Any:
    """Convert a public :class:`ToolSpec` at the host boundary.

    Existing first-party callers may still pass an internal ``AgentTool`` or
    a legacy test Tool.  Those values are returned unchanged for compatibility
    and are validated by the existing runtime projection.
    """

    if not isinstance(value, ToolSpec):
        return value

    # Keep the public SDK independent from the agent runtime.  Importing this
    # only while a Tool is registered avoids a package-initialization cycle.
    from fireclaw_core.agent.tool_runtime import AgentTool

    return AgentTool(
        name=value.name,
        description=value.description,
        input_schema=dict(value.input_schema),
        handler=value.handler,
        effect=value.effect,
        roles=tuple(value.roles),
        modes=tuple(value.modes),
        requires_sandbox=value.requires_sandbox,
        result_authority=value.result_authority,
        max_execution_seconds=float(value.max_execution_seconds),
        max_result_bytes=value.max_result_bytes,
        metadata=dict(value.metadata),
    )


def normalize_registered_physical_capability(value: Any) -> Any:
    """Convert a public physical Tool contract at the host boundary.

    The public SDK deliberately does not import FireClaw's lifecycle or ROS
    types.  This adapter is the only place where the host turns that neutral
    contract into the legacy ``PhysicalSkillPlugin`` model used by task and
    safety projections.
    """

    if not isinstance(value, PhysicalToolSpec):
        return value

    from fireclaw_core.execution.skill_plugin import (
        TaskInputBinding,
        define_physical_skill_plugin,
    )

    bindings = tuple(
        _normalize_task_input_binding(binding)
        for binding in value.task_input_bindings
    )

    def input_builder(_robot: Any, inputs: dict[str, Any]) -> dict[str, Any]:
        if value.input_builder is None:
            return dict(inputs)
        return dict(value.input_builder(dict(inputs)))

    def action_handler(
        inputs: dict[str, Any],
        *,
        feedback_sink: Any = None,
        cancellation_requested: Any = None,
    ) -> Any:
        kwargs = {
            "feedback_sink": feedback_sink,
            "cancellation_requested": cancellation_requested,
        }
        try:
            parameters = signature(value.handler).parameters
        except (TypeError, ValueError):
            # Some extension callables do not expose inspectable signatures;
            # the minimal public contract remains a single input object.
            return value.handler(dict(inputs))
        accepts_kwargs = any(
            item.kind == Parameter.VAR_KEYWORD
            for item in parameters.values()
        )
        if accepts_kwargs:
            return value.handler(dict(inputs), **kwargs)
        supported = {
            name: item
            for name, item in kwargs.items()
            if name in parameters
        }
        return value.handler(dict(inputs), **supported)

    return define_physical_skill_plugin(
        plugin_id=value.plugin_id,
        name=value.name,
        label=value.label,
        description=value.description,
        parameters=dict(value.input_schema),
        output_schema=dict(value.output_schema),
        action=value.action,
        action_input_builder=input_builder,
        action_handler=action_handler,
        domain=value.domain,
        safety_class=value.safety_class,
        risk_level=value.risk_level,
        dry_run_only=value.dry_run_only,
        allow_real_robot=value.allow_real_robot,
        max_attempts=value.max_attempts,
        idempotent=value.idempotent,
        timeout_seconds=value.timeout_seconds,
        cancellation_ack_timeout_seconds=(
            value.cancellation_ack_timeout_seconds
        ),
        required_sensors=tuple(value.required_sensors),
        sensor_alternatives={
            str(sensor): tuple(alternatives)
            for sensor, alternatives in value.sensor_alternatives.items()
        },
        failure_categories=tuple(value.failure_categories),
        preconditions=tuple(value.preconditions),
        degraded_mode_policy=value.degraded_mode_policy,
        task_input_bindings=bindings,
        auto_include_for_target=value.auto_include_for_target,
        task_type=value.task_type,
        default_followup_skills=tuple(value.default_followup_tools),
        robot_agent_supplemental=value.robot_agent_supplemental,
        resource_locks=tuple(value.resource_locks),
        success_evidence=tuple(value.success_evidence),
        operator_started_message=value.operator_started_message,
        metadata=dict(value.metadata),
    )


def _normalize_task_input_binding(value: TaskInputBindingSpec) -> Any:
    from fireclaw_core.execution.skill_plugin import TaskInputBinding

    kwargs: dict[str, Any] = {
        "input_name": value.input_name,
        "source_paths": tuple(value.source_paths),
        "required": value.required,
        "protected": value.protected,
        "coercion": value.coercion,
    }
    if value.has_default:
        kwargs["default"] = value.default
    return TaskInputBinding(**kwargs)


__all__ = [
    "normalize_registered_physical_capability",
    "normalize_registered_tool",
]
