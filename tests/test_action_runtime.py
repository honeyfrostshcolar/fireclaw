from fireclaw_core.action_runtime import RobotActionRuntime, RobotAdapterActionBackend
from fireclaw_core.robot import DryRunRobotAdapter, RobotActionResult


def test_robot_action_runtime_emits_lifecycle_events_for_adapter_action():
    events = []
    robot = DryRunRobotAdapter(robot_id="robot-1")
    runtime = RobotActionRuntime(
        backend=RobotAdapterActionBackend(robot),
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
        task_id="task-1",
    )

    result = runtime.run(
        skill_name="navigate_to_floor",
        action_type="navigate_to_floor",
        inputs={"floor": 2},
        dry_run=True,
        risk_level="low",
        timeout_seconds=None,
    )

    assert result.ok is True
    assert result.status == "succeeded"
    assert result.data["action_id"].startswith("action-")
    assert result.data["task_id"] == "task-1"
    assert [event_type for event_type, _payload in events] == [
        "action.requested",
        "action.started",
        "action.succeeded",
    ]
    assert events[0][1]["skill_name"] == "navigate_to_floor"
    assert events[0][1]["action_type"] == "navigate_to_floor"
    assert events[0][1]["inputs"] == {"floor": 2}
    assert events[0][1]["task_id"] == "task-1"
    assert events[2][1]["status"] == "succeeded"


class FeedbackBackend:
    def execute(self, action_type, inputs, feedback_sink=None):
        if feedback_sink is not None:
            feedback_sink({"progress": 0.25, "message": "leaving safe zone"})
            feedback_sink({"progress": 0.75, "message": "near target"})
        return RobotActionResult(
            ok=True,
            status="succeeded",
            robot_id="robot-1",
            mode="feedback-test",
            action=action_type,
            dry_run=True,
            data={"floor": inputs["floor"]},
            timestamp="2026-06-03T00:00:00+00:00",
        )


def test_robot_action_runtime_emits_backend_feedback_events():
    events = []
    runtime = RobotActionRuntime(
        backend=FeedbackBackend(),
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
        task_id="task-1",
    )

    result = runtime.run(
        skill_name="navigate_to_floor",
        action_type="navigate_to_floor",
        inputs={"floor": 2},
        dry_run=True,
        risk_level="low",
        timeout_seconds=None,
    )

    assert result.status == "succeeded"
    assert [event_type for event_type, _payload in events] == [
        "action.requested",
        "action.started",
        "action.feedback",
        "action.feedback",
        "action.succeeded",
    ]
    first_feedback = events[2][1]
    second_feedback = events[3][1]
    assert first_feedback["action_id"].startswith("action-")
    assert first_feedback["task_id"] == "task-1"
    assert first_feedback["skill_name"] == "navigate_to_floor"
    assert first_feedback["action_type"] == "navigate_to_floor"
    assert first_feedback["inputs"] == {"floor": 2}
    assert first_feedback["progress"] == 0.25
    assert second_feedback["progress"] == 0.75
