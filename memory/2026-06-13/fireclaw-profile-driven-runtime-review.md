# FireClaw Profile-Driven Runtime Review — 2026-06-13

## Task Goal

Review the user's completed implementation of the profile-driven runtime plan: profile as source of truth for gateway and mission behavior.

## Current Progress

Checked latest commits:

- `357a9e0 feat: add profile-backed capability skill chains`
- `ef924fc feat: derive robot registry from profiles`
- `40df947 feat: make robot gateway derive runtime config from profile`
- `28c7826 feat: load mission robot registry from profiles`
- `2fd2476 feat: use profile skill chains during mission dispatch`
- `d6d1b46 docs: document profile-driven startup`
- `b7f853a test: cover profile-driven runtime flow`
- `57b380c fix: expose gateway package entrypoint`

Git status still has untracked planning/memory files:

- `docs/superpowers/plans/2026-06-12-fireclaw-robot-capability-profile.md`
- `docs/superpowers/plans/2026-06-13-fireclaw-profile-driven-runtime-plan.md`
- `memory/2026-06-12/fireclaw-debugging-readiness-check.md`
- `memory/2026-06-13/`

## Files/Areas Inspected

- `src/fireclaw_core/agent/robot_profile.py`
- `src/fireclaw_core/agent/robot_registry.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/gateway/serve.py`
- `src/fireclaw_core/gateway/config.py`
- `src/fireclaw_core/mission/mission_runtime.py`
- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/mission/mission_gateway.py`
- `src/fireclaw_core/execution/skills.py`
- `src/fireclaw_core/agent/agent.py`
- `src/fireclaw_core/agent/robot.py`
- `fireclaw.example.toml`
- `examples/robot_profiles/gazebo_turtlebot3.toml`
- `tests/test_gateway_robot_profile_config.py`
- `tests/test_profile_driven_runtime_e2e.py`
- `tests/test_mission_cli.py`
- `tests/test_gateway_robot_agent_cli.py`

Used CodeGraph for:

- profile-driven runtime context
- adapter construction side effects
- mission gateway/mission agent flow
- dry_run flow from gateway config to execution

## Validation Commands

Focused suite:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py tests/test_robot_registry.py tests/test_gateway_robot_profile_config.py tests/test_gateway_structured_task.py tests/test_gateway_robot_agent_cli.py tests/test_mission_runtime_profiles.py tests/test_mission_agent_profile_skill_chains.py tests/test_mission_agent_structured_task.py tests/test_task_contract.py tests/test_mission_cli.py tests/test_profile_driven_runtime_e2e.py -q
```

Result:

- `91 passed in 11.60s`

CLI probes:

```bash
.venv/bin/python -m fireclaw_core --help >/tmp/fireclaw-help.txt
.venv/bin/python -m fireclaw_core robot-gateway --help >/tmp/fireclaw-robot-gateway-help.txt
.venv/bin/python -m fireclaw_core.gateway --help >/tmp/fireclaw-gateway-help.txt
.venv/bin/python -m fireclaw_core serve --help >/tmp/fireclaw-serve-help.txt
.venv/bin/python -m fireclaw_core robot-profile export --profile examples/robot_profiles/gazebo_turtlebot3.toml --output /tmp/fireclaw-profile-robots.json
```

Result:

- All commands exited 0.
- Export produced `robot_id: gazebo_turtlebot3`.

Secret scan:

```bash
rg -n "tp-[A-Za-z0-9]|sk-[A-Za-z0-9]{12,}|api_key\\s*=\\s*\"[^\"]+\"|api-key\\s+\"[^\"]+\"|token-plan" fireclaw.example.toml README.md docs examples src tests -g '!openclaw-main/**'
```

Result:

- Matches are placeholders, test fake keys, and plan examples.
- No real previous `tp-...` API key observed.

Full suite:

```bash
.venv/bin/python -m pytest -q
```

Result:

- `1153 passed, 6 skipped in 151.15s`

## Main Finding

High priority: `GatewayConfig.dry_run` is not applied to the robot adapter. In profile-driven mode, `resolve_gateway_config_with_profile()` applies `adapter=profile.adapter`; the example profile uses `adapter = "ros1"`. `create_robot_adapter("ros1", ...)` returns `Ros1RobotAdapter` with `dry_run=False`. `FireClawGateway.health()` reports `config.dry_run`, but `FireClawGateway.state()` and action execution use `robot.dry_run`.

Evidence:

- `src/fireclaw_core/gateway/gateway.py:104-111` creates the adapter before validation and without applying `config.dry_run`.
- `src/fireclaw_core/agent/robot.py:370-375` gives `Ros1RobotAdapter.dry_run = False`.
- `src/fireclaw_core/execution/skills.py:198-203` passes `dry_run=robot.dry_run` into `RobotActionRuntime`.
- `src/fireclaw_core/agent/robot.py:536-546` executes ROS1 transport when config transport is enabled, independent of gateway dry_run.
- `examples/ros1_configs/gazebo_turtlebot3_move_base.yaml:4-5` has `transport.enabled: true`.

Impact:

- With `fireclaw.example.toml` and `examples/robot_profiles/gazebo_turtlebot3.toml`, a user can believe `dry_run = true` prevents real ROS1 transport, but execution may still call ROS1 transport because the adapter's `dry_run` remains false.

Suggested fix:

- Apply `GatewayConfig.dry_run` to adapters when constructing them, or make adapter creation accept a dry-run override.
- Add regression asserting `FireClawGateway(GatewayConfig(robot_profile_path=ros1_profile, dry_run=True)).robot.dry_run is True` and that a submitted structured task does not call ROS1 transport.
- Keep `--real-run` as the explicit path to set `dry_run=False`.

## Other Findings / Risks

1. Profile validation still happens after adapter construction.
   - For ROS1 the constructor currently loads YAML and creates an adapter object, not live ROS calls, but safety-critical startup should validate profile/static config before binding a runtime adapter where possible.

2. `serve --config` profile wiring is implemented, but direct CLI commands like `plan-mission` and `submit-subtask` still require `--robot-registry` and do not accept profile paths.
   - This is not a bug for the HTTP `serve` workflow, but the CLI surface is inconsistent with "profile is the source of truth."

3. `_ensure_data_dir()` still creates a default `robots.json` even when `[mission].robot_profiles` is used.
   - Runtime uses `agent.registry`, so behavior is correct, but the extra file can confuse users.

4. Tests do not currently cover the dry-run override issue.
   - Existing e2e tests use simulator profiles and do not prove ROS1 dry-run behavior.

## Current Conclusion

Implementation largely achieves the intended profile-driven architecture: gateway config derives from profile, mission registry can derive from profiles, selected robot profile skill chains reach structured task dispatch, and full tests pass.

Do not treat this as ready for ROS1/Gazebo operator-facing debugging until the dry-run/adapter mismatch is fixed.

## Next Recommended Step

Fix the dry-run propagation first, then add a regression test using a ROS1 profile with `dry_run=True`. After that, clean up CLI consistency and the extra `robots.json` template behavior.
