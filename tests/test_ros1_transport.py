from types import SimpleNamespace

from fireclaw_core.ros.ros1_config import Ros1EndpointConfig, Ros1TransportConfig
from fireclaw_core.ros.ros1_transport import Ros1Transport


class FakePublisher:
    def __init__(self):
        self.published = []

    def publish(self, payload):
        self.published.append(payload)


class FakeServiceProxy:
    def __init__(self):
        self.calls = []

    def __call__(self, payload):
        self.calls.append(payload)
        return {"accepted": True}


class FakeTriggerRequest:
    __slots__ = ("reason",)
    _slot_types = ("string",)

    def __init__(self):
        self.reason = ""


class FakeActionClient:
    def __init__(self):
        self.goals = []
        self.cancelled = False

    def wait_for_server(self, timeout=None):
        self.server_timeout = timeout
        return True

    def send_goal(self, goal, feedback_cb=None):
        self.goals.append(goal)
        if feedback_cb is not None:
            feedback_cb({"progress": 0.5})

    def wait_for_result(self, timeout=None):
        self.result_timeout = timeout
        return True

    def get_result(self):
        return {"done": True}

    def cancel_goal(self):
        self.cancelled = True


class CancellableActionClient(FakeActionClient):
    def __init__(self):
        super().__init__()
        self.wait_count = 0

    def wait_for_result(self, timeout=None):
        self.wait_count += 1
        return False


class FakeRos1Module:
    def __init__(self):
        self.publisher = FakePublisher()
        self.service = FakeServiceProxy()
        self.action_client = FakeActionClient()

    def create_publisher(self, name, type_name):
        self.publisher_args = (name, type_name)
        return self.publisher

    def create_service_proxy(self, name, type_name):
        self.service_args = (name, type_name)
        return self.service

    def resolve_service_request_class(self, type_name):
        assert type_name == "std_srvs/Trigger"
        return FakeTriggerRequest

    def create_action_client(self, name, type_name):
        self.action_args = (name, type_name)
        return self.action_client

    def duration(self, seconds):
        return seconds


def test_ros1_transport_publishes_topic_payload():
    fake = FakeRos1Module()
    transport = Ros1Transport(module=fake)
    endpoint = Ros1EndpointConfig(interface="topic", name="/status", type="std_msgs/String")

    result = transport.execute(endpoint, {"data": "ready"}, Ros1TransportConfig(enabled=True))

    assert result["status"] == "succeeded"
    assert fake.publisher_args == ("/status", "std_msgs/String")
    assert fake.publisher.published == [{"data": "ready"}]


def test_ros1_transport_calls_service_payload():
    fake = FakeRos1Module()
    transport = Ros1Transport(module=fake)
    endpoint = Ros1EndpointConfig(interface="service", name="/stop", type="std_srvs/Trigger")

    result = transport.execute(endpoint, {"reason": "test"}, Ros1TransportConfig(enabled=True))

    assert result["status"] == "succeeded"
    assert result["response"] == {"accepted": True}
    assert fake.service_args == ("/stop", "std_srvs/Trigger")
    request = fake.service.calls[0]
    assert isinstance(request, FakeTriggerRequest)
    assert request.reason == "test"


def test_ros1_transport_builds_service_request_from_dict():
    fake = FakeRos1Module()
    transport = Ros1Transport(module=fake)
    endpoint = Ros1EndpointConfig(interface="service", name="/stop", type="std_srvs/Trigger")

    result = transport.execute(endpoint, {"reason": "operator stop"}, Ros1TransportConfig(enabled=True))

    assert result["status"] == "succeeded"
    request = fake.service.calls[0]
    assert isinstance(request, FakeTriggerRequest)
    assert request.reason == "operator stop"


