# Durable Task Queue v1 Design

## Goal

Add a durable task queue/registry for FireClaw Gateway so accepted robot tasks have persistent lifecycle state across process boundaries.

## Scope

This version adds:

- a JSONL-backed task queue store;
- task records for accepted, running, terminal, denied, and lost tasks;
- optional `dedupe_key` handling for idempotent task submission;
- Gateway startup reconciliation that marks non-terminal records as `lost`;
- Gateway state/task trace integration with persisted queue status.

This version does not add:

- automatic robot action replay after restart;
- multi-robot scheduling;
- priority queues;
- distributed locking;
- cross-process concurrent writers.

## OpenClaw Analogue

OpenClaw's `task-registry.ts` keeps in-flight task records, terminal statuses, runtime-specific cancellation hooks, and test runtime overrides. FireClaw reuses the registry/control-plane shape, but adapts it for rescue robotics:

```text
OpenClaw task registry -> FireClaw durable robot task queue
OpenClaw cancel runtime -> FireClaw Gateway cancel event + ROS action cancel path
OpenClaw lost/terminal states -> FireClaw restart-safe task audit states
```

FireClaw does not resume physical robot actions after restart in v1. A restarted process cannot safely infer whether the robot completed, stopped, or kept moving; the fail-safe behavior is to mark previous non-terminal records as `lost` and require operator review.

## Data Model

`TaskQueueRecord` stores:

- `task_id`
- `session_id`
- `command`
- `status`
- `created_at`
- `started_at`
- `ended_at`
- `dedupe_key`
- `error`
- `result`

Terminal statuses are:

```text
completed, cancelled, failed, denied, lost
```

Non-terminal statuses are:

```text
accepted, running, cancel_requested
```

## Storage

`JsonlTaskQueue` appends immutable JSON lines to a queue log. Reads compact records by `task_id`, keeping the newest update for each field. This matches the existing local-first FireClaw `EventLedger` style and keeps every state transition auditable.

V1 uses a process-local lock in Gateway around writes. It does not claim multi-process writer safety.

## Gateway Flow

Submission:

```text
POST /tasks
-> parse command/session/operator/dedupe_key
-> if non-terminal record with same dedupe_key exists, return existing task
-> create queue record status=accepted
-> enforce active task capacity
-> record denied if control policy blocks submission
-> mark running when worker starts
-> mark terminal when task.completed/task.cancelled/task.failed is recorded
```

Cancellation:

```text
POST /tasks/<id>/cancel
-> existing in-memory TaskControl sets cancel_event
-> queue status becomes cancel_requested
-> final executor result later becomes cancelled/completed/failed
```

Startup:

```text
FireClawGateway.__init__
-> load durable queue
-> mark non-terminal records from previous process as lost
-> append task.lost events for audit
```

## API

`POST /tasks` accepts optional:

```json
{
  "command": "去二楼救人",
  "session_id": "default",
  "dedupe_key": "operator-retry-001"
}
```

If a non-terminal task already exists for the same `dedupe_key`, Gateway returns:

```json
{
  "status": "duplicate",
  "task_id": "task-...",
  "session_id": "default",
  "dedupe_key": "operator-retry-001"
}
```

`GET /state` includes a queue summary.

`GET /tasks/<id>` keeps returning event trace and projected state, with an added `queue_record` field when available.

## Testing

Tests cover:

- JSONL queue creation, update, terminal filtering, and dedupe lookup;
- Gateway writes accepted/running/terminal queue states;
- duplicate `dedupe_key` returns the existing non-terminal task without starting another thread;
- Gateway startup marks stale non-terminal records as `lost`;
- cancel requests update queue status to `cancel_requested`.

## Research Impact

Durable task queue v1 strengthens FireClaw's robotics safety argument by separating task acceptance from volatile process memory. It does not solve autonomous recovery after crashes; instead, it chooses an auditable fail-safe behavior that avoids replaying physical actions without fresh operator context.
