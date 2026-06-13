# FireClaw Sensor Discovery Design Notes

## Date

2026-06-14

## Task Goal

Record the design discussion for future FireClaw sensor discovery work before implementation planning.

## Context

During ROS1/Gazebo debugging, the robot-local task reached `robot_agent.plan_accepted` but was blocked by SafetyGate because `search_for_victims` requires `rgb_camera` and the current robot state did not report that sensor.

The user identified that manually maintaining `available_sensors` in robot profiles is brittle:

- forgetting a sensor causes false SafetyGate blocks;
- misspelled sensor names cause inconsistent checks;
- changing robot hardware requires manual profile edits;
- Gazebo and real robot sensor sets may differ.

## Current Design Direction

Use runtime sensor discovery instead of static `available_sensors` as the source of truth for SafetyGate.

Core distinction:

- `declared`: profile says a mapping/capability is expected or previously confirmed.
- `discovered`: current runtime sees a topic/device that may correspond to a sensor.
- `verified`: current runtime has validated the sensor is present and usable now.

SafetyGate must use only `verified` sensors.

## First-Version Scope

The user confirmed:

- first version should only support ROS1/Gazebo;
- if Gazebo has no real camera topic or no valid camera messages, `search_for_victims` should remain blocked;
- do not use simulated `rgb_camera` to bypass SafetyGate.

Out of scope for first version:

- ROS2 discovery;
- full non-ROS robot discovery;
- silent profile overwrites;
- complex long-term sensor health analytics.

## Proposed Runtime Flow

```text
ROS1 Master
  -> rostopic list
  -> rostopic type <topic>
  -> short message probe / freshness check
  -> Ros1SensorDiscoveryReport
  -> Ros1RobotAdapter.get_robot_state()
  -> RobotState.available_sensors = verified sensors
  -> SafetyGate
```

SafetyGate should not understand ROS topic names directly. ROS-specific discovery stays behind the adapter/sensor-discovery boundary.

## Topic Naming Problem

Different robots may use different topic names for the same physical sensor:

- `/camera/image_raw`
- `/front_camera/image_raw`
- `/camera/color/image_raw`
- `/rgb/image_raw`
- `/usb_cam/image_raw`

The system can discover topics and message types automatically, but ROS topic names do not always encode semantic meaning. For example, `sensor_msgs/Image` proves there is an image stream, but not whether it is front RGB, rear RGB, thermal, depth, or a debug stream.

Therefore:

- keep built-in default mapping rules for common ROS conventions;
- allow profile-specific mapping rules for robot-specific topic names;
- give uncertain mappings a lower confidence or `pending` status instead of treating them as verified.

Example mapping rule:

```toml
[[robot.sensor_discovery.rules]]
topic_pattern = "/front_camera/image_raw"
message_type = "sensor_msgs/Image"
sensor = "rgb_camera"
confirmed = true
```

## Topic Exists Does Not Mean Sensor Works

`rostopic list` only proves that ROS Master knows about a topic. It does not prove the sensor is currently usable.

Possible failure cases:

- topic exists but no messages are published;
- messages are stale;
- messages are malformed or empty;
- rate is too low;
- sensor node is alive but hardware is disconnected.

First version should at least distinguish:

- `discovered`: topic/type found;
- `verified`: topic/type found and a recent valid message was observed;
- `degraded`: topic/type found but message probe failed or timed out;
- `rejected`: topic/type does not match any known or configured rule.

## Safety-Critical Sensors

Some sensors should have stricter validation because failures affect safety:

- `gas_detector`;
- `thermal_camera`;
- potentially `lidar` for navigation and obstacle avoidance.

First version may use a simple freshness check for all sensors. Later versions should support per-sensor health checks:

- minimum message rate;
- timestamp freshness;
- numeric range checks;
- frame id checks;
- stale/frozen-value detection;
- escalate versus block policy.

## Gazebo Versus Real Robot

For this first version, Gazebo should not lower the SafetyGate standard for `search_for_victims`.

Policy:

- Gazebo may use ROS1 discovery and real Gazebo topics;
- if no `rgb_camera` topic is found and verified, `search_for_victims` remains blocked;
- no simulated or profile-declared `rgb_camera` should be used to bypass the block.

Later versions can support simulation-only test modes, but those must be clearly marked and must not silently carry over to real robot mode.

## Automatic Profile Write-Back Discussion

The user asked whether automatic profile write-back should be supported.

Conclusion:

- Do not silently overwrite profiles.
- A future `robot-profile discover` command can generate a suggested profile or discovery rules file.
- Writing back should require explicit user action, such as `--write-profile`.
- Write-back should store confirmed mapping rules, not static `available_sensors`.

Preferred command shape:

```bash
.venv/bin/python -m fireclaw_core robot-profile discover \
  --profile examples/robot_profiles/gazebo_turtlebot3.toml \
  --ros1-config examples/ros1_configs/gazebo_turtlebot3_move_base.yaml \
  --output /tmp/gazebo_turtlebot3.discovered.toml
```

Generated output should be a suggestion that the operator can review.

## Robot Change / Stale Profile Problem

Automatic write-back creates a risk when a profile is reused after changing robot hardware.

Danger cases:

- new robot does not have the old topic;
- new robot has the same topic name and type, but the physical sensor has different semantics;
- topic name and message type match, but camera location/use is different;
- the old sensor existed during onboarding but is currently disconnected.

Future profiles should support a discovery fingerprint or identity binding.

Potential fields:

```toml
[robot.identity]
id = "gazebo_turtlebot3"
adapter = "ros1"
robot_model = "turtlebot3_burger"

[robot.discovery_fingerprint]
created_at = "2026-06-14T00:30:00+08:00"
source = "ros1"
topics_hash = "..."
nodes_hash = "..."
confirmed_by = "operator"
```

Startup behavior:

- recompute current ROS graph fingerprint from topic list, topic types, and optionally nodes;
- compare it with the profile fingerprint;
- if it matches, reuse confirmed mapping rules but still re-verify live data;
- if it does not match, mark confirmed rules as stale or pending;
- safety-critical sensors must not be considered verified solely because of old confirmed rules.

Important distinction:

- `confirmed rule` means an operator once confirmed a semantic mapping for a specific robot/profile context.
- `verified sensor` means the current runtime has checked that the sensor is usable now.

SafetyGate should always use `verified`, not `confirmed`.

## Suggested First Implementation Tasks

1. Define `SensorFinding`, `SensorDiscoveryReport`, and `SensorMappingRule`.
2. Add `sensor_diagnostics` to `RobotState` while keeping `available_sensors` as the SafetyGate input.
3. Implement `Ros1SensorDiscovery` with default mappings and message freshness probe.
4. Integrate discovery into `Ros1RobotAdapter.get_robot_state()`.
5. Return discovery diagnostics from `/state`.
6. Add profile support for `[robot.sensor_discovery]` and `[[robot.sensor_discovery.rules]]`.
7. Add tests for:
   - topic/type found and recent message -> verified;
   - topic/type found but no message -> degraded;
   - degraded `rgb_camera` does not satisfy SafetyGate for `search_for_victims`;
   - profile custom topic rule works.
8. Update the ROS1/Gazebo debugging docs to explain that missing camera topic/message means the block is expected.

## Research-Level Note

This can become a meaningful FireClaw research contribution: converting a robot's runtime communication graph into auditable, verified embodied-agent capabilities. The important research distinction is not just auto-configuration, but safe capability grounding:

```text
static declaration != runtime discovery != verified current capability
```

## Implementation Plan Created

Created:

- `docs/superpowers/plans/2026-06-14-ros1-sensor-discovery.md`

The plan keeps the first implementation scoped to ROS1/Gazebo, requires verified sensors for SafetyGate, adds profile discovery rules, and treats profile write-back as an explicit reviewed action rather than silent mutation.

## Implementation Status

Tasks 1-6 completed:

- `src/fireclaw_core/sensors/discovery.py` — core models: SensorMappingRule, SensorFinding, SensorDiscoveryReport, DEFAULT_SENSOR_MAPPING_RULES, match_sensor_rule
- `src/fireclaw_core/ros/ros1_sensor_discovery.py` — ROS1 discovery with Protocol-based DI, static fakes, CLI implementations
- `src/fireclaw_core/agent/robot_profile.py` — SensorDiscoveryProfileConfig, profile parser
- `src/fireclaw_core/agent/robot.py` — RobotState.sensor_diagnostics, Ros1RobotAdapter.sensor_discovery, get_robot_state() with discovery
- `src/fireclaw_core/gateway/gateway.py` — attach_profile_sensor_discovery() wiring
- `src/fireclaw_core/mission/mission_cli.py` — robot-profile discover command
- Tests: test_sensor_discovery.py, test_ros1_sensor_discovery.py, test_robot_profile.py, test_ros1_adapter_state.py, test_gateway_robot_profile_config.py, test_safety.py, test_mission_cli.py

## Verification Result (2026-06-14)

**Focused tests:** 79 passed (7 sensor discovery related test files)

**Full suite:** 1177 passed, 6 skipped, 1 pre-existing failure (test_serve.py network connectivity)

**Live Gazebo `/state` sensor diagnostics:**
- `sensor_diagnostics` field present in robot state response
- `/scan` topic: matched to `lidar`, status `degraded` (topic exists, no recent message within 2.0s)
- `/imu` topic: matched to `imu`, status `degraded` (topic exists, no recent message within 2.0s)
- 19 other topics: status `rejected` (no sensor mapping rule matched)
- `verified_sensors: []`, `available_sensors: []`
- No camera topic present in Gazebo TurtleBot3 world (expected)

**Mission submission result:**
- Mission gateway started and registered `gazebo_turtlebot3` robot
- Fleet doctor reported robot unreachable (timeout) due to slow `/state` endpoint
- `/state` endpoint takes ~11s because CLI probes (`rostopic echo -n 1`) spawn per-topic subprocess with 2s timeout

**SafetyGate outcome:**
- `available_sensors` is empty (no verified sensors)
- `search_for_victims` would be blocked because `rgb_camera` is not in available_sensors
- SafetyGate regression test confirms this behavior (test_safety.py::test_safety_blocks_search_when_rgb_camera_is_only_degraded)

**Remaining gaps:**
1. CLI probe performance: `Ros1CliGraphProvider.topic_types()` does N+1 subprocess calls (one `rostopic list` + one `rostopic type` per topic). With 20+ topics, `/state` takes 11s. Future optimization: use `rostopic list -p` (includes types in one call) or cache results.
2. Mission gateway fleet health check times out because robot gateway's `/state` is slow. The health endpoint (`/health`) is fast (5ms), but the fleet doctor may call `/state` instead.
3. Gazebo TurtleBot3 default world has no camera topic. To test verified `rgb_camera`, a camera sensor must be added to the Gazebo world or a different robot profile used.
4. No live `robot-profile discover` test was run (requires ROS1 master with actual publishing topics).
