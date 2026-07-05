# Sensor Policy Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the remaining Phase 2 ROS1 health false positives, then add Phase 3 safety-critical sensor policy so FireClaw can turn sensor health diagnostics into explicit `allow`, `warn`, `escalate`, or `block` safety decisions.

**Architecture:** Keep discovery responsible for producing `SensorFinding` and `RobotState.sensor_diagnostics`; keep SafetyGate responsible for final execution decisions. Add a small ROS1 CLI parsing hardening step before Phase 3 because SafetyGate policy must not consume false `healthy` inputs. Add `fireclaw_core.safety.sensor_policy` as a ROS-agnostic policy layer that maps `(skill, required sensor, health status, runtime mode)` into a safety action and human-readable reason.

**Tech Stack:** Python 3.11 dataclasses, existing `SensorFinding` diagnostics, existing `SafetyGate`, existing `Skill.required_sensors`, pytest, no new runtime dependency.

---

## Source Context

- Master plan: `docs/superpowers/plans/2026-06-14-sensor-capability-grounding-master-plan.md`
- Final design: `docs/superpowers/specs/2026-06-14-sensor-capability-grounding-final-design.md`
- Phase 2 review memory: `memory/2026-06-15/fireclaw-phase2-health-review.md`

Current Phase 2 focused verification:

```bash
.venv/bin/python -m pytest tests/test_sensor_health.py tests/test_sensor_discovery.py tests/test_ros1_sensor_discovery.py tests/test_ros1_adapter_state.py tests/test_agent.py::test_agent_blocks_search_when_discovered_camera_health_is_invalid -q
# 35 passed
```

Known Phase 2 gaps to fix before Phase 3:

- `sensor_msgs/Image` with `data: []` can be marked healthy because `payload_size` is currently the whole YAML text byte length.
- `sensor_msgs/LaserScan` with `ranges: []` can be marked healthy because `_count_finite_ranges()` counts numeric metadata outside the `ranges` field.

## File Structure

- Modify `src/fireclaw_core/ros/ros1_sensor_discovery.py`
  - Add focused ROS YAML-style array field parsing helpers.
  - Make image payload size use `data` item count for camera sensors.
  - Make lidar finite range count use only the `ranges` field.
- Modify `tests/test_ros1_sensor_discovery.py`
  - Add regression tests for real `rostopic echo`-style image and laser scan text.
- Create `src/fireclaw_core/safety/sensor_policy.py`
  - Owns sensor policy actions, policy decisions, and default skill/sensor health behavior.
- Modify `src/fireclaw_core/safety/__init__.py`
  - Re-export sensor policy models if this package currently exposes safety models there.
- Modify `src/fireclaw_core/safety/safety.py`
  - Call sensor policy while evaluating skill required sensors.
  - Convert policy `block` to block reasons, `escalate` to confirmation reasons, and `warn`/`degrade` to warnings.
- Modify `src/fireclaw_core/execution/skills.py`
  - Add `lidar` to navigation skill requirements so lidar health can actually guard navigation.
  - Add `lidar` to dry-run/simulator default adapter sensor lists only if tests show those adapters must model navigation-capable robots.
- Modify `src/fireclaw_core/agent/robot.py`
  - Update default test/simulator sensor sets to include `lidar` where they represent navigation-capable robots.
- Create `tests/test_sensor_policy.py`
  - Unit tests for policy decisions independent of SafetyGate.
- Modify `tests/test_safety.py`
  - Integration tests proving SafetyGate uses policy output.
- Modify `docs/deployment/ros1-gazebo-debugging-guide.md`
  - Document policy behavior and diagnostics.
- Modify `memory/2026-06-15/fireclaw-phase2-health-review.md`
  - Record that the Phase 2 parser false positives were fixed and which tests prove it.

---

### Task 0: Fix ROS1 Health Parsing False Positives

**Files:**
- Modify: `src/fireclaw_core/ros/ros1_sensor_discovery.py`
- Modify: `tests/test_ros1_sensor_discovery.py`
- Modify: `memory/2026-06-15/fireclaw-phase2-health-review.md`

