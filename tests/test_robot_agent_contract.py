from __future__ import annotations

from fireclaw_core.agent.robot_agent import (
    RobotAgentTaskEnvelope,
    RobotLocalPlan,
    RobotLocalPlanStep,
    envelope_from_structured_task,
    planning_result_from_local_plan,
)
from fireclaw_core.task.task_contract import StructuredRobotTask


def test_envelope_from_structured_task_adds_safe_allowed_skills():
    task = StructuredRobotTask(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="去2楼搜索受困人员",
        task_type="search",
        target={"floor": 2},
        required_skills=["navigate_to_floor", "search_for_victims"],
        constraints={"execution_group": 0},
        risk_level="low",
        operator_id="operator-1",
    )

    envelope = envelope_from_structured_task(task, fallback_robot_id="robot-fallback")

    assert envelope == RobotAgentTaskEnvelope(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="去2楼搜索受困人员",
        task_type="search",
        target={"floor": 2},
        allowed_skills=[
            "navigate_to_floor",
            "search_for_victims",
            "report_status",
            "return_to_safe_zone",
        ],
        required_skills=["navigate_to_floor", "search_for_victims"],
        constraints={"execution_group": 0},
        risk_level="low",
        operator_id="operator-1",
    )


def test_envelope_uses_fallback_robot_id_when_task_has_none():
    task = StructuredRobotTask(
        task_id="task-1",
        task_type="search",
        target={"floor": 2},
        required_skills=["navigate_to_floor"],
    )

    envelope = envelope_from_structured_task(task, fallback_robot_id="gateway-robot")

    assert envelope.robot_id == "gateway-robot"


def test_planning_result_from_local_plan_preserves_step_order_and_floor():
    envelope = RobotAgentTaskEnvelope(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="去2楼搜索",
        task_type="search",
        target={"floor": 2},
        allowed_skills=["report_status", "navigate_to_floor"],
        required_skills=["navigate_to_floor"],
        constraints={},
        risk_level="low",
        operator_id="operator-1",
    )
    local_plan = RobotLocalPlan(
        intent="search",
        steps=[
            RobotLocalPlanStep("report_status", {"floor": 2}, reason="announce start"),
            RobotLocalPlanStep("navigate_to_floor", {"floor": 2}, reason="move"),
        ],
        rationale="Robot reports before moving.",
        confidence=0.8,
    )

    result = planning_result_from_local_plan(envelope, local_plan)

    assert result.status == "planned"
    assert result.intent == "search"
    assert result.target_floor == 2
    assert [step.skill_name for step in result.plan.steps] == [
        "report_status",
        "navigate_to_floor",
    ]
