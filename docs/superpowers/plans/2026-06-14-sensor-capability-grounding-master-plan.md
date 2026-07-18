# Sensor Capability Grounding Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move FireClaw from ROS1 topic discovery to final sensor capability grounding, where verified robot capabilities are derived from runtime discovery, profile freshness, sensor health, safety policy, operator confirmation, and backend-independent adapters.

**Architecture:** Keep the completed fingerprint/stale-profile layer as Phase 1 baseline. Implement Phase 2 next by adding sensor health models and wiring them into ROS1 discovery so `verified` means a mapped sensor is currently healthy, not merely present. Use later phases to consume Phase 2 health status in safety policy, operator confirmation, and adapter-agnostic discovery backends.

**Tech Stack:** Python 3.11 dataclasses, existing FireClaw sensor discovery models, ROS1 graph/message probe abstractions, pytest with deterministic fakes, TOML robot profiles, no new external runtime dependency.

---

## Source Documents

- Design spec: `docs/superpowers/specs/2026-06-14-sensor-capability-grounding-final-design.md`
- Completed Phase 1 spec: `docs/superpowers/specs/2026-06-14-ros1-discovery-fingerprint-stale-profile-design.md`
- Completed Phase 1 plan: `docs/superpowers/plans/2026-06-14-ros1-discovery-fingerprint-stale-profile.md`
- Memory record: `memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md`

## Current Baseline

The current baseline is green:

```bash
.venv/bin/python -m pytest -q
# 1199 passed, 6 skipped
```

Completed behavior:

- ROS1 topic/type discovery.
- Profile-configurable sensor mapping rules.
- `discovered`, `verified`, `degraded`, and `rejected` findings.
- Runtime/profile discovery fingerprints.
- `fresh`, `missing`, `stale`, and `unknown` fingerprint comparison.
- Stale/missing/unknown profile confirmation downgrades.
- SafetyGate trusts only live verified sensors.

---

## Five-Phase Roadmap

### Phase 1: Discovery Fingerprint And Stale Profile Detection

Status: complete.

Do not reimplement this phase. Treat it as the foundation for all later phases.

Exit criteria already met:

- Profile fingerprint parsing exists.
- Runtime ROS1 graph fingerprint exists.
- Stale/missing/unknown fingerprint states are visible in discovery diagnostics.
- Confirmed rules under non-fresh fingerprint show stale confirmation.
- Full suite passed after cleanup.

### Phase 2: Per-Sensor Health Checks

Status: next implementation phase. This plan expands Phase 2 to executable steps.

Goal:

- Make `verified` depend on sensor-specific health, not only topic/type and recent-message presence.

### Phase 3: Safety-Critical Sensor Policy

Status: planned after Phase 2.

Entry condition:

- `SensorFinding` includes health status and health reason.
- ROS1 discovery can produce degraded findings for invalid data, not only missing data.

Planned deliverables:

- `src/fireclaw_core/safety/sensor_policy.py`
- `tests/test_sensor_policy.py`
- SafetyGate integration tests showing block/escalate/warn behavior from health status.

### Phase 4: Operator Confirmation Loop

Status: planned after Phase 2 and Phase 3.

Entry condition:

- Discovery diagnostics include enough mapping, fingerprint, and health information for an operator to review.

Planned deliverables:

- `robot-profile diff-discovery`
- `robot-profile confirm-discovery`
- parseable profile write-back with `confirmed_at` and `confirmed_by`
- tests covering changed fingerprint, selected-rule confirmation, and profile reload.

### Phase 5: Adapter-Agnostic Discovery Backend

Status: planned after ROS1 health/policy boundaries stabilize.

Entry condition:

- ROS1 discovery no longer changes common model fields on every phase.

Planned deliverables:

- common `SensorDiscoveryBackend` protocol;
- ROS1 backend renamed or wrapped behind the protocol;
- simulator/dry-run static backend with explicit runtime labels;
- tests proving SafetyGate consumes common verified capabilities without backend-specific logic.

---

# Phase 2 Executable Plan: Per-Sensor Health Checks

## Phase 2 File Structure

- Create `src/fireclaw_core/sensors/health.py`
  - Owns health statuses, observations, policies, default policies, and policy evaluation.
- Modify `src/fireclaw_core/sensors/__init__.py`
  - Re-export health models.
- Modify `src/fireclaw_core/sensors/discovery.py`
  - Add health fields to `SensorFinding`.
- Modify `src/fireclaw_core/ros/ros1_sensor_discovery.py`
  - Convert ROS1 message probes into health observations.
  - Mark a matching sensor as `verified` only when health is `healthy`.
