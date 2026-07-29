# src/fireclaw_core/task_contract.py
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import isfinite
from typing import Any

from fireclaw_core.mission.mission_planner import MissionSubtask
from fireclaw_core.planner.planner import Plan, PlanningResult, PlanStep

VALID_PRIORITIES = {"low", "normal", "high", "emergency"}
VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}
FLOOR_SKILLS = {"navigate_to_floor", "search_for_victims", "assess_victim", "report_status"}
POINT_SKILLS = {"navigate_to_point"}


@dataclass(frozen=True)
class MemoryLineage:
    """Control-plane event IDs carried across memory-store boundaries."""

    runtime_mode: str
    command_event_id: str | None = None
    plan_event_id: str | None = None
    subtask_event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MemoryLineage":
        return cls(
            runtime_mode=str(payload.get("runtime_mode") or ""),
            command_event_id=_optional_str(payload.get("command_event_id")),
            plan_event_id=_optional_str(payload.get("plan_event_id")),
            subtask_event_id=_optional_str(payload.get("subtask_event_id")),
        )


@dataclass(frozen=True)
class StructuredRobotTask:
    task_id: str
    task_type: str
    target: dict[str, Any]
    required_skills: list[str]
    allowed_skills: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    priority: str = "normal"
    risk_level: str = "low"
    operator_id: str | None = None
    mission_id: str | None = None
    robot_id: str | None = None
    command: str | None = None
    memory_lineage: MemoryLineage | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.memory_lineage is None:
            payload.pop("memory_lineage")
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StructuredRobotTask":
        return cls(
            task_id=str(payload.get("task_id") or ""),
            task_type=str(payload.get("task_type") or ""),
            target=dict(payload.get("target") or {}),
            required_skills=[str(item) for item in payload.get("required_skills") or []],
            allowed_skills=[str(item) for item in payload.get("allowed_skills") or []],
            constraints=dict(payload.get("constraints") or {}),
            priority=str(payload.get("priority") or "normal"),
            risk_level=str(payload.get("risk_level") or "low"),
            operator_id=_optional_str(payload.get("operator_id")),
            mission_id=_optional_str(payload.get("mission_id")),
            robot_id=_optional_str(payload.get("robot_id")),
            command=_optional_str(payload.get("command")),
            memory_lineage=(
                MemoryLineage.from_dict(payload["memory_lineage"])
                if isinstance(payload.get("memory_lineage"), dict)
                else None
            ),
        )


def structured_task_from_mission_subtask(
    *,
    mission_id: str,
    subtask: MissionSubtask,
    operator_id: str | None = None,
    task_id: str | None = None,
    capability_skill_chains: dict[str, list[str]] | None = None,
    memory_lineage: MemoryLineage | None = None,
) -> StructuredRobotTask:
    task_type = (
        subtask.task_type
        or _task_type_from_capability(subtask.capability_required)
    )
    required_skills = skills_from_capability(
        subtask.capability_required,
        capability_skill_chains=capability_skill_chains,
    )
    if (
        isinstance(subtask.target.get("pose"), dict)
        and "navigate_to_point" not in required_skills
    ):
        required_skills.insert(0, "navigate_to_point")
    target = (
        dict(subtask.target)
        if subtask.target
        else (
            {"floor": subtask.floor}
            if subtask.floor is not None
            else {}
        )
    )
    return StructuredRobotTask(
        task_id=(
            task_id
            or subtask.node_id
            or (
                f"{mission_id}:{subtask.robot_id}:"
                f"{subtask.floor}:{subtask.execution_group}"
            )
        ),
        task_type=task_type,
        target=target,
        required_skills=required_skills,
        constraints={
            "execution_group": subtask.execution_group,
            **(
                {"completion_goal": subtask.completion_goal}
                if subtask.completion_goal is not None
                else {}
            ),
            **(
                {"completion_contract": dict(subtask.completion_contract)}
                if subtask.completion_contract
                else {}
            ),
        },
        priority="normal",
        risk_level="low",
        operator_id=operator_id,
        mission_id=mission_id,
        robot_id=subtask.robot_id,
        command=subtask.command,
        memory_lineage=memory_lineage,
    )


