# Task/Action State Model v1 Design

## Goal

Define FireClaw's first explicit state model for tasks, skill steps, and robot actions.

This is not an action feedback implementation. It is the framework layer that explains how existing EventLedger events become a coherent task/action state summary for Gateway clients, operator tools, memory, and later ROS1 adapters.

## Why This Comes Next

FireClaw now emits three kinds of execution events:

```text
task events  -> task.received, task.completed, task.cancelled, ...
skill events -> skill.started, skill.attempted, skill.succeeded, ...
action events -> action.requested, action.started, action.succeeded, ...
```

The raw event list is useful for audit, but it is too low-level for control-plane decisions. A client should be able to ask:

- is this task still running?
- which skill is active?
- which robot action is active?
- what actions did a skill produce?
- did cancellation affect the task, skill, or action?
- what is the terminal state?

Task/Action State Model v1 answers those questions by projecting append-only events into a structured state snapshot.

## OpenClaw Analogue

OpenClaw keeps run-level context and emits sequenced events for lifecycle, tools, plans, approvals, and command output. Gateway and TUI surfaces then project those events into current run state and human-facing UI.

Relevant OpenClaw shape:

```text
runId -> sequenced events -> projected run/tool state -> final lifecycle state
```

FireClaw adaptation:

```text
task_id -> task/skill/action events -> projected task state -> final task/action state
```

## Current FireClaw Inputs

The projection should consume only existing event records from `EventLedger.events_for_task(task_id)`.

Each event has:

- `event_id`
- `task_id`
- `session_id`
- `type`
- `timestamp`
- `payload`

The projection should not mutate events and should not require database indexes in v1.

## State Objects

### Task State

One task-level summary:

- `task_id`
- `session_id`
- `status`
- `command`
- `started_at`
- `ended_at`
- `active_skill_name`
- `active_action_id`
- `skill_count`
- `action_count`
- `result`

Allowed task statuses for v1:

- `unknown`
- `received`
- `planned`
- `running`
- `awaiting_confirmation`
- `cancel_requested`
- `cancelled`
- `succeeded`
- `failed`
- `blocked`
- `clarify`

Terminal task events map as:

- `task.completed` with result status `succeeded` -> `succeeded`
- `task.completed` with result status `block` -> `blocked`
- `task.completed` with result status `clarify` -> `clarify`
- `task.completed` with result status `failed` -> `failed`
- `task.cancelled` -> `cancelled`
- `task.failed` -> `failed`

### Skill State

One state object per skill execution occurrence, not merely per skill name. Repeated skills should become separate records.

Fields:

- `skill_run_id`
- `skill_name`
- `status`
- `inputs`
- `started_at`
- `ended_at`
- `attempt_count`
- `last_error`
- `action_ids`

Allowed skill statuses for v1:

- `started`
- `running`
- `succeeded`
- `failed`
- `cancelled`

Because existing skill events do not carry a skill-run id, v1 can derive stable local ids from event order:

```text
skill-1, skill-2, ...
```

### Action State

One state object per robot action.

Fields:

- `action_id`
- `task_id`
- `skill_run_id`
- `skill_name`
- `action_type`
- `status`
- `inputs`
- `requested_at`
- `started_at`
- `ended_at`
- `risk_level`
- `dry_run`
- `timeout_seconds`
- `feedback_count`
- `last_feedback`
- `output`
- `error`

Allowed action statuses for v1:

- `requested`
- `started`
- `running`
- `feedback`
- `cancel_requested`
- `cancelled`
- `succeeded`
- `failed`
- `timeout`

`action.feedback` is included in the model even though current backends do not emit it yet. The projection should support it once future backends produce it.

## Projection Rules

Projection should be deterministic and event-order based.

Rules:

1. Start with `task.status="unknown"`.
2. `task.received` sets task status to `received`, command, and started timestamp.
3. `task.planned` sets status to `planned` unless a later running/terminal status exists.
4. `safety.decided` with `require_confirmation`, `block`, or `clarify` can update task status before terminal result.
5. `skill.started` creates a new skill state and sets task status to `running`.
6. `action.requested` creates or updates an action state, associates it with the current skill run when possible, and sets active action.
7. `action.started` sets action status to `running`.
8. `action.feedback` increments `feedback_count` and stores `last_feedback`.
9. Terminal action events set action status and ended timestamp.
10. `skill.attempted` increments attempt count and captures failed attempt errors.
11. Terminal skill events set skill status and ended timestamp.
12. `task.cancel_requested` sets task status to `cancel_requested` unless already terminal.
13. Terminal task events set final task status, ended timestamp, and result.

If events are malformed or missing fields, projection should keep best-effort state rather than raising.

## Gateway Integration

`FireClawGateway.task_trace(task_id)` should eventually return:

```python
{
    "task_id": task_id,
    "events": [...],
    "result": {...} | None,
    "status": "running" | "succeeded" | ...,
    "state": {
        "task": {...},
        "skills": [...],
        "actions": [...],
    },
}
```

This keeps existing clients compatible while adding a structured state view.

## Memory Integration

Memory should continue storing the final agent result for now. V1 does not need to write the projected state into JSONL memory.

Later phases can persist task/action summaries if they become useful for retrieval, experiment evaluation, or incident replay.

## Operator Projection

Operator console can keep using raw events for now. Once the state model is stable, it can use projected state for richer summaries such as:

- current active action;
- last feedback;
- cancellation in progress;
- final action outcome.

## Testing Strategy

Add focused tests for a pure projection module before Gateway integration:

- empty events produce `unknown`;
- successful rescue event sequence produces task `succeeded`, five skills, five actions;
- cancel-requested sequence produces task `cancel_requested` while still active;
- final cancelled sequence produces task `cancelled`;
- action feedback updates `feedback_count` and `last_feedback`;
- malformed events do not crash projection.

Then add Gateway coverage that `task_trace(task_id)` includes `state`.

## Research Impact

This state model is important for making FireClaw auditable and evaluable.

It enables:

- clearer robot execution traces;
- action-level success/failure/cancellation metrics;
- incident replay;
- later ROS1 feedback mapping;
- stronger experimental logging for embodied-agent research.

Without this projection layer, FireClaw has logs but no stable control-plane state model. That weakens both engineering clarity and research claims.

## Scope Exclusions

This design does not implement:

- action feedback emission from simulator or ROS1;
- real ROS1 bindings;
- persistent indexed event store;
- fleet-level state;
- UI rendering changes;
- learned policy state estimation.
