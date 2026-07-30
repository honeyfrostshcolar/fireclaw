# ROS1 move_base Upstream Import

## Timestamp

- 2026-07-29 22:01:50 +08

## Task Goal

Import an official ROS1 Noetic `move_base` implementation into the
FireClaw navigation Plugin workspace without coupling navigation algorithms to
the Agent core or requiring FireClaw-owned launch files.

## User Decision

The user wants FireClaw to discover and use navigation as a capability, but
does not want every robot launch file and deployment parameter hard-coded into
the Agent framework.

## Environment Observed

- OS: Ubuntu 20.04 Focal
- ROS: ROS1 Noetic
- System packages already installed:
  - `ros-noetic-navigation 1.17.3`
  - `ros-noetic-move-base 1.17.3`
  - `ros-noetic-move-base-msgs 1.14.1`
- An unrelated source checkout also exists at
  `/home/lpp/catkin_ws/src/navigation`; FireClaw does not depend on it.

## Upstream Source

- ROS Index analogue inspected:
  `https://index.ros.org/r/navigation/`
- Repository: `https://github.com/ros-planning/navigation.git`
- Branch: `noetic-devel`
- Commit: `f44bb1fc2810399165115cc98b530fe4b9397c18`
- Package version: `1.17.3`
- Upstream commit date: `2025-06-04`

The source was shallow-cloned to `/tmp`, then imported with `git archive` into
`extensions/navigation-move-base/ros_ws/src/navigation/`. This avoids a nested
Git repository. `navigation.repos` pins the exact source for reconstruction.

## Architecture Decision

- `move_base` is a Runtime, not Agent core code.
- The Navigation Plugin owns Tool registration, Adapter binding, readiness,
  lifecycle metadata, and bundled Navigation Skill instructions.
- Plugin-local `config/` and `launch/` are optional.
- Recommended real-robot mode: externally managed `move_base`; FireClaw checks
  readiness and attaches to `/move_base`.
- Possible future simulation mode: trusted Plugin service starts a fixed,
  allowlisted argv command.
- The LLM may choose `navigate_to_point` and its authorized target, but may not
  generate arbitrary shell/`roslaunch` commands or mutate safety-critical
  navigation parameters.

## Files Modified

- `extensions/navigation-move-base/ros_ws/src/navigation/`
- `extensions/navigation-move-base/ros_ws/navigation.repos`
- `extensions/navigation-move-base/ros_ws/UPSTREAM.md`
- `extensions/navigation-move-base/README.md`
- `extensions/README.md`
- `extensions/_template/README.md`

## Next Step

Build the isolated catkin workspace, then verify that the expected packages and
`move_base` executable resolve from the overlay. Do not attempt Gazebo
navigation until a robot model, TF tree, odometry, laser topic, map, and
deployment-owned parameters are selected.

## Verification Update

- 2026-07-29 22:05 +08
- Command:
  `catkin_make -DCMAKE_BUILD_TYPE=Release`
- Result: passed; all 16 Navigation Stack packages built to 100%.
- Overlay package path:
  `extensions/navigation-move-base/ros_ws/src/navigation/move_base`
- Overlay package version: `1.17.3`
- Built executable:
  `extensions/navigation-move-base/ros_ws/devel/lib/move_base/move_base`
- `git diff --check`: passed.
- `rospack` printed a sandbox-only warning because it could not rewrite
  `~/.ros/.rospack_cache`; package resolution still succeeded.
- Catkin-generated `build/`, `devel/`, `install/`, `log/`, and
  `src/CMakeLists.txt` are ignored.

## Current Conclusion

The official `move_base` Runtime is now present and buildable inside the
FireClaw navigation Plugin workspace. This does not yet mean that a complete
robot can navigate: the deployment must still provide robot description,
odometry, laser data, TF, map/localization, costmap/planner parameters, and a
trusted startup process.

## Recommended Next Step

Implement the Plugin runtime readiness contract in `external` mode:

1. verify ROS master connectivity;
2. verify `/move_base` exposes `move_base_msgs/MoveBaseAction`;
3. expose readiness through the Plugin service/Adapter boundary;
4. return `runtime_unavailable` instead of letting the LLM run shell commands;
5. test against the user's upcoming simulator bringup.
