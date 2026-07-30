# FireClaw Agent Skills

`skills/` is reserved for Agent-facing instructions and reusable workflows,
following OpenClaw's `SKILL.md` model.

A Skill explains how an Agent performs a coherent class of work. It can
coordinate multiple Tools, describe decision criteria, interpret feedback,
and define recovery or escalation behavior. It is not an algorithm package
and is not synonymous with an atomic Tool.

Example:

```text
skills/navigation/SKILL.md
  -> when navigation is appropriate
  -> how to choose among navigation Tools
  -> how to monitor progress
  -> when to cancel, recover, or escalate
```

Plugin packages, Tool implementations, ROS workspaces, algorithms, launch
files, and deployment configuration belong under `extensions/`.

## Current Compatibility Exception

`skills/examples/*.skill.json` predates the canonical terminology. These files
are executable subprocess **Tool manifests**, even though their legacy suffix
and loader call them skills. They remain here so the current
`workspace_skills_dir` compatibility loader and tests continue to work.

Do not use `.skill.json` as the design model for new OpenClaw-style Skills.
The naming migration for `Skill`, `SkillRegistry`, `.skill.json`, and
`workspace_skills_dir` requires a separate compatibility change.

See:

- `docs/architecture/plugin-skill-tool-terminology.md`
- `extensions/README.md`