def test_ros1_transport_sends_action_goal_and_feedback():
    fake = FakeRos1Module()
    feedback = []
    transport = Ros1Transport(module=fake, feedback_sink=feedback.append)
    endpoint = Ros1EndpointConfig(interface="action", name="/move_base", type="move_base_msgs/MoveBaseAction")

    result = transport.execute(
        endpoint,
        {"target_pose": {"pose": {"position": {"x": 1.0}}}},
        Ros1TransportConfig(enabled=True, wait_for_server_seconds=2.0, wait_for_result_seconds=3.0),
    )

    assert result["status"] == "succeeded"
    assert result["response"] == {"done": True}
    assert fake.action_args == ("/move_base", "move_base_msgs/MoveBaseAction")
    assert fake.action_client.server_timeout == 2.0
    assert fake.action_client.result_timeout == 0.05
    assert fake.action_client.goals == [{"target_pose": {"pose": {"position": {"x": 1.0}}}}]
    assert feedback == [{"progress": 0.5}]


def test_ros1_transport_reports_disabled_transport():
    transport = Ros1Transport(module=SimpleNamespace())
    endpoint = Ros1EndpointConfig(interface="topic", name="/status", type="std_msgs/String")

    result = transport.execute(endpoint, {"data": "ready"}, Ros1TransportConfig(enabled=False))

    assert result["status"] == "not_configured"


def test_ros1_transport_cancels_active_action_when_requested():
    fake = FakeRos1Module()
    fake.action_client = CancellableActionClient()
    checks = iter([False, True])
    transport = Ros1Transport(module=fake)
    endpoint = Ros1EndpointConfig(
        interface="action",
        name="/move_base",
        type="move_base_msgs/MoveBaseAction",
        cancel_supported=True,
    )

    result = transport.execute(
        endpoint,
        {"target_pose": {}},
        Ros1TransportConfig(enabled=True, wait_for_server_seconds=0.1, wait_for_result_seconds=5.0),
        cancellation_requested=lambda: next(checks, True),
    )

    assert result["status"] == "cancelled"
    assert fake.action_client.cancelled is True
    assert fake.action_client.wait_count == 1


def test_ros1_transport_reports_action_result_timeout():
    fake = FakeRos1Module()
    fake.action_client = CancellableActionClient()
    transport = Ros1Transport(module=fake)
    endpoint = Ros1EndpointConfig(
        interface="action",
        name="/move_base",
        type="move_base_msgs/MoveBaseAction",
        cancel_supported=True,
    )

    result = transport.execute(
        endpoint,
        {"target_pose": {}},
        Ros1TransportConfig(enabled=True, wait_for_server_seconds=0.1, wait_for_result_seconds=0.01),
    )

    assert result["status"] == "timeout"
    assert "result timeout" in result["error"]


def test_resolve_ros_type_gives_clear_error_on_missing_package():
    import pytest
    from fireclaw_core.ros.ros1_transport import Ros1RuntimeModule

    module = Ros1RuntimeModule(rospy=SimpleNamespace(), actionlib=SimpleNamespace())
    with pytest.raises(RuntimeError, match="not installed"):
        module._resolve_ros_type("nonexistent_pkg/Foo", preferred_module="msg")


def test_resolve_ros_type_gives_clear_error_on_missing_class():
    import pytest
    from unittest.mock import patch
    from fireclaw_core.ros.ros1_transport import Ros1RuntimeModule

    module = Ros1RuntimeModule(rospy=SimpleNamespace(), actionlib=SimpleNamespace())
    fake_module = SimpleNamespace(String=type("String", (), {}))
    with patch("fireclaw_core.ros.ros1_transport.import_module", return_value=fake_module):
        with pytest.raises(RuntimeError, match="not found"):
            module._resolve_ros_type("std_msgs/NonexistentType", preferred_module="msg")


def test_response_to_data_handles_slots():
    from fireclaw_core.ros.ros1_transport import _response_to_data

    class SlottedMsg:
        __slots__ = ("x", "y")
        def __init__(self):
            self.x = 1.0
            self.y = 2.0

    result = _response_to_data(SlottedMsg())
    assert result == {"x": 1.0, "y": 2.0}


