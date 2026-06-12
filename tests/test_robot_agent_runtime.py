from __future__ import annotations

from fireclaw_core.robot_agent import (
    DeterministicRobotAgentPlanner,
    RobotAgentPlannerError,
    RobotAgentRuntime,
    RobotAgentTaskEnvelope,
    RobotLocalPlan,
    RobotLocalPlanStep,
)
from fireclaw_core.task_contract import StructuredRobotTask


class FakePlanner:
    def __init__(self, plan=None, error=None):
        self.plan_value = plan
        self.error = error

    def plan(self, envelope, *, context, cancellation_requested=None):
        if self.error is not None:
            raise self.error
        return self.plan_value


def _task(risk_level: str = "low") -> StructuredRobotTask:
    return StructuredRobotTask(
        task_id="task-1",
        task_type="search",
        target={"floor": 2},
        required_skills=["navigate_to_floor", "report_status"],
        risk_level=risk_level,
        robot_id="robot-1",
        mission_id="mission-1",
        command="去2楼搜索",
    )


def test_runtime_accepts_valid_local_plan():
    runtime = RobotAgentRuntime(
        planner=FakePlanner(
            RobotLocalPlan(
                intent="search",
                steps=[
                    RobotLocalPlanStep("report_status", {"floor": 2}),
                    RobotLocalPlanStep("navigate_to_floor", {"floor": 2}),
                ],
            )
        )
    )
    events = []

    result = runtime.plan_structured_task(
        _task(),
        fallback_robot_id="robot-1",
        context={},
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert result.status == "planned"
    assert [step.skill_name for step in result.plan.steps] == ["report_status", "navigate_to_floor"]
    assert any(event_type == "robot_agent.plan_accepted" for event_type, _ in events)


def test_runtime_falls_back_when_planner_fails():
    runtime = RobotAgentRuntime(planner=FakePlanner(error=RobotAgentPlannerError("bad model")))
    events = []

    result = runtime.plan_structured_task(
        _task(),
        fallback_robot_id="robot-1",
        context={},
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert result.status == "planned"
    assert [step.skill_name for step in result.plan.steps] == ["navigate_to_floor", "report_status"]
    assert any(event_type == "robot_agent.plan_failed" for event_type, _ in events)
    assert any(event_type == "robot_agent.fallback_used" for event_type, _ in events)


def test_runtime_falls_back_when_policy_rejects_plan():
    runtime = RobotAgentRuntime(
        planner=FakePlanner(
            RobotLocalPlan(
                intent="search",
                steps=[
                    RobotLocalPlanStep("navigate_to_floor", {"floor": 3}),
                    RobotLocalPlanStep("report_status", {"floor": 3}),
                ],
            )
        )
    )
    events = []

    result = runtime.plan_structured_task(
        _task(),
        fallback_robot_id="robot-1",
        context={},
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert result.status == "planned"
    assert [step.inputs.get("floor") for step in result.plan.steps] == [2, 2]
    assert any(event_type == "robot_agent.policy_rejected" for event_type, _ in events)
    assert any(event_type == "robot_agent.fallback_used" for event_type, _ in events)


def test_runtime_returns_clarify_for_high_risk_without_executing_local_plan():
    runtime = RobotAgentRuntime(
        planner=FakePlanner(
            RobotLocalPlan(
                intent="search",
                steps=[
                    RobotLocalPlanStep("navigate_to_floor", {"floor": 2}),
                    RobotLocalPlanStep("report_status", {"floor": 2}),
                ],
            )
        )
    )
    events = []

    result = runtime.plan_structured_task(
        _task(risk_level="high"),
        fallback_robot_id="robot-1",
        context={},
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert result.status == "clarify"
    assert "approval" in result.message.lower()
    assert any(event_type == "robot_agent.policy_rejected" for event_type, _ in events)


# --- DeterministicRobotAgentPlanner direct test ---


def test_deterministic_planner_builds_floor_aware_plan_from_required_skills():
    envelope = RobotAgentTaskEnvelope(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="去2楼搜索",
        task_type="search",
        target={"floor": 2},
        allowed_skills=["navigate_to_floor", "report_status", "return_to_safe_zone"],
        required_skills=["navigate_to_floor", "report_status"],
        constraints={},
        risk_level="low",
        operator_id="operator-1",
    )

    plan = DeterministicRobotAgentPlanner().plan(envelope, context={})

    assert plan.intent == "search"
    assert plan.confidence == 1.0
    assert [step.skill_name for step in plan.steps] == ["navigate_to_floor", "report_status"]
    assert plan.steps[0].inputs == {"floor": 2}
    assert plan.steps[1].inputs == {"floor": 2}
