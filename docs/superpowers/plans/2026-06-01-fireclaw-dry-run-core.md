# FireClaw Dry-Run Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build the first runnable Python dry-run FireClaw core that can parse `去二楼救人`, execute a five-step simulated rescue plan, and persist memory.

**Architecture:** Implement a small `src/fireclaw_core` package with explicit modules for planning, skills, dry-run robot adapter, safety, execution, memory, and agent orchestration. Keep all robot actions simulated and deterministic so the system can be tested before ROS2 or LLM integration.

**Tech Stack:** Python 3.11, `pytest`, JSONL memory files, dataclasses/enums.

---

### Task 1: Project Skeleton and Planner Tests

**Files:**
- Create: `pyproject.toml`
- Create: `src/fireclaw_core/__init__.py`
- Create: `src/fireclaw_core/planner.py`
- Test: `tests/test_planner.py`

- [x] **Step 1: Write failing planner tests**

Create `tests/test_planner.py` with tests for parsing `去二楼救人`, `去2楼救人`, and returning clarification for unknown commands.

- [x] **Step 2: Run planner tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_planner.py -v`

Expected: fails because `fireclaw_core.planner` does not exist.

- [x] **Step 3: Implement minimal planner**

Create `src/fireclaw_core/planner.py` with `PlanStep`, `Plan`, `PlanningResult`, and `RuleBasedPlanner`.

- [x] **Step 4: Run planner tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_planner.py -v`

Expected: all planner tests pass.

### Task 2: Skills, Robot Adapter, and Executor

**Files:**
- Create: `src/fireclaw_core/robot.py`
- Create: `src/fireclaw_core/skills.py`
- Create: `src/fireclaw_core/executor.py`
- Test: `tests/test_execution.py`

- [x] **Step 1: Write failing execution tests**

Create tests that build the default skill registry, execute the five rescue steps, and assert the dry-run robot adapter recorded simulated actions only.

- [x] **Step 2: Run execution tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_execution.py -v`

Expected: fails because skills/executor modules do not exist.

- [x] **Step 3: Implement dry-run robot adapter, skill registry, built-in skills, and sequential executor**

Implement five built-in skills: `navigate_to_floor`, `search_for_victims`, `assess_victim`, `report_status`, and `return_to_safe_zone`.

- [x] **Step 4: Run execution tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_execution.py -v`

Expected: all execution tests pass.

### Task 3: Safety Gate

**Files:**
- Create: `src/fireclaw_core/safety.py`
- Test: `tests/test_safety.py`

- [x] **Step 1: Write failing safety tests**

Create tests for allowing a valid dry-run plan, blocking missing skills, clarifying unparsed commands, and blocking non-dry-run mode.

- [x] **Step 2: Run safety tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_safety.py -v`

Expected: fails because `fireclaw_core.safety` does not exist.

- [x] **Step 3: Implement `SafetyGate` and structured decisions**

Use decisions `allow`, `block`, and `clarify`.

- [x] **Step 4: Run safety tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_safety.py -v`

Expected: all safety tests pass.

### Task 4: JSONL Memory Store

**Files:**
- Create: `src/fireclaw_core/memory.py`
- Test: `tests/test_memory.py`

- [x] **Step 1: Write failing memory tests**

Create tests that append a run record and read it back from a JSONL file under a temporary directory.

- [x] **Step 2: Run memory tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_memory.py -v`

Expected: fails because `fireclaw_core.memory` does not exist.

- [x] **Step 3: Implement append/list JSONL memory store**

Use UTF-8 JSON lines and create parent directories automatically.

- [x] **Step 4: Run memory tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_memory.py -v`

Expected: all memory tests pass.

### Task 5: Agent Orchestration and Demo CLI

**Files:**
- Create: `src/fireclaw_core/agent.py`
- Create: `src/fireclaw_core/__main__.py`
- Test: `tests/test_agent.py`

- [x] **Step 1: Write failing agent tests**

Create tests for a full successful `去二楼救人` run, unknown command clarification, and memory write error reporting.

- [x] **Step 2: Run agent tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_agent.py -v`

Expected: fails because `fireclaw_core.agent` does not exist.

- [x] **Step 3: Implement `FireClawAgent` and a minimal module CLI**

The CLI should support `.venv/bin/python -m fireclaw_core "去二楼救人" --memory-path <path>`.

- [x] **Step 4: Run agent tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_agent.py -v`

Expected: all agent tests pass.

### Task 6: Final Verification and Memory Update

**Files:**
- Modify: `memory/2026-06-01/fireclaw-dry-run-core.md`

- [x] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest -v`

Expected: all tests pass.

- [x] **Step 2: Run demo command**

Run: `.venv/bin/python -m fireclaw_core "去二楼救人" --memory-path /tmp/fireclaw-demo-memory.jsonl`

Expected: JSON output showing successful dry-run rescue execution.

- [x] **Step 3: Update memory record**

Append implemented files, verification commands, test results, and remaining gaps to `memory/2026-06-01/fireclaw-dry-run-core.md`.