- Modify `tests/test_sensor_health.py`
  - Unit tests for policy evaluation.
- Modify `tests/test_sensor_discovery.py`
  - Serialization tests for health fields.
- Modify `tests/test_ros1_sensor_discovery.py`
  - Integration tests for healthy/degraded ROS1 findings.
- Modify `tests/test_ros1_adapter_state.py`
  - Robot state diagnostics include health status.
- Modify `memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md`
  - Record Phase 2 implementation and verification results.

---

### Task 1: Add Sensor Health Core Models

**Files:**
- Create: `src/fireclaw_core/sensors/health.py`
- Modify: `src/fireclaw_core/sensors/__init__.py`
- Create: `tests/test_sensor_health.py`

- [ ] **Step 1: Write failing health model tests**

Create `tests/test_sensor_health.py`:

```python
from fireclaw_core.sensors.health import (
    DEFAULT_SENSOR_HEALTH_POLICIES,
    SensorObservation,
    evaluate_sensor_health,
)


def test_camera_health_requires_recent_non_empty_payload() -> None:
    result = evaluate_sensor_health(
        "rgb_camera",
        SensorObservation(observed=True, age_seconds=0.5, payload_size=128, frame_id="camera"),
    )

    assert result.status == "healthy"
    assert result.reason is None


def test_camera_health_degrades_empty_payload() -> None:
    result = evaluate_sensor_health(
        "rgb_camera",
        SensorObservation(observed=True, age_seconds=0.5, payload_size=0, frame_id="camera"),
    )

    assert result.status == "invalid"
    assert result.reason == "payload is empty"


def test_gas_detector_health_rejects_non_finite_value() -> None:
    result = evaluate_sensor_health(
        "gas_detector",
        SensorObservation(observed=True, age_seconds=0.2, numeric_value=float("nan")),
    )

    assert result.status == "invalid"
    assert result.reason == "numeric value is not finite"


def test_gas_detector_health_rejects_out_of_range_value() -> None:
    result = evaluate_sensor_health(
        "gas_detector",
        SensorObservation(observed=True, age_seconds=0.2, numeric_value=-1.0),
    )

    assert result.status == "invalid"
    assert result.reason == "numeric value below minimum: -1.0 < 0.0"


def test_lidar_health_requires_finite_ranges() -> None:
    result = evaluate_sensor_health(
        "lidar",
        SensorObservation(observed=True, age_seconds=0.2, finite_range_count=0, frame_id="base_scan"),
    )

    assert result.status == "invalid"
    assert result.reason == "no finite ranges"


def test_unknown_sensor_falls_back_to_freshness_only() -> None:
    result = evaluate_sensor_health(
        "custom_sensor",
        SensorObservation(observed=True, age_seconds=0.2),
    )

    assert result.status == "healthy"
    assert result.reason is None


def test_default_policy_names_cover_initial_sensor_set() -> None:
    assert set(DEFAULT_SENSOR_HEALTH_POLICIES) >= {
        "rgb_camera",
        "thermal_camera",
        "gas_detector",
        "lidar",
        "imu",
    }
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_health.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'fireclaw_core.sensors.health'`.

- [ ] **Step 3: Implement health models**

Create `src/fireclaw_core/sensors/health.py`:

