# FireClaw Dry-Run Profile Runtime Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure profile-driven robot gateways obey `GatewayConfig.dry_run`, so ROS1 profiles never execute ROS transport unless `--real-run` explicitly disables dry-run.

**Architecture:** Keep the current profile-driven runtime and existing robot adapters. Add a small adapter dry-run synchronization helper in gateway startup, then add regressions proving `dry_run=true` overrides ROS1 adapter state and prevents ROS1 transport calls. After the safety fix, clean up CLI/config behavior that still exposes old `robots.json` assumptions.

**Tech Stack:** Python 3.11+, existing FireClaw gateway/agent/execution modules, pytest.

---

## Current State To Reuse

- Reuse `RobotCapabilityProfile` and profile loading from `src/fireclaw_core/agent/robot_profile.py`.
- Reuse `resolve_gateway_config_with_profile()` from `src/fireclaw_core/gateway/gateway.py`.
- Reuse existing `create_robot_adapter()` from `src/fireclaw_core/execution/runtime_config.py`.
- Reuse `Ros1RobotAdapter` from `src/fireclaw_core/agent/robot.py`.
- Reuse profile-driven mission runtime from `src/fireclaw_core/mission/mission_runtime.py`.

## Problem Summary

The profile-driven gateway currently reports `GatewayConfig.dry_run`, but ROS1 execution uses `robot.dry_run`.

Known evidence:

- `src/fireclaw_core/gateway/gateway.py` constructs the adapter with `create_robot_adapter(...)`.
- `src/fireclaw_core/agent/robot.py` defines `Ros1RobotAdapter.dry_run = False`.
- `src/fireclaw_core/execution/skills.py` passes `dry_run=robot.dry_run` into `RobotActionRuntime`.
- `src/fireclaw_core/agent/robot.py` executes ROS1 transport when `config.transport.enabled` is true.
- `examples/robot_profiles/gazebo_turtlebot3.toml` uses `adapter = "ros1"`.
- `fireclaw.example.toml` uses `[robot_gateway].dry_run = true`.

Therefore, a user can believe the gateway is dry-run while the ROS1 adapter is not.

## Non-Goals

- Do not create a new robot profile format.
- Do not replace `create_robot_adapter()`.
- Do not remove `robots.json` compatibility.
- Do not rewrite the safety gate.
- Do not disable ROS1 transport globally; only dry-run mode should prevent real transport.

---

## File Structure

- Modify `src/fireclaw_core/gateway/gateway.py`
  - Add `apply_gateway_dry_run_to_robot()`.
  - Call it immediately after adapter construction.
  - Keep `--real-run` as the explicit way to set `dry_run=False`.
- Modify `src/fireclaw_core/agent/robot.py`
  - Make `Ros1RobotAdapter._record_configured_action()` skip transport in dry-run mode and return a successful dry-run record.
- Add/modify `tests/test_gateway_dry_run_profile.py`
  - Cover ROS1 profile + gateway dry-run behavior.
  - Cover real-run behavior preserves ROS1 transport capability.
- Modify `tests/test_robot.py`
  - Cover `Ros1RobotAdapter(dry_run=True)` does not call transport.
  - Keep existing transport-enabled tests for `dry_run=False`.
- Modify `src/fireclaw_core/gateway/serve.py`
  - Avoid writing default `robots.json` when profile-backed robot registry is configured.
- Modify `src/fireclaw_core/mission/mission_cli.py`
  - Optionally add profile-path support to direct `plan-mission` / `submit-subtask` CLI.
- Modify `README.md`, `docs/deployment/ros1-gazebo-debugging-guide.md`, and `fireclaw.example.toml`
  - Clarify that default profile-driven config is dry-run unless `--real-run` is passed.

---

## Task 1: Add Failing Regression For Gateway Dry-Run Override

**Files:**
- Create: `tests/test_gateway_dry_run_profile.py`
- Use existing fixtures/helpers from nearby gateway tests when practical.

- [ ] **Step 1: Write a ROS1 profile fixture helper**

