# FireClaw Mission Planning Guard & Audit

**Date:** 2026-07-04
**Status:** Design approved by user; spec written; implementation plan not yet written.

## Task Goal

Design the first FireClaw guardrail/audit framework slice for mission-level planning. The immediate trigger is the LLM mission planner bug where the model can theoretically fabricate robot IDs such as `robot_001`.

## Current Progress

- Reviewed recent project memory from 2026-06-15 and 2026-06-16.
- Confirmed FireClaw has partial safety/checking mechanisms but not a unified audit/check framework.
- Confirmed current LLM mission planner uses function/tool calling.
- Diagnosed root cause:
  - `MISSION_PLAN_TOOL` declares `robot_id` as a plain string.
  - `_parse_response()` only rejects unknown robot IDs when `known_robot_ids` is non-empty.
  - If no online robots are in context, the LLM can produce a fabricated robot ID and the parser currently allows it until a later validator rejects it.
- Used CodeGraph to inspect FireClaw symbols:
  - `src/fireclaw_core/planner/llm_planner.py`
  - `LLMMissionPlanner`
  - `_parse_response`
  - `MISSION_PLAN_TOOL`
  - `MissionAgent.plan_and_submit`
- Used CodeGraph to inspect OpenClaw reference patterns:
  - `createOpenClawTools`
  - `openai-tool-schema.ts`
  - tool schema normalization/strictness helpers
  - tool call ID extraction/validation utilities

## User Decisions

- User agreed that a complete guard/audit framework is important for FireClaw because firefighting robots operate in dangerous environments.
- User selected first-version scope A: mission planning only.
- User approved the recommended lightweight Guard + Audit model over a minimal patch or full generic pipeline.

## Design Spec

Written to:

- `docs/superpowers/specs/2026-07-04-fireclaw-mission-planning-guard-audit-design.md`

The design covers:

- preflight guard for no available robots;
- dynamic tool schema with `robot_id.enum`;
- fail-closed parser guard for unknown robot IDs;
- validator decision capture;
- mission planning audit record;
- required TDD tests;
- future extension to robot-local planning, SafetyGate, execution audit, and append-only audit logs.

## Current Hypothesis

The first implementation should introduce small mission-level guard/audit dataclasses and helper functions rather than a generic policy pipeline. This fixes the immediate `robot_001` bug while creating a clear architecture direction for later safety-critical audit work.

## Next Recommended Step

Follow the Superpowers workflow:

1. Ask the user to review the written spec.
2. If approved, invoke `superpowers:writing-plans`.
3. Write implementation plan under `docs/superpowers/plans/2026-07-04-fireclaw-mission-planning-guard-audit.md`.
4. Implement with TDD:
   - no available robots should not call provider;
   - dynamic schema includes `robot_id.enum`;
   - unknown robot ID blocks at parser guard;
   - valid robot ID proceeds and records validator decision;
   - audit record includes command, available robots, schema, tool call, decisions, final status.

## Important Constraint

Do not commit automatically unless the user explicitly asks. The brainstorming skill suggests committing specs, but repository instructions say commits should only be made when explicitly requested.

## 2026-07-04 Update: Implementation Plan Written

**Timestamp:** 2026-07-04 Asia/Shanghai, current session.

### Current Progress

- Resumed work by reading the newest memory directories:
  - `memory/2026-07-04/fireclaw-mission-planning-guard-audit.md`
  - all `memory/2026-06-16/*.md`
- Checked current git status and confirmed the worktree already has many uncommitted changes from prior work.
- Checked CodeGraph status:
  - 228 indexed files
  - 4155 nodes
  - 10630 edges
  - no pending stale-file banner reported in the responses used.
- Re-read the approved design spec:
  - `docs/superpowers/specs/2026-07-04-fireclaw-mission-planning-guard-audit-design.md`
- Used CodeGraph and focused file reads to inspect:
  - `src/fireclaw_core/planner/llm_planner.py`
  - `src/fireclaw_core/mission/mission_agent.py`
  - `src/fireclaw_core/mission/mission_planner.py`
  - `src/fireclaw_core/mission/mission_plan_validator.py`
  - `tests/test_llm_planner.py`
  - `tests/test_mission_agent.py`

### Files Modified

- Added implementation plan:
  - `docs/superpowers/plans/2026-07-04-fireclaw-mission-planning-guard-audit.md`

### Plan Summary

