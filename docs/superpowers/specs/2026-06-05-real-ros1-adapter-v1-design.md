# Real ROS1 Adapter v1 Design

## Goal

Complete the first real ROS1 adapter slice by adding template rendering and optional ROS1 topic/service/action transport.

## Scope

This version adds:

- template rendering from FireClaw skill inputs and `targets` into ROS-shaped payload dictionaries;
- `transport.enabled` config switch;
- optional `rospy` and `actionlib` transport code paths;
- fake transport tests for service, topic, and action execution;
- conservative fallback when ROS dependencies are unavailable.

This version does not add:

- live robot validation in this environment;
- full ROS message introspection;
- all possible ROS message constructors;
- persistent action client registry;
- multi-robot routing.

## Config

```yaml
transport:
  enabled: true
  wait_for_server_seconds: 5.0
  wait_for_result_seconds: 30.0
```

Templates may reference inputs:

```yaml
goal_template:
  target_pose:
    pose:
      position:
        x: "{{ targets.floor_${floor}.x }}"
        y: "{{ targets.floor_${floor}.y }}"
```

Input `{"floor": 2}` resolves `floor_${floor}` to `floor_2`.

## Runtime

`Ros1RobotAdapter` behavior:

- no endpoint -> `not_configured`;
- transport disabled -> `not_configured`;
- transport enabled but ROS unavailable -> `failed`;
- transport enabled and fake/real transport succeeds -> `succeeded`;
- transport exception -> `failed`.

Transport interface:

- `topic`: publish resolved payload;
- `service`: call service with resolved payload;
- `action`: send goal, wait for result, return result payload;
- `cancel`: action cancellation remains supported through endpoint metadata, but persistent client cancellation is left for the next slice.

## Safety Note

The adapter still requires Gateway safety, authorization, and emergency stop layers. Enabling ROS transport should only happen with a robot-specific config reviewed by a human operator.
