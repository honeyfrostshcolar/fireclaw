from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from fireclaw_core.planner import Plan, PlanningResult, PlanStep
from fireclaw_core.task_contract import StructuredRobotTask

SAFE_SUPPLEMENTAL_SKILLS = ("report_status", "return_to_safe_zone")
# Used by RobotAgentPolicy (floor-mutation guard) and DeterministicRobotAgentPlanner.
FLOOR_SKILLS = {"navigate_to_floor", "search_for_victims", "assess_victim", "report_status"}


@dataclass(frozen=True)
class RobotAgentTaskEnvelope:
    task_id: str
    mission_id: str | None
    robot_id: str
    command: str | None
    task_type: str
    target: dict[str, Any]
    allowed_skills: list[str]
    required_skills: list[str]
    constraints: dict[str, Any]
    risk_level: str
    operator_id: str | None


@dataclass(frozen=True)
class RobotLocalPlanStep:
    skill_name: str
    inputs: dict[str, Any]
    reason: str | None = None


@dataclass(frozen=True)
class RobotLocalPlan:
    intent: str
    steps: list[RobotLocalPlanStep]
    rationale: str | None = None
    confidence: float | None = None


def envelope_from_structured_task(
    task: StructuredRobotTask,
    *,
    fallback_robot_id: str,
) -> RobotAgentTaskEnvelope:
    allowed_skills = list(dict.fromkeys([*task.required_skills, *SAFE_SUPPLEMENTAL_SKILLS]))
    return RobotAgentTaskEnvelope(
        task_id=task.task_id,
        mission_id=task.mission_id,
        robot_id=task.robot_id or fallback_robot_id,
        command=task.command,
        task_type=task.task_type,
        target=dict(task.target),
        allowed_skills=allowed_skills,
        required_skills=list(task.required_skills),
        constraints=dict(task.constraints),
        risk_level=task.risk_level,
        operator_id=task.operator_id,
    )


def planning_result_from_local_plan(
    envelope: RobotAgentTaskEnvelope,
    local_plan: RobotLocalPlan,
) -> PlanningResult:
    floor = envelope.target.get("floor")
    target_floor = floor if isinstance(floor, int) else None
    steps = [
        PlanStep(skill_name=step.skill_name, inputs=dict(step.inputs))
        for step in local_plan.steps
    ]
    return PlanningResult(
        status="planned",
        message="Robot-local agent produced an executable plan.",
        intent=local_plan.intent or envelope.task_type,
        target_floor=target_floor,
        plan=Plan(intent=local_plan.intent or envelope.task_type, steps=steps),
    )


class RobotAgentPlanner(Protocol):
    def plan(
        self,
        envelope: RobotAgentTaskEnvelope,
        *,
        context: dict[str, Any],
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> RobotLocalPlan:
        ...
