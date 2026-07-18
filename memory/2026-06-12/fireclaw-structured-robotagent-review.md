# FireClaw Structured RobotAgent Review — 2026-06-12

## Task Goal

Review the user's completed work after the RobotAgent architecture refinement and standalone server commits. Identify bugs, integration gaps, test status, and next recommended fixes.

## Context Read

- Recent memory:
  - `memory/2026-06-11/fireclaw-embodied-roadmap-review.md`
- Current branch status:
  - `master...origin/master [ahead 14]`
  - no dirty tracked code changes at review start
  - untracked docs/plans/specs:
    - `docs/architecture/fireclaw-robotagent-refactor-plan-2026-06-11.zh-CN.md`
    - `docs/superpowers/plans/2026-06-11-fireclaw-robotagent-architecture-refinement-plan.md`
    - `docs/superpowers/plans/2026-06-11-fireclaw-standalone-server-plan.md`
    - `docs/superpowers/specs/2026-06-11-fireclaw-robotagent-architecture-refinement-design.md`
- Current HEAD:
  - `a29ec95 feat: refine robot agent structured task architecture`
- Review range:
  - `origin/master..HEAD`

## Commands Run

- `git status --short --branch`
- `git log --oneline --decorate --max-count=20`
- `git diff --stat origin/master..HEAD`
- `git diff --name-status origin/master..HEAD`
- CodeGraph context for structured task architecture implementation
- Focused tests:
  - `.venv/bin/python -m pytest tests/test_task_contract.py tests/test_robot_agent_structured_task.py tests/test_gateway_structured_task.py tests/test_mission_agent_structured_task.py tests/test_mission_plan_validator.py tests/test_safety_unknown_state.py tests/test_subagent_client.py::test_robot_subagent_client_sends_structured_task_payload tests/test_serve.py tests/test_planner_builder.py -q`
- Broader integration tests:
  - `.venv/bin/python -m pytest tests/test_gateway.py tests/test_mission_agent.py tests/test_mission_scheduler.py tests/test_embodied_gateway_e2e.py tests/test_embodied_eval.py tests/test_embodied_proof_bundle.py -q`
- Full suite:
  - `.venv/bin/python -m pytest -q`
- CLI probe:
  - `.venv/bin/python -m fireclaw_core serve --help`
- Literal scans:
  - `rg -n "battery_percent=0\\.0|reachable_floors=\\[\\]|available_sensors=\\[\\]|EnvironmentState\\(reachable_floors=None|RobotState\\(" src tests`
  - `rg -n "serve|run_server_blocking|planner_builder|project.scripts|console_scripts|fireclaw" pyproject.toml src/fireclaw_core/__main__.py src/fireclaw_core/mission_cli.py tests/test_mission_cli.py tests/test_serve.py`

## Verification Results

- Focused structured-task/server tests:
  - `27 passed in 4.46s`
- Broader gateway/mission/embodied integration tests:
  - `109 passed in 58.83s`
- Full suite:
  - `1068 passed, 6 skipped in 140.72s`

## Findings

1. **Important: ROS1 adapter still returns placeholder state as real values**
   - `src/fireclaw_core/robot.py`
   - `Ros1RobotAdapter.get_robot_state()` still returns `battery_percent=0.0`.
   - `Ros1RobotAdapter.get_environment_state()` still returns `reachable_floors=[]`.
   - This conflicts with the new unknown-state safety semantics. In a ROS1/Gazebo structured task path, SafetyGate will treat these as real low battery / unreachable floor and block execution instead of warning or requiring confirmation.
   - Existing tests do not cover this regression. There is no `tests/test_ros1_adapter_state.py`; current ROS1 state test only checks `supports_real_execution=True`.

2. **Important: SafetyGate cannot yet handle `available_sensors=None`**
   - `src/fireclaw_core/safety.py`
   - `evaluate()` does `set(robot_state.available_sensors)` when `available_sensors` is not explicitly passed.
   - If `RobotState.available_sensors` is changed to `None` as intended by the spec, this will raise `TypeError`.
   - The dataclass type in `src/fireclaw_core/robot.py` is still `available_sensors: list[str]`, not `list[str] | None`, so the implementation is only partially aligned with the design.

3. **Important: standalone server is not exposed as `fireclaw serve`**
   - `src/fireclaw_core/serve.py` exists and has tests for `start_server()`.
   - `pyproject.toml` has no `[project.scripts]`.
   - `src/fireclaw_core/__main__.py` still routes to the old single-command dry-run CLI.
   - `src/fireclaw_core/mission_cli.py` has no `serve` or `mission` subcommands.
   - `.venv/bin/python -m fireclaw_core serve --help` shows the old dry-run agent help, not a serve subcommand.
   - This means the standalone-server spec/plan goal is not complete from an operator perspective.

4. **Moderate: structured task generation still re-parses natural language**
   - `src/fireclaw_core/mission_agent.py`
   - `submit_subtask()` builds `structured_task` by calling `RuleBasedPlanner()._extract_floor(command)`.
   - The mission planner already has `MissionSubtask.floor`, but that value is not passed into `submit_subtask()`.
   - Scheduler and direct submission can still drop back to command-only behavior if the command format changes, even when the mission plan has structured floor/capability data.

5. **Moderate: Gateway accepts invalid structured task payloads asynchronously**
   - `src/fireclaw_core/gateway.py`
   - `/tasks` accepts any dict as `structured_task`; invalid payloads become empty/default fields through `StructuredRobotTask.from_dict()` and fail later inside the task worker.
   - For a formal robot protocol, request-time validation with HTTP 400 would be clearer and safer.

## Current Conclusion

The branch is test-green and the structured task happy path works. However, the ROS1/Gazebo readiness claim is not yet safe because the real ROS1 adapter still uses placeholder state values that SafetyGate interprets as real unsafe conditions. Standalone server support is also only library-level, not the promised operator CLI.

## Next Recommended Step

1. Fix ROS1 unknown-state semantics first:
   - make `Ros1RobotAdapter.get_robot_state().battery_percent` return `None`;
   - make `Ros1RobotAdapter.get_environment_state().reachable_floors` return `None`;
   - update `RobotState.available_sensors` typing and SafetyGate handling for `None`;
   - add focused regression tests for ROS1 adapter state and SafetyGate sensor unknown handling.
2. Wire `fireclaw serve` / `fireclaw mission` into CLI only after the safety fix.
3. Refactor structured task generation so `MissionSubtask` data, not re-parsed command text, is the source for `StructuredRobotTask`.

## Update 2026-06-12 — Review Fixes Implemented

### Task Goal

Fix the review findings: ROS1 unknown-state semantics, structured-task Gateway validation, MissionSubtask-based structured task generation, and CLI exposure for `fireclaw serve` / `fireclaw mission`.

### Files Modified

- `src/fireclaw_core/robot.py`
- `src/fireclaw_core/safety.py`
- `src/fireclaw_core/gateway.py`
- `src/fireclaw_core/mission_agent.py`
- `src/fireclaw_core/mission_scheduler.py`
- `src/fireclaw_core/interactive.py`
- `src/fireclaw_core/mission_cli.py`
- `src/fireclaw_core/__main__.py`
- `pyproject.toml`
- related tests and docs

### Verification

- Focused review-fix tests passed.
- CLI help probes for `serve` and `mission` passed.
- Full suite passed.

### Current Conclusion

The structured RobotAgent path now treats ROS1 unknown state explicitly, rejects malformed structured task payloads at the Gateway boundary, preserves MissionSubtask data as the structured-task source of truth, and exposes standalone operator commands.
