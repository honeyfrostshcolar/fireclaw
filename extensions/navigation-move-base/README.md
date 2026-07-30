# ROS1 move_base Navigation Plugin

This Plugin package connects FireClaw to the ROS1 `move_base` Runtime.

The upstream ROS Navigation Stack is vendored as a pinned source snapshot at
`ros_ws/src/navigation/`. Its exact source is recorded in
`ros_ws/navigation.repos` and `ros_ws/UPSTREAM.md`.

## Contributions

The intended package shape is:

```text
Navigation Plugin
├── Navigation Skill
├── navigate_to_point Tool
├── future get_navigation_status Tool
├── future cancel_navigation Tool
├── Ros1RobotAdapter binding
└── move_base Runtime
```

Current FireClaw already has the legacy `PhysicalSkillPlugin` definition for
the `navigate_to_point` Tool and a `Ros1RobotAdapter.navigate_to_point()`
binding. Do not register a duplicate Tool.

## Runtime Contract

```text
Tool: navigate_to_point
Inputs: x, y, yaw, frame_id
Adapter action: navigate_to_point
ROS action: /move_base
ROS type: move_base_msgs/MoveBaseAction
```

## Layout

- `ros_ws/src/navigation/`: pinned upstream ROS Navigation Stack source.
- `ros_ws/navigation.repos`: reproducible upstream checkout manifest.
- `plugin/`: Plugin activation and lifecycle.
- `tools/`: atomic FireClaw Tool definitions or compatibility bindings.
- `runtime/`: optional thin integration helpers; do not duplicate `move_base`.
- `skills/navigation/SKILL.md`: Agent navigation workflow.
- `tests/`: ROS contract and simulation tests.
- `config/`: optional Plugin-owned parameter overlays.
- `launch/`: optional Plugin-owned simulation or deployment launch files.

`config/` and `launch/` are not required for the initial integration. A robot
deployment may own its launch and navigation parameters outside FireClaw.
Upstream package launch files remain inside `ros_ws/src/navigation/`.

## Runtime Ownership

The recommended real-robot mode is `external`: robot bringup, `systemd`, or a
trusted ROS launch process starts `move_base`; FireClaw checks readiness and
attaches to `/move_base`.

A future `managed` mode may let a trusted Plugin service start an allowlisted
command for simulation. The LLM must never compose arbitrary shell or
`roslaunch` commands, and launch arguments are not Tool parameters.

## Build

From `extensions/navigation-move-base/ros_ws/`:

```bash
source /opt/ros/noetic/setup.bash
catkin_make
```

The system-installed `move_base` remains usable. Sourcing this workspace's
`devel/setup.bash` selects the pinned source build as an overlay.

## Initial Acceptance Criteria

1. `/move_base` exposes `move_base_msgs/MoveBaseAction`.
2. `map -> odom -> base_link` TF is continuous and correctly timestamped.
3. Localization, map, costmaps, and `/cmd_vel` operate without FireClaw.
4. An RViz `2D Nav Goal` succeeds before Agent integration.
5. Action feedback, success, abort, preempt, cancellation, and timeout are
   observable.
6. FireClaw sends a single-floor `map` goal through
   `Ros1RobotAdapter.navigate_to_point`.
7. Emergency stop and `robot_motion`/`local_navigation` leases prevent new
   motion dispatch.
