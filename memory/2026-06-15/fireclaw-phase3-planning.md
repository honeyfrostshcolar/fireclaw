# FireClaw Phase 3 Planning

## Task Goal

Create an implementation plan for Phase 3: safety-critical sensor policy, with the Phase 2 ROS1 health parser false-positive fixes included as the first task.

## Current Progress

- Reviewed the master sensor capability grounding plan.
- Reviewed the Phase 2 health review record.
- Inspected current SafetyGate behavior and default skill sensor metadata.
- Created the Phase 3 plan at:
  - `docs/superpowers/plans/2026-06-15-sensor-policy-phase3.md`

## Key Design Decision

The Phase 2 parser fixes should be Task 0 of the Phase 3 plan, not a separate standalone plan, because Phase 3 safety policy depends on trustworthy health diagnostics.

Phase 3 should also update `navigate_to_floor` to require `lidar`; otherwise the planned "lidar degraded blocks navigation" behavior has no skill metadata path into SafetyGate.

## Files Inspected

- `docs/superpowers/plans/2026-06-14-sensor-capability-grounding-master-plan.md`
- `docs/superpowers/specs/2026-06-14-sensor-capability-grounding-final-design.md`
- `memory/2026-06-15/fireclaw-phase2-health-review.md`
- `src/fireclaw_core/safety/safety.py`
- `src/fireclaw_core/execution/skills.py`
- `src/fireclaw_core/sensors/discovery.py`
- `src/fireclaw_core/agent/robot.py`
- `tests/test_safety.py`
- `tests/test_skill_metadata.py`

## Next Recommended Step

Execute `docs/superpowers/plans/2026-06-15-sensor-policy-phase3.md` from Task 0 through Task 3 using either:

- `superpowers:subagent-driven-development`, with review after each task; or
- `superpowers:executing-plans`, inline in the current session.

Do not proceed to Phase 4 until Phase 3 focused tests and the full suite have been run in the implementation session.
