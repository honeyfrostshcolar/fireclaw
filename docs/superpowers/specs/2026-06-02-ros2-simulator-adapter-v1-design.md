# ROS2 / Simulator Adapter v1 Design

## Goal

Move FireClaw from a pure dry-run robot stub toward an embodied robotics adapter boundary by adding explicit robot/environment state snapshots and a deterministic simulator adapter.

## Scope

- Add structured robot state snapshots.
- Add structured environment state snapshots.
- Add `SimulatorRobotAdapter`.
- Add CLI adapter selection:
  - `dry-run`
  - `simulator`
  - `mock-ros2`
- Let the safety gate use robot/environment state for pre-execution blocking.
- Record state snapshots in agent results and memory.

## Non-Goals

- No real ROS2 imports.
- No Gazebo/Ignition/Webots integration yet.
- No map server or navigation stack integration.
- No real hardware side effects.

## Adapter State Contract

Robot adapters expose:

```python
get_robot_state() -> RobotState
get_environment_state() -> EnvironmentState
```

`RobotState` includes:

- `robot_id`
- `mode`
- `dry_run`
- `online`
- `battery_percent`
- `current_floor`
- `available_sensors`
- `supports_real_execution`

`EnvironmentState` includes:

- `reachable_floors`
- `hazards`
- `victims_by_floor`

## Simulator Adapter

`SimulatorRobotAdapter` is deterministic and dependency-free.

It should:

- start on a configurable floor;
- keep `current_floor` updated after navigation;
- simulate victim search results from `victims_by_floor`;
- fail navigation to unreachable floors;
- return structured `RobotActionResult` with `mode="simulator"`.

## Safety State Checks

Before execution, safety should block when:

- robot is offline;
- battery is too low;
- target floor is not reachable in the environment snapshot.

State checks are hard blocks. Operator confirmation must not bypass them.

## Agent / Memory

Normal command results include:

```json
"robot_state": {...},
"environment_state": {...}
```

These snapshots are appended to memory with the task result for later incident replay and experiment analysis.

## Verification

- Robot tests for state snapshots and simulator behavior.
- Safety tests for offline, low battery, and unreachable floors.
- Agent memory test for state snapshots.
- CLI test for `--adapter simulator`.
- Full pytest run and manual simulator CLI demo.
