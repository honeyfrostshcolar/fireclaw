"""ROS1 smoke tests – verify transport layer against a real roscore.

These tests require:
  - ROS1 Noetic installed at /opt/ros/noetic/
  - roscore, turtlesim, actionlib_tutorials available

Marked with ``ros1_smoke`` so they are skipped by default::

    pytest -m ros1_smoke tests/test_ros1_smoke.py
"""
from __future__ import annotations

import os
import subprocess
import time

import pytest

# ---------------------------------------------------------------------------
# Marker – every test in this module requires a running ROS1 master.
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.ros1_smoke


# ---------------------------------------------------------------------------
# Helper: build env dict inheriting current process with ROS overrides
# ---------------------------------------------------------------------------

def _make_ros_env() -> dict[str, str]:
    """Return an env dict inheriting the current process with ROS overrides."""
    env = dict(os.environ)
    env["ROS_MASTER_URI"] = "http://localhost:11311"
    env["ROS_DISTRO"] = "noetic"
    return env


# ---------------------------------------------------------------------------
# Helper: wait for ROS1 master to be reachable
# ---------------------------------------------------------------------------

def wait_for_ros_master(timeout: float = 10.0) -> None:
    """Block until the ROS1 master responds to getSystemState.

    Uses raw XML-RPC to probe the master, avoiding the need to import
    ``rospy`` (which has a heavy dependency chain) in the test process.
    """
    import xmlrpc.client

    master_uri = "http://localhost:11311"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            proxy = xmlrpc.client.ServerProxy(master_uri)
            code, _msg, _state = proxy.getSystemState("fireclaw_smoke")
            if code == 1:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    raise TimeoutError(
        f"ROS1 master not reachable after {timeout}s"
    )


# ---------------------------------------------------------------------------
# Helper: wait for a named ROS node to appear
# ---------------------------------------------------------------------------

