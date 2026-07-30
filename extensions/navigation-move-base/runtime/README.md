# move_base Runtime Integration

The Runtime is ROS1 `move_base`. The trusted boundary is the existing
`Ros1RobotAdapter`, which sends a `MoveBaseGoal` to `/move_base` and returns
normalized feedback and terminal status.

Only add helper code here when message conversion or completion verification
cannot be expressed by the Adapter and endpoint configuration.
