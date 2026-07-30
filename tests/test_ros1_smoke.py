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
import uuid
from datetime import datetime, timezone

import pytest

# ---------------------------------------------------------------------------
# Marker – every test in this module requires a running ROS1 master.
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.ros1_smoke


# ---------------------------------------------------------------------------
# Artifact collector – records check names for JSONL output
# ---------------------------------------------------------------------------

class _ArtifactCollector:
    """Collects smoke check names and writes a JSONL artifact on teardown."""

    def __init__(self) -> None:
        self.checks: list[str] = []
        self.started_at: str = datetime.now(timezone.utc).isoformat()
        self._passed: bool = True
        self._error_summary: str | None = None

    def record(self, name: str, passed: bool, error: str | None = None) -> None:
        self.checks.append(name)
        if not passed:
            self._passed = False
            if error and self._error_summary is None:
                self._error_summary = error


@pytest.fixture(scope="session")
def smoke_artifact_collector():
    """Yield an artifact collector; write JSONL at teardown if env var set."""
    artifact_path = os.environ.get("FIRECLAW_ROS1_SMOKE_ARTIFACTS")
    collector = _ArtifactCollector()
    yield collector
    if artifact_path is not None:
        from fireclaw_core.ros.ros1_smoke_artifacts import (
            JsonlRos1SmokeArtifactStore,
            Ros1SmokeArtifact,
        )

        finished_at = datetime.now(timezone.utc).isoformat()
        robot_id = os.environ.get("FIRECLAW_ROBOT_ID", "unknown")
        environment = os.environ.get("FIRECLAW_ROS1_ENV", "sim")
        artifact = Ros1SmokeArtifact(
            run_id=f"smoke-{uuid.uuid4().hex[:8]}",
            robot_id=robot_id,
            environment=environment,
            command="pytest tests/test_ros1_smoke.py",
            checks=tuple(collector.checks),
            passed=collector._passed,
            started_at=collector.started_at,
            finished_at=finished_at,
            error_summary=collector._error_summary,
        )
        store = JsonlRos1SmokeArtifactStore(artifact_path)
        store.append(artifact)


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
    from fireclaw_core.ros.ros1_transport import Ros1RuntimeModule
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
    smoke_artifact_collector,
) -> None:
    """All three ROS1 processes should still be running."""
    passed = (
        ros_master.poll() is None
        and turtlesim_node.poll() is None
        and fibonacci_server.poll() is None
    )
    smoke_artifact_collector.record("infrastructure", passed)
    assert ros_master.poll() is None, "roscore exited unexpectedly"
    assert turtlesim_node.poll() is None, "turtlesim_node exited unexpectedly"
    assert fibonacci_server.poll() is None, "fibonacci_server exited unexpectedly"


# ---------------------------------------------------------------------------
# Topic smoke test – publish Twist to turtlesim via Ros1Transport
# ---------------------------------------------------------------------------

def test_ros1_diagnostic_tools_read_turtlesim(
    ros_master,
    turtlesim_node,
    smoke_artifact_collector,
    monkeypatch,
):
    """Read a live topic through the bounded diagnostic backend."""
    from fireclaw_core.ros.ros1_config import Ros1DiagnosticsConfig
    from fireclaw_core.ros.ros1_diagnostics import Ros1DiagnosticsBackend

    monkeypatch.setenv("ROS_MASTER_URI", "http://localhost:11311")
    backend = Ros1DiagnosticsBackend.from_config(
        Ros1DiagnosticsConfig(
            topic_allowlist=("/turtle1/pose",),
            frame_allowlist=("world",),
            action_allowlist=("/move_base",),
            max_topics=10,
            max_samples=2,
            max_timeout_seconds=2.0,
            max_output_bytes=8_192,
        ),
        robot_id="ros1-smoke-turtle",
    )
    assert backend is not None

    topics = backend.list_topics(limit=10)
    sample = backend.sample_topic(
        "/turtle1/pose",
        sample_count=1,
        timeout_seconds=2.0,
    )
    rate = backend.topic_rate(
        "/turtle1/pose",
        window_seconds=2.0,
    )

    passed = (
        topics["status"] == "ok"
        and any(
            item["topic"] == "/turtle1/pose"
            for item in topics["topics"]
        )
        and sample["status"] == "ok"
        and isinstance(sample["samples"][0].get("x"), float)
        and rate["status"] == "ok"
        and rate["average_hz"] > 0
    )
    smoke_artifact_collector.record("diagnostic_tools", passed)
    assert passed