```python
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal


SensorHealthStatus = Literal["healthy", "degraded", "stale", "invalid", "unknown"]


@dataclass(frozen=True)
class SensorObservation:
    observed: bool
    age_seconds: float | None = None
    payload_size: int | None = None
    numeric_value: float | None = None
    finite_range_count: int | None = None
    frame_id: str | None = None
    timestamp_age_seconds: float | None = None
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SensorHealthPolicy:
    sensor: str
    max_age_seconds: float = 2.0
    require_payload: bool = False
    require_numeric_value: bool = False
    min_numeric_value: float | None = None
    max_numeric_value: float | None = None
    require_finite_ranges: bool = False
    require_frame_id: bool = False


@dataclass(frozen=True)
class SensorHealthResult:
    sensor: str
    status: SensorHealthStatus
    reason: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "sensor": self.sensor,
            "status": self.status,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.details:
            payload["details"] = dict(self.details)
        return payload


DEFAULT_SENSOR_HEALTH_POLICIES: dict[str, SensorHealthPolicy] = {
    "rgb_camera": SensorHealthPolicy(
        sensor="rgb_camera",
        require_payload=True,
        require_frame_id=True,
    ),
    "thermal_camera": SensorHealthPolicy(
        sensor="thermal_camera",
        require_payload=True,
        require_frame_id=True,
    ),
    "gas_detector": SensorHealthPolicy(
        sensor="gas_detector",
        require_numeric_value=True,
        min_numeric_value=0.0,
    ),
    "lidar": SensorHealthPolicy(
        sensor="lidar",
        require_finite_ranges=True,
        require_frame_id=True,
    ),
    "imu": SensorHealthPolicy(
        sensor="imu",
        require_frame_id=True,
    ),
}


def evaluate_sensor_health(
    sensor: str,
    observation: SensorObservation,
    *,
    policy: SensorHealthPolicy | None = None,
) -> SensorHealthResult:
    active_policy = policy or DEFAULT_SENSOR_HEALTH_POLICIES.get(sensor, SensorHealthPolicy(sensor=sensor))
    if not observation.observed:
        return SensorHealthResult(sensor=sensor, status="stale", reason="no recent observation")
    if observation.age_seconds is None:
        return SensorHealthResult(sensor=sensor, status="unknown", reason="observation age is unknown")
    if observation.age_seconds > active_policy.max_age_seconds:
        return SensorHealthResult(
            sensor=sensor,
            status="stale",
            reason=f"observation is stale: {observation.age_seconds:.1f}s > {active_policy.max_age_seconds:.1f}s",
        )
    if active_policy.require_payload and (observation.payload_size is None or observation.payload_size <= 0):
        return SensorHealthResult(sensor=sensor, status="invalid", reason="payload is empty")
    if active_policy.require_numeric_value:
        if observation.numeric_value is None:
            return SensorHealthResult(sensor=sensor, status="invalid", reason="numeric value is missing")
        if not math.isfinite(observation.numeric_value):
            return SensorHealthResult(sensor=sensor, status="invalid", reason="numeric value is not finite")
        if active_policy.min_numeric_value is not None and observation.numeric_value < active_policy.min_numeric_value:
            return SensorHealthResult(
                sensor=sensor,
                status="invalid",
                reason=f"numeric value below minimum: {observation.numeric_value} < {active_policy.min_numeric_value}",
            )
        if active_policy.max_numeric_value is not None and observation.numeric_value > active_policy.max_numeric_value:
            return SensorHealthResult(
                sensor=sensor,
                status="invalid",
                reason=f"numeric value above maximum: {observation.numeric_value} > {active_policy.max_numeric_value}",
            )
    if active_policy.require_finite_ranges and (observation.finite_range_count is None or observation.finite_range_count <= 0):
        return SensorHealthResult(sensor=sensor, status="invalid", reason="no finite ranges")
    if active_policy.require_frame_id and not observation.frame_id:
        return SensorHealthResult(sensor=sensor, status="degraded", reason="frame_id is missing")
    if observation.timestamp_age_seconds is not None and observation.timestamp_age_seconds > active_policy.max_age_seconds:
        return SensorHealthResult(
            sensor=sensor,
            status="stale",
            reason=f"message timestamp is stale: {observation.timestamp_age_seconds:.1f}s > {active_policy.max_age_seconds:.1f}s",
        )
    return SensorHealthResult(sensor=sensor, status="healthy")
```

- [ ] **Step 4: Export health models**

Modify `src/fireclaw_core/sensors/__init__.py` by importing:

```python
from fireclaw_core.sensors.health import (
    DEFAULT_SENSOR_HEALTH_POLICIES,
    SensorHealthPolicy,
    SensorHealthResult,
    SensorHealthStatus,
    SensorObservation,
    evaluate_sensor_health,
)
```

Add to `__all__`:

```python
    "DEFAULT_SENSOR_HEALTH_POLICIES",
    "SensorHealthPolicy",
    "SensorHealthResult",
    "SensorHealthStatus",
    "SensorObservation",
    "evaluate_sensor_health",
```

- [ ] **Step 5: Run health tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_health.py -q
```

Expected: PASS.

- [ ] **Step 6: Checkpoint Task 1**

Run:

```bash
git diff --stat

