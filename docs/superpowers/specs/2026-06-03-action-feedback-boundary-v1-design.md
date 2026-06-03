# Action Feedback Boundary v1 Design

## Goal

Add a FireClaw-level action feedback path before implementing real ROS1 transport:

```text
robot backend progress -> action.feedback event -> task/action state projection -> Gateway/demo trace
```

## Design

`RobotActionRuntime` remains the single lifecycle boundary for robot actions. It will pass a `feedback_sink` callback into backends. Backends can call the sink with JSON-ready progress payloads while an action is running. The runtime enriches each feedback with action identity, task id, skill name, action type, inputs, dry-run flag, risk level, and timeout metadata before emitting `action.feedback`.

Current mock adapters will emit deterministic feedback through `RobotAdapterActionBackend` so tests and demos prove the trace path. A future real ROS1 backend should map `actionlib` feedback callbacks into the same `feedback_sink`.

## Scope

This does not implement real ROS1 subscriptions, WebSocket/SSE streaming, or operator UI rendering. It only establishes the internal event/state contract that those layers will consume.

## Verification

- Runtime emits `action.feedback` between `action.started` and terminal action events.
- `project_task_state(...)` already tracks `feedback_count` and `last_feedback`.
- E2E demo exposes feedback events and projected feedback state.

