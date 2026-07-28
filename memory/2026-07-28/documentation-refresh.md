# Documentation Refresh

## 2026-07-28 22:31:09 +08

### Task goal

Correct the obsolete OpenClaw reference-directory name and update the root
README after the recent task-planning, execution-recovery, and evidence-fusion
development.

### User decisions

- The local upstream reference directory is `openclaw/`.
- Historical records and active guides must not continue referring to the old
  directory name.
- README must describe current FireClaw behavior rather than the original
  single-agent dry-run prototype.

### Changes

- Replaced every old reference-directory occurrence outside the upstream
  `openclaw/` subtree with `openclaw`.
- Updated root `AGENTS.md`, `AGENTS.zh-CN.md`, `CLAUDE.md`, and
  `CLAUDE.zh-CN.md`.
- Updated historical plans, specifications, architecture notes, and memory
  records containing the obsolete path.
- Corrected two mechanically ambiguous statements in
  `task-planning-capability-audit.md` so they no longer say that `openclaw/`
  differs from itself.
- Reworked the beginning of `README.md` around the current architecture:
  frozen mission snapshots, world-state beliefs, bounded LLM deliberation,
  semantic task graphs, deterministic compilation, completion evidence,
  execution-event revision, checkpoint recovery, and audit memory.
- Added an explicit current-capabilities table, research/integration warning,
  known planning gaps, and the correct `openclaw/` reference policy.
- Replaced the obsolete deterministic-only planner section with the current
  deterministic/LLM hybrid planning and plan-revision flow.
- Expanded the scheduler section to cover evidence validation, same-task
  result recheck, node reconciliation, cancellation fencing, and restart
  checkpoints.
- Corrected README examples that imported modules from obsolete flat package
  paths.
- Corrected module execution examples to use
  `fireclaw_core.mission.mission_cli`,
  `fireclaw_core.devtools.doctor`, and
  `fireclaw_core.infra.operator_console`.
- Checked the live `plan-mission --help` output and replaced obsolete
  `--registry-path`/`--registry-out` examples with
  `--robot-registry`/`--mission-registry`.
- Removed a private Python installation path from environment setup.
- Corrected stale statements about Gateway authentication, ROS1 transport,
  emergency stop, cancellation, and robot-local versus mission-level restart
  recovery.
- Corrected active agent guides that still described ROS2 as the primary
  adapter direction; ROS1 is the current robot-integration target, while the
  ROS2-shaped adapter remains only a legacy compatibility test double.

### Commands and verification

An exact search for the obsolete reference-directory token, excluding the
upstream subtree, returned no matches.

All corrected README import examples were imported in one Python process with:

```text
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -c "..."
```

Result: success.

Also passed:

```text
git diff --check
```

No production Python behavior changed in this documentation task, so the
previous complete result remains the relevant behavioral verification:

```text
1722 passed, 6 skipped
```

### Conclusion and next step

The authoritative guides and README now use the actual `openclaw/` directory
and describe the current FireClaw architecture. Older dated records still
retain their at-the-time statements about whether an upstream tree or
CodeGraph was available in that historical workspace; only the obsolete
directory identifier was corrected.

The next documentation pass should split the large README into a concise root
overview plus focused operator, robot integration, mission planning, and
research evaluation documents. That restructuring was intentionally not mixed
into this correctness refresh.

No commit was requested or made.
