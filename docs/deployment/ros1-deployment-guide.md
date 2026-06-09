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

### Dict Payload Conversion

`Ros1Transport.execute()` accepts dict payloads and automatically converts them to ROS message objects. This means you do **not** need to import ROS message classes (e.g. `geometry_msgs.msg.Twist`) or construct messages manually — just pass a plain dict whose keys match the ROS message field names.

**Topic example (Twist):**
```python
result = transport.execute(
    endpoint,
    {"linear": {"x": 2.0, "y": 0.0, "z": 0.0}, "angular": {"x": 0.0, "y": 0.0, "z": 0.0}},
    config,
)
```

**Action example (FibonacciGoal):**
```python
result = transport.execute(
    endpoint,
    {"order": 5},
    config,
)
```

**Service example (TriggerRequest):**
```python
result = transport.execute(
    endpoint,
    {"reason": "operator stop"},
    config,
)
```

**When to use dicts vs real message objects:**

| Scenario | Recommended | Reason |
|----------|-------------|--------|
| Normal skill/agent code | Dict payload | Simpler, no ROS imports needed |
| Cancellation workflows | Real message + direct client | `transport.execute()` is blocking; cancellation needs client reference |
| Custom feedback handling | Dict payload | Feedback sink handles conversion automatically |

The transport checks for `module.resolve_message_class()`, `module.resolve_service_request_class()`, and `module.resolve_action_goal_class()` to convert dicts. If the module does not support resolution, the payload is passed through as-is (useful for pre-constructed ROS message objects).

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
- Nested dicts supported
- See `examples/ros1_configs/` for working examples

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
# Default unit suite skips ROS1 smoke tests.
.venv/bin/python -m pytest -q

# Explicit ROS1 smoke proof.
# Requires ROS1 commands on PATH: roscore, rosrun, turtlesim, actionlib_tutorials.
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py -q

# Run specific smoke test
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py::test_ros1_action_fibonacci_goal -q
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
