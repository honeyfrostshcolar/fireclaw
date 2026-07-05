# FireClaw Phase 5 Review

## Task Goal

Review the user's implementation of `docs/superpowers/plans/2026-06-15-adapter-agnostic-discovery-phase5.md`.

## Current Progress

Reviewed commits:

- `afc8aa0 fix: separate discovery candidates from confirmation authority`
- `18426e8 feat: add sensor discovery backend protocol`
- `007d067 test: prove robot state consumes discovery backend protocol`
- `ad72331 feat: attach profile sensor discovery through backend factory`
- `71116b3 test: cover simulator discovery backend safety flow`
- `9b4be3c docs: document adapter agnostic discovery backends`

## Commands Executed

```bash
git status --short && git log --oneline -14
git diff --stat 0674eae..HEAD && git diff --name-only 0674eae..HEAD
.venv/bin/python -m pytest tests/test_profile_discovery.py tests/test_mission_cli.py tests/test_sensor_backends.py tests/test_gateway_robot_profile_config.py tests/test_ros1_adapter_state.py tests/test_safety.py -q
.venv/bin/python - <<'PY'
from fireclaw_core.agent.robot import Ros1RobotAdapter, SimulatorRobotAdapter
from fireclaw_core.ros.ros1_config import Ros1AdapterConfig
from fireclaw_core.sensors.backends import StaticDeclaredDiscoveryBackend

real = Ros1RobotAdapter(config=Ros1AdapterConfig(robot_id='r1'), sensor_discovery=StaticDeclaredDiscoveryBackend(sensors=('rgb_camera',), source='static', allow_real_mode=False))
state = real.get_robot_state()
print('ros1_static_state_available=', state.available_sensors)
print('ros1_static_state_diag_source=', state.sensor_diagnostics['source'] if state.sensor_diagnostics else None)

sim = SimulatorRobotAdapter(robot_id='sim1')
setattr(sim, 'sensor_discovery', StaticDeclaredDiscoveryBackend(sensors=('gas_detector',), source='simulator', allow_real_mode=False))
state2 = sim.get_robot_state()
print('sim_state_available=', state2.available_sensors)
print('sim_state_diag=', state2.sensor_diagnostics)
PY
.venv/bin/python -m pytest -q
```

## Observed Results

- Focused Phase 5 tests: `82 passed in 22.11s`.
- Full suite: `1246 passed, 6 skipped in 193.92s`.
- Phase 4 semantic fixes are present:
  - `robot-profile discover` writes `confirmed = false`.
  - `discover` no longer writes `confirmed_by = "robot-profile discover"`.
  - `build_discovery_diff()` returns `needs_confirmation` when `unconfirmed` is non-empty.
- Backend protocol and gateway factory exist:
  - `SensorDiscoveryBackend`
  - `Ros1SensorDiscoveryBackend`
  - `StaticDeclaredDiscoveryBackend`
  - `create_profile_sensor_discovery_backend()`

## Findings

1. Important: `Ros1RobotAdapter.get_robot_state()` accepts a static backend even when it is not allowed for real mode.
   - Probe result: `Ros1RobotAdapter(..., sensor_discovery=StaticDeclaredDiscoveryBackend(... allow_real_mode=False)).get_robot_state()` returned `available_sensors=['rgb_camera']` and diagnostics source `static`.
   - The helper `ensure_real_mode_backend_allowed()` exists and tests pass when called directly, but the real adapter runtime path does not call it.
   - The current profile factory avoids this for normal ROS1 profiles, but the runtime boundary itself is still not fail-closed.

2. Important: simulator gateway attaches a static backend, but `SimulatorRobotAdapter.get_robot_state()` ignores `sensor_discovery`.
   - Probe result after setting `sim.sensor_discovery = StaticDeclaredDiscoveryBackend(sensors=('gas_detector',), source='simulator')`:
     - `state.available_sensors` remained the adapter default `['rgb_camera', 'thermal_camera', 'lidar']`.
     - `state.sensor_diagnostics` remained `None`.
   - This weakens the Phase 5 goal that simulator/dry-run discovery is explicitly labeled through common diagnostics.

## Current Conclusion

The planned tests are green and the main Phase 5 structure is present, but I would not call the final sensor capability grounding fully closed until the two runtime enforcement gaps are fixed. The remaining work is small and should be handled as a Phase 5 cleanup before declaring the five-phase roadmap complete.

## Recommended Next Step

- Call `ensure_real_mode_backend_allowed()` before `Ros1RobotAdapter` consumes a backend report, or validate at adapter construction/assignment boundary.
- Give simulator/dry-run adapters the same backend-report consumption path as ROS1 adapters, or only attach static backend where `get_robot_state()` actually consumes it.
- Add regression tests for both probes above.

## Runtime Enforcement Cleanup Update

- `Ros1RobotAdapter.get_robot_state()` now calls `ensure_real_mode_backend_allowed()` before consuming backend reports.
- Static discovery backends with `allow_real_mode=false` now fail closed in ROS1 real mode.
- `SimulatorRobotAdapter.get_robot_state()` now consumes attached discovery backends and exposes `sensor_diagnostics`.
- Verification:
  - Focused cleanup: 41 passed
  - Probe: `ValueError Static sensor discovery backend is not allowed for real mode` / `sim_state_available= ['gas_detector']` / `sim_state_diag_source= simulator`
  - Full suite: 1248 passed, 6 skipped in 176.91s