- [ ] **Step 1: Write failing regression tests for ROS CLI text**

Append these imports and tests to `tests/test_ros1_sensor_discovery.py`. If `Ros1CliMessageProbe` is not already imported, add it to the existing import list from `fireclaw_core.ros.ros1_sensor_discovery`.

```python
import subprocess
```

```python
def test_ros1_cli_probe_reports_empty_image_data_as_empty_payload(monkeypatch) -> None:
    image_text = """header:
  seq: 1
  stamp:
    secs: 12
    nsecs: 34
  frame_id: "camera"
height: 480
width: 640
encoding: "rgb8"
is_bigendian: 0
step: 1920
data: []"""

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=image_text, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    observation = Ros1CliMessageProbe().observe("/camera/image_raw", "rgb_camera", 1.0)

    assert observation.observed is True
    assert observation.frame_id == "camera"
    assert observation.payload_size == 0
```

```python
def test_ros1_cli_probe_counts_non_empty_image_data(monkeypatch) -> None:
    image_text = """header:
  frame_id: "camera"
height: 1
width: 2
encoding: "mono8"
data: [0, 17]"""

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=image_text, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    observation = Ros1CliMessageProbe().observe("/camera/image_raw", "rgb_camera", 1.0)

    assert observation.payload_size == 2
```

```python
def test_ros1_cli_probe_reports_empty_laserscan_ranges_as_zero(monkeypatch) -> None:
    scan_text = """header:
  seq: 1
  stamp:
    secs: 12
    nsecs: 34
  frame_id: "laser"
angle_min: -1.57
angle_max: 1.57
angle_increment: 0.01
time_increment: 0.0
scan_time: 0.1
range_min: 0.12
range_max: 3.5
ranges: []
intensities: []"""

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=scan_text, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    observation = Ros1CliMessageProbe().observe("/scan", "lidar", 1.0)

    assert observation.frame_id == "laser"
    assert observation.finite_range_count == 0
```

```python
def test_ros1_cli_probe_counts_only_laserscan_ranges(monkeypatch) -> None:
    scan_text = """header:
  seq: 1
  stamp:
    secs: 12
    nsecs: 34
  frame_id: "laser"
angle_min: -1.57
angle_max: 1.57
ranges: [inf, .nan, 0.75, 2.5]
intensities: [10, 20]"""

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=scan_text, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    observation = Ros1CliMessageProbe().observe("/scan", "lidar", 1.0)

    assert observation.finite_range_count == 2
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py -q
```

Expected: at least the empty image and empty lidar tests fail because current parsing returns a nonzero payload size and nonzero finite range count.

- [ ] **Step 3: Implement focused array parsing helpers**

In `src/fireclaw_core/ros/ros1_sensor_discovery.py`, replace the current `payload_size=len(text.encode("utf-8")) if text else 0` line inside `Ros1CliMessageProbe.observe()` with sensor-aware payload extraction:

```python
        payload_size = _extract_payload_size(text, sensor)
        return SensorObservation(
            observed=result.returncode == 0 and bool(text),
            age_seconds=0.0 if result.returncode == 0 and text else None,
            payload_size=payload_size,
            frame_id=_extract_frame_id(text),
            numeric_value=_extract_float_value(text),
            finite_range_count=_count_finite_ranges(text),
        )
```

Add these helpers below `_extract_float_value()` and update `_count_finite_ranges()` to use them:

