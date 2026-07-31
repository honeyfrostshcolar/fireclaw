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
├── move_base_navigation_status Tool
├── move_base_parameter_catalog/get/set_parameters Tools
├── move_base_cancel_navigation Tool
├── move_base_clear_costmaps Tool
├── Plugin-owned ROS1 action adapter
└── move_base Runtime
```

The package is discovered from `fireclaw.plugin.json` and activated through
`plugin/entrypoint.py`. The entrypoint registers all of its Tool contributions
atomically through the generic FireClaw Plugin Host. Gateway code does not name
any move_base Tool or call a move_base-specific registration function.

The old `fireclaw_core.navigation.move_base_plugin` import path is retained as
a compatibility shim for existing tests and integrations; the canonical
catalog, adapters, and Tool contracts are owned by this extension package.

Simulation exposes the finite typed parameter catalog for tuning experiments.
Real mode hides bounded mutation by default, and any explicitly enabled real
parameter still requires exact operator approval.

## Runtime Contract

```text
Tool: navigate_to_point
Inputs: x, y, yaw, frame_id
Plugin action handler: navigate_to_point
ROS action: /move_base
ROS type: move_base_msgs/MoveBaseAction
```

## Layout

- `ros_ws/src/navigation/`: pinned upstream ROS Navigation Stack source.
- `ros_ws/navigation.repos`: reproducible upstream checkout manifest.
- `fireclaw.plugin.json`: manifest read by the generic extension scanner.
- `plugin/entrypoint.py`: provider-owned activation and Tool registration.
- `plugin/move_base.py`: provider-owned parameter catalog and ROS adapters.
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
6. FireClaw sends a single-floor `map` goal through the Plugin-owned action
   adapter.
7. Emergency stop and `robot_motion`/`local_navigation` leases prevent new
   motion dispatch.
