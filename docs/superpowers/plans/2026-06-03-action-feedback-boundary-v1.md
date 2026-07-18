# Action Feedback Boundary v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a FireClaw action feedback event path that current mock runs and future ROS1 actionlib callbacks can use.

**Architecture:** Extend `RobotActionBackend.execute(...)` with an optional `feedback_sink`. `RobotActionRuntime` enriches feedback payloads and emits `action.feedback`; mock ROS1 backend produces deterministic progress through the existing robot adapter wrapper.

**Tech Stack:** Python 3.11 protocols/callables, pytest, existing EventLedger and task state projection.

---

### Task 1: Runtime Feedback Sink

**Files:**
- Modify: `src/fireclaw_core/action_runtime.py`
- Modify: `tests/test_action_runtime.py`

- [x] **Step 1: Write failing test**

Add a backend that calls `feedback_sink` twice and assert runtime emits `action.feedback` events before `action.succeeded`.

- [x] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_action_runtime.py::test_robot_action_runtime_emits_backend_feedback_events -q
```

Expected: fail because `RobotActionBackend.execute(...)` does not accept `feedback_sink`.

- [x] **Step 3: Implement minimal runtime feedback sink**

Update protocol, wrapper, and runtime.

- [x] **Step 4: Verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_action_runtime.py -q
```

Expected: pass.

### Task 2: Mock ROS1 Feedback Demo

**Files:**
- Modify: `src/fireclaw_core/robot.py`
- Modify: `src/fireclaw_core/demo.py`
- Modify: `tests/test_demo.py`

- [x] **Step 1: Write failing test**

Assert `run_rescue_demo(...)` exposes `action.feedback` and projected `feedback_count`.

- [x] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_demo.py::test_run_rescue_demo_returns_gateway_trace_with_mock_ros1_action_feedback -q
```

Expected: fail because mock runs do not emit feedback yet.

- [x] **Step 3: Implement deterministic mock ROS1 feedback**

Add mock feedback metadata and expose feedback fields in compact action events.

- [x] **Step 4: Verify focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_action_runtime.py tests/test_demo.py tests/test_task_state.py -q
```

Expected: pass.

### Task 3: Docs, Memory, Full Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-03/fireclaw-dry-run-core.md`

- [x] **Step 1: Document feedback boundary**
- [x] **Step 2: Update memory**
- [x] **Step 3: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full suite passes.

