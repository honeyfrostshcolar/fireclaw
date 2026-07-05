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
