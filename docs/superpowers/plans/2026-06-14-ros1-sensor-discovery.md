# ROS1 Sensor Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build first-version ROS1/Gazebo sensor discovery so FireClaw SafetyGate uses verified runtime sensors instead of hand-maintained static `available_sensors`.

**Architecture:** Add a small sensor discovery boundary that produces `SensorDiscoveryReport` objects from ROS1 topic/type/message probes. `Ros1RobotAdapter.get_robot_state()` exposes only verified sensors as `RobotState.available_sensors`, while `/state` includes diagnostics for discovered/degraded/rejected sensor candidates. Profiles provide optional discovery rules and explicit write-back is limited to a reviewed suggestion file or `--write-profile`.

**Tech Stack:** Python 3.11 dataclasses, existing TOML profile loader, subprocess-backed ROS1 CLI probing for real runs, pytest with fake providers/probes for deterministic tests.

---

## Scope

First version includes:

- ROS1/Gazebo only.
- Default topic/type mapping rules plus profile overrides.
- Fresh-message verification before a sensor enters `available_sensors`.
- Diagnostics in robot state and gateway `/state`.
- `robot-profile discover` that emits suggested discovery rules; it does not silently overwrite profiles.

First version excludes:

- ROS2 discovery.
- Full non-ROS hardware SDK discovery.
- Simulated `rgb_camera` fallback for Gazebo when no camera topic/message exists.
- Long-term sensor health history.

Safety invariant:

```text
declared rule != discovered topic != verified sensor
SafetyGate may only use verified sensors.
```

---

## File Structure

- Create `src/fireclaw_core/sensors/__init__.py`
  - Re-export discovery dataclasses and helpers.
- Create `src/fireclaw_core/sensors/discovery.py`
  - Owns `SensorMappingRule`, `SensorFinding`, `SensorDiscoveryReport`, default mappings, and rule matching.
- Create `src/fireclaw_core/ros/ros1_sensor_discovery.py`
  - Owns ROS1-specific topic list/type probing and recent-message verification.
- Modify `src/fireclaw_core/agent/robot.py`
  - Add `sensor_diagnostics` to `RobotState`.
  - Allow `Ros1RobotAdapter` to hold and call a discovery object.
- Modify `src/fireclaw_core/agent/robot_profile.py`
  - Parse `[robot.sensor_discovery]` and `[[robot.sensor_discovery.rules]]`.
- Modify `src/fireclaw_core/gateway/gateway.py`
  - When a ROS1 robot profile is loaded, attach `Ros1SensorDiscovery` to the adapter.
- Modify `src/fireclaw_core/mission/mission_cli.py`
  - Add `robot-profile discover`.
- Modify `examples/robot_profiles/gazebo_turtlebot3.toml`
  - Add ROS1 discovery config with no simulated camera fallback.
- Modify `docs/deployment/ros1-gazebo-debugging-guide.md`
  - Document expected SafetyGate block when no verified camera exists.
- Test files:
  - Create `tests/test_sensor_discovery.py`
  - Create `tests/test_ros1_sensor_discovery.py`
  - Modify `tests/test_robot_profile.py`
  - Modify `tests/test_ros1_adapter_state.py`
  - Modify `tests/test_mission_cli.py`
  - Modify `tests/test_gateway_robot_profile_config.py`

---

### Task 1: Sensor Discovery Core Models

**Files:**
- Create: `src/fireclaw_core/sensors/__init__.py`
- Create: `src/fireclaw_core/sensors/discovery.py`
- Test: `tests/test_sensor_discovery.py`

- [ ] **Step 1: Write failing tests for mapping and report serialization**

Add `tests/test_sensor_discovery.py`:

```python
from fireclaw_core.sensors.discovery import (
    DEFAULT_SENSOR_MAPPING_RULES,
    SensorDiscoveryReport,
    SensorFinding,
    SensorMappingRule,
    match_sensor_rule,
    verified_sensor_names,
)


def test_default_laser_scan_maps_to_lidar() -> None:
    rule = match_sensor_rule("/scan", "sensor_msgs/LaserScan", DEFAULT_SENSOR_MAPPING_RULES)

    assert rule is not None
    assert rule.sensor == "lidar"
    assert rule.confidence == 0.99


def test_profile_topic_pattern_overrides_nonstandard_camera_name() -> None:
    rules = (
        SensorMappingRule(
            topic_pattern="/front_camera/image_raw",
            message_type="sensor_msgs/Image",
            sensor="rgb_camera",
            source="profile",
            confidence=0.95,
            confirmed=True,
        ),
    )

    rule = match_sensor_rule("/front_camera/image_raw", "sensor_msgs/Image", rules)

    assert rule is not None
    assert rule.sensor == "rgb_camera"
    assert rule.confirmed is True


def test_report_verified_sensor_names_excludes_degraded() -> None:
    report = SensorDiscoveryReport(
        findings=(
            SensorFinding(
                sensor="rgb_camera",
                topic="/camera/image_raw",
                message_type="sensor_msgs/Image",
                status="degraded",
                confidence=0.9,
                source="ros1",
                reason="no recent message",
            ),
            SensorFinding(
                sensor="lidar",
                topic="/scan",
                message_type="sensor_msgs/LaserScan",
                status="verified",
                confidence=0.99,
                source="ros1",
                reason=None,
            ),
        )
    )

    assert verified_sensor_names(report) == ["lidar"]
    assert report.to_dict()["findings"][0]["status"] == "degraded"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_discovery.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'fireclaw_core.sensors'`.

