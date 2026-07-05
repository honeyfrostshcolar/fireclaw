# ROS1 Adapter v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dependency-free mock ROS1 adapter slot so FireClaw can route built-in robot skills through a ROS1-shaped backend without importing `rospy`.

**Architecture:** Add `Ros1CommandSpec` and `MockRos1RobotAdapter` to `robot.py`, expose `mock-ros1` through `runtime_config.py`, keep `mock-ros2` as a backwards-compatible alias, and update docs/tests.

**Tech Stack:** Python 3.11 dataclasses, pytest, existing `RobotAdapter` protocol and CLI/Gateway adapter factory.

---

### Task 1: Mock ROS1 Adapter

**Files:**
- Modify: `src/fireclaw_core/robot.py`
- Modify: `tests/test_robot.py`

- [x] **Step 1: Write failing tests**

Add tests asserting `MockRos1RobotAdapter` records ROS1 command specs, uses `mode="mock_ros1"`, and exposes state without ROS dependency.

- [x] **Step 2: Run tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot.py::test_mock_ros1_robot_adapter_records_ros1_command_specs_without_ros_dependency -q
```

Expected: fail because `MockRos1RobotAdapter` does not exist.

- [x] **Step 3: Implement adapter**

Add `Ros1CommandSpec` and `MockRos1RobotAdapter`.

- [x] **Step 4: Run robot tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot.py -q
```

Expected: robot tests pass.

### Task 2: Adapter Factory And CLI Choice

**Files:**
- Modify: `src/fireclaw_core/runtime_config.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_agent.py`

- [x] **Step 1: Write failing adapter factory/CLI tests**

Assert `create_robot_adapter("mock-ros1", ...)` returns a mock ROS1 adapter and CLI accepts `--adapter mock-ros1`.

- [x] **Step 2: Run tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py::test_module_cli_accepts_mock_ros1_adapter -q
```

Expected: fail because `mock-ros1` is not an adapter choice.

- [x] **Step 3: Update adapter choices**

Add `mock-ros1`; keep `mock-ros2` as alias to `MockRos1RobotAdapter`.

- [x] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot.py tests/test_cli.py tests/test_agent.py -q
```

Expected: focused tests pass.

### Task 3: Docs, Memory, Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-03/fireclaw-dry-run-core.md`

- [x] **Step 1: Update README**

Document `mock-ros1` and clarify `mock-ros2` is legacy alias.

- [x] **Step 2: Update memory**

Record commands, files, results, and remaining gaps.

- [x] **Step 3: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full suite passes.