# If the user has explicitly authorized commits for this execution, then run:
git add src/fireclaw_core/sensors/health.py src/fireclaw_core/sensors/__init__.py tests/test_sensor_health.py
git commit -m "feat: add sensor health core models"
```

---

### Task 2: Add Health Diagnostics To Sensor Findings

**Files:**
- Modify: `src/fireclaw_core/sensors/discovery.py`
- Test: `tests/test_sensor_discovery.py`

- [ ] **Step 1: Write failing serialization test**

Append to `tests/test_sensor_discovery.py`:

```python
def test_sensor_finding_serializes_health_status_and_reason() -> None:
    finding = SensorFinding(
        sensor="rgb_camera",
        topic="/camera/image_raw",
        message_type="sensor_msgs/Image",
        status="degraded",
        confidence=0.9,
        source="ros1",
        reason="health check failed",
        health_status="invalid",
        health_reason="payload is empty",
    )

    payload = finding.to_dict()

    assert payload["health_status"] == "invalid"
    assert payload["health_reason"] == "payload is empty"
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_discovery.py::test_sensor_finding_serializes_health_status_and_reason -q
```

Expected: FAIL with `TypeError: SensorFinding.__init__() got an unexpected keyword argument 'health_status'`.

- [ ] **Step 3: Implement health fields on findings**

Modify `src/fireclaw_core/sensors/discovery.py`.

Add an import:

```python
from fireclaw_core.sensors.health import SensorHealthStatus
```

Add fields to `SensorFinding` after `confirmation_stale`:

```python
    health_status: SensorHealthStatus | None = None
    health_reason: str | None = None
```

In `SensorFinding.to_dict()`, after `confirmation_stale` handling in the payload:

```python
        if self.health_status is not None:
            payload["health_status"] = self.health_status
        if self.health_reason is not None:
            payload["health_reason"] = self.health_reason
```

- [ ] **Step 4: Run discovery tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_discovery.py -q
```

Expected: PASS.

- [ ] **Step 5: Checkpoint Task 2**

Run:

```bash
git diff --stat

# If the user has explicitly authorized commits for this execution, then run:
git add src/fireclaw_core/sensors/discovery.py tests/test_sensor_discovery.py
git commit -m "feat: include health diagnostics in sensor findings"
```

---

### Task 3: Teach ROS1 Discovery To Use Health Observations

**Files:**
- Modify: `src/fireclaw_core/ros/ros1_sensor_discovery.py`
- Test: `tests/test_ros1_sensor_discovery.py`

- [ ] **Step 1: Write failing ROS1 health tests**

Append to `tests/test_ros1_sensor_discovery.py`:

```python
from fireclaw_core.sensors.health import SensorObservation


def test_ros1_discovery_verifies_camera_only_when_health_is_healthy() -> None:
    discovery = Ros1SensorDiscovery(
        graph_provider=StaticRos1GraphProvider({"/camera/image_raw": "sensor_msgs/Image"}),
        message_probe=StaticRos1MessageProbe(
            {"/camera/image_raw": True},
            observations={
                "/camera/image_raw": SensorObservation(
                    observed=True,
                    age_seconds=0.2,
                    payload_size=64,
                    frame_id="camera_rgb",
                )
            },
        ),
    )

    report = discovery.discover()
    payload = report.to_dict()

    assert report.verified_sensors() == ["rgb_camera"]
    assert payload["findings"][0]["status"] == "verified"
    assert payload["findings"][0]["health_status"] == "healthy"


def test_ros1_discovery_degrades_camera_with_empty_payload() -> None:
    discovery = Ros1SensorDiscovery(
        graph_provider=StaticRos1GraphProvider({"/camera/image_raw": "sensor_msgs/Image"}),
        message_probe=StaticRos1MessageProbe(
            {"/camera/image_raw": True},
            observations={
                "/camera/image_raw": SensorObservation(
                    observed=True,
                    age_seconds=0.2,
                    payload_size=0,
                    frame_id="camera_rgb",
                )
            },
        ),
    )

    report = discovery.discover()
    payload = report.to_dict()

    assert report.verified_sensors() == []
    assert payload["findings"][0]["status"] == "degraded"
    assert payload["findings"][0]["health_status"] == "invalid"
    assert payload["findings"][0]["health_reason"] == "payload is empty"


def test_ros1_discovery_degrades_gas_detector_with_out_of_range_value() -> None:
    discovery = Ros1SensorDiscovery(
        graph_provider=StaticRos1GraphProvider({"/gas_sensor": "std_msgs/Float32"}),
        message_probe=StaticRos1MessageProbe(
            {"/gas_sensor": True},
            observations={
                "/gas_sensor": SensorObservation(
                    observed=True,
                    age_seconds=0.1,
                    numeric_value=-1.0,
                )
            },
        ),
    )

    report = discovery.discover()
    payload = report.to_dict()

    assert report.verified_sensors() == []
    assert payload["findings"][0]["sensor"] == "gas_detector"
    assert payload["findings"][0]["health_status"] == "invalid"
    assert "below minimum" in payload["findings"][0]["health_reason"]
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py::test_ros1_discovery_verifies_camera_only_when_health_is_healthy tests/test_ros1_sensor_discovery.py::test_ros1_discovery_degrades_camera_with_empty_payload tests/test_ros1_sensor_discovery.py::test_ros1_discovery_degrades_gas_detector_with_out_of_range_value -q
```

