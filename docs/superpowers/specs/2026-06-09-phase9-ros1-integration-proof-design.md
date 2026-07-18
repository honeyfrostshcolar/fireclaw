# Phase 9: ROS1 Integration Proof — Design Spec

## Goal

Prove that FireClaw's `Ros1Transport` + `Ros1RuntimeModule` can connect to a real ROS1 master and complete topic, service, and action communication. Add ROS1 config examples, define a ROS2 adapter protocol boundary, enhance message introspection, and write deployment documentation.

## Non-Goals

- No ROS2 implementation (protocol definition only)
- No Gazebo/Ignition/Webots integration
- No map server or navigation stack integration
- No real hardware side effects beyond what turtlesim and actionlib_tutorials provide
- No CI/CD pipeline integration (marker-based skip only)

## Architecture

Phase 9 is 9 sequential tasks, each producing self-contained changes:

```
Phase 9: Real Robot / ROS Integration Proof
├── Task 1: ROS1 Smoke Test Infrastructure
├── Task 2: ROS1 Topic Smoke Test
├── Task 3: ROS1 Service Smoke Test
├── Task 4: ROS1 Action Smoke Test
├── Task 5: ROS1 Cancel/Timeout Smoke Test
├── Task 6: YAML Config Examples
├── Task 7: ROS2 Adapter Protocol
├── Task 8: Message Introspection Enhancement
└── Task 9: Deployment Docs
```

## Task 1: ROS1 Smoke Test Infrastructure

### Problem

Smoke tests need a real ROS master, but `pytest` runs without one by default.

### Design

Session-scoped pytest fixtures manage ROS process lifecycles:

```python
@pytest.fixture(scope="session")
def ros_master():
    proc = subprocess.Popen(["roscore"], ...)
    wait_for_ros_master(timeout=10)
    yield proc
    proc.terminate()

@pytest.fixture(scope="session")
def turtlesim_node(ros_master):
    proc = subprocess.Popen(["rosrun", "turtlesim", "turtlesim_node"], ...)
    wait_for_node("/turtlesim", timeout=10)
    yield proc
    proc.terminate()

@pytest.fixture(scope="session")
def fibonacci_server(ros_master):
    proc = subprocess.Popen(["rosrun", "actionlib_tutorials", "fibonacci_server.py"], ...)
    wait_for_action_server("/fibonacci", timeout=10)
    yield proc
    proc.terminate()
```

Marker: `@pytest.mark.ros1_smoke` registered in `pytest.ini`. All smoke tests share this marker. Default `pytest` skips them; `pytest -m ros1_smoke` runs them.

### Helper Functions

- `wait_for_ros_master(timeout)` — polls `rospy.get_master().getSystemState()`
- `wait_for_node(name, timeout)` — polls `rosnode._ping()`
- `wait_for_action_server(name, timeout)` — uses `actionlib.SimpleActionClient.wait_for_server()`

### Files

- Create: `tests/test_ros1_smoke.py`
- Modify: `pytest.ini` (register marker)

## Task 2: ROS1 Topic Smoke Test

### Scenario

Publish a `geometry_msgs/Twist` message to `/turtle1/cmd_vel` through `Ros1Transport`.

### Config

```yaml
endpoints:
  navigate_to_floor:
    interface: topic
    name: "/turtle1/cmd_vel"
    type: "geometry_msgs/Twist"
    goal_template:
      linear: {x: "${floor}", y: 0.0, z: 0.0}
      angular: {x: 0.0, y: 0.0, z: 0.0}
```

### Verification

- `transport.execute()` returns `{"status": "succeeded"}`
- `Ros1RuntimeModule.create_publisher` called with correct name/type
- Read `/turtle1/pose` to confirm turtle position changed

### Files

- Add test function to: `tests/test_ros1_smoke.py`

## Task 3: ROS1 Service Smoke Test

### Scenario

Call turtlesim `/clear` service (type `std_srvs/Empty`) through `Ros1Transport`.

### Verification

- `transport.execute()` returns `{"status": "succeeded", "response": ...}`
- Service proxy called with correct arguments

### Files

- Add test function to: `tests/test_ros1_smoke.py`

## Task 4: ROS1 Action Smoke Test

### Scenario

Send a Fibonacci goal (order=5) to `/fibonacci` action server through `Ros1Transport`.

### Config

```yaml
endpoints:
  navigate_to_floor:
    interface: action
    name: "/fibonacci"
    type: "actionlib_tutorials/FibonacciAction"
    cancel_supported: true
    feedback_supported: true
    goal_template:
      order: "${floor}"
```

### Verification

- Returns `{"status": "succeeded", "response": {"sequence": [0, 1, 1, 2, 3, 5]}}`
- Feedback callback triggered at least once
- `wait_for_server` and `wait_for_result` called with correct timeouts

### Files

- Add test function to: `tests/test_ros1_smoke.py`

## Task 5: ROS1 Cancel/Timeout Smoke Test

### Cancel Scenario

- Send Fibonacci goal (order=10)
- In feedback callback, trigger cancellation
- Verify `cancel_goal()` called, returns `{"status": "cancelled"}`

### Timeout Scenario

- Set `wait_for_result_seconds=0.1` (too short for Fibonacci)
- Verify returns `{"status": "timeout"}`

### Files

- Add test function to: `tests/test_ros1_smoke.py`

