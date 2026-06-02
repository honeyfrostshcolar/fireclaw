# Gateway Async Task Runner v1 Implementation Plan

**Goal:** Make HTTP Gateway task submission return `accepted + task_id` immediately while the agent continues in a background worker.

**Architecture:** Keep `FireClawGateway.run_agent(...)` synchronous for local callers. Add `submit_agent(...)` for async task submission and route HTTP `POST /tasks`, `/confirm`, and `/cancel` through it.

**Tech Stack:** Python 3.11, stdlib `threading`, stdlib HTTP server, pytest.

---

### Task 1: Async HTTP Contract

- [x] **Step 1: Write failing Gateway tests**

Update HTTP Gateway tests to expect `POST /tasks` and confirmation requests to return `status="accepted"` with `task_id`, then poll `gateway.task_trace(task_id)` until final result appears.

- [x] **Step 2: Verify RED**

Run `.venv/bin/python -m pytest tests/test_gateway.py -q`.

Observed failure: current HTTP responses were still synchronous (`succeeded` / `awaiting_confirmation`) instead of `accepted`.

### Task 2: Background Task Runner

- [x] **Step 1: Add `submit_agent(...)`**

Create task id, append `task.received`, start a daemon worker thread, and immediately return an accepted response.

- [x] **Step 2: Share execution path**

Extract internal `_execute_agent_task(...)` so `run_agent(...)` and `submit_agent(...)` share planner, safety, execution, memory, and event recording logic.

- [x] **Step 3: Handle escaped worker exceptions**

Append `task.failed` with a minimal failed result when a worker exception escapes.

- [x] **Step 4: Keep shutdown bounded**

Track active task threads and join them briefly in `stop()`.

### Task 3: HTTP and Operator Console

- [x] **Step 1: Route HTTP POST endpoints through async submission**

Return HTTP `202 Accepted` from `/tasks`, `/confirm`, and `/cancel`.

- [x] **Step 2: Keep synchronous Python API available**

Add a test proving `gateway.run_agent(...)` still returns a completed result.

- [x] **Step 3: Update operator console**

Use `gateway.submit_agent(...)`, poll by immediate `task_id`, and return after final task result appears.

### Task 4: Documentation and Verification

- [x] **Step 1: Update README**

Document HTTP `202 Accepted`, accepted response shape, polling behavior, and the distinction between `run_agent(...)` and `submit_agent(...)`.

- [x] **Step 2: Add design and plan docs**

Record OpenClaw reference points and FireClaw adaptation.

- [x] **Step 3: Record memory**

Append session update with commands, files modified, verification, and remaining gaps.

- [x] **Step 4: Run full verification**

Run `.venv/bin/python -m pytest -q`.

---

## Self-Review

- Scope is limited to task acceptance/background execution and does not add SSE/WebSocket or durable queues.
- Existing structured EventLedger remains the progress source of truth.
- Synchronous in-process API remains available for local tools.