- [ ] **Step 3: Implement core model module**

Create `src/fireclaw_core/sensors/discovery.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Literal


SensorFindingStatus = Literal["discovered", "verified", "degraded", "rejected"]


@dataclass(frozen=True)
class SensorMappingRule:
    topic_pattern: str
    message_type: str
    sensor: str
    source: str = "default"
    confidence: float = 0.8
    confirmed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "topic_pattern": self.topic_pattern,
            "message_type": self.message_type,
            "sensor": self.sensor,
            "source": self.source,
            "confidence": self.confidence,
            "confirmed": self.confirmed,
        }


@dataclass(frozen=True)
class SensorFinding:
    sensor: str
    topic: str
    message_type: str
    status: SensorFindingStatus
    confidence: float
    source: str
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "sensor": self.sensor,
            "topic": self.topic,
            "message_type": self.message_type,
            "status": self.status,
            "confidence": self.confidence,
            "source": self.source,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True)
class SensorDiscoveryReport:
    findings: tuple[SensorFinding, ...]
    source: str = "ros1"

    def verified_sensors(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for finding in self.findings:
            if finding.status == "verified" and finding.sensor not in seen:
                seen.add(finding.sensor)
                result.append(finding.sensor)
        return result

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "verified_sensors": self.verified_sensors(),
            "findings": [finding.to_dict() for finding in self.findings],
        }


DEFAULT_SENSOR_MAPPING_RULES: tuple[SensorMappingRule, ...] = (
    SensorMappingRule("/scan", "sensor_msgs/LaserScan", "lidar", confidence=0.99),
    SensorMappingRule("/imu", "sensor_msgs/Imu", "imu", confidence=0.99),
    SensorMappingRule("/camera/image_raw", "sensor_msgs/Image", "rgb_camera", confidence=0.9),
    SensorMappingRule("/rgb/image_raw", "sensor_msgs/Image", "rgb_camera", confidence=0.9),
    SensorMappingRule("/camera/color/image_raw", "sensor_msgs/Image", "rgb_camera", confidence=0.9),
    SensorMappingRule("/thermal/image_raw", "sensor_msgs/Image", "thermal_camera", confidence=0.85),
    SensorMappingRule("/gas_sensor", "std_msgs/Float32", "gas_detector", confidence=0.9),
)


def match_sensor_rule(
    topic: str,
    message_type: str,
    rules: tuple[SensorMappingRule, ...],
) -> SensorMappingRule | None:
    for rule in rules:
        if rule.message_type == message_type and fnmatch(topic, rule.topic_pattern):
            return rule
    return None


def verified_sensor_names(report: SensorDiscoveryReport) -> list[str]:
    return report.verified_sensors()
```

Create `src/fireclaw_core/sensors/__init__.py`:

```python
from fireclaw_core.sensors.discovery import (
    DEFAULT_SENSOR_MAPPING_RULES,
    SensorDiscoveryReport,
    SensorFinding,
    SensorMappingRule,
    match_sensor_rule,
    verified_sensor_names,
)

__all__ = [
    "DEFAULT_SENSOR_MAPPING_RULES",
    "SensorDiscoveryReport",
    "SensorFinding",
    "SensorMappingRule",
    "match_sensor_rule",
    "verified_sensor_names",
]
```

- [ ] **Step 4: Run tests to verify core models pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_discovery.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/sensors tests/test_sensor_discovery.py
git commit -m "feat: add sensor discovery model"
```

---

### Task 2: ROS1 Topic Discovery and Freshness Probe

**Files:**
- Create: `src/fireclaw_core/ros/ros1_sensor_discovery.py`
- Test: `tests/test_ros1_sensor_discovery.py`

- [ ] **Step 1: Write failing ROS1 discovery tests with fakes**

Add `tests/test_ros1_sensor_discovery.py`:

```python
from fireclaw_core.ros.ros1_sensor_discovery import (
    Ros1SensorDiscovery,
    StaticRos1GraphProvider,
    StaticRos1MessageProbe,
)
from fireclaw_core.sensors.discovery import SensorMappingRule


