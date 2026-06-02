# Operator Console Projection v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an OpenClaw-inspired human-facing operator projection layer for FireClaw events.

**Architecture:** Keep Gateway/EventLedger structured. Add a deterministic projector that maps events to Chinese messages and a simple terminal runner that prints projected progress while a local Gateway task executes.

**Tech Stack:** Python 3.11, stdlib threading/time, pytest.

---

### Task 1: Operator Event Projector

**Files:**
- Create: `src/fireclaw_core/operator_projection.py`
- Create: `tests/test_operator_projection.py`

- [x] **Step 1: Write failing projector tests**

Test rescue event mapping, safety decisions, confirmation events, failed attempts, and task completion messages.

- [x] **Step 2: Run projector tests and observe failure**

Run: `.venv/bin/python -m pytest tests/test_operator_projection.py -q`

Expected: import fails because the module does not exist.

- [x] **Step 3: Implement `OperatorEventProjector`**

Create a small class with `project(event)` plus helper functions for skill names, floor extraction, and reason joining.

- [x] **Step 4: Run projector tests**

Run: `.venv/bin/python -m pytest tests/test_operator_projection.py -q`

Expected: tests pass.

### Task 2: Operator Console Runner

**Files:**
- Create: `src/fireclaw_core/operator_console.py`
- Create: `tests/test_operator_console.py`

- [x] **Step 1: Write failing console tests**

Test that `run_operator_command(...)` prints Chinese progress for `去二楼救人` and returns the final succeeded result.

- [x] **Step 2: Run console tests and observe failure**

Run: `.venv/bin/python -m pytest tests/test_operator_console.py -q`

Expected: import fails because the module does not exist.

- [x] **Step 3: Implement console runner**

Run `gateway.run_agent(...)` in a thread, poll `gateway.events.latest_events(...)`, project each unseen event, write messages to `out`, join the worker, flush remaining events, and return the result.

- [x] **Step 4: Run console tests**

Run: `.venv/bin/python -m pytest tests/test_operator_console.py -q`

Expected: tests pass.

### Task 3: CLI and Docs

**Files:**
- Modify: `src/fireclaw_core/operator_console.py`
- Modify: `README.md`
- Modify: `memory/2026-06-02/fireclaw-dry-run-core.md`
- Modify: `docs/superpowers/plans/2026-06-02-operator-console-projection-v1.md`

- [x] **Step 1: Add CLI main**

Support:

```bash
.venv/bin/python -m fireclaw_core.operator_console "去二楼救人" --adapter simulator
```

- [x] **Step 2: Update README**

Document the operator console and explain that JSON remains machine-facing while the console is human-facing.

- [x] **Step 3: Mark plan complete**

Mark all implementation checkboxes as `[x]`.

- [x] **Step 4: Record memory**

Append a timestamped update with files modified, tests run, and remaining gaps.

- [x] **Step 5: Run full verification**

Run: `.venv/bin/python -m pytest -q`

Expected: all tests pass.

---

## Self-Review

- Spec coverage: projector, console runner, CLI, docs, memory, and verification are covered.
- Placeholder scan: no placeholder implementation step remains.
- Type consistency: event dictionaries are plain `dict[str, Any]`; projector returns `str | None`.
