# Gateway Task Registry / Backpressure v1 Design

## Goal

Prevent a robot-side Gateway from silently accepting multiple concurrent execution tasks. FireClaw should expose active task state and return a clear busy response when the robot has no execution capacity.

## OpenClaw Reference

The first CodeGraph query for `resolveGatewayInflightMap` timed out after 120 seconds, so implementation used local OpenClaw source inspection plus earlier CodeGraph findings.

Relevant OpenClaw patterns:

- Gateway keeps active run state in maps such as `chatAbortControllers`.
- Repeated or active chat sends can return `status: "in_flight"` with the active `runId`.
- Gateway `dedupe` state protects active and terminal run snapshots.
- Maintenance code avoids evicting active run dedupe entries.

FireClaw adapts this from chat/run dedupe to robot execution capacity:

```text
active task controls + robot capacity -> accepted or busy
```

## Chosen Approach

Add a simple in-process task registry and capacity model to `FireClawGateway`.

Default:

- `max_active_execution_tasks = 1`

This matches the current single-robot Gateway model. One Gateway process represents one robot adapter, so only one execution task should command that robot at a time unless explicitly configured otherwise.

## Behavior

`POST /tasks`:

- If `active_execution_tasks < max_active_execution_tasks`, accept the task as before.
- If capacity is full, return HTTP `409 Conflict` with `status="busy"`.

Busy response:

```json
{
  "status": "busy",
  "message": "机器人当前已有任务在执行，请等待当前任务结束或取消后再提交。",
  "active_task_id": "task-...",
  "active_tasks": [],
  "capacity": {
    "active_execution_tasks": 1,
    "max_active_execution_tasks": 1,
    "available_execution_slots": 0
  }
}
```

`GET /state` now includes:

- `task_capacity`
- `active_tasks`

## Implementation Notes

`TaskControl` now records:

- `task_id`
- `session_id`
- `command`
- `started_at`
- `cancel_event`

Gateway registers a task control before starting the worker thread and removes it when the worker exits. Capacity checks happen under the same task lock used by active task registration.

## Non-Goals

- No durable task queue.
- No priority scheduler.
- No fleet-level routing.
- No automatic retry when the robot becomes available.
- No operator authorization yet.

## Safety Rationale

This is a control-plane safety boundary. Firefighting robot tasks may include navigation, victim search, manipulation, radio reporting, and future ROS2 actions. Accepting multiple simultaneous execution tasks for one robot would make action ownership ambiguous. Returning `busy` is safer than queueing or parallelizing by default.