The implementation plan breaks the work into five tasks:

1. Add `mission_planning_audit.py` with `GuardDecision`, `MissionPlanningAuditRecord`, sink protocol, robot snapshot helper, and immutable decision append helper.
2. Add `MissionPlanningResult.audit_record`, dynamic `robot_id.enum` schema helper, and no-available-robots preflight guard in `LLMMissionPlanner`.
3. Convert `LLMMissionPlanner._parse_response()` into fail-closed parser guard logic with raw tool-call capture and parser allow/block decisions.
4. Wire `MissionAgent` to append `MissionPlanValidator` allow/block decisions and record finalized audit records through an optional sink before dispatch.
5. Run focused and broader regression groups.

### Validation Performed

- Ran placeholder scan on the plan:
  - `rg -n "TBD|TODO|implement later|fill in details|appropriate error handling|Write tests for the above|Similar to Task" docs/superpowers/plans/2026-07-04-fireclaw-mission-planning-guard-audit.md`
  - Result: no matches after fixing a self-review false positive.
- Did not run implementation tests because no production/test implementation has been made yet.

### Current Conclusion

The project is ready for implementation of the mission planning guard/audit slice. The next agent/session should start from the written plan and execute it with TDD. The plan intentionally keeps commits out of the steps unless the user explicitly requests them.

### Next Recommended Step

Use one of:

- `superpowers:executing-plans` for inline execution in the current session, or
- `superpowers:subagent-driven-development` if the user explicitly authorizes subagent-driven work.

Given the current environment and user request to continue working, inline execution is the simplest default unless the user asks for subagents.

## 2026-07-04 Update: Mission Planning Guard/Audit Implemented

**Timestamp:** 2026-07-04 Asia/Shanghai, current session.

### Task Goal

Execute `docs/superpowers/plans/2026-07-04-fireclaw-mission-planning-guard-audit.md` with TDD.

### Files Modified

- Added:
  - `src/fireclaw_core/mission/mission_planning_audit.py`
  - `tests/test_mission_planning_audit.py`
- Modified:
  - `src/fireclaw_core/mission/mission_planner.py`
  - `src/fireclaw_core/planner/llm_planner.py`
  - `src/fireclaw_core/mission/mission_agent.py`
  - `tests/test_llm_planner.py`
  - `tests/test_mission_agent.py`
  - `tests/test_gateway.py`

### Implementation Summary

- Added `GuardDecision`, `MissionPlanningAuditRecord`, `MissionPlanningAuditSink`, `build_available_robot_snapshot()`, `append_guard_decision()`, and `utc_now_iso()`.
- Added optional `audit_record` to `MissionPlanningResult`.
- Added `build_constrained_mission_plan_tool(context)` in `LLMMissionPlanner`.
  - Deep-copies `MISSION_PLAN_TOOL`.
  - Injects `robot_id.enum` from `context.available_robots`.
  - Leaves the base schema unchanged.
- Added no-available-robots preflight guard in `LLMMissionPlanner.plan()`.
  - Does not call provider.
  - Returns `MissionPlanningResult(status="error")`.
  - Adds audit decision `preflight/block/no_available_robots`.
- Refactored `LLMMissionPlanner._parse_response()` to return audit records.
  - Captures raw tool call id, name, and arguments.
  - Blocks missing tool calls, wrong tool name, invalid intent, empty subtasks, invalid subtask shape, missing robot ID, unknown robot ID, missing command, invalid floor, missing capability, and invalid execution group.
  - Unknown robot IDs now fail closed with `unknown_robot_id` regardless of known set emptiness; preflight normally prevents empty set from reaching parser.
  - Valid parser output records `parser/allow/mission_plan_parsed`.
- Wired `MissionAgent` with optional `mission_planning_audit_sink`.
  - Non-planned planner results record planner-provided audit if available.
  - Validator block appends `validator/block/mission_plan_invalid` and records audit before returning blocked.
  - Validator allow appends `validator/allow/mission_plan_valid` and records audit before dispatch.
  - If audit persistence fails for an otherwise allowed plan, mission is blocked before dispatch with message `Mission planning audit could not be recorded.`
- Adjusted `tests/test_gateway.py::_wait_for_task_result()` default timeout from `5.0` to `15.0`.
  - Root-cause investigation showed gateway async tasks completed correctly in focused diagnostics, but the previous 5s helper was flaky under gateway file/full-suite sequencing in this environment.
  - This is a test robustness change only; production gateway behavior was not changed.