```python
def _extract_payload_size(text: str, sensor: str) -> int:
    if sensor in {"rgb_camera", "thermal_camera"}:
        return _count_array_items(text, "data") or 0
    return len(text.encode("utf-8")) if text else 0


def _count_array_items(text: str, field_name: str) -> int | None:
    values = _extract_array_values(text, field_name)
    if values is None:
        return None
    return len(values)


def _extract_array_values(text: str, field_name: str) -> list[str] | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(f"{field_name}:"):
            continue
        after_colon = stripped.split(":", 1)[1].strip()
        if after_colon.startswith("["):
            return _parse_inline_array(after_colon, lines[index + 1 :])
        if after_colon:
            return [after_colon]
        return _parse_block_array(lines[index + 1 :])
    return None


def _parse_inline_array(first_fragment: str, following_lines: list[str]) -> list[str]:
    fragments = [first_fragment]
    if "]" not in first_fragment:
        for line in following_lines:
            stripped = line.strip()
            fragments.append(stripped)
            if "]" in stripped:
                break
    joined = " ".join(fragments)
    start = joined.find("[")
    end = joined.find("]", start + 1)
    if start == -1 or end == -1:
        return []
    inner = joined[start + 1 : end].strip()
    if not inner:
        return []
    return [item.strip() for item in inner.split(",") if item.strip()]


def _parse_block_array(following_lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in following_lines:
        if line and not line.startswith((" ", "\t", "-")):
            break
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("-"):
            value = stripped[1:].strip()
            if value:
                values.append(value)
    return values
```

Replace `_count_finite_ranges()` with:

```python
def _count_finite_ranges(text: str) -> int | None:
    values = _extract_array_values(text, "ranges")
    if values is None:
        return None
    count = 0
    for token in values:
        try:
            value = float(token)
        except ValueError:
            continue
        if value == value and value not in {float("inf"), float("-inf")}:
            count += 1
    return count
```

- [ ] **Step 4: Run regression tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py tests/test_sensor_health.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Record parser fix in memory**

Append this entry to `memory/2026-06-15/fireclaw-phase2-health-review.md`:

```markdown
## Parser Fix Update

- Fixed `Ros1CliMessageProbe.observe()` so camera payload size comes from parsed `data` array item count instead of whole YAML text size.
- Fixed LaserScan finite range counting so only the `ranges` field is inspected.
- Added ROS CLI text regression tests for empty/non-empty image data and empty/non-empty laser scan ranges.
- Verification:
  - `.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py tests/test_sensor_health.py -q`
```

- [ ] **Step 6: Commit Task 0**

```bash
git add src/fireclaw_core/ros/ros1_sensor_discovery.py tests/test_ros1_sensor_discovery.py memory/2026-06-15/fireclaw-phase2-health-review.md
git commit -m "fix: parse ros1 sensor payload health strictly"
```

---

### Task 1: Add Safety-Critical Sensor Policy Core

**Files:**
- Create: `src/fireclaw_core/safety/sensor_policy.py`
- Create: `tests/test_sensor_policy.py`

- [ ] **Step 1: Write failing policy tests**

Create `tests/test_sensor_policy.py`:

