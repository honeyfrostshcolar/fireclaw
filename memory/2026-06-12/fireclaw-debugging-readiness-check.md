# FireClaw Debugging Readiness Check — 2026-06-12

## Task Goal

Restore recent project memory before starting debugging, summarize current progress, and identify missing pieces that may block robot-local agent / ROS1 Gazebo debugging.

## Commands Run

- `find memory -maxdepth 1 -type d 2>/dev/null | sort -r | head -20`
- `git status --short --branch 2>/dev/null || true`
- `rg --files memory/2026-06-12 memory/2026-06-11 2>/dev/null | sort`
- `git diff -- fireclaw.example.toml`
- `find . -maxdepth 2 -type f \( -name 'pyproject.toml' -o -name 'package.json' -o -name 'pytest.ini' -o -name 'tox.ini' -o -name 'uv.lock' -o -name 'requirements*.txt' \) | sort`
- Read recent records under `memory/2026-06-12/` and `memory/2026-06-11/`.
- CodeGraph context for current Gazebo smoke helper and mission CLI debugging entry points.
- `.venv/bin/python -m fireclaw_core --help`
- `.venv/bin/python -m fireclaw_core serve --help`
- `.venv/bin/python -m fireclaw_core submit-subtask --help`
- `sed -n '1,260p' src/fireclaw_core/ros/gazebo_smoke.py`
- `rg -n "mission_cli|submit-subtask|turtlebo3|turtlebot3|gazebo_smoke|robot-agent|fireclaw_core" docs/deployment/ros1-gazebo-debugging-guide.md docs/deployment/ros1-deployment-guide.md docs/superpowers/plans/2026-06-11-ros1-gazebo-simulation-debugging-roadmap.md`
- `.venv/bin/python -m pytest tests/test_gazebo_smoke.py tests/test_gateway_robot_agent_cli.py tests/test_gateway_structured_task.py -q`

## Observed State

- Current branch: `master...origin/master [ahead 9]`.
- Dirty tracked file: `fireclaw.example.toml`.
- `fireclaw.example.toml` currently contains a real-looking API key and provider endpoint. This should not remain in a tracked example file.
- Recent memory says robot-local agent implementation exists and was previously full-suite green:
  - `RobotAgentRuntime`
  - deterministic and LLM robot-agent planners
  - gateway robot-agent CLI/config support
  - structured task dispatch from mission to robot gateway
  - `FireClawAgent.run_planning_result()`
  - safety gate blocks/escalates high/critical risk local plans
- Source code has already been reorganized into domain subpackages under `src/fireclaw_core/`.
- Current CLI exposes:
  - `serve`
  - `mission`
  - `submit-subtask`
  - `lifecycle-check`
  - memory/replay/approval/event commands
- `serve --help` exposes robot-agent and LLM provider flags.

## Verification

- Focused readiness tests:
  - `.venv/bin/python -m pytest tests/test_gazebo_smoke.py tests/test_gateway_robot_agent_cli.py tests/test_gateway_structured_task.py -q`
  - Result: `7 passed in 2.49s`

## Missing / Risk Items Before Debugging

1. `fireclaw.example.toml` contains a real-looking API key. Replace it with a placeholder and put real credentials only in ignored `fireclaw.toml` or environment variables.
2. `src/fireclaw_core/ros/gazebo_smoke.py` is only a preparation helper:
   - writes `robots.json`;
   - does not health-check robot or mission gateway;
   - does not submit a mission;
   - does not poll traces;
   - does not assert `robot_agent.*` events;
   - does not write a proof artifact.
3. `docs/deployment/ros1-gazebo-debugging-guide.md` still contains stale commands:
   - `python -m fireclaw_core.mission_cli submit-subtask --robot-id ... --adapter-config ...`
   - `turtlebo3_navigation.launch` typo
   - references to `mission_cli register-robot`, which is not in current top-level help
   - robot gateway command path likely needs to be updated after package reorganization.
4. Live LLM robot-agent planning has unit tests with fake provider responses, but no live provider call has been verified in this session.
5. Gazebo/ROS1 runtime movement has not been verified in this session.

## Current Conclusion

FireClaw is ready for dry-run robot-local agent debugging. Before serious Gazebo debugging or experiment evidence collection, fix the credential leak in the example config and update the debugging guide/smoke workflow so the manual commands match the current CLI.

