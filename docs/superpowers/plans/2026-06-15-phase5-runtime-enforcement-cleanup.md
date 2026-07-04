# Phase 5 Runtime Enforcement Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two remaining Phase 5 runtime enforcement gaps so static discovery cannot be trusted in real mode and simulator/dry-run adapters expose backend diagnostics through the same `RobotState` path.

**Architecture:** Keep `SensorDiscoveryBackend` as the common contract. Add one shared helper for adapter state construction so ROS1 and simulator-style adapters consume backend reports consistently. Enforce `ensure_real_mode_backend_allowed()` inside the runtime state path, not only in tests.

**Tech Stack:** Python 3.11 dataclasses/protocols, existing `SensorDiscoveryBackend`, existing robot adapters, pytest.

---

## Current Review Findings

From `memory/2026-06-15/fireclaw-phase5-review.md`:

1. `Ros1RobotAdapter.get_robot_state()` accepts `StaticDeclaredDiscoveryBackend(... allow_real_mode=False)` and reports static sensors as available.
2. `SimulatorRobotAdapter.get_robot_state()` ignores an attached `sensor_discovery` backend and returns old static `available_sensors` with no diagnostics.

## File Structure

- Modify `src/fireclaw_core/agent/robot.py`
  - Add a shared `_discovered_sensor_state()` helper.
  - Call `ensure_real_mode_backend_allowed()` before consuming backend reports.
  - Add optional `sensor_discovery` field to `SimulatorRobotAdapter`.
  - Use the shared backend consumption path in `Ros1RobotAdapter` and `SimulatorRobotAdapter`.
- Modify `tests/test_ros1_adapter_state.py`
  - Add regression that ROS1 real mode rejects static backend.
- Modify `tests/test_gateway_robot_profile_config.py`
  - Strengthen simulator profile test to verify `RobotState.sensor_diagnostics` and backend-derived sensors.
- Modify `tests/test_safety.py`
  - Keep/adjust SafetyGate test if simulator backend output changes ordering.
- Modify `memory/2026-06-15/fireclaw-phase5-review.md`
  - Record cleanup result and exact verification output.

---

### Task 1: Reject Static Backends In ROS1 Real Runtime

**Files:**
- Modify: `src/fireclaw_core/agent/robot.py`
- Modify: `tests/test_ros1_adapter_state.py`

- [ ] **Step 1: Write failing ROS1 regression test**

Append to `tests/test_ros1_adapter_state.py`:

```python
import pytest


def test_ros1_adapter_state_rejects_static_backend_for_real_mode() -> None:
    adapter = Ros1RobotAdapter(
        config=Ros1AdapterConfig(robot_id="robot-1"),
        sensor_discovery=StaticDeclaredDiscoveryBackend(
            sensors=("rgb_camera",),
            source="static",
            allow_real_mode=False,
        ),
    )

    with pytest.raises(ValueError, match="Static sensor discovery backend is not allowed for real mode"):
        adapter.get_robot_state()
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_adapter_state.py::test_ros1_adapter_state_rejects_static_backend_for_real_mode -q
```

Expected: FAIL because `Ros1RobotAdapter.get_robot_state()` currently consumes the static backend.

- [ ] **Step 3: Add shared backend state helper**

In `src/fireclaw_core/agent/robot.py`, update the import:

```python
from fireclaw_core.sensors.backends import SensorDiscoveryBackend, ensure_real_mode_backend_allowed
```

Add this helper near `RobotState` or before adapter classes:

```python
def _discovered_sensor_state(
    *,
    backend: SensorDiscoveryBackend | None,
    mode: str,
    dry_run: bool,
) -> tuple[list[str] | None, dict[str, Any] | None]:
    if backend is None:
        return None, None
    ensure_real_mode_backend_allowed(backend, mode=mode, dry_run=dry_run)
    report = backend.discover()
    return report.verified_sensors(), report.to_dict()
```

- [ ] **Step 4: Use helper in `Ros1RobotAdapter.get_robot_state()`**

Replace the discovery block in `Ros1RobotAdapter.get_robot_state()`:

```python
        sensor_diagnostics: dict[str, Any] | None = None
        available_sensors: list[str] | None = None
        if self.sensor_discovery is not None:
            try:
                report = self.sensor_discovery.discover()
                sensor_diagnostics = report.to_dict()
                available_sensors = report.verified_sensors()
            except Exception:
                logging.warning("Sensor discovery failed, falling back to static sensors", exc_info=True)
                if self.available_sensors:
                    available_sensors = list(self.available_sensors)
```

with:

```python
        sensor_diagnostics: dict[str, Any] | None = None
        available_sensors: list[str] | None = None
        if self.sensor_discovery is not None:
            try:
                available_sensors, sensor_diagnostics = _discovered_sensor_state(
                    backend=self.sensor_discovery,
                    mode=self.mode,
                    dry_run=self.dry_run,
                )
            except ValueError:
                raise
            except Exception:
                logging.warning("Sensor discovery failed, falling back to static sensors", exc_info=True)
                if self.available_sensors:
                    available_sensors = list(self.available_sensors)
```

The explicit `except ValueError: raise` is important: policy violations should fail closed, not fall back to static sensors.

- [ ] **Step 5: Run ROS1 adapter state tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_adapter_state.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/fireclaw_core/agent/robot.py tests/test_ros1_adapter_state.py
git commit -m "fix: reject static discovery backend in real ros1 state"
```

---

### Task 2: Make Simulator Adapter Consume Discovery Backend Diagnostics

**Files:**
- Modify: `src/fireclaw_core/agent/robot.py`
- Modify: `tests/test_gateway_robot_profile_config.py`

- [ ] **Step 1: Strengthen simulator gateway regression**

Modify `tests/test_gateway_robot_profile_config.py::test_simulator_profile_attaches_static_declared_discovery_backend`.

After:

```python
    assert "lidar" in report.verified_sensors()
```

add:

```python
    state = gateway.robot.get_robot_state()
    assert state.sensor_diagnostics is not None
    assert state.sensor_diagnostics["source"] == "simulator"
    assert state.available_sensors == ["rgb_camera", "thermal_camera", "lidar"]
```

Also add a direct simulator backend override regression:

```python
def test_simulator_adapter_state_uses_attached_discovery_backend() -> None:
    from fireclaw_core.agent.robot import SimulatorRobotAdapter
    from fireclaw_core.sensors.backends import StaticDeclaredDiscoveryBackend

    robot = SimulatorRobotAdapter(robot_id="sim-1")
    robot.sensor_discovery = StaticDeclaredDiscoveryBackend(
        sensors=("gas_detector",),
        source="simulator",
        allow_real_mode=False,
    )

    state = robot.get_robot_state()

    assert state.available_sensors == ["gas_detector"]
    assert state.sensor_diagnostics is not None
    assert state.sensor_diagnostics["source"] == "simulator"
```

- [ ] **Step 2: Run simulator tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_gateway_robot_profile_config.py::test_simulator_profile_attaches_static_declared_discovery_backend \
  tests/test_gateway_robot_profile_config.py::test_simulator_adapter_state_uses_attached_discovery_backend \
  -q
```

Expected: FAIL because `SimulatorRobotAdapter.get_robot_state()` ignores `sensor_discovery`.

- [ ] **Step 3: Add backend field to simulator adapter**

In `src/fireclaw_core/agent/robot.py`, add this field to `SimulatorRobotAdapter`:

```python
    sensor_discovery: SensorDiscoveryBackend | None = None
```

- [ ] **Step 4: Use shared helper in `SimulatorRobotAdapter.get_robot_state()`**

Replace the current `SimulatorRobotAdapter.get_robot_state()` with:

```python
    def get_robot_state(self) -> RobotState:
        sensor_diagnostics: dict[str, Any] | None = None
        available_sensors = list(self.available_sensors)
        if self.sensor_discovery is not None:
            discovered_sensors, sensor_diagnostics = _discovered_sensor_state(
                backend=self.sensor_discovery,
                mode=self.mode,
                dry_run=self.dry_run,
            )
            if discovered_sensors is not None:
                available_sensors = discovered_sensors
        return RobotState(
            robot_id=self.robot_id,
            mode=self.mode,
            dry_run=self.dry_run,
            online=self.online and not self.emergency_stopped,
            battery_percent=float(self.battery_percent),
            current_floor=self.current_floor,
            available_sensors=available_sensors,
            supports_real_execution=False,
            sensor_diagnostics=sensor_diagnostics,
        )
```

