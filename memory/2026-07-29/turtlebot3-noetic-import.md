# TurtleBot3 Noetic Import

## Timestamp

- 2026-07-29, after the corrected manual download.

## Task Goal

Import and validate a small ROS1 Noetic/Gazebo robot platform for testing
FireClaw's `navigate_to_point` Tool against the pinned `move_base` Runtime.

## Manual Download Verification

The user downloaded three clean Git repositories under
`/home/lpp/turtlebot3-noetic-src/`:

- `turtlebot3`, branch `noetic`,
  commit `4ae959ea6a52415c90bf752d2b76e3e28f8a87e2`;
- `turtlebot3_msgs`, branch `noetic`,
  commit `76e78b0a34e07cf1dd16dafdc54c44f35c5b83eb`;
- `turtlebot3_simulations`, branch `noetic`,
  commit `e9d809ca8e3bf889c0275e4103b15a341ffab888`.

All working trees were clean. Key packages use `catkin`:

- `turtlebot3_description 1.2.6`;
- `turtlebot3_navigation 1.2.6`;
- `turtlebot3_msgs 1.0.1`;
- `turtlebot3_gazebo 1.3.2`.

The expected Burger URDF, `move_base.launch`, navigation maps/parameters,
Gazebo launch files, models, and worlds were present.

## Import

Each clean checkout was exported with `git archive` into:

`robots/turtlebot3_burger/ros_ws/src/`

Imported result:

- 11 ROS packages;
- 316 files;
- about 31 MB;
- no nested Git repositories.

Exact upstream source is recorded in:

- `robots/turtlebot3_burger/ros_ws/turtlebot3.repos`;
- `robots/turtlebot3_burger/ros_ws/UPSTREAM.md`.

## Architecture Decision

TurtleBot3 is a Robot Platform, not a FireClaw Plugin:

- `robots/turtlebot3_burger/` owns the model, simulation, sensors, maps, and
  platform navigation configuration;
- `extensions/navigation-move-base/` owns the reusable navigation Runtime and
  Plugin boundary;
- FireClaw core owns Tool policy, safety, authorization, resource leases, and
  Robot Adapter dispatch.

Trusted deployment/operator bringup starts Gazebo and ROS nodes. The LLM must
not execute arbitrary launch commands.

## Dependency Check

Command:

`rosdep check --from-paths robots/turtlebot3_burger/ros_ws/src --ignore-src`

Missing system keys:

- `ros-noetic-rosserial-python`;
- `ros-noetic-hls-lfcd-lds-driver`.

These serve physical TurtleBot3 bringup. Do not install them until the
simulation build proves they are required.

## Next Step

Build the robot overlay on top of the pinned FireClaw Navigation Stack, then
launch Gazebo headlessly or with GUI and validate TF/topics before connecting
FireClaw.

## Build and Runtime Verification

### Initial Build Failure

The first `catkin_make -DCMAKE_BUILD_TYPE=Release` failed while catkin
interrogated `turtlebot3_example/setup.py`:

`error: invalid command 'turtlebot3_example'`

The host has setuptools `75.3.2`, which replaced the standard-library
distutils implementation used by this pinned ROS1 package. No upstream source
change was made.

Successful command:

```bash
export SETUPTOOLS_USE_DISTUTILS=stdlib
source extensions/navigation-move-base/ros_ws/devel/setup.bash
cd robots/turtlebot3_burger/ros_ws
catkin_make -DCMAKE_BUILD_TYPE=Release
```

Result: all 11 packages built to 100%.

### Gazebo Smoke Test

A temporary untracked headless launch loaded the upstream
`turtlebot3_world.world` and Burger Xacro without modifying upstream files.

Observed interfaces:

- `/scan`: `sensor_msgs/LaserScan`, 360 ranges, frame `base_scan`;
- `/odom`: `nav_msgs/Odometry`;
- `/cmd_vel`: `geometry_msgs/Twist`, subscribed by `/gazebo`;
- `/tf`: `tf2_msgs/TFMessage`;
- `/clock`, `/joint_states`, and `/gazebo/model_states`.

A simulation-only `0.1 m/s` velocity command was applied for about one second,
then a zero command was sent. Odom x changed from approximately `-2.000` to
`-1.841`, proving command, physics, and odometry flow.

### Navigation Stack Test

Launched `turtlebot3_navigation.launch` with RViz disabled. Verified:

- `/map_server`;
- `/amcl`;
- `/robot_state_publisher`;
- `/move_base`;
- complete `move_base` Action topics;
- `map -> odom -> base_footprint -> base_link -> base_scan`;
- static and obstacle costmap layers consuming the map and `/scan`;
- DWA local planner initialization.

An initial test goal `(0.5, 0.0)` was incorrectly treated as a relative goal;
`move_base` correctly interpreted it as an absolute `map` pose, attempted
recovery, and was cancelled after a wall-clock timeout. This was a test input
error, not a Runtime failure.

A subsequent actionlib smoke test sent the robot's current absolute pose and
captured:

- state: `3` (`SUCCEEDED`);
- status: `Goal reached.`

The headless simulator can advance ROS simulation time faster than wall time.
Connection and safety timeouts in automated tests must use wall-clock budgets
where appropriate.

All navigation and Gazebo processes were terminated cleanly after testing.

## Current Conclusion

The imported TurtleBot3 source is usable for FireClaw's ROS1 simulation:
source integrity, catkin build, Gazebo sensors, TF, base actuation, localization,
costmaps, planner startup, and move_base Action completion are verified.

`ros-noetic-rosserial-python` and `ros-noetic-hls-lfcd-lds-driver` remain
uninstalled. They are not required for the verified Gazebo path; install them
only when testing physical TurtleBot3 bringup.

## Next Recommended Step

Replace the temporary headless smoke launch with a small trusted platform
bringup package, then connect FireClaw's existing
`Ros1RobotAdapter.navigate_to_point()` to this running `/move_base` Action and
verify authorization, resource leases, cancellation, feedback, timeout, and
audit events end to end.
