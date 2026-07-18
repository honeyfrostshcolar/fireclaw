# ROS1 Config / Real Adapter Skeleton v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dependency-free ROS1 configuration loader and real-adapter skeleton that fixes FireClaw's future ROS1 transport boundary without controlling hardware.

**Architecture:** Keep config parsing in `ros1_config.py`, adapter creation in `runtime_config.py`, and robot method behavior in `robot.py`. Doctor consumes the same config loader so readiness checks match runtime behavior.

**Tech Stack:** Python dataclasses, JSON, pytest, existing FireClaw runtime/doctor modules.

---

### Task 1: ROS1 Config Loader

**Files:**
- Create: `src/fireclaw_core/ros1_config.py`
- Test: `tests/test_ros1_config.py`

- [ ] Write failing tests for valid config loading and invalid endpoint validation.
- [ ] Run `./.venv/bin/python -m pytest tests/test_ros1_config.py -q` and confirm import failure.
- [ ] Implement dataclasses `Ros1EndpointConfig`, `Ros1EmergencyStopConfig`, `Ros1TimeoutConfig`, `Ros1AdapterConfig`.
- [ ] Implement `load_ros1_adapter_config(path)`.
- [ ] Run `./.venv/bin/python -m pytest tests/test_ros1_config.py -q` and confirm pass.

### Task 2: ROS1 Adapter Skeleton

**Files:**
- Modify: `src/fireclaw_core/robot.py`
- Modify: `src/fireclaw_core/runtime_config.py`
- Test: `tests/test_robot.py`

- [ ] Write failing tests proving `create_robot_adapter("ros1", ..., config_path=...)` returns `Ros1RobotAdapter`.
- [ ] Write failing tests proving `navigate_to_floor(...)` records configured endpoint metadata and returns `status="not_configured"`.
- [ ] Run targeted tests and confirm failure.
- [ ] Implement `Ros1RobotAdapter`.
- [ ] Extend `ADAPTER_CHOICES` and `create_robot_adapter(...)`.
- [ ] Run targeted tests and confirm pass.

### Task 3: Doctor ROS1 Config Checks

**Files:**
- Modify: `src/fireclaw_core/doctor.py`
- Test: `tests/test_doctor.py`

- [ ] Write failing tests for `run_doctor(adapter="ros1", ros1_config_path=...)`.
- [ ] Run targeted test and confirm failure.
- [ ] Add `ros1_config_path` argument to `run_doctor(...)` and CLI `--ros1-config`.
- [ ] Add check `ros1_config`.
- [ ] Run targeted doctor tests and confirm pass.

### Task 4: Docs, Memory, Full Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-04/fireclaw-ros1-config-skeleton.md`

- [ ] Document a minimal ROS1 config example and doctor command.
- [ ] Record files changed, commands run, and known gaps in memory.
- [ ] Run `./.venv/bin/python -m pytest -q`.
- [ ] Do not commit unless the user explicitly asks.
