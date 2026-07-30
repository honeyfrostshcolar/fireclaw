# Agent Skill Template

Copy this directory to `skills/<skill-name>/` and replace the placeholders in
`SKILL.md`.

A Skill should document a coherent Agent workflow:

- when it applies;
- which Tools it may use;
- how to sequence or choose Tools;
- how to interpret observations and completion evidence;
- failure recovery, fallback, cancellation, and escalation;
- safety constraints and prohibited behavior.

Do not place plugin code, Tool handlers, ROS packages, algorithms, or heavy
runtime dependencies here. Those belong under `extensions/`.