Create `tests/test_gateway_dry_run_profile.py` with:

```python
from __future__ import annotations

from pathlib import Path

from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig


def _write_ros1_profile(path: Path, data_dir: Path, ros1_config: Path) -> None:
    path.write_text(
        f"""
[robot]
id = "ros1-profile-robot"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "{ros1_config}"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]

[capability_skill_chains]
search_for_victims = ["navigate_to_floor", "search_for_victims", "report_status"]
""".strip(),
        encoding="utf-8",
    )
```

- [ ] **Step 2: Write failing gateway dry-run test**

Add:

```python
def test_profile_gateway_applies_dry_run_to_ros1_adapter(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    ros1_config = Path("examples/ros1_configs/gazebo_turtlebot3_move_base.yaml")
    _write_ros1_profile(profile_path, tmp_path / "robot-data", ros1_config)

    gateway = FireClawGateway(
        GatewayConfig(
            port=0,
            robot_profile_path=str(profile_path),
            dry_run=True,
            workspace_skills_dir=None,
        )
    )

    assert gateway.config.adapter == "ros1"
    assert gateway.config.dry_run is True
    assert gateway.robot.dry_run is True
    assert gateway.health()["dry_run"] is True
    assert gateway.state()["robot_state"]["dry_run"] is True
```

- [ ] **Step 3: Write real-run preservation test**

Add:

```python
def test_profile_gateway_real_run_keeps_ros1_adapter_live(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    ros1_config = Path("examples/ros1_configs/gazebo_turtlebot3_move_base.yaml")
    _write_ros1_profile(profile_path, tmp_path / "robot-data", ros1_config)

    gateway = FireClawGateway(
        GatewayConfig(
            port=0,
            robot_profile_path=str(profile_path),
            dry_run=False,
            workspace_skills_dir=None,
        )
    )

    assert gateway.config.adapter == "ros1"
    assert gateway.config.dry_run is False
    assert gateway.robot.dry_run is False
    assert gateway.state()["robot_state"]["dry_run"] is False
```

- [ ] **Step 4: Run test and confirm failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_dry_run_profile.py -q
```

Expected:

- `test_profile_gateway_applies_dry_run_to_ros1_adapter` fails because `gateway.robot.dry_run` is currently `False`.

- [ ] **Step 5: Commit failing test only if using strict TDD branch checkpoints**

```bash
git add tests/test_gateway_dry_run_profile.py
git commit -m "test: expose profile gateway dry-run mismatch"
```

If keeping commits only after green tests, skip this commit and continue to Task 2.

---

## Task 2: Propagate Gateway Dry-Run To The Adapter

**Files:**
- Modify: `src/fireclaw_core/gateway/gateway.py`
- Test: `tests/test_gateway_dry_run_profile.py`

- [ ] **Step 1: Add a helper in `gateway.py`**

Near `resolve_gateway_config_with_profile()`, add:

```python
def apply_gateway_dry_run_to_robot(robot: Any, dry_run: bool) -> None:
    """Synchronize gateway dry-run mode onto adapters that expose a dry_run flag."""
    if hasattr(robot, "dry_run"):
        try:
            setattr(robot, "dry_run", dry_run)
        except Exception:
            logging.warning("Failed to apply gateway dry_run=%s to robot adapter", dry_run, exc_info=True)
```

`gateway.py` already imports `logging` and `Any`, so no new imports are needed.

- [ ] **Step 2: Call the helper immediately after adapter construction**

Change `FireClawGateway.__init__` from:

```python
self.robot = create_robot_adapter(resolved_config.adapter, resolved_config.robot_id, config_path=resolved_config.ros1_config_path)
```

to:

```python
self.robot = create_robot_adapter(
    resolved_config.adapter,
    resolved_config.robot_id,
    config_path=resolved_config.ros1_config_path,
)
apply_gateway_dry_run_to_robot(self.robot, resolved_config.dry_run)
```

- [ ] **Step 3: Run gateway dry-run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_dry_run_profile.py -q
```

Expected:

- PASS.

- [ ] **Step 4: Run adjacent gateway tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_robot_profile_config.py tests/test_gateway_structured_task.py tests/test_gateway_robot_agent_cli.py -q
```

Expected:

- PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/gateway/gateway.py tests/test_gateway_dry_run_profile.py
git commit -m "fix: apply gateway dry-run mode to profile adapters"
```

---

## Task 3: Make ROS1 Adapter Skip Transport In Dry-Run Mode

**Files:**
- Modify: `src/fireclaw_core/agent/robot.py`
- Modify: `tests/test_robot.py`

- [ ] **Step 1: Write failing ROS1 adapter test**

In `tests/test_robot.py`, add a fake transport:

```python
class FailingRos1Transport:
    def execute(self, endpoint, payload, config, cancellation_requested=None):
        raise AssertionError("dry-run ROS1 adapter must not execute transport")
```

Add test:

```python
def test_ros1_robot_adapter_dry_run_skips_transport() -> None:
    config = load_ros1_adapter_config("examples/ros1_configs/gazebo_turtlebot3_move_base.yaml")
    adapter = Ros1RobotAdapter(config=config, transport=FailingRos1Transport(), dry_run=True)

    result = adapter.navigate_to_floor(2)

    assert result.ok is True
    assert result.status == "succeeded"
    assert result.dry_run is True
    assert result.data["dry_run"] is True
    assert result.data["ros1_name"] == "/move_base"
```

Ensure `load_ros1_adapter_config` and `Ros1RobotAdapter` are imported in `tests/test_robot.py`. They may already be imported for existing ROS1 tests.

- [ ] **Step 2: Run test and confirm failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot.py::test_ros1_robot_adapter_dry_run_skips_transport -q
```

Expected:

- FAIL because `_record_configured_action()` currently calls transport when `transport.enabled` is true.

- [ ] **Step 3: Implement dry-run branch before transport execution**

In `src/fireclaw_core/agent/robot.py`, inside `Ros1RobotAdapter._record_configured_action()`, after `base_data` is built and before:

```python
if self.config.transport.enabled:
```

insert:

```python
if self.dry_run:
    return RobotActionResult(
        ok=True,
        status="succeeded",
        robot_id=self.robot_id,
        mode=self.mode,
        action=action,
        dry_run=True,
        data=base_data,
        timestamp=timestamp,
    )
```

- [ ] **Step 4: Confirm real-run transport tests still pass**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_robot.py::test_ros1_robot_adapter_executes_transport_enabled_action_with_rendered_goal \
  tests/test_robot.py::test_ros1_robot_adapter_cancels_transport_enabled_action \
  tests/test_robot.py::test_ros1_robot_adapter_dry_run_skips_transport \
  -q
```

Expected:

- PASS.
- Existing real transport tests should still pass because their adapters use `dry_run=False`.

- [ ] **Step 5: Run execution/gateway tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_action_runtime.py tests/test_execution.py tests/test_gateway_dry_run_profile.py -q
```

Expected:

- PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/agent/robot.py tests/test_robot.py
git commit -m "fix: skip ros1 transport during dry-run"
```

---

## Task 4: Validate Profile Before Runtime Binding Where Possible

**Files:**
- Modify: `src/fireclaw_core/gateway/gateway.py`
- Modify: `tests/test_gateway_robot_profile_config.py`

- [ ] **Step 1: Add test for invalid profile failing before store files are touched**

Add to `tests/test_gateway_robot_profile_config.py`:

```python
def test_invalid_profile_does_not_create_gateway_store_files(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    data_dir = tmp_path / "robot-data"
    profile_path.write_text(
        f"""
[robot]
id = "bad-robot"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["missing_skill"]
llm_exposed_skills = ["missing_skill"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="enabled skill 'missing_skill' is not registered"):
        FireClawGateway(
            GatewayConfig(
                port=0,
                robot_profile_path=str(profile_path),
                workspace_skills_dir=None,
            )
        )

    assert not (data_dir / "memory.jsonl").exists()
    assert not (data_dir / "events.jsonl").exists()
    assert not (data_dir / "tasks.jsonl").exists()
```

