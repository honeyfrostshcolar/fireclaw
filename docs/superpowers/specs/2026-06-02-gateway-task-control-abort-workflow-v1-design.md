# Gateway Task Control / Abort Workflow v1 Design

## Goal

Add first-class task cancellation to FireClaw Gateway so an operator or external system can request that an active background task stop before executing more robot skills.

## OpenClaw Reference

OpenClaw uses active run tracking and abort controllers:

- `src/gateway/chat-abort.ts` defines `ChatAbortControllerEntry` with `controller`, `sessionId`, `sessionKey`, timestamps, owner metadata, and run kind.
- `registerChatAbortController(...)` registers active runs by `runId` and returns cleanup logic.
- `abortChatRunById(...)` looks up the active run, triggers abort, broadcasts an `aborted` final state, and cleans run state.
- `chat.abort` in `src/gateway/server-methods/chat.ts` can abort by `runId` or session.

FireClaw adapts this pattern to robotics:

```text
task_id -> active TaskControl -> cancel_event -> cooperative executor stop -> task.cancelled
```

## Chosen Approach

Use cooperative cancellation, not hard termination.

When `POST /tasks/<task_id>/cancel` is called:

- Gateway finds the active task control by `task_id`.
- Gateway sets a cancellation flag.
- Gateway appends `task.cancel_requested`.
- The currently running skill is allowed to return.
- `PlanExecutor` checks the cancellation flag before starting each step and after each step completes.
- If cancellation is requested, execution returns `status="cancelled"` and Gateway appends `task.cancelled`.

This is intentionally conservative for firefighting robots. Killing a Python thread, subprocess, ROS action, or CUDA policy mid-call can leave hardware or state machines in unsafe states. Later adapters can implement stronger cancellation at their own boundary.

## Interfaces

HTTP:

- `POST /tasks/<task_id>/cancel`

Response for an active task:

```json
{
  "status": "cancel_requested",
  "task_id": "task-...",
  "session_id": "operator-a",
  "message": "已请求取消任务，当前 skill 返回后将停止后续步骤。"
}
```

Response when the task already has a final result:

```json
{
  "status": "completed",
  "task_id": "task-...",
  "message": "任务已经结束，无法取消。"
}
```

Internal:

- `PlanExecutor(..., cancellation_requested=callable)`
- `FireClawAgent(..., cancellation_requested=callable)`
- `FireClawGateway.cancel_task(task_id)`

## Events

New event:

- `task.cancel_requested`

Existing final event reused:

- `task.cancelled`

`task_trace(task_id)` now reports task status as:

- final result status when complete;
- `cancel_requested` after cancellation is requested but before final cancellation;
- `running` while active;
- `unknown` if no active state or final result exists.

## Non-Goals

- No forced thread termination.
- No subprocess termination yet.
- No ROS2 action cancel yet.
- No fleet-level task rerouting.
- No authorization or operator identity binding yet.

## Safety Rationale

Firefighting robot cancellation must be fail-safe. This v1 avoids interrupting a skill inside unknown hardware or algorithm code. It only prevents the next skill from starting. For real robots, skill wrappers should later expose safe cancellation points, emergency stop behavior, and ROS2 action cancellation where the underlying controller supports it.
