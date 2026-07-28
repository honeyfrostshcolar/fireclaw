# FireClaw Dry-Run/Profile Runtime Review 2

## Date

2026-06-13

## Goal

Review the user's completed implementation of the dry-run/profile runtime fix plan.

## Current Progress

The implementation covers the previously identified high-priority dry-run bug:

- `FireClawGateway` now loads the robot profile before resolving gateway config.
- Gateway dry-run mode is synchronized onto adapters via `apply_gateway_dry_run_to_robot`.
- `Ros1RobotAdapter._record_configured_action()` returns before ROS1 transport execution when `dry_run=True`.
- Profile-driven MissionGateway startup no longer creates a legacy `robots.json` template.
- Direct mission CLI commands now accept repeated `--robot-profile` arguments and make `--robot-registry` optional when profiles are provided.

## Commands Run

```bash
find memory -maxdepth 2 -type f | sort | tail -20
git status --short --branch
git log --oneline -8
git diff -- src/fireclaw_core/gateway/gateway.py src/fireclaw_core/mission/mission_cli.py tests/test_gateway_robot_profile_config.py tests/test_mission_cli.py
sed -n '1,220p' memory/2026-06-13/fireclaw-dry-run-profile-runtime-fix.md
.venv/bin/python -m pytest tests/test_gateway_dry_run_profile.py tests/test_gateway_robot_profile_config.py tests/test_robot.py tests/test_gateway_serve_profiles.py tests/test_mission_cli.py -q
rg -n "tp-[A-Za-z0-9]|sk-[A-Za-z0-9]{12,}|api_key\s*=\s*\"[^\"]+\"|api-key\s+\"[^\"]+\"|token-plan" fireclaw.example.toml README.md docs examples src tests -g '!openclaw/**'
.venv/bin/python -m pytest -q
```

## Results

- Focused tests: `55 passed in 7.50s`.
- Full suite: `1161 passed, 6 skipped in 152.02s`.
- Secret scan: no real `tp-...` key found. Matches are placeholders, test fake secrets, or old plan examples.

## Findings

No blocking correctness issues found.

Minor cleanup:

- `tests/test_gateway_dry_run_profile.py::_write_ros1_profile(tmp_path, *, dry_run)` accepts `dry_run` but never uses it. This is non-blocking because dry-run is correctly controlled by `GatewayConfig.dry_run`, but the helper signature is misleading.
- `_build_mission_runtime_paths()` uses `Path("robots.json")` as a placeholder when only `--robot-profile` is provided. Runtime does not read it when profiles are present, so this is acceptable, but a future cleanup could make `MissionRuntimePaths.robot_registry` optional.

## Current Git Status

Branch is ahead of origin by 34 commits. Remaining modified files:

- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/mission/mission_cli.py`
- `tests/test_gateway_robot_profile_config.py`
- `tests/test_mission_cli.py`

Untracked plan/memory docs remain under `docs/superpowers/plans/` and `memory/`.

## Next Recommended Step

Commit the remaining uncommitted source/test changes if the user wants this implementation preserved as a coherent change set. Optional cleanup: remove the unused `dry_run` parameter from `_write_ros1_profile()` or use it in the helper name/comment to avoid confusion.