- [ ] **Step 2: Run test**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_robot_profile_config.py::test_invalid_profile_does_not_create_gateway_store_files -q
```

Expected:

- It may already pass for stores, but the test locks in the expected startup behavior.

- [ ] **Step 3: Refactor profile loading to avoid double load**

In `gateway.py`, add:

```python
def load_gateway_robot_profile(config: GatewayConfig):
    if not config.robot_profile_path:
        return None
    from fireclaw_core.agent.robot_profile import load_robot_capability_profile

    return load_robot_capability_profile(config.robot_profile_path)
```

Change `resolve_gateway_config_with_profile()` to accept an optional profile:

```python
def resolve_gateway_config_with_profile(config: GatewayConfig, profile: Any | None = None) -> GatewayConfig:
    if not config.robot_profile_path:
        return config
    from dataclasses import replace

    if profile is None:
        profile = load_gateway_robot_profile(config)
    return replace(
        config,
        adapter=profile.adapter,
        robot_id=profile.robot_id,
        ros1_config_path=profile.ros1_config,
        memory_path=str(profile.memory_path),
        event_path=str(profile.event_path),
        task_queue_path=str(profile.task_queue_path),
    )
```

Then in `FireClawGateway.__init__`:

```python
self.robot_profile = load_gateway_robot_profile(config)
resolved_config = resolve_gateway_config_with_profile(config, self.robot_profile)
self.config = resolved_config
```

Do not remove existing `resolve_gateway_config_with_profile(config)` behavior; tests call it directly.

- [ ] **Step 4: Keep validation but avoid creating stores before validation**

Keep current order after adapter construction:

```python
self.robot = create_robot_adapter(...)
apply_gateway_dry_run_to_robot(self.robot, resolved_config.dry_run)
self._validate_robot_profile()
self.memory = JsonlMemoryStore(...)
```

This still constructs the adapter before full skill-registry validation, because `create_default_skill_registry(self.robot)` needs the adapter. That is acceptable for now because Task 2 and Task 3 ensure dry-run is applied before any execution path.

- [ ] **Step 5: Run gateway config tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_robot_profile_config.py tests/test_gateway_dry_run_profile.py -q
```

Expected:

- PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/gateway/gateway.py tests/test_gateway_robot_profile_config.py
git commit -m "refactor: load gateway robot profile once before store setup"
```

---

## Task 5: Stop Creating Legacy `robots.json` When Profiles Are Configured

**Files:**
- Modify: `src/fireclaw_core/gateway/serve.py`
- Add or modify: `tests/test_gateway_serve_profiles.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_gateway_serve_profiles.py`:

```python
from __future__ import annotations

from pathlib import Path

from fireclaw_core.gateway.serve import _ensure_data_dir


def test_ensure_data_dir_skips_robots_template_when_profiles_are_configured(tmp_path: Path) -> None:
    _ensure_data_dir(tmp_path, create_robot_template=False)

    assert tmp_path.exists()
    assert not (tmp_path / "robots.json").exists()
```

- [ ] **Step 2: Run test and confirm failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_serve_profiles.py -q
```

Expected:

- FAIL because `_ensure_data_dir()` does not accept `create_robot_template`.

- [ ] **Step 3: Modify `_ensure_data_dir()`**

Change in `src/fireclaw_core/gateway/serve.py`:

```python
def _ensure_data_dir(data_dir: Path, *, create_robot_template: bool = True) -> None:
    """Create data directory and optional robots.json template."""
    data_dir.mkdir(parents=True, exist_ok=True)
    if not create_robot_template:
        return
    robots_path = data_dir / "robots.json"
    if not robots_path.exists():
        robots_path.write_text(json.dumps(DEFAULT_ROBOTS_TEMPLATE, indent=2, ensure_ascii=False))
        logger.info("Created robot registry template at %s", robots_path)
```

Change `start_server()`:

```python
_ensure_data_dir(data_dir, create_robot_template=not bool(robot_profiles))
```

- [ ] **Step 4: Run serve tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_serve_profiles.py tests/test_mission_runtime_profiles.py -q
```

Expected:

- PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/gateway/serve.py tests/test_gateway_serve_profiles.py
git commit -m "fix: skip legacy robot registry template for profile runtime"
```

---

## Task 6: Add Profile Support To Direct Mission CLI Commands

**Files:**
- Modify: `src/fireclaw_core/mission/mission_cli.py`
- Modify: `tests/test_mission_cli.py`

- [ ] **Step 1: Add CLI argument to shared paths**

Change `_add_shared_paths()`:

```python
parser.add_argument("--robot-registry", default=None, help="Path to robot registry JSON.")
parser.add_argument("--robot-profile", action="append", default=None, help="Path to robot profile TOML. Repeat for multiple robots.")
```

Keep `--robot-registry` supported. It should be required only when no `--robot-profile` is passed.

- [ ] **Step 2: Write failing CLI path builder test**

Add to `tests/test_mission_cli.py`:

```python
def test_build_mission_runtime_paths_accepts_robot_profiles(tmp_path):
    from argparse import Namespace
    from fireclaw_core.mission.mission_cli import _build_mission_runtime_paths

    args = Namespace(
        robot_registry=None,
        robot_profile=[str(tmp_path / "robot.toml")],
        mission_registry=str(tmp_path / "missions.jsonl"),
        memory_path=None,
        memory_index=None,
        task_registry=None,
        subagent_registry=None,
        session_lineage=None,
        task_flow=None,
        approval_path=None,
    )

    paths = _build_mission_runtime_paths(args)

    assert paths.robot_profiles == (tmp_path / "robot.toml",)
    assert paths.robot_registry == tmp_path / "robots.json"
```

- [ ] **Step 3: Implement path builder behavior**

Change `_build_mission_runtime_paths()`:

```python
robot_profiles = tuple(Path(p) for p in (getattr(args, "robot_profile", None) or ()))
robot_registry = getattr(args, "robot_registry", None)
if robot_registry is None:
    if not robot_profiles:
        raise SystemExit("--robot-registry is required when --robot-profile is not provided")
    robot_registry_path = Path("robots.json")
else:
    robot_registry_path = Path(robot_registry)
return MissionRuntimePaths(
    robot_registry=robot_registry_path,
    mission_registry=Path(args.mission_registry),
    robot_profiles=robot_profiles,
    ...
)
```

Use existing optional fields for the rest of the function.

- [ ] **Step 4: Add CLI help regression**

Add:

```python
def test_plan_mission_help_exposes_robot_profile_flag():
    completed = subprocess.run(
        [".venv/bin/python", "-m", "fireclaw_core", "plan-mission", "--help"],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert "--robot-profile" in completed.stdout
    assert "--robot-registry" in completed.stdout
```

- [ ] **Step 5: Run mission CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_cli.py tests/test_mission_runtime_profiles.py -q
```

Expected:

- PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/mission/mission_cli.py tests/test_mission_cli.py
git commit -m "feat: allow mission cli to load robot profiles"
```

---

## Task 7: Update Docs And Example Config For Dry-Run Semantics

**Files:**
- Modify: `fireclaw.example.toml`
- Modify: `README.md`
- Modify: `docs/deployment/ros1-gazebo-debugging-guide.md`

- [ ] **Step 1: Clarify config comments**

In `fireclaw.example.toml`, update the `[robot_gateway]` comment near `dry_run`:

```toml
# dry_run = true means robot-local execution records ROS1 commands without sending them.
# Use CLI --real-run only when ROS/Gazebo or robot hardware is ready.
dry_run = true
```

- [ ] **Step 2: Clarify startup docs**

In README and ROS1 debugging guide, include:

```bash
# Safe default: profile-backed dry run, no ROS transport execution
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml

# Explicit ROS/Gazebo transport execution
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml --real-run
```

Also state:

```text
In profile-driven mode, profile chooses the adapter and ROS config; dry_run chooses whether the adapter may send transport commands.
```

- [ ] **Step 3: Run docs secret scan**

Run:

```bash
rg -n "tp-[A-Za-z0-9]|sk-[A-Za-z0-9]{12,}|api_key\\s*=\\s*\"[^\"]+\"|api-key\\s+\"[^\"]+\"" fireclaw.example.toml README.md docs -g '!openclaw/**'
```

Expected:

- Only placeholders or test-plan examples.

- [ ] **Step 4: Commit**

```bash
git add fireclaw.example.toml README.md docs/deployment/ros1-gazebo-debugging-guide.md
git commit -m "docs: clarify profile dry-run behavior"
```

---

## Task 8: Final Verification

**Files:**
- No code changes unless verification exposes failures.

- [ ] **Step 1: Run focused profile/runtime suite**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_gateway_dry_run_profile.py \
  tests/test_gateway_robot_profile_config.py \
  tests/test_gateway_structured_task.py \
  tests/test_gateway_robot_agent_cli.py \
  tests/test_robot.py \
  tests/test_action_runtime.py \
  tests/test_execution.py \
  tests/test_mission_runtime_profiles.py \
  tests/test_mission_agent_profile_skill_chains.py \
  tests/test_profile_driven_runtime_e2e.py \
  tests/test_mission_cli.py \
  -q
```

Expected:

- PASS.

- [ ] **Step 2: Run CLI probes**

Run:

```bash
.venv/bin/python -m fireclaw_core --help >/tmp/fireclaw-help.txt
.venv/bin/python -m fireclaw_core robot-gateway --help >/tmp/fireclaw-robot-gateway-help.txt
.venv/bin/python -m fireclaw_core.gateway --help >/tmp/fireclaw-gateway-help.txt
.venv/bin/python -m fireclaw_core serve --help >/tmp/fireclaw-serve-help.txt
.venv/bin/python -m fireclaw_core plan-mission --help >/tmp/fireclaw-plan-mission-help.txt
```

Expected:

- All exit 0.
- `plan-mission --help` includes `--robot-profile`.

- [ ] **Step 3: Run secret scan**

Run:

```bash
rg -n "tp-[A-Za-z0-9]|sk-[A-Za-z0-9]{12,}|api_key\\s*=\\s*\"[^\"]+\"|api-key\\s+\"[^\"]+\"" \
  fireclaw.example.toml README.md docs examples src tests \
  -g '!openclaw/**'
```

Expected:

- No real secrets. Placeholder/test fake keys are acceptable if already present and clearly not real.

- [ ] **Step 4: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected:

- Full suite passes, with only known skips.

- [ ] **Step 5: Record memory**

Create `memory/2026-06-13/fireclaw-dry-run-profile-runtime-fix.md` with:

- commands run;
- files changed;
- test results;
- whether dry-run ROS1 transport was verified;
- any remaining risks.

---

## Expected Behavior After Completion

Safe default:

```bash
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml
```

Expected:

- profile still sets `adapter = "ros1"`;
- `GatewayConfig.dry_run = true`;
- `gateway.robot.dry_run = true`;
- `/health` and `/state` agree on dry-run status;
- ROS1 command payloads can be rendered/logged;
- ROS1 transport is not executed.

Real ROS/Gazebo run:

```bash
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml --real-run
```

Expected:

- `GatewayConfig.dry_run = false`;
- `gateway.robot.dry_run = false`;
- ROS1 transport may execute according to `ros1_config.transport.enabled`;
- safety gate and approval logic still apply.

## Self-Review

- Spec coverage: covers dry-run propagation, ROS1 transport prevention, validation ordering cleanup, legacy `robots.json` cleanup, CLI profile consistency, docs, and final verification.
- Duplication check: no new profile type, no new adapter type, no new skill registry, no new planner.
- Safety check: first executable tasks are tests around ROS1 dry-run behavior, then minimal implementation.
- Backward compatibility: `robots.json` remains supported when no profiles are configured.
