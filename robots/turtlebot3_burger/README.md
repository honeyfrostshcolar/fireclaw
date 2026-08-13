# TurtleBot3 Burger Simulation Platform

This platform provides a small ROS1 Noetic/Gazebo robot for exercising the
FireClaw navigation Plugin and `navigate_to_point` Tool.

## Boundary

- Robot model, Gazebo world, sensors, odometry and base TF: this directory.
- Robot/map bindings such as map path, topics, frames, footprint and physical
  limits: the root `fireclaw.toml`.
- Generic map_server, AMCL, move_base composition and default parameters:
  `extensions/navigation-move-base/`.
- ROS1 Navigation Stack and `move_base` Runtime:
  `extensions/navigation-move-base/`.
- FireClaw Tool registration, policy, safety, authorization, and Adapter:
  FireClaw core and the navigation Plugin.

The LLM does not run these launch commands. They are trusted operator or
deployment bringup procedures; the Robot Agent attaches after readiness checks
pass.

## Source

The exact upstream repositories and commits are recorded in
`ros_ws/turtlebot3.repos` and `ros_ws/UPSTREAM.md`.

## Build

```bash
source /opt/ros/noetic/setup.bash
source extensions/navigation-move-base/ros_ws/devel/setup.bash
export SETUPTOOLS_USE_DISTUTILS=stdlib
cd robots/turtlebot3_burger/ros_ws
catkin_make
source devel/setup.bash
```

`SETUPTOOLS_USE_DISTUTILS=stdlib` is required by the pinned ROS1 package's
legacy `setup.py` when the host has a modern setuptools release. It affects
only package interrogation during the build.

## FireClaw deployment reference

This platform now demonstrates the intended thin robot boundary:

- `ros_ws/src/fireclaw_turtlebot3_burger/launch/robot_base.launch` starts
  Gazebo, the Burger model, sensors, odometry and TF;
- root `fireclaw.toml` selects the map and binds the Burger topics, frames,
  footprint, sensor characteristics and motion limits;
- the Navigation Plugin starts map_server, AMCL and move_base and loads its own
  conservative default YAML files.

Copy the committed root example once, then keep robot-local edits in the
ignored root config:

```bash
cp fireclaw.example.toml fireclaw.toml
```

From the repository root, plan and materialize the deployment once:

```bash
fireclaw deploy plan --profile fireclaw.toml
fireclaw deploy apply --profile fireclaw.toml
```

Then start the generated processes in two terminals:

```bash
~/.fireclaw/deployments/gazebo-turtlebot3-burger/current/bin/fireclaw-bringup
~/.fireclaw/deployments/gazebo-turtlebot3-burger/current/bin/fireclaw-gateway
```

The first wrapper composes the robot-owned base launch with the Plugin-owned
navigation launch. The second refuses to start the Gateway until `/scan`,
`/odom`, map_server, AMCL, `/move_base`, `map -> base_footprint`, and the lidar
TF are ready.

## Manual Simulation

```bash
export TURTLEBOT3_MODEL=burger
roslaunch turtlebot3_gazebo turtlebot3_world.launch
```

Before FireClaw integration, verify:

- `/scan`, `/odom`, `/cmd_vel`, and `/clock`;
- `odom -> base_footprint -> base_link -> base_scan` TF;
- Gazebo teleoperation;
- `map -> odom` after localization starts;
- `/move_base` action feedback, success, abort, preemption, and cancellation.
