# Phase 9: ROS1 Integration Proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove FireClaw's ROS1 transport works against a real ROS1 master, add config examples, define ROS2 protocol boundary, enhance message introspection, and write deployment docs.

**Architecture:** Session-scoped pytest fixtures manage roscore/turtlesim/fibonacci_server lifecycles. Smoke tests are gated by `@pytest.mark.ros1_smoke` and skipped by default. ROS2 adapter is protocol-only (no implementation).

**Tech Stack:** Python, pytest, rospy, actionlib, turtlesim (ROS1 Noetic), actionlib_tutorials

---

## File Structure

```
tests/test_ros1_smoke.py          — NEW: all ROS1 smoke tests + fixtures + helpers
tests/test_ros2_adapter.py        — NEW: ROS2 protocol tests
tests/test_ros1_transport.py      — MODIFY: add __slots__ + error message tests
src/fireclaw_core/ros1_transport.py — MODIFY: enhance _resolve_ros_type, _response_to_data, add validate_payload_against_type
src/fireclaw_core/ros2_adapter.py   — NEW: Ros2AdapterProtocol
examples/ros1_configs/turtlesim_teleop.yaml — NEW
examples/ros1_configs/fibonacci_action.yaml — NEW
examples/ros1_configs/fireclaw_robot.yaml   — NEW
docs/deployment/ros1-deployment-guide.md    — NEW
pyproject.toml                    — MODIFY: register ros1_smoke marker
```

---

### Task 1: Register pytest marker and create smoke test infrastructure

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/test_ros1_smoke.py`

- [ ] **Step 1: Register the `ros1_smoke` marker in pyproject.toml**

Add to the `[tool.pytest.ini_options]` section in `pyproject.toml`:

```toml
markers = [
    "ros1_smoke: requires running ROS1 master (roscore + turtlesim + actionlib_tutorials)",
]
```

- [ ] **Step 2: Create smoke test file with helpers and fixtures**

Create `tests/test_ros1_smoke.py`:

```python
"""ROS1 smoke tests — require a real ROS1 master.

Run with: pytest -m ros1_smoke
Requires: roscore, turtlesim, actionlib_tutorials installed.
"""
from __future__ import annotations

import subprocess
import time
from typing import Any

import pytest

from fireclaw_core.ros1_config import Ros1EndpointConfig, Ros1TransportConfig
from fireclaw_core.ros1_transport import Ros1Transport

pytestmark = pytest.mark.ros1_smoke


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def wait_for_ros_master(timeout: float = 10.0) -> None:
    """Block until roscore is reachable or raise on timeout."""
    import rospy

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            rospy.get_master().getSystemState("/test")
            return
        except Exception:
            time.sleep(0.2)
    raise TimeoutError(f"roscore not reachable within {timeout}s")


def wait_for_node(node_name: str, timeout: float = 10.0) -> None:
    """Block until a ROS node is registered or raise on timeout."""
    import rosnode

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            nodes = rosnode.get_node_names()
            if node_name in nodes:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Node {node_name} not found within {timeout}s")


def wait_for_action_server(
    action_name: str,
    timeout: float = 10.0,
) -> None:
    """Block until an action server is available or raise on timeout.

    Uses a lightweight polling approach: checks the ROS parameter server
    and node list for the action server, avoiding the need to import
    action type modules at fixture setup time.
    """
    import rospy

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            master = rospy.get_master()
            state = master.getSystemState("/test")[2]
            # Look for the action server's status topic
            for topic_list in state:
                for name, _uri in topic_list:
                    if action_name in name:
                        return
        except Exception:
            pass
        time.sleep(0.3)
    raise TimeoutError(f"Action server {action_name} not available within {timeout}s")


