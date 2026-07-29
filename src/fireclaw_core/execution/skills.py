from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fireclaw_core.execution.action_runtime import RobotActionRuntime
from fireclaw_core.agent.robot import RobotActionResult, RobotAdapter
from fireclaw_core.execution.runtime import SubprocessSkillRunner


SkillHandler = Callable[..., RobotActionResult]

GENERIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}

FLOOR_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "floor": {"type": "integer"},
    },
    "required": ["floor"],
    "additionalProperties": False,
}

LOCAL_CONTEXT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "floor": {
            "type": "integer",
            "description": "Legacy multi-floor compatibility field.",
        },
    },
    "additionalProperties": False,
}

POINT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "x": {"type": "number"},
        "y": {"type": "number"},
        "yaw": {"type": "number", "default": 0.0},
        "frame_id": {"type": "string", "minLength": 1, "default": "map"},
    },
    "required": ["x", "y"],
    "additionalProperties": False,
}

EMPTY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

RISK_LEVELS = {"low", "medium", "high", "critical"}

# --- Phase 2: typed output schemas ---

GENERIC_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}

NAVIGATE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "from_floor": {"type": "integer"},
    },
    "required": ["robot_id", "floor"],
}

NAVIGATE_POINT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "yaw": {"type": "number"},
        "frame_id": {"type": "string"},
    },
    "required": ["robot_id", "x", "y", "yaw", "frame_id"],
}

SEARCH_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "victims_found": {"type": "integer"},
    },
    "required": ["robot_id", "floor", "victims_found"],
}

ASSESS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "condition": {"type": "string"},
    },
    "required": ["robot_id", "floor", "condition"],
}

REPORT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "message": {"type": "string"},
    },
    "required": ["robot_id", "floor", "message"],
}

EMPTY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
}

# --- Phase 2: skill domains ---

SKILL_DOMAINS = {"navigation", "perception", "communication", "safety", "manipulation"}

# --- Phase 2: degraded mode policies ---

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
    input_schema: dict[str, Any] = field(default_factory=lambda: dict(GENERIC_INPUT_SCHEMA))
    risk_level: str = "low"
    # Phase 2: richer metadata and typed contracts
    output_schema: dict[str, Any] = field(default_factory=lambda: dict(GENERIC_OUTPUT_SCHEMA))
    domain: str = "navigation"
    preconditions: list[str] = field(default_factory=list)
    degraded_mode_policy: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def run(
        self,
        inputs: dict[str, Any],
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> RobotActionResult:
        if self.runtime == "subprocess":
            return self.handler(inputs, cancellation_requested=cancellation_requested)
        try:
            return self.handler(inputs, cancellation_requested=cancellation_requested)
        except TypeError:
            return self.handler(inputs)


@dataclass
class SkillRegistry:
    skills: dict[str, Skill]

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def has(self, name: str) -> bool:
        return name in self.skills

    def names(self) -> set[str]:
        return set(self.skills)

    def register(self, skill: Skill, *, replace: bool = False) -> None:
        if skill.name in self.skills and not replace:
            raise ValueError(f"Skill already registered: {skill.name}")
        self.skills[skill.name] = skill

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
                "input_schema": dict(skill.input_schema),
                "risk_level": skill.risk_level,
                "output_schema": dict(skill.output_schema),
                "domain": skill.domain,
                "preconditions": list(skill.preconditions),
                "degraded_mode_policy": skill.degraded_mode_policy,
                "metadata": dict(skill.metadata),
            }
            for skill in sorted(self.skills.values(), key=lambda item: item.name)
        ]


