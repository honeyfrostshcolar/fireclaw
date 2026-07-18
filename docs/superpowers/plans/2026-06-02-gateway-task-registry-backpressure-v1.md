# Gateway Task Registry / Backpressure v1 Implementation Plan

**Goal:** Add active task capacity and busy responses to FireClaw Gateway.

**Architecture:** Extend `TaskControl` and Gateway's active task map into a small task registry. Check capacity before accepting new async execution tasks. Expose capacity and active task summaries through `/state`.

---

### Task 1: Reference and RED Tests

- [x] **Step 1: Try CodeGraph reference lookup**

Run a narrow CodeGraph search for OpenClaw `resolveGatewayInflightMap`.

Observed: CodeGraph timed out after 120 seconds. Used local OpenClaw source search/read and prior CodeGraph findings for active run / in-flight / dedupe behavior.

- [x] **Step 2: Write failing busy test**

Add a slow policy skill, submit one active task, then submit a second execution task and expect HTTP `409` with `status="busy"`.

- [x] **Step 3: Write state capacity assertions**

Assert `/state` includes `task_capacity` and `active_tasks` while a task is running.

- [x] **Step 4: Verify RED**

Run `.venv/bin/python -m pytest tests/test_gateway.py -q`.

Observed: second task returned 200/accepted instead of 409/busy.

### Task 2: Capacity and Registry

- [x] **Step 1: Add config capacity**

Add `GatewayConfig.max_active_execution_tasks`, defaulting to `1`.

- [x] **Step 2: Extend `TaskControl`**

Record `command` and `started_at` for active summaries.

- [x] **Step 3: Check capacity under task lock**

Before registering a new task, reject with `busy` when the active task count reaches capacity.

- [x] **Step 4: Return HTTP 409**

Map `submit_agent(...).status == "busy"` to HTTP `409 Conflict`.

### Task 3: State Projection

- [x] **Step 1: Add task capacity helper**

Return active count, max active count, and available slots.

- [x] **Step 2: Add active task summaries**

Return task id, session id, command, started timestamp, and cancel-request flag.

- [x] **Step 3: Include fields in `/state`**

Expose `task_capacity` and `active_tasks`.

### Task 4: Docs, Memory, Verification

- [x] **Step 1: Update README**

Document capacity, active task summaries, and busy response.

- [x] **Step 2: Record memory**

Append session update with commands, files, OpenClaw reference, and remaining gaps.

- [x] **Step 3: Run full verification**

Run `.venv/bin/python -m pytest -q`.

---

## Self-Review

- Scope is limited to immediate backpressure, not durable queueing.
- Default capacity is conservative for a single robot.
- Busy response is explicit and inspectable by external systems.