```python
from fireclaw_core.safety.sensor_policy import evaluate_sensor_policy


def test_policy_allows_healthy_required_sensor() -> None:
    decision = evaluate_sensor_policy(
        skill_name="search_for_victims",
        sensor="rgb_camera",
        health_status="healthy",
        mode="ros1",
        dry_run=False,
        verified_sensors={"rgb_camera"},
    )

    assert decision.action == "allow"
    assert decision.reason is None


def test_policy_blocks_victim_search_when_rgb_camera_degraded_without_alternative() -> None:
    decision = evaluate_sensor_policy(
        skill_name="search_for_victims",
        sensor="rgb_camera",
        health_status="degraded",
        health_reason="payload is empty",
        mode="ros1",
        dry_run=False,
        verified_sensors=set(),
    )

    assert decision.action == "block"
    assert decision.reason == "Skill search_for_victims requires rgb_camera, but health is degraded: payload is empty"


def test_policy_escalates_victim_search_when_rgb_camera_degraded_but_thermal_verified() -> None:
    decision = evaluate_sensor_policy(
        skill_name="search_for_victims",
        sensor="rgb_camera",
        health_status="degraded",
        health_reason="payload is empty",
        mode="ros1",
        dry_run=False,
        verified_sensors={"thermal_camera"},
    )

    assert decision.action == "escalate"
    assert decision.reason == "Skill search_for_victims requires rgb_camera, but thermal_camera is the only verified victim-search sensor."


def test_policy_blocks_gas_detector_failures_for_real_execution() -> None:
    decision = evaluate_sensor_policy(
        skill_name="enter_hazard_zone",
        sensor="gas_detector",
        health_status="stale",
        health_reason="no recent observation",
        mode="ros1",
        dry_run=False,
        verified_sensors=set(),
    )

    assert decision.action == "block"
    assert decision.reason == "Skill enter_hazard_zone requires gas_detector, but health is stale: no recent observation"


def test_policy_blocks_lidar_failures_for_real_navigation() -> None:
    decision = evaluate_sensor_policy(
        skill_name="navigate_to_floor",
        sensor="lidar",
        health_status="invalid",
        health_reason="no finite ranges",
        mode="ros1",
        dry_run=False,
        verified_sensors=set(),
    )

    assert decision.action == "block"
    assert decision.reason == "Skill navigate_to_floor requires lidar, but health is invalid: no finite ranges"


def test_policy_warns_for_unknown_sensor_health_in_dry_run() -> None:
    decision = evaluate_sensor_policy(
        skill_name="navigate_to_floor",
        sensor="lidar",
        health_status="unknown",
        health_reason="observation age is unknown",
        mode="simulator",
        dry_run=True,
        verified_sensors=set(),
    )

    assert decision.action == "warn"
    assert decision.reason == "Skill navigate_to_floor requires lidar, but health is unknown: observation age is unknown"


def test_policy_escalates_unknown_sensor_health_for_real_execution() -> None:
    decision = evaluate_sensor_policy(
        skill_name="navigate_to_floor",
        sensor="imu",
        health_status="unknown",
        health_reason="observation age is unknown",
        mode="ros1",
        dry_run=False,
        verified_sensors=set(),
    )

    assert decision.action == "escalate"
    assert decision.reason == "Skill navigate_to_floor requires imu, but health is unknown: observation age is unknown"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_policy.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'fireclaw_core.safety.sensor_policy'`.

- [ ] **Step 3: Implement policy model**

Create `src/fireclaw_core/safety/sensor_policy.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fireclaw_core.sensors.health import SensorHealthStatus


SensorPolicyAction = Literal["allow", "warn", "degrade", "escalate", "block"]


@dataclass(frozen=True)
class SensorPolicyDecision:
    action: SensorPolicyAction
    reason: str | None = None


VICTIM_SEARCH_ALTERNATIVES: dict[str, frozenset[str]] = {
    "rgb_camera": frozenset({"thermal_camera"}),
    "thermal_camera": frozenset({"rgb_camera"}),
}

NAVIGATION_SKILLS = frozenset({"navigate_to_floor", "return_to_safe_zone"})
VICTIM_SEARCH_SKILLS = frozenset({"search_for_victims", "assess_victim"})


def evaluate_sensor_policy(
    *,
    skill_name: str,
    sensor: str,
    health_status: SensorHealthStatus | str | None,
    mode: str,
    dry_run: bool,
    verified_sensors: set[str],
    health_reason: str | None = None,
) -> SensorPolicyDecision:
    if health_status == "healthy":
        return SensorPolicyDecision(action="allow")

    if health_status is None:
        return SensorPolicyDecision(
            action="block",
            reason=f"Skill {skill_name} requires unavailable sensor: {sensor}",
        )

    reason = _format_reason(skill_name, sensor, health_status, health_reason)

    if health_status == "unknown":
        return SensorPolicyDecision(action="warn" if dry_run else "escalate", reason=reason)

    if sensor == "gas_detector":
        return SensorPolicyDecision(action="block", reason=reason)

    if sensor == "lidar" and skill_name in NAVIGATION_SKILLS:
        return SensorPolicyDecision(action="warn" if dry_run else "block", reason=reason)

    if sensor == "imu" and skill_name in NAVIGATION_SKILLS:
        return SensorPolicyDecision(action="warn" if dry_run else "escalate", reason=reason)

    if sensor in {"rgb_camera", "thermal_camera"} and skill_name in VICTIM_SEARCH_SKILLS:
        alternatives = VICTIM_SEARCH_ALTERNATIVES.get(sensor, frozenset())
        if alternatives & verified_sensors:
            alternative = sorted(alternatives & verified_sensors)[0]
            return SensorPolicyDecision(
                action="escalate" if not dry_run else "warn",
                reason=f"Skill {skill_name} requires {sensor}, but {alternative} is the only verified victim-search sensor.",
            )
        return SensorPolicyDecision(action="block", reason=reason)

    return SensorPolicyDecision(action="warn" if dry_run else "escalate", reason=reason)


def _format_reason(
    skill_name: str,
    sensor: str,
    health_status: SensorHealthStatus | str,
    health_reason: str | None,
) -> str:
    suffix = f": {health_reason}" if health_reason else ""
    return f"Skill {skill_name} requires {sensor}, but health is {health_status}{suffix}"
```

