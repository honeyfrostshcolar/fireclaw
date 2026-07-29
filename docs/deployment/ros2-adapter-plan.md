# ROS2 Adapter Implementation Plan

## Goal

Add a native ROS2 (`rclpy`) adapter to FireClaw so that the agent framework can
control robots running ROS2 Humble (or later) without depending on the ROS1
bridge.

## Architecture

The ROS2 adapter follows the same boundary pattern as the existing ROS1 adapter:
the agent core never imports `rclpy` directly.  A transport layer translates
between FireClaw's dict-based action payloads and ROS2 message types, and the
adapter layer owns node lifecycle, pub/sub, service, and action client wiring.

```
agent core
   |
   v
Ros2Adapter (RobotAdapter protocol)
   |
   v
Ros2Transport (dict <-> ROS2 msg conversion)
   |
   v
rclpy Node (lifecycle, spin, shutdown)
```

## Implementation Phases

### Phase 1: rclpy Node Lifecycle

**Goal:** Create and destroy an `rclpy` node cleanly.

- [ ] Implement `Ros2NodeManager` that calls `rclpy.init()` / `rclpy.shutdown()`.
- [ ] Create a node with a configurable namespace and node name derived from `robot_id`.
- [ ] Support context-manager usage (`with Ros2NodeManager(...) as node:`).
- [ ] Unit test: init/shutdown without raising, node name matches config.

### Phase 2: Publishers (Topic Control)

**Goal:** Publish dict payloads to ROS2 topics.

- [ ] Implement `publish(topic_name, msg_type, payload_dict)` on the transport.
- [ ] Use `rclpy.serialization` or `rosidl_runtime_py.utilities` to resolve message class from `package/MsgType` string.
- [ ] Convert flat dict to ROS2 message via recursive field assignment.
- [ ] Unit test with mock publisher; verify dict-to-msg conversion for `geometry_msgs/Twist`.

### Phase 3: Services (Call/Response)

**Goal:** Call ROS2 services synchronously.

- [ ] Implement `call_service(service_name, srv_type, request_dict) -> response_dict`.
- [ ] Resolve service type from `package/SrvType` string.
- [ ] Convert request dict to ROS2 request message, call, convert response back.
- [ ] Unit test with mock service server; verify round-trip for `std_srvs/Trigger`.

### Phase 4: Actions (Goal/Feedback/Result/Cancel)

**Goal:** Send goals, receive feedback, get results, cancel.

- [ ] Implement `ActionClientWrapper` that wraps `rclpy.action.ActionClient`.
- [ ] `send_goal(action_name, action_type, goal_dict)` returns a future.
- [ ] Feedback callback converts feedback message to dict and emits into FireClaw event stream.
- [ ] `cancel_goal(handle)` sends cancel request.
- [ ] `get_result(handle)` blocks with configurable timeout.
- [ ] Unit test with mock action server (or `example_interfaces/action/Fibonacci`).

### Phase 5: Typed Message Conversion (dict <-> ROS2 msg)

**Goal:** Robust, recursive conversion between plain dicts and ROS2 message objects.

- [ ] Implement `dict_to_ros_msg(msg_class, data: dict) -> object`.
- [ ] Implement `ros_msg_to_dict(msg: object) -> dict`.
- [ ] Handle nested messages, arrays, booleans, strings, numeric types.
- [ ] Handle `default` values for unset fields.
- [ ] Unit test round-trip for `geometry_msgs/Twist`, `nav_msgs/Odometry`, `std_msgs/Header`.

### Phase 6: Feedback and Cancel Handling

**Goal:** Wire ROS2 action feedback and cancellation into FireClaw's action runtime.

- [ ] Implement feedback sink that converts ROS2 feedback messages to `action.feedback` events.
- [ ] Implement cancel propagation: FireClaw cancel request -> ROS2 cancel goal.
- [ ] Handle edge cases: server not available, goal rejected, cancel timeout.
- [ ] Unit test: feedback events emitted, cancel propagates correctly.

