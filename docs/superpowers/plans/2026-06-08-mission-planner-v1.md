# Mission Planner v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a deterministic mission planner that decomposes multi-floor/multi-robot natural-language commands into robot subtask assignments with parallel/sequential execution ordering.

**Architecture:** New `MissionPlanner` class in `fireclaw_core.mission_planner` parses Chinese multi-floor commands, matches robots by `capabilities` and `zone` from `RobotRegistry`, and produces a `MissionPlan` with execution-group-ordered subtasks. `MissionAgent` gets a `plan_and_submit()` method. CLI gets a `plan-mission` command. V1 is rule-based only (no LLM).

**Tech Stack:** Python dataclasses, regex pattern matching, existing `RobotRegistry`, `MissionAgent`, `JsonlMissionRegistry`, argparse CLI, pytest.

---

### Task 1: MissionPlanner Data Types and Parser

**Files:**
- Create: `src/fireclaw_core/mission_planner.py`
- Create: `tests/test_mission_planner.py`

- [x] Write failing tests for `MissionPlanner.plan()` parsing multi-floor commands:
  - "去二楼和三楼搜索受困人员" → 2 subtasks, floors [2, 3], intent "search"
  - "去二楼搜索受困人员" → 1 subtask, floor [2], intent "search"
  - "搜索整栋楼" → clarify (no specific floor)
  - "巡逻一楼和二楼" → 2 subtasks, intent "patrol"
- [x] Implement `MissionSubtask` dataclass: `robot_id`, `command`, `floor`, `capability_required`, `execution_group`
- [x] Implement `MissionPlan` dataclass: `intent`, `command`, `subtasks`, `execution_groups`
- [x] Implement `MissionPlannerContext` dataclass: `available_robots` (list of `RobotRegistryEntry`)
- [x] Implement `MissionPlanner.plan(command, context)` with floor extraction and intent parsing
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_planner.py::test_mission_planner_parses_multi_floor_command -q`
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_planner.py -q`

### Task 2: Robot Capability Matching and Assignment

**Files:**
- Modify: `src/fireclaw_core/mission_planner.py`
- Modify: `tests/test_mission_planner.py`

- [x] Write failing tests for robot assignment:
  - 2 robots with `search_for_victims` capability, 2 floors → each robot gets 1 floor
  - 1 robot with capability, 2 floors → same robot gets both floors (sequential groups)
  - No robot with required capability → clarify status
  - Robot with `zone` filter matching → preferred assignment
  - Disabled robot excluded from assignment
- [x] Implement `_assign_robots()` method: match subtasks to robots by capability, prefer zone match, round-robin when multiple robots qualify
- [x] Implement sequential grouping: when robot count < floor count, assign same robot to multiple floors with incrementing `execution_group`
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_planner.py -q`

### Task 3: MissionPlanner Protocol and Integration

**Files:**
- Modify: `src/fireclaw_core/mission_planner.py`
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `tests/test_mission_agent.py`

- [x] Write failing test for `MissionAgent.plan_and_submit(command)` producing subtasks from planner output
- [x] Define `MissionPlannerProtocol` in `mission_planner.py`
- [x] Add `planner: MissionPlannerProtocol | None` parameter to `MissionAgent.__init__`
- [x] Implement `MissionAgent.plan_and_submit(command, session_id, operator)`:
  - calls planner with `MissionPlannerContext` built from `self.registry`
  - for each subtask in plan, calls `self.submit_subtask()`
  - returns aggregated result with plan + subtask results
- [x] Write failing test for plan_and_submit when no planner configured → returns error
- [x] Implement no-planner error case
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_agent.py -q`

### Task 4: Mission CLI plan-mission Command

**Files:**
- Modify: `src/fireclaw_core/mission_cli.py`
- Modify: `tests/test_mission_cli.py`

- [x] Write failing subprocess test for `python -m fireclaw_core.mission_cli plan-mission --command "去二楼和三楼搜索受困人员"`
- [x] Add `plan-mission` subcommand to CLI with `--command` argument
- [x] Build `MissionPlanner` and wire into `MissionAgent.plan_and_submit()`
- [x] Print JSON result with plan and subtask outcomes
- [x] Write test for `plan-mission` with no capable robot → non-zero exit
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_cli.py -q`

### Task 5: Full Suite Verification and Documentation

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-08/fireclaw-work-resume.md`
- Modify: `docs/superpowers/plans/2026-06-08-mission-planner-v1.md`

- [x] Document mission planner in README under mission architecture section
- [x] Record files changed, commands, limitations, and next step in memory
- [x] Mark plan checkboxes completed
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_planner.py tests/test_mission_agent.py tests/test_mission_cli.py -q`
- [x] Run `./.venv/bin/python -m pytest -q`
