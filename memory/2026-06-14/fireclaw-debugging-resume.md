# FireClaw Debugging Resume

## Date

2026-06-14

## Task Goal

Restore recent project memory after profile-driven runtime work and begin debugging the local FireClaw gateway/mission runtime.

## Commands Run

- `find memory -maxdepth 1 -type d | sort | tail -10`
- `git status --short --branch`
- Read recent memory under `memory/2026-06-12/` and `memory/2026-06-13/`.
- `git diff --stat`
- `git diff -- fireclaw.example.toml`
- Read `fireclaw.toml`, `examples/robot_profiles/gazebo_turtlebot3.toml`, relevant config/gateway source, and endpoint snippets.
- CodeGraph context/explore for gateway profile config, mission profile registry, and startup flow.
- Config probe for `fireclaw.toml` before and after local config adjustment.
- Gateway object construction probe without starting HTTP service.
- `.venv/bin/python -m pytest tests/test_gateway_dry_run_profile.py tests/test_gateway_robot_profile_config.py tests/test_mission_runtime_profiles.py tests/test_profile_driven_runtime_e2e.py -q`
- `ss -ltnp | rg ':8765|:8766' || true`
- `curl -sS http://127.0.0.1:8765/health`
- `curl -sS -H 'X-Operator-Scopes: admin' http://127.0.0.1:8765/state`
- `curl -sS -H 'X-Operator-Scopes: admin' http://127.0.0.1:8766/fleet/state`
- `curl -sS -H 'X-Operator-Scopes: admin' http://127.0.0.1:8766/fleet/doctor`
- `ps -p 1223215,1234729 -o pid,ppid,lstart,cmd --no-headers`
- `tail -80 data/robots/gazebo_turtlebot3/events.jsonl`

## Current Progress

- Recent memory shows profile-driven runtime and dry-run profile propagation fixes were implemented and previously full-suite green.
- Current local `fireclaw.toml` contained real provider credentials. Do not copy them into tracked files or chat output.
- Initial local config was inconsistent:
  - `[mission].robot_profiles = ["examples/robot_profiles/gazebo_turtlebot3.toml"]`
  - profile robot id is `gazebo_turtlebot3`
  - `[robot_gateway].robot_id` was `debug-robot-1`
  - `[robot_gateway].profile_path` was missing
- Config probe before adjustment showed `ids_aligned=False`.

## Files Modified

- `fireclaw.toml` only:
  - removed hand-written robot gateway identity/path fields for debugging;
  - added `profile_path = "examples/robot_profiles/gazebo_turtlebot3.toml"`;
  - left `dry_run = true` and provider config intact.

No source code was modified.

## Verification Results

- Post-adjustment config probe:
  - `robot_gateway_profile_path = examples/robot_profiles/gazebo_turtlebot3.toml`
  - mission profile robot ids: `["gazebo_turtlebot3"]`
  - profile path matches mission profile: `True`
- Gateway construction probe:
  - resolved robot id: `gazebo_turtlebot3`
  - resolved adapter: `ros1`
  - resolved ROS1 config: `examples/ros1_configs/gazebo_turtlebot3_move_base.yaml`
  - resolved memory path: `data/robots/gazebo_turtlebot3/memory.jsonl`
  - gateway dry-run: `True`
  - adapter dry-run: `True`
- Focused pytest:
  - `14 passed in 0.69s`

## Runtime State Observed

Existing processes were already listening:

- PID `1223215`: `.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml --robot-profile examples/robot_profiles/gazebo_turtlebot3.toml --real-run`
- PID `1234729`: `.venv/bin/python -m fireclaw_core serve --config fireclaw.toml --data-dir data/debug-gazebo`

Because the robot gateway was explicitly started with `--real-run`, live `/health` and `/state` reported:

- robot id: `gazebo_turtlebot3`
- adapter: `ros1`
- dry_run: `false`

For safety, no new mission was submitted to this running real-run gateway.

Mission side runtime was reachable:

- `/fleet/state` showed one enabled online robot: `gazebo_turtlebot3`.
- `/fleet/doctor` status was `healthy`, with one onboarding warning: no enabled robot declares `emergency_stop`.
- Lifecycle section warned about stale tasks from a previous mission.

Robot event log shows the previous mission reached robot-local planning:

- `robot_agent.plan_requested`
- `robot_agent.plan_accepted`
- `task.structured_received`
- `task.planned`
- safety decision: `block`

The previous run was blocked by safety gate because `search_for_victims` requires unavailable sensor `rgb_camera`. The task result had `dry_run=false` because the gateway process was started with `--real-run`.

## Current Hypothesis

The code path is largely functional through mission planning, profile registry, subagent dispatch, robot-local LLM planning, and safety gating. The immediate debugging blockers are runtime/configuration issues:

1. Running robot gateway was started with `--real-run`, so it is not safe for dry-run smoke submission.
2. Sensor metadata is incomplete for the intended victim search flow: `available_sensors` is empty/null while `search_for_victims` requires `rgb_camera`.
3. `fireclaw.example.toml` is currently modified relative to HEAD and appears to revert profile-driven example fields. Decide later whether to restore it to the profile-driven style.
4. `serve --help` does not expose a direct `--robot-profile` flag even though `[mission].robot_profiles` from config works.

## Next Recommended Step

For safe smoke debugging:

1. Stop or leave aside the existing real-run robot gateway.
2. Restart robot gateway without `--real-run`:
   - `.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml`
3. Restart mission gateway using the same config:
   - `.venv/bin/python -m fireclaw_core serve --config fireclaw.toml`
4. Verify:
   - `curl -sS http://127.0.0.1:8765/health`
   - `curl -sS -H 'X-Operator-Scopes: admin' http://127.0.0.1:8766/fleet/state`
5. Submit a mission only after `dry_run=true` is confirmed.
6. Then handle the expected safety block by either:
   - adding `rgb_camera` to the robot profile/sensor state for simulation, if the simulated perception capability is intended; or
   - keeping the block and treating it as correct safety behavior until a perception adapter is available.