Expected: FAIL because `StaticRos1MessageProbe` does not accept `observations`.

- [ ] **Step 3: Extend static and CLI message probes**

Modify `src/fireclaw_core/ros/ros1_sensor_discovery.py`.

Add imports:

```python
from fireclaw_core.sensors.health import (
    SensorObservation,
    evaluate_sensor_health,
)
```

Change `StaticRos1MessageProbe` to:

```python
@dataclass(frozen=True)
class StaticRos1MessageProbe:
    topic_status: dict[str, bool]
    observations: dict[str, SensorObservation] | None = None

    def has_recent_message(self, topic: str, timeout_seconds: float) -> bool:
        return bool(self.topic_status.get(topic, False))

    def observe(self, topic: str, sensor: str, timeout_seconds: float) -> SensorObservation:
        if self.observations is not None and topic in self.observations:
            return self.observations[topic]
        return SensorObservation(
            observed=self.has_recent_message(topic, timeout_seconds),
            age_seconds=0.0 if self.has_recent_message(topic, timeout_seconds) else None,
        )
```

Add an `observe()` method to `Ros1CliMessageProbe`:

```python
    def observe(self, topic: str, sensor: str, timeout_seconds: float) -> SensorObservation:
        try:
            result = subprocess.run(
                [self.rostopic_executable, "echo", "-n", "1", topic],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return SensorObservation(observed=False)
        text = result.stdout.strip()
        return SensorObservation(
            observed=result.returncode == 0 and bool(text),
            age_seconds=0.0 if result.returncode == 0 and text else None,
            payload_size=len(text.encode("utf-8")) if text else 0,
            frame_id=_extract_frame_id(text),
            numeric_value=_extract_float_value(text),
            finite_range_count=_count_finite_ranges(text),
        )
```

Add helper functions near the bottom of the file:

```python
def _extract_frame_id(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("frame_id:"):
            value = stripped.split(":", 1)[1].strip().strip("'\"")
            return value or None
    return None


def _extract_float_value(text: str) -> float | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            value = stripped.split(":", 1)[1].strip()
            try:
                return float(value)
            except ValueError:
                return None
    return None


def _count_finite_ranges(text: str) -> int | None:
    if "ranges:" not in text:
        return None
    count = 0
    for token in text.replace("[", " ").replace("]", " ").replace(",", " ").split():
        try:
            value = float(token)
        except ValueError:
            continue
        if value == value and value not in {float("inf"), float("-inf")}:
            count += 1
    return count
```

- [ ] **Step 4: Use health results in discovery**

Modify `Ros1SensorDiscovery.discover()`.

Before creating a finding for a matched rule, compute:

```python
            observation = _observe_topic(
                self.message_probe,
                topic=topic,
                sensor=rule.sensor,
                timeout_seconds=self.timeout_seconds,
            )
            health = evaluate_sensor_health(rule.sensor, observation)
            finding_status = "verified" if health.status == "healthy" else "degraded"
            reason = None if finding_status == "verified" else health.reason or f"sensor health is {health.status}"
```

Replace the current `if self.message_probe.has_recent_message(...): ... else: ...` block with one `SensorFinding` append:

```python
            findings.append(
                SensorFinding(
                    sensor=rule.sensor,
                    topic=topic,
                    message_type=message_type,
                    status=finding_status,
                    confidence=rule.confidence,
                    source=rule.source,
                    reason=reason,
                    confirmed=rule.confirmed,
                    confirmation_stale=rule.confirmed and confirmation_stale,
                    health_status=health.status,
                    health_reason=health.reason,
                )
            )
```

Add helper:

```python
def _observe_topic(
    probe: Ros1MessageProbe,
    *,
    topic: str,
    sensor: str,
    timeout_seconds: float,
) -> SensorObservation:
    observe = getattr(probe, "observe", None)
    if callable(observe):
        return observe(topic, sensor, timeout_seconds)
    observed = probe.has_recent_message(topic, timeout_seconds)
    return SensorObservation(
        observed=observed,
        age_seconds=0.0 if observed else None,
    )
```

- [ ] **Step 5: Run ROS1 discovery tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py -q
```

Expected: PASS after existing tests are updated only if needed to provide valid health observations for camera/lidar/gas cases.

- [ ] **Step 6: Checkpoint Task 3**

Run:

```bash
git diff --stat

