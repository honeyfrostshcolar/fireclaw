# ROS2 / Simulator Adapter v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add structured robot/environment state and a deterministic simulator adapter while keeping real ROS2 out of scope.

**Architecture:** Extend `RobotAdapter` with state snapshot methods, add `SimulatorRobotAdapter`, let `SafetyGate` consume state snapshots, and add CLI adapter selection.

**Tech Stack:** Python 3.11, pytest.

---

### Task 1: Adapter State Contract and Simulator

**Files:**
- Modify: `src/fireclaw_core/robot.py`
- Test: `tests/test_robot.py`

- [x] **Step 1: Write failing robot state tests**

Add tests for `get_robot_state()` and `get_environment_state()` on existing adapters.

- [x] **Step 2: Write failing simulator tests**

Add tests for simulator navigation, victim search, unreachable floors, and state updates.

- [x] **Step 3: Run robot tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot.py -v
```

- [x] **Step 4: Implement state contract and simulator adapter**

Add `RobotState`, `EnvironmentState`, adapter state methods, and `SimulatorRobotAdapter`.

- [x] **Step 5: Run robot tests and verify GREEN**

Run robot tests again.

### Task 2: Safety Uses Robot and Environment State

**Files:**
- Modify: `src/fireclaw_core/safety.py`
- Test: `tests/test_safety.py`

- [x] **Step 1: Write failing safety state tests**

Add tests for offline robot, low battery, and unreachable target floor.

- [x] **Step 2: Run safety tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_safety.py -v
```

- [x] **Step 3: Implement safety state checks**

Add optional `robot_state` and `environment_state` inputs to `SafetyGate.evaluate`.

- [x] **Step 4: Run safety tests and verify GREEN**

Run safety tests again.

### Task 3: Agent State Snapshots and CLI Adapter Selection

**Files:**
- Modify: `src/fireclaw_core/agent.py`
- Modify: `src/fireclaw_core/__main__.py`
- Tests: `tests/test_agent.py`, `tests/test_cli.py`

- [x] **Step 1: Write failing agent snapshot test**

Assert normal task results and memory records include `robot_state` and `environment_state`.

- [x] **Step 2: Write failing CLI adapter test**

Run CLI with `--adapter simulator` and assert execution mode/state mode are `simulator`.

- [x] **Step 3: Run agent and CLI tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py tests/test_cli.py -v
```

- [x] **Step 4: Implement state snapshot plumbing and CLI factory**

Pass state snapshots into safety, include them in result/memory, and add adapter selection.

- [x] **Step 5: Run agent and CLI tests and verify GREEN**

Run the same targeted tests.

### Task 4: Docs, Verification, Memory

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-02/fireclaw-dry-run-core.md`

- [x] **Step 1: Document simulator adapter**

Add README usage examples and adapter boundary notes.

- [x] **Step 2: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -v
```

- [x] **Step 3: Run manual simulator CLI demo**

Run:

```bash
.venv/bin/python -m fireclaw_core "去二楼救人" --adapter simulator --memory-path /tmp/fireclaw-sim-memory.jsonl
```

- [x] **Step 4: Update memory and mark plan complete**

Append implementation notes and mark all checkboxes complete.
