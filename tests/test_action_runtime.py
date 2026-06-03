from fireclaw_core.action_runtime import RobotActionRuntime, RobotAdapterActionBackend
from fireclaw_core.robot import DryRunRobotAdapter


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
