"""ROS1 smoke tests – verify transport layer against a real roscore.

These tests require:
  - ROS1 Noetic installed at /opt/ros/noetic/
  - roscore, turtlesim, actionlib_tutorials available

Marked with ``ros1_smoke`` so they are skipped by default::

    pytest -m ros1_smoke tests/test_ros1_smoke.py
"""
from __future__ import annotations

import subprocess
import time

import pytest

# ---------------------------------------------------------------------------
# Marker – every test in this module requires a running ROS1 master.
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.ros1_smoke

# ---------------------------------------------------------------------------
# Helper: wait for ROS1 master to be reachable
# ---------------------------------------------------------------------------

def wait_for_ros_master(timeout: float = 10.0) -> None:
    """Block until the ROS1 master responds to getSystemState.

    Raises ``TimeoutError`` if the master is not reachable within *timeout*
    seconds.  Imports ``rospy`` lazily so the module can be collected even
    when ROS1 is not installed.
    """
    import rospy  # type: ignore[import-untyped]

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            code, _, _ = rospy.get_master().getSystemState("fireclaw_smoke")
            if code == 1:
                return
        except Exception:  # noqa: BLE001 – broad is intentional here
            pass
        time.sleep(0.2)
    raise TimeoutError(
        f"ROS1 master not reachable after {timeout}s"
    )


# ---------------------------------------------------------------------------
# Helper: wait for a named ROS node to appear
# ---------------------------------------------------------------------------

def wait_for_node(node_name: str, timeout: float = 10.0) -> None:
    """Block until *node_name* is visible via ``rosnode``.

    Raises ``TimeoutError`` if the node does not appear within *timeout*
    seconds.
    """
    import rosnode  # type: ignore[import-untyped]

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            nodes = rosnode.get_node_names()
            if node_name in nodes:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    raise TimeoutError(
        f"ROS node {node_name!r} not found after {timeout}s"
    )


# ---------------------------------------------------------------------------
# Helper: wait for a ROS action server (topic-based detection)
# ---------------------------------------------------------------------------

def wait_for_action_server(action_name: str, timeout: float = 10.0) -> None:
    """Block until the action server for *action_name* is registered.

    Instead of instantiating a ``SimpleActionClient`` (which requires the
    action type at import time), we poll the master's ``getSystemState`` and
    look for the ``/result`` topic published by the action server.

    Raises ``TimeoutError`` if the server does not appear within *timeout*
    seconds.
    """
    import rospy  # type: ignore[import-untyped]

    result_topic = f"{action_name}/result"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            code, pubs, _ = rospy.get_master().getSystemState(
                "fireclaw_smoke_action"
            )
            if code == 1:
                # *pubs* is a list of [topicName, [publishers, …]]
                for topic_name, _pubs in pubs:
                    if topic_name == result_topic:
                        return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
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


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def ros_master() -> subprocess.Popen:  # type: ignore[type-arg]
    """Start ``roscore`` and yield the ``Popen`` handle.

    The fixture waits for the master to be responsive before yielding.
    Teardown terminates the process gracefully.
    """
    env = {
        "ROS_MASTER_URI": "http://localhost:11311",
        "ROS_DISTRO": "noetic",
        "PYTHONPATH": "/opt/ros/noetic/lib/python3/dist-packages",
        "PATH": "/opt/ros/noetic/bin:/usr/bin:/bin",
    }
    proc = subprocess.Popen(
        ["roscore"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
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
    env = {
        "ROS_MASTER_URI": "http://localhost:11311",
        "ROS_DISTRO": "noetic",
        "PYTHONPATH": "/opt/ros/noetic/lib/python3/dist-packages",
        "PATH": "/opt/ros/noetic/bin:/usr/bin:/bin",
    }
    proc = subprocess.Popen(
        ["rosrun", "turtlesim", "turtlesim_node"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
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
    env = {
        "ROS_MASTER_URI": "http://localhost:11311",
        "ROS_DISTRO": "noetic",
        "PYTHONPATH": "/opt/ros/noetic/lib/python3/dist-packages",
        "PATH": "/opt/ros/noetic/bin:/usr/bin:/bin",
    }
    proc = subprocess.Popen(
        ["rosrun", "actionlib_tutorials", "fibonacci_server.py"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        wait_for_action_server("/fibonacci", timeout=10.0)
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
    """Publish a Twist to /turtle1/cmd_vel and verify transport succeeds."""
    from fireclaw_core.ros1_config import Ros1EndpointConfig, Ros1TransportConfig
    from fireclaw_core.ros1_transport import Ros1Transport

    module = _make_real_ros_module()
    transport = Ros1Transport(module=module)
    endpoint = Ros1EndpointConfig(
        interface="topic",
        name="/turtle1/cmd_vel",
        type="geometry_msgs/Twist",
    )
    config = Ros1TransportConfig(enabled=True)
    payload = {
        "linear": {"x": 2.0, "y": 0.0, "z": 0.0},
        "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
    }

    result = transport.execute(endpoint, payload, config)

    assert result["status"] == "succeeded"


# ---------------------------------------------------------------------------
# Service smoke test – call turtlesim /clear via Ros1Transport
# ---------------------------------------------------------------------------

def test_ros1_service_call_clear(ros_master, turtlesim_node):
    """Call turtlesim /clear service through Ros1Transport."""
    from fireclaw_core.ros1_config import Ros1EndpointConfig, Ros1TransportConfig
    from fireclaw_core.ros1_transport import Ros1Transport

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
    """Send Fibonacci goal via Ros1Transport and verify result."""
    from fireclaw_core.ros1_config import Ros1EndpointConfig, Ros1TransportConfig
    from fireclaw_core.ros1_transport import Ros1Transport

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

    result = transport.execute(endpoint, {"order": 5}, config)

    assert result["status"] == "succeeded"
    response = result["response"]
    assert "sequence" in response
    assert response["sequence"] == [0, 1, 1, 2, 3, 5]
    assert len(feedback_received) > 0, "Should have received at least one feedback"
