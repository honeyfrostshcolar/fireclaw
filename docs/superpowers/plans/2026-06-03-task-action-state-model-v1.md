# Task Action State Model v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic projection from EventLedger task events into structured task, skill, and robot action state.

**Architecture:** Create a pure `task_state.py` projection module that consumes event dicts and returns dataclass-backed state dictionaries. Then add the projected state to `FireClawGateway.task_trace(task_id)` without removing existing `events`, `result`, or `status`.

**Tech Stack:** Python 3.11 dataclasses, pytest, existing FireClaw EventLedger and Gateway.

---

### Task 1: Pure Task/Skill/Action State Projection

**Files:**
- Create: `src/fireclaw_core/task_state.py`
- Create: `tests/test_task_state.py`

- [x] **Step 1: Write failing projection tests**

Create tests covering empty events, successful task projection, feedback projection, cancellation projection, and malformed event tolerance.

- [x] **Step 2: Run tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_state.py -q
```

Expected: fail because `fireclaw_core.task_state` does not exist.

- [x] **Step 3: Implement minimal projection module**

Create `project_task_state(events)` returning:

```python
{
    "task": {...},
    "skills": [...],
    "actions": [...],
}
```

- [x] **Step 4: Run projection tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_state.py -q
```

Expected: all task state tests pass.

### Task 2: Gateway Trace State Integration

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Modify: `tests/test_gateway.py`

- [x] **Step 1: Write failing Gateway trace assertion**

Update Gateway trace test to assert `task_trace(task_id)["state"]` contains task, skill, and action summaries.

- [x] **Step 2: Run Gateway test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway.py::test_gateway_runs_task_and_returns_recent_memory -q
```

Expected: fail because `state` is not present.

- [x] **Step 3: Add projection to `task_trace`**

Import `project_task_state` and include `state` in the task trace response.

- [x] **Step 4: Run Gateway tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway.py -q
```

Expected: all Gateway tests pass.

### Task 3: Docs, Memory, Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-03/fireclaw-dry-run-core.md`

- [x] **Step 1: Update README**

Document that task traces include a projected `state` field.

- [x] **Step 2: Update memory**

Record files changed, RED/GREEN commands, full verification, and remaining gaps.

- [x] **Step 3: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full suite passes.
