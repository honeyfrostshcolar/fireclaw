# FireClaw Gazebo Mission Debug Run

## Task Goal

Run the user's four-terminal Gazebo -> robot-gateway -> serve -> mission submission flow and report concrete failures.

## Commands / Probes

- Checked ports:
  - `ss -ltnp | rg ':8765|:8766'`
- Checked running processes:
  - `pgrep -af 'roslaunch|roscore|gzserver|gzclient|fireclaw_core robot-gateway|fireclaw_core serve'`
- Checked ROS topics:
  - `source /opt/ros/noetic/setup.bash && rostopic list`
- Checked gateway health:
  - `curl -sS http://127.0.0.1:8765/health`
- Checked mission gateway fleet state/doctor:
  - `curl -sS -H 'X-Operator-Scopes: admin' http://127.0.0.1:8766/fleet/state`
  - `curl -sS -H 'X-Operator-Scopes: admin' http://127.0.0.1:8766/fleet/doctor`
- Submitted mission:
  - `curl -m 30 -sS -X POST http://127.0.0.1:8766/missions -H 'Content-Type: application/json' -H 'X-Operator-Scopes: admin' -d '{"command":"做个短暂的移动","operator":{"operator_id":"nankai","role":"operator"}}'`

## Observed State

- `robot-gateway` was already listening on `127.0.0.1:8765`.
- `serve` was already listening on `0.0.0.0:8766`.
- `robot-gateway /health` returned:
  - `status=ok`
  - `robot_id=gazebo_turtlebot3`
  - `adapter=ros1`
  - `dry_run=true`
- Initially Gazebo was not actually publishing sensor messages:
  - `/scan` and `/imu` existed, but `rostopic info` showed `Publishers: None`.
  - `rostopic echo -n 1 /scan` timed out with `WARNING: no messages received and simulated time is active. Is /clock being published?`
- After starting Gazebo with:
  - `source /opt/ros/noetic/setup.bash && export TURTLEBOT3_MODEL=burger && roslaunch turtlebot3_gazebo turtlebot3_world.launch`
  `/scan` had publisher `/gazebo` and `rostopic echo -n 1 /scan` returned LaserScan data.

## Current Failures

1. `GET /state` on robot-gateway is slow on cold discovery.
   - Direct `/state` took about `9.7s` on the first call.
   - A second immediate `/state` call took about `0.006s`, proving the 5s discovery cache works only briefly.
   - MissionGateway's `SubagentClient` timeout is `5.0s`, so cold `/state` discovery can make the main Agent mark the robot unreachable even though the robot gateway is healthy.

2. LLM mission planner still fabricates robot IDs.
   - Mission submission returned:
     - `status=blocked`
     - `message=Mission plan failed deterministic validation.`
     - `errors=["Robot robot1 is not registered."]`
   - A later submission returned:
     - `errors=["Robot fire_robot_1 is not registered."]`
   - This confirms the prompt-level instruction is insufficient; the LLM planner needs a hard runtime robot_id constraint.

## Current Conclusion

The runtime flow reaches the main Agent and planner, but two issues block useful LLM-mode debugging:

- Presence/state checks are too slow with ROS1 discovery and exceed the main Agent's 5s subagent timeout.
- LLM planner is allowed by schema to output arbitrary `robot_id` strings and relies on later validation to block them.

## Recommended Next Fix

- Make robot-state discovery fast enough for main Agent presence:
  - increase the mission subagent timeout, and/or
  - increase ROS1 discovery cache TTL, and/or
  - avoid full ROS discovery in every `/state` cold path.
- Make LLM robot selection hard-constrained:
  - build per-request tool schema with `robot_id.enum = [online robot IDs]`;
  - return `no_robots` before calling LLM when no online robots are available;
  - reject or retry when LLM outputs a robot ID outside registry.

## 2026-06-16 Update

- Changed `src/fireclaw_core/subagent/subagent_client.py` default `RobotSubagentClient.timeout_seconds` from `5.0` to `15.0`.
- Verification:
  - `.venv/bin/python -m pytest tests/test_mission_agent.py tests/test_mission_cli.py tests/test_gateway_robot_profile_config.py -q`
  - Result: `113 passed in 22.07s`.
