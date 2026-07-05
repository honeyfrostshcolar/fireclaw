# FireClaw Phase 5 Planning

## Task Goal

Create the final sensor capability grounding implementation plan: Phase 5 adapter-agnostic discovery backends, with the remaining Phase 4 semantic fixes included as Task 0.

## Current Progress

- Reviewed Phase 5 requirements from the final design.
- Reviewed `memory/2026-06-15/fireclaw-phase4-review.md`.
- Inspected current ROS1 sensor discovery attachment in `src/fireclaw_core/gateway/gateway.py`.
- Inspected current robot state discovery consumption in `src/fireclaw_core/agent/robot.py`.
- Created plan:
  - `docs/superpowers/plans/2026-06-15-adapter-agnostic-discovery-phase5.md`

## Key Design Decisions

- Phase 4 semantic fixes should be Task 0 of Phase 5 because the remaining changes are small and directly affect final discovery authority semantics.
- `SensorDiscoveryReport` remains the common data contract.
- Add `SensorDiscoveryBackend.discover() -> SensorDiscoveryReport`.
- Wrap existing `Ros1SensorDiscovery` rather than rewriting it.
- Add a `StaticDeclaredDiscoveryBackend` for simulator/dry-run only.
- Real robot mode must reject static-only discovery backends by default.
- Gateway profile attachment should use a backend factory instead of constructing `Ros1SensorDiscovery` directly.

## Files Inspected

- `docs/superpowers/specs/2026-06-14-sensor-capability-grounding-final-design.md`
- `docs/superpowers/plans/2026-06-14-sensor-capability-grounding-master-plan.md`
- `memory/2026-06-15/fireclaw-phase4-review.md`
- `src/fireclaw_core/agent/robot.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/sensors/discovery.py`
- `src/fireclaw_core/ros/ros1_sensor_discovery.py`
- `tests/test_ros1_adapter_state.py`
- `tests/test_gateway_robot_profile_config.py`
- `tests/test_safety.py`

## Next Recommended Step

Execute `docs/superpowers/plans/2026-06-15-adapter-agnostic-discovery-phase5.md` from Task 0 through Task 5.

Do not claim the final sensor capability grounding roadmap is complete until:

- focused Phase 5 tests pass;
- full suite passes;
- `robot-profile discover` no longer writes confirmed authority;
- static discovery is rejected for real mode;
- gateway attaches discovery through the backend factory.
