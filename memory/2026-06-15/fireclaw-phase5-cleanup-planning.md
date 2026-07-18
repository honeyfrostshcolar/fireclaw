# FireClaw Phase 5 Runtime Enforcement Cleanup Planning

## Task Goal

Create a narrow cleanup plan for the two Phase 5 review findings:

1. ROS1 real runtime can still consume a static backend if manually attached.
2. Simulator adapter ignores attached discovery backend diagnostics.

## Current Progress

Created plan:

- `docs/superpowers/plans/2026-06-15-phase5-runtime-enforcement-cleanup.md`

## Key Design Decisions

- Add a shared `_discovered_sensor_state()` helper in `src/fireclaw_core/agent/robot.py`.
- Call `ensure_real_mode_backend_allowed()` inside the runtime state path, not only from unit tests.
- Re-raise `ValueError` policy violations in `Ros1RobotAdapter.get_robot_state()` so the adapter fails closed instead of falling back to static sensors.
- Add `sensor_discovery` to `SimulatorRobotAdapter` and make `get_robot_state()` consume backend reports just like ROS1.

## Next Recommended Step

Execute the cleanup plan from Task 1 to Task 3, then re-run:

- focused cleanup tests;
- the two original probes;
- full suite.

Do not call the five-phase sensor capability grounding roadmap complete until this cleanup passes.
