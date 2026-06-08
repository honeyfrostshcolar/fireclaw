# Mission Control CLI v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the main FireClaw mission layer through a runnable CLI for explicit robot subtask submission and mission trace inspection.

**Architecture:** Add `fireclaw_core.mission_cli` as a thin command-line wrapper around `RobotRegistry`, `JsonlMissionRegistry`, and `MissionAgent`. The CLI uses existing robot-local Gateway endpoints through `RobotSubagentClient`; it does not add a mission HTTP server or autonomous mission decomposition in v1.

**Tech Stack:** Python `argparse`, JSON stdout, existing MissionAgent/RobotRegistry/MissionRegistry, pytest subprocess tests.

---

### Task 1: Submit Subtask CLI

**Files:**
- Create: `src/fireclaw_core/mission_cli.py`
- Create: `tests/test_mission_cli.py`

- [x] Write failing subprocess test for `python -m fireclaw_core.mission_cli submit-subtask`.
- [x] Implement CLI argument parsing for `submit-subtask`.
- [x] Load robot registry JSON.
- [x] Create `JsonlMissionRegistry`.
- [x] Call `MissionAgent.submit_subtask(...)`.
- [x] Print JSON result and return exit code `0` for accepted/duplicate/succeeded/running, `1` otherwise.
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_cli.py::test_mission_cli_submit_subtask_records_mission -q`.

### Task 2: Mission Trace CLI

**Files:**
- Modify: `src/fireclaw_core/mission_cli.py`
- Modify: `tests/test_mission_cli.py`

- [x] Write failing subprocess test for `python -m fireclaw_core.mission_cli trace`.
- [x] Implement `trace` subcommand.
- [x] Load the same registry and mission registry.
- [x] Call `MissionAgent.mission_trace(...)`.
- [x] Print JSON trace and return `0` for found missions, `1` for missing missions.
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_cli.py -q`.

### Task 3: Docs, Memory, Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-08/fireclaw-work-resume.md`
- Modify: `docs/superpowers/plans/2026-06-08-mission-control-cli-v1.md`

- [x] Document mission CLI commands and their role.
- [x] Record commands, files changed, limitations, and next step in memory.
- [x] Mark plan checkboxes completed.
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_cli.py tests/test_mission_agent.py tests/test_mission_registry.py -q`.
- [x] Run `./.venv/bin/python -m pytest -q`.
