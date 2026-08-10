from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from fireclaw_core.agent.robot import RobotActionResult, RobotAdapter
from fireclaw_core.execution.action_runtime import (
    RobotActionRuntime,
    accepts_keyword_argument,
    invoke_robot_action_handler,
)
from fireclaw_core.execution.skill_plugin import (
    PhysicalSkillPlugin,
    validate_object_schema,
)
from fireclaw_core.plugin.plugin_host import FireClawPluginHost


SkillHandler = Callable[..., RobotActionResult]
GENERIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}
GENERIC_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}
RISK_LEVELS = {"low", "medium", "high", "critical"}
SKILL_DOMAINS = {
    "navigation",
    "perception",
    "communication",
    "safety",
    "manipulation",
}
DEGRADED_MODE_POLICIES = {"skip", "fallback", "retry", "abort", "escalate"}


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    handler: SkillHandler
    runtime: str = "in_process"
    dry_run_only: bool = True
    max_attempts: int = 1
    idempotent: bool = False
    required_sensors: list[str] = field(default_factory=list)
    failure_categories: list[str] = field(default_factory=list)
    allow_real_robot: bool = False
    timeout_seconds: float | None = None
    cancellation_ack_timeout_seconds: float = 2.0
    input_schema: dict[str, Any] = field(
        default_factory=lambda: dict(GENERIC_INPUT_SCHEMA)
    )
    risk_level: str = "low"
    output_schema: dict[str, Any] = field(
        default_factory=lambda: dict(GENERIC_OUTPUT_SCHEMA)
    )
    domain: str = "navigation"
    preconditions: list[str] = field(default_factory=list)
    degraded_mode_policy: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    physical_plugin: PhysicalSkillPlugin | None = None

    def validate_inputs(self, inputs: Any) -> list[str]:
        return validate_object_schema(self.input_schema, inputs)

    def operator_message(
        self,
        event: str,
        inputs: dict[str, Any],
    ) -> str | None:
        if (
            event == "started"
            and self.physical_plugin is not None
            and self.physical_plugin.operator_started_message is not None
        ):
            return self.physical_plugin.operator_started_message(dict(inputs))
        return None

    def run(
        self,
        inputs: dict[str, Any],
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> RobotActionResult:
        if accepts_keyword_argument(
            self.handler,
            "cancellation_requested",
        ):
            return self.handler(
                inputs,
                cancellation_requested=cancellation_requested,
            )
        return self.handler(inputs)


class SkillRegistry:
    """Executable tool projection backed by the unified plugin host."""

    def __init__(
        self,
        skills: dict[str, Skill],
        *,
        host: FireClawPluginHost | None = None,
    ) -> None:
        self.host = host or FireClawPluginHost()
        for skill in skills.values():
            self.register(skill)

    @property
    def skills(self) -> dict[str, Skill]:
        return {
            item.contribution_id: item.value
            for item in self.host.contributions("tool")
            if isinstance(item.value, Skill)
        }

    def get(self, name: str) -> Skill | None:
        item = self.host.get("tool", name)
        if item is None or not isinstance(item.value, Skill):
            return None
        return item.value

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    def names(self) -> set[str]:
        return set(self.skills)

    def register(self, skill: Skill, *, replace: bool = False) -> None:
        existing = self.host.get("tool", skill.name)
        if existing is not None and not replace:
            raise ValueError(f"Skill already registered: {skill.name}")
        if existing is not None:
            self.host.dispose_plugin(existing.owner_plugin_id)
        owner_plugin_id = (
            skill.physical_plugin.plugin_id
            if skill.physical_plugin is not None
            else str(skill.metadata.get("plugin_id") or f"fireclaw.tool.{skill.name}")
        )
        def register(api) -> None:
            if (
                skill.physical_plugin is not None
                and self.host.get(
                    "physical_capability",
                    skill.physical_plugin.name,
                )
                is None
            ):
                api.register_physical_capability(skill.physical_plugin)
            api.register_tool(skill)

        self.host.activate(
            owner_plugin_id,
            register,
            name=skill.name,
            description=skill.description,
            source=(
                "physical_skill_projection"
                if skill.physical_plugin is not None
                else str(skill.metadata.get("source") or "skill")
            ),
            trust_level="trusted",
        )

    def register_plugin(
        self,
        plugin: PhysicalSkillPlugin,
        *,
        robot: RobotAdapter,
        action_runtime: RobotActionRuntime | None = None,
        replace: bool = False,
    ) -> Skill:
        existing_physical = self.host.get(
            "physical_capability",
            plugin.name,
        )
        if existing_physical is not None:
            if not replace and existing_physical.owner_plugin_id != plugin.plugin_id:
                raise ValueError(
                    f"Physical skill already registered: {plugin.name}"
                )
            if existing_physical.owner_plugin_id != plugin.plugin_id:
                self.host.dispose_plugin(existing_physical.owner_plugin_id)
        skill = skill_from_physical_plugin(
            plugin,
            robot=robot,
            action_runtime=action_runtime,
        )
        if self.host.get("physical_capability", plugin.name) is None:
            self.host.activate(
                plugin.plugin_id,
                lambda api: api.register_physical_capability(plugin),
                name=plugin.label,
                description=plugin.description,
                source="physical_skill",
                trust_level="trusted",
            )
        self.register(skill, replace=replace)
        return skill

    def extend(self, skills: list[Skill], *, replace: bool = False) -> None:
        for skill in skills:
            self.register(skill, replace=replace)

    def list_metadata(self) -> list[dict[str, Any]]:
        return [
            {
                "name": skill.name,
                "description": skill.description,
                "runtime": skill.runtime,
                "dry_run_only": skill.dry_run_only,
                "max_attempts": skill.max_attempts,
                "idempotent": skill.idempotent,
                "required_sensors": list(skill.required_sensors),
                "failure_categories": list(skill.failure_categories),
                "allow_real_robot": skill.allow_real_robot,
                "timeout_seconds": skill.timeout_seconds,
                "cancellation_ack_timeout_seconds": (
                    skill.cancellation_ack_timeout_seconds
                ),
                "input_schema": dict(skill.input_schema),
                "risk_level": skill.risk_level,
                "output_schema": dict(skill.output_schema),
                "domain": skill.domain,
                "preconditions": list(skill.preconditions),
                "degraded_mode_policy": skill.degraded_mode_policy,
                "metadata": dict(skill.metadata),
                "physical_plugin": (
                    skill.physical_plugin.to_metadata()
                    if skill.physical_plugin is not None
                    else None
                ),
            }
            for skill in sorted(self.skills.values(), key=lambda item: item.name)
        ]


def skill_from_physical_plugin(
    plugin: PhysicalSkillPlugin,
    *,
    robot: RobotAdapter,
    action_runtime: RobotActionRuntime | None = None,
) -> Skill:
    """Bind one trusted plugin definition to one robot adapter."""

    def direct_action(**kwargs: Any) -> RobotActionResult:
        feedback_sink = kwargs.pop("feedback_sink", None)
        cancellation_requested = kwargs.pop("cancellation_requested", None)
        raw_result = plugin.action_handler(
            dict(kwargs),
            feedback_sink=feedback_sink,
            cancellation_requested=cancellation_requested,
        )
        return _coerce_physical_action_result(
            raw_result,
            robot=robot,
            action=plugin.action,
            inputs=kwargs,
        )
    if action_runtime is not None:
        register_action = getattr(action_runtime.backend, "register_action", None)
        if callable(register_action):
            register_action(plugin.action, direct_action, replace=True)

    def handler(
        inputs: dict[str, Any],
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> RobotActionResult:
        action_inputs = plugin.action_input_builder(robot, dict(inputs))
        if action_runtime is not None:
            return action_runtime.run(
                skill_name=plugin.name,
                action_type=plugin.action,
                inputs=action_inputs,
                dry_run=robot.dry_run,
                risk_level=plugin.risk_level,
                timeout_seconds=plugin.timeout_seconds,
                cancellation_ack_timeout_seconds=(
                    plugin.cancellation_ack_timeout_seconds
                ),
                cancellation_requested=cancellation_requested,
            )
        return invoke_robot_action_handler(
            direct_action,
            action_inputs,
            cancellation_requested=cancellation_requested,
        )

    runtime_dry_run_only = bool(getattr(robot, "dry_run", True))
    return Skill(
        name=plugin.name,
        description=plugin.description,
        handler=handler,
        runtime="in_process",
        dry_run_only=plugin.dry_run_only and runtime_dry_run_only,
        max_attempts=plugin.max_attempts,
        idempotent=plugin.idempotent,
        required_sensors=list(plugin.required_sensors),
        failure_categories=list(plugin.failure_categories),
        allow_real_robot=plugin.allow_real_robot,
        timeout_seconds=plugin.timeout_seconds,
        cancellation_ack_timeout_seconds=(
            plugin.cancellation_ack_timeout_seconds
        ),
        input_schema=dict(plugin.parameters),
        risk_level=plugin.risk_level,
        output_schema=dict(plugin.output_schema),
        domain=plugin.domain,
        preconditions=list(plugin.preconditions),
        degraded_mode_policy=plugin.degraded_mode_policy,
        metadata=plugin.to_metadata(),
        physical_plugin=plugin,
    )


def _coerce_physical_action_result(
    value: Any,
    *,
    robot: RobotAdapter,
    action: str,
    inputs: dict[str, Any],
) -> RobotActionResult:
    """Normalize a Plugin result without exposing core result types in SDK."""

    if isinstance(value, RobotActionResult):
        return value
    if not isinstance(value, dict):
        raise TypeError(
            f"Physical Plugin action {action!r} must return an object result."
        )
    status = str(value.get("status") or ("succeeded" if value.get("ok") else "failed"))
    ok = bool(value.get("ok", status == "succeeded"))
    raw_data = value.get("data")
    data = dict(raw_data) if isinstance(raw_data, dict) else dict(value)
    return RobotActionResult(
        ok=ok,
        status=status,
        robot_id=str(value.get("robot_id") or getattr(robot, "robot_id", "unknown")),
        mode=str(value.get("mode") or getattr(robot, "mode", "unknown")),
        action=str(value.get("action") or action),
        dry_run=bool(value.get("dry_run", getattr(robot, "dry_run", True))),
        data=data,
        timestamp=str(
            value.get("timestamp")
            or datetime.now(timezone.utc).isoformat()
        ),
        error=(str(value["error"]) if value.get("error") is not None else None),
    )


def create_default_skill_registry(
    robot: RobotAdapter,
    action_runtime: RobotActionRuntime | None = None,
    *,
    plugin_host: FireClawPluginHost | None = None,
) -> SkillRegistry:
    if plugin_host is None:
        plugin_host = FireClawPluginHost()
    registry = SkillRegistry(skills={}, host=plugin_host)
    for contribution in plugin_host.contributions("physical_capability"):
        plugin = contribution.value
        if isinstance(plugin, PhysicalSkillPlugin):
            registry.register_plugin(
                plugin,
                robot=robot,
                action_runtime=action_runtime,
            )
    return registry