## Task 6: YAML Config Examples

### Files

- Create: `examples/ros1_configs/turtlesim_teleop.yaml`
- Create: `examples/ros1_configs/fibonacci_action.yaml`
- Create: `examples/ros1_configs/fireclaw_robot.yaml`

### turtlesim_teleop.yaml

Basic topic publishing for turtlesim control.

### fibonacci_action.yaml

Actionlib Fibonacci goal with cancel/feedback support.

### fireclaw_robot.yaml

Full firefighting robot config template with:
- navigate_to_floor (action, move_base_msgs/MoveBaseAction)
- search_for_victims (topic, std_msgs/String)
- emergency_stop (service, std_srvs/Trigger)
- namespace, timeouts, targets

## Task 7: ROS2 Adapter Protocol

### Design

Create `src/fireclaw_core/ros2_adapter.py` with a `Protocol` class that mirrors `RobotAdapter` but adds ROS2-specific concerns:

```python
class Ros2AdapterProtocol(Protocol):
    robot_id: str
    mode: str  # "ros2"
    dry_run: bool

    def navigate_to_floor(self, floor: int, **kwargs: Any) -> Any: ...
    def search_for_victims(self, floor: int, **kwargs: Any) -> Any: ...
    def assess_victim(self, floor: int, **kwargs: Any) -> Any: ...
    def report_status(self, floor: int, **kwargs: Any) -> Any: ...
    def return_to_safe_zone(self, **kwargs: Any) -> Any: ...
    def emergency_stop(self, reason: str | None = None, **kwargs: Any) -> Any: ...
    def get_robot_state(self) -> Any: ...
    def get_environment_state(self) -> Any: ...
    def capabilities(self) -> Any: ...
    def init_node(self, node_name: str) -> None: ...
    def shutdown_node(self) -> None: ...
```

### Files

- Create: `src/fireclaw_core/ros2_adapter.py`
- Create: `tests/test_ros2_adapter.py`

## Task 8: Message Introspection Enhancement

### Enhancement 1: Type-safe _resolve_ros_type

Better error messages when ROS type packages are missing:

```python
def _resolve_ros_type(self, type_name, *, preferred_module):
    package, _, class_name = type_name.partition("/")
    try:
        module = import_module(f"{package}.{preferred_module}")
    except ImportError:
        available = self._list_available_types(package)
        raise RuntimeError(f"ROS type package '{package}' not installed. Available: {available or 'none'}")
    try:
        return getattr(module, class_name)
    except AttributeError:
        available = [n for n in dir(module) if not n.startswith("_")]
        raise RuntimeError(f"ROS type '{class_name}' not found in {package}.{preferred_module}. Available: {available}")
```

### Enhancement 2: __slots__-aware _response_to_data

Prefer `__slots__` over `__dict__` for ROS messages (ROS messages use slots):

```python
def _response_to_data(response):
    if isinstance(response, (dict, list, str, int, float, bool)) or response is None:
        return response
    if hasattr(response, "__slots__"):
        return {slot: _response_to_data(getattr(response, slot)) for slot in response.__slots__}
    if hasattr(response, "__dict__"):
        return {k: _response_to_data(v) for k, v in vars(response).items() if not k.startswith("_")}
    return str(response)
```

### Enhancement 3: Payload validation (optional)

```python
def validate_payload_against_type(payload: dict, msg_class: Any) -> list[str]:
    errors = []
    if not hasattr(msg_class, "__slots__"):
        return errors
    for key in payload:
        if key not in msg_class.__slots__:
            errors.append(f"Unknown field '{key}' for {msg_class.__name__}")
    return errors
```

### Files

- Modify: `src/fireclaw_core/ros1_transport.py`
- Modify: `tests/test_ros1_transport.py`

## Task 9: Deployment Docs

### Content

`docs/deployment/ros1-deployment-guide.md`:

1. Prerequisites: Ubuntu 20.04 + ROS1 Noetic + rospy + actionlib
2. Installation: pip install or git clone
3. Config: ros1_adapter_config.yaml field reference
4. Startup: roscore → robot nodes → FireClaw Gateway
5. Smoke test: pytest -m ros1_smoke
6. Troubleshooting table

### Files

- Create: `docs/deployment/ros1-deployment-guide.md`

## Testing Strategy

| Test Type | Coverage |
|-----------|----------|
| Unit tests | Existing fakes (test_ros1_transport.py, test_ros1_config.py) — unchanged |
| Smoke tests | Real ROS master + turtlesim + actionlib_tutorials — new |
| Protocol tests | ROS2 protocol can be implemented — new |

Smoke tests are gated by `@pytest.mark.ros1_smoke` and require ROS1 environment. They do not replace existing unit tests.

## Acceptance Criteria

1. `pytest -m ros1_smoke` passes with roscore + turtlesim + fibonacci_server running
2. Topic publish to turtlesim moves the turtle
3. Service call to turtlesim /clear succeeds
4. Action goal to Fibonacci returns correct sequence
5. Cancel and timeout produce correct status
6. YAML config examples parse correctly via `load_ros1_adapter_config()`
7. `Ros2AdapterProtocol` is defined and implementable
8. `_resolve_ros_type` gives clear error on missing packages
9. `_response_to_data` handles __slots__ messages
10. Deployment guide exists and is accurate
