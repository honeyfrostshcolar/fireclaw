from __future__ import annotations

from fireclaw_core.agent.robot_agent import (
    RobotAgentTaskEnvelope,
    RobotLocalPlan,
    RobotLocalPlanStep,
    envelope_from_structured_task,
    planning_result_from_local_plan,
)
from fireclaw_core.task.task_contract import StructuredRobotTask


POINT_TARGET = {
    "frame_id": "map",
    "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
}


def test_envelope_defaults_allowed_tools_to_required_tools() -> None:
    task = StructuredRobotTask(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="导航到 map 坐标 (2.0, 1.5)",
        task_type="navigate",
        target=POINT_TARGET,
        required_skills=["navigate_to_point"],
        constraints={"execution_group": 0},
        risk_level="low",
        operator_id="operator-1",
    )

    envelope = envelope_from_structured_task(
        task,
        fallback_robot_id="robot-fallback",
    )

    assert envelope == RobotAgentTaskEnvelope(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="导航到 map 坐标 (2.0, 1.5)",
        task_type="navigate",
        target=POINT_TARGET,
        allowed_skills=["navigate_to_point"],
        required_skills=["navigate_to_point"],
        constraints={"execution_group": 0},
        risk_level="low",
        operator_id="operator-1",
    )


def test_envelope_uses_fallback_robot_id_when_task_has_none() -> None:
    task = StructuredRobotTask(
        task_id="task-1",
        task_type="navigate",
        target=POINT_TARGET,
        required_skills=["navigate_to_point"],
    )

    envelope = envelope_from_structured_task(
        task,
        fallback_robot_id="gateway-robot",
    )

    assert envelope.robot_id == "gateway-robot"


def test_planning_result_from_local_plan_preserves_step_order_and_point() -> None:
    envelope = RobotAgentTaskEnvelope(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="先观察局部风险，再前往目标点",
        task_type="approach_target",
        target=POINT_TARGET,
        allowed_skills=["inspect_local_hazard", "navigate_to_point"],
        required_skills=["navigate_to_point"],
        constraints={},
        risk_level="low",
        operator_id="operator-1",
    )
    local_plan = RobotLocalPlan(
        intent="approach_target",
        steps=[
            RobotLocalPlanStep(
                "inspect_local_hazard",
                {"radius": 2.0},
                reason="check route",
            ),
            RobotLocalPlanStep(
                "navigate_to_point",
                {"x": 2.0, "y": 1.5, "yaw": 0.0, "frame_id": "map"},
                reason="move",
            ),
        ],
        rationale="Observe before moving.",
        confidence=0.8,
    )

    result = planning_result_from_local_plan(envelope, local_plan)

    assert result.status == "planned"
    assert result.intent == "approach_target"
    assert result.target_floor is None
    assert result.target_pose == {
        "x": 2.0,
        "y": 1.5,
        "yaw": 0.0,
        "frame_id": "map",
    }
    assert [step.skill_name for step in result.plan.steps] == [
        "inspect_local_hazard",
        "navigate_to_point",
    ]