## Next Recommended Step

1. Replace the tracked example API key with a placeholder.
2. Create or verify local ignored `fireclaw.toml` for real provider credentials.
3. Update the Gazebo debugging guide to use current commands.
4. Expand `gazebo_smoke.py` into a closed-loop smoke runner, or run the manual equivalent:
   - start robot-local gateway;
   - start mission gateway;
   - write `robots.json`;
   - submit mission/subtask;
   - poll robot/mission traces;
   - assert `robot_agent.plan_requested` and `robot_agent.plan_accepted` or an explicit safe fallback/approval event.

## Update 2026-06-12 — Robot Gateway Config Entry Added

### Task Goal

Reduce robot-local gateway startup friction before debugging. The user wanted to avoid a long command with repeated provider and robot-agent flags because `fireclaw.toml` already contains LLM provider settings.

### Files Modified

- `src/fireclaw_core/gateway/config.py`
  - Added `[robot_gateway]` config loading.
  - Keeps `[robot_agent.provider]` fallback to `[provider]`.
- `src/fireclaw_core/gateway/gateway.py`
  - Added `--config`.
  - CLI args override config values.
  - Existing flags remain usable for one-off overrides.
- `src/fireclaw_core/gateway/__main__.py`
  - Restored `python -m fireclaw_core.gateway`.
- `src/fireclaw_core/mission/mission_cli.py`
  - Added top-level `robot-gateway` forwarding command.
- `src/fireclaw_core/__main__.py`
  - Routed `python -m fireclaw_core robot-gateway`.
- `tests/test_gateway_robot_agent_cli.py`
  - Added coverage for `robot-gateway --help`, package module `--help`, and `[robot_gateway]` config loading.
- `fireclaw.example.toml`
  - Restored as a placeholder-only tracked example and added `[robot_gateway]`.
- `README.md`
  - Updated stale robot gateway startup command.
- Local ignored `fireclaw.toml`
  - Added `[robot_gateway]` local simulator debugging settings without changing provider credentials.

### Verification

- RED before implementation:
  - `.venv/bin/python -m pytest tests/test_gateway_robot_agent_cli.py -q`
  - Result: `3 failed, 1 passed`
  - Expected failures:
    - `robot-gateway --help` did not expose gateway flags.
    - `python -m fireclaw_core.gateway --help` failed due missing `__main__.py`.
    - config loader did not return `robot_gateway_*` keys.
- GREEN after implementation:
  - `.venv/bin/python -m pytest tests/test_gateway_robot_agent_cli.py -q`
  - Result: `4 passed in 0.35s`
- Broader relevant suite:
  - `.venv/bin/python -m pytest tests/test_gateway_robot_agent_cli.py tests/test_mission_cli.py -q`
  - Result: `29 passed in 7.74s`
- CLI probes:
  - `.venv/bin/python -m fireclaw_core robot-gateway --help`
  - `.venv/bin/python -m fireclaw_core.gateway --help`
  - `.venv/bin/python -m fireclaw_core --help`

### Current Usage

Robot-local gateway can now start with:

```bash
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml
```

Mission gateway can start with:

```bash
.venv/bin/python -m fireclaw_core serve --config fireclaw.toml
```

Current local `fireclaw.toml` uses:

- mission gateway port: `8766`
- robot gateway port: `8765`
- robot id: `debug-robot-1`
- robot gateway adapter: `simulator`
- robot-agent planner: `llm`
- robot-agent provider/model: inherited from `[provider]`

### Next Recommended Step

Start the two gateways from config, write or verify `data/debug-sim/robots.json`, then submit `去二楼救人` through `fireclaw mission`.

## Update 2026-06-12 — Data Directory Layout Adjusted

### User Decision

The user pointed out that robot information should not be mixed in a flat debug directory. Main agent state and each robot-local subagent state should be stored under separate directories.

### Config Changes

- Local ignored `fireclaw.toml`:
  - `[server].data_dir` changed to `./data/mission`.
  - `[robot_gateway].memory_path` changed to `data/robots/debug-robot-1/memory.jsonl`.
  - `[robot_gateway].event_path` changed to `data/robots/debug-robot-1/events.jsonl`.
  - `[robot_gateway].task_queue_path` changed to `data/robots/debug-robot-1/tasks.jsonl`.
  - Comment for `robot_id` now points to `data/mission/robots.json`.
