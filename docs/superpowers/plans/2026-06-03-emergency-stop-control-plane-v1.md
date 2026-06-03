# Emergency Stop Control Plane v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a framework-level emergency stop endpoint and robot hook that can cancel active tasks and produce auditable stop events.

**Architecture:** Reuse `ControlPolicy` with `emergency.stop`, add Gateway emergency-stop state and `POST /emergency-stop`, and add mock `RobotAdapter.emergency_stop(...)` implementations.

**Tech Stack:** Python 3.11 dataclasses/protocols, existing HTTP Gateway, pytest, JSONL EventLedger.

---

### Task 1: Robot Emergency Stop Hook

**Files:**
- Modify: `src/fireclaw_core/robot.py`
- Modify: `tests/test_robot.py`

- [x] **Step 1: Write failing test**

Assert mock ROS1 adapter records emergency-stop state/result.

- [x] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot.py::test_mock_ros1_robot_adapter_records_emergency_stop_without_ros_dependency -q
```

Expected: fail because `emergency_stop` does not exist.

- [x] **Step 3: Implement hook**

Add protocol method and adapter implementations.

- [x] **Step 4: Verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot.py -q
```

Expected: pass.

### Task 2: Gateway Emergency Stop Endpoint

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Modify: `tests/test_gateway.py`

- [x] **Step 1: Write failing tests**

Test admin can trigger emergency stop and operator cannot.

- [x] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway.py::test_gateway_admin_emergency_stop_cancels_active_task_and_records_audit_events -q
```

Expected: fail because `/emergency-stop` does not exist.

- [x] **Step 3: Implement endpoint/state/audit**

Add `emergency_stop(...)`, HTTP route, state exposure, and active-task cancellation.

- [x] **Step 4: Verify focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway.py tests/test_robot.py -q
```

Expected: pass.

### Task 3: Docs, Memory, Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-03/fireclaw-dry-run-core.md`

- [x] **Step 1: Document endpoint and semantics**
- [x] **Step 2: Update memory**
- [x] **Step 3: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full suite passes.

