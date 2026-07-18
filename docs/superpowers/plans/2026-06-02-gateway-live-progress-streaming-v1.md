# Gateway Live Progress / Streaming v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit and persist skill progress events while Gateway tasks execute.

**Architecture:** Add an optional synchronous event sink to `PlanExecutor`, pass it through `FireClawAgent`, and bind it to `EventLedger` in Gateway. Existing CLI and direct agent calls keep working without an event sink.

**Tech Stack:** Python 3.11, dataclasses, stdlib HTTP server, pytest.

---

### Task 1: Executor Event Sink

**Files:**
- Modify: `src/fireclaw_core/executor.py`
- Test: `tests/test_execution.py`

- [x] **Step 1: Write failing tests for executor live events**

Add tests that construct a `PlanExecutor(registry, event_sink=events.append)` style sink and assert event order and payloads.

- [x] **Step 2: Run focused execution tests and observe failure**

Run: `.venv/bin/python -m pytest tests/test_execution.py -q`

Expected: new tests fail because `PlanExecutor.__init__` does not accept `event_sink`.

- [x] **Step 3: Implement optional executor event sink**

Add:

```python
ExecutionEventSink = Callable[[str, dict[str, Any]], None]
```

Store it on `PlanExecutor`, add a private `_emit(...)` helper that catches callback exceptions, then emit:

- `skill.started` before each skill run loop;
- `skill.attempted` after each skill attempt;
- `skill.succeeded` for successful final step;
- `skill.failed` for missing skill or terminal failure.

- [x] **Step 4: Run focused execution tests**

Run: `.venv/bin/python -m pytest tests/test_execution.py -q`

Expected: execution tests pass.

### Task 2: Agent Event Sink Plumbing

**Files:**
- Modify: `src/fireclaw_core/agent.py`
- Test: `tests/test_agent.py`

- [x] **Step 1: Write failing agent test**

Add a test that creates `FireClawAgent(event_sink=...)`, runs `去二楼救人`, and asserts the sink receives executor skill events.

- [x] **Step 2: Run focused agent test and observe failure**

Run: `.venv/bin/python -m pytest tests/test_agent.py -q`

Expected: new test fails because `FireClawAgent.__init__` does not accept `event_sink`.

- [x] **Step 3: Pass event sink from agent into executor**

Add `event_sink` to `FireClawAgent.__init__`, store no extra state unless needed, and instantiate `PlanExecutor(self.registry, event_sink=event_sink)`.

- [x] **Step 4: Run focused agent tests**

Run: `.venv/bin/python -m pytest tests/test_agent.py -q`

Expected: agent tests pass.

### Task 3: Gateway Live Ledger Binding

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Test: `tests/test_gateway.py`

- [x] **Step 1: Write failing Gateway trace test**

Update `test_gateway_runs_task_and_returns_recent_memory` to expect live events:

- `skill.started`
- `skill.attempted`
- `skill.succeeded`

for each skill.

- [x] **Step 2: Run focused Gateway tests and observe failure**

Run: `.venv/bin/python -m pytest tests/test_gateway.py -q`

Expected: Gateway trace test fails because live events are not yet wired.

- [x] **Step 3: Bind task-scoped event sink in Gateway**

Change `_create_agent(...)` to accept `task_id` and `session_id`. Build an event sink that appends to `self.events`. Pass it into `FireClawAgent`.

Update `_record_result_events(...)` to skip derived `skill.succeeded` / `skill.failed` events when the task already has any `skill.started`, `skill.attempted`, `skill.succeeded`, or `skill.failed` event.

- [x] **Step 4: Run focused Gateway tests**

Run: `.venv/bin/python -m pytest tests/test_gateway.py -q`

Expected: Gateway tests pass.

### Task 4: Documentation, Memory, Full Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-02/fireclaw-dry-run-core.md`
- Modify: `docs/superpowers/plans/2026-06-02-gateway-live-progress-streaming-v1.md`

- [x] **Step 1: Update README**

Document live progress polling and the new event types.

- [x] **Step 2: Mark this plan complete**

Change all task checkboxes in this file to `[x]` after implementation and verification.

- [x] **Step 3: Record memory**

Append a timestamped session update with files modified, commands run, results, and remaining gaps.

- [x] **Step 4: Run full verification**

Run: `.venv/bin/python -m pytest -q`

Expected: all tests pass.

---

## Self-Review

- Spec coverage: event sink, Gateway binding, duplicate prevention, tests, docs, and memory are covered.
- Placeholder scan: no `TBD` or unspecified implementation steps remain.
- Type consistency: `ExecutionEventSink` is consistently a callable receiving `(event_type, payload)`.
