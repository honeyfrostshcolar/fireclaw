# Gateway Async Task Runner v1 Design

## Goal

Change FireClaw Gateway's HTTP control plane from blocking task execution to OpenClaw-like accepted/background runs. External systems should submit a task, receive a stable `task_id` immediately, and then observe progress through the existing EventLedger endpoints.

## OpenClaw Reference

CodeGraph inspection of `openclaw-main` showed the relevant pattern:

- `chat.send` accepts a structured request and returns a `runId`.
- `registerChatAbortController(...)` tracks active chat runs by `runId`.
- `broadcastChatFinal(...)` emits a final structured chat event and clears run sequence state.
- TUI/chat components project structured run/tool events into human-facing state.

FireClaw mirrors this shape at a smaller Python scale:

```text
POST /tasks -> accepted task_id -> background FireClawAgent run -> EventLedger progress/final events
```

## Chosen Approach

Keep synchronous `FireClawGateway.run_agent(...)` for in-process callers, tests, and local tools. Add `FireClawGateway.submit_agent(...)` for asynchronous task submission.

HTTP endpoints use the asynchronous path:

- `POST /tasks`
- `POST /confirm`
- `POST /cancel`

Each endpoint returns HTTP `202 Accepted` with:

```json
{
  "status": "accepted",
  "task_id": "task-...",
  "session_id": "operator-a",
  "message": "任务已接收，正在后台执行。"
}
```

## Runtime Model

`submit_agent(...)` creates a `task_id`, records `task.received`, starts a daemon worker thread, and returns immediately.

The worker calls the same internal execution path as `run_agent(...)`, so planner, safety gate, memory, skill execution, confirmation, and result-event recording stay shared. If an unexpected exception escapes the agent, the worker records `task.failed` with a minimal failed result payload.

Gateway keeps a small in-process map of active task threads so `stop()` can join them during tests and local shutdown.

## Event and Query Behavior

Existing query endpoints remain the source of truth:

- `GET /tasks/<task_id>`
- `GET /tasks/<task_id>/events`
- `GET /events/recent?session_id=<id>&limit=<n>`

Before final completion, `GET /tasks/<task_id>` returns the event trace with `result=null`. After completion, `result` is read from `task.completed`, `task.cancelled`, or `task.failed`.

## Operator Console

`operator_console.py` now uses `submit_agent(...)` directly. It receives `task_id` immediately, polls EventLedger by task id, projects new events into Chinese text, and returns when the task trace contains a final result.

## Non-Goals

- No thread pool or durable queue yet.
- No task persistence beyond EventLedger JSONL records.
- No in-flight cancellation of a running skill yet.
- No SSE/WebSocket transport yet.
- No authentication or operator identity binding yet.

## Testing

Focused tests cover:

- HTTP `POST /tasks` returns `accepted` and a `task_id`.
- Background execution eventually produces a final `succeeded` result in `task_trace`.
- HTTP confirmation uses the same async behavior.
- Synchronous `gateway.run_agent(...)` still returns a completed result.
- Operator console still prints human-readable progress.
