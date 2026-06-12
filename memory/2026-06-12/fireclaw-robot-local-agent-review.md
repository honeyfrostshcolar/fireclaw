# FireClaw Robot-Local Agent Review — 2026-06-12

## Task Goal

Review the user's completed robot-local agent implementation and assess whether FireClaw can now be used for Gazebo debugging with an OpenClaw-like main-agent to robot-local subagent loop.

## Context Restored

- Read recent memory:
  - `memory/2026-06-12/fireclaw-robot-local-agent-planning.md`
  - `memory/2026-06-12/fireclaw-structured-robotagent-review.md`
- Checked current diff:
  - `src/fireclaw_core/planner_builder.py`
  - `tests/test_gateway_structured_task.py`
- Inspected implementation structure with CodeGraph:
  - `src/fireclaw_core/robot_agent.py`
  - `src/fireclaw_core/gateway.py`
  - `src/fireclaw_core/agent.py`
  - `src/fireclaw_core/subagent_client.py`
  - `src/fireclaw_core/mission_agent.py`
  - `src/fireclaw_core/mission_scheduler.py`
  - `src/fireclaw_core/gazebo_smoke.py`

## Observed Implementation

- `RobotAgentRuntime`, `RobotAgentPolicy`, `LLMRobotAgentPlanner`, and `DeterministicRobotAgentPlanner` exist.
- `FireClawGateway` has robot-agent config fields and CLI flags:
  - `--robot-agent`
  - `--robot-agent-planner {deterministic,llm}`
  - `--robot-agent-provider-base-url`
  - `--robot-agent-provider-api-key`
  - `--robot-agent-model`
- Robot-local LLM planning is used only when the robot gateway receives a `structured_task`.
- Mission-level dispatch does send `structured_task` through `RobotSubagentClient.submit_task()` when using `MissionSubtask` / `MissionScheduler`.
- `FireClawAgent.run_planning_result()` executes precomputed planning results through the normal SafetyGate and PlanExecutor.
- High/critical risk robot-local plans return a clarify/approval-required path and do not execute directly.

## Verification Run

- Focused robot-local agent suite:
  - `.venv/bin/python -m pytest tests/test_robot_agent_contract.py tests/test_robot_agent_policy.py tests/test_robot_agent_planner.py tests/test_robot_agent_runtime.py tests/test_robot_agent_fireclaw_agent_execution.py tests/test_gateway_robot_agent_cli.py tests/test_gateway_structured_task.py tests/test_gazebo_smoke.py -q`
  - Result: `32 passed in 2.53s`
- Mission/subagent integration suite:
  - `.venv/bin/python -m pytest tests/test_embodied_mission_e2e.py tests/test_embodied_gateway_e2e.py tests/test_mission_agent_structured_task.py tests/test_subagent_client.py tests/test_planner_builder.py -q`
  - Result: `14 passed in 5.36s`
- CLI probes:
  - `.venv/bin/python -m fireclaw_core.gateway --help`
  - `.venv/bin/python -m fireclaw_core --help`
  - `.venv/bin/python -m fireclaw_core serve --help`
  - `.venv/bin/python -m fireclaw_core mission --help`
  - `.venv/bin/python -m fireclaw_core submit-subtask --help`
- Manual dry-run HTTP loop:
  - Started a dry-run `FireClawGateway` with `robot_agent_enabled=True`.
  - Submitted a `MissionSubtask` through `MissionAgent` and `RobotSubagentClient`.
  - Result:
    - submit status: `accepted`
    - result status: `succeeded`
    - robot-agent events: `robot_agent.plan_requested`, `robot_agent.plan_accepted`
- Full suite:
  - `.venv/bin/python -m pytest -q`
  - Result: `1111 passed, 6 skipped in 147.75s`

## Findings

1. No code-level blocker was found in the dry-run robot-local agent path. The mission-to-robot gateway structured-task loop executes and emits robot-agent events.
2. The LLM robot-local planner path is schema/unit tested with fake provider responses, but no live model call was made in this review.
3. Gazebo was not launched in this environment, so ROS1/Gazebo runtime movement is not verified by this review.
4. `src/fireclaw_core/gazebo_smoke.py` is currently a preparation helper that writes `robots.json`; it does not submit a mission, poll traces, assert `robot_agent.*` events, or produce a proof bundle.
5. `docs/deployment/ros1-gazebo-debugging-guide.md` contains stale or incorrect manual commands:
   - `mission_cli submit-subtask --robot-id ... --adapter-config ...` does not match the current CLI.
   - The current CLI uses `python -m fireclaw_core submit-subtask --robot ... --robot-registry ... --mission-registry ...`.
   - `turtlebo3_navigation.launch` appears to be a typo for `turtlebot3_navigation.launch`.
6. Robot-local LLM planning only triggers for structured task payloads. Direct natural-language POSTs to the robot gateway still use the regular `FireClawAgent.run()` path rather than the robot-local LLM runtime.

## Current Conclusion

The implementation is usable for dry-run debugging and appears ready for manual Gazebo debugging if ROS1/Gazebo and the ROS1 config are set up. It is not yet a polished one-command Gazebo proof workflow because the smoke helper and deployment guide do not fully drive or verify the mission-to-robot-local-agent loop.

## Next Recommended Step

Before relying on this for repeatable experiments, update the Gazebo guide and expand `gazebo_smoke.py` into a real closed-loop smoke runner that:

1. writes/validates `robots.json`;
2. optionally checks robot gateway `/health`;
3. submits a mission command to MissionGateway;
4. polls mission trace and robot trace until terminal;
5. asserts `robot_agent.plan_requested` and either `robot_agent.plan_accepted` or safe fallback/approval events;
6. writes a compact JSON proof artifact under `results/`.