def test_ros1_discovery_verifies_topic_when_recent_message_exists() -> None:
    discovery = Ros1SensorDiscovery(
        graph_provider=StaticRos1GraphProvider({"/scan": "sensor_msgs/LaserScan"}),
        message_probe=StaticRos1MessageProbe({"/scan": True}),
    )

    report = discovery.discover()

    assert report.verified_sensors() == ["lidar"]
    assert report.findings[0].status == "verified"


def test_ros1_discovery_marks_matching_topic_degraded_without_recent_message() -> None:
    discovery = Ros1SensorDiscovery(
        graph_provider=StaticRos1GraphProvider({"/camera/image_raw": "sensor_msgs/Image"}),
        message_probe=StaticRos1MessageProbe({"/camera/image_raw": False}),
    )

    report = discovery.discover()

    assert report.verified_sensors() == []
    assert report.findings[0].sensor == "rgb_camera"
    assert report.findings[0].status == "degraded"
    assert "no recent message" in str(report.findings[0].reason)


def test_ros1_discovery_uses_profile_rule_for_nonstandard_camera_topic() -> None:
    discovery = Ros1SensorDiscovery(
        graph_provider=StaticRos1GraphProvider({"/front_camera/image_raw": "sensor_msgs/Image"}),
        message_probe=StaticRos1MessageProbe({"/front_camera/image_raw": True}),
        extra_rules=(
            SensorMappingRule(
                topic_pattern="/front_camera/image_raw",
                message_type="sensor_msgs/Image",
                sensor="rgb_camera",
                source="profile",
                confidence=0.95,
                confirmed=True,
            ),
        ),
    )

    report = discovery.discover()

    assert report.verified_sensors() == ["rgb_camera"]
    assert report.findings[0].source == "profile"


def test_ros1_discovery_rejects_unmapped_topic() -> None:
    discovery = Ros1SensorDiscovery(
        graph_provider=StaticRos1GraphProvider({"/debug/image": "custom_msgs/DebugImage"}),
        message_probe=StaticRos1MessageProbe({"/debug/image": True}),
    )

    report = discovery.discover()

    assert report.verified_sensors() == []
    assert report.findings[0].status == "rejected"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'fireclaw_core.ros.ros1_sensor_discovery'`.

- [ ] **Step 3: Implement ROS1 discovery with injectable providers**

Create `src/fireclaw_core/ros/ros1_sensor_discovery.py`:

```python
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol

from fireclaw_core.sensors.discovery import (
    DEFAULT_SENSOR_MAPPING_RULES,
    SensorDiscoveryReport,
    SensorFinding,
    SensorMappingRule,
    match_sensor_rule,
)


class Ros1GraphProvider(Protocol):
    def topic_types(self) -> dict[str, str]:
        ...


class Ros1MessageProbe(Protocol):
    def has_recent_message(self, topic: str, timeout_seconds: float) -> bool:
        ...


@dataclass(frozen=True)
class StaticRos1GraphProvider:
    topics: dict[str, str]

    def topic_types(self) -> dict[str, str]:
        return dict(self.topics)


@dataclass(frozen=True)
class StaticRos1MessageProbe:
    topic_status: dict[str, bool]

    def has_recent_message(self, topic: str, timeout_seconds: float) -> bool:
        return bool(self.topic_status.get(topic, False))


@dataclass(frozen=True)
class Ros1CliGraphProvider:
    rostopic_executable: str = "rostopic"

    def topic_types(self) -> dict[str, str]:
        list_result = subprocess.run(
            [self.rostopic_executable, "list"],
            check=True,
            capture_output=True,
            text=True,
        )
        topics = [line.strip() for line in list_result.stdout.splitlines() if line.strip()]
        result: dict[str, str] = {}
        for topic in topics:
            type_result = subprocess.run(
                [self.rostopic_executable, "type", topic],
                check=False,
                capture_output=True,
                text=True,
            )
            message_type = type_result.stdout.strip()
            if type_result.returncode == 0 and message_type:
                result[topic] = message_type
        return result


