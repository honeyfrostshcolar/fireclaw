# TurtleBot3 Burger Simulation Platform

This platform provides a small ROS1 Noetic/Gazebo robot for exercising the
FireClaw navigation Plugin and `navigate_to_point` Tool.

## Boundary

- Robot model, Gazebo world, sensors, maps, and platform navigation parameters:
  this directory.
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
