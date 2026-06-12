from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

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


@dataclass(frozen=True)
class RobotAgentPolicyDecision:
    """Outcome of a robot-local policy validation.

    ``status`` is one of:
    - ``"allow"`` — plan is safe to execute without further approval.
    - ``"reject"`` — plan violates constraints; ``reasons`` explains why.
    - ``"approval_required"`` — plan is structurally valid but the risk
      level demands operator approval before execution.
    """

    status: Literal["allow", "reject", "approval_required"]
    reasons: list[str]


class RobotAgentPolicy:
    """Validates a robot-local plan against its task envelope.

    Checks (in priority order):
    1. Every planned skill must be in ``envelope.allowed_skills``.
    2. Floor-targeting skills must use the envelope's expected floor.
    3. All ``envelope.required_skills`` must appear in the plan.
    4. High/critical risk levels require operator approval.

    If any of checks 1–3 fail the decision is ``reject``.
    If check 4 is the only remaining concern the decision is
    ``approval_required``.  Otherwise the decision is ``allow``.
    """

    def validate(
        self,
        envelope: RobotAgentTaskEnvelope,
        plan: RobotLocalPlan,
    ) -> RobotAgentPolicyDecision:
        reasons: list[str] = []
        allowed = set(envelope.allowed_skills)
        planned = [step.skill_name for step in plan.steps]

        for skill_name in planned:
            if skill_name not in allowed:
                reasons.append(f"skill {skill_name!r} is outside allowed_skills")

        expected_floor = envelope.target.get("floor")
        if isinstance(expected_floor, int):
            for step in plan.steps:
                if step.skill_name in FLOOR_SKILLS:
                    actual_floor = step.inputs.get("floor")
                    if actual_floor != expected_floor:
                        reasons.append(
                            f"skill {step.skill_name!r} uses floor {actual_floor!r}, expected {expected_floor!r}"
                        )

        for skill_name in envelope.required_skills:
            if skill_name not in planned:
                reasons.append(f"required skill {skill_name!r} is missing")

        if reasons:
            return RobotAgentPolicyDecision(status="reject", reasons=reasons)

        if envelope.risk_level in {"high", "critical"}:
            return RobotAgentPolicyDecision(
                status="approval_required",
                reasons=[f"risk level {envelope.risk_level!r} requires approval before robot-local execution"],
            )

        return RobotAgentPolicyDecision(status="allow", reasons=[])


class RobotAgentPlanner(Protocol):
    def plan(
        self,
        envelope: RobotAgentTaskEnvelope,
        *,
        context: dict[str, Any],
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> RobotLocalPlan:
        ...
