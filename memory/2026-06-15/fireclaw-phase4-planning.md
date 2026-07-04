# FireClaw Phase 4 Planning

## Task Goal

Create an implementation plan for Phase 4: auditable operator confirmation loop for sensor discovery.

## Current Progress

- Reviewed the Phase 4 requirements from the final sensor capability grounding design.
- Inspected current `robot-profile discover` implementation in `src/fireclaw_core/mission/mission_cli.py`.
- Inspected current profile parsing in `src/fireclaw_core/agent/robot_profile.py`.
- Inspected current discovery models in `src/fireclaw_core/sensors/discovery.py`.
- Created the implementation plan:
  - `docs/superpowers/plans/2026-06-15-operator-confirmation-phase4.md`

## Key Design Decisions

- `robot-profile discover` should remain a discovery/suggestion command.
- `robot-profile diff-discovery` should compare runtime discovery against profile rules and fingerprint without modifying the profile.
- `robot-profile confirm-discovery` should be the auditable action that writes `confirmed=true`, `confirmed_by`, `confirmed_at`, and the runtime fingerprint to the profile.
- `SensorMappingRule` needs rule-level `confirmed_by` and `confirmed_at`.
- `DiscoveryFingerprint` should accept `confirmed_at` so profile confirmation can record who confirmed the runtime identity and when.
- Profile TOML rendering and block replacement should move out of `mission_cli.py` into `src/fireclaw_core/agent/profile_discovery.py`.

## Files Inspected

- `docs/superpowers/plans/2026-06-14-sensor-capability-grounding-master-plan.md`
- `docs/superpowers/specs/2026-06-14-sensor-capability-grounding-final-design.md`
- `memory/2026-06-15/fireclaw-phase3-review.md`
- `src/fireclaw_core/mission/mission_cli.py`
- `src/fireclaw_core/agent/robot_profile.py`
- `src/fireclaw_core/sensors/discovery.py`
- `tests/test_mission_cli.py`
- `tests/test_robot_profile.py`
- `tests/test_gateway_robot_profile_config.py`

## Next Recommended Step

Execute `docs/superpowers/plans/2026-06-15-operator-confirmation-phase4.md` from Task 1 through Task 5.

Do not proceed to Phase 5 until:

- focused Phase 4 tests pass;
- full suite passes in the implementation session;
- `confirm-discovery` has been verified to refuse confirmation when runtime fingerprint is unavailable.
