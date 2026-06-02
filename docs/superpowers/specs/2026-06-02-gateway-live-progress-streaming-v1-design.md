# Gateway Live Progress / Streaming v1 Design

## Goal

Add live task-progress events to FireClaw Gateway so external systems can observe long-running robot work while it is executing, instead of only receiving derived events after `agent.run(...)` finishes.

## Context

Gateway Event Ledger v1 already records append-only task traces with `task_id`, `session_id`, event type, timestamp, and payload. The current limitation is that skill events are derived after the agent returns a final result. That is acceptable for short dry-run tasks, but weak for ROS2 actions, simulator jobs, future UI monitoring, and incident replay. Firefighting robot operators need progress visibility during execution.

## Chosen Approach

Use a synchronous event callback passed from Gateway to `FireClawAgent`, then to `PlanExecutor`.

This keeps the first version simple:

- no WebSocket or SSE transport yet;
- no async execution queue yet;
- no dependency on ROS2 internals;
- no behavior change for CLI or tests that do not pass a callback.

Gateway will keep exposing polling endpoints:

- `GET /tasks/<task_id>/events`
- `GET /events/recent?session_id=<id>&limit=<n>`

Clients can poll these endpoints while a request is still running. A later version can add SSE/WebSocket over the same ledger event stream.

## Event Model

The live callback emits these additional events:

- `skill.started`: emitted immediately before a skill begins execution.
- `skill.attempted`: emitted after each attempt, including failed retry attempts.
- `skill.succeeded`: emitted when a skill completes successfully.
- `skill.failed`: emitted when a skill fails terminally.

Gateway will continue to emit:

- `task.received`
- `task.planned`
- `safety.decided`
- `confirmation.pending`
- `confirmation.confirmed`
- `task.completed`
- `task.cancelled`

To avoid duplicate final skill events, Gateway will only derive `skill.succeeded` / `skill.failed` from the result when no live skill events were already emitted for that task.

## Interfaces

Add a lightweight callback type in the execution layer:

```python
ExecutionEventSink = Callable[[str, dict[str, Any]], None]
```

`PlanExecutor` accepts an optional `event_sink`. It calls:

```python
event_sink("skill.started", payload)
event_sink("skill.attempted", payload)
event_sink("skill.succeeded", payload)
event_sink("skill.failed", payload)
```

`FireClawAgent` accepts an optional `event_sink` and passes it into `PlanExecutor`.

Gateway creates a task-scoped sink that appends to `EventLedger` with the current `task_id` and `session_id`.

## Payload Shape

Skill event payloads include:

- `skill_name`
- `inputs`
- `attempt_number` where applicable
- `max_attempts` where applicable
- `status`
- `output` where available
- `error` where available
- `failure_category` for terminal failures when available
- `operator_action` for terminal failures when available

This keeps enough detail for debugging and replay without forcing the planner or Gateway to know skill internals.

## Error Handling

Event callback failures must not mask robot execution. `PlanExecutor` catches callback exceptions and continues. Gateway ledger append failures are therefore visible only as missing progress events, not as failed robot skills. This is appropriate for v1 because safety-critical control should not depend on observability persistence.

## Testing

Focused tests will cover:

- executor emits `skill.started`, `skill.attempted`, and `skill.succeeded` for successful skills;
- executor emits failed attempts and terminal `skill.failed` for retry exhaustion;
- Gateway task traces contain live skill started/attempted events and avoid duplicate final skill events;
- all existing Gateway and agent tests still pass.

## Non-Goals

- No asynchronous task queue.
- No SSE/WebSocket endpoint.
- No true cancellation of in-flight skills.
- No ROS2 action feedback subscription yet.
- No retention or indexing beyond JSONL scans.

## Research Impact

This moves FireClaw closer to an auditable embodied-agent control plane. For research, it creates a measurable trace of planner decisions, safety decisions, skill attempts, retries, and outcomes. That trace is useful for evaluating reliability, failure recovery, operator trust, and safety workflows. It is still an engineering substrate, not itself a novel research contribution.