# If the user has explicitly authorized commits for this execution, then run:
git add src/fireclaw_core/ros/ros1_sensor_discovery.py tests/test_ros1_sensor_discovery.py
git commit -m "feat: apply sensor health checks during ros1 discovery"
```

---

### Task 4: Preserve Backward Compatibility For Existing Freshness Tests

**Files:**
- Modify: `tests/test_ros1_sensor_discovery.py`
- Modify: `src/fireclaw_core/ros/ros1_sensor_discovery.py`

- [ ] **Step 1: Run the existing broader discovery suite**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_discovery.py tests/test_sensor_health.py tests/test_ros1_sensor_discovery.py tests/test_ros1_adapter_state.py -q
```

Expected: PASS. If a previously valid non-inspectable sensor now fails because a strict policy requires details that the old fake does not provide, apply Step 2.

- [ ] **Step 2: Keep fallback observations freshness-only when payload is not inspectable**

If old tests fail because fallback observations lack payload/frame details, modify `_observe_topic()` fallback to mark details:

```python
    return SensorObservation(
        observed=observed,
        age_seconds=0.0 if observed else None,
        details={"inspection": "freshness_only"},
    )
```

Then modify `evaluate_sensor_health()` in `src/fireclaw_core/sensors/health.py` immediately after stale age checks:

```python
    if observation.details.get("inspection") == "freshness_only":
        return SensorHealthResult(sensor=sensor, status="healthy")
```

This preserves compatibility for existing probes while allowing richer static and CLI observations to fail strict policies.

- [ ] **Step 3: Run compatibility suite**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_health.py tests/test_ros1_sensor_discovery.py tests/test_ros1_adapter_state.py -q
```

Expected: PASS.

- [ ] **Step 4: Checkpoint Task 4**

Run:

```bash
git diff --stat

# If the user has explicitly authorized commits for this execution, then run:
git add src/fireclaw_core/sensors/health.py src/fireclaw_core/ros/ros1_sensor_discovery.py tests/test_ros1_sensor_discovery.py tests/test_ros1_adapter_state.py
git commit -m "fix: preserve freshness-only health fallback"
```

---

### Task 5: Expose Health Diagnostics Through Robot State

**Files:**
- Modify: `tests/test_ros1_adapter_state.py`
- Modify: `src/fireclaw_core/agent/robot.py` only if diagnostics are not already passed through

- [ ] **Step 1: Add robot state health diagnostic test**

Append to `tests/test_ros1_adapter_state.py`:

```python
from fireclaw_core.sensors.health import SensorObservation


def test_ros1_adapter_state_includes_sensor_health_diagnostics() -> None:
    adapter = Ros1RobotAdapter(
        config=Ros1AdapterConfig(robot_id="robot-1"),
        sensor_discovery=Ros1SensorDiscovery(
            graph_provider=StaticRos1GraphProvider({"/camera/image_raw": "sensor_msgs/Image"}),
            message_probe=StaticRos1MessageProbe(
                {"/camera/image_raw": True},
                observations={
                    "/camera/image_raw": SensorObservation(
                        observed=True,
                        age_seconds=0.1,
                        payload_size=0,
                        frame_id="camera",
                    )
                },
            ),
        ),
    )

    state = adapter.get_robot_state()

    assert state.available_sensors == []
    assert state.sensor_diagnostics is not None
    finding = state.sensor_diagnostics["findings"][0]
    assert finding["sensor"] == "rgb_camera"
    assert finding["status"] == "degraded"
    assert finding["health_status"] == "invalid"
    assert finding["health_reason"] == "payload is empty"
```

- [ ] **Step 2: Run test**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_adapter_state.py::test_ros1_adapter_state_includes_sensor_health_diagnostics -q
```

Expected: PASS if `Ros1RobotAdapter.get_robot_state()` already serializes `SensorDiscoveryReport.to_dict()`. If it fails because diagnostics are not passed through, apply Step 3.

- [ ] **Step 3: Pass discovery report diagnostics through robot state**

Modify `src/fireclaw_core/agent/robot.py` inside `Ros1RobotAdapter.get_robot_state()`.

Ensure the discovery branch contains:

```python
                report = self.sensor_discovery.discover()
                sensor_diagnostics = report.to_dict()
                available_sensors = report.verified_sensors()
```

No other robot state changes are needed.

- [ ] **Step 4: Run adapter state tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_adapter_state.py -q
```

Expected: PASS.

- [ ] **Step 5: Checkpoint Task 5**

Run:

```bash
git diff --stat