### Phase 7: Smoke Test Marker

**Goal:** Gate ROS2 smoke tests behind an environment variable, like ROS1.

- [ ] Add `FIRECLAW_RUN_ROS2_SMOKE` environment variable marker.
- [ ] Create `tests/test_ros2_smoke.py` with `@pytest.mark.skipif` guard.
- [ ] Smoke tests: topic publish, service call, action goal, action cancel.
- [ ] Use `example_interfaces` (Fibonacci action, AddTwoInts service) for portable smoke tests.
- [ ] Document in `docs/deployment/ros2-smoke-test-template.md`.

## Dependencies

| Package | Purpose | Version |
|---------|---------|---------|
| `rclpy` | ROS2 Python client library | Humble+ |
| `geometry_msgs` | Common geometry message types | Humble+ |
| `std_srvs` | Standard service types (Trigger, Empty) | Humble+ |
| `example_interfaces` | Fibonacci action, AddTwoInts service (testing) | Humble+ |
| `rosidl_runtime_py` | Message class resolution from type strings | Humble+ |
| `action_msgs` | Action status types | Humble+ |

All dependencies are available in a standard ROS2 Humble desktop install.
No additional pip packages are required.

## Testing Strategy

### Unit Tests (no ROS2 runtime required)

- Mock `rclpy` node, publisher, service client, action client.
- Test dict-to-msg and msg-to-dict conversion in isolation.
- Test adapter lifecycle (init, create node, shutdown).
- Test error handling (timeout, server unavailable, type resolution failure).

### Integration Tests (ROS2 runtime required)

- Run against a local ROS2 daemon (`ros2 daemon start`).
- Use `example_interfaces` for portable tests.
- Gate behind `FIRECLAW_RUN_ROS2_SMOKE=1`.

### Smoke Tests (real robot)

- Follow the pattern in `ros1-hardware-smoke-template.md`.
- Gate behind `FIRECLAW_RUN_ROS2_SMOKE=1`.
- Require a safety observer for hardware tests.

## Config Schema

The ROS2 adapter reuses the same YAML config schema as ROS1, with the following
extensions:

```yaml
robot_id: "fireclaw-robot-01"
namespace: "/fireclaw/robot_01"
adapter_type: "ros2"               # selects Ros2Adapter
transport:
  enabled: true
  wait_for_server_seconds: 10.0
  wait_for_result_seconds: 60.0
endpoints:
  navigate_to_point:
    interface: action
    name: "/navigate_to_pose"
    type: "nav2_msgs/NavigateToPose"
    cancel_supported: true
    feedback_supported: true
  search_for_victims:
    interface: action
    name: "/fireclaw/robot_01/search"
    type: "fireclaw_msgs/SearchAction"
    cancel_supported: true
    feedback_supported: true
emergency_stop:
  interface: service
  name: "/fireclaw/robot_01/emergency_stop"
  type: "std_srvs/Trigger"
```

The `adapter_type: "ros2"` field is the only new required key.  All other fields
follow the existing ROS1 config schema.

## File Layout

```
src/fireclaw_core/
  ros2_adapter.py        # Ros2Adapter (RobotAdapter protocol)
  ros2_transport.py      # dict <-> ROS2 msg, pub/sub/service/action
  ros2_node_manager.py   # rclpy lifecycle wrapper

tests/
  test_ros2_adapter.py   # unit tests (mocked rclpy)
  test_ros2_transport.py # dict conversion tests
  test_ros2_smoke.py     # integration smoke (gated)

docs/deployment/
  ros2-adapter-plan.md   # this file
  ros2-smoke-test-template.md  # hardware smoke checklist
```

## Migration Path

1. Implement Phases 1-5 in a feature branch.
2. Run unit tests (no ROS2 required).
3. Install ROS2 Humble, run integration tests.
4. Create smoke test template, test on simulator.
5. Test on real hardware with safety observer.
6. Merge after all phases pass.
