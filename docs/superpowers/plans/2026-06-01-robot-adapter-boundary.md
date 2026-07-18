# Robot Adapter Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Formalize the robot adapter boundary so FireClaw can keep using a dry-run robot today while preparing for future ROS2 or real robot implementations without rewriting planner, skills, executor, or memory.

**Architecture:** Define a `RobotAdapter` protocol and structured robot metadata/state. Keep `DryRunRobotAdapter` as the default implementation. Add a `MockRos2RobotAdapter` that implements the same interface without depending on ROS2. Built-in skills should depend on the protocol instead of the concrete dry-run adapter.

**Tech Stack:** Python 3.11, `pytest`, dataclasses, typing protocols.

---

### Task 1: Robot Adapter Protocol and Structured Results

**Files:**
- Modify: `src/fireclaw_core/robot.py`
- Test: `tests/test_robot.py`

- [x] **Step 1: Write failing robot adapter tests**

Add tests for:

- `DryRunRobotAdapter.mode == "dry_run"`;
- action results include `robot_id`, `mode`, `action`, `status`, `dry_run`, `data`, and `timestamp`;
- failed dry-run actions return `status="failed"` and structured `error`.

- [x] **Step 2: Run robot tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot.py -v
```

Expected: fails because structured result fields and `mode` are not complete.

- [x] **Step 3: Implement protocol and structured results**

Implement:

- `RobotAdapter` protocol;
- `RobotActionResult` with fields:
  - `ok`
  - `status`
  - `robot_id`
  - `mode`
  - `action`
  - `dry_run`
  - `data`
  - `timestamp`
  - `error`
- update `DryRunRobotAdapter` to populate the fields.

- [x] **Step 4: Run robot tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot.py -v
```

Expected: all robot tests pass.

### Task 2: Built-In Skills Depend on RobotAdapter Protocol

**Files:**
- Modify: `src/fireclaw_core/skills.py`
- Test: `tests/test_execution.py`

- [x] **Step 1: Write failing protocol compatibility test**

Add a tiny fake adapter implementing the same methods as `RobotAdapter`, then verify `create_default_skill_registry(fake_adapter)` can execute at least `navigate_to_floor`.

- [x] **Step 2: Run execution tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution.py -v
```

Expected: fails if `create_default_skill_registry` is typed or implemented against `DryRunRobotAdapter` only.

- [x] **Step 3: Update skill registry factory**

Change `create_default_skill_registry(robot: RobotAdapter)` to depend on the protocol.

- [x] **Step 4: Run execution tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution.py -v
```

Expected: all execution tests pass.

### Task 3: Mock ROS2 Adapter

**Files:**
- Modify: `src/fireclaw_core/robot.py`
- Test: `tests/test_robot.py`

- [x] **Step 1: Write failing MockRos2 tests**

Add tests for:

- `MockRos2RobotAdapter.mode == "mock_ros2"`;
- it implements the same action methods;
- it records intended ROS2-like commands without importing ROS2;
- action results set `dry_run=True` and `mode="mock_ros2"`.

- [x] **Step 2: Run robot tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot.py -v
```

Expected: fails because `MockRos2RobotAdapter` does not exist.

- [x] **Step 3: Implement MockRos2RobotAdapter**

Implement `MockRos2RobotAdapter` as a non-ROS test double that records `topic`, `action`, and payload-style data. It must not import ROS2 packages.

- [x] **Step 4: Run robot tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot.py -v
```

Expected: all robot tests pass.

### Task 4: Agent Uses RobotAdapter Boundary

**Files:**
- Modify: `src/fireclaw_core/agent.py`
- Test: `tests/test_agent.py`

- [x] **Step 1: Write failing agent adapter test**

Add a test constructing `FireClawAgent(robot=MockRos2RobotAdapter(...))` and running `去二楼救人`. The first version should still be blocked unless `dry_run=True`, but the adapter should work when agent dry-run mode is enabled.

Expected:

- result status is `succeeded`;
- robot mode in execution outputs is `mock_ros2`;
- no ROS2 import is required.

- [x] **Step 2: Run agent tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: fails until the agent type and skill registry accept the protocol.

- [x] **Step 3: Update agent type hints if needed**

Use the `RobotAdapter` protocol for the `robot` parameter. Do not change agent behavior unless required by tests.

- [x] **Step 4: Run agent tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: all agent tests pass.

### Task 5: Documentation and Final Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-01/fireclaw-dry-run-core.md`

- [x] **Step 1: Document robot adapter boundary**

Update README with:

- `RobotAdapter` boundary;
- `DryRunRobotAdapter`;
- `MockRos2RobotAdapter`;
- guidance that future ROS2 adapters should implement the protocol without changing planner/executor.

- [x] **Step 2: Run full test suite**

Run:

```bash
.venv/bin/python -m pytest -v
```

Expected: all tests pass.

- [x] **Step 3: Run CLI demo**

Run:

```bash
.venv/bin/python -m fireclaw_core "去二楼救人" --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Expected: result status is `succeeded` and outputs include structured robot result fields.

- [x] **Step 4: Update memory record and mark plan complete**

Append implemented files, verification commands, test results, and remaining gaps to:

```text
memory/2026-06-01/fireclaw-dry-run-core.md
```

Then mark all plan checkboxes complete.