# If the user has explicitly authorized commits for this execution, then run:
git add tests/test_ros1_adapter_state.py src/fireclaw_core/agent/robot.py
git commit -m "test: expose sensor health diagnostics in robot state"
```

---

### Task 6: Prove SafetyGate Still Uses Only Healthy Verified Sensors

**Files:**
- Modify: `tests/test_agent.py`
- Modify: `tests/test_safety.py` only if a direct SafetyGate regression is clearer

- [ ] **Step 1: Add agent regression for unhealthy discovered camera**

Append to `tests/test_agent.py`:

```python
from fireclaw_core.sensors.health import SensorObservation


def test_agent_blocks_search_when_discovered_camera_health_is_invalid() -> None:
    robot = Ros1RobotAdapter(
        config=Ros1AdapterConfig(robot_id="robot-1"),
        sensor_discovery=Ros1SensorDiscovery(
            graph_provider=StaticRos1GraphProvider({
                "/camera/image_raw": "sensor_msgs/Image",
                "/thermal/image_raw": "sensor_msgs/Image",
            }),
            message_probe=StaticRos1MessageProbe(
                {
                    "/camera/image_raw": True,
                    "/thermal/image_raw": True,
                },
                observations={
                    "/camera/image_raw": SensorObservation(
                        observed=True,
                        age_seconds=0.1,
                        payload_size=0,
                        frame_id="camera",
                    ),
                    "/thermal/image_raw": SensorObservation(
                        observed=True,
                        age_seconds=0.1,
                        payload_size=64,
                        frame_id="thermal",
                    ),
                },
            ),
        ),
        dry_run=True,
    )
    agent = FireClawAgent(robot=robot, workspace_skills_dir=None, dry_run=True)

    result = agent.run("去二楼救人")

    assert result["status"] == "block"
    assert "rgb_camera" in result["message"]
    assert result["robot_state"]["available_sensors"] == ["thermal_camera"]
```

- [ ] **Step 2: Run regression**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py::test_agent_blocks_search_when_discovered_camera_health_is_invalid -q
```

Expected: PASS after Tasks 1-5.

- [ ] **Step 3: Run relevant agent/safety suite**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py tests/test_safety.py tests/test_safety_unknown_state.py -q
```

Expected: PASS.

- [ ] **Step 4: Checkpoint Task 6**

Run:

```bash
git diff --stat

# If the user has explicitly authorized commits for this execution, then run:
git add tests/test_agent.py tests/test_safety.py tests/test_safety_unknown_state.py
git commit -m "test: require healthy verified sensors for safety"
```

---

### Task 7: Document Phase 2 Runtime Behavior

**Files:**
- Modify: `docs/deployment/ros1-gazebo-debugging-guide.md`
- Modify: `memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md`

- [ ] **Step 1: Update Gazebo debugging guide**

Add a section to `docs/deployment/ros1-gazebo-debugging-guide.md`:

```markdown
## Sensor Health Diagnostics

FireClaw separates topic discovery from sensor verification.

- `discovered`: a topic/type matched a known or profile rule.
- `healthy`: the current stream passed the sensor health policy.
- `verified`: the sensor is mapped and healthy.
- `degraded`: the topic exists but the health policy failed.

SafetyGate only uses verified sensors from `RobotState.available_sensors`.

For Gazebo TurtleBot3, `search_for_victims` remains blocked unless a camera topic is present and its health check passes. A camera topic with empty or stale data is reported in `sensor_diagnostics.findings` but is not added to `available_sensors`.
```

- [ ] **Step 2: Update memory after verification**

Append to `memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md`:

```markdown

## Sensor Health Phase 2 Implementation (2026-06-14)

Implemented per-sensor health checks for ROS1 sensor discovery.

Behavior:

- `SensorObservation`, `SensorHealthPolicy`, and `SensorHealthResult` model sensor health.
- ROS1 discovery now marks a sensor verified only when health status is `healthy`.
- Invalid camera payload, invalid gas value, and invalid lidar ranges produce degraded findings.
- Robot state diagnostics include `health_status` and `health_reason`.
- SafetyGate continues to consume only `RobotState.available_sensors`.

Verification:

- Sensor health suite: write the terminal summary from the Task 1 and Task 3 pytest commands after those commands have run.
- Adapter/agent safety suite: write the terminal summary from the Task 5 and Task 6 pytest commands after those commands have run.
- Full suite: write the terminal summary from the Task 8 full-suite command after that command has run.
```

Do not append this memory section until the referenced pytest commands have completed. Copy the observed summaries from the terminal output.

- [ ] **Step 3: Checkpoint Task 7**

Run:

```bash
git diff --stat

