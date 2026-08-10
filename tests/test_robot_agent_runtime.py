from __future__ import annotations

from pathlib import Path

from fireclaw_core.agent.agent import FireClawAgent
from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.agent.robot_agent import (
    DeterministicRobotAgentPlanner,
    RobotAgentPlannerError,
    RobotAgentRuntime,
    RobotAgentTaskEnvelope,
    RobotLocalPlan,
    RobotLocalPlanStep,
)
from fireclaw_core.task.task_contract import StructuredRobotTask


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


class FakePlanner:
    def __init__(self, plan=None, error=None):
        self.plan_value = plan
        self.error = error

    def plan(self, envelope, *, context, cancellation_requested=None):
        if self.error is not None:
            raise self.error
        return self.plan_value


def _registry():
    return FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        extension_paths=(EXTENSIONS,),
        plugin_services={"adapter": "dry-run"},
    ).registry


def _task(risk_level: str = "low") -> StructuredRobotTask:
    return StructuredRobotTask(
        task_id="task-1",
        task_type="navigate",
        target=POINT_TARGET,
        required_skills=["navigate_to_point"],
        risk_level=risk_level,
        robot_id="robot-1",
        mission_id="mission-1",
        command="导航到 map 坐标 (2.0, 1.5)",
    )


def _runtime(planner) -> RobotAgentRuntime:
    runtime = RobotAgentRuntime(planner=planner)
    runtime.bind_skill_catalog(_registry())
    return runtime


def test_runtime_accepts_valid_local_plan() -> None:
    runtime = _runtime(
        FakePlanner(
            RobotLocalPlan(
                intent="navigate",
                steps=[
                    RobotLocalPlanStep("navigate_to_point", POINT_INPUTS),
                ],
            )
        )
    )
    events = []

    result = runtime.plan_structured_task(
        _task(),
        fallback_robot_id="robot-1",
        context={},
        event_sink=lambda event_type, payload: events.append(
            (event_type, payload)
        ),
    )

    assert result.status == "planned"
    assert [step.skill_name for step in result.plan.steps] == [
        "navigate_to_point"
    ]
    assert any(
        event_type == "robot_agent.plan_accepted"
        for event_type, _ in events
    )


def test_runtime_falls_back_when_planner_fails() -> None:
    runtime = _runtime(
        FakePlanner(error=RobotAgentPlannerError("bad model"))
    )
    events = []

    result = runtime.plan_structured_task(
        _task(),
        fallback_robot_id="robot-1",
        context={},
        event_sink=lambda event_type, payload: events.append(
            (event_type, payload)
        ),
    )

    assert result.status == "planned"
    assert result.plan.steps[0].skill_name == "navigate_to_point"
    assert result.plan.steps[0].inputs == POINT_INPUTS
    assert any(
        event_type == "robot_agent.plan_failed" for event_type, _ in events
    )
    assert any(
        event_type == "robot_agent.fallback_used" for event_type, _ in events
    )


def test_runtime_falls_back_when_policy_rejects_mutated_target() -> None:
    runtime = _runtime(
        FakePlanner(
            RobotLocalPlan(
                intent="navigate",
                steps=[
                    RobotLocalPlanStep(
                        "navigate_to_point",
                        {**POINT_INPUTS, "x": 3.0},
                    )
                ],
            )
        )
    )
    events = []

    result = runtime.plan_structured_task(
        _task(),
        fallback_robot_id="robot-1",
        context={},
        event_sink=lambda event_type, payload: events.append(
            (event_type, payload)
        ),
    )

    assert result.status == "planned"
    assert result.plan.steps[0].inputs == POINT_INPUTS
    assert any(
        event_type == "robot_agent.policy_rejected"
        for event_type, _ in events
    )
    assert any(
        event_type == "robot_agent.fallback_used" for event_type, _ in events
    )


def test_runtime_returns_clarify_for_high_risk_plan() -> None:
    runtime = _runtime(
        FakePlanner(
            RobotLocalPlan(
                intent="navigate",
                steps=[
                    RobotLocalPlanStep("navigate_to_point", POINT_INPUTS),
                ],
            )
        )
    )
    events = []

    result = runtime.plan_structured_task(
        _task(risk_level="high"),
        fallback_robot_id="robot-1",
        context={},
        event_sink=lambda event_type, payload: events.append(
            (event_type, payload)
        ),
    )

    assert result.status == "clarify"
    assert "approval" in result.message.lower()
    assert any(
        event_type == "robot_agent.policy_rejected"
        for event_type, _ in events
    )


def test_deterministic_planner_projects_inputs_from_bound_plugin_catalog() -> None:
    envelope = RobotAgentTaskEnvelope(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="导航到 map 坐标 (2.0, 1.5)",
        task_type="navigate",
        target=POINT_TARGET,
        allowed_skills=["navigate_to_point"],
        required_skills=["navigate_to_point"],
        constraints={},
        risk_level="low",
        operator_id="operator-1",
    )

    plan = DeterministicRobotAgentPlanner(
        skill_catalog=_registry(),
    ).plan(envelope, context={})

    assert plan.intent == "navigate"
    assert plan.confidence == 1.0
    assert [step.skill_name for step in plan.steps] == [
        "navigate_to_point"
    ]
    assert plan.steps[0].inputs == POINT_INPUTS
