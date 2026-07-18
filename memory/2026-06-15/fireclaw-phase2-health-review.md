# FireClaw Phase 2 Sensor Health Review

## Task Goal

Review the user's Phase 2 implementation for per-sensor health checks in ROS1/Gazebo sensor discovery before continuing toward the final sensor capability grounding design.

## Current Progress

- Phase 1 ROS1 discovery, profile rules, verified sensors, fingerprint/stale profile work already exists.
- Phase 2 commits reviewed: `7503daf` through `79be5d5`.
- Relevant modules:
  - `src/fireclaw_core/sensors/health.py`
  - `src/fireclaw_core/ros/ros1_sensor_discovery.py`
  - `tests/test_sensor_health.py`
  - `tests/test_ros1_sensor_discovery.py`
  - `tests/test_ros1_adapter_state.py`

## Commands Executed

```bash
git status --short && git log --oneline -8
.venv/bin/python -m pytest tests/test_sensor_health.py tests/test_sensor_discovery.py tests/test_ros1_sensor_discovery.py tests/test_ros1_adapter_state.py tests/test_agent.py::test_agent_blocks_search_when_discovered_camera_health_is_invalid -q
.venv/bin/python - <<'PY'
from fireclaw_core.ros.ros1_sensor_discovery import _count_finite_ranges, _extract_frame_id
from fireclaw_core.sensors.health import SensorObservation, evaluate_sensor_health

scan_empty = '''header:
  seq: 1
  stamp:
    secs: 12
    nsecs: 34
  frame_id: "laser"
angle_min: -1.57
angle_max: 1.57
angle_increment: 0.01
time_increment: 0.0
scan_time: 0.1
range_min: 0.12
range_max: 3.5
ranges: []
intensities: []'''
finite_count = _count_finite_ranges(scan_empty)
print('empty_scan_finite_count=', finite_count)
print('empty_scan_health=', evaluate_sensor_health('lidar', SensorObservation(observed=True, age_seconds=0.0, finite_range_count=finite_count, frame_id=_extract_frame_id(scan_empty))).to_dict())

image_empty = '''header:
  seq: 1
  stamp:
    secs: 12
    nsecs: 34
  frame_id: "camera"
height: 480
width: 640
encoding: "rgb8"
is_bigendian: 0
step: 1920
data: []'''
print('empty_image_payload_size_as_cli=', len(image_empty.strip().encode('utf-8')))
print('empty_image_health=', evaluate_sensor_health('rgb_camera', SensorObservation(observed=True, age_seconds=0.0, payload_size=len(image_empty.strip().encode('utf-8')), frame_id=_extract_frame_id(image_empty))).to_dict())
PY
```

## Observed Results

- Focused Phase 2 test command passed: `35 passed in 0.23s`.
- Boundary probe results:
  - Empty LaserScan ranges produced `empty_scan_finite_count= 10` and `{'sensor': 'lidar', 'status': 'healthy'}`.
  - Empty Image data produced `empty_image_payload_size_as_cli= 149` and `{'sensor': 'rgb_camera', 'status': 'healthy'}`.

## Findings

1. `Ros1CliMessageProbe.observe()` uses the byte size of the whole `rostopic echo` YAML text as `payload_size`. This means an empty `sensor_msgs/Image` with `data: []` still has nonzero YAML text and can be verified as a healthy `rgb_camera`/`thermal_camera`. The existing test only covers a manually constructed `SensorObservation(payload_size=0)`, not real ROS CLI text.

2. `_count_finite_ranges()` counts finite numeric tokens across the whole message whenever `"ranges:"` appears. A `LaserScan` with `ranges: []` can still be counted as having finite ranges because header timestamps, angle metadata, and range bounds are numeric. This can incorrectly verify `lidar`.

## Current Conclusion

The Phase 2 architecture is directionally correct and the focused tests pass, but the real ROS1 CLI parsing layer has two safety-relevant false-positive paths. Do not treat Phase 2 as complete until image data length and LaserScan ranges parsing are fixed and regression tests cover real YAML-like CLI output.

## Next Recommended Step

Patch `src/fireclaw_core/ros/ros1_sensor_discovery.py` so:

- Image payload health uses parsed `data` length, not whole YAML text length.
- LaserScan finite range count only inspects the `ranges` field content.
- Add tests with ROS YAML-style strings for:
  - `sensor_msgs/Image` with `data: []` should be degraded/invalid.
  - `sensor_msgs/Image` with non-empty `data` should be healthy when `frame_id` exists.
  - `sensor_msgs/LaserScan` with `ranges: []` should be degraded/invalid even if metadata has numeric values.
  - `sensor_msgs/LaserScan` with at least one finite range should be healthy.

## Remaining Uncertainty

The current parser is still ad hoc over `rostopic echo` text. A more robust later phase should prefer structured ROS message inspection through Python ROS APIs or a typed probe interface rather than relying on text parsing.

## Parser Fix Update

- Fixed `Ros1CliMessageProbe.observe()` so camera payload size comes from parsed `data` array item count instead of whole YAML text size.
- Fixed LaserScan finite range counting so only the `ranges` field is inspected.
- Added ROS CLI text regression tests for empty/non-empty image data and empty/non-empty laser scan ranges.
- Verification:
  - `.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py tests/test_sensor_health.py -q` (26 passed)
  - `.venv/bin/python -m pytest tests/ -q` (1216 passed, 6 skipped)
