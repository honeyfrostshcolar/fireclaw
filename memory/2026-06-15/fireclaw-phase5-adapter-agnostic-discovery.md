# FireClaw Phase 5 Adapter-Agnostic Discovery

## Task Goal

Finish sensor capability grounding by moving discovery behind a backend-agnostic contract and fixing the final Phase 4 confirmation semantics.

## Files Modified

- `src/fireclaw_core/mission/mission_cli.py`
- `src/fireclaw_core/agent/profile_discovery.py`
- `src/fireclaw_core/sensors/backends.py`
- `src/fireclaw_core/sensors/__init__.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/agent/robot.py`
- `tests/test_mission_cli.py`
- `tests/test_profile_discovery.py`
- `tests/test_sensor_backends.py`
- `tests/test_gateway_robot_profile_config.py`
- `tests/test_ros1_adapter_state.py`
- `tests/test_safety.py`
- `docs/deployment/ros1-gazebo-debugging-guide.md`

## Actual Verification Results

- Focused Phase 5 tests: **82 passed** in 21.37s
  - `tests/test_profile_discovery.py`, `tests/test_mission_cli.py`, `tests/test_sensor_backends.py`, `tests/test_gateway_robot_profile_config.py`, `tests/test_ros1_adapter_state.py`, `tests/test_safety.py`
- Full suite: **1246 passed, 6 skipped** in 190.80s

All Phase 5 adapter-agnostic discovery tests pass. No regressions.

## Remaining Gaps

- ROS2 and vendor SDK discovery backends are still future work.
- Static simulator/dry-run discovery is intentionally labeled and is not valid real-world authority.
- ROS1 typed message inspection would still be stronger than `rostopic echo` text parsing.
