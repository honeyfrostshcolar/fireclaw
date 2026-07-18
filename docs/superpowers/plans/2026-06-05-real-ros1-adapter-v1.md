# Real ROS1 Adapter v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add template rendering and optional ROS1 topic/service/action transport to the ROS1 adapter.

**Architecture:** Keep template rendering in `ros1_template.py`, transport integration in `ros1_transport.py`, config parsing in `ros1_config.py`, and robot action orchestration in `robot.py`.

**Tech Stack:** Python dataclasses, optional `rospy`/`actionlib`, pytest fake transport tests.

---

### Task 1: Template Rendering

**Files:**
- Create: `src/fireclaw_core/ros1_template.py`
- Test: `tests/test_ros1_template.py`

- [ ] Write failing tests for resolving `{{ targets.floor_${floor}.x }}`.
- [ ] Implement recursive template rendering.
- [ ] Run targeted tests.

### Task 2: Transport Config

**Files:**
- Modify: `src/fireclaw_core/ros1_config.py`
- Test: `tests/test_ros1_config.py`

- [ ] Write failing tests for `transport.enabled`.
- [ ] Add `Ros1TransportConfig`.
- [ ] Run targeted tests.

### Task 3: ROS1 Transport Interface

**Files:**
- Create: `src/fireclaw_core/ros1_transport.py`
- Test: `tests/test_ros1_transport.py`

- [ ] Write fake transport tests for topic, service, and action.
- [ ] Implement `Ros1Transport` with optional imports and fake-injectable modules.
- [ ] Run targeted tests.

### Task 4: Adapter Integration

**Files:**
- Modify: `src/fireclaw_core/robot.py`
- Modify: `src/fireclaw_core/runtime_config.py`
- Test: `tests/test_robot.py`

- [ ] Write tests proving transport-enabled adapter succeeds with fake transport.
- [ ] Keep transport-disabled behavior as `not_configured`.
- [ ] Run targeted tests.

### Task 5: Docs, Memory, Verification

**Files:**
- Modify: `README.md`
- Create: `memory/2026-06-05/fireclaw-real-ros1-adapter-v1.md`

- [ ] Document `transport.enabled`.
- [ ] Record commands and known gaps.
- [ ] Run `./.venv/bin/python -m pytest -q`.
