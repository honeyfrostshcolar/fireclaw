# FireClaw Skill / Action Remap YAML Session

## 2026-06-05 11:20 CST

### Task Goal

Expose FireClaw's skill/action to ROS1 endpoint mapping as a user-editable YAML/JSON file so future robot skills can be added without changing core code.

### User Direction

The user asked whether each task would need its own ROS1 flow. We clarified that ROS1 binding should happen at the capability/skill layer, not per natural-language task. The user then suggested exposing the interface and using a YAML-like file where robot teams can fill in their own mappings, with official standard ROS types as defaults and remapping for custom systems.

### Design Decision

Implemented **Skill / Action Remap YAML v1**:

```text
FireClaw action/skill name -> YAML remap entry -> ROS1 endpoint config -> Ros1RobotAdapter skeleton
```

This remains dependency-free. `.json` uses `json.loads`; `.yaml`/`.yml` uses a strict local YAML subset parser for nested mappings and scalar values. No `rospy`, `actionlib`, PyYAML, or live robot transport was added.

### Files Modified

- `src/fireclaw_core/ros1_config.py`
- `src/fireclaw_core/robot.py`
- `src/fireclaw_core/doctor.py`
- `tests/test_ros1_config.py`
- `tests/test_robot.py`
- `tests/test_doctor.py`
- `README.md`
- `docs/superpowers/specs/2026-06-05-skill-action-remap-yaml-v1-design.md`
- `docs/superpowers/plans/2026-06-05-skill-action-remap-yaml-v1.md`
- `memory/2026-06-05/fireclaw-skill-action-remap-yaml.md`

### Implementation Details

- Added YAML remap support:
  - top-level `remap` is an alias for `endpoints`;
  - `emergency_stop` may appear under `remap` and is normalized into `config.emergency_stop`;
  - top-level `targets` is loaded into `Ros1AdapterConfig.targets`.
- Added standard profiles:
  - `move_base` -> `interface=action`, `type=move_base_msgs/MoveBaseAction`, cancel/feedback supported;
  - `trigger_service` -> `interface=service`, `type=std_srvs/Trigger`;
  - `string_topic` -> `interface=topic`, `type=std_msgs/String`.
- `Ros1EndpointConfig` now stores:
  - `profile`
  - `goal_template`
  - `request_template`
- Custom remap keys such as `spray_water` are allowed. The parser no longer rejects action names outside the built-in action list.
- `Ros1RobotAdapter` action results now include:
  - `ros1_profile`
  - `goal_template`
  - `request_template`
  - `targets`
- Doctor ROS1 config check now reports:
  - `custom_actions`
  - `workspace_skill_names`
  - `workspace_skills_missing_remap`
  - `unknown_remap_actions`

### Commands Executed

- `.venv/bin/python -m pytest tests/test_ros1_config.py::test_load_ros1_adapter_config_parses_yaml_remap_profiles_and_targets -q`
  - RED: YAML was parsed as JSON.
- `.venv/bin/python -m pytest tests/test_ros1_config.py::test_load_ros1_adapter_config_parses_yaml_remap_profiles_and_targets -q`
  - GREEN after adding YAML parser, profile expansion, `remap`, `targets`, and remap-level `emergency_stop`.
- `.venv/bin/python -m pytest tests/test_ros1_config.py::test_load_ros1_adapter_config_allows_custom_skill_remap -q`
  - GREEN because custom action support was already enabled by the previous parser change.
- `.venv/bin/python -m pytest tests/test_robot.py::test_ros1_robot_adapter_result_includes_remap_template_metadata -q`
  - RED: adapter result did not expose profile/template metadata.
- `.venv/bin/python -m pytest tests/test_robot.py::test_ros1_robot_adapter_result_includes_remap_template_metadata -q`
  - GREEN after adding remap metadata to action result data.
- `.venv/bin/python -m pytest tests/test_doctor.py::test_run_doctor_reports_workspace_skills_missing_ros1_remap -q`
  - RED: Doctor did not report custom/workspace remap coverage.
- `.venv/bin/python -m pytest tests/test_doctor.py::test_run_doctor_reports_workspace_skills_missing_ros1_remap -q`
  - GREEN after passing workspace skill names into ROS1 config check.
- `.venv/bin/python -m pytest tests/test_ros1_config.py tests/test_robot.py tests/test_doctor.py tests/test_cli.py -q`
  - GREEN: 45 passed.

### Current Conclusion

FireClaw now exposes the intended extension boundary: future skills can be declared by manifest and bound to ROS1 endpoints in a YAML remap file. This avoids creating a separate ROS1 flow for every natural-language task and shifts robot-specific integration into auditable configuration.

### Remaining Gaps

- YAML parser intentionally supports only a strict subset: mappings, scalar strings/numbers/booleans, comments, and blank lines.
- No template rendering yet.
- No actual `rospy`/`actionlib` call construction yet.
- No message-field validation against real ROS message definitions.
- No CLI helper to print unresolved remap coverage without running full doctor.