- LLM trace investigation:
  - Trace support exists through `--llm-trace-path`.
  - Current `data/debug-gazebo` did not contain an LLM trace file, so the previous `robot1` / `fire_robot_1` LLM calls were not persisted.
  - To capture future LLM planner prompts/tool calls, start `serve` with `--llm-trace-path data/debug-gazebo/llm-traces.jsonl`.

## 2026-06-16 Trace Re-run

- Restarted `serve` with:
  - `--llm-trace-path data/debug-gazebo/llm-traces.jsonl`
  - proxy env vars unset for the main Agent process because `httpx` failed on `socks://127.0.0.1:7890/`.
- Submitted:
  - `做个短暂的移动`
- Main Agent mission planner trace was recorded successfully.
- Trace showed the actual system prompt included:
  - `- gazebo_turtlebot3: 能力=[search_for_victims], 区域=未分配, 状态=已启用`
  - `robot_id 必须是上面列出的可用机器人之一`
- LLM tool call returned:
  - `robot_id = "gazebo_turtlebot3"`
  - `intent = "search"`
  - `command = "短暂移动并搜索受害者"`
  - `floor = 1`
  - `capability_required = "search_for_victims"`
- Therefore this trace run did **not** reproduce robot_id hallucination. It confirmed that with the 15s subagent timeout and an online robot context, the prompt did contain `gazebo_turtlebot3` and the LLM selected it correctly.
- The resulting robot-local task failed later because the `robot-gateway` process still inherited the invalid proxy environment and its robot-local LLM call failed with:
  - `Unknown scheme for proxy URL URL('socks://127.0.0.1:7890/')`

## Updated Diagnosis

- Previous `robot1` / `fire_robot_1` failures cannot be proven from trace because no trace was enabled then.
- The most likely causes were:
  - main Agent saw no online robots due to `/state` timing out under the old 5s subagent timeout; and/or
  - LLM nondeterminism under a soft prompt-only constraint.
- A hard `robot_id` enum is still recommended because prompt-only constraints are not safety-grade, even if this re-run selected the correct robot.

## 2026-06-16 Provider Proxy Fix

- Changed `OpenAICompatProvider` to accept `trust_env: bool = False` and pass it to `httpx.post(...)`.
- This prevents FireClaw LLM calls from implicitly reading process-wide proxy env vars such as:
  - `ALL_PROXY=socks://127.0.0.1:7890/`
- Added tests:
  - default provider calls `httpx.post(..., trust_env=False)`;
  - callers can explicitly opt in with `trust_env=True`.
- Verification:
  - `.venv/bin/python -m pytest tests/test_provider.py tests/test_provider_runtime.py tests/test_llm_planner.py -q`
  - Result: `53 passed in 0.18s`
  - `.venv/bin/python -m pytest tests/test_mission_agent.py tests/test_mission_cli.py tests/test_gateway_robot_profile_config.py -q`
  - Result: `113 passed in 22.98s`
- Probe with the bad `ALL_PROXY` still present:
  - provider call to `http://127.0.0.1:9` now raises normal `ProviderError [Errno 111] Connection refused`;
  - it no longer raises `ValueError: Unknown scheme for proxy URL URL('socks://127.0.0.1:7890/')`.

## 2026-06-16 LLM Primitive Skill Composition

- Implemented plan: `docs/superpowers/plans/2026-06-16-llm-primitive-skill-composition.md`
- All 7 tasks complete, 146 tests passing
- Key changes:
  - Skills have `metadata` field with kind (primitive/composite), primitive_capability, input_schema, safety_class
  - RobotCapabilityProfile has `primitive_skills` field (backward compatible)
  - New `skill_inventory.py` builds OpenClaw-like runtime inventory for LLM
  - MissionAgent has primitive fallback when planner returns "clarify"
  - Robot-local LLM prompt includes skill_inventory and planning_rules
  - Policy rejects empty primitive_composition plans
- This enables the LLM to compose primitive skills (navigate_to_floor, report_status) for unknown commands without needing pre-defined capabilities
- See `memory/2026-06-16/fireclaw-llm-primitive-skill-composition.md` for full details