- [ ] **Step 4: Run policy tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_policy.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/fireclaw_core/safety/sensor_policy.py tests/test_sensor_policy.py
git commit -m "feat: add safety-critical sensor policy"
```

---

### Task 2: Wire Sensor Policy Into SafetyGate

**Files:**
- Modify: `src/fireclaw_core/safety/safety.py`
- Modify: `src/fireclaw_core/execution/skills.py`
- Modify: `src/fireclaw_core/agent/robot.py`
- Modify: `tests/test_safety.py`
- Modify: `tests/test_skill_metadata.py`

- [ ] **Step 1: Write failing SafetyGate policy integration tests**

Append these tests to `tests/test_safety.py`:

```python
def test_safety_blocks_navigation_when_lidar_health_is_invalid() -> None:
    planning_result = RuleBasedPlanner().plan("去二楼救人")
    robot = DryRunRobotAdapter(robot_id="robot-1")
    registry = create_default_skill_registry(robot)
    robot_state = RobotState(
        robot_id="robot-1",
        mode="ros1",
        dry_run=False,
        online=True,
        battery_percent=100.0,
        current_floor=1,
        available_sensors=["rgb_camera"],
        supports_real_execution=True,
        sensor_diagnostics={
            "source": "ros1",
            "verified_sensors": ["rgb_camera"],
            "findings": [
                {
                    "sensor": "lidar",
                    "topic": "/scan",
                    "message_type": "sensor_msgs/LaserScan",
                    "status": "degraded",
                    "health_status": "invalid",
                    "health_reason": "no finite ranges",
                    "confidence": 0.99,
                    "source": "ros1",
                }
            ],
        },
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=False,
        robot_state=robot_state,
        environment_state=EnvironmentState(reachable_floors=[2]),
        operator_confirmed=True,
    )

    assert decision.status == "block"
    assert "Skill navigate_to_floor requires lidar, but health is invalid: no finite ranges" in decision.reasons
```

```python
def test_safety_requires_confirmation_when_imu_health_is_unknown_for_navigation() -> None:
    planning_result = RuleBasedPlanner().plan("运行 navigate_to_floor")
    robot = DryRunRobotAdapter(robot_id="robot-1")
    registry = SkillRegistry(
        skills={
            "navigate_to_floor": Skill(
                name="navigate_to_floor",
                description="Navigate robot to a target floor.",
                handler=lambda inputs: _successful_result(),
                required_sensors=["imu"],
                allow_real_robot=True,
                dry_run_only=False,
                idempotent=True,
            )
        }
    )
    robot_state = RobotState(
        robot_id="robot-1",
        mode="ros1",
        dry_run=False,
        online=True,
        battery_percent=100.0,
        current_floor=1,
        available_sensors=[],
        supports_real_execution=True,
        sensor_diagnostics={
            "source": "ros1",
            "verified_sensors": [],
            "findings": [
                {
                    "sensor": "imu",
                    "topic": "/imu",
                    "message_type": "sensor_msgs/Imu",
                    "status": "degraded",
                    "health_status": "unknown",
                    "health_reason": "observation age is unknown",
                    "confidence": 0.99,
                    "source": "ros1",
                }
            ],
        },
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=False,
        robot_state=robot_state,
        operator_confirmed=False,
    )

    assert decision.status == "require_confirmation"
    assert "Skill navigate_to_floor requires imu, but health is unknown: observation age is unknown" in decision.reasons
