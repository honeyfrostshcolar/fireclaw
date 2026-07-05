# FireClaw Phase 4 Review

## Task Goal

Review the user's implementation of `docs/superpowers/plans/2026-06-15-operator-confirmation-phase4.md`.

## Current Progress

Reviewed commits:

- `40d3479 feat: add sensor discovery confirmation audit metadata`
- `a04225b feat: add profile discovery confirmation helpers`
- `803171a feat: add robot profile discovery diff command`
- `000b776 feat: add robot profile confirm-discovery CLI command`
- `0674eae docs: document discovery confirmation workflow`

## Commands Executed

```bash
git status --short && git log --oneline -12
git diff --stat 8581138..HEAD && git diff --name-only 8581138..HEAD
.venv/bin/python - <<'PY'
from fireclaw_core.agent.profile_discovery import build_discovery_diff
from fireclaw_core.sensors.discovery import DiscoveryFingerprint, FingerprintComparison, SensorDiscoveryReport, SensorFinding, SensorMappingRule
report = SensorDiscoveryReport(
    findings=(SensorFinding(sensor='lidar', topic='/scan', message_type='sensor_msgs/LaserScan', status='verified', confidence=0.99, source='ros1'),),
    runtime_fingerprint=DiscoveryFingerprint(source='ros1', topics_hash='sha256:abc'),
    profile_fingerprint=DiscoveryFingerprint(source='ros1', topics_hash='sha256:abc'),
    fingerprint_comparison=FingerprintComparison(status='fresh'),
)
rules=(SensorMappingRule(topic_pattern='/scan', message_type='sensor_msgs/LaserScan', sensor='lidar', source='profile', confirmed=False),)
print(build_discovery_diff(report, rules).to_dict())
PY
.venv/bin/python -m pytest tests/test_sensor_discovery.py tests/test_robot_profile.py tests/test_profile_discovery.py tests/test_mission_cli.py tests/test_ros1_sensor_discovery.py tests/test_gateway_robot_profile_config.py tests/test_ros1_adapter_state.py -q
.venv/bin/python -m pytest -q
```

## Observed Results

- Focused Phase 4 test set: `99 passed in 22.12s`.
- Full suite: `1237 passed, 6 skipped in 190.38s`.
- Probe for unconfirmed but otherwise fresh profile rule returned:

```python
{
    'status': 'fresh',
    'added': [],
    'removed': [],
    'changed': [],
    'confirmed': [],
    'unconfirmed': ['/scan'],
    'stale_confirmation': False,
    'fingerprint_status': 'fresh',
}
```

## Findings

1. Important: `robot-profile discover` still writes confirmation-shaped data.
   - `src/fireclaw_core/mission/mission_cli.py` still renders `confirmed_by = "robot-profile discover"` in discovery output and `confirmed = true` for verified findings.
   - This conflicts with the Phase 4 semantic requirement that `discover` produces candidates and `confirm-discovery` is the auditable action that writes confirmed authority.
   - Existing tests still assert the old discover behavior, so the suite passes while the workflow semantics remain mixed.

2. Important: `diff-discovery` reports `status="fresh"` even when matching rules are unconfirmed.
   - `build_discovery_diff()` puts such topics in `unconfirmed`, but the top-level status ignores `unconfirmed`.
   - A profile with a fresh fingerprint and only unconfirmed rules can look clean at the status level, even though operator confirmation is still required.

## Current Conclusion

The implementation is mechanically green and most Phase 4 pieces are present, but Phase 4 should not be considered semantically complete until these two issues are fixed. Do not proceed to Phase 5 before separating `discover` from confirmation authority and making unconfirmed mappings action-required in diff output.

## Recommended Next Step

Patch Phase 4 before moving on:

- Make `robot-profile discover` output candidate rules with `confirmed = false` and no `confirmed_by`/`confirmed_at` fingerprint authority.
- Keep `confirm-discovery` as the only command that writes `confirmed = true`, `confirmed_by`, `confirmed_at`, and confirmed fingerprint metadata.
- Make `build_discovery_diff()` return `status="changed"` or `status="needs_confirmation"` when `unconfirmed` is non-empty.
- Update tests that currently assert `confirmed_by = "robot-profile discover"`.
