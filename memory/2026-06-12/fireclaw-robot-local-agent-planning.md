# FireClaw Robot-Local Agent Planning — 2026-06-12

## Task Goal

Plan the next FireClaw development phase: an OpenClaw-style multi-agent loop where the main mission agent delegates a bounded task to a robot-local subagent, and the robot-local subagent calls an LLM for local analysis/planning before executing through ROS1/Gazebo.

## Context Restored

- Read recent memory:
  - `memory/2026-06-12/fireclaw-structured-robotagent-review.md`
  - `memory/2026-06-11/fireclaw-embodied-roadmap-review.md`
- Confirmed current CLI state:
  - `python -m fireclaw_core --help` exposes `serve` and `mission`.
  - `python -m fireclaw_core serve --help` supports `--planner llm` and provider args.
  - robot-local ROS1 execution still lives in `python -m fireclaw_core.gateway --adapter ros1 --real-run --ros1-config ...`.
- Inspected OpenClaw analogues using CodeGraph:
  - subagent/session spawn and lifecycle patterns;
  - task-flow registry pattern;
  - gateway client/session dispatch surface;
  - exec approval/follow-up pattern.
- Inspected FireClaw integration points using CodeGraph:
  - `FireClawGateway._execute_agent_task()`
  - `StructuredRobotTask`
  - `planning_result_from_structured_task()`
  - `FireClawAgent`
  - `RuleBasedPlanner`
  - `SafetyGate`

## User Decisions

- User selected option 2 from the previous assessment: OpenClaw-style multi-agent loop.
- User selected implementation shape 1: embed `RobotAgent` inside the robot-local `FireClawGateway` process.
- User selected autonomy model 2: constrained autonomy.
  - The robot-local LLM may plan inside an explicit envelope.
  - It may not expand `allowed_skills`, mutate the target, or execute high-risk actions without escalation.
- User confirmed the proposed architecture, data/interface design, execution flow, error handling, and test strategy.

## Design Written

- Created:
  - `docs/superpowers/specs/2026-06-12-fireclaw-robot-local-agent-design.md`

## Current Design Summary

Runtime chain:

```text
Operator
  -> MissionGateway / MissionAgent
     -> mission-level planner
     -> StructuredRobotTask envelope
     -> RobotSubagentClient
        -> robot-local FireClawGateway
           -> RobotAgentRuntime
              -> RobotAgentPlanner
              -> RobotAgentPolicy
              -> SafetyGate
              -> PlanExecutor
              -> ROS1/Gazebo adapter
```

Key planned types:

- `RobotAgentTaskEnvelope`
- `RobotLocalPlan`
- `RobotLocalPlanStep`
- `RobotAgentPolicy`
- `RobotAgentRuntime`
- optional `GatewayConfig` robot-agent planner/provider fields

Fallback rule:

- If robot-local LLM is disabled, unavailable, invalid, or rejected by policy, fallback to the existing structured-task conversion path.
- Do not fallback around SafetyGate blocks or confirmation requirements.

Planned events:

- `robot_agent.plan_requested`
- `robot_agent.plan_accepted`
- `robot_agent.plan_failed`
- `robot_agent.plan_invalid`
- `robot_agent.policy_rejected`
- `robot_agent.fallback_used`

## Verification / Self-Review

- Ran placeholder scan:
  - `rg -n "TBD|TODO|implement later|fill in|适当|待定" docs/superpowers/specs/2026-06-12-fireclaw-robot-local-agent-design.md`
  - Result: no matches.
- No implementation code was changed.
- No commit was made because repository instructions say to commit only when explicitly requested.

## Next Recommended Step

After user reviews and approves the spec file, invoke `superpowers:writing-plans` and create:

- `docs/superpowers/plans/2026-06-12-fireclaw-robot-local-agent-implementation-plan.md`

The implementation plan should be TDD-oriented and split at least into:

1. robot-local envelope and local plan data types;
2. robot agent policy validation;
3. LLM planner/parser with provider runtime;
4. robot agent runtime fallback behavior;
5. gateway config and CLI flags;
6. gateway integration tests and event assertions;
7. Gazebo smoke runner/proof gate as a non-default validation path.

## Update 2026-06-12 12:06 CST — Implementation Plan Written

### User Decision

The user approved the spec and asked to write the plan directly.

### Plan Created

- `docs/superpowers/plans/2026-06-12-fireclaw-robot-local-agent-implementation-plan.md`

### Plan Scope

The implementation plan is TDD-oriented and split into seven tasks:

1. robot-local agent data contract;
2. robot-agent policy gate;
3. robot-local LLM planner;
4. robot-agent runtime and fallback;
5. gateway integration and CLI flags;
6. reusable `FireClawAgent.run_planning_result()` execution helper;
7. provider runtime helper and Gazebo smoke command.

### Self-Review

- Placeholder scan:
  - `rg -n 'TBD|TODO|implement later|fill in|待定|适当|This step depends' docs/superpowers/plans/2026-06-12-fireclaw-robot-local-agent-implementation-plan.md`
  - Result: no matches.
- Markdown fence scan:
  - `rg -n '^### Task|^```|^````' docs/superpowers/plans/2026-06-12-fireclaw-robot-local-agent-implementation-plan.md | tail -80`
  - Result: code fences appear balanced; nested markdown block uses four-backtick fence.

### Current State

No implementation code has been changed yet. Only planning/spec/memory documents were added.

### Next Recommended Step

Ask the user to choose execution mode:

- Subagent-driven development, recommended by the plan for faster task-by-task implementation and review;
- Inline execution in this session using the executing-plans workflow.