def test_response_to_data_handles_nested_slots():
    from fireclaw_core.ros.ros1_transport import _response_to_data

    class Inner:
        __slots__ = ("value",)
        def __init__(self):
            self.value = 42

    class Outer:
        __slots__ = ("inner", "name")
        def __init__(self):
            self.inner = Inner()
            self.name = "test"

    result = _response_to_data(Outer())
    assert result == {"inner": {"value": 42}, "name": "test"}


def test_validate_payload_against_type():
    from fireclaw_core.ros.ros1_transport import validate_payload_against_type

    class Twist:
        __slots__ = ("linear", "angular")

    errors = validate_payload_against_type({"linear": 1.0, "angular": 0.5}, Twist)
    assert errors == []

    errors = validate_payload_against_type({"linear": 1.0, "bogus": 0.5}, Twist)
    assert len(errors) == 1
    assert "bogus" in errors[0]


def test_validate_payload_skips_when_no_slots():
    from fireclaw_core.ros.ros1_transport import validate_payload_against_type

    class NoSlots:
        pass

    errors = validate_payload_against_type({"anything": 1}, NoSlots)
    assert errors == []


# --- Fake message classes for dict-to-message conversion tests ---

class FakeVector3:
    __slots__ = ("x", "y", "z")
    _slot_types = ("float64", "float64", "float64")

    def __init__(self):
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0


class FakeTwist:
    __slots__ = ("linear", "angular")
    _slot_types = ("geometry_msgs/Vector3", "geometry_msgs/Vector3")

    def __init__(self):
        self.linear = FakeVector3()
        self.angular = FakeVector3()


class FakeFibonacciGoal:
    __slots__ = ("order",)
    _slot_types = ("int32",)

    def __init__(self):
        self.order = 0


def test_ros1_transport_builds_topic_message_from_dict():
    """Transport should convert dict payload to ROS message object before publishing."""
    fake = FakeRos1Module()
    # Stub out resolve_message_class so the test can verify conversion
    fake.resolve_message_class = lambda type_name: FakeTwist
    transport = Ros1Transport(module=fake)
    endpoint = Ros1EndpointConfig(interface="topic", name="/cmd_vel", type="geometry_msgs/Twist")

    result = transport.execute(
        endpoint,
        {"linear": {"x": 1.0}, "angular": {"z": 0.5}},
        Ros1TransportConfig(enabled=True),
    )

    assert result["status"] == "succeeded"
    # Publisher should have received a FakeTwist object, not a raw dict
    published = fake.publisher.published
    assert len(published) == 1
    msg = published[0]
    assert isinstance(msg, FakeTwist), f"Expected FakeTwist but got {type(msg).__name__}: {msg}"
    assert msg.linear.x == 1.0
    assert msg.angular.z == 0.5


def test_ros1_transport_builds_action_goal_from_dict():
    """Transport should convert dict payload to ROS action goal object before sending."""
    fake = FakeRos1Module()
    # Stub out resolve_action_goal_class so the test can verify conversion
    fake.resolve_action_goal_class = lambda type_name: FakeFibonacciGoal
    transport = Ros1Transport(module=fake)
    endpoint = Ros1EndpointConfig(interface="action", name="/fibonacci", type="actionlib_tutorials/FibonacciAction")

    result = transport.execute(
        endpoint,
        {"order": 5},
        Ros1TransportConfig(enabled=True, wait_for_server_seconds=2.0, wait_for_result_seconds=3.0),
    )

    assert result["status"] == "succeeded"
    # Action client should have received a FakeFibonacciGoal object, not a raw dict
    goals = fake.action_client.goals
    assert len(goals) == 1
    goal = goals[0]
    assert isinstance(goal, FakeFibonacciGoal), f"Expected FakeFibonacciGoal but got {type(goal).__name__}: {goal}"
    assert goal.order == 5
