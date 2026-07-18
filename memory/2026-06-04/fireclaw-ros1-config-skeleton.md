# FireClaw ROS1 Config / Real Adapter Skeleton Session

## 2026-06-04 20:40 CST

### Task Goal

Start the next phase after Doctor v1 by adding a dependency-free ROS1 configuration and real-adapter skeleton boundary.

### Current Progress

Implemented:

- `src/fireclaw_core/ros1_config.py`
- `Ros1RobotAdapter` in `src/fireclaw_core/robot.py`
- `ros1` adapter choice in `src/fireclaw_core/runtime_config.py`
- `--ros1-config` in `src/fireclaw_core/__main__.py`
- ROS1 config checks in `src/fireclaw_core/doctor.py`
- tests for config parsing, adapter creation, CLI config wiring, and doctor readiness
- spec and plan:
  - `docs/superpowers/specs/2026-06-04-ros1-config-real-adapter-skeleton-v1-design.md`
  - `docs/superpowers/plans/2026-06-04-ros1-config-real-adapter-skeleton-v1.md`

### OpenClaw Analogue

Checked OpenClaw config/validation patterns through CodeGraph under `openclaw-main`.

Relevant analogue:

- structured config loading in `ShareGatewayRelaySettings.loadConfig(...)`
- request/config validation via `assertValidParams(...)`

FireClaw adaptation:

```text
JSON ROS1 endpoint config -> typed validation -> runtime adapter skeleton -> explicit not_configured result
```

### Commands Executed

- `.venv/bin/python -m pytest tests/test_ros1_config.py -q`
  - RED: failed because `fireclaw_core.ros1_config` did not exist.
- `.venv/bin/python -m pytest tests/test_ros1_config.py -q`
  - GREEN: 2 passed.
- `.venv/bin/python -m pytest tests/test_robot.py::test_runtime_config_creates_ros1_adapter_from_config_without_ros_dependency tests/test_robot.py::test_ros1_robot_adapter_records_configured_endpoint_but_refuses_live_execution -q`
  - RED: failed because `Ros1RobotAdapter` did not exist.
- `.venv/bin/python -m pytest tests/test_robot.py::test_runtime_config_creates_ros1_adapter_from_config_without_ros_dependency tests/test_robot.py::test_ros1_robot_adapter_records_configured_endpoint_but_refuses_live_execution -q`
  - GREEN: 2 passed.
- `.venv/bin/python -m pytest tests/test_doctor.py::test_run_doctor_checks_ros1_config_readiness tests/test_doctor.py::test_run_doctor_fails_ros1_without_config_path -q`
  - RED: failed because `run_doctor(...)` had no `ros1_config_path` and no `ros1_config` check.
- `.venv/bin/python -m pytest tests/test_doctor.py::test_run_doctor_checks_ros1_config_readiness tests/test_doctor.py::test_run_doctor_fails_ros1_without_config_path -q`
  - GREEN: 2 passed.
- `.venv/bin/python -m pytest tests/test_cli.py::test_module_cli_accepts_ros1_config_for_adapter_skeleton -q`
  - RED: failed first because CLI did not accept `--ros1-config`; after wiring, expected result was corrected to safety `block` because the `ros1` skeleton has no reachable floor/battery telemetry yet.
- `.venv/bin/python -m pytest tests/test_cli.py::test_module_cli_accepts_ros1_config_for_adapter_skeleton -q`
  - GREEN: 1 passed.
- `.venv/bin/python -m pytest tests/test_ros1_config.py tests/test_robot.py tests/test_doctor.py tests/test_cli.py -q`
  - GREEN: 41 passed.

### Implementation Details

- `load_ros1_adapter_config(path)` parses JSON into dataclasses.
- Endpoint interfaces are constrained to `topic`, `service`, or `action`.
- Built-in endpoint names are constrained to FireClaw's robot action boundary:
  - `navigate_to_floor`
  - `search_for_victims`
  - `assess_victim`
  - `report_status`
  - `return_to_safe_zone`
- `Ros1RobotAdapter` does not import ROS packages.
- `Ros1RobotAdapter` records configured endpoint metadata in `Ros1CommandSpec`.
- Action calls return `RobotActionResult(status="not_configured", ok=False, mode="ros1", dry_run=False)` with `error="Live ROS1 transport is not implemented yet."`
- Missing endpoints return `not_configured` with a specific missing endpoint error.
- `doctor --adapter ros1 --ros1-config ...` reports missing built-in actions, emergency stop presence, and action feedback/cancel declarations.

### Current Conclusion

FireClaw now has a real ROS1 adapter configuration boundary without pretending to control hardware. This is the correct precondition for later binding actual `rospy`/`actionlib` transport once the target robot's topic, service, action, and message names are known.

### Remaining Gaps

- No live ROS master check.
- No `rospy` or `actionlib` transport.
- No message serialization or request/goal builders.
- `Ros1RobotAdapter.get_robot_state()` returns conservative unknown telemetry, so safety blocks rescue commands before execution.
- Need target robot stack details before implementing real transport:
  - actionlib action names and types;
  - service/topic names and message types;
  - feedback message fields;
  - cancellation semantics;
  - emergency-stop endpoint.
