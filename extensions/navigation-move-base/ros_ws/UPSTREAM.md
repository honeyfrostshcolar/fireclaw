# Upstream ROS Navigation Stack

- Repository: https://github.com/ros-planning/navigation.git
- Branch: `noetic-devel`
- Commit: `f44bb1fc2810399165115cc98b530fe4b9397c18`
- Upstream package version: `1.17.3`
- Snapshot date: `2025-06-04`
- Imported into FireClaw: `2026-07-29`

The source under `src/navigation/` was imported with `git archive`, so it does
not contain a nested Git repository. Package-level license declarations and
source headers are preserved from upstream.

To reconstruct the checkout with `vcstool`:

```bash
cd extensions/navigation-move-base/ros_ws/src
vcs import < ../navigation.repos
```

Do not edit upstream source merely to expose Agent parameters. FireClaw Tool
schemas, validation, authorization, and ROS translation belong in the Plugin,
Tool, and Adapter layers.
