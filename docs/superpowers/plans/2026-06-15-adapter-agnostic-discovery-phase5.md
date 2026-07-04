# Adapter-Agnostic Discovery Phase 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the sensor capability grounding roadmap by fixing the remaining Phase 4 confirmation semantics and moving sensor discovery behind a backend-agnostic interface that supports ROS1, simulator/dry-run, and future non-ROS adapters without changing SafetyGate.

**Architecture:** Keep `SensorDiscoveryReport` as the common contract consumed by robot state and SafetyGate. Add a `SensorDiscoveryBackend` protocol plus small backend implementations/wrappers. Gateway/profile setup should create a backend through a factory instead of hard-coding `Ros1SensorDiscovery`, while real robot mode must reject static-only discovery for safety-critical sensors.

**Tech Stack:** Python 3.11 protocols/dataclasses, current FireClaw sensor discovery models, existing ROS1 discovery class, pytest, TOML robot profiles, no new runtime dependency.

---

## Source Context

- Master plan: `docs/superpowers/plans/2026-06-14-sensor-capability-grounding-master-plan.md`
- Final design: `docs/superpowers/specs/2026-06-14-sensor-capability-grounding-final-design.md`
- Phase 4 review: `memory/2026-06-15/fireclaw-phase4-review.md`

## Current Baseline

- Phase 1/2/3 are implemented and reviewed.
- Phase 4 is mechanically green but has two semantic issues:
  - `robot-profile discover` still emits confirmation-shaped data.
  - `diff-discovery` can report `status="fresh"` while rules are unconfirmed.
- Current ROS1 profile attachment hard-codes `Ros1SensorDiscovery` in `src/fireclaw_core/gateway/gateway.py`.
- `Ros1RobotAdapter.get_robot_state()` already only requires an object with `discover() -> SensorDiscoveryReport`; this is the natural Phase 5 backend seam.

## File Structure

- Modify `src/fireclaw_core/mission/mission_cli.py`
  - Fix `robot-profile discover` so it writes candidates only.
- Modify `src/fireclaw_core/agent/profile_discovery.py`
  - Make `unconfirmed` affect diff status.
- Modify `tests/test_mission_cli.py`
  - Update discover/diff semantic regressions.
- Modify `tests/test_profile_discovery.py`
  - Add unconfirmed diff status regression.
- Create `src/fireclaw_core/sensors/backends.py`
  - Define `SensorDiscoveryBackend`, `Ros1SensorDiscoveryBackend`, `StaticDeclaredDiscoveryBackend`, and factory helpers.
- Modify `src/fireclaw_core/sensors/__init__.py`
  - Re-export backend protocol/helpers.
- Modify `src/fireclaw_core/gateway/gateway.py`
  - Attach discovery backends through a factory instead of directly constructing ROS1 discovery.
- Modify `src/fireclaw_core/agent/robot.py`
  - Rename/type the adapter field conceptually as a backend-compatible object if needed, while preserving the existing `sensor_discovery` attribute for compatibility.
- Modify `tests/test_sensor_backends.py`
  - Unit tests for backend protocol behavior and static backend safety labels.
- Modify `tests/test_gateway_robot_profile_config.py`
  - Tests proving profile attach uses backend factory and rejects static-only backend for ROS1/real mode.
- Modify `tests/test_ros1_adapter_state.py`
  - Tests proving robot state consumes any backend with `discover()`, not a ROS1-specific class.
- Modify `docs/deployment/ros1-gazebo-debugging-guide.md`
  - Document final backend model and real-mode restriction.
- Create `memory/2026-06-15/fireclaw-phase5-adapter-agnostic-discovery.md`
  - Record implementation and verification.

---

### Task 0: Fix Phase 4 Confirmation Semantics

**Files:**
- Modify: `src/fireclaw_core/mission/mission_cli.py`
- Modify: `src/fireclaw_core/agent/profile_discovery.py`
- Modify: `tests/test_mission_cli.py`
- Modify: `tests/test_profile_discovery.py`
- Modify: `memory/2026-06-15/fireclaw-phase4-review.md`