def validate_structured_robot_task(task: StructuredRobotTask) -> list[str]:
    errors: list[str] = []
    if not task.task_id:
        errors.append("task_id must not be empty")
    if not task.task_type:
        errors.append("task_type must not be empty")
    if task.task_type == "primitive_composition":
        if not task.allowed_skills:
            errors.append("allowed_skills must not be empty for primitive_composition")
    else:
        if not task.required_skills:
            errors.append("required_skills must not be empty")
    if task.priority not in VALID_PRIORITIES:
        errors.append(f"priority must be one of {sorted(VALID_PRIORITIES)}")
    if task.risk_level not in VALID_RISK_LEVELS:
        errors.append(f"risk_level must be one of {sorted(VALID_RISK_LEVELS)}")
    floor = task.target.get("floor")
    if floor is not None and (not isinstance(floor, int) or floor <= 0):
        errors.append("target.floor must be a positive integer when provided")
    pose = task.target.get("pose")
    if pose is not None:
        if not isinstance(pose, dict):
            errors.append("target.pose must be an object when provided")
        else:
            coordinates = [pose.get(axis) for axis in ("x", "y")]
            if not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and isfinite(float(value))
                for value in coordinates
            ):
                errors.append("target.pose requires finite numeric x and y")
            yaw = pose.get("yaw")
            if (
                yaw is not None
                and (
                    not isinstance(yaw, (int, float))
                    or isinstance(yaw, bool)
                    or not isfinite(float(yaw))
                )
            ):
                errors.append("target.pose yaw must be finite when provided")
            frame_id = task.target.get("frame_id", pose.get("frame_id"))
            if frame_id is not None and (
                not isinstance(frame_id, str)
                or not frame_id.strip()
            ):
                errors.append(
                    "target frame_id must be a non-empty string when provided"
                )
    if task.allowed_skills:
        allowed = set(task.allowed_skills)
        for skill_name in task.required_skills:
            if skill_name not in allowed:
                errors.append(f"required skill {skill_name!r} is not in allowed_skills")
    if task.memory_lineage is not None:
        if task.memory_lineage.runtime_mode not in {"real", "replay", "simulation"}:
            errors.append("memory_lineage.runtime_mode must be real, replay, or simulation")
        if not any((
            task.memory_lineage.command_event_id,
            task.memory_lineage.plan_event_id,
            task.memory_lineage.subtask_event_id,
        )):
            errors.append("memory_lineage must contain at least one event id")
    return errors


def planning_result_from_structured_task(task: StructuredRobotTask) -> PlanningResult:
    errors = validate_structured_robot_task(task)
    if errors:
        return PlanningResult(status="clarify", message="; ".join(errors), intent=task.task_type)

    floor = task.target.get("floor")
    target_floor = floor if isinstance(floor, int) else None
    target_pose = (
        task.target.get("pose")
        if isinstance(task.target.get("pose"), dict)
        else None
    )
    steps: list[PlanStep] = []
    for skill_name in task.required_skills:
        inputs: dict[str, Any] = {}
        if skill_name in FLOOR_SKILLS and target_floor is not None:
            inputs["floor"] = target_floor
        if skill_name in POINT_SKILLS and target_pose is not None:
            inputs.update({
                "x": float(target_pose["x"]),
                "y": float(target_pose["y"]),
                "yaw": float(target_pose.get("yaw", 0.0)),
                "frame_id": str(task.target.get("frame_id") or "map"),
            })
        if task.constraints:
            inputs["constraints"] = dict(task.constraints)
        steps.append(PlanStep(skill_name=skill_name, inputs=inputs))
    return PlanningResult(
        status="planned",
        message="Structured robot task converted to executable plan.",
        intent=task.task_type,
        target_floor=target_floor,
        target_pose=(
            {
                "x": float(target_pose["x"]),
                "y": float(target_pose["y"]),
                "yaw": float(target_pose.get("yaw", 0.0)),
                "frame_id": str(task.target.get("frame_id") or "map"),
            }
            if target_pose is not None
            else None
        ),
        plan=Plan(intent=task.task_type, steps=steps),
    )


def _task_type_from_capability(capability: str) -> str:
    if capability == "search_for_victims":
        return "search"
    return capability


def skills_from_capability(
    capability: str,
    *,
    capability_skill_chains: dict[str, list[str]] | None = None,
) -> list[str]:
    if capability_skill_chains is not None and capability in capability_skill_chains:
        return list(capability_skill_chains[capability])
    return _skills_from_capability(capability)


def _skills_from_capability(capability: str) -> list[str]:
    if capability == "search_for_victims":
        return ["search_for_victims", "report_status"]
    return [capability]


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
