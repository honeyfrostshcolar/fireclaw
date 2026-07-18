# Skill / Action Remap YAML v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let FireClaw load user-editable YAML/JSON skill-action remaps and report binding coverage for built-in and workspace skills.

**Architecture:** Extend `ros1_config.py` with a dependency-free YAML subset parser, remap/profile expansion, and custom action support. Keep runtime consumption in `robot.py` unchanged except for carrying template metadata. Extend Doctor to compare remap keys against built-in actions and loaded workspace skills.

**Tech Stack:** Python dataclasses, JSON, strict local YAML subset parser, pytest.

---

### Task 1: YAML Remap Loading

**Files:**
- Modify: `src/fireclaw_core/ros1_config.py`
- Test: `tests/test_ros1_config.py`

- [ ] Write a failing test that loads `.yaml` with `remap.navigate_to_floor.profile=move_base` and a `targets.floor_2` mapping.
- [ ] Run `./.venv/bin/python -m pytest tests/test_ros1_config.py::test_load_ros1_adapter_config_parses_yaml_remap_profiles_and_targets -q` and confirm failure.
- [ ] Add a strict YAML subset parser and choose JSON vs YAML by suffix.
- [ ] Add profile expansion for `move_base`, `trigger_service`, and `string_topic`.
- [ ] Run the targeted test and confirm pass.

### Task 2: Custom Skill Remap Support

**Files:**
- Modify: `src/fireclaw_core/ros1_config.py`
- Modify: `src/fireclaw_core/robot.py`
- Test: `tests/test_ros1_config.py`
- Test: `tests/test_robot.py`

- [ ] Write a failing test that remaps custom action `spray_water`.
- [ ] Run targeted test and confirm failure from unknown endpoint rejection.
- [ ] Remove the unknown endpoint hard rejection.
- [ ] Store `profile`, `goal_template`, and `request_template` in `Ros1EndpointConfig`.
- [ ] Include template metadata in `Ros1RobotAdapter` action result data.
- [ ] Run targeted tests and confirm pass.

### Task 3: Doctor Binding Coverage

**Files:**
- Modify: `src/fireclaw_core/doctor.py`
- Test: `tests/test_doctor.py`

- [ ] Write a failing test with a workspace skill manifest and a missing remap binding.
- [ ] Run targeted test and confirm failure.
- [ ] Extend `_ros1_config_check(...)` to accept workspace skill names.
- [ ] Report `custom_actions`, `workspace_skills_missing_remap`, and `unknown_remap_actions`.
- [ ] Run targeted test and confirm pass.

### Task 4: Docs, Memory, Verification

**Files:**
- Modify: `README.md`
- Create: `memory/2026-06-05/fireclaw-skill-action-remap-yaml.md`

- [ ] Document YAML remap profiles and a custom skill example.
- [ ] Record commands and conclusions in memory.
- [ ] Run `./.venv/bin/python -m pytest -q`.
- [ ] Do not commit unless explicitly asked.