```

```python
def test_safety_warns_for_unknown_lidar_health_in_dry_run() -> None:
    planning_result = RuleBasedPlanner().plan("运行 navigate_to_floor")
    registry = SkillRegistry(
        skills={
            "navigate_to_floor": Skill(
                name="navigate_to_floor",
                description="Navigate robot to a target floor.",
                handler=lambda inputs: _successful_result(),
                required_sensors=["lidar"],
                dry_run_only=True,
                idempotent=True,
            )
        }
    )
    robot_state = RobotState(
        robot_id="robot-1",
        mode="simulator",
        dry_run=True,
        online=True,
        battery_percent=100.0,
        current_floor=1,
        available_sensors=[],
        supports_real_execution=False,
        sensor_diagnostics={
            "source": "simulator",
            "verified_sensors": [],
            "findings": [
                {
                    "sensor": "lidar",
                    "topic": "/scan",
                    "message_type": "sensor_msgs/LaserScan",
                    "status": "degraded",
                    "health_status": "unknown",
                    "health_reason": "observation age is unknown",
                    "confidence": 0.99,
                    "source": "simulator",
                }
            ],
        },
    )

    decision = SafetyGate().evaluate(
        planning_result,
        registry,
        dry_run=True,
        robot_state=robot_state,
    )

    assert decision.status == "allow"
    assert "Skill navigate_to_floor requires lidar, but health is unknown: observation age is unknown" in decision.warnings
```

- [ ] **Step 2: Write failing metadata test for navigation sensor requirement**

Add to `tests/test_skill_metadata.py`:

```python
def test_navigation_skill_requires_lidar() -> None:
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="r1"))

    assert registry.get("navigate_to_floor").required_sensors == ["lidar"]
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_safety.py::test_safety_blocks_navigation_when_lidar_health_is_invalid tests/test_safety.py::test_safety_requires_confirmation_when_imu_health_is_unknown_for_navigation tests/test_safety.py::test_safety_warns_for_unknown_lidar_health_in_dry_run tests/test_skill_metadata.py::test_navigation_skill_requires_lidar -q
```

Expected: FAIL because SafetyGate does not call sensor policy and `navigate_to_floor` does not require `lidar`.

- [ ] **Step 4: Add sensor diagnostics helpers to SafetyGate**

In `src/fireclaw_core/safety/safety.py`, import the policy:

```python
from fireclaw_core.safety.sensor_policy import evaluate_sensor_policy
```

Add these helper functions near the bottom of the file:

```python
def _sensor_findings_by_name(robot_state: RobotState | None) -> dict[str, dict[str, object]]:
    if robot_state is None or not robot_state.sensor_diagnostics:
        return {}
    findings = robot_state.sensor_diagnostics.get("findings")
    if not isinstance(findings, list):
        return {}
    result: dict[str, dict[str, object]] = {}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        sensor = finding.get("sensor")
        if isinstance(sensor, str) and sensor not in result:
            result[sensor] = finding
    return result
```

- [ ] **Step 5: Replace missing sensor handling with policy-aware handling**

In `SafetyGate.evaluate()`, before the loop over plan steps, create diagnostics lookup:

```python
        sensor_findings = _sensor_findings_by_name(robot_state)
```

Inside the `for sensor in skill.required_sensors:` block, replace the simple missing-sensor append with:

```python
                finding = sensor_findings.get(sensor)
                health_status = finding.get("health_status") if finding else ("healthy" if sensor in sensors else None)
                health_reason = finding.get("health_reason") if finding else None
                policy_decision = evaluate_sensor_policy(
                    skill_name=step.skill_name,
                    sensor=sensor,
                    health_status=str(health_status) if health_status is not None else None,
                    health_reason=str(health_reason) if health_reason is not None else None,
                    mode=robot_state.mode if robot_state is not None else "unknown",
                    dry_run=dry_run,
                    verified_sensors=set(sensors),
                )
                if policy_decision.action == "block":
                    missing_sensors.append(policy_decision.reason or f"Skill {step.skill_name} requires unavailable sensor: {sensor}")
                elif policy_decision.action == "escalate":
                    sensor_confirmations.append(policy_decision.reason or f"Skill {step.skill_name} requires operator confirmation for sensor: {sensor}")
                elif policy_decision.action in {"warn", "degrade"} and policy_decision.reason:
                    sensor_warnings.append(policy_decision.reason)
