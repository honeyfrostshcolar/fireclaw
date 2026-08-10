from threading import Event
from time import monotonic, sleep

import pytest

from fireclaw_core.execution.action_runtime import RegisteredActionBackend, RobotActionRuntime
from fireclaw_core.agent.robot import DryRunRobotAdapter, RobotActionResult


def _result(action: str, data: dict) -> RobotActionResult:
    return RobotActionResult(
        ok=True,
        status="succeeded",
        robot_id="robot-1",
        mode="plugin-test",
        action=action,
        dry_run=True,
        data=data,
        timestamp="2026-08-09T00:00:00+00:00",
    )


def test_robot_action_runtime_emits_lifecycle_events_for_adapter_action():
    events = []
    robot = DryRunRobotAdapter(robot_id="robot-1")
    backend = RegisteredActionBackend(robot)
    backend.register_action(
        "navigate_to_waypoint",
        lambda floor: _result("navigate_to_waypoint", {"floor": floor}),
    )
    runtime = RobotActionRuntime(
        backend=backend,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
        task_id="task-1",
    )

    result = runtime.run(
        skill_name="navigate_to_waypoint",
        action_type="navigate_to_waypoint",
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
    assert events[0][1]["skill_name"] == "navigate_to_waypoint"
    assert events[0][1]["action_type"] == "navigate_to_waypoint"
    assert events[0][1]["inputs"] == {"floor": 2}
    assert events[0][1]["task_id"] == "task-1"
    assert events[2][1]["status"] == "succeeded"


def test_robot_action_runtime_executes_single_floor_point_navigation():
    robot = DryRunRobotAdapter(robot_id="robot-1")
    backend = RegisteredActionBackend(robot)
    backend.register_action(
        "navigate_to_point",
        lambda x, y, yaw=0.0, frame_id="map": _result(
            "navigate_to_point",
            {"x": x, "y": y, "yaw": yaw, "frame_id": frame_id},
        ),
    )
    runtime = RobotActionRuntime(
        backend=backend,
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
        skill_name="navigate_to_waypoint",
        action_type="navigate_to_waypoint",
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
    assert first_feedback["skill_name"] == "navigate_to_waypoint"
    assert first_feedback["action_type"] == "navigate_to_waypoint"
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
        skill_name="navigate_to_waypoint",
        action_type="navigate_to_waypoint",
        inputs={"floor": 2},
        dry_run=True,
        risk_level="low",
        timeout_seconds=None,
        cancellation_requested=lambda: False,
    )

    assert backend.cancellation_requested is not None
    assert backend.cancellation_requested() is False


class InternalTypeErrorBackend:
    def __init__(self):
        self.call_count = 0

    def execute(
        self,
        action_type,
        inputs,
        feedback_sink=None,
        cancellation_requested=None,
    ):
        self.call_count += 1
        raise TypeError("handler implementation failed after starting")


def test_robot_action_runtime_does_not_replay_internal_type_error():
    backend = InternalTypeErrorBackend()
    runtime = RobotActionRuntime(backend=backend)

    with pytest.raises(
        TypeError,
        match="handler implementation failed after starting",
    ):
        runtime.run(
            skill_name="physical_action",
            action_type="physical_action",
            inputs={},
            dry_run=True,
            risk_level="low",
            timeout_seconds=None,
        )

    assert backend.call_count == 1


class CancelledBackend:
    def execute(self, action_type, inputs, feedback_sink=None, cancellation_requested=None):
        return RobotActionResult(
            ok=False,
            status="cancelled",
            robot_id="robot-1",
            mode="cancelled-test",
            action=action_type,
            dry_run=False,
            data={
                "cancellation_acknowledged": True,
                "runtime_stopped": True,
            },
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
        skill_name="navigate_to_waypoint",
        action_type="navigate_to_waypoint",
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


def test_robot_action_runtime_cancels_before_backend_start():
    class NeverCalledBackend:
        called = False

        def execute(self, action_type, inputs, **_kwargs):
            self.called = True
            return _result(action_type, inputs)

    backend = NeverCalledBackend()
    events = []
    runtime = RobotActionRuntime(
        backend=backend,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    result = runtime.run(
        skill_name="physical_action",
        action_type="physical_action",
        inputs={},
        dry_run=False,
        risk_level="low",
        timeout_seconds=1.0,
        cancellation_requested=lambda: True,
    )

    assert backend.called is False
    assert result.status == "cancelled"
    assert result.data["cancellation_reason"] == "operator_cancelled"
    assert result.data["cancellation_acknowledged"] is True
    assert result.data["runtime_stopped"] is True
    assert [event_type for event_type, _payload in events] == [
        "action.requested",
        "action.cancel_requested",
        "action.cancelled",
    ]


class CooperativeCancellationBackend:
    def __init__(self, *, report_success_after_cancel: bool = False):
        self.started = Event()
        self.report_success_after_cancel = report_success_after_cancel

    def execute(
        self,
        action_type,
        inputs,
        feedback_sink=None,
        cancellation_requested=None,
    ):
        self.started.set()
        while not cancellation_requested():
            sleep(0.001)
        return RobotActionResult(
            ok=self.report_success_after_cancel,
            status=(
                "succeeded" if self.report_success_after_cancel else "cancelled"
            ),
            robot_id="robot-1",
            mode="cooperative-test",
            action=action_type,
            dry_run=False,
            data={
                "cancellation_acknowledged": True,
                "runtime_stopped": True,
            },
            timestamp="2026-08-09T00:00:00+00:00",
        )


def test_robot_action_runtime_cooperatively_cancels_in_flight_action():
    backend = CooperativeCancellationBackend()
    events = []
    runtime = RobotActionRuntime(
        backend=backend,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    result = runtime.run(
        skill_name="physical_action",
        action_type="physical_action",
        inputs={},
        dry_run=False,
        risk_level="low",
        timeout_seconds=1.0,
        cancellation_ack_timeout_seconds=0.2,
        cancellation_requested=backend.started.is_set,
    )

    assert result.status == "cancelled"
    assert result.data["cancellation_reason"] == "operator_cancelled"
    assert result.data["cancellation_acknowledged"] is True
    assert result.data["runtime_stopped"] is True
    assert [event_type for event_type, _payload in events].count(
        "action.cancelled"
    ) == 1
    assert [
        event_type
        for event_type, _payload in events
        if event_type.startswith("action.")
        and event_type
        in {
            "action.succeeded",
            "action.failed",
            "action.cancelled",
            "action.timed_out",
            "action.lost",
        }
    ] == ["action.cancelled"]


def test_robot_action_runtime_enforces_monotonic_deadline():
    backend = CooperativeCancellationBackend()
    events = []
    runtime = RobotActionRuntime(
        backend=backend,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )
    started = monotonic()

    result = runtime.run(
        skill_name="physical_action",
        action_type="physical_action",
        inputs={},
        dry_run=False,
        risk_level="low",
        timeout_seconds=0.02,
        cancellation_ack_timeout_seconds=0.2,
    )

    assert monotonic() - started < 1.0
    assert result.status == "timed_out"
    assert result.data["cancellation_reason"] == "deadline_exceeded"
    assert result.data["cancellation_acknowledged"] is True
    assert result.data["runtime_stopped"] is True
    assert [event_type for event_type, _payload in events][-2:] == [
        "action.cancel_requested",
        "action.timed_out",
    ]


def test_robot_action_runtime_cancel_wins_completion_race_once_requested():
    backend = CooperativeCancellationBackend(report_success_after_cancel=True)
    events = []
    runtime = RobotActionRuntime(
        backend=backend,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    result = runtime.run(
        skill_name="physical_action",
        action_type="physical_action",
        inputs={},
        dry_run=False,
        risk_level="low",
        timeout_seconds=1.0,
        cancellation_ack_timeout_seconds=0.2,
        cancellation_requested=backend.started.is_set,
    )

    assert result.status == "cancelled"
    assert result.ok is False
    assert result.data["backend_status"] == "succeeded"
    terminal_events = [
        event_type
        for event_type, _payload in events
        if event_type
        in {
            "action.succeeded",
            "action.failed",
            "action.cancelled",
            "action.timed_out",
            "action.lost",
        }
    ]
    assert terminal_events == ["action.cancelled"]


def test_robot_action_runtime_marks_unacknowledged_stop_as_lost():
    class UncooperativeBackend:
        def __init__(self):
            self.started = Event()
            self.release = Event()
            self.finished = Event()

        def execute(self, action_type, inputs, **_kwargs):
            self.started.set()
            self.release.wait(timeout=1.0)
            self.finished.set()
            return _result(action_type, inputs)

    backend = UncooperativeBackend()
    events = []
    runtime = RobotActionRuntime(
        backend=backend,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )
    started = monotonic()

    result = runtime.run(
        skill_name="physical_action",
        action_type="physical_action",
        inputs={},
        dry_run=False,
        risk_level="critical",
        timeout_seconds=0.02,
        cancellation_ack_timeout_seconds=0.02,
    )

    assert monotonic() - started < 1.0
    assert result.status == "lost"
    assert result.data["cancellation_reason"] == "deadline_exceeded"
    assert result.data["cancellation_acknowledged"] is False
    assert result.data["runtime_stopped"] is False
    assert result.data["resource_release_safe"] is False
    assert [event_type for event_type, _payload in events][-2:] == [
        "action.cancel_requested",
        "action.lost",
    ]
    terminal_count = len(events)
    backend.release.set()
    assert backend.finished.wait(timeout=0.2)
    sleep(0.01)
    assert len(events) == terminal_count


def test_backend_cancel_without_explicit_stop_acknowledgement_is_lost():
    class UnacknowledgedCancelledBackend:
        def execute(self, action_type, inputs, **_kwargs):
            return RobotActionResult(
                ok=False,
                status="cancelled",
                robot_id="robot-1",
                mode="unacknowledged-test",
                action=action_type,
                dry_run=False,
                data={},
                timestamp="2026-08-09T00:00:00+00:00",
            )

    result = RobotActionRuntime(
        backend=UnacknowledgedCancelledBackend()
    ).run(
        skill_name="physical_action",
        action_type="physical_action",
        inputs={},
        dry_run=False,
        risk_level="low",
        timeout_seconds=None,
    )

    assert result.status == "lost"
    assert result.data["cancellation_acknowledged"] is False
    assert result.data["runtime_stopped"] is False
    assert result.data["resource_release_safe"] is False


def test_registered_backend_never_reflects_same_named_adapter_method():
    class LegacyLookingRobot(DryRunRobotAdapter):
        called = False

        def navigate_to_point(self, **_kwargs):
            self.called = True
            return _result("navigate_to_point", {})

    robot = LegacyLookingRobot(robot_id="robot-legacy-looking")
    backend = RegisteredActionBackend(robot)

    result = backend.execute(
        "navigate_to_point",
        {"x": 1.0, "y": 2.0, "frame_id": "map"},
    )

    assert result.status == "failed"
    assert "Unsupported robot action type" in (result.error or "")
    assert robot.called is False
