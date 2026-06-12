# src/fireclaw_core/task_contract.py
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from fireclaw_core.mission.mission_planner import MissionSubtask
from fireclaw_core.planner.planner import Plan, PlanningResult, PlanStep

VALID_PRIORITIES = {"low", "normal", "high", "emergency"}
VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}
FLOOR_SKILLS = {"navigate_to_floor", "search_for_victims", "assess_victim", "report_status"}


@dataclass(frozen=True)
class StructuredRobotTask:
    task_id: str
    task_type: str
    target: dict[str, Any]
    required_skills: list[str]
    constraints: dict[str, Any] = field(default_factory=dict)
    priority: str = "normal"
    risk_level: str = "low"
    operator_id: str | None = None
    mission_id: str | None = None
    robot_id: str | None = None
    command: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StructuredRobotTask":
        return cls(
            task_id=str(payload.get("task_id") or ""),
            task_type=str(payload.get("task_type") or ""),
            target=dict(payload.get("target") or {}),
            required_skills=[str(item) for item in payload.get("required_skills") or []],
            constraints=dict(payload.get("constraints") or {}),
            priority=str(payload.get("priority") or "normal"),
            risk_level=str(payload.get("risk_level") or "low"),
            operator_id=_optional_str(payload.get("operator_id")),
            mission_id=_optional_str(payload.get("mission_id")),
            robot_id=_optional_str(payload.get("robot_id")),
            command=_optional_str(payload.get("command")),
        )


def structured_task_from_mission_subtask(
    *,
    mission_id: str,
    subtask: MissionSubtask,
    operator_id: str | None = None,
    task_id: str | None = None,
    capability_skill_chains: dict[str, list[str]] | None = None,
) -> StructuredRobotTask:
    task_type = _task_type_from_capability(subtask.capability_required)
    required_skills = skills_from_capability(
        subtask.capability_required,
        capability_skill_chains=capability_skill_chains,
    )
    return StructuredRobotTask(
        task_id=task_id or f"{mission_id}:{subtask.robot_id}:{subtask.floor}:{subtask.execution_group}",
        task_type=task_type,
        target={"floor": subtask.floor},
        required_skills=required_skills,
        constraints={"execution_group": subtask.execution_group},
        priority="normal",
        risk_level="low",
        operator_id=operator_id,
        mission_id=mission_id,
        robot_id=subtask.robot_id,
        command=subtask.command,
    )


def validate_structured_robot_task(task: StructuredRobotTask) -> list[str]:
    errors: list[str] = []
    if not task.task_id:
        errors.append("task_id must not be empty")
    if not task.task_type:
        errors.append("task_type must not be empty")
    if not task.required_skills:
        errors.append("required_skills must not be empty")
    if task.priority not in VALID_PRIORITIES:
        errors.append(f"priority must be one of {sorted(VALID_PRIORITIES)}")
    if task.risk_level not in VALID_RISK_LEVELS:
        errors.append(f"risk_level must be one of {sorted(VALID_RISK_LEVELS)}")
    floor = task.target.get("floor")
    if floor is not None and (not isinstance(floor, int) or floor <= 0):
        errors.append("target.floor must be a positive integer when provided")
    return errors


def planning_result_from_structured_task(task: StructuredRobotTask) -> PlanningResult:
    errors = validate_structured_robot_task(task)
    if errors:
        return PlanningResult(status="clarify", message="; ".join(errors), intent=task.task_type)

    floor = task.target.get("floor")
    target_floor = floor if isinstance(floor, int) else None
    steps: list[PlanStep] = []
    for skill_name in task.required_skills:
        inputs: dict[str, Any] = {}
        if skill_name in FLOOR_SKILLS and target_floor is not None:
            inputs["floor"] = target_floor
        if task.constraints:
            inputs["constraints"] = dict(task.constraints)
        steps.append(PlanStep(skill_name=skill_name, inputs=inputs))
    return PlanningResult(
        status="planned",
        message="Structured robot task converted to executable plan.",
        intent=task.task_type,
        target_floor=target_floor,
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
        return ["navigate_to_floor", "search_for_victims", "report_status"]
    return [capability]


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
