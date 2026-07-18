# FireClaw LLM Robot ID Hallucination Debug

## Task Goal

Investigate why mission submission with LLM planner produced `robot_001` and failed with `Robot robot_001 is not registered.`

## Findings

- `fireclaw.toml` currently sets `[planner].type = "llm"`.
- `src/fireclaw_core/planner/llm_planner.py::build_system_prompt()` already includes available robots and explicitly says `robot_id 必须是上面列出的可用机器人之一`.
- The tool schema `MISSION_PLAN_TOOL` currently declares `robot_id` as plain string, not an enum derived from the runtime registry.
- `LLMMissionPlanner._parse_response()` checks unknown robot IDs only when `known_robot_ids` is non-empty:
  - `known_robot_ids = {r.robot_id for r in context.available_robots}`
  - `if known_robot_ids and robot_id not in known_robot_ids: ...`
- `MissionAgent.plan_and_submit()` builds planner context only from online robots:
  - `available_robots=[e for e in self.registry.enabled_entries() if e.robot_id in online_robot_ids]`
- Therefore there are two likely failure modes:
  1. Prompt includes `gazebo_turtlebot3`, but LLM ignored soft instruction and fabricated `robot_001`.
  2. `available_robots` was empty because the main server did not consider the robot online; then prompt says no available robots, internal unknown-ID check is skipped, and deterministic `MissionPlanValidator` later blocks with `Robot robot_001 is not registered.`

## Current Conclusion

This is a real issue to fix. Current behavior is fail-safe because validation blocks the mission, but it is not good enough: LLM must not be allowed to fabricate robot IDs, and no-online-robot context should fail before LLM planning or produce a clear no_robots/clarify result.

## Recommended Fix

- Make LLM robot selection a hard runtime constraint:
  - Build a per-request mission-plan tool schema where `robot_id` is an enum of online robot IDs.
  - Optionally constrain `capability_required` similarly or validate against the selected robot.
- If `available_robots` is empty, return `no_robots`/`clarify` before calling the LLM.
- Remove the `if known_robot_ids` loophole or explicitly reject any subtask when no known robots exist.
- Add regression tests for:
  - prompt/tool schema includes only available robot IDs;
  - LLM returns `robot_001` while `gazebo_turtlebot3` is available -> planner returns error/retry, not a planned invalid mission;
  - no online robots -> LLM is not called.
