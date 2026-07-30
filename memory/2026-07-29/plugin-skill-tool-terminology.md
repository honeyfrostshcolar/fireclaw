# Canonical Plugin, Skill, Tool, Runtime, and Adapter Terminology

## Timestamp

- 2026-07-29: user identified that FireClaw's `Skill` terminology had become
  narrower than OpenClaw's; architecture and repository descriptions were
  corrected.

## User Decision

The user understands Skill as a coherent capability area such as navigation,
including how to navigate, inspect status, cancel, and recover. This
understanding is correct and should be preserved.

Do not describe `navigate_to_point`, `cancel_navigation`, or
`get_navigation_status` as separate complete Skills. They are atomic Tools
that a Navigation Skill may coordinate.

## OpenClaw Evidence Inspected

- OpenClaw expands `SKILL.md` content into Agent context as operational
  instructions/workflows.
- `openclaw/extensions/tavily/index.ts` registers atomic
  `tavily_search` and `tavily_extract` Tools.
- `openclaw/extensions/tavily/skills/tavily/SKILL.md` explains when and how to
  combine web search, Tavily search, and extraction.
- OpenClaw Plugins can therefore register multiple Tools and bundle broader
  Skills; Skill and Tool are not aliases.

## Canonical Definitions

- Plugin: installable/deployable package and lifecycle owner; may contribute
  multiple Tools, Skills, services, hooks, adapters, and runtimes.
- Skill: Agent-facing instructions, domain knowledge, and reusable workflow;
  may coordinate multiple Tools and need not itself be executable.
- Tool: one atomic typed operation proposed by the LLM.
- Runtime/Algorithm: implementation that performs work, such as move_base,
  Nav2, a subprocess, perception node, or robot SDK.
- Adapter: trusted translation boundary from Tool arguments to Runtime APIs
  and normalized feedback/results.

## Navigation Example

```text
move_base Navigation Plugin
├── Navigation Skill
├── navigate_to_point Tool
├── future get_navigation_status Tool
├── future cancel_navigation Tool
├── Ros1RobotAdapter
└── move_base Runtime
```

## Legacy FireClaw Names

These existing compatibility identifiers actually model Tools:

- `Skill`
- `SkillRegistry`
- `PhysicalSkillPlugin`
- `*.skill.json`
- `workspace_skills_dir`
- `required_skills` / `allowed_skills`
- `skill.*` lifecycle event names

Do not extend their old semantics into new APIs or documentation. A future
code migration must preserve configuration, task-contract, checkpoint, and
event compatibility.

## Repository Correction

The immediately preceding workspace decision was corrected:

- `extensions/` now stores Plugin packages, Tool implementations, Runtime/
  algorithm source, ROS workspaces, config, launch files, and tests.
- `skills/` is reserved for Agent-facing `SKILL.md` workflows.
- `extensions/navigation-move-base/` is the navigation Plugin package.
- `extensions/navigation-move-base/skills/navigation/SKILL.md` is the bundled
  Navigation Skill.
- `skills/examples/*.skill.json` remains only as a legacy executable Tool
  manifest compatibility fixture.

## Files Updated

- `AGENTS.md`
- `README.md`
- `pyproject.toml`
- `fireclaw.example.toml`
- `docs/architecture/plugin-skill-tool-terminology.md`
- `docs/architecture/fireclaw-agent-terminology.md`
- `docs/architecture/physical-skill-plugin-runtime.md`
- `docs/architecture/plugin-host-agent-harness.md`
- FireClaw/OpenClaw alignment documents
- `extensions/` and `skills/` workspace descriptions/templates
- prior same-day memory records containing the rejected terminology

## Next Step

Before navigation integration, inspect the incoming ROS packages under
`extensions/navigation-move-base/ros_ws/src/`. Then implement/verify atomic
navigation Tools and evolve the Navigation Skill as those Tools become
available. Do not refactor all legacy Python names during ROS integration;
plan that migration separately with compatibility aliases and serialized-data
versioning.

## Verification

- Terminology/path scan found no active reference to the rejected
  `skills/navigation/move_base` layout; the only retained occurrence is inside
  the explicitly superseded memory record.
- Workspace Tool-manifest, example, manifest, physical Tool compatibility, and
  Plugin Host tests: `42 passed`.
- `git diff --check`: passed.
