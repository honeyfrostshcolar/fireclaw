# ROS1 Streaming Feedback & Action Cancellation v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire ROS1 action feedback and Gateway cancellation into FireClaw's existing action event model.

**Architecture:** Extend existing cancellation callbacks from `PlanExecutor` through `Skill`, `RobotActionRuntime`, `RobotAdapterActionBackend`, `Ros1RobotAdapter`, and `Ros1Transport`. Keep transport fake-injectable for tests.

**Tech Stack:** Python callbacks, fake ROS1 action clients, pytest.

---

### Task 1: Propagate Cancellation Into Robot Backend

**Files:**
- Modify: `src/fireclaw_core/skills.py`
- Modify: `src/fireclaw_core/action_runtime.py`
- Test: `tests/test_action_runtime.py`

- [ ] Write failing test proving backend receives `cancellation_requested`.
- [ ] Implement optional cancellation argument through robot skill handler and action runtime.
- [ ] Run targeted tests.

### Task 2: ROS1 Action Cancel

**Files:**
- Modify: `src/fireclaw_core/ros1_transport.py`
- Test: `tests/test_ros1_transport.py`

- [ ] Write failing test proving cancellation calls `cancel_goal()`.
- [ ] Implement active action client tracking and polling.
- [ ] Run targeted tests.

### Task 3: Adapter Feedback Bridge

**Files:**
- Modify: `src/fireclaw_core/robot.py`
- Test: `tests/test_robot.py`
- Test: `tests/test_gateway.py`

- [ ] Write tests proving ROS1 feedback reaches `action.feedback`.
- [ ] Pass feedback sink and cancellation callback into transport execution.
- [ ] Run targeted tests.

### Task 4: Docs, Memory, Verification

**Files:**
- Modify: `README.md`
- Create: `memory/2026-06-05/fireclaw-ros1-streaming-feedback-cancel-v1.md`

- [ ] Document feedback/cancel behavior.
- [ ] Record commands and known gaps.
- [ ] Run `./.venv/bin/python -m pytest -q`.
