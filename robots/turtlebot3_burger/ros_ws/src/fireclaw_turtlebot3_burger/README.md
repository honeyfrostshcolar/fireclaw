# FireClaw TurtleBot3 Burger integration

This package is the robot-owned base layer used by the Navigation Plugin. It
does not register Tools, contain ROS Navigation algorithms, or duplicate
move_base parameter files. It supplies only the platform bringup that cannot
be shared across robots:

- `launch/robot_base.launch`: Gazebo, the Burger URDF, sensors, odometry and TF;

The root `fireclaw.toml` supplies the map, topics, frames, footprint and
physical limits. The generic
`extensions/navigation-move-base/launch/fireclaw_navigation.launch` starts
map_server, AMCL and move_base and loads Plugin-owned defaults.

The files are based on the pinned TurtleBot3 Burger configuration already
stored in the sibling upstream packages. Keeping this thin package separate
means upstream imports remain unchanged and a real robot replaces only this
base bringup plus the root Profile values.