def wait_for_node(node_name: str, timeout: float = 10.0) -> None:
    """Block until *node_name* is visible via the ``rosnode`` CLI."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["rosnode", "list"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0 and node_name in result.stdout:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    raise TimeoutError(
        f"ROS node {node_name!r} not found after {timeout}s"
    )


# ---------------------------------------------------------------------------
# Helper: wait for a ROS action server (topic-based detection)
# ---------------------------------------------------------------------------

def wait_for_action_server(action_name: str, timeout: float = 10.0) -> None:
    """Block until the action server for *action_name* is registered.

    Uses raw XML-RPC to poll the master's ``getSystemState`` and look for
    the ``/result`` topic published by the action server.
    """
    import xmlrpc.client

    master_uri = "http://localhost:11311"
    result_topic = f"{action_name}/result"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            proxy = xmlrpc.client.ServerProxy(master_uri)
            code, _msg, state = proxy.getSystemState("fireclaw_smoke_action")
            if code == 1:
                # state[0] = [[topicName, [publishers]], ...]
                for topic_name, _pubs in state[0]:
                    if topic_name == result_topic:
                        return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    raise TimeoutError(
        f"Action server for {action_name!r} not found after {timeout}s"
    )


# ---------------------------------------------------------------------------
# Helper: create a Ros1RuntimeModule backed by real rospy/actionlib
# ---------------------------------------------------------------------------

def _make_real_ros_module():
    """Create a Ros1RuntimeModule backed by real rospy/actionlib."""
    from fireclaw_core.ros1_transport import Ros1RuntimeModule
    return Ros1RuntimeModule.load()


def _ensure_rospy_node(name: str = "fireclaw_smoke_test") -> None:
    """Initialize rospy once for tests that use ROS Python clients."""
    import rospy

    if not rospy.core.is_initialized():
        rospy.init_node(name, anonymous=True)


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def ros_master() -> subprocess.Popen:  # type: ignore[type-arg]
    """Start ``roscore`` and yield the ``Popen`` handle."""
    proc = subprocess.Popen(
        ["roscore"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_make_ros_env(),
    )
    try:
        wait_for_ros_master(timeout=15.0)
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture(scope="session")
def turtlesim_node(
    ros_master: subprocess.Popen,  # noqa: ARG001 – dependency trigger
) -> subprocess.Popen:  # type: ignore[type-arg]
    """Start ``turtlesim_node`` and yield the ``Popen`` handle."""
    proc = subprocess.Popen(
        ["rosrun", "turtlesim", "turtlesim_node"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_make_ros_env(),
    )
    try:
        wait_for_node("/turtlesim", timeout=10.0)
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture(scope="session")
def fibonacci_server(
    ros_master: subprocess.Popen,  # noqa: ARG001 – dependency trigger
) -> subprocess.Popen:  # type: ignore[type-arg]
    """Start ``fibonacci_server`` and yield the ``Popen`` handle."""
    proc = subprocess.Popen(
        ["/opt/ros/noetic/lib/actionlib_tutorials/fibonacci_server.py"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_make_ros_env(),
    )
    try:
        wait_for_action_server("/fibonacci", timeout=30.0)
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# Placeholder test – infrastructure health check
# ---------------------------------------------------------------------------

def test_ros1_smoke_infrastructure_starts(
    ros_master: subprocess.Popen,
    turtlesim_node: subprocess.Popen,
    fibonacci_server: subprocess.Popen,
) -> None:
    """All three ROS1 processes should still be running."""
    assert ros_master.poll() is None, "roscore exited unexpectedly"
    assert turtlesim_node.poll() is None, "turtlesim_node exited unexpectedly"
    assert fibonacci_server.poll() is None, "fibonacci_server exited unexpectedly"


# ---------------------------------------------------------------------------
# Topic smoke test – publish Twist to turtlesim via Ros1Transport
# ---------------------------------------------------------------------------

def test_ros1_topic_publish_to_turtlesim(ros_master, turtlesim_node):
    """Publish a Twist to /turtle1/cmd_vel via transport.execute() with a dict payload."""
    from fireclaw_core.ros1_config import Ros1EndpointConfig, Ros1TransportConfig
    from fireclaw_core.ros1_transport import Ros1Transport

    _ensure_rospy_node("fireclaw_smoke_topic")
    module = _make_real_ros_module()
    transport = Ros1Transport(module=module)
    endpoint = Ros1EndpointConfig(
        interface="topic",
        name="/turtle1/cmd_vel",
        type="geometry_msgs/Twist",
    )
    config = Ros1TransportConfig(enabled=True)

    result = transport.execute(
        endpoint,
        {"linear": {"x": 2.0, "y": 0.0, "z": 0.0}, "angular": {"x": 0.0, "y": 0.0, "z": 0.0}},
        config,
    )
    assert result["status"] == "succeeded"


# ---------------------------------------------------------------------------
# Service smoke test – call turtlesim /clear via Ros1Transport
# ---------------------------------------------------------------------------

def test_ros1_service_call_clear(ros_master, turtlesim_node):
    """Call turtlesim /clear service through Ros1Transport."""
    from fireclaw_core.ros1_config import Ros1EndpointConfig, Ros1TransportConfig
    from fireclaw_core.ros1_transport import Ros1Transport

    _ensure_rospy_node("fireclaw_smoke_service")
    module = _make_real_ros_module()
    transport = Ros1Transport(module=module)
    endpoint = Ros1EndpointConfig(
        interface="service",
        name="/clear",
        type="std_srvs/Empty",
    )
    config = Ros1TransportConfig(enabled=True)

    result = transport.execute(endpoint, {}, config)

    assert result["status"] == "succeeded"


# ---------------------------------------------------------------------------
# Action smoke test – send Fibonacci goal via Ros1Transport
# ---------------------------------------------------------------------------

def test_ros1_action_fibonacci_goal(ros_master, fibonacci_server):
    """Send Fibonacci goal via transport.execute() with a dict payload."""
    from fireclaw_core.ros1_config import Ros1EndpointConfig, Ros1TransportConfig
    from fireclaw_core.ros1_transport import Ros1Transport

    _ensure_rospy_node("fireclaw_smoke_action")
    module = _make_real_ros_module()
    feedback_received: list[dict] = []
    transport = Ros1Transport(module=module, feedback_sink=feedback_received.append)
    endpoint = Ros1EndpointConfig(
        interface="action",
        name="/fibonacci",
        type="actionlib_tutorials/FibonacciAction",
        cancel_supported=True,
        feedback_supported=True,
    )
    config = Ros1TransportConfig(
        enabled=True,
        wait_for_server_seconds=5.0,
        wait_for_result_seconds=10.0,
    )

    result = transport.execute(
        endpoint,
        {"order": 5},
        config,
    )
    assert result["status"] == "succeeded"
    result_data = result["response"]
    assert result_data is not None
    assert list(result_data["sequence"]) == [0, 1, 1, 2, 3, 5]
    assert len(feedback_received) > 0, "Should have received at least one feedback"


# ---------------------------------------------------------------------------
# Action cancel smoke test – cancel Fibonacci goal after first feedback
# ---------------------------------------------------------------------------

def test_ros1_action_cancel(ros_master, fibonacci_server):
    """Send Fibonacci goal and cancel it after first feedback."""
    from fireclaw_core.ros1_config import Ros1EndpointConfig, Ros1TransportConfig
    from fireclaw_core.ros1_transport import Ros1Transport

    _ensure_rospy_node("fireclaw_smoke_cancel")
    module = _make_real_ros_module()
    cancel_count = 0

    def on_feedback(feedback: dict) -> None:
        nonlocal cancel_count
        cancel_count += 1

    transport = Ros1Transport(module=module, feedback_sink=on_feedback)
    endpoint = Ros1EndpointConfig(
        interface="action",
        name="/fibonacci",
        type="actionlib_tutorials/FibonacciAction",
        cancel_supported=True,
        feedback_supported=True,
    )
    config = Ros1TransportConfig(
        enabled=True,
        wait_for_server_seconds=5.0,
        wait_for_result_seconds=10.0,
    )

    import actionlib_tutorials.msg

    goal = actionlib_tutorials.msg.FibonacciGoal(order=100)
    client = module.create_action_client("/fibonacci", "actionlib_tutorials/FibonacciAction")
    client.wait_for_server(timeout=module.duration(5.0))
    client.send_goal(goal, feedback_cb=transport._handle_feedback)

    # Wait for first feedback, then cancel
    deadline = time.monotonic() + 10.0
    while cancel_count < 1 and time.monotonic() < deadline:
        time.sleep(0.05)
    client.cancel_goal()

    assert cancel_count >= 1, "Should have received at least one feedback before cancel"


# ---------------------------------------------------------------------------
# Action timeout smoke test – verify timeout on short wait
# ---------------------------------------------------------------------------

def test_ros1_action_timeout(ros_master, fibonacci_server):
    """Verify timeout produces correct status when result takes too long."""
    from fireclaw_core.ros1_config import Ros1EndpointConfig, Ros1TransportConfig
    from fireclaw_core.ros1_transport import Ros1Transport

    _ensure_rospy_node("fireclaw_smoke_timeout")
    module = _make_real_ros_module()
    transport = Ros1Transport(module=module)
    endpoint = Ros1EndpointConfig(
        interface="action",
        name="/fibonacci",
        type="actionlib_tutorials/FibonacciAction",
        cancel_supported=True,
    )
    config = Ros1TransportConfig(
        enabled=True,
        wait_for_server_seconds=5.0,
        wait_for_result_seconds=0.01,  # Too short for computation
    )

    import actionlib_tutorials.msg

    goal = actionlib_tutorials.msg.FibonacciGoal(order=100)
    result = transport.execute(endpoint, goal, config)

    assert result["status"] == "timeout"