- [ ] **Step 1: Write failing tests for discover-as-candidate semantics**

Modify `tests/test_mission_cli.py`.

In `test_robot_profile_discover_outputs_runtime_fingerprint`, replace:

```python
    assert 'confirmed_by = "robot-profile discover"' in text
```

with:

```python
    assert 'confirmed_by = "robot-profile discover"' not in text
    assert "confirmed_at" not in text
```

Add this assertion to `test_robot_profile_discover_writes_suggested_rules`:

```python
    assert "confirmed = false" in text
    assert "confirmed_by" not in text
    assert "confirmed_at" not in text
```

Add this assertion to `test_robot_profile_discover_write_profile_appends_rules`:

```python
    assert "confirmed = false" in text
    assert "confirmed_by" not in text
```

- [ ] **Step 2: Write failing test for unconfirmed diff status**

Append to `tests/test_profile_discovery.py`:

```python
def test_build_discovery_diff_marks_unconfirmed_rules_as_needing_confirmation() -> None:
    report = SensorDiscoveryReport(
        findings=(
            SensorFinding(
                sensor="lidar",
                topic="/scan",
                message_type="sensor_msgs/LaserScan",
                status="verified",
                confidence=0.99,
                source="ros1",
            ),
        ),
        runtime_fingerprint=DiscoveryFingerprint(source="ros1", topics_hash="sha256:new"),
        profile_fingerprint=DiscoveryFingerprint(source="ros1", topics_hash="sha256:new"),
        fingerprint_comparison=FingerprintComparison(status="fresh"),
    )
    profile_rules = (
        SensorMappingRule(
            topic_pattern="/scan",
            message_type="sensor_msgs/LaserScan",
            sensor="lidar",
            source="profile",
            confirmed=False,
        ),
    )

    diff = build_discovery_diff(report, profile_rules)

    assert diff.status == "needs_confirmation"
    assert diff.unconfirmed == ["/scan"]
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mission_cli.py::test_robot_profile_discover_writes_suggested_rules \
  tests/test_mission_cli.py::test_robot_profile_discover_write_profile_appends_rules \
  tests/test_mission_cli.py::test_robot_profile_discover_outputs_runtime_fingerprint \
  tests/test_profile_discovery.py::test_build_discovery_diff_marks_unconfirmed_rules_as_needing_confirmation \
  -q
```

Expected: FAIL because discover still writes confirmation-shaped fields and diff status ignores `unconfirmed`.

- [ ] **Step 4: Fix discover output**

In `src/fireclaw_core/mission/mission_cli.py`, inside `_fingerprint_lines()` for the `discover` branch, remove:

```python
            lines.append('confirmed_by = "robot-profile discover"')
```

Inside the `rule_lines.extend([...])` block for `discover`, replace:

```python
                f"confirmed = {str(finding.status == 'verified').lower()}",
```

with:

```python
                "confirmed = false",
```

- [ ] **Step 5: Fix diff status**

In `src/fireclaw_core/agent/profile_discovery.py`, replace the status assignment:

```python
    status = "fresh" if not added and not removed and not changed and not stale_confirmation else "changed"
```

with:

```python
    if unconfirmed:
        status = "needs_confirmation"
    elif not added and not removed and not changed and not stale_confirmation:
        status = "fresh"
    else:
        status = "changed"
```

- [ ] **Step 6: Run Phase 4 semantic tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_profile_discovery.py tests/test_mission_cli.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Update Phase 4 review memory**

Append to `memory/2026-06-15/fireclaw-phase4-review.md`:

```markdown
## Phase 4 Semantic Fix Update

- `robot-profile discover` now writes candidate rules with `confirmed = false`.
- `discover` no longer writes `confirmed_by` or `confirmed_at`.
- `confirm-discovery` remains the only command that writes confirmed rule/fingerprint audit metadata.
- `diff-discovery` now reports `status = "needs_confirmation"` when matching rules are unconfirmed.
- Verification:
  - `.venv/bin/python -m pytest tests/test_profile_discovery.py tests/test_mission_cli.py -q`
```

- [ ] **Step 8: Commit Task 0**

```bash
git add src/fireclaw_core/mission/mission_cli.py src/fireclaw_core/agent/profile_discovery.py tests/test_mission_cli.py tests/test_profile_discovery.py memory/2026-06-15/fireclaw-phase4-review.md
git commit -m "fix: separate discovery candidates from confirmation authority"
```

---

### Task 1: Add Common Sensor Discovery Backend Protocol

**Files:**
- Create: `src/fireclaw_core/sensors/backends.py`
- Modify: `src/fireclaw_core/sensors/__init__.py`
- Create: `tests/test_sensor_backends.py`

- [ ] **Step 1: Write failing backend protocol tests**

Create `tests/test_sensor_backends.py`:

```python
import pytest

from fireclaw_core.sensors.backends import (
    StaticDeclaredDiscoveryBackend,
    ensure_real_mode_backend_allowed,
)


def test_static_declared_backend_labels_dry_run_source_and_verified_sensors() -> None:
    backend = StaticDeclaredDiscoveryBackend(
        sensors=("rgb_camera", "lidar"),
        source="dry_run",
        allow_real_mode=False,
    )

    report = backend.discover()

    assert report.source == "dry_run"
    assert report.verified_sensors() == ["rgb_camera", "lidar"]
    assert all(finding.status == "verified" for finding in report.findings)
    assert all(finding.health_status == "healthy" for finding in report.findings)


def test_static_declared_backend_is_rejected_for_real_mode_by_default() -> None:
    backend = StaticDeclaredDiscoveryBackend(
        sensors=("rgb_camera",),
        source="dry_run",
        allow_real_mode=False,
    )

    with pytest.raises(ValueError, match="Static sensor discovery backend is not allowed for real mode"):
        ensure_real_mode_backend_allowed(backend, mode="ros1", dry_run=False)


def test_static_declared_backend_is_allowed_for_dry_run() -> None:
    backend = StaticDeclaredDiscoveryBackend(
        sensors=("rgb_camera",),
        source="dry_run",
        allow_real_mode=False,
    )

    ensure_real_mode_backend_allowed(backend, mode="dry_run", dry_run=True)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_backends.py -q
```

Expected: FAIL because `fireclaw_core.sensors.backends` does not exist.

- [ ] **Step 3: Implement backend protocol and static backend**

Create `src/fireclaw_core/sensors/backends.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fireclaw_core.sensors.discovery import SensorDiscoveryReport, SensorFinding


class SensorDiscoveryBackend(Protocol):
    def discover(self) -> SensorDiscoveryReport:
        ...


@dataclass(frozen=True)
class StaticDeclaredDiscoveryBackend:
    sensors: tuple[str, ...]
    source: str = "dry_run"
    allow_real_mode: bool = False

    def discover(self) -> SensorDiscoveryReport:
        findings = tuple(
            SensorFinding(
                sensor=sensor,
                topic=f"static://{sensor}",
                message_type="static/declaration",
                status="verified",
                confidence=1.0,
                source=self.source,
                reason="static declared sensor for non-real runtime",
                health_status="healthy",
            )
            for sensor in self.sensors
        )
        return SensorDiscoveryReport(findings=findings, source=self.source)


@dataclass(frozen=True)
class Ros1SensorDiscoveryBackend:
    delegate: SensorDiscoveryBackend

    def discover(self) -> SensorDiscoveryReport:
        return self.delegate.discover()


def ensure_real_mode_backend_allowed(
    backend: SensorDiscoveryBackend,
    *,
    mode: str,
    dry_run: bool,
) -> None:
    if dry_run or mode in {"dry_run", "simulator", "gazebo"}:
        return
    allow_real_mode = getattr(backend, "allow_real_mode", True)
    if not allow_real_mode:
        raise ValueError("Static sensor discovery backend is not allowed for real mode")
```