- Tracked `fireclaw.example.toml`:
  - Updated to the same mission/robots split without real provider credentials.

### Intended Layout

```text
data/
  mission/
    robots.json
    missions.jsonl
    memory.jsonl
    memory.sqlite
    tasks.jsonl
    subagents.jsonl
    lineage.jsonl
    flows.jsonl
    approvals.jsonl
  robots/
    debug-robot-1/
      memory.jsonl
      events.jsonl
      tasks.jsonl
```

### Verification

- Ran config load probe:
  - `data_dir='./data/mission'`
  - `robot_gateway_robot_id='debug-robot-1'`
  - `robot_gateway_adapter='ros1'`
  - `robot_gateway_memory_path='data/robots/debug-robot-1/memory.jsonl'`
  - `robot_gateway_event_path='data/robots/debug-robot-1/events.jsonl'`
  - `robot_gateway_task_queue_path='data/robots/debug-robot-1/tasks.jsonl'`

### Note

The user's current local config has `[robot_gateway].adapter = "ros1"`. Starting the robot-local gateway with this value requires a valid `robot_gateway.ros1_config` or `--ros1-config`. For local simulator debugging, set it back to `"simulator"`.

## Update 2026-06-12 — Robot Capability Profile Plan Written

### Task Goal

The user identified that FireClaw should behave more like OpenClaw: LLM/provider config should not require hand-wiring every robot action, and robot skills should be maintained as capabilities/tools that the model can see while the runtime safely maps them to ROS1 bindings.

### OpenClaw References Checked

- `openclaw-main/src/agents/openclaw-tools.ts`
  - `createOpenClawTools(...)` collects tools from runtime config, plugins, workspace, session context, and policy gates before model calls.
- `openclaw-main/src/plugins/tools.ts`
  - plugin tools keep metadata/execution behind a stable runtime boundary.
- `openclaw-main/src/gateway/server-methods/tools-invoke.ts`
  - gateway tool invocation is a controlled boundary, not arbitrary model execution.
- `openclaw-main/openclaw.mjs`
  - launcher config path resolution keeps model/provider config separate from tool availability and runtime state.

### FireClaw Current Gap

- `Skill` metadata already has input/output schemas.
- `tool_schema.py` can serialize skill metadata into OpenAI-style function tool schemas.
- Robot-local LLM currently receives only `create_robot_local_plan`; concrete skills are strings under `allowed_skills`.
- Capability-to-skill mapping is still hard-coded in `task_contract.py`.
- ROS1 remap config is separate from `SkillRegistry` and LLM tool exposure.
- Fleet doctor can warn about missing remaps but does not create a unified profile.

### Plan Created

- `docs/superpowers/plans/2026-06-12-fireclaw-robot-capability-profile.md`

### Plan Scope

The plan is TDD-oriented and split into ten tasks:

1. robot capability profile contract and TOML loader;
2. profile validation against `SkillRegistry` and ROS1 remaps;
3. LLM skill tool generation from `SkillRegistry`;
4. direct concrete skill tool calls in `LLMRobotAgentPlanner`;
5. gateway profile loading and `skill_tools` context;
6. profile-backed capability-to-skills resolution;
7. `robot-profile export` command for `robots.json`;
8. Gazebo TurtleBot3 example profile;
9. documentation/debug workflow updates;
10. focused verification suite.

### Self-Review

- Placeholder scan:
  - `rg -n "TBD|TODO|implement later|fill in|适当|待定|Similar to|Write tests for the above|handle edge cases" docs/superpowers/plans/2026-06-12-fireclaw-robot-capability-profile.md || true`
  - Result: no matches.
- Symbol consistency scan:
  - `rg -n "RobotCapabilityProfile|load_robot_capability_profile|validate_robot_capability_profile|build_robot_skill_tools|local_plan_from_direct_tool_calls|skills_from_capability|robot-profile|robot_gateway_profile_path" docs/superpowers/plans/2026-06-12-fireclaw-robot-capability-profile.md`
  - Result: all planned symbols are introduced before later use.

### Next Recommended Step

Ask the user to choose execution mode:

- Subagent-driven development, recommended because the plan has independent tasks and explicit test boundaries;
- Inline execution in this session using the executing-plans workflow.
