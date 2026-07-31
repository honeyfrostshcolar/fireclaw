---
name: navigation
description: Single-floor robot navigation using trusted navigation Tools and live execution feedback.
---

# Navigation

Use this Skill for movement on the robot's current two-dimensional map.

## Current Tool

- `navigate_to_point(x, y, yaw, frame_id)`: move to an authorized pose in the
  current map frame.
- `move_base_navigation_status`: inspect structured action status.
- `move_base_parameter_catalog`: inspect mode-specific tuning permissions.
- `move_base_get_parameters(scope, names)`: read current typed values.
- `move_base_set_parameters(scope, parameters)`: apply bounded tuning values
  from the catalog.
- `move_base_cancel_navigation(reason)`: cancel the active goal through the
  trusted adapter.
- `move_base_clear_costmaps(scope)`: request a bounded local/global clear.

Do not assume that status-query, cancellation, costmap clearing, multi-floor
transition, or recovery Tools exist until the Plugin has registered them and
the current capability projection exposes them.

## Workflow

1. Use only the target authorized by the task contract.
2. Confirm the Tool is exposed by the current capability projection.
3. Submit one navigation operation and inspect structured feedback.
4. If navigation stalls, query `move_base_navigation_status`, then inspect the
   relevant scope with `move_base_get_parameters` and the ROS diagnostics Tools.
5. In simulation, tune only values returned by `move_base_parameter_catalog`,
   then retry the navigation operation and record the evidence.
6. Treat timeout, blocked path, preemption, emergency stop, and unknown outcome
   as distinct states.
7. Do not report completion unless host-validated navigation evidence exists.

## Safety

Never bypass Capability Policy, Safety Gate, exact action authorization,
resource leases, or emergency stop. Real-mode parameter mutation is disabled by
default and requires exact operator authorization even when enabled. Do not
modify map, footprint, frame/topic bindings, planner class, or hardware limits
through Tool arguments.
