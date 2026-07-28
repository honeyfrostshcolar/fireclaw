# Gateway Task Control / Abort Workflow v1 Implementation Plan

**Goal:** Add cooperative cancellation for active Gateway background tasks.

**Architecture:** Track active task controls in Gateway, expose `POST /tasks/<task_id>/cancel`, propagate a cancellation check through `FireClawAgent` into `PlanExecutor`, and stop execution at skill boundaries.

**Tech Stack:** Python 3.11, stdlib `threading.Event`, stdlib HTTP server, pytest.

---

### Task 1: OpenClaw Reference and RED Tests

- [x] **Step 1: Inspect OpenClaw abort/run control**

Attempted CodeGraph lookups for `registerChatAbortController`; broad and narrow CodeGraph calls timed out. Used previously gathered CodeGraph findings plus local source reads of `openclaw/src/gateway/chat-abort.ts` and `openclaw/src/gateway/server-methods/chat.ts`.

- [x] **Step 2: Write failing Gateway cancellation test**

Add a slow subprocess policy skill, submit a rescue task using it, wait for `skill.started`, call `POST /tasks/<task_id>/cancel`, and assert the task finishes as `cancelled` without starting later rescue skills.

- [x] **Step 3: Verify RED**

Run `.venv/bin/python -m pytest tests/test_gateway.py -q`.

Observed failure: `POST /tasks/<task_id>/cancel` returned HTTP 404.

### Task 2: Cooperative Cancellation Core

- [x] **Step 1: Add executor cancellation check**

Add `CancellationCheck = Callable[[], bool]` and let `PlanExecutor` return `ExecutionResult(status="cancelled")` before a step starts or after a step completes when cancellation is requested.

- [x] **Step 2: Propagate through agent**

Add `cancellation_requested` to `FireClawAgent` and pass it to `PlanExecutor`.

- [x] **Step 3: Return cancellation message**

Return `任务已取消。` for cancelled executions.

### Task 3: Gateway Task Control

- [x] **Step 1: Add active task controls**

Add `TaskControl` with `task_id`, `session_id`, and `threading.Event`.

- [x] **Step 2: Track active tasks**

Register task controls in `submit_agent(...)`, remove them when the worker exits, and keep bounded thread joins in `stop()`.

- [x] **Step 3: Add cancel endpoint**

Implement `POST /tasks/<task_id>/cancel` and `FireClawGateway.cancel_task(...)`.

- [x] **Step 4: Add task status projection**

Expose `running`, `cancel_requested`, final result status, or `unknown` in `task_trace(...)`.

### Task 4: Human Projection, Docs, Verification

- [x] **Step 1: Project cancel request event**

Map `task.cancel_requested` to Chinese operator text.

- [x] **Step 2: Add focused tests**

Run `.venv/bin/python -m pytest tests/test_execution.py tests/test_gateway.py tests/test_operator_projection.py tests/test_operator_console.py -q`.

Observed: 28 passed.

- [x] **Step 3: Update docs**

Update README and add this design/plan.

- [x] **Step 4: Record memory**

Append session update with commands, files, verification, and remaining gaps.

- [x] **Step 5: Run full verification**

Run `.venv/bin/python -m pytest -q`.

---

## Self-Review

- Scope is cooperative cancellation only.
- No hard process/thread termination is introduced.
- The current design is safe for dry-run and conservative for future robot adapters.