```

Keep the existing `sensors_unknown` branch before this policy branch. The final structure should be:

```python
                if sensors_unknown:
                    ...
                    break
                finding = sensor_findings.get(sensor)
                health_status = finding.get("health_status") if finding else ("healthy" if sensor in sensors else None)
                ...
```

- [ ] **Step 6: Add lidar to navigation skill metadata**

In `src/fireclaw_core/execution/skills.py`, update the `navigate_to_floor` `Skill`:

```python
                required_sensors=["lidar"],
```

- [ ] **Step 7: Update default adapter sensor lists for navigation-capable test adapters**

In `src/fireclaw_core/agent/robot.py`, update default `available_sensors` for dry-run/simulator/mock adapters that represent navigation-capable robots:

```python
available_sensors: list[str] = field(default_factory=lambda: ["rgb_camera", "thermal_camera", "lidar"])
```

Apply this to `DryRunRobotAdapter`, `MockRos1RobotAdapter`, `MockRos2RobotAdapter`, and `SimulatorRobotAdapter` if each currently uses `["rgb_camera", "thermal_camera"]`.

- [ ] **Step 8: Run SafetyGate and metadata tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_policy.py tests/test_safety.py tests/test_skill_metadata.py -q
```

Expected: all tests pass.

- [ ] **Step 9: Run profile/runtime tests that may be affected by default lidar**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py tests/test_agent.py tests/test_gateway_structured_task.py tests/test_profile_driven_runtime_e2e.py -q
```

Expected: all tests pass. If a test intentionally expects only RGB/thermal sensors, update that test expectation only when the adapter under test is now explicitly navigation-capable.

- [ ] **Step 10: Commit Task 2**

```bash
git add src/fireclaw_core/safety/safety.py src/fireclaw_core/execution/skills.py src/fireclaw_core/agent/robot.py tests/test_safety.py tests/test_skill_metadata.py
git commit -m "feat: enforce sensor health policy in safety gate"
```

---

### Task 3: Document Diagnostics And Run Full Verification

**Files:**
- Modify: `docs/deployment/ros1-gazebo-debugging-guide.md`
- Modify: `memory/2026-06-15/fireclaw-phase3-sensor-policy.md`

- [ ] **Step 1: Document Phase 3 behavior**

Append this section to `docs/deployment/ros1-gazebo-debugging-guide.md`:

```markdown
## Safety-Critical Sensor Policy

FireClaw treats runtime sensor capability as a safety input, not as static configuration.

The runtime chain is:

```text
ROS1 topic/type discovery
  -> topic-to-sensor mapping
  -> sensor-specific health check
  -> verified sensor list and findings diagnostics
  -> SafetyGate sensor policy
```

SafetyGate only trusts sensors that are verified at runtime. Degraded, stale, invalid, or unknown sensors are handled by sensor-specific policy:

- `rgb_camera`: required for victim search unless a verified alternative victim-search sensor is available.
- `thermal_camera`: required for victim/fire assessment; degraded thermal data may block or require operator confirmation depending on the skill.
- `gas_detector`: stale, invalid, or degraded gas data blocks hazardous-atmosphere decisions.
- `lidar`: stale, invalid, or degraded lidar blocks real navigation.
- `imu`: unknown or degraded IMU can require operator confirmation for real navigation.

In dry-run or simulator mode, unknown health may be downgraded to a warning. This behavior is explicit and must not be used as authority for real robot execution.
```
```

- [ ] **Step 2: Create Phase 3 memory record**

Create `memory/2026-06-15/fireclaw-phase3-sensor-policy.md`:

```markdown
# FireClaw Phase 3 Sensor Policy Implementation

## Task Goal

Add safety-critical sensor policy after Phase 2 health checks so SafetyGate can distinguish allow, warn, escalate, and block behavior from sensor health diagnostics.

## Files Modified

- `src/fireclaw_core/ros/ros1_sensor_discovery.py`
- `src/fireclaw_core/safety/sensor_policy.py`
- `src/fireclaw_core/safety/safety.py`
- `src/fireclaw_core/execution/skills.py`
- `src/fireclaw_core/agent/robot.py`
- `tests/test_ros1_sensor_discovery.py`
- `tests/test_sensor_policy.py`
- `tests/test_safety.py`
- `tests/test_skill_metadata.py`
- `docs/deployment/ros1-gazebo-debugging-guide.md`

## Verification Commands

- `.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py tests/test_sensor_health.py -q`
- `.venv/bin/python -m pytest tests/test_sensor_policy.py tests/test_safety.py tests/test_skill_metadata.py -q`
- `.venv/bin/python -m pytest tests/test_cli.py tests/test_agent.py tests/test_gateway_structured_task.py tests/test_profile_driven_runtime_e2e.py -q`
- `.venv/bin/python -m pytest -q`

## Current Conclusion

Fill this section with the actual test results after implementation. Do not claim Phase 3 complete until the full suite command has been run in this session and its output has been checked.
```

- [ ] **Step 3: Run focused Phase 3 verification**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py tests/test_sensor_health.py tests/test_sensor_policy.py tests/test_safety.py tests/test_skill_metadata.py -q
```

Expected: all tests pass.

- [ ] **Step 4: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass or only known unrelated skips remain.

- [ ] **Step 5: Record actual verification output**

Update `memory/2026-06-15/fireclaw-phase3-sensor-policy.md` with:

```markdown
## Actual Verification Results

- Focused Phase 3 command: `<paste exact pass/fail count>`
- Full suite command: `<paste exact pass/fail/skip count>`

## Remaining Gaps

- Phase 4 operator confirmation loop is still not implemented.
- Phase 5 adapter-agnostic discovery backend is still not implemented.
- ROS1 CLI parsing is stricter but still text-based; a later backend should prefer typed ROS message inspection.
```

Replace the angle-bracket placeholders with the actual command outputs before committing.

- [ ] **Step 6: Commit Task 3**

```bash
git add docs/deployment/ros1-gazebo-debugging-guide.md memory/2026-06-15/fireclaw-phase3-sensor-policy.md
git commit -m "docs: document sensor safety policy"
```

---

## Final Verification Checklist

- [ ] Empty ROS1 image `data: []` is invalid for camera health.
- [ ] Empty ROS1 laser scan `ranges: []` is invalid for lidar health.
- [ ] Healthy sensors still verify through discovery.
- [ ] `gas_detector` health failures block hazardous skills.
- [ ] `lidar` health failures block real navigation.
- [ ] `rgb_camera` health failures block victim search when no verified alternative exists.
- [ ] `imu` unknown health requires confirmation for real navigation.
- [ ] Dry-run/simulator unknown health behavior is a warning, not silent real-world authority.
- [ ] Full suite has been run in the implementation session.

## Self-Review

Spec coverage:

- Phase 2 parser false positives are covered by Task 0.
- Phase 3 policy model is covered by Task 1.
- SafetyGate integration is covered by Task 2.
- Documentation, memory, and full verification are covered by Task 3.

Placeholder scan:

- The only angle-bracket text appears inside Task 3 Step 5 as explicit replacement instructions for actual command output after implementation.
- No implementation step contains `TBD` or open-ended "add appropriate handling" language.

Type consistency:

- `SensorPolicyDecision.action` uses `allow`, `warn`, `degrade`, `escalate`, `block`.
- `evaluate_sensor_policy()` receives `health_status`, `health_reason`, `mode`, `dry_run`, and `verified_sensors`.
- SafetyGate maps `block` to block reasons, `escalate` to confirmation reasons, and `warn`/`degrade` to warnings.
