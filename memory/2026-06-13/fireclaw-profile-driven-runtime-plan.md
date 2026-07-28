# FireClaw Profile-Driven Runtime Plan — 2026-06-13

## Task Goal

Plan the next implementation phase for making FireClaw behave more like OpenClaw from the user's perspective: maintain robot profile files once, and let gateway/mission startup derive robot identity, adapter settings, storage paths, robot registry entries, skill chains, and LLM-exposed tools automatically.

## Current Progress

- Read recent memory from 2026-06-12:
  - `memory/2026-06-12/fireclaw-robot-capability-profile.md`
  - `memory/2026-06-12/fireclaw-toml-config-support.md`
  - `memory/2026-06-12/fireclaw-robot-local-agent-planning.md`
- Checked git status:
  - Branch is ahead of origin by 22 commits.
  - Untracked:
    - `docs/superpowers/plans/2026-06-12-fireclaw-robot-capability-profile.md`
    - `docs/superpowers/plans/2026-06-13-fireclaw-profile-driven-runtime-plan.md`
    - `memory/2026-06-12/fireclaw-debugging-readiness-check.md`
    - `src/fireclaw_core/gateway/__main__.py`
- Used CodeGraph on FireClaw:
  - `RobotCapabilityProfile` exists in `src/fireclaw_core/agent/robot_profile.py`.
  - `FireClawGateway.__init__` currently creates adapter/stores before loading profile.
  - `structured_task_from_mission_subtask()` already accepts `capability_skill_chains`.
  - `MissionAgent.submit_subtask()` does not pass capability skill chains.
  - `MissionRuntimePaths` currently loads `RobotRegistry` from `robots.json`.
- Used CodeGraph on `openclaw/` to inspect config/session/gateway analogues. The useful architectural lesson is config/profile-driven runtime composition, not copying UI/session code.

## Plan Created

Created:

- `docs/superpowers/plans/2026-06-13-fireclaw-profile-driven-runtime-plan.md`

The plan is intentionally connection-layer work, not duplicate feature work. It reuses:

- `RobotCapabilityProfile`
- `RobotRegistry`
- `build_robot_skill_tools()`
- `LLMRobotAgentPlanner`
- `structured_task_from_mission_subtask()`
- existing gateway and mission runtime boundaries

## Main Planned Tasks

1. Extend robot profile with `capability_skill_chains`.
2. Derive `RobotRegistry` from profiles while keeping `robots.json` fallback.
3. Make robot gateway resolve and validate profile before adapter/store construction.
4. Let mission runtime load profiles directly from config.
5. Pass selected robot profile skill chains into mission dispatch.
6. Simplify `fireclaw.example.toml` and docs so users maintain profiles rather than duplicated fields.
7. Add profile-driven end-to-end regression.
8. Commit or otherwise handle untracked `src/fireclaw_core/gateway/__main__.py`.

## Verification

Ran:

```bash
rg -n "TBD|TODO|implement later|fill in|待定|适当" docs/superpowers/plans/2026-06-13-fireclaw-profile-driven-runtime-plan.md
```

Result:

- Exit code 1, no matches.

## Current Conclusion

The remaining work is not another planner or robot-agent implementation. The key missing part is making profile the source of truth across startup and dispatch:

- robot gateway: profile should drive adapter, robot id, ROS config, and per-robot storage paths;
- mission runtime: profile should drive robot registry entries;
- mission dispatch: selected robot profile should drive capability-to-skill chains;
- config/docs: `fireclaw.toml` should point to profiles instead of repeating robot fields.

## Next Recommended Step

Execute `docs/superpowers/plans/2026-06-13-fireclaw-profile-driven-runtime-plan.md` task by task. Before code changes, decide whether to commit the existing untracked `src/fireclaw_core/gateway/__main__.py`, because current CLI verification for `python -m fireclaw_core.gateway` depends on it.
