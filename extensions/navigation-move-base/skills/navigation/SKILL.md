---
name: navigation
description: Single-floor robot navigation using trusted navigation Tools and live execution feedback.
---

# Navigation

Use this Skill for movement on the robot's current two-dimensional map.

## Current Tool

- `navigate_to_point(x, y, yaw, frame_id)`: move to an authorized pose in the
  current map frame.

Do not assume that status-query, cancellation, costmap clearing, multi-floor
transition, or recovery Tools exist until the Plugin has registered them and
the current capability projection exposes them.

## Workflow

1. Use only the target authorized by the task contract.
2. Confirm the Tool is exposed by the current capability projection.
3. Submit one navigation operation and inspect structured feedback.
4. Treat timeout, blocked path, preemption, emergency stop, and unknown outcome
   as distinct states.
5. Do not report completion unless host-validated navigation evidence exists.

## Safety

Never bypass Capability Policy, Safety Gate, exact action authorization,
resource leases, or emergency stop. Do not modify map, footprint, velocity,
costmap, planner-class, or raw recovery parameters through Tool arguments.
