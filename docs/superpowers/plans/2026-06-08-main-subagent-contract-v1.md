# Main/Subagent Contract v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first FireClaw main-agent to robot-subagent contract, using existing robot-local Gateways as callable embodied subagents.

**Architecture:** Keep `FireClawGateway` as the robot subagent control plane. Add a new mission-level layer with a static robot registry, an HTTP subagent client, and a minimal mission agent that submits explicit subtasks to robot subagents and aggregates their accepted task records. V1 does not implement autonomous mission decomposition, peer-to-peer robot communication, or direct ROS control from the main agent.

**Tech Stack:** Python dataclasses, JSON config loading, `urllib.request` HTTP client, existing Gateway HTTP API, pytest.

---

### Task 1: Robot Registry

**Files:**
- Create: `src/fireclaw_core/robot_registry.py`
- Create: `tests/test_robot_registry.py`

- [x] Write failing tests for loading robot entries from JSON.
- [x] Implement `RobotRegistryEntry`, `RobotRegistry`, and `load_robot_registry`.
- [x] Validate required fields: `robot_id`, `base_url`.
- [x] Run `./.venv/bin/python -m pytest tests/test_robot_registry.py -q`.

### Task 2: Robot Subagent HTTP Client

**Files:**
- Create: `src/fireclaw_core/subagent_client.py`
- Create: `tests/test_subagent_client.py`

- [x] Write failing integration test using a local `FireClawGateway` as a robot subagent.
- [x] Implement `RobotSubagentClient.get_state(...)`.
- [x] Implement `RobotSubagentClient.submit_task(...)`.
- [x] Implement `RobotSubagentClient.get_task_trace(...)`.
- [x] Implement `RobotSubagentClient.cancel_task(...)`.
- [x] Run `./.venv/bin/python -m pytest tests/test_subagent_client.py -q`.

### Task 3: Minimal Mission Agent

**Files:**
- Create: `src/fireclaw_core/mission_agent.py`
- Create: `tests/test_mission_agent.py`

- [x] Write failing test for explicit subtask submission to a registered robot.
- [x] Implement `MissionAgent.submit_subtask(robot_id, command, session_id=None, dedupe_key=None)`.
- [x] Return mission-level result with `mission_id`, `robot_id`, `task_id`, `status`, and `subtasks`.
- [x] Write failing test for rejecting unknown robots before HTTP submission.
- [x] Implement unknown-robot rejection.
- [x] Run `./.venv/bin/python -m pytest tests/test_mission_agent.py -q`.

### Task 4: Docs and Memory

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-08/fireclaw-work-resume.md`
- Modify: `docs/superpowers/plans/2026-06-08-main-subagent-contract-v1.md`

- [x] Document the Main/Subagent Contract v1 under the Gateway or architecture section.
- [x] Record files changed, commands, OpenClaw analogue, and limitations in memory.
- [x] Mark plan checkboxes completed as tasks finish.
- [x] Run `./.venv/bin/python -m pytest tests/test_robot_registry.py tests/test_subagent_client.py tests/test_mission_agent.py -q`.
- [x] Run `./.venv/bin/python -m pytest -q`.
