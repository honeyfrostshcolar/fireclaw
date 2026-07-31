# Robot terminal outcome propagation

## 2026-07-31 00:51:49 +08

### Task goal

Implement the first two Robot/Mission lifecycle items requested by the user:

1. Unify Robot terminal outcomes as `completed`, `blocked`, `escalated`,
   `failed`, `timed_out`, and `cancelled`; retain `lost` for infrastructure
   failure.
2. Propagate the authoritative Robot terminal outcome from Robot Gateway
   through task trace, Mission Agent, Mission Registry, Scheduler, monitoring,
   and operator projections without converting blocked/escalated work into
   success.

### OpenClaw analogue inspected

- `openclaw/src/agents/agent-run-terminal-outcome.ts`
- `AgentRunTerminalOutcome`
- `buildAgentRunTerminalOutcome`
- `mergeAgentRunTerminalOutcome`

Reused structure: keep a normalized terminal outcome separate from broader
runtime/wait status, preserve sticky cancellation, and avoid inferring success
from a wrapper status. FireClaw adaptation adds embodied outcomes such as
`blocked`, `escalated`, `timed_out`, and infrastructure `lost`.

### Files and behavior implemented

- Added `src/fireclaw_core/task/terminal_outcome.py`.
  - Canonical status and event mappings.
  - Legacy aliases such as `succeeded -> completed`,
    `block/denied -> blocked`, `clarify/awaiting_confirmation -> escalated`,
    and `timeout -> timed_out`.
  - Separate terminal-only and full runtime trace resolvers.
  - Only `task.*` terminal events can terminate a task. A
    `skill.succeeded` event can no longer accidentally complete the task.
- Updated Robot Gateway terminal persistence.
  - `_record_result_events` now emits the exact terminal event, such as
    `task.blocked` or `task.escalated`, and stores the same queue status.
  - Raw Agent result status remains available as `raw_status`.
  - Exception and restart-loss paths include structured terminal outcomes.
  - `task_trace()` reads event/result/queue state in one SQLite transaction.
  - Lock order is `task lock -> runtime transaction`; an intermediate
    implementation used the reverse order and deadlocked, so it was corrected.
- Updated Mission propagation.
  - Mission Agent and Robot client use the shared trace status resolver.
  - Mission Registry normalizes terminal status on write and legacy JSONL read.
  - Mission aggregate status is `escalated` when intervention is required;
    blocked/failed/timed_out/lost produces mission failure.
  - Scheduler recognizes all canonical failure outcomes and has explicit
    `on_escalated` and `on_timed_out` policy fields.
  - Restart reconciliation still recognizes active states such as `running`.
  - When one parallel node invalidates a plan, other active nodes remain
    active, are cancelled/fenced, and are not incorrectly rewritten as failed.
- Updated task state, event aggregation, stream events, incident replay,
  consolidation, interactive output, operator projections, and CLI/demo/eval
  success checks to distinguish internal execution `succeeded` from external
  Robot terminal `completed`.
- Updated the gateway-to-gateway E2E fixture to use floor 1. Its deterministic
  test planner had hard-coded floor 2, conflicting with the current
  single-floor project decision.

### Regression cases added

- Legacy status normalization.
- Matching terminal event selection.
- Stale `task.completed` wrapper with a real `escalated` result.
- Sticky cancellation.
- Active `running` status remains non-terminal.
- `skill.succeeded` does not terminate the task.
- Mission escalation propagates into Mission Registry.
- Stale completed queue/event data cannot override an escalated result.

### Commands and results

- `/home/lpp/miniconda3/envs/py310/bin/python -m compileall -q src`
  - passed
- `git diff --check`
  - passed
- Focused Robot/Gateway/Mission propagation suite
  - `154 passed`
- Final full suite:
  - `/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q`
  - `1993 passed, 7 skipped in 173.27s`

Local HTTP Gateway tests required permission to bind temporary loopback ports.

### Important observed bug fixed

Before this work a Robot Agent result such as `block`, `clarify`, or
`awaiting_confirmation` was wrapped by Robot Gateway as `task.completed` with
queue status `completed`. Mission Agent could therefore report a physically
blocked task as successful. The exact terminal result is now authoritative
across all layers.

### Current conclusion

The requested items 1 and 2 are implemented end to end. Robot terminal state
and Mission terminal projection now share one contract, preserve active state,
and survive legacy persisted records. No commit was created because the user
did not request one in this turn.

### Next recommended step

Implement the remaining Mission coordination lifecycle: aggregate multiple
Robot terminal reports into one operator-facing completion response, including
bounded wait/timeout and explicit cancellation of unfinished sibling tasks
when the mission must stop.