def _robot_skill_handler(
    *,
    robot: RobotAdapter,
    action_runtime: RobotActionRuntime | None,
    skill_name: str,
    action_type: str,
    input_builder: Callable[[dict[str, Any]], dict[str, Any]],
    direct_handler: Callable[[dict[str, Any]], RobotActionResult],
    risk_level: str = "low",
) -> SkillHandler:
    def handler(
        inputs: dict[str, Any],
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> RobotActionResult:
        if action_runtime is None:
            return direct_handler(inputs)
        action_inputs = input_builder(inputs)
        return action_runtime.run(
            skill_name=skill_name,
            action_type=action_type,
            inputs=action_inputs,
            dry_run=robot.dry_run,
            risk_level=risk_level,
            timeout_seconds=None,
            cancellation_requested=cancellation_requested,
        )

    return handler


def _current_floor_input(robot: RobotAdapter, inputs: dict[str, Any]) -> int:
    floor = inputs.get("floor")
    if isinstance(floor, int) and not isinstance(floor, bool):
        return floor
    current_floor = getattr(robot, "current_floor", None)
    return current_floor if isinstance(current_floor, int) else 1


def create_default_skill_registry(
    robot: RobotAdapter,
    action_runtime: RobotActionRuntime | None = None,
) -> SkillRegistry:
    runtime_dry_run_only = bool(getattr(robot, "dry_run", True))
    return SkillRegistry(
        skills={
            "navigate_to_point": Skill(
                name="navigate_to_point",
                description=(
                    "Navigate within the current single-floor map to a target "
                    "point expressed in an explicit coordinate frame."
                ),
                handler=_robot_skill_handler(
                    robot=robot,
                    action_runtime=action_runtime,
                    skill_name="navigate_to_point",
                    action_type="navigate_to_point",
                    input_builder=lambda inputs: {
                        "x": float(inputs["x"]),
                        "y": float(inputs["y"]),
                        "yaw": float(inputs.get("yaw", 0.0)),
                        "frame_id": str(inputs.get("frame_id", "map")),
                    },
                    direct_handler=lambda inputs: robot.navigate_to_point(
                        float(inputs["x"]),
                        float(inputs["y"]),
                        float(inputs.get("yaw", 0.0)),
                        str(inputs.get("frame_id", "map")),
                    ),
                ),
                input_schema=dict(POINT_INPUT_SCHEMA),
                output_schema=dict(NAVIGATE_POINT_OUTPUT_SCHEMA),
                domain="navigation",
                preconditions=["robot_online", "target_point_reachable"],
                required_sensors=["lidar"],
                degraded_mode_policy="retry",
                idempotent=True,
                dry_run_only=runtime_dry_run_only,
                allow_real_robot=True,
                metadata={
                    "kind": "primitive",
                    "primitive_capability": "navigation",
                    "spatial_scope": "single_floor_2d",
                    "safety_class": "motion",
                    "requires_approval": False,
                },
            ),
            "navigate_to_floor": Skill(
                name="navigate_to_floor",
                description="Navigate robot to a target floor.",
                handler=_robot_skill_handler(
                    robot=robot,
                    action_runtime=action_runtime,
                    skill_name="navigate_to_floor",
                    action_type="navigate_to_floor",
                    input_builder=lambda inputs: {"floor": int(inputs["floor"])},
                    direct_handler=lambda inputs: robot.navigate_to_floor(int(inputs["floor"])),
                ),
                input_schema=dict(FLOOR_INPUT_SCHEMA),
                output_schema=dict(NAVIGATE_OUTPUT_SCHEMA),
                domain="navigation",
                preconditions=["robot_online", "floor_reachable"],
                required_sensors=["lidar"],
                degraded_mode_policy="retry",
                idempotent=True,
                dry_run_only=runtime_dry_run_only,
                allow_real_robot=True,
                metadata={
                    "kind": "primitive",
                    "primitive_capability": "navigation",
                    "legacy": True,
                    "spatial_scope": "multi_floor_extension",
                    "input_schema": {
                        "type": "object",
                        "properties": {"floor": {"type": "integer", "minimum": 1}},
                        "required": ["floor"],
                    },
                    "safety_class": "motion",
                    "requires_approval": False,
                },
            ),
            "search_for_victims": Skill(
                name="search_for_victims",
                description="Search for victims in the robot's current operating area.",
                handler=_robot_skill_handler(
                    robot=robot,
                    action_runtime=action_runtime,
                    skill_name="search_for_victims",
                    action_type="search_for_victims",
                    input_builder=lambda inputs: {
                        "floor": _current_floor_input(robot, inputs)
                    },
                    direct_handler=lambda inputs: robot.search_for_victims(
                        _current_floor_input(robot, inputs)
                    ),
                ),
                input_schema=dict(LOCAL_CONTEXT_INPUT_SCHEMA),
                output_schema=dict(SEARCH_OUTPUT_SCHEMA),
                domain="perception",
                preconditions=["robot_online", "camera_available"],
                required_sensors=["rgb_camera"],
                degraded_mode_policy="fallback",
                dry_run_only=runtime_dry_run_only,
                allow_real_robot=True,
                metadata={
                    "kind": "composite",
                    "primitive_capability": "perception",
                    "input_schema": {
                        "type": "object",
                        "properties": {"floor": {"type": "integer", "minimum": 1}},
                        "required": [],
                    },
                    "safety_class": "perception",
                    "requires_approval": False,
                },
            ),
            "assess_victim": Skill(
                name="assess_victim",
                description="Assess victim condition in the current operating area.",
                handler=_robot_skill_handler(
                    robot=robot,
                    action_runtime=action_runtime,
                    skill_name="assess_victim",
                    action_type="assess_victim",
                    input_builder=lambda inputs: {
                        "floor": _current_floor_input(robot, inputs)
                    },
                    direct_handler=lambda inputs: robot.assess_victim(
                        _current_floor_input(robot, inputs)
                    ),
                ),
                input_schema=dict(LOCAL_CONTEXT_INPUT_SCHEMA),
                output_schema=dict(ASSESS_OUTPUT_SCHEMA),
                domain="perception",
                preconditions=["robot_online", "victim_detected"],
                required_sensors=["rgb_camera", "thermal_camera"],
                degraded_mode_policy="skip",
                dry_run_only=runtime_dry_run_only,
                allow_real_robot=True,
                metadata={
                    "kind": "composite",
                    "primitive_capability": "perception",
                    "input_schema": {
                        "type": "object",
                        "properties": {"floor": {"type": "integer", "minimum": 1}},
                        "required": [],
                    },
                    "safety_class": "perception",
                    "requires_approval": False,
                },
            ),
            "report_status": Skill(
                name="report_status",
                description="Report status to operator.",
                handler=_robot_skill_handler(
                    robot=robot,
                    action_runtime=action_runtime,
                    skill_name="report_status",
                    action_type="report_status",
                    input_builder=lambda inputs: {
                        "floor": _current_floor_input(robot, inputs)
                    },
                    direct_handler=lambda inputs: robot.report_status(
                        _current_floor_input(robot, inputs)
                    ),
                ),
                input_schema=dict(LOCAL_CONTEXT_INPUT_SCHEMA),
                output_schema=dict(REPORT_OUTPUT_SCHEMA),
                domain="communication",
                preconditions=["robot_online"],
                degraded_mode_policy="retry",
                idempotent=True,
                dry_run_only=runtime_dry_run_only,
                allow_real_robot=True,
                metadata={
                    "kind": "primitive",
                    "primitive_capability": "communication",
                    "input_schema": {
                        "type": "object",
                        "properties": {"floor": {"type": "integer", "minimum": 1}},
                        "required": [],
                    },
                    "safety_class": "reporting",
                    "requires_approval": False,
                },
            ),
            "return_to_safe_zone": Skill(
                name="return_to_safe_zone",
                description="Return robot to safe zone.",
                handler=_robot_skill_handler(
                    robot=robot,
                    action_runtime=action_runtime,
                    skill_name="return_to_safe_zone",
                    action_type="return_to_safe_zone",
                    input_builder=lambda inputs: {},
                    direct_handler=lambda inputs: robot.return_to_safe_zone(),
                ),
                input_schema=dict(EMPTY_INPUT_SCHEMA),
                output_schema=dict(EMPTY_OUTPUT_SCHEMA),
                domain="safety",
                preconditions=["robot_online"],
                degraded_mode_policy="abort",
                idempotent=True,
                dry_run_only=runtime_dry_run_only,
                allow_real_robot=True,
                risk_level="medium",
                metadata={
                    "kind": "composite",
                    "primitive_capability": "navigation",
                    "input_schema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                    "safety_class": "motion",
                    "requires_approval": False,
                },
            ),
        }
    )


def create_subprocess_skill(
    *,
    name: str,
    description: str,
    command: list[str],
    timeout_seconds: float = 30.0,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    dry_run_only: bool = True,
    max_attempts: int = 1,
    idempotent: bool = False,
    required_sensors: list[str] | None = None,
    failure_categories: list[str] | None = None,
    allow_real_robot: bool = False,
    input_schema: dict[str, Any] | None = None,
    risk_level: str = "low",
    output_schema: dict[str, Any] | None = None,
    domain: str = "navigation",
    preconditions: list[str] | None = None,
    degraded_mode_policy: str | None = None,
) -> Skill:
    runner = SubprocessSkillRunner(
        command=command,
        timeout_seconds=timeout_seconds,
        cwd=cwd,
        env=env or {},
    )
    return Skill(
        name=name,
        description=description,
        handler=runner.run,
        runtime="subprocess",
        dry_run_only=dry_run_only,
        max_attempts=max_attempts,
        idempotent=idempotent,
        required_sensors=required_sensors or [],
        failure_categories=failure_categories or [],
        allow_real_robot=allow_real_robot,
        timeout_seconds=float(timeout_seconds),
        input_schema=input_schema or dict(GENERIC_INPUT_SCHEMA),
        risk_level=risk_level,
        output_schema=output_schema or dict(GENERIC_OUTPUT_SCHEMA),
        domain=domain,
        preconditions=preconditions or [],
        degraded_mode_policy=degraded_mode_policy,
    )
