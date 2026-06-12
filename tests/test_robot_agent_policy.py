from __future__ import annotations

from fireclaw_core.robot_agent import (
    RobotAgentPolicy,
    RobotAgentTaskEnvelope,
    RobotLocalPlan,
    RobotLocalPlanStep,
)


def _envelope(risk_level: str = "low") -> RobotAgentTaskEnvelope:
    return RobotAgentTaskEnvelope(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="去2楼搜索",
        task_type="search",
        target={"floor": 2},
        allowed_skills=["navigate_to_floor", "search_for_victims", "report_status"],
        required_skills=["navigate_to_floor", "report_status"],
        constraints={},
        risk_level=risk_level,
        operator_id="operator-1",
    )


def test_policy_allows_plan_inside_envelope():
    plan = RobotLocalPlan(
        intent="search",
        steps=[
            RobotLocalPlanStep("navigate_to_floor", {"floor": 2}),
            RobotLocalPlanStep("report_status", {"floor": 2}),
        ],
    )

    decision = RobotAgentPolicy().validate(_envelope(), plan)

    assert decision.status == "allow"
    assert decision.reasons == []


def test_policy_rejects_skill_outside_allowed_set():
    plan = RobotLocalPlan(
        intent="search",
        steps=[
            RobotLocalPlanStep("navigate_to_floor", {"floor": 2}),
            RobotLocalPlanStep("firefight", {"floor": 2}),
            RobotLocalPlanStep("report_status", {"floor": 2}),
        ],
    )

    decision = RobotAgentPolicy().validate(_envelope(), plan)

    assert decision.status == "reject"
    assert any("firefight" in reason for reason in decision.reasons)


def test_policy_rejects_mutated_floor():
    plan = RobotLocalPlan(
        intent="search",
        steps=[
            RobotLocalPlanStep("navigate_to_floor", {"floor": 3}),
            RobotLocalPlanStep("report_status", {"floor": 2}),
        ],
    )

    decision = RobotAgentPolicy().validate(_envelope(), plan)

    assert decision.status == "reject"
    assert any("floor" in reason for reason in decision.reasons)


def test_policy_rejects_missing_required_skill():
    plan = RobotLocalPlan(
        intent="search",
        steps=[RobotLocalPlanStep("navigate_to_floor", {"floor": 2})],
    )

    decision = RobotAgentPolicy().validate(_envelope(), plan)

    assert decision.status == "reject"
    assert any("report_status" in reason for reason in decision.reasons)


def test_policy_requires_approval_for_high_risk_execution():
    plan = RobotLocalPlan(
        intent="search",
        steps=[
            RobotLocalPlanStep("navigate_to_floor", {"floor": 2}),
            RobotLocalPlanStep("report_status", {"floor": 2}),
        ],
    )

    decision = RobotAgentPolicy().validate(_envelope(risk_level="high"), plan)

    assert decision.status == "approval_required"
    assert any("risk" in reason.lower() for reason in decision.reasons)


def test_policy_requires_approval_for_critical_risk_execution():
    plan = RobotLocalPlan(
        intent="search",
        steps=[
            RobotLocalPlanStep("navigate_to_floor", {"floor": 2}),
            RobotLocalPlanStep("report_status", {"floor": 2}),
        ],
    )

    decision = RobotAgentPolicy().validate(_envelope(risk_level="critical"), plan)

    assert decision.status == "approval_required"
    assert any("risk" in reason.lower() for reason in decision.reasons)


def test_policy_rejects_multiple_simultaneous_violations():
    """Plan with both an unauthorized skill AND a floor mutation accumulates both reasons."""
    plan = RobotLocalPlan(
        intent="search",
        steps=[
            RobotLocalPlanStep("navigate_to_floor", {"floor": 3}),  # wrong floor
            RobotLocalPlanStep("firefight", {"floor": 2}),  # not in allowed_skills
        ],
    )

    decision = RobotAgentPolicy().validate(_envelope(), plan)

    assert decision.status == "reject"
    assert len(decision.reasons) >= 2
    assert any("firefight" in reason for reason in decision.reasons)
    assert any("floor" in reason for reason in decision.reasons)