### TDD Evidence

Observed expected RED failures before implementation:

- `tests/test_mission_planning_audit.py` failed with `ModuleNotFoundError` before adding audit module.
- New `tests/test_llm_planner.py` schema/preflight tests failed because `build_constrained_mission_plan_tool` did not exist.
- Parser audit tests failed because `MissionPlanningResult.audit_record` was `None`.
- MissionAgent audit tests failed because `MissionAgent.__init__()` did not accept `mission_planning_audit_sink`.

### Verification Commands And Results

- `.venv/bin/python -m pytest tests/test_mission_planning_audit.py -q`
  - `4 passed in 0.02s`
- `.venv/bin/python -m pytest tests/test_llm_planner.py::test_constrained_mission_plan_tool_adds_robot_id_enum_without_mutating_base_tool tests/test_llm_planner.py::test_llm_planner_blocks_without_provider_call_when_no_available_robots -q`
  - `2 passed in 0.05s`
- `.venv/bin/python -m pytest tests/test_llm_planner.py::test_llm_planner_uses_constrained_schema_for_provider_call tests/test_llm_planner.py::test_llm_planner_blocks_unknown_robot_id_with_audit_record tests/test_llm_planner.py::test_llm_planner_records_parser_allow_for_valid_plan -q`
  - `3 passed in 0.06s`
- `.venv/bin/python -m pytest tests/test_mission_agent.py::test_mission_agent_records_validator_allow_decision_before_dispatch tests/test_mission_agent.py::test_mission_agent_records_validator_block_decision_without_dispatching tests/test_mission_agent.py::test_mission_agent_blocks_allowed_plan_when_audit_sink_fails -q`
  - `3 passed in 0.10s`
- `.venv/bin/python -m pytest tests/test_mission_planning_audit.py tests/test_llm_planner.py tests/test_mission_agent.py -q`
  - `94 passed in 0.21s`
- `.venv/bin/python -m pytest tests/test_task_contract.py tests/test_robot_agent_contract.py tests/test_robot_agent_runtime.py tests/test_robot_agent_structured_task.py tests/test_gateway_structured_task.py tests/test_subagent_client.py -q`
  - `43 passed in 8.25s`
- `.venv/bin/python -m pytest tests/test_provider.py tests/test_provider_runtime.py tests/test_llm_planner.py -q`
  - `58 passed in 0.12s`
- First full-suite run:
  - `.venv/bin/python -m pytest -q`
  - Result: `2 failed, 1287 passed, 6 skipped in 207.79s`
  - Failures were gateway async task timeout tests.
- Focused rerun of failed gateway tests:
  - `.venv/bin/python -m pytest tests/test_gateway.py::test_gateway_events_endpoint_returns_recent_events tests/test_gateway.py::test_gateway_events_endpoint_filters_by_task_id -q`
  - One pass/one timeout on first focused rerun, later passed after investigation.
- Diagnostic script showed gateway tasks did complete and wrote terminal events; first task had result by the next 0.5s polling tick in the probe.
- `tests/test_gateway.py` before timeout adjustment:
  - One timeout in `test_gateway_confirms_pending_high_risk_skill`, showing timeout target varied and was test timing related.
- After increasing `_wait_for_task_result()` default timeout to `15.0`:
  - `.venv/bin/python -m pytest tests/test_gateway.py -q`
  - `28 passed in 100.75s`
- Final full-suite run:
  - `.venv/bin/python -m pytest -q`
  - `1289 passed, 6 skipped in 206.70s`

### Current Conclusion

Mission-level planning now has a concrete guard/audit slice:

- LLM planner cannot call the provider with no available robots.
- LLM planner tool schema constrains robot IDs to currently available robots.
- Parser rejects fabricated robot IDs close to the source and records why.
- MissionAgent records validator decisions before dispatch.
- Audit sink failure blocks allowed plans rather than silently allowing unaudited physical work.

This strengthens engineering correctness and research validity for FireClaw's safety-critical planning story. It is still mission-planning-only; future work should extend the same audit pattern to robot-local planning, SafetyGate decisions, and execution/ROS payload logging.

### Remaining Notes

- `data/` remains local untracked runtime/debug output and should not be committed unless explicitly requested.
- No commit has been made for this implementation in this session yet.