Modify `src/fireclaw_core/sensors/__init__.py` to export:

```python
from fireclaw_core.sensors.backends import (
    Ros1SensorDiscoveryBackend,
    SensorDiscoveryBackend,
    StaticDeclaredDiscoveryBackend,
    ensure_real_mode_backend_allowed,
)
```

- [ ] **Step 4: Run backend tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_backends.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/fireclaw_core/sensors/backends.py src/fireclaw_core/sensors/__init__.py tests/test_sensor_backends.py
git commit -m "feat: add sensor discovery backend protocol"
```

---

### Task 2: Make Robot State Consume Backend-Agnostic Discovery

**Files:**
- Modify: `src/fireclaw_core/agent/robot.py`
- Modify: `tests/test_ros1_adapter_state.py`

- [ ] **Step 1: Write failing backend-agnostic robot state test**

Append to `tests/test_ros1_adapter_state.py`:

```python
from fireclaw_core.sensors.backends import StaticDeclaredDiscoveryBackend


def test_ros1_adapter_state_consumes_backend_protocol_not_ros1_class() -> None:
    adapter = Ros1RobotAdapter(
        config=Ros1AdapterConfig(robot_id="robot-1"),
        sensor_discovery=StaticDeclaredDiscoveryBackend(
            sensors=("rgb_camera", "lidar"),
            source="test_static",
            allow_real_mode=True,
        ),
    )

    state = adapter.get_robot_state()

    assert state.available_sensors == ["rgb_camera", "lidar"]
    assert state.sensor_diagnostics is not None
    assert state.sensor_diagnostics["source"] == "test_static"
```

- [ ] **Step 2: Run test**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_adapter_state.py::test_ros1_adapter_state_consumes_backend_protocol_not_ros1_class -q
```

Expected: PASS if `Ros1RobotAdapter` already depends only on `discover()`. If it fails, update the adapter field typing/imports but keep the existing `sensor_discovery` attribute name.

- [ ] **Step 3: Tighten adapter field annotation if needed**

If needed, modify `src/fireclaw_core/agent/robot.py`:

```python
from fireclaw_core.sensors.backends import SensorDiscoveryBackend
```

and change:

```python
    sensor_discovery: Any | None = None
```

to:

```python
    sensor_discovery: SensorDiscoveryBackend | None = None
```

If this creates import cycles, keep `Any` and rely on the test as the contract.

