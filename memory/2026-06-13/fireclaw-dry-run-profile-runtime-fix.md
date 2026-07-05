# FireClaw Dry-Run Profile Runtime Fix

## Date: 2026-06-13

## Goal

Ensure profile-driven robot gateways obey `GatewayConfig.dry_run`, so ROS1 profiles never execute ROS transport unless `--real-run` explicitly disables dry-run.

## Problem

`GatewayConfig.dry_run = True` was not propagated to `Ros1RobotAdapter.dry_run`. A user could believe the gateway was dry-run while the ROS1 adapter would still call transport if `config.transport.enabled` was true.

## Changes

### Task 1: Failing Regression Tests
- **File:** `tests/test_gateway_dry_run_profile.py` (new)
- Two tests: `test_profile_gateway_applies_dry_run_to_ros1_adapter` (fails initially) and `test_profile_gateway_real_run_keeps_ros1_adapter_live` (passes)

### Task 2: Propagate Gateway Dry-Run To Adapter
- **File:** `src/fireclaw_core/gateway/gateway.py`
- Added `apply_gateway_dry_run_to_robot(robot, dry_run)` helper function
- Called immediately after `create_robot_adapter()` in `FireClawGateway.__init__`

### Task 3: ROS1 Adapter Skips Transport In Dry-Run
- **File:** `src/fireclaw_core/agent/robot.py`
- Added `if self.dry_run:` early-return in `_record_configured_action()` before transport block
- Returns `RobotActionResult(ok=True, status="succeeded", dry_run=True)` without touching transport
- **File:** `tests/test_robot.py` — added `FailingRos1Transport` and test

### Task 4: Validate Profile Before Store Setup
- **File:** `src/fireclaw_core/gateway/gateway.py`
- Added `load_gateway_robot_profile()` helper
- Refactored `resolve_gateway_config_with_profile()` to accept optional profile arg
- `__init__` now loads profile once, validates before creating stores
- **File:** `tests/test_gateway_robot_profile_config.py` — added `test_invalid_profile_does_not_create_gateway_store_files`

### Task 5: Skip Legacy robots.json For Profile Runtime
- **File:** `src/fireclaw_core/gateway/serve.py`
- `_ensure_data_dir()` now accepts `create_robot_template` kwarg
- `start_server()` passes `create_robot_template=not bool(robot_profiles)`
- **File:** `tests/test_gateway_serve_profiles.py` (new)

### Task 6: Mission CLI Profile Support
- **File:** `src/fireclaw_core/mission/mission_cli.py`
- Added `--robot-profile` argument with `action="append"`
- `--robot-registry` now optional when `--robot-profile` is provided
- **File:** `tests/test_mission_cli.py` — 3 new tests

### Task 7: Docs Clarification
- **Files:** `fireclaw.example.toml`, `README.md`, `docs/deployment/ros1-gazebo-debugging-guide.md`
- Clarified dry-run default and `--real-run` override

## Commits

- `78e40ca` fix: skip ros1 transport during dry-run
- (Task 2 commit) fix: apply gateway dry-run mode to profile adapters
- (Task 4 commit) refactor: load gateway robot profile once before store setup
- (Task 5 commit) fix: skip legacy robot registry template for profile runtime
- (Task 6 commit) feat: allow mission cli to load robot profiles
- `e02a468` docs: clarify profile dry-run behavior

## Test Results

- **Before:** 1153 passed, 6 skipped
- **After:** 1161 passed, 6 skipped (+8 new tests)
- Focused suite: 99 passed
- CLI probes: all exit 0, `plan-mission --help` shows `--robot-profile`
- Secret scan: clean (only pre-existing test fakes)

## Verified Behavior

### Safe default:
```bash
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml
```
- `gateway.config.dry_run = True`
- `gateway.robot.dry_run = True`
- ROS1 transport NOT called
- `/health` and `/state` agree on dry-run status

### Real ROS/Gazebo run:
```bash
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml --real-run
```
- `gateway.config.dry_run = False`
- `gateway.robot.dry_run = False`
- ROS1 transport may execute per `ros1_config.transport.enabled`

## Remaining Risks

- ROS1 high-fidelity or hardware validation (real robot) not yet performed
- `apply_gateway_dry_run_to_robot` uses `setattr` — works for all current adapter dataclasses but is not type-checked at compile time
