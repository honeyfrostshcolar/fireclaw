from __future__ import annotations

from pathlib import Path

from fireclaw_core.agent.agent import FireClawAgent
from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.agent.robot_agent import (
    RobotAgentPolicy,
    RobotAgentTaskEnvelope,
    RobotLocalPlan,
    RobotLocalPlanStep,
)


EXTENSIONS = Path(__file__).resolve().parents[1] / "extensions"
POINT_TARGET = {
    "frame_id": "map",
    "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
}
POINT_INPUTS = {
    "x": 2.0,
    "y": 1.5,
    "yaw": 0.0,
    "frame_id": "map",
}


def _policy() -> RobotAgentPolicy:
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        extension_paths=(EXTENSIONS,),
        plugin_services={"adapter": "dry-run"},
    )
    return RobotAgentPolicy(skill_catalog=agent.registry)


def _envelope(risk_level: str = "low") -> RobotAgentTaskEnvelope:
    return RobotAgentTaskEnvelope(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="导航到 map 坐标 (2.0, 1.5)",
        task_type="navigate",
        target=POINT_TARGET,
        allowed_skills=["navigate_to_point"],
        required_skills=["navigate_to_point"],
        constraints={},
        risk_level=risk_level,
        operator_id="operator-1",
    )


def _matching_plan() -> RobotLocalPlan:
    return RobotLocalPlan(
        intent="navigate",
        steps=[RobotLocalPlanStep("navigate_to_point", POINT_INPUTS)],
    )


def test_policy_allows_plugin_tool_inside_envelope() -> None:
    decision = _policy().validate(_envelope(), _matching_plan())

    assert decision.status == "allow"
    assert decision.reasons == []


def test_policy_rejects_tool_outside_allowed_set() -> None:
    plan = RobotLocalPlan(
        intent="navigate",
        steps=[
            RobotLocalPlanStep("deploy_hose", {"length": 10.0}),
            RobotLocalPlanStep("navigate_to_point", POINT_INPUTS),
        ],
    )

    decision = _policy().validate(_envelope(), plan)

    assert decision.status == "reject"
    assert any("deploy_hose" in reason for reason in decision.reasons)


def test_policy_rejects_mutated_plugin_bound_point_target() -> None:
    plan = RobotLocalPlan(
        intent="navigate",
        steps=[
            RobotLocalPlanStep(
                "navigate_to_point",
                {**POINT_INPUTS, "x": 3.0},
            )
        ],
    )

    decision = _policy().validate(_envelope(), plan)

    assert decision.status == "reject"
    assert any("uses x" in reason for reason in decision.reasons)


def test_policy_rejects_missing_required_tool() -> None:
    plan = RobotLocalPlan(intent="navigate", steps=[])

    decision = _policy().validate(_envelope(), plan)

    assert decision.status == "reject"
    assert any("navigate_to_point" in reason for reason in decision.reasons)


def test_policy_requires_approval_for_high_risk_execution() -> None:
    decision = _policy().validate(
        _envelope(risk_level="high"),
        _matching_plan(),
    )

    assert decision.status == "approval_required"
    assert any("risk" in reason.lower() for reason in decision.reasons)


def test_policy_requires_approval_for_critical_risk_execution() -> None:
    decision = _policy().validate(
        _envelope(risk_level="critical"),
        _matching_plan(),
    )

    assert decision.status == "approval_required"
    assert any("risk" in reason.lower() for reason in decision.reasons)


def test_policy_accumulates_unauthorized_tool_and_target_mutation() -> None:
    plan = RobotLocalPlan(
        intent="navigate",
        steps=[
            RobotLocalPlanStep(
                "navigate_to_point",
                {**POINT_INPUTS, "x": 3.0},
            ),
            RobotLocalPlanStep("deploy_hose", {"length": 10.0}),
        ],
    )

    decision = _policy().validate(_envelope(), plan)

    assert decision.status == "reject"
    assert len(decision.reasons) >= 2
    assert any("deploy_hose" in reason for reason in decision.reasons)
    assert any("uses x" in reason for reason in decision.reasons)


def test_policy_rejects_empty_primitive_composition() -> None:
    envelope = RobotAgentTaskEnvelope(
        task_id="t1",
        mission_id="m1",
        robot_id="r1",
        command="navigate using the delegated primitive",
        task_type="primitive_composition",
        target=POINT_TARGET,
        allowed_skills=["navigate_to_point"],
        required_skills=[],
        constraints={},
        risk_level="low",
        operator_id=None,
    )
    plan = RobotLocalPlan(
        intent="navigate",
        steps=[],
        rationale="",
        confidence=0.5,
    )

    decision = _policy().validate(envelope, plan)

    assert decision.status == "reject"
    assert any("no executable steps" in reason for reason in decision.reasons)