Do not catch `ValueError` here. If a static backend is somehow configured for non-dry-run simulator mode, the policy should fail visibly.

- [ ] **Step 5: Run simulator and gateway tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_robot_profile_config.py tests/test_ros1_adapter_state.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/fireclaw_core/agent/robot.py tests/test_gateway_robot_profile_config.py
git commit -m "fix: expose simulator discovery backend diagnostics"
```

---

### Task 3: Focused Verification And Memory Update

**Files:**
- Modify: `memory/2026-06-15/fireclaw-phase5-review.md`

- [ ] **Step 1: Run focused cleanup tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_ros1_adapter_state.py \
  tests/test_gateway_robot_profile_config.py \
  tests/test_sensor_backends.py \
  tests/test_safety.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 2: Re-run the two original probes**

Run:

```bash
.venv/bin/python - <<'PY'
from fireclaw_core.agent.robot import Ros1RobotAdapter, SimulatorRobotAdapter
from fireclaw_core.ros.ros1_config import Ros1AdapterConfig
from fireclaw_core.sensors.backends import StaticDeclaredDiscoveryBackend

try:
    real = Ros1RobotAdapter(
        config=Ros1AdapterConfig(robot_id="r1"),
        sensor_discovery=StaticDeclaredDiscoveryBackend(
            sensors=("rgb_camera",),
            source="static",
            allow_real_mode=False,
        ),
    )
    real.get_robot_state()
except Exception as exc:
    print(type(exc).__name__, str(exc))

sim = SimulatorRobotAdapter(robot_id="sim1")
sim.sensor_discovery = StaticDeclaredDiscoveryBackend(
    sensors=("gas_detector",),
    source="simulator",
    allow_real_mode=False,
)
state = sim.get_robot_state()
print("sim_state_available=", state.available_sensors)
print("sim_state_diag_source=", state.sensor_diagnostics["source"] if state.sensor_diagnostics else None)
PY
```

Expected output includes:

```text
ValueError Static sensor discovery backend is not allowed for real mode
sim_state_available= ['gas_detector']
sim_state_diag_source= simulator
```

- [ ] **Step 3: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass or only known unrelated skips remain.

- [ ] **Step 4: Update Phase 5 review memory**

Append to `memory/2026-06-15/fireclaw-phase5-review.md`:

```markdown
## Runtime Enforcement Cleanup Update

- `Ros1RobotAdapter.get_robot_state()` now calls `ensure_real_mode_backend_allowed()` before consuming backend reports.
- Static discovery backends with `allow_real_mode=false` now fail closed in ROS1 real mode.
- `SimulatorRobotAdapter.get_robot_state()` now consumes attached discovery backends and exposes `sensor_diagnostics`.
- Verification:
  - Focused cleanup command: replace with exact pass/fail count.
  - Probe command: replace with exact output.
  - Full suite: replace with exact pass/fail/skip count.
```

Replace the verification lines with actual output.

- [ ] **Step 5: Commit Task 3**

```bash
git add memory/2026-06-15/fireclaw-phase5-review.md
git commit -m "docs: record phase5 runtime enforcement cleanup"
```

---

## Final Verification Checklist

- [ ] ROS1 real mode rejects `StaticDeclaredDiscoveryBackend(allow_real_mode=False)`.
- [ ] ROS1 discovery failures still fall back to static sensors only for ordinary discovery failures, not policy violations.
- [ ] Simulator adapter consumes an attached backend.
- [ ] Simulator `RobotState.sensor_diagnostics` includes backend report diagnostics.
- [ ] Gateway simulator profile backend is visible through `gateway.robot.get_robot_state()`.
- [ ] SafetyGate continues consuming `RobotState.available_sensors`.
- [ ] Full suite has been run after cleanup.

## Self-Review

Spec coverage:

- Finding 1 from Phase 5 review is covered by Task 1.
- Finding 2 from Phase 5 review is covered by Task 2.
- Verification and memory are covered by Task 3.

Placeholder scan:

- The only "replace with exact output" text is in the memory-update instruction and must be replaced during execution.

Type consistency:

- `_discovered_sensor_state()` returns `(available_sensors, sensor_diagnostics)`.
- Both ROS1 and simulator adapters use `SensorDiscoveryBackend`.
- Policy violations raise `ValueError` and are not swallowed by fallback logic.
