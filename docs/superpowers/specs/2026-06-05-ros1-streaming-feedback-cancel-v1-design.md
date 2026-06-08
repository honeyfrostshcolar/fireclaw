# ROS1 Streaming Feedback & Action Cancellation v1 Design

## Goal

Connect real ROS1 action feedback and cancellation to FireClaw's existing action lifecycle and Gateway cancel flow.

## Scope

This version adds:

- forwarding ROS1 `actionlib` feedback callbacks into `action.feedback` events;
- passing Gateway/PlanExecutor cancellation callbacks down to robot action backends;
- cancelling active ROS1 action clients with `cancel_goal()`;
- fake transport tests for feedback and cancellation.

This version does not add:

- SSE/WebSocket streaming endpoints;
- real ROS master smoke tests;
- multi-action client persistence across process restarts;
- UI approval/cancel screens.

## Flow

```text
actionlib feedback_cb
-> Ros1Transport feedback_sink
-> Ros1RobotAdapter action feedback sink
-> RobotActionRuntime action.feedback
-> task_trace.state feedback_count
```

Cancellation:

```text
POST /tasks/<id>/cancel
-> TaskControl.cancel_event
-> PlanExecutor cancellation_requested
-> RobotActionRuntime
-> RobotAdapterActionBackend
-> Ros1RobotAdapter
-> Ros1Transport.cancel_goal()
```

## Safety

Cancellation is best-effort. FireClaw records the cancellation request and calls the ROS action client if one is active. If ROS cancellation fails or the action server still returns a terminal result, that result remains auditable in the task trace.