- [ ] **Step 4: Run adapter state tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_adapter_state.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/fireclaw_core/agent/robot.py tests/test_ros1_adapter_state.py
git commit -m "test: prove robot state consumes discovery backend protocol"
```

---

### Task 3: Add Backend Factory And Gateway Integration

**Files:**
- Modify: `src/fireclaw_core/sensors/backends.py`
- Modify: `src/fireclaw_core/gateway/gateway.py`
- Modify: `tests/test_gateway_robot_profile_config.py`

- [ ] **Step 1: Write failing gateway backend tests**

Append to `tests/test_gateway_robot_profile_config.py`:

```python
def test_profile_gateway_attaches_sensor_discovery_backend_wrapper(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    ros1_path = tmp_path / "ros1.yaml"
    ros1_path.write_text(
        """
robot_id: robot-1
transport:
  enabled: false
endpoints:
  navigate_to_floor:
    interface: action
    name: /move_base
    type: move_base_msgs/MoveBaseAction
""".strip(),
        encoding="utf-8",
    )
    profile_path.write_text(
        f"""
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "{ros1_path}"
data_dir = "{tmp_path / "robot-data"}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor"]
llm_exposed_skills = ["navigate_to_floor"]

[robot.sensor_discovery]
enabled = true
message_timeout_seconds = 1.0
""".strip(),
        encoding="utf-8",
    )

    gateway = FireClawGateway(GatewayConfig(robot_profile_path=str(profile_path)))
    backend = getattr(gateway.robot, "sensor_discovery", None)

    from fireclaw_core.sensors.backends import Ros1SensorDiscoveryBackend

    assert isinstance(backend, Ros1SensorDiscoveryBackend)
```

```python
def test_static_discovery_backend_rejected_for_ros1_real_mode() -> None:
    from fireclaw_core.sensors.backends import StaticDeclaredDiscoveryBackend, ensure_real_mode_backend_allowed

    backend = StaticDeclaredDiscoveryBackend(sensors=("rgb_camera",), source="static", allow_real_mode=False)

    import pytest

    with pytest.raises(ValueError, match="Static sensor discovery backend is not allowed for real mode"):
        ensure_real_mode_backend_allowed(backend, mode="ros1", dry_run=False)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_gateway_robot_profile_config.py::test_profile_gateway_attaches_sensor_discovery_backend_wrapper \
  tests/test_gateway_robot_profile_config.py::test_static_discovery_backend_rejected_for_ros1_real_mode \
  -q
```

Expected: first test FAILS because gateway currently attaches raw `Ros1SensorDiscovery`; second may pass if Task 1 is implemented.

- [ ] **Step 3: Add ROS1 backend factory**

In `src/fireclaw_core/sensors/backends.py`, add:

```python
def create_profile_sensor_discovery_backend(profile: object) -> SensorDiscoveryBackend | None:
    sensor_discovery = getattr(profile, "sensor_discovery", None)
    if sensor_discovery is None or not sensor_discovery.enabled:
        return None
    adapter = getattr(profile, "adapter", "")
    if adapter == "ros1":
        from fireclaw_core.ros.ros1_sensor_discovery import (
            Ros1CliGraphProvider,
            Ros1CliMessageProbe,
            Ros1SensorDiscovery,
        )

        return Ros1SensorDiscoveryBackend(
            Ros1SensorDiscovery(
                graph_provider=Ros1CliGraphProvider(),
                message_probe=Ros1CliMessageProbe(),
                extra_rules=sensor_discovery.rules,
                timeout_seconds=sensor_discovery.message_timeout_seconds,
                profile_fingerprint=getattr(profile, "discovery_fingerprint", None),
            )
        )
    if adapter in {"simulator", "dry_run", "mock"}:
        return StaticDeclaredDiscoveryBackend(
            sensors=("rgb_camera", "thermal_camera", "lidar"),
            source=adapter,
            allow_real_mode=False,
        )
    return None
```

Export `create_profile_sensor_discovery_backend` from `src/fireclaw_core/sensors/__init__.py`.

- [ ] **Step 4: Use factory in gateway**

Modify `src/fireclaw_core/gateway/gateway.py`.

Replace the ROS1-specific imports/construction in `attach_profile_sensor_discovery()` with:

```python
    from fireclaw_core.sensors.backends import create_profile_sensor_discovery_backend

    backend = create_profile_sensor_discovery_backend(profile)
    if backend is None:
        return
    setattr(robot, "sensor_discovery", backend)
```

Keep the existing early returns for `profile is None` and `not profile.sensor_discovery.enabled`. Remove the `profile.adapter != "ros1"` early return so simulator/dry-run profiles can attach explicit static backends.

- [ ] **Step 5: Run gateway tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_robot_profile_config.py tests/test_sensor_backends.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/fireclaw_core/sensors/backends.py src/fireclaw_core/sensors/__init__.py src/fireclaw_core/gateway/gateway.py tests/test_gateway_robot_profile_config.py
git commit -m "feat: attach profile sensor discovery through backend factory"
```

---

### Task 4: Add Simulator/Dry-Run Backend Coverage Through Robot State And SafetyGate

**Files:**
- Modify: `tests/test_gateway_robot_profile_config.py`
- Modify: `tests/test_safety.py`
- Modify: `src/fireclaw_core/sensors/backends.py` if needed

- [ ] **Step 1: Add simulator profile backend test**

Append to `tests/test_gateway_robot_profile_config.py`:

```python
def test_simulator_profile_attaches_static_declared_discovery_backend(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        f"""
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "{tmp_path / "robot-data"}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims"]

[robot.sensor_discovery]
enabled = true
message_timeout_seconds = 1.0
""".strip(),
        encoding="utf-8",
    )

    gateway = FireClawGateway(GatewayConfig(robot_profile_path=str(profile_path)))
    backend = getattr(gateway.robot, "sensor_discovery", None)

    from fireclaw_core.sensors.backends import StaticDeclaredDiscoveryBackend

    assert isinstance(backend, StaticDeclaredDiscoveryBackend)
    report = backend.discover()
    assert report.source == "simulator"
    assert "lidar" in report.verified_sensors()
```

- [ ] **Step 2: Add SafetyGate backend report test**

Append to `tests/test_safety.py`:

```python
def test_safety_uses_backend_verified_sensors_from_robot_state() -> None:
    planning_result = RuleBasedPlanner().plan("去二楼救人")
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="robot-1"))
    robot_state = RobotState(
        robot_id="robot-1",
        mode="simulator",
        dry_run=True,
        online=True,
        battery_percent=100.0,
        current_floor=1,
        available_sensors=["rgb_camera", "lidar"],
        supports_real_execution=False,
        sensor_diagnostics={
            "source": "simulator",
            "verified_sensors": ["rgb_camera", "lidar"],
            "findings": [],
        },
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=True,
        robot_state=robot_state,
        environment_state=EnvironmentState(reachable_floors=[2]),
    )

    assert decision.status == "allow"
```

- [ ] **Step 3: Run tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_gateway_robot_profile_config.py::test_simulator_profile_attaches_static_declared_discovery_backend \
  tests/test_safety.py::test_safety_uses_backend_verified_sensors_from_robot_state \
  -q
```

Expected: all tests pass after Task 3. If the SafetyGate test fails because of unrelated dry-run skill metadata, adjust only the test setup to use a registry whose skills are dry-run compatible.

- [ ] **Step 4: Commit Task 4**

```bash
git add tests/test_gateway_robot_profile_config.py tests/test_safety.py src/fireclaw_core/sensors/backends.py
git commit -m "test: cover simulator discovery backend safety flow"
```

---

### Task 5: Documentation, Memory, And Full Verification

**Files:**
- Modify: `docs/deployment/ros1-gazebo-debugging-guide.md`
- Create: `memory/2026-06-15/fireclaw-phase5-adapter-agnostic-discovery.md`

- [ ] **Step 1: Document final backend model**

Append to `docs/deployment/ros1-gazebo-debugging-guide.md`:

```markdown
## Adapter-Agnostic Discovery Backends

FireClaw discovery now uses a backend contract:

```text
SensorDiscoveryBackend.discover() -> SensorDiscoveryReport
```

The core safety path consumes only `SensorDiscoveryReport`:

```text
backend discovery
  -> RobotState.available_sensors
  -> RobotState.sensor_diagnostics
  -> SafetyGate
```

Backend labels are explicit:

- `ros1`: live ROS1 topic/type/message discovery.
- `simulator` or `dry_run`: static declared discovery for non-real runtime only.

Real robot mode must not rely on static declared discovery for safety-critical sensors. Static backends are allowed for simulator and dry-run workflows, but they cannot silently masquerade as real sensor verification.
```

- [ ] **Step 2: Create Phase 5 memory record**

Create `memory/2026-06-15/fireclaw-phase5-adapter-agnostic-discovery.md`:

```markdown
# FireClaw Phase 5 Adapter-Agnostic Discovery

## Task Goal

Finish sensor capability grounding by moving discovery behind a backend-agnostic contract and fixing the final Phase 4 confirmation semantics.

## Files Modified

- `src/fireclaw_core/mission/mission_cli.py`
- `src/fireclaw_core/agent/profile_discovery.py`
- `src/fireclaw_core/sensors/backends.py`
- `src/fireclaw_core/sensors/__init__.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/agent/robot.py`
- `tests/test_mission_cli.py`
- `tests/test_profile_discovery.py`
- `tests/test_sensor_backends.py`
- `tests/test_gateway_robot_profile_config.py`
- `tests/test_ros1_adapter_state.py`
- `tests/test_safety.py`
- `docs/deployment/ros1-gazebo-debugging-guide.md`

## Verification Commands

- `.venv/bin/python -m pytest tests/test_profile_discovery.py tests/test_mission_cli.py -q`
- `.venv/bin/python -m pytest tests/test_sensor_backends.py tests/test_gateway_robot_profile_config.py tests/test_ros1_adapter_state.py tests/test_safety.py -q`
- `.venv/bin/python -m pytest -q`

## Current Conclusion

Replace this sentence with actual verification results after implementation.
```

- [ ] **Step 3: Run focused Phase 5 verification**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_profile_discovery.py \
  tests/test_mission_cli.py \
  tests/test_sensor_backends.py \
  tests/test_gateway_robot_profile_config.py \
  tests/test_ros1_adapter_state.py \
  tests/test_safety.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 4: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass or only known unrelated skips remain.

- [ ] **Step 5: Record actual verification output**

Update `memory/2026-06-15/fireclaw-phase5-adapter-agnostic-discovery.md` with exact pass/fail counts:

```markdown
## Actual Verification Results

- Focused Phase 5 command: replace with exact output.
- Full suite command: replace with exact output.

## Remaining Gaps

- ROS2 and vendor SDK discovery backends are still future work.
- Static simulator/dry-run discovery is intentionally labeled and is not valid real-world authority.
- ROS1 typed message inspection would still be stronger than `rostopic echo` text parsing.
```

- [ ] **Step 6: Commit Task 5**

```bash
git add docs/deployment/ros1-gazebo-debugging-guide.md memory/2026-06-15/fireclaw-phase5-adapter-agnostic-discovery.md
git commit -m "docs: document adapter agnostic discovery backends"
```

---

## Final Verification Checklist

- [ ] `robot-profile discover` writes candidates, not confirmed authority.
- [ ] `confirm-discovery` is the only command that writes `confirmed=true`, `confirmed_by`, and `confirmed_at`.
- [ ] `diff-discovery` reports unconfirmed mappings as action-required.
- [ ] A common `SensorDiscoveryBackend` protocol exists.
- [ ] ROS1 discovery is attached through a backend wrapper/factory.
- [ ] Simulator/dry-run static discovery is explicitly labeled.
- [ ] Static discovery is rejected for real mode.
- [ ] Robot state consumes backend reports without knowing ROS1-specific classes.
- [ ] SafetyGate consumes common verified capabilities through `RobotState`.
- [ ] Full suite has been run in the implementation session.

## Self-Review

Spec coverage:

- Phase 4 semantic fixes are covered by Task 0.
- Common backend protocol is covered by Task 1.
- Robot state backend consumption is covered by Task 2.
- Gateway factory integration is covered by Task 3.
- Simulator/dry-run explicit backend behavior is covered by Task 4.
- Documentation and full verification are covered by Task 5.

Placeholder scan:

- The only replacement instructions are in the memory template for actual verification output and must be replaced during implementation.
- No implementation task uses `TBD` or unspecified "handle edge cases" language.

Type consistency:

- `SensorDiscoveryBackend.discover()` returns `SensorDiscoveryReport`.
- `StaticDeclaredDiscoveryBackend` is non-real by default.
- `Ros1SensorDiscoveryBackend` wraps the existing ROS1 discovery implementation without changing the report shape.
- Gateway attaches backends through `create_profile_sensor_discovery_backend()`.