@dataclass(frozen=True)
class Ros1CliMessageProbe:
    rostopic_executable: str = "rostopic"

    def has_recent_message(self, topic: str, timeout_seconds: float) -> bool:
        try:
            result = subprocess.run(
                [self.rostopic_executable, "echo", "-n", "1", topic],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0 and bool(result.stdout.strip())


@dataclass
class Ros1SensorDiscovery:
    graph_provider: Ros1GraphProvider
    message_probe: Ros1MessageProbe
    extra_rules: tuple[SensorMappingRule, ...] = ()
    timeout_seconds: float = 2.0

    def discover(self) -> SensorDiscoveryReport:
        findings: list[SensorFinding] = []
        rules = self.extra_rules + DEFAULT_SENSOR_MAPPING_RULES
        try:
            topic_types = self.graph_provider.topic_types()
        except Exception as exc:
            return SensorDiscoveryReport(
                findings=(
                    SensorFinding(
                        sensor="ros1_graph",
                        topic="*",
                        message_type="unknown",
                        status="degraded",
                        confidence=0.0,
                        source="ros1",
                        reason=f"ROS1 topic discovery failed: {exc}",
                    ),
                )
            )

        for topic, message_type in sorted(topic_types.items()):
            rule = match_sensor_rule(topic, message_type, rules)
            if rule is None:
                findings.append(
                    SensorFinding(
                        sensor="unknown",
                        topic=topic,
                        message_type=message_type,
                        status="rejected",
                        confidence=0.0,
                        source="ros1",
                        reason="no sensor mapping rule matched",
                    )
                )
                continue
            if self.message_probe.has_recent_message(topic, self.timeout_seconds):
                findings.append(
                    SensorFinding(
                        sensor=rule.sensor,
                        topic=topic,
                        message_type=message_type,
                        status="verified",
                        confidence=rule.confidence,
                        source=rule.source,
                    )
                )
            else:
                findings.append(
                    SensorFinding(
                        sensor=rule.sensor,
                        topic=topic,
                        message_type=message_type,
                        status="degraded",
                        confidence=rule.confidence,
                        source=rule.source,
                        reason=f"topic exists but no recent message within {self.timeout_seconds:.1f}s",
                    )
                )
        return SensorDiscoveryReport(findings=tuple(findings), source="ros1")
```

- [ ] **Step 4: Run ROS1 discovery tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py -q
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/ros/ros1_sensor_discovery.py tests/test_ros1_sensor_discovery.py
git commit -m "feat: add ros1 sensor discovery"
```

---

### Task 3: Profile Sensor Discovery Configuration

**Files:**
- Modify: `src/fireclaw_core/agent/robot_profile.py`
- Modify: `examples/robot_profiles/gazebo_turtlebot3.toml`
- Test: `tests/test_robot_profile.py`

- [ ] **Step 1: Write failing profile parser tests**

Append to `tests/test_robot_profile.py`:

```python
from fireclaw_core.agent.robot_profile import load_robot_capability_profile


def test_robot_profile_loads_sensor_discovery_rules(tmp_path):
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "data/robots/robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]

[robot.sensor_discovery]
enabled = true
message_timeout_seconds = 1.5

[[robot.sensor_discovery.rules]]
topic_pattern = "/front_camera/image_raw"
message_type = "sensor_msgs/Image"
sensor = "rgb_camera"
confidence = 0.95
confirmed = true
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)

    assert profile.sensor_discovery.enabled is True
    assert profile.sensor_discovery.message_timeout_seconds == 1.5
    assert profile.sensor_discovery.rules[0].topic_pattern == "/front_camera/image_raw"
    assert profile.sensor_discovery.rules[0].sensor == "rgb_camera"
    assert profile.sensor_discovery.rules[0].confirmed is True
```

- [ ] **Step 2: Run profile test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py::test_robot_profile_loads_sensor_discovery_rules -q
```

Expected: FAIL with `AttributeError: 'RobotCapabilityProfile' object has no attribute 'sensor_discovery'`.

- [ ] **Step 3: Add profile dataclass fields and parser**

Modify `src/fireclaw_core/agent/robot_profile.py`:

```python
from fireclaw_core.sensors.discovery import SensorMappingRule
```

Add near `RobotCapabilityProfile`:

```python
@dataclass(frozen=True)
class SensorDiscoveryProfileConfig:
    enabled: bool = True
    message_timeout_seconds: float = 2.0
    rules: tuple[SensorMappingRule, ...] = ()
```

Add field to `RobotCapabilityProfile`:

```python
    sensor_discovery: SensorDiscoveryProfileConfig = field(
        default_factory=SensorDiscoveryProfileConfig
    )
```

In `load_robot_capability_profile()`, before returning:

```python
    sensor_discovery = _sensor_discovery_config(robot)
```

Pass into `RobotCapabilityProfile(...)`:

```python
        sensor_discovery=sensor_discovery,
```

Add helper functions:

```python
def _sensor_discovery_config(robot: dict[str, Any]) -> SensorDiscoveryProfileConfig:
    raw = robot.get("sensor_discovery")
    if raw is None:
        return SensorDiscoveryProfileConfig()
    if not isinstance(raw, dict):
        raise ValueError("robot.sensor_discovery must be a table when provided.")
    rules_raw = raw.get("rules") or []
    if not isinstance(rules_raw, list):
        raise ValueError("robot.sensor_discovery.rules must be an array of tables.")
    rules: list[SensorMappingRule] = []
    for index, item in enumerate(rules_raw):
        if not isinstance(item, dict):
            raise ValueError(f"robot.sensor_discovery.rules[{index}] must be a table.")
        rules.append(
            SensorMappingRule(
                topic_pattern=_required_string(item, "topic_pattern"),
                message_type=_required_string(item, "message_type"),
                sensor=_required_string(item, "sensor"),
                source="profile",
                confidence=float(item.get("confidence", 0.9)),
                confirmed=bool(item.get("confirmed", False)),
            )
        )
    return SensorDiscoveryProfileConfig(
        enabled=bool(raw.get("enabled", True)),
        message_timeout_seconds=float(raw.get("message_timeout_seconds", 2.0)),
        rules=tuple(rules),
    )
```

- [ ] **Step 4: Update Gazebo TurtleBot3 profile**

Modify `examples/robot_profiles/gazebo_turtlebot3.toml` by adding:

```toml
[robot.sensor_discovery]
enabled = true
message_timeout_seconds = 2.0

[[robot.sensor_discovery.rules]]
topic_pattern = "/camera/image_raw"
message_type = "sensor_msgs/Image"
sensor = "rgb_camera"
confidence = 0.9
confirmed = false

[[robot.sensor_discovery.rules]]
topic_pattern = "/scan"
message_type = "sensor_msgs/LaserScan"
sensor = "lidar"
confidence = 0.99
confirmed = true
```

Do not add static `available_sensors` to this profile.

- [ ] **Step 5: Run profile tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py -q
```

Expected: all tests in `tests/test_robot_profile.py` pass.

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/agent/robot_profile.py examples/robot_profiles/gazebo_turtlebot3.toml tests/test_robot_profile.py
git commit -m "feat: load sensor discovery rules from robot profiles"
```

---

### Task 4: RobotState Diagnostics and ROS1 Adapter Integration

**Files:**
- Modify: `src/fireclaw_core/agent/robot.py`
- Modify: `src/fireclaw_core/gateway/gateway.py`
- Test: `tests/test_ros1_adapter_state.py`
- Test: `tests/test_gateway_robot_profile_config.py`

- [ ] **Step 1: Write failing adapter state tests**

Append to `tests/test_ros1_adapter_state.py`:

```python
from fireclaw_core.agent.robot import Ros1RobotAdapter
from fireclaw_core.ros.ros1_config import Ros1AdapterConfig
from fireclaw_core.ros.ros1_sensor_discovery import (
    Ros1SensorDiscovery,
    StaticRos1GraphProvider,
    StaticRos1MessageProbe,
)


def test_ros1_adapter_state_uses_verified_discovered_sensors() -> None:
    adapter = Ros1RobotAdapter(
        config=Ros1AdapterConfig(robot_id="robot-1"),
        sensor_discovery=Ros1SensorDiscovery(
            graph_provider=StaticRos1GraphProvider({
                "/camera/image_raw": "sensor_msgs/Image",
                "/scan": "sensor_msgs/LaserScan",
            }),
            message_probe=StaticRos1MessageProbe({
                "/camera/image_raw": False,
                "/scan": True,
            }),
        ),
    )

    state = adapter.get_robot_state()

    assert state.available_sensors == ["lidar"]
    assert state.sensor_diagnostics is not None
    findings = state.sensor_diagnostics["findings"]
    assert any(item["sensor"] == "rgb_camera" and item["status"] == "degraded" for item in findings)
```

- [ ] **Step 2: Run adapter test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_adapter_state.py::test_ros1_adapter_state_uses_verified_discovered_sensors -q
```

Expected: FAIL because `Ros1RobotAdapter` does not accept `sensor_discovery` and `RobotState` has no diagnostics field.

- [ ] **Step 3: Extend RobotState and Ros1RobotAdapter**

Modify `src/fireclaw_core/agent/robot.py`:

```python
from fireclaw_core.sensors.discovery import SensorDiscoveryReport
```

Change `RobotState` to:

```python
@dataclass
class RobotState:
    robot_id: str
    mode: str
    dry_run: bool
    online: bool
    battery_percent: float | None
    current_floor: int | None
    available_sensors: list[str] | None
    supports_real_execution: bool
    sensor_diagnostics: dict[str, Any] | None = None
```

Add field to `Ros1RobotAdapter`:

```python
    sensor_discovery: Any | None = None
```

Change `Ros1RobotAdapter.get_robot_state()` to:

```python
    def get_robot_state(self) -> RobotState:
        sensor_diagnostics: dict[str, Any] | None = None
        available_sensors: list[str] | None = None
        if self.sensor_discovery is not None:
            report = self.sensor_discovery.discover()
            sensor_diagnostics = report.to_dict()
            available_sensors = report.verified_sensors()
        elif self.available_sensors:
            available_sensors = list(self.available_sensors)
        return RobotState(
            robot_id=self.robot_id,
            mode=self.mode,
            dry_run=self.dry_run,
            online=not self.emergency_stopped,
            battery_percent=None,
            current_floor=self.current_floor,
            available_sensors=available_sensors,
            supports_real_execution=True,
            sensor_diagnostics=sensor_diagnostics,
        )
```

Existing positional `RobotState(...)` calls remain valid because the new field has a default.

- [ ] **Step 4: Attach discovery from gateway profile**

Modify `src/fireclaw_core/gateway/gateway.py` after the robot adapter is created and after profile validation has access to `self.robot_profile`:

```python
def attach_profile_sensor_discovery(robot, profile) -> None:
    if profile is None:
        return
    if profile.adapter != "ros1":
        return
    if not profile.sensor_discovery.enabled:
        return
    from fireclaw_core.ros.ros1_sensor_discovery import (
        Ros1CliGraphProvider,
        Ros1CliMessageProbe,
        Ros1SensorDiscovery,
    )

    setattr(
        robot,
        "sensor_discovery",
        Ros1SensorDiscovery(
            graph_provider=Ros1CliGraphProvider(),
            message_probe=Ros1CliMessageProbe(),
            extra_rules=profile.sensor_discovery.rules,
            timeout_seconds=profile.sensor_discovery.message_timeout_seconds,
        ),
    )
```

Call it in `FireClawGateway.__init__` immediately after `apply_gateway_dry_run_to_robot(...)`:

```python
        attach_profile_sensor_discovery(self.robot, self.robot_profile)
```

- [ ] **Step 5: Write gateway attachment regression test**

Add to `tests/test_gateway_robot_profile_config.py`:

```python
def test_profile_gateway_attaches_ros1_sensor_discovery(tmp_path):
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

    assert getattr(gateway.robot, "sensor_discovery", None) is not None
```

- [ ] **Step 6: Run adapter and gateway tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_adapter_state.py tests/test_gateway_robot_profile_config.py -q
```

Expected: both test files pass.

- [ ] **Step 7: Commit**

```bash
git add src/fireclaw_core/agent/robot.py src/fireclaw_core/gateway/gateway.py tests/test_ros1_adapter_state.py tests/test_gateway_robot_profile_config.py
git commit -m "feat: expose verified ros1 sensors in robot state"
```

---

### Task 5: SafetyGate Regression for Verified-Only Sensors

**Files:**
- Modify: `tests/test_safety.py`

- [ ] **Step 1: Add regression test for degraded camera block**

Append to `tests/test_safety.py`:

```python
from fireclaw_core.agent.robot import RobotState


def test_safety_blocks_search_when_rgb_camera_is_only_degraded() -> None:
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
        available_sensors=["lidar"],
        supports_real_execution=True,
        sensor_diagnostics={
            "source": "ros1",
            "verified_sensors": ["lidar"],
            "findings": [
                {
                    "sensor": "rgb_camera",
                    "topic": "/camera/image_raw",
                    "message_type": "sensor_msgs/Image",
                    "status": "degraded",
                    "confidence": 0.9,
                    "source": "ros1",
                    "reason": "topic exists but no recent message within 2.0s",
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
    )

    assert decision.status == "block"
    assert "Skill search_for_victims requires unavailable sensor: rgb_camera" in decision.reasons
```

- [ ] **Step 2: Run safety test**

Run:

```bash
.venv/bin/python -m pytest tests/test_safety.py::test_safety_blocks_search_when_rgb_camera_is_only_degraded -q
```

Expected: PASS. This test documents the invariant; no SafetyGate code change should be needed.

- [ ] **Step 3: Run broader safety tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_safety.py tests/test_safety_unknown_state.py -q
```

Expected: both files pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_safety.py
git commit -m "test: document verified-only sensor safety behavior"
```

---

### Task 6: Robot Profile Discover Command

**Files:**
- Modify: `src/fireclaw_core/mission/mission_cli.py`
- Test: `tests/test_mission_cli.py`

- [ ] **Step 1: Write failing CLI tests for discover output**

Append to `tests/test_mission_cli.py`:

```python
def test_robot_profile_discover_writes_suggested_rules(tmp_path, monkeypatch):
    profile_path = tmp_path / "robot.toml"
    output_path = tmp_path / "discovered.toml"
    profile_path.write_text(
        f"""
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "{tmp_path / "robot-data"}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".strip(),
        encoding="utf-8",
    )

    from fireclaw_core.ros import ros1_sensor_discovery

    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliGraphProvider,
        "topic_types",
        lambda self: {"/scan": "sensor_msgs/LaserScan"},
    )
    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliMessageProbe,
        "has_recent_message",
        lambda self, topic, timeout_seconds: True,
    )

    exit_code = main([
        "robot-profile",
        "discover",
        "--profile",
        str(profile_path),
        "--output",
        str(output_path),
    ])

    assert exit_code == 0
    text = output_path.read_text(encoding="utf-8")
    assert "[[robot.sensor_discovery.rules]]" in text
    assert 'sensor = "lidar"' in text
    assert 'topic_pattern = "/scan"' in text
```

- [ ] **Step 2: Run CLI test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_cli.py::test_robot_profile_discover_writes_suggested_rules -q
```

Expected: FAIL because `discover` is not a valid `robot-profile` subcommand.

- [ ] **Step 3: Add CLI parser options**

Modify `src/fireclaw_core/mission/mission_cli.py` near the existing `robot-profile export` parser:

```python
    profile_discover = robot_profile_sub.add_parser(
        "discover",
        help="Discover ROS1 sensor topics and write suggested profile rules.",
    )
    profile_discover.add_argument("--profile", required=True, help="Path to robot profile TOML.")
    profile_discover.add_argument("--output", required=True, help="Path to suggested TOML output.")
    profile_discover.add_argument(
        "--write-profile",
        action="store_true",
        help="Explicitly write suggested discovery rules to the profile instead of --output.",
    )
```

- [ ] **Step 4: Implement discover handler**

Modify `_handle_robot_profile()`:

```python
    if args.robot_profile_command == "discover":
        from fireclaw_core.ros.ros1_sensor_discovery import (
            Ros1CliGraphProvider,
            Ros1CliMessageProbe,
            Ros1SensorDiscovery,
        )

        profile = load_robot_capability_profile(args.profile)
        discovery = Ros1SensorDiscovery(
            graph_provider=Ros1CliGraphProvider(),
            message_probe=Ros1CliMessageProbe(),
            extra_rules=profile.sensor_discovery.rules,
            timeout_seconds=profile.sensor_discovery.message_timeout_seconds,
        )
        report = discovery.discover()
        lines: list[str] = [
            "# Suggested FireClaw sensor discovery rules.",
            "# Review before copying into the robot profile.",
            "",
            "[robot.sensor_discovery]",
            "enabled = true",
            f"message_timeout_seconds = {profile.sensor_discovery.message_timeout_seconds:.1f}",
            "",
        ]
        for finding in report.findings:
            if finding.status not in {"verified", "degraded"}:
                continue
            lines.extend([
                "[[robot.sensor_discovery.rules]]",
                f'topic_pattern = "{finding.topic}"',
                f'message_type = "{finding.message_type}"',
                f'sensor = "{finding.sensor}"',
                f"confidence = {finding.confidence:.2f}",
                f"confirmed = {str(finding.status == 'verified').lower()}",
                "",
            ])
        text = "\n".join(lines)
        output = Path(args.profile if args.write_profile else args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if args.write_profile:
            existing = output.read_text(encoding="utf-8")
            output.write_text(existing.rstrip() + "\n\n" + text, encoding="utf-8")
        else:
            output.write_text(text, encoding="utf-8")
        _print_json({
            "status": "written",
            "output": str(output),
            "verified_sensors": report.verified_sensors(),
        })
        return 0
```

Behavior:

- Without `--write-profile`, write a separate suggestion file.
- With `--write-profile`, append explicit rules to the profile.
- Never write static `available_sensors`.

- [ ] **Step 5: Run CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_cli.py::test_robot_profile_discover_writes_suggested_rules -q
```

Expected: PASS.

- [ ] **Step 6: Run related CLI suite**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_cli.py tests/test_robot_profile.py tests/test_ros1_sensor_discovery.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/fireclaw_core/mission/mission_cli.py tests/test_mission_cli.py
git commit -m "feat: add robot profile sensor discovery command"
```

---

### Task 7: Documentation and Debugging Workflow

**Files:**
- Modify: `docs/deployment/ros1-gazebo-debugging-guide.md`
- Modify: `README.md`
- Modify: `memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md`

- [ ] **Step 1: Update ROS1/Gazebo debugging guide**

Add a section to `docs/deployment/ros1-gazebo-debugging-guide.md`:

```markdown
## Sensor Discovery and SafetyGate

The ROS1 robot gateway derives `RobotState.available_sensors` from verified ROS topics.

For each candidate topic, FireClaw checks:

1. the topic is present in `rostopic list`;
2. `rostopic type <topic>` matches a default or profile mapping rule;
3. `rostopic echo -n 1 <topic>` returns a recent message within the configured timeout.

Only verified sensors are passed to SafetyGate. If `/camera/image_raw` is absent, has the wrong type, or does not publish a message, `rgb_camera` is not available and `search_for_victims` is blocked. This is expected in Gazebo unless a real camera topic is running.

Generate suggested profile rules:

```bash
.venv/bin/python -m fireclaw_core robot-profile discover \
  --profile examples/robot_profiles/gazebo_turtlebot3.toml \
  --output /tmp/gazebo_turtlebot3.discovered.toml
```

Review the suggestion before copying rules into a profile. Do not use static `available_sensors` to bypass SafetyGate.
```
```

- [ ] **Step 2: Update README profile note**

Add a short note near the profile-driven startup section in `README.md`:

```markdown
Robot profiles may include sensor discovery rules, but runtime sensor availability comes from verified adapter state. In ROS1/Gazebo, a topic must exist, match a sensor rule, and publish a recent message before the corresponding sensor enters `RobotState.available_sensors`.
```

- [ ] **Step 3: Append implementation status to memory**

Append to `memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md`:

```markdown
## Implementation Plan

Created `docs/superpowers/plans/2026-06-14-ros1-sensor-discovery.md`.

The plan keeps first-version scope to ROS1/Gazebo, uses verified sensors for SafetyGate, and adds `robot-profile discover` for reviewed suggestion output instead of silent profile overwrites.
```

- [ ] **Step 4: Run documentation grep checks**

Run:

```bash
rg -n "available_sensors.*bypass|simulated rgb_camera|UNFINISHED_MARKER_A|UNFINISHED_MARKER_B" README.md docs/deployment/ros1-gazebo-debugging-guide.md memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md
```

Expected: exit code `1` with no matches.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/deployment/ros1-gazebo-debugging-guide.md memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md
git commit -m "docs: document ros1 sensor discovery workflow"
```

---

### Task 8: Focused Verification and Live ROS1/Gazebo Smoke

**Files:**
- No source changes required.

- [ ] **Step 1: Run focused automated tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_sensor_discovery.py \
  tests/test_ros1_sensor_discovery.py \
  tests/test_robot_profile.py \
  tests/test_ros1_adapter_state.py \
  tests/test_gateway_robot_profile_config.py \
  tests/test_safety.py \
  tests/test_mission_cli.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full suite passes. If ROS-dependent skipped tests remain skipped, record the skip count.

- [ ] **Step 3: Start Gazebo**

Run in terminal 1:

```bash
source /opt/ros/noetic/setup.bash
export TURTLEBOT3_MODEL=burger
roslaunch turtlebot3_gazebo turtlebot3_world.launch
```

Expected: Gazebo starts and ROS master is available.

- [ ] **Step 4: Start robot gateway in real-run mode**

Run in terminal 2:

```bash
cd /home/nankai/fireclaw
source /opt/ros/noetic/setup.bash
.venv/bin/python -m fireclaw_core robot-gateway \
  --config fireclaw.toml \
  --robot-profile examples/robot_profiles/gazebo_turtlebot3.toml \
  --real-run
```

Expected: gateway starts at `http://127.0.0.1:8765`.

- [ ] **Step 5: Inspect robot state diagnostics**

Run:

```bash
curl -sS -H 'X-Operator-Scopes: admin' http://127.0.0.1:8765/state
```

Expected:

- response includes `robot_state.sensor_diagnostics`;
- `/scan` is `verified` when Gazebo publishes scan messages;
- `rgb_camera` is absent or `degraded` when no camera topic/message exists;
- `robot_state.available_sensors` includes only verified sensors.

- [ ] **Step 6: Start mission gateway**

Run in terminal 3:

```bash
cd /home/nankai/fireclaw
.venv/bin/python -m fireclaw_core serve \
  --config fireclaw.toml \
  --data-dir data/debug-gazebo
```

Expected: mission gateway starts at `http://127.0.0.1:8766`.

- [ ] **Step 7: Submit mission and confirm expected block**

Run:

```bash
cd /home/nankai/fireclaw
.venv/bin/python -m fireclaw_core plan-mission \
  --server http://127.0.0.1:8766 \
  --command "去二楼搜索伤员"
```

Expected when no camera topic is verified:

- mission is planned and dispatched;
- robot-local agent produces a plan;
- SafetyGate blocks `search_for_victims`;
- reason includes `Skill search_for_victims requires unavailable sensor: rgb_camera`.

- [ ] **Step 8: Record proof in memory**

Append a result section to `memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md`:

```markdown
## Verification Result

- Focused tests:
- Full suite:
- Live Gazebo `/state` sensor diagnostics:
- Mission submission result:
- SafetyGate outcome:
- Remaining gaps:
```

Record concrete command output summaries and timestamps for each bullet before committing this note.

- [ ] **Step 9: Commit verification note**

```bash
git add memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md
git commit -m "docs: record ros1 sensor discovery verification"
```

---

## Plan Self-Review

Spec coverage:

- ROS1/Gazebo only: covered by Scope and Tasks 2, 4, 8.
- No simulated camera fallback: covered by Scope, Task 7 docs, Task 8 expected block.
- Topic naming rules: covered by Tasks 1, 2, 3.
- Topic exists versus usable: covered by Task 2 degraded tests and Task 4 adapter state.
- Safety-critical verified-only behavior: covered by Task 5.
- Automatic profile write-back risk: covered by Task 6 explicit output and `--write-profile`.
- Non-ROS discovery: intentionally excluded from first version; only the generic report model is adapter-neutral.

Placeholder scan:

- The plan uses concrete file paths, code snippets, commands, and expected outcomes.
- It does not include undefined task names or incomplete acceptance criteria.

Type consistency:

- `SensorMappingRule`, `SensorFinding`, and `SensorDiscoveryReport` are defined in Task 1 before use.
- `Ros1SensorDiscovery`, `StaticRos1GraphProvider`, and `StaticRos1MessageProbe` are defined in Task 2 before use.
- `RobotState.sensor_diagnostics` is added with a default in Task 4 so existing positional tests remain compatible.
