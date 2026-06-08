# Mission Registry and Trace v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist main-agent missions/subtasks and aggregate robot-subagent task traces into a mission-level trace.

**Architecture:** Add a JSONL mission registry for main-agent mission/subtask lifecycle records. Extend `MissionAgent` so explicit subtask submission creates or updates mission records. Add trace aggregation that retrieves each robot subagent's local trace through `RobotSubagentClient` and projects a mission-level status while preserving robot-local traces as source of truth.

**Tech Stack:** Python dataclasses, JSONL append-only storage, existing `RobotRegistry` and `RobotSubagentClient`, pytest.

---

### Task 1: Mission Registry Store

**Files:**
- Create: `src/fireclaw_core/mission_registry.py`
- Create: `tests/test_mission_registry.py`

- [x] Write failing tests for mission creation, subtask recording, subtask status update, and mission trace projection.
- [x] Implement `MissionSubtaskRecord`, `MissionRecord`, and `JsonlMissionRegistry`.
- [x] Support `create_mission(...)`, `record_subtask(...)`, `update_subtask(...)`, `get_mission(...)`, and `mission_trace(...)`.
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_registry.py -q`.

### Task 2: MissionAgent Persistence

**Files:**
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `tests/test_mission_agent.py`

- [x] Write failing test proving `MissionAgent.submit_subtask(...)` writes mission and subtask records.
- [x] Add optional `mission_registry` dependency to `MissionAgent`.
- [x] Record mission/subtask on successful subagent submission.
- [x] Update existing mission agent tests as needed without weakening assertions.
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_agent.py -q`.

### Task 3: Mission Trace Aggregation

**Files:**
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `tests/test_mission_agent.py`

- [x] Write failing test proving `MissionAgent.mission_trace(mission_id)` fetches robot task traces and aggregates status.
- [x] Implement trace aggregation through `subagent_client.get_task_trace(...)`.
- [x] Update mission subtask status from fetched robot trace result when available.
- [x] Preserve robot-local trace payloads under each mission subtask.
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_agent.py -q`.

### Task 4: Integration, Docs, Memory

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-08/fireclaw-work-resume.md`
- Modify: `docs/superpowers/plans/2026-06-08-mission-registry-trace-v1.md`

- [x] Document mission registry and trace aggregation in README.
- [x] Record implementation details, commands, known limitations, and next step in memory.
- [x] Mark plan checkboxes completed as tasks finish.
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_registry.py tests/test_mission_agent.py -q`.
- [x] Run `./.venv/bin/python -m pytest -q`.
