# FireClaw Dry-Run Profile Runtime Fix Plan — 2026-06-13

## Task Goal

Create the next implementation plan after reviewing the profile-driven runtime implementation. The priority is to fix the dry-run mismatch where `GatewayConfig.dry_run=true` does not propagate to ROS1 adapters selected by robot profiles.

## Current Progress

Previous review record:

- `memory/2026-06-13/fireclaw-profile-driven-runtime-review.md`

Review conclusion:

- Profile-driven runtime is mostly correct.
- Full suite passed: `1153 passed, 6 skipped`.
- Main blocker: ROS1 profile + gateway `dry_run=true` may still leave `Ros1RobotAdapter.dry_run=False`, and ROS1 transport can execute because execution uses `robot.dry_run`.

Used CodeGraph for the plan:

- `Ros1Transport.execute()`
- `Ros1RobotAdapter._record_configured_action()`
- `Ros1AdapterConfig`
- related ROS1 adapter tests

## Plan Created

Created:

- `docs/superpowers/plans/2026-06-13-fireclaw-dry-run-profile-runtime-fix-plan.md`

## Planned Tasks

1. Add failing gateway regression proving ROS1 profile + `GatewayConfig.dry_run=True` should set `gateway.robot.dry_run=True`.
2. Propagate gateway dry-run mode to adapters immediately after adapter creation.
3. Make `Ros1RobotAdapter` skip ROS1 transport when `self.dry_run=True`.
4. Refactor profile loading so gateway loads profile once and does not create stores before validation.
5. Stop creating legacy `robots.json` when mission profiles are configured.
6. Add `--robot-profile` support to direct mission CLI commands while keeping `--robot-registry` compatibility.
7. Update docs and example config to explain dry-run versus `--real-run`.
8. Run focused tests, CLI probes, secret scan, and full pytest suite.

## Verification

Ran placeholder scan:

```bash
rg -n "TBD|TODO|implement later|fill in|待定|适当" docs/superpowers/plans/2026-06-13-fireclaw-dry-run-profile-runtime-fix-plan.md
```

Result:

- Exit code 1, no matches.

## Current Conclusion

Do not start ROS1/Gazebo debug as operator-facing workflow until Tasks 1-3 are complete. The plan is intentionally scoped to dry-run safety semantics and profile-driven CLI polish; it does not duplicate the profile/runtime work already implemented.

## Next Recommended Step

Execute the plan from Task 1. The highest-value first fix is:

- test `FireClawGateway(GatewayConfig(robot_profile_path=ros1_profile, dry_run=True)).robot.dry_run is True`;
- then apply `GatewayConfig.dry_run` to the adapter;
- then ensure `Ros1RobotAdapter(dry_run=True)` does not call transport.
