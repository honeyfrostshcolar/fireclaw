from fireclaw_core.execution.action_runtime import RobotActionRuntime, RobotAdapterActionBackend
from fireclaw_core.agent.robot import DryRunRobotAdapter, Ros1RobotAdapter, RobotActionResult
from fireclaw_core.ros.ros1_config import parse_ros1_adapter_config
from fireclaw_core.ros.ros1_transport import Ros1Transport


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


def test_robot_action_runtime_executes_single_floor_point_navigation():
    robot = DryRunRobotAdapter(robot_id="robot-1")
    runtime = RobotActionRuntime(
        backend=RobotAdapterActionBackend(robot),
        task_id="task-point",
    )

    result = runtime.run(
        skill_name="navigate_to_point",
        action_type="navigate_to_point",
        inputs={"x": 2.0, "y": 1.5, "yaw": 0.25, "frame_id": "map"},
        dry_run=True,
        risk_level="low",
        timeout_seconds=None,
    )

    assert result.ok is True
    assert result.action == "navigate_to_point"
    assert result.data["x"] == 2.0
    assert result.data["y"] == 1.5
    assert result.data["frame_id"] == "map"


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


class CancellationAwareBackend:
    def __init__(self):
        self.cancellation_requested = None

    def execute(self, action_type, inputs, feedback_sink=None, cancellation_requested=None):
        self.cancellation_requested = cancellation_requested
        return RobotActionResult(
            ok=True,
            status="succeeded",
            robot_id="robot-1",
            mode="cancel-aware-test",
            action=action_type,
            dry_run=True,
            data={},
            timestamp="2026-06-05T00:00:00+00:00",
        )


def test_robot_action_runtime_passes_cancellation_callback_to_backend():
    backend = CancellationAwareBackend()
    runtime = RobotActionRuntime(backend=backend)

    runtime.run(
        skill_name="navigate_to_floor",
        action_type="navigate_to_floor",
        inputs={"floor": 2},
        dry_run=True,
        risk_level="low",
        timeout_seconds=None,
        cancellation_requested=lambda: False,
    )

    assert backend.cancellation_requested is not None
    assert backend.cancellation_requested() is False


class CancelledBackend:
    def execute(self, action_type, inputs, feedback_sink=None, cancellation_requested=None):
        return RobotActionResult(
            ok=False,
            status="cancelled",
            robot_id="robot-1",
            mode="cancelled-test",
            action=action_type,
            dry_run=False,
            data={},
            timestamp="2026-06-05T00:00:00+00:00",
            error="cancelled by ROS1 action client",
        )


def test_robot_action_runtime_emits_cancelled_events_for_backend_cancelled_result():
    events = []
    runtime = RobotActionRuntime(
        backend=CancelledBackend(),
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
        task_id="task-1",
    )

    result = runtime.run(
        skill_name="navigate_to_floor",
        action_type="navigate_to_floor",
        inputs={"floor": 2},
        dry_run=False,
        risk_level="low",
        timeout_seconds=None,
    )

    assert result.status == "cancelled"
    assert [event_type for event_type, _payload in events] == [
        "action.requested",
        "action.started",
        "action.cancel_requested",
        "action.cancelled",
    ]


class FeedbackActionClient:
    def wait_for_server(self, timeout=None):
        return True

    def send_goal(self, goal, feedback_cb=None):
        if feedback_cb is not None:
            feedback_cb({"progress": 0.5, "message": "halfway"})

    def wait_for_result(self, timeout=None):
        return True

    def get_result(self):
        return {"arrived": True}


class FeedbackRos1Module:
    def __init__(self):
        self.action_client = FeedbackActionClient()

    def create_action_client(self, name, type_name):
        return self.action_client

    def duration(self, seconds):
        return seconds


def test_robot_action_runtime_emits_ros1_action_feedback_events():
    events = []
    config = parse_ros1_adapter_config(
        {
            "robot_id": "robot-ros1",
            "transport": {"enabled": True},
            "remap": {
                "navigate_to_floor": {
                    "profile": "move_base",
                    "name": "/move_base",
                    "goal_template": {"floor": "{{ floor }}"},
                }
            },
        }
    )
    robot = Ros1RobotAdapter(
        config=config,
        transport=Ros1Transport(module=FeedbackRos1Module()),
    )
    runtime = RobotActionRuntime(
        backend=RobotAdapterActionBackend(robot),
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
        task_id="task-1",
    )

    result = runtime.run(
        skill_name="navigate_to_floor",
        action_type="navigate_to_floor",
        inputs={"floor": 2},
        dry_run=False,
        risk_level="low",
        timeout_seconds=None,
    )

    assert result.status == "succeeded"
    feedback_events = [payload for event_type, payload in events if event_type == "action.feedback"]
    assert feedback_events[0]["progress"] == 0.5
    assert feedback_events[0]["message"] == "halfway"
    assert feedback_events[0]["task_id"] == "task-1"
