# ROS1 Adapter v1 Design

## Goal

Add the first ROS1-facing adapter boundary for FireClaw without importing `rospy` or binding to deployment-specific topic/service/action names.

This phase creates the ROS1 adapter slot and a mock ROS1 backend that records ROS1 command specifications. Real ROS1 transport remains future work.

## Why This Comes Next

The agreed framework order is:

```text
Robot Integration Boundary v1
-> Task/Action State Model v1
-> Operator/Safety Control Plane v2
-> ROS1 Adapter v1
```

FireClaw now has task/action lifecycle state and operator/control audit events. The next large framework gap is the robot integration backend for the user's actual ROS1 environment.

## Architecture

Keep ROS1 outside FireClaw core:

```text
FireClaw core
  -> RobotAdapter protocol
    -> MockRos1RobotAdapter
    -> future RealRos1RobotAdapter
        -> rospy / actionlib / topics / services
```

V1 should introduce:

- `Ros1CommandSpec`
- `MockRos1RobotAdapter`
- `--adapter mock-ros1`
- compatibility alias for existing `mock-ros2`

## Command Spec

`Ros1CommandSpec` records how a FireClaw action would map to ROS1:

- `interface`
- `name`
- `action`
- `payload`
- `cancel_supported`
- `feedback_supported`

Initial interface values:

- `topic`
- `service`
- `actionlib`

V1 should use mock topic-like specs for all current rescue actions.

## Scope Exclusions

This version does not implement:

- `rospy`;
- `actionlib`;
- live ROS master connection;
- real topic/service names;
- ROS message serialization;
- ROS feedback callbacks;
- real cancellation over ROS.

Those details should be added after the adapter slot is stable and the user's actual robot interfaces are known.
