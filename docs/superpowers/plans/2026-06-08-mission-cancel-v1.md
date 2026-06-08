# Mission Cancel v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add mission-level cancellation that propagates cancel requests from the main mission agent to all recorded robot subtasks.

**Architecture:** Extend `MissionAgent` with `cancel_mission(mission_id)` that reads `JsonlMissionRegistry`, skips terminal subtasks, calls `RobotSubagentClient.cancel_task(...)` for active robot subtasks, and updates mission subtask status from cancel responses. Add a `mission_cli cancel` command as the operator/script entrypoint.

**Tech Stack:** Existing `MissionAgent`, `JsonlMissionRegistry`, `RobotSubagentClient`, argparse CLI, pytest.

---

### Task 1: MissionAgent Cancel

**Files:**
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `tests/test_mission_agent.py`

- [x] Write failing test proving `MissionAgent.cancel_mission(...)` calls subagent cancel for recorded subtasks.
- [x] Extend `SubagentClient` protocol with `cancel_task(...)`.
- [x] Implement `cancel_mission(mission_id, operator=None)`.
- [x] Update mission subtask status to `cancel_requested`, `cancelled`, or returned cancel status.
- [x] Skip terminal subtasks.
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_agent.py -q`.

### Task 2: Mission CLI Cancel

**Files:**
- Modify: `src/fireclaw_core/mission_cli.py`
- Modify: `tests/test_mission_cli.py`

- [x] Write failing subprocess test for `python -m fireclaw_core.mission_cli cancel`.
- [x] Add `cancel` subcommand.
- [x] Load robot and mission registries.
- [x] Call `MissionAgent.cancel_mission(...)`.
- [x] Print JSON result and return `0` if mission exists, `1` if missing/not configured.
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_cli.py -q`.

### Task 3: Docs, Memory, Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-08/fireclaw-work-resume.md`
- Modify: `docs/superpowers/plans/2026-06-08-mission-cancel-v1.md`

- [x] Document mission cancel CLI.
- [x] Record commands, files changed, limitations, and next step in memory.
- [x] Mark plan checkboxes completed.
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_cli.py tests/test_mission_agent.py tests/test_mission_registry.py -q`.
- [x] Run `./.venv/bin/python -m pytest -q`.
