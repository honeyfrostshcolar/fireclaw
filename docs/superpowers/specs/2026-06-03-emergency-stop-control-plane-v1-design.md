# Emergency Stop Control Plane v1 Design

## Goal

Add a framework-level emergency stop path before real ROS1 hardware binding:

```text
operator request -> emergency.stop scope check -> Gateway audit events -> active task cancellation -> robot emergency_stop hook
```

## Scope

This version does not wire a physical e-stop, real ROS1 topic/service/action, authentication, or signed authorization. It establishes the internal control-plane contract that a future ROS1 adapter can map to a real emergency-stop interface.

## Behavior

- `POST /emergency-stop` accepts optional `session_id`, `reason`, and `operator`.
- `ControlPolicy` evaluates `emergency.stop`.
- Authorized requests:
  - set Gateway emergency stop state;
  - call `robot.emergency_stop(reason=...)`;
  - request cancellation for all active tasks;
  - record `emergency_stop.requested` and `emergency_stop.activated`.
- Unauthorized requests:
  - record `emergency_stop.denied`;
  - do not call robot stop;
  - do not cancel active tasks.
- `/state` exposes the emergency stop state.

## Safety Semantics

Emergency stop is stronger than normal task cancellation. Normal cancellation is task-scoped and cooperative. Emergency stop is gateway/robot-scoped, highest priority, and records a separate audit trail.