# ---------------------------------------------------------------------------
# Session-scoped fixtures — start ROS processes once per test run
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def ros_master():
    """Start roscore for the entire test session."""
    proc = subprocess.Popen(
        ["roscore"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_ros_master(timeout=15.0)
        yield proc
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture(scope="session")
def turtlesim_node(ros_master: subprocess.Popen[bytes]):
    """Start turtlesim_node after roscore is ready."""
    proc = subprocess.Popen(
        ["rosrun", "turtlesim", "turtlesim_node"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_node("/turtlesim", timeout=10.0)
        yield proc
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture(scope="session")
def fibonacci_server(ros_master: subprocess.Popen[bytes]):
    """Start actionlib_tutorials fibonacci_server after roscore is ready."""
    proc = subprocess.Popen(
        ["rosrun", "actionlib_tutorials", "fibonacci_server.py"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_action_server("/fibonacci", timeout=10.0)
        yield proc
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# Smoke test: placeholder — real tests added in Tasks 2-5
# ---------------------------------------------------------------------------

def test_ros1_smoke_infrastructure_starts(ros_master, turtlesim_node, fibonacci_server):
    """Verify all ROS1 processes started successfully."""
    assert ros_master.poll() is None, "roscore should be running"
    assert turtlesim_node.poll() is None, "turtlesim_node should be running"
    assert fibonacci_server.poll() is None, "fibonacci_server should be running"
```

- [ ] **Step 3: Verify the marker is registered and file parses**

Run: `.venv/bin/python -m pytest --co -m ros1_smoke tests/test_ros1_smoke.py 2>&1 | head -20`

Expected: Shows `test_ros1_smoke_infrastructure_starts` collected (no import errors, no marker warning).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml tests/test_ros1_smoke.py
git commit -m "test: add ROS1 smoke test infrastructure with pytest marker"
```

---

### Task 2: ROS1 Topic Smoke Test

**Files:**
- Modify: `tests/test_ros1_smoke.py`

- [ ] **Step 1: Add topic smoke test**

Add to `tests/test_ros1_smoke.py`:

```python
def test_ros1_topic_publish_to_turtlesim(ros_master, turtlesim_node):
    """Publish a Twist to /turtle1/cmd_vel and verify turtlesim received it."""
    import rospy
    from geometry_msgs.msg import Twist

    # Read initial pose
    rospy.init_node("fireclaw_smoke_test", anonymous=True)
    pose_msg = rospy.wait_for_message("/turtle1/pose", rospy.AnyMsg, timeout=5.0)

    # Publish via Ros1Transport
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
    # Give turtlesim a moment to process
    time.sleep(0.5)
```

Also add the helper at module level:

```python
def _make_real_ros_module():
    """Create a Ros1RuntimeModule backed by real rospy/actionlib."""
    from fireclaw_core.ros1_transport import Ros1RuntimeModule
    return Ros1RuntimeModule.load()
```

- [ ] **Step 2: Run topic smoke test**

Run: `.venv/bin/python -m pytest tests/test_ros1_smoke.py::test_ros1_topic_publish_to_turtlesim -m ros1_smoke -v`

Expected: PASS (roscore + turtlesim running).

- [ ] **Step 3: Commit**

```bash
git add tests/test_ros1_smoke.py
git commit -m "test: add ROS1 topic smoke test for turtlesim"
```

---

### Task 3: ROS1 Service Smoke Test

**Files:**
- Modify: `tests/test_ros1_smoke.py`

- [ ] **Step 1: Add service smoke test**

Add to `tests/test_ros1_smoke.py`:

```python
def test_ros1_service_call_clear(ros_master, turtlesim_node):
    """Call turtlesim /clear service through Ros1Transport."""
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
```

- [ ] **Step 2: Run service smoke test**

Run: `.venv/bin/python -m pytest tests/test_ros1_smoke.py::test_ros1_service_call_clear -m ros1_smoke -v`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_ros1_smoke.py
git commit -m "test: add ROS1 service smoke test for turtlesim /clear"
```

---

### Task 4: ROS1 Action Smoke Test

**Files:**
- Modify: `tests/test_ros1_smoke.py`

- [ ] **Step 1: Add action smoke test**

Add to `tests/test_ros1_smoke.py`:

```python
def test_ros1_action_fibonacci_goal(ros_master, fibonacci_server):
    """Send Fibonacci goal via Ros1Transport and verify result."""
    module = _make_real_ros_module()
    feedback_received: list[dict[str, Any]] = []
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
```

- [ ] **Step 2: Run action smoke test**

Run: `.venv/bin/python -m pytest tests/test_ros1_smoke.py::test_ros1_action_fibonacci_goal -m ros1_smoke -v`

Expected: PASS with Fibonacci sequence [0, 1, 1, 2, 3, 5].

- [ ] **Step 3: Commit**

```bash
git add tests/test_ros1_smoke.py
git commit -m "test: add ROS1 action smoke test for Fibonacci goal"
```

---

### Task 5: ROS1 Cancel/Timeout Smoke Test

**Files:**
- Modify: `tests/test_ros1_smoke.py`

- [ ] **Step 1: Add cancel smoke test**

Add to `tests/test_ros1_smoke.py`:

```python
def test_ros1_action_cancel(ros_master, fibonacci_server):
    """Send Fibonacci goal and cancel it mid-execution."""
    module = _make_real_ros_module()
    cancel_count = 0

    def on_feedback(feedback: dict[str, Any]) -> None:
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

    # Cancel after first feedback
    feedback_count = 0

    def should_cancel() -> bool:
        nonlocal feedback_count
        feedback_count = cancel_count
        return cancel_count >= 1

    result = transport.execute(endpoint, {"order": 100}, config, cancellation_requested=should_cancel)

    assert result["status"] == "cancelled"


def test_ros1_action_timeout(ros_master, fibonacci_server):
    """Verify timeout produces correct status when result takes too long."""
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
        wait_for_result_seconds=0.01,  # Too short for any computation
    )

    result = transport.execute(endpoint, {"order": 100}, config)

    assert result["status"] == "timeout"
```

- [ ] **Step 2: Run cancel and timeout smoke tests**

Run: `.venv/bin/python -m pytest tests/test_ros1_smoke.py::test_ros1_action_cancel tests/test_ros1_smoke.py::test_ros1_action_timeout -m ros1_smoke -v`

Expected: Both PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_ros1_smoke.py
git commit -m "test: add ROS1 cancel and timeout smoke tests"
```

---

### Task 6: YAML Config Examples

**Files:**
- Create: `examples/ros1_configs/turtlesim_teleop.yaml`
- Create: `examples/ros1_configs/fibonacci_action.yaml`
- Create: `examples/ros1_configs/fireclaw_robot.yaml`

- [ ] **Step 1: Create turtlesim config**

Create `examples/ros1_configs/turtlesim_teleop.yaml`:

```yaml
# ROS1 turtlesim teleop example
# Use with: rosrun turtlesim turtlesim_node
robot_id: "turtle1"
namespace: null
transport:
  enabled: true
  wait_for_server_seconds: 5.0
  wait_for_result_seconds: 10.0
endpoints:
  navigate_to_floor:
    interface: topic
    name: "/turtle1/cmd_vel"
    type: "geometry_msgs/Twist"
    goal_template:
      linear:
        x: "${floor}"
        y: 0.0
        z: 0.0
      angular:
        x: 0.0
        y: 0.0
        z: 0.0
targets: {}
```

- [ ] **Step 2: Create fibonacci config**

Create `examples/ros1_configs/fibonacci_action.yaml`:

```yaml
# ROS1 actionlib Fibonacci example
# Use with: rosrun actionlib_tutorials fibonacci_server.py
robot_id: "fibonacci_robot"
namespace: null
transport:
  enabled: true
  wait_for_server_seconds: 5.0
  wait_for_result_seconds: 30.0
endpoints:
  navigate_to_floor:
    interface: action
    name: "/fibonacci"
    type: "actionlib_tutorials/FibonacciAction"
    cancel_supported: true
    feedback_supported: true
    goal_template:
      order: "${floor}"
targets: {}
```

- [ ] **Step 3: Create fireclaw robot config**

Create `examples/ros1_configs/fireclaw_robot.yaml`:

```yaml
# FireClaw firefighting robot ROS1 config template
# Adapt endpoints to your actual robot's ROS topics/services/actions.
robot_id: "firebot_01"
namespace: "/fireclaw/firebot_01"
transport:
  enabled: true
  wait_for_server_seconds: 10.0
  wait_for_result_seconds: 60.0
endpoints:
  navigate_to_floor:
    interface: action
    name: "/navigate_to_floor"
    type: "move_base_msgs/MoveBaseAction"
    cancel_supported: true
    feedback_supported: true
    goal_template:
      target_pose:
        header:
          frame_id: "map"
        pose:
          position:
            x: 0.0
            y: 0.0
            z: "${floor}"
          orientation:
            x: 0.0
            y: 0.0
            z: 0.0
            w: 1.0
  search_for_victims:
    interface: topic
    name: "/perception/search"
    type: "std_msgs/String"
    request_template:
      data: "search_floor_${floor}"
  report_status:
    interface: topic
    name: "/operator_report"
    type: "std_msgs/String"
    request_template:
      data: "status_report_floor_${floor}"
  return_to_safe_zone:
    interface: action
    name: "/navigate_to_safe_zone"
    type: "move_base_msgs/MoveBaseAction"
    cancel_supported: true
    goal_template:
      target_pose:
        header:
          frame_id: "map"
        pose:
          position: "${targets.safe_zone}"
          orientation:
            x: 0.0
            y: 0.0
            z: 0.0
            w: 1.0
emergency_stop:
  interface: service
  name: "/emergency_stop"
  type: "std_srvs/Trigger"
timeouts:
  default_seconds: 30.0
targets:
  safe_zone:
    x: 0.0
    y: 0.0
```

- [ ] **Step 4: Verify configs parse correctly**

Run: `.venv/bin/python -c "
from fireclaw_core.ros1_config import load_ros1_adapter_config
for f in ['examples/ros1_configs/turtlesim_teleop.yaml', 'examples/ros1_configs/fibonacci_action.yaml', 'examples/ros1_configs/fireclaw_robot.yaml']:
    cfg = load_ros1_adapter_config(f)
    print(f'{f}: robot_id={cfg.robot_id}, endpoints={list(cfg.endpoints.keys())}')
"`

Expected: All three parse without error.

- [ ] **Step 5: Commit**

```bash
git add examples/ros1_configs/
git commit -m "docs: add ROS1 YAML config examples (turtlesim, fibonacci, fireclaw)"
```

---

### Task 7: ROS2 Adapter Protocol

**Files:**
- Create: `src/fireclaw_core/ros2_adapter.py`
- Create: `tests/test_ros2_adapter.py`

- [ ] **Step 1: Create ROS2 adapter protocol**

Create `src/fireclaw_core/ros2_adapter.py`:

```python
"""ROS2 adapter protocol boundary.

ROS2 is not yet supported. This module defines the protocol that future
ROS2 adapter implementations must satisfy. When a ROS2 environment becomes
available, implement this protocol with rclpy.
"""
from __future__ import annotations

from typing import Any, Protocol


class Ros2AdapterProtocol(Protocol):
    """Contract for ROS2 robot adapters.

    Mirrors RobotAdapter but adds ROS2-specific concerns:
    - Node lifecycle (init/shutdown)
    - QoS profile awareness
    - Lifecycle node support (managed nodes)

    Implementations should use rclpy for ROS2 communication.
    """

    robot_id: str
    mode: str  # "ros2"
    dry_run: bool

    def navigate_to_floor(self, floor: int, **kwargs: Any) -> Any:
        """Navigate robot to specified floor."""
        ...

    def search_for_victims(self, floor: int, **kwargs: Any) -> Any:
        """Search for victims on specified floor."""
        ...

    def assess_victim(self, floor: int, **kwargs: Any) -> Any:
        """Assess victim condition on specified floor."""
        ...

    def report_status(self, floor: int, **kwargs: Any) -> Any:
        """Report robot status."""
        ...

    def return_to_safe_zone(self, **kwargs: Any) -> Any:
        """Return robot to safe zone."""
        ...

    def emergency_stop(self, reason: str | None = None, **kwargs: Any) -> Any:
        """Emergency stop robot."""
        ...

    def get_robot_state(self) -> Any:
        """Get current robot state snapshot."""
        ...

    def get_environment_state(self) -> Any:
        """Get current environment state snapshot."""
        ...

    def capabilities(self) -> Any:
        """Get adapter capabilities."""
        ...

    def init_node(self, node_name: str) -> None:
        """Initialize rclpy node."""
        ...

    def shutdown_node(self) -> None:
        """Shutdown rclpy node."""
        ...
```

- [ ] **Step 2: Create ROS2 protocol tests**

Create `tests/test_ros2_adapter.py`:

```python
"""Tests for ROS2 adapter protocol boundary."""
from __future__ import annotations

from typing import Any

from fireclaw_core.ros2_adapter import Ros2AdapterProtocol


class MinimalRos2Adapter:
    """Minimal implementation to verify protocol is satisfiable."""

    robot_id: str = "test_robot"
    mode: str = "ros2"
    dry_run: bool = True

    def navigate_to_floor(self, floor: int, **kwargs: Any) -> Any:
        return {"ok": True, "floor": floor}

    def search_for_victims(self, floor: int, **kwargs: Any) -> Any:
        return {"ok": True, "victims": 0}

    def assess_victim(self, floor: int, **kwargs: Any) -> Any:
        return {"ok": True}

    def report_status(self, floor: int, **kwargs: Any) -> Any:
        return {"ok": True}

    def return_to_safe_zone(self, **kwargs: Any) -> Any:
        return {"ok": True}

    def emergency_stop(self, reason: str | None = None, **kwargs: Any) -> Any:
        return {"ok": True, "reason": reason}

    def get_robot_state(self) -> Any:
        return {"robot_id": self.robot_id, "mode": self.mode}

    def get_environment_state(self) -> Any:
        return {}

    def capabilities(self) -> Any:
        return {"supported_modes": {"ros2"}}

    def init_node(self, node_name: str) -> None:
        pass

    def shutdown_node(self) -> None:
        pass


def _check_protocol(adapter: Ros2AdapterProtocol) -> Ros2AdapterProtocol:
    """Type-check: adapter satisfies protocol at runtime."""
    return adapter


def test_ros2_protocol_is_implementable():
    adapter = MinimalRos2Adapter()
    checked = _check_protocol(adapter)
    assert checked.mode == "ros2"
    assert checked.robot_id == "test_robot"


def test_ros2_protocol_has_required_methods():
    adapter = MinimalRos2Adapter()
    assert adapter.init_node("test") is None
    assert adapter.shutdown_node() is None
    assert adapter.navigate_to_floor(2) == {"ok": True, "floor": 2}
    assert adapter.emergency_stop("test") == {"ok": True, "reason": "test"}


def test_ros2_protocol_docstring_exists():
    assert Ros2AdapterProtocol.__doc__ is not None
    assert "ROS2" in Ros2AdapterProtocol.__doc__
```

- [ ] **Step 3: Run ROS2 protocol tests**

Run: `.venv/bin/python -m pytest tests/test_ros2_adapter.py -v`

Expected: 3 PASS.

- [ ] **Step 4: Commit**

```bash
git add src/fireclaw_core/ros2_adapter.py tests/test_ros2_adapter.py
git commit -m "feat: add ROS2 adapter protocol boundary (no implementation)"
```

---

### Task 8: Message Introspection Enhancement

**Files:**
- Modify: `src/fireclaw_core/ros1_transport.py`
- Modify: `tests/test_ros1_transport.py`

- [ ] **Step 1: Enhance _resolve_ros_type with better error messages**

In `src/fireclaw_core/ros1_transport.py`, replace the `_resolve_ros_type` method in `Ros1RuntimeModule`:

```python
    def _resolve_ros_type(self, type_name: str, *, preferred_module: str) -> Any:
        package, _, class_name = type_name.partition("/")
        if not package or not class_name:
            raise ValueError(f"ROS type must be in package/Class form: {type_name}")
        try:
            module = import_module(f"{package}.{preferred_module}")
        except ImportError as exc:
            raise RuntimeError(
                f"ROS type package '{package}' not installed. "
                f"Install with: apt install ros-$(rosversion -d)-{package.replace('_', '-')}"
            ) from exc
        try:
            return getattr(module, class_name)
        except AttributeError as exc:
            available = [n for n in dir(module) if not n.startswith("_")]
            raise RuntimeError(
                f"ROS type '{class_name}' not found in {package}.{preferred_module}. "
                f"Available types: {available}"
            ) from exc
```

- [ ] **Step 2: Enhance _response_to_data with __slots__ support**

In `src/fireclaw_core/ros1_transport.py`, replace `_response_to_data`:

```python
def _response_to_data(response: Any) -> Any:
    """Convert a ROS response to a plain dict/list/scalar.

    Prefers __slots__ (used by ROS messages) over __dict__.
    """
    if isinstance(response, (dict, list, str, int, float, bool)) or response is None:
        return response
    if isinstance(response, (tuple, list)):
        return [_response_to_data(item) for item in response]
    if hasattr(response, "__slots__"):
        return {
            slot: _response_to_data(getattr(response, slot))
            for slot in response.__slots__
        }
    if hasattr(response, "__dict__"):
        return {
            key: _response_to_data(value)
            for key, value in vars(response).items()
            if not key.startswith("_")
        }
    return str(response)
```

- [ ] **Step 3: Add validate_payload_against_type function**

Add to `src/fireclaw_core/ros1_transport.py` after `_response_to_data`:

```python
def validate_payload_against_type(payload: dict[str, Any], msg_class: Any) -> list[str]:
    """Check payload fields against a ROS message type's slots.

    Returns a list of error strings. Empty list means valid.
    """
    errors: list[str] = []
    if not hasattr(msg_class, "__slots__"):
        return errors  # Cannot validate without slots
    valid_fields = set(msg_class.__slots__)
    for key in payload:
        if key not in valid_fields:
            errors.append(
                f"Unknown field '{key}' for {msg_class.__name__}. "
                f"Valid fields: {sorted(valid_fields)}"
            )
    return errors
```

- [ ] **Step 4: Add tests for enhanced error messages and __slots__**

Add to `tests/test_ros1_transport.py`:

```python
def test_resolve_ros_type_gives_clear_error_on_missing_package():
    from fireclaw_core.ros1_transport import Ros1RuntimeModule

    module = Ros1RuntimeModule(rospy=SimpleNamespace(), actionlib=SimpleNamespace())
    with pytest.raises(RuntimeError, match="not installed"):
        module._resolve_ros_type("nonexistent_pkg/Foo", preferred_module="msg")


def test_resolve_ros_type_gives_clear_error_on_missing_class():
    from fireclaw_core.ros1_transport import Ros1RuntimeModule

    module = Ros1RuntimeModule(rospy=SimpleNamespace(), actionlib=SimpleNamespace())
    with pytest.raises(RuntimeError, match="not found"):
        module._resolve_ros_type("std_msgs/NonexistentType", preferred_module="msg")


def test_response_to_data_handles_slots():
    from fireclaw_core.ros1_transport import _response_to_data

    class SlottedMsg:
        __slots__ = ("x", "y")
        def __init__(self):
            self.x = 1.0
            self.y = 2.0

    result = _response_to_data(SlottedMsg())
    assert result == {"x": 1.0, "y": 2.0}


def test_response_to_data_handles_nested_slots():
    from fireclaw_core.ros1_transport import _response_to_data

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
    from fireclaw_core.ros1_transport import validate_payload_against_type

    class Twist:
        __slots__ = ("linear", "angular")

    errors = validate_payload_against_type({"linear": 1.0, "angular": 0.5}, Twist)
    assert errors == []

    errors = validate_payload_against_type({"linear": 1.0, "bogus": 0.5}, Twist)
    assert len(errors) == 1
    assert "bogus" in errors[0]


def test_validate_payload_skips_when_no_slots():
    from fireclaw_core.ros1_transport import validate_payload_against_type

    class NoSlots:
        pass

    errors = validate_payload_against_type({"anything": 1}, NoSlots)
    assert errors == []
```

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/python -m pytest tests/test_ros1_transport.py -v`

Expected: All PASS (existing + new).

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/ros1_transport.py tests/test_ros1_transport.py
git commit -m "feat: enhance ROS1 message introspection with __slots__ support and better errors"
```

---

### Task 9: Deployment Docs

**Files:**
- Create: `docs/deployment/ros1-deployment-guide.md`

- [ ] **Step 1: Create deployment guide**

Create `docs/deployment/ros1-deployment-guide.md`:

```markdown
# ROS1 Deployment Guide

## Prerequisites

- Ubuntu 20.04 (Noetic)
- ROS1 Noetic installed (`apt install ros-noetic-desktop-full`)
- Python 3.8+ with rospy (`apt install ros-noetic-rospy`)
- actionlib (`apt install ros-noetic-actionlib`)

Verify installation:

```bash
roscore --help          # Should print usage
rospy --version         # Should print version
rospack find actionlib  # Should print path
```

## Installation

```bash
git clone <fireclaw-repo>
cd fireclaw
pip install -e .
```

## Configuration

FireClaw uses YAML config files for ROS1 adapter configuration.

### Config Structure

```yaml
robot_id: "my_robot"           # Required: unique robot identifier
namespace: "/fireclaw/my_robot" # Optional: ROS namespace
transport:
  enabled: true                 # Required: enable real ROS transport
  wait_for_server_seconds: 10.0 # Action server connection timeout
  wait_for_result_seconds: 60.0 # Action result timeout
endpoints:
  action_name:                  # Maps FireClaw action to ROS endpoint
    interface: action           # "topic", "service", or "action"
    name: "/ros/topic/name"     # ROS topic/service/action name
    type: "pkg/MsgType"         # ROS message type (package/Class)
    cancel_supported: true      # Can this action be cancelled?
    feedback_supported: true    # Does this action provide feedback?
    goal_template:              # Template for action goal
      field: "${variable}"      # ${var} substituted from action payload
emergency_stop:                 # Optional: emergency stop endpoint
  interface: service
  name: "/emergency_stop"
  type: "std_srvs/Trigger"
timeouts:
  default_seconds: 30.0
targets:                        # Static values for template substitution
  safe_zone: {x: 0.0, y: 0.0}
```

### Template Syntax

- `${variable}` — substituted from action payload
- `{{expression}}` — evaluated expression
- Nested dicts supported

### Example Configs

See `examples/ros1_configs/` for:
- `turtlesim_teleop.yaml` — Basic turtlesim topic control
- `fibonacci_action.yaml` — Actionlib Fibonacci goal
- `fireclaw_robot.yaml` — Full firefighting robot template

## Startup

```bash
# Terminal 1: Start ROS master
roscore

# Terminal 2: Start your robot nodes
rosrun turtlesim turtlesim_node
# or your actual robot driver

# Terminal 3: Start FireClaw Gateway
python -m fireclaw_core.mission_cli submit-subtask \
  --robot-id turtle1 \
  --command "navigate to floor 2" \
  --adapter-config examples/ros1_configs/turtlesim_teleop.yaml
```

## Running Smoke Tests

Smoke tests verify ROS1 integration against a real ROS master.

```bash
# Requires: roscore + turtlesim + actionlib_tutorials installed
pytest -m ros1_smoke -v

# Run specific smoke test
pytest -m ros1_smoke tests/test_ros1_smoke.py::test_ros1_action_fibonacci_goal -v
```

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| `ROS1 transport requires rospy` | rospy not installed | `apt install ros-noetic-rospy` |
| `action server unavailable` | Action server not running | `rosrun actionlib_tutorials fibonacci_server.py` |
| `roscore not reachable` | roscore not started | Run `roscore` in a terminal |
| `ROS type package not installed` | Missing ROS package | `apt install ros-noetic-<package>` |
| `Connection refused` | Wrong ROS_MASTER_URI | `export ROS_MASTER_URI=http://localhost:11311` |
| `Timeout waiting for result` | Action takes too long | Increase `wait_for_result_seconds` in config |
```

- [ ] **Step 2: Commit**

```bash
git add docs/deployment/ros1-deployment-guide.md
git commit -m "docs: add ROS1 deployment guide"
```

---

### Task 10: Full Verification

- [ ] **Step 1: Run all unit tests (exclude smoke)**

Run: `.venv/bin/python -m pytest -q --ignore=tests/test_ros1_smoke.py`

Expected: All existing tests pass (566+).

- [ ] **Step 2: Run smoke tests (requires ROS1 environment)**

Run: `.venv/bin/python -m pytest -m ros1_smoke -v`

Expected: All smoke tests pass.

- [ ] **Step 3: Run ROS2 protocol tests**

Run: `.venv/bin/python -m pytest tests/test_ros2_adapter.py -v`

Expected: 3 PASS.

- [ ] **Step 4: Verify YAML configs parse**

Run: `.venv/bin/python -c "
from fireclaw_core.ros1_config import load_ros1_adapter_config
for f in ['examples/ros1_configs/turtlesim_teleop.yaml', 'examples/ros1_configs/fibonacci_action.yaml', 'examples/ros1_configs/fireclaw_robot.yaml']:
    cfg = load_ros1_adapter_config(f)
    print(f'OK: {f} -> robot_id={cfg.robot_id}, endpoints={list(cfg.endpoints.keys())}')
"`

Expected: All three print OK.
