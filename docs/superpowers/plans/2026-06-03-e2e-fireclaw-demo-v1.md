# End-to-End FireClaw Demo v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one runnable local demo command that proves FireClaw can execute a rescue command through Gateway, operator/control audit, mock ROS1 action dispatch, and projected task state.

**Architecture:** Create `fireclaw_core.demo.run_rescue_demo(...)` as a small orchestration layer over `FireClawGateway`. Add `--demo rescue` to the module CLI while preserving existing direct-agent command behavior.

**Tech Stack:** Python 3.11, argparse, dataclasses/dicts, pytest, existing FireClaw Gateway and mock ROS1 adapter.

---

### Task 1: Demo Runner

**Files:**
- Create: `src/fireclaw_core/demo.py`
- Create: `tests/test_demo.py`

- [x] **Step 1: Write failing test**

Create `tests/test_demo.py` with a test that calls `run_rescue_demo(...)` and asserts the returned JSON-ready dict includes successful status, `mock_ros1` robot state, operator/control events, action events, and projected state.

- [x] **Step 2: Run test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_demo.py::test_run_rescue_demo_returns_gateway_trace_with_mock_ros1_action_state -q
```

Expected: fail because `fireclaw_core.demo` does not exist.

- [x] **Step 3: Implement demo runner**

Implement `run_rescue_demo(...)` using `FireClawGateway.submit_agent(...)` and `task_trace(...)`.

- [x] **Step 4: Run focused test**

Run:

```bash
.venv/bin/python -m pytest tests/test_demo.py -q
```

Expected: pass.

### Task 2: CLI Demo Entry

**Files:**
- Modify: `src/fireclaw_core/__main__.py`
- Modify: `tests/test_cli.py`

- [x] **Step 1: Write failing CLI test**

Add a test that runs:

```bash
.venv/bin/python -m fireclaw_core --demo rescue --memory-path <tmp> --event-path <tmp> --robot-id demo-ros1
```

and asserts the output contains the same demo evidence.

- [x] **Step 2: Run test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py::test_module_cli_runs_rescue_demo_through_gateway_mock_ros1 -q
```

Expected: fail because `--demo` is not supported.

- [x] **Step 3: Implement CLI flag**

Add optional `--demo rescue` and `--event-path`. Make positional `command` optional only when demo mode is used.

- [x] **Step 4: Run focused CLI test**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py::test_module_cli_runs_rescue_demo_through_gateway_mock_ros1 -q
```

Expected: pass.

### Task 3: Docs, Memory, Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-03/fireclaw-dry-run-core.md`

- [x] **Step 1: Document demo command**

Add one README command showing the end-to-end demo.

- [x] **Step 2: Update memory**

Record files, commands, conclusion, and remaining gaps.

- [x] **Step 3: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full suite passes.

