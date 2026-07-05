# FireClaw Phase 3 Sensor Policy Implementation

## Task Goal

Add safety-critical sensor policy after Phase 2 health checks so SafetyGate can distinguish allow, warn, escalate, and block behavior from sensor health diagnostics.

## Files Modified

- `src/fireclaw_core/ros/ros1_sensor_discovery.py`
- `src/fireclaw_core/safety/sensor_policy.py`
- `src/fireclaw_core/safety/safety.py`
- `src/fireclaw_core/execution/skills.py`
- `src/fireclaw_core/agent/robot.py`
- `tests/test_ros1_sensor_discovery.py`
- `tests/test_sensor_policy.py`
- `tests/test_safety.py`
- `tests/test_skill_metadata.py`
- `docs/deployment/ros1-gazebo-debugging-guide.md`

## Verification Commands

- `.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py tests/test_sensor_health.py -q`
- `.venv/bin/python -m pytest tests/test_sensor_policy.py tests/test_safety.py tests/test_skill_metadata.py -q`
- `.venv/bin/python -m pytest tests/test_cli.py tests/test_agent.py tests/test_gateway_structured_task.py tests/test_profile_driven_runtime_e2e.py -q`
- `.venv/bin/python -m pytest -q`

## Actual Verification Results

- Focused Phase 3 command: `63 passed in 0.11s`
- Full suite command: `1227 passed, 6 skipped in 174.96s`

## Remaining Gaps

- Phase 4 operator confirmation loop is still not implemented.
- Phase 5 adapter-agnostic discovery backend is still not implemented.
- ROS1 CLI parsing is stricter but still text-based; a later backend should prefer typed ROS message inspection.
