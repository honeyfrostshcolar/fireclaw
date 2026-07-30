# Robot Capability Workspace (Superseded)

> Superseded later on 2026-07-29 by
> `plugin-skill-tool-terminology.md`. The original decision incorrectly used
> `skills/` for Plugin packages and algorithms. Canonical layout now places
> Plugin/Tool/Runtime/ROS code under `extensions/` and reserves `skills/` for
> Agent-facing `SKILL.md` workflows.

## Task Goal

Create one repository-local home for navigation, scanning, coverage search,
perception, manipulation, and future robot algorithms without introducing a
registry parallel to `FireClawPluginHost`.

## Timestamp

- 2026-07-29: workspace structure and move_base scaffold created.

## OpenClaw Analogue

Inspected OpenClaw's repository-local `extensions/` organization and external
plugin package compatibility contracts. Reused the package-per-capability
shape and the principle that discovery feeds the shared plugin registry.

This conclusion was rejected. `workspace_skills_dir` is a legacy executable
Tool-manifest loader and must not define the meaning of OpenClaw-style Skills.

## Implemented Structure

- The original `skills/` algorithm scaffold was removed.
- Canonical replacement: `extensions/navigation-move-base/`.
- Canonical bundled Skill:
  `extensions/navigation-move-base/skills/navigation/SKILL.md`.
- Root `skills/` now contains Agent-facing workflow templates plus a clearly
  marked legacy `.skill.json` compatibility exception.
- `.gitignore`: ignores capability-local catkin build outputs.
- `README.md` and `fireclaw.example.toml`: document the workspace.

## Architecture Decision

Algorithm source may live in the same repository, but it must not be imported
into the Agent core. Agent tools are contributed through the shared Plugin
Host. Physical ROS capabilities use a `PhysicalSkillPlugin` contract and a
Robot Adapter binding. The algorithm remains independently runnable.

Automatic arbitrary Python plugin loading is not enabled. Current recursive
discovery is limited to subprocess `.skill.json` manifests. Physical
capabilities require explicit trusted activation until compatibility,
signature/trust, and isolation policy are implemented.

## Next Step

Place the incoming navigation stack under
`extensions/navigation-move-base/ros_ws/src/`, then inspect its package, launch,
TF, topic, action, map, and costmap contracts before changing FireClaw
bindings. Validate standalone RViz navigation before Agent execution.