# If the user has explicitly authorized commits for this execution, then run:
git add docs/deployment/ros1-gazebo-debugging-guide.md memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md
git commit -m "docs: document sensor health diagnostics"
```

---

### Task 8: Phase 2 Verification

**Files:**
- No source changes expected.

- [ ] **Step 1: Run focused Phase 2 suite**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_health.py tests/test_sensor_discovery.py tests/test_ros1_sensor_discovery.py tests/test_ros1_adapter_state.py tests/test_agent.py::test_agent_blocks_search_when_discovered_camera_health_is_invalid -q
```

Expected: PASS.

- [ ] **Step 2: Run broader sensor/profile/agent suite**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_health.py tests/test_sensor_discovery.py tests/test_ros1_sensor_discovery.py tests/test_robot_profile.py tests/test_ros1_adapter_state.py tests/test_gateway_robot_profile_config.py tests/test_safety.py tests/test_safety_unknown_state.py tests/test_agent.py tests/test_gateway_structured_task.py tests/test_mission_cli.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: PASS with the current baseline count plus new tests.

- [ ] **Step 4: Inspect diff**

Run:

```bash
git diff --stat
git diff -- src/fireclaw_core/sensors/health.py src/fireclaw_core/sensors/discovery.py src/fireclaw_core/ros/ros1_sensor_discovery.py src/fireclaw_core/agent/robot.py
```

Expected:

- health model is isolated in `sensors/health.py`;
- discovery layer owns health diagnostics;
- SafetyGate logic is not expanded with ROS1-specific details;
- no unrelated refactors.

---

# Phase 3 Master Plan: Safety-Critical Sensor Policy

Do not start Phase 3 until Phase 2 is complete and full suite is green.

Phase 3 deliverables:

1. Create `src/fireclaw_core/safety/sensor_policy.py`.
2. Create `tests/test_sensor_policy.py`.
3. Add policy actions: `allow`, `warn`, `degrade`, `escalate`, `block`.
4. Map initial critical sensors:
   - `gas_detector`: block or escalate for hazardous atmosphere decisions.
   - `thermal_camera`: block or escalate for victim/fire assessment.
   - `lidar`: block or escalate for navigation in real mode.
   - `rgb_camera`: block search when no alternate victim-search sensor is verified.
   - `imu`: warn/degrade/escalate depending on navigation risk.
5. Integrate policy decisions without adding ROS-specific logic to SafetyGate.

Phase 3 acceptance:

- A degraded `gas_detector` blocks or escalates gas-dependent skills.
- A degraded `lidar` blocks real navigation.
- A degraded `rgb_camera` blocks `search_for_victims` when no alternative sensor is verified.
- Dry-run/simulator policies are explicit and cannot apply silently to real mode.

# Phase 4 Master Plan: Operator Confirmation Loop

Do not start Phase 4 until Phase 1 and Phase 2 diagnostics are stable.

Phase 4 deliverables:

1. Extend profile discovery command group:
   - `robot-profile diff-discovery`
   - `robot-profile confirm-discovery`
2. Add profile fields:
   - `confirmed_at`
   - `confirmed_by`
   - selected confirmed rules bound to current fingerprint.
3. Add tests for:
   - confirming only selected rules;
   - changed fingerprint making prior confirmation stale;
   - parseable profile write-back;
   - audit output in JSON.

Phase 4 acceptance:

- Confirmation is never implicit.
- A changed fingerprint invalidates previous confirmation freshness.
- Operator identity and confirmation timestamp are visible in profile or command output.

# Phase 5 Master Plan: Adapter-Agnostic Discovery Backend

Do not start Phase 5 until ROS1 discovery fields are stable after Phase 2 and Phase 3.

Phase 5 deliverables:

1. Create common backend protocol:

```python
class SensorDiscoveryBackend:
    def discover(self) -> SensorDiscoveryReport:
        ...
```

2. Rename or wrap ROS1 discovery as a backend implementation.
3. Add static simulator/dry-run backend with explicit source labels.
4. Add tests proving real mode rejects static-only verified safety-critical sensors.
5. Keep SafetyGate consuming backend-independent verified sensors.

Phase 5 acceptance:

- ROS1, simulator, and dry-run discovery produce the same report shape.
- Real mode does not accept static-only discovery for critical sensors.
- Adding ROS2 later does not require changing SafetyGate or skill metadata.

---

## Final Completion Criteria

The sensor capability grounding final version is complete when:

- Phase 1 fingerprint/stale-profile detection remains green.
- Phase 2 health checks are implemented and verified.
- Phase 3 policy consumes health status and task risk.
- Phase 4 operator confirmation is explicit and auditable.
- Phase 5 backend interface keeps SafetyGate backend-agnostic.
- Full suite passes.
- Gazebo debugging docs explain expected block/degraded behavior.
- Memory records contain exact verification commands and results for every phase.