def test_ros1_topic_publish_to_turtlesim(ros_master, turtlesim_node, smoke_artifact_collector):
    """Publish a Twist to /turtle1/cmd_vel via transport.execute() with a dict payload."""
    from fireclaw_core.ros.ros1_config import Ros1EndpointConfig, Ros1TransportConfig
    from fireclaw_core.ros.ros1_transport import Ros1Transport

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
    smoke_artifact_collector.record("topic_publish", result["status"] == "succeeded")
    assert result["status"] == "succeeded"


# ---------------------------------------------------------------------------
# Service smoke test – call turtlesim /clear via Ros1Transport
# ---------------------------------------------------------------------------

def test_ros1_service_call_clear(ros_master, turtlesim_node, smoke_artifact_collector):
    """Call turtlesim /clear service through Ros1Transport."""
    from fireclaw_core.ros.ros1_config import Ros1EndpointConfig, Ros1TransportConfig
    from fireclaw_core.ros.ros1_transport import Ros1Transport

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

    smoke_artifact_collector.record("service_call", result["status"] == "succeeded")
    assert result["status"] == "succeeded"


# ---------------------------------------------------------------------------
# Action smoke test – send Fibonacci goal via Ros1Transport
# ---------------------------------------------------------------------------

def test_ros1_action_fibonacci_goal(ros_master, fibonacci_server, smoke_artifact_collector):
    """Send Fibonacci goal via transport.execute() with a dict payload."""
    from fireclaw_core.ros.ros1_config import Ros1EndpointConfig, Ros1TransportConfig
    from fireclaw_core.ros.ros1_transport import Ros1Transport

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
    goal_passed = (
        result["status"] == "succeeded"
        and result.get("response") is not None
        and list(result["response"]["sequence"]) == [0, 1, 1, 2, 3, 5]
        and len(feedback_received) > 0
    )
    smoke_artifact_collector.record("action_goal", goal_passed)
    assert result["status"] == "succeeded"
    result_data = result["response"]
    assert result_data is not None
    assert list(result_data["sequence"]) == [0, 1, 1, 2, 3, 5]
    assert len(feedback_received) > 0, "Should have received at least one feedback"


# ---------------------------------------------------------------------------
# Action cancel smoke test – cancel Fibonacci goal after first feedback
# ---------------------------------------------------------------------------

def test_ros1_action_cancel(ros_master, fibonacci_server, smoke_artifact_collector):
    """Send Fibonacci goal and cancel it after first feedback."""
    from fireclaw_core.ros.ros1_config import Ros1EndpointConfig, Ros1TransportConfig
    from fireclaw_core.ros.ros1_transport import Ros1Transport

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

    smoke_artifact_collector.record("action_cancel", cancel_count >= 1)
    assert cancel_count >= 1, "Should have received at least one feedback before cancel"


# ---------------------------------------------------------------------------
# Action timeout smoke test – verify timeout on short wait
# ---------------------------------------------------------------------------

def test_ros1_action_timeout(ros_master, fibonacci_server, smoke_artifact_collector):
    """Verify timeout produces correct status when result takes too long."""
    from fireclaw_core.ros.ros1_config import Ros1EndpointConfig, Ros1TransportConfig
    from fireclaw_core.ros.ros1_transport import Ros1Transport

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

    smoke_artifact_collector.record("action_timeout", result["status"] == "timeout")
    assert result["status"] == "timeout"
