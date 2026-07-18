# ROS1 Discovery Fingerprint Stale Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ROS1 discovery fingerprints so FireClaw can detect stale profile-confirmed sensor discovery rules after robot hardware, Gazebo launch files, or ROS topic graphs change.

**Architecture:** Extend the sensor discovery core with fingerprint and comparison models. Parse an optional `[robot.discovery_fingerprint]` profile table, pass it into `Ros1SensorDiscovery`, compute the current ROS1 topic/type graph hash at runtime, and expose `fresh/missing/stale/unknown` diagnostics through `SensorDiscoveryReport.to_dict()` and robot state. Keep SafetyGate unchanged: it still trusts only live `verified` sensors.

**Tech Stack:** Python 3.11 dataclasses, SHA-256 from `hashlib`, existing TOML profile loader, existing ROS1 graph provider abstraction, pytest with static ROS1 fakes.

---

## Scope

This plan implements only phase 1 from:

- `docs/superpowers/specs/2026-06-14-ros1-discovery-fingerprint-stale-profile-design.md`

It does not implement ROS2 discovery, non-ROS discovery backends, a separate operator confirmation command, cryptographic signatures, or full per-sensor health policy.

## File Structure

- Modify `src/fireclaw_core/sensors/discovery.py`
  - Add `DiscoveryFingerprint`, `FingerprintComparison`, `fingerprint_topic_types()`, and `compare_fingerprints()`.
  - Add optional fingerprint fields to `SensorFinding` and `SensorDiscoveryReport`.
- Modify `src/fireclaw_core/sensors/__init__.py`
  - Re-export the new fingerprint types and helpers.
- Modify `src/fireclaw_core/agent/robot_profile.py`
  - Add `discovery_fingerprint` to `RobotCapabilityProfile`.
  - Parse optional `[robot.discovery_fingerprint]`.
- Modify `src/fireclaw_core/ros/ros1_sensor_discovery.py`
  - Accept an optional profile fingerprint.
  - Compute the runtime fingerprint from `topic_types()`.
  - Attach comparison diagnostics and stale confirmation markers.
- Modify `src/fireclaw_core/gateway/gateway.py`
  - Pass `profile.discovery_fingerprint` into `Ros1SensorDiscovery`.
- Modify `src/fireclaw_core/mission/mission_cli.py`
  - Include `[robot.discovery_fingerprint]` in `robot-profile discover` output.
  - Write or replace exactly one fingerprint table during `--write-profile`.
- Modify tests:
  - `tests/test_sensor_discovery.py`
  - `tests/test_robot_profile.py`
  - `tests/test_ros1_sensor_discovery.py`
  - `tests/test_ros1_adapter_state.py`
  - `tests/test_gateway_robot_profile_config.py`
  - `tests/test_mission_cli.py`
- Modify memory:
  - `memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md`

---

### Task 1: Add Fingerprint Core Models

**Files:**
- Modify: `src/fireclaw_core/sensors/discovery.py`
- Modify: `src/fireclaw_core/sensors/__init__.py`
- Test: `tests/test_sensor_discovery.py`

- [ ] **Step 1: Write failing fingerprint tests**

Append to `tests/test_sensor_discovery.py`:

```python
from fireclaw_core.sensors.discovery import (
    DiscoveryFingerprint,
    compare_fingerprints,
    fingerprint_topic_types,
)


def test_fingerprint_topic_types_is_order_independent() -> None:
    first = fingerprint_topic_types({
        "/scan": "sensor_msgs/LaserScan",
        "/camera/image_raw": "sensor_msgs/Image",
    })
    second = fingerprint_topic_types({
        "/camera/image_raw": "sensor_msgs/Image",
        "/scan": "sensor_msgs/LaserScan",
    })

    assert first == second
    assert first.source == "ros1"
    assert first.topics_hash.startswith("sha256:")


def test_fingerprint_topic_types_changes_when_graph_changes() -> None:
    first = fingerprint_topic_types({"/scan": "sensor_msgs/LaserScan"})
    second = fingerprint_topic_types({"/scan": "sensor_msgs/PointCloud2"})

    assert first.topics_hash != second.topics_hash


def test_compare_fingerprints_reports_fresh_missing_stale_and_source_mismatch() -> None:
    runtime = DiscoveryFingerprint(source="ros1", topics_hash="sha256:abc")

    assert compare_fingerprints(runtime, runtime).status == "fresh"
    assert compare_fingerprints(runtime, None).status == "missing"

    stale = compare_fingerprints(
        runtime,
        DiscoveryFingerprint(source="ros1", topics_hash="sha256:def"),
    )
    assert stale.status == "stale"
    assert stale.reason == "topics_hash_mismatch"

    source_mismatch = compare_fingerprints(
        runtime,
        DiscoveryFingerprint(source="ros2", topics_hash="sha256:abc"),
    )
    assert source_mismatch.status == "stale"
    assert source_mismatch.reason == "source_mismatch"


def test_report_serializes_fingerprint_diagnostics() -> None:
    runtime = DiscoveryFingerprint(source="ros1", topics_hash="sha256:abc")
    profile = DiscoveryFingerprint(source="ros1", topics_hash="sha256:def")
    comparison = compare_fingerprints(runtime, profile)
    report = SensorDiscoveryReport(
        findings=(),
        source="ros1",
        runtime_fingerprint=runtime,
        profile_fingerprint=profile,
        fingerprint_comparison=comparison,
    )

    payload = report.to_dict()

    assert payload["runtime_fingerprint"] == {
        "source": "ros1",
        "topics_hash": "sha256:abc",
    }
    assert payload["profile_fingerprint"] == {
        "source": "ros1",
        "topics_hash": "sha256:def",
    }
    assert payload["profile_fingerprint_status"] == "stale"
    assert payload["profile_fingerprint_reason"] == "topics_hash_mismatch"
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_discovery.py::test_fingerprint_topic_types_is_order_independent tests/test_sensor_discovery.py::test_fingerprint_topic_types_changes_when_graph_changes tests/test_sensor_discovery.py::test_compare_fingerprints_reports_fresh_missing_stale_and_source_mismatch tests/test_sensor_discovery.py::test_report_serializes_fingerprint_diagnostics -q
```

Expected: FAIL with import errors for `DiscoveryFingerprint`, `compare_fingerprints`, and `fingerprint_topic_types`.

- [ ] **Step 3: Implement fingerprint models**

Modify `src/fireclaw_core/sensors/discovery.py`.

Add imports:

```python
import hashlib
```

Replace the current `typing` import with:

```python
from typing import Literal
```

Add after `SensorFindingStatus`:

```python
FingerprintComparisonStatus = Literal["fresh", "missing", "stale", "unknown"]


@dataclass(frozen=True)
class DiscoveryFingerprint:
    source: str
    topics_hash: str
    nodes_hash: str | None = None
    created_at: str | None = None
    confirmed_by: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "source": self.source,
            "topics_hash": self.topics_hash,
        }
        if self.nodes_hash is not None:
            payload["nodes_hash"] = self.nodes_hash
        if self.created_at is not None:
            payload["created_at"] = self.created_at
        if self.confirmed_by is not None:
            payload["confirmed_by"] = self.confirmed_by
        return payload


@dataclass(frozen=True)
class FingerprintComparison:
    status: FingerprintComparisonStatus
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"status": self.status}
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload
```

Add helper functions before `match_sensor_rule()`:

```python
def fingerprint_topic_types(
    topic_types: dict[str, str],
    *,
    source: str = "ros1",
) -> DiscoveryFingerprint:
    lines = [
        f"{topic}\t{message_type}"
        for topic, message_type in sorted(topic_types.items())
    ]
    digest = hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()
    return DiscoveryFingerprint(source=source, topics_hash=f"sha256:{digest}")


def compare_fingerprints(
    runtime: DiscoveryFingerprint | None,
    profile: DiscoveryFingerprint | None,
) -> FingerprintComparison:
    if runtime is None:
        return FingerprintComparison(status="unknown", reason="runtime_fingerprint_unavailable")
    if profile is None:
        return FingerprintComparison(status="missing", reason="profile_fingerprint_missing")
    if runtime.source != profile.source:
        return FingerprintComparison(status="stale", reason="source_mismatch")
    if runtime.topics_hash != profile.topics_hash:
        return FingerprintComparison(status="stale", reason="topics_hash_mismatch")
    if runtime.nodes_hash is not None and profile.nodes_hash is not None and runtime.nodes_hash != profile.nodes_hash:
        return FingerprintComparison(status="stale", reason="nodes_hash_mismatch")
    return FingerprintComparison(status="fresh")
```

Extend `SensorFinding` by adding fields:

```python
    confirmed: bool = False
    confirmation_stale: bool = False
```

Then in `SensorFinding.to_dict()`, after `source`:

```python
            "confirmed": self.confirmed,
            "confirmation_stale": self.confirmation_stale,
```

Extend `SensorDiscoveryReport`:

```python
@dataclass(frozen=True)
class SensorDiscoveryReport:
    findings: tuple[SensorFinding, ...]
    source: str = "ros1"
    runtime_fingerprint: DiscoveryFingerprint | None = None
    profile_fingerprint: DiscoveryFingerprint | None = None
    fingerprint_comparison: FingerprintComparison | None = None
```

Replace `SensorDiscoveryReport.to_dict()` with:

```python
    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "source": self.source,
            "verified_sensors": self.verified_sensors(),
            "findings": [finding.to_dict() for finding in self.findings],
        }
        if self.runtime_fingerprint is not None:
            payload["runtime_fingerprint"] = self.runtime_fingerprint.to_dict()
        if self.profile_fingerprint is not None:
            payload["profile_fingerprint"] = self.profile_fingerprint.to_dict()
        if self.fingerprint_comparison is not None:
            payload["profile_fingerprint_status"] = self.fingerprint_comparison.status
            if self.fingerprint_comparison.reason is not None:
                payload["profile_fingerprint_reason"] = self.fingerprint_comparison.reason
        return payload
```

- [ ] **Step 4: Export fingerprint symbols**

Modify `src/fireclaw_core/sensors/__init__.py`.

Add to the import list:

```python
    DiscoveryFingerprint,
    FingerprintComparison,
    FingerprintComparisonStatus,
    compare_fingerprints,
    fingerprint_topic_types,
```

Add to `__all__`:

```python
    "DiscoveryFingerprint",
    "FingerprintComparison",
    "FingerprintComparisonStatus",
    "compare_fingerprints",
    "fingerprint_topic_types",
```

- [ ] **Step 5: Run the core tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_discovery.py -q
```

Expected: PASS.

- [ ] **Step 6: Checkpoint Task 1**

Run:

```bash
git diff --stat

# If the user has explicitly authorized commits for this execution, then run:
git add src/fireclaw_core/sensors/discovery.py src/fireclaw_core/sensors/__init__.py tests/test_sensor_discovery.py
git commit -m "feat: add discovery fingerprint core models"
```

---

### Task 2: Parse Profile Discovery Fingerprints

**Files:**
- Modify: `src/fireclaw_core/agent/robot_profile.py`
- Test: `tests/test_robot_profile.py`

- [ ] **Step 1: Write failing profile parser tests**

Append to `tests/test_robot_profile.py`:

```python
from fireclaw_core.sensors.discovery import DiscoveryFingerprint


def test_load_robot_profile_with_discovery_fingerprint(tmp_path: Path) -> None:
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
enabled_skills = ["navigate_to_floor", "search_for_victims"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims"]

[robot.discovery_fingerprint]
source = "ros1"
topics_hash = "sha256:abc"
nodes_hash = "sha256:nodes"
created_at = "2026-06-14T12:00:00+08:00"
confirmed_by = "operator"
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)

    assert profile.discovery_fingerprint == DiscoveryFingerprint(
        source="ros1",
        topics_hash="sha256:abc",
        nodes_hash="sha256:nodes",
        created_at="2026-06-14T12:00:00+08:00",
        confirmed_by="operator",
    )


def test_load_robot_profile_without_discovery_fingerprint_is_backward_compatible(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims"]
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)

    assert profile.discovery_fingerprint is None


def test_load_robot_profile_rejects_empty_discovery_fingerprint_source(tmp_path: Path) -> None:
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
enabled_skills = ["navigate_to_floor", "search_for_victims"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims"]

[robot.discovery_fingerprint]
source = ""
topics_hash = "sha256:abc"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="robot.discovery_fingerprint.source must be a non-empty string"):
        load_robot_capability_profile(profile_path)
```

If `pytest` is not already imported in `tests/test_robot_profile.py`, add:

```python
import pytest
```

- [ ] **Step 2: Run the failing profile tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py::test_load_robot_profile_with_discovery_fingerprint tests/test_robot_profile.py::test_load_robot_profile_without_discovery_fingerprint_is_backward_compatible tests/test_robot_profile.py::test_load_robot_profile_rejects_empty_discovery_fingerprint_source -q
```

Expected: FAIL because `RobotCapabilityProfile` has no `discovery_fingerprint`.

- [ ] **Step 3: Implement profile parsing**

Modify imports in `src/fireclaw_core/agent/robot_profile.py`:

```python
from fireclaw_core.sensors.discovery import DiscoveryFingerprint, SensorMappingRule
```

Add a field to `RobotCapabilityProfile` after `sensor_discovery`:

```python
    discovery_fingerprint: DiscoveryFingerprint | None = None
```

In `load_robot_capability_profile()`, after:

```python
    sensor_discovery = _sensor_discovery_config(robot)
```

add:

```python
    discovery_fingerprint = _discovery_fingerprint(robot)
```

Then pass it into `RobotCapabilityProfile(...)`:

```python
        discovery_fingerprint=discovery_fingerprint,
```

Add this helper after `_sensor_discovery_config()`:

```python
def _discovery_fingerprint(robot: dict[str, Any]) -> DiscoveryFingerprint | None:
    raw = robot.get("discovery_fingerprint")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("robot.discovery_fingerprint must be a table when provided.")
    source = raw.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("robot.discovery_fingerprint.source must be a non-empty string.")
    topics_hash = raw.get("topics_hash")
    if not isinstance(topics_hash, str) or not topics_hash.strip():
        raise ValueError("robot.discovery_fingerprint.topics_hash must be a non-empty string.")
    nodes_hash = raw.get("nodes_hash")
    if nodes_hash is not None and (not isinstance(nodes_hash, str) or not nodes_hash.strip()):
        raise ValueError("robot.discovery_fingerprint.nodes_hash must be a non-empty string when provided.")
    created_at = raw.get("created_at")
    if created_at is not None and not isinstance(created_at, str):
        raise ValueError("robot.discovery_fingerprint.created_at must be a string when provided.")
    confirmed_by = raw.get("confirmed_by")
    if confirmed_by is not None and (not isinstance(confirmed_by, str) or not confirmed_by.strip()):
        raise ValueError("robot.discovery_fingerprint.confirmed_by must be a non-empty string when provided.")
    return DiscoveryFingerprint(
        source=source.strip(),
        topics_hash=topics_hash.strip(),
        nodes_hash=nodes_hash.strip() if isinstance(nodes_hash, str) else None,
        created_at=created_at.strip() if isinstance(created_at, str) else None,
        confirmed_by=confirmed_by.strip() if isinstance(confirmed_by, str) else None,
    )
```

- [ ] **Step 4: Run profile tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py -q
```

Expected: PASS.

- [ ] **Step 5: Checkpoint Task 2**

Run:

```bash
git diff --stat

# If the user has explicitly authorized commits for this execution, then run:
git add src/fireclaw_core/agent/robot_profile.py tests/test_robot_profile.py
git commit -m "feat: parse robot discovery fingerprints"
```

---

### Task 3: Attach Fingerprint Comparison To ROS1 Discovery

**Files:**
- Modify: `src/fireclaw_core/ros/ros1_sensor_discovery.py`
- Test: `tests/test_ros1_sensor_discovery.py`

- [ ] **Step 1: Write failing ROS1 discovery tests**

Append to `tests/test_ros1_sensor_discovery.py`:

```python
from fireclaw_core.sensors.discovery import DiscoveryFingerprint, fingerprint_topic_types


def test_ros1_discovery_reports_fresh_profile_fingerprint() -> None:
    topics = {"/scan": "sensor_msgs/LaserScan"}
    discovery = Ros1SensorDiscovery(
        graph_provider=StaticRos1GraphProvider(topics),
        message_probe=StaticRos1MessageProbe({"/scan": True}),
        profile_fingerprint=fingerprint_topic_types(topics),
    )

    report = discovery.discover()
    payload = report.to_dict()

    assert payload["profile_fingerprint_status"] == "fresh"
    assert "profile_fingerprint_reason" not in payload
    assert report.verified_sensors() == ["lidar"]


def test_ros1_discovery_reports_missing_profile_fingerprint() -> None:
    discovery = Ros1SensorDiscovery(
        graph_provider=StaticRos1GraphProvider({"/scan": "sensor_msgs/LaserScan"}),
        message_probe=StaticRos1MessageProbe({"/scan": True}),
    )

    report = discovery.discover()
    payload = report.to_dict()

    assert payload["profile_fingerprint_status"] == "missing"
    assert payload["profile_fingerprint_reason"] == "profile_fingerprint_missing"


def test_ros1_discovery_marks_stale_profile_confirmation() -> None:
    discovery = Ros1SensorDiscovery(
        graph_provider=StaticRos1GraphProvider({"/front_camera/image_raw": "sensor_msgs/Image"}),
        message_probe=StaticRos1MessageProbe({"/front_camera/image_raw": False}),
        profile_fingerprint=DiscoveryFingerprint(source="ros1", topics_hash="sha256:old"),
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
    payload = report.to_dict()

    assert payload["profile_fingerprint_status"] == "stale"
    assert payload["profile_fingerprint_reason"] == "topics_hash_mismatch"
    assert report.verified_sensors() == []
    assert payload["findings"][0]["sensor"] == "rgb_camera"
    assert payload["findings"][0]["status"] == "degraded"
    assert payload["findings"][0]["confirmed"] is True
    assert payload["findings"][0]["confirmation_stale"] is True


def test_ros1_discovery_reports_unknown_fingerprint_when_graph_provider_fails() -> None:
    discovery = Ros1SensorDiscovery(
        graph_provider=_FailingGraphProvider(),  # type: ignore[arg-type]
        message_probe=StaticRos1MessageProbe({}),
        profile_fingerprint=DiscoveryFingerprint(source="ros1", topics_hash="sha256:abc"),
    )

    report = discovery.discover()
    payload = report.to_dict()

    assert payload["profile_fingerprint_status"] == "unknown"
    assert payload["profile_fingerprint_reason"] == "runtime_fingerprint_unavailable"
    assert report.verified_sensors() == []
```

- [ ] **Step 2: Run the failing ROS1 discovery tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py::test_ros1_discovery_reports_fresh_profile_fingerprint tests/test_ros1_sensor_discovery.py::test_ros1_discovery_reports_missing_profile_fingerprint tests/test_ros1_sensor_discovery.py::test_ros1_discovery_marks_stale_profile_confirmation tests/test_ros1_sensor_discovery.py::test_ros1_discovery_reports_unknown_fingerprint_when_graph_provider_fails -q
```

Expected: FAIL because `Ros1SensorDiscovery` does not accept `profile_fingerprint`.

- [ ] **Step 3: Implement ROS1 fingerprint comparison**

Modify imports in `src/fireclaw_core/ros/ros1_sensor_discovery.py`:

```python
    DiscoveryFingerprint,
    compare_fingerprints,
    fingerprint_topic_types,
```

Add field to `Ros1SensorDiscovery`:

```python
    profile_fingerprint: DiscoveryFingerprint | None = None
```

In the graph provider exception branch, replace `SensorDiscoveryReport(...)` with:

```python
                SensorDiscoveryReport(
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
                    ),
                    runtime_fingerprint=None,
                    profile_fingerprint=self.profile_fingerprint,
                    fingerprint_comparison=compare_fingerprints(None, self.profile_fingerprint),
                )
```

After successful `topic_types = self.graph_provider.topic_types()`, add:

```python
        runtime_fingerprint = fingerprint_topic_types(topic_types)
        fingerprint_comparison = compare_fingerprints(runtime_fingerprint, self.profile_fingerprint)
        confirmation_stale = fingerprint_comparison.status == "stale"
```

When creating a verified finding from `rule`, include:

```python
                        confirmed=rule.confirmed,
                        confirmation_stale=rule.confirmed and confirmation_stale,
```

When creating a degraded finding from `rule`, include:

```python
                        confirmed=rule.confirmed,
                        confirmation_stale=rule.confirmed and confirmation_stale,
```

Replace the final report with:

```python
        return self._remember(
            SensorDiscoveryReport(
                findings=tuple(findings),
                source="ros1",
                runtime_fingerprint=runtime_fingerprint,
                profile_fingerprint=self.profile_fingerprint,
                fingerprint_comparison=fingerprint_comparison,
            )
        )
```

- [ ] **Step 4: Run ROS1 discovery tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py -q
```

Expected: PASS.

- [ ] **Step 5: Checkpoint Task 3**

Run:

```bash
git diff --stat

# If the user has explicitly authorized commits for this execution, then run:
git add src/fireclaw_core/ros/ros1_sensor_discovery.py tests/test_ros1_sensor_discovery.py
git commit -m "feat: compare ros1 discovery fingerprints"
```

---

### Task 4: Wire Profile Fingerprints Into Gateway And Robot State

**Files:**
- Modify: `src/fireclaw_core/gateway/gateway.py`
- Test: `tests/test_gateway_robot_profile_config.py`
- Test: `tests/test_ros1_adapter_state.py`

- [ ] **Step 1: Add robot state regression**

Append to `tests/test_ros1_adapter_state.py`:

```python
from fireclaw_core.sensors.discovery import DiscoveryFingerprint


def test_ros1_adapter_state_exposes_stale_fingerprint_diagnostics() -> None:
    adapter = Ros1RobotAdapter(
        config=Ros1AdapterConfig(robot_id="robot-1"),
        sensor_discovery=Ros1SensorDiscovery(
            graph_provider=StaticRos1GraphProvider({"/scan": "sensor_msgs/LaserScan"}),
            message_probe=StaticRos1MessageProbe({"/scan": True}),
            profile_fingerprint=DiscoveryFingerprint(source="ros1", topics_hash="sha256:old"),
        ),
    )

    state = adapter.get_robot_state()

    assert state.available_sensors == ["lidar"]
    assert state.sensor_diagnostics is not None
    assert state.sensor_diagnostics["profile_fingerprint_status"] == "stale"
    assert state.sensor_diagnostics["profile_fingerprint_reason"] == "topics_hash_mismatch"
```

- [ ] **Step 2: Add gateway wiring regression**

Append to `tests/test_gateway_robot_profile_config.py`:

```python
def test_profile_gateway_passes_discovery_fingerprint_to_ros1_discovery(tmp_path: Path) -> None:
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

[robot.discovery_fingerprint]
source = "ros1"
topics_hash = "sha256:abc"
confirmed_by = "operator"
""".strip(),
        encoding="utf-8",
    )

    gateway = FireClawGateway(GatewayConfig(robot_profile_path=str(profile_path)))

    discovery = getattr(gateway.robot, "sensor_discovery", None)
    assert discovery is not None
    assert discovery.profile_fingerprint is not None
    assert discovery.profile_fingerprint.topics_hash == "sha256:abc"
```

- [ ] **Step 3: Run the failing wiring tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_adapter_state.py::test_ros1_adapter_state_exposes_stale_fingerprint_diagnostics tests/test_gateway_robot_profile_config.py::test_profile_gateway_passes_discovery_fingerprint_to_ros1_discovery -q
```

Expected: the adapter state test may pass after Task 3; the gateway test FAILS because `attach_profile_sensor_discovery()` does not pass the profile fingerprint.

- [ ] **Step 4: Wire the profile fingerprint**

Modify `src/fireclaw_core/gateway/gateway.py` inside `attach_profile_sensor_discovery()`.

In the `Ros1SensorDiscovery(...)` constructor, add:

```python
            profile_fingerprint=profile.discovery_fingerprint,
```

The constructor block should become:

```python
        Ros1SensorDiscovery(
            graph_provider=Ros1CliGraphProvider(),
            message_probe=Ros1CliMessageProbe(),
            extra_rules=profile.sensor_discovery.rules,
            timeout_seconds=profile.sensor_discovery.message_timeout_seconds,
            profile_fingerprint=profile.discovery_fingerprint,
        ),
```

- [ ] **Step 5: Run wiring tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_adapter_state.py tests/test_gateway_robot_profile_config.py -q
```

Expected: PASS.

- [ ] **Step 6: Checkpoint Task 4**

Run:

```bash
git diff --stat

# If the user has explicitly authorized commits for this execution, then run:
git add src/fireclaw_core/gateway/gateway.py tests/test_ros1_adapter_state.py tests/test_gateway_robot_profile_config.py
git commit -m "feat: wire profile fingerprints into ros1 discovery"
```

---

### Task 5: Write Fingerprints From `robot-profile discover`

**Files:**
- Modify: `src/fireclaw_core/mission/mission_cli.py`
- Test: `tests/test_mission_cli.py`

- [ ] **Step 1: Add CLI output regression**

Append to `tests/test_mission_cli.py`:

```python
def test_robot_profile_discover_outputs_runtime_fingerprint(tmp_path, monkeypatch):
    profile_path = tmp_path / "robot.toml"
    output_path = tmp_path / "discovered.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".format(data_dir=tmp_path / "robot-data").strip(),
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

    from fireclaw_core.mission.mission_cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "mission-cli",
            "robot-profile",
            "discover",
            "--profile",
            str(profile_path),
            "--output",
            str(output_path),
        ],
    )

    assert main() == 0

    text = output_path.read_text(encoding="utf-8")
    assert "[robot.discovery_fingerprint]" in text
    assert 'source = "ros1"' in text
    assert 'topics_hash = "sha256:' in text
    assert 'confirmed_by = "robot-profile discover"' in text
```

- [ ] **Step 2: Add write-profile replacement regression**

Append to `tests/test_mission_cli.py`:

```python
def test_robot_profile_discover_write_profile_replaces_existing_fingerprint_table(tmp_path, monkeypatch):
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]

[robot.discovery_fingerprint]
source = "ros1"
topics_hash = "sha256:old"
confirmed_by = "operator"

[robot.sensor_discovery]
enabled = true
message_timeout_seconds = 2.0
""".format(data_dir=tmp_path / "robot-data").strip(),
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
    monkeypatch.setattr(
        "sys.argv",
        [
            "mission-cli",
            "robot-profile",
            "discover",
            "--profile",
            str(profile_path),
            "--write-profile",
        ],
    )

    from fireclaw_core.mission.mission_cli import main

    assert main() == 0

    text = profile_path.read_text(encoding="utf-8")
    assert text.count("[robot.discovery_fingerprint]") == 1
    assert "sha256:old" not in text

    from fireclaw_core.agent.robot_profile import load_robot_capability_profile

    profile = load_robot_capability_profile(profile_path)
    assert profile.discovery_fingerprint is not None
    assert profile.discovery_fingerprint.topics_hash.startswith("sha256:")
    assert profile.sensor_discovery.rules[0].sensor == "lidar"
```

- [ ] **Step 3: Run the failing CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_cli.py::test_robot_profile_discover_outputs_runtime_fingerprint tests/test_mission_cli.py::test_robot_profile_discover_write_profile_replaces_existing_fingerprint_table -q
```

Expected: FAIL because CLI output does not include `[robot.discovery_fingerprint]`.

- [ ] **Step 4: Add TOML block helpers**

Modify `src/fireclaw_core/mission/mission_cli.py` inside `_handle_robot_profile()`.

After `_toml_escape()`, add:

```python
        def _fingerprint_lines() -> list[str]:
            if report.runtime_fingerprint is None:
                return []
            fingerprint = report.runtime_fingerprint
            lines = [
                "[robot.discovery_fingerprint]",
                f'source = "{_toml_escape(fingerprint.source)}"',
                f'topics_hash = "{_toml_escape(fingerprint.topics_hash)}"',
            ]
            if fingerprint.nodes_hash is not None:
                lines.append(f'nodes_hash = "{_toml_escape(fingerprint.nodes_hash)}"')
            lines.append('confirmed_by = "robot-profile discover"')
            lines.append("")
            return lines

        def _replace_table_block(existing: str, table_header: str, replacement_lines: list[str]) -> str:
            if table_header not in existing:
                return existing.rstrip() + "\n\n" + "\n".join(replacement_lines).rstrip() + "\n"
            lines = existing.splitlines()
            result: list[str] = []
            index = 0
            while index < len(lines):
                if lines[index].strip() == table_header:
                    result.extend(replacement_lines)
                    index += 1
                    while index < len(lines):
                        stripped = lines[index].strip()
                        if stripped.startswith("[") and stripped.endswith("]"):
                            break
                        index += 1
                    continue
                result.append(lines[index])
                index += 1
            return "\n".join(result).rstrip() + "\n"
```

- [ ] **Step 5: Include fingerprint TOML in discover output**

Replace `header_lines` with:

```python
        fingerprint_lines = _fingerprint_lines()
        header_lines: list[str] = [
            "# Suggested FireClaw sensor discovery rules.",
            "# Review before copying into the robot profile.",
            "",
        ]
        header_lines.extend(fingerprint_lines)
        header_lines.extend([
            "[robot.sensor_discovery]",
            "enabled = true",
            f"message_timeout_seconds = {profile.sensor_discovery.message_timeout_seconds:.1f}",
            "",
        ])
```

- [ ] **Step 6: Replace existing fingerprint table during write-profile**

Replace the current `if args.write_profile:` block:

```python
        if args.write_profile:
            existing = output.read_text(encoding="utf-8")
            if "[robot.sensor_discovery]" in existing:
                addition = "\n".join(rule_lines)
            else:
                addition = text
            output.write_text(existing.rstrip() + "\n\n" + addition, encoding="utf-8")
```

with:

```python
        if args.write_profile:
            existing = output.read_text(encoding="utf-8")
            updated = existing
            if fingerprint_lines:
                updated = _replace_table_block(updated, "[robot.discovery_fingerprint]", fingerprint_lines)
            if "[robot.sensor_discovery]" in updated:
                addition = "\n".join(rule_lines)
            else:
                addition = "\n".join([
                    "[robot.sensor_discovery]",
                    "enabled = true",
                    f"message_timeout_seconds = {profile.sensor_discovery.message_timeout_seconds:.1f}",
                    "",
                    *rule_lines,
                ])
            output.write_text(updated.rstrip() + "\n\n" + addition.rstrip() + "\n", encoding="utf-8")
```

Leave the `else: output.write_text(text, ...)` path unchanged.

- [ ] **Step 7: Run CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_cli.py::test_robot_profile_discover_writes_suggested_rules tests/test_mission_cli.py::test_robot_profile_discover_write_profile_appends_rules tests/test_mission_cli.py::test_robot_profile_discover_write_profile_keeps_existing_discovery_table_parseable tests/test_mission_cli.py::test_robot_profile_discover_outputs_runtime_fingerprint tests/test_mission_cli.py::test_robot_profile_discover_write_profile_replaces_existing_fingerprint_table -q
```

Expected: PASS.

- [ ] **Step 8: Checkpoint Task 5**

Run:

```bash
git diff --stat

# If the user has explicitly authorized commits for this execution, then run:
git add src/fireclaw_core/mission/mission_cli.py tests/test_mission_cli.py
git commit -m "feat: write discovery fingerprints from profile discovery"
```

---

### Task 6: Final Verification And Memory Update

**Files:**
- Modify: `memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md`

- [ ] **Step 1: Run the focused fingerprint suite**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_discovery.py tests/test_robot_profile.py tests/test_ros1_sensor_discovery.py tests/test_ros1_adapter_state.py tests/test_gateway_robot_profile_config.py tests/test_mission_cli.py -q
```

Expected: PASS.

- [ ] **Step 2: Run the broader sensor/profile suite**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_discovery.py tests/test_ros1_sensor_discovery.py tests/test_robot_profile.py tests/test_ros1_adapter_state.py tests/test_gateway_robot_profile_config.py tests/test_safety.py tests/test_agent.py tests/test_gateway_structured_task.py tests/test_mission_cli.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full suite and record known unrelated failures**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected based on current memory: either full PASS, or the same two unrelated failures:

- `tests/test_cli.py::test_module_cli_accepts_ros1_config_for_adapter_skeleton`
- `tests/test_serve.py::test_start_server_submit_and_trace`

If new failures appear in fingerprint/sensor/profile tests, stop and debug before proceeding.

- [ ] **Step 4: Update memory**

Append to `memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md`:

```markdown

## Discovery Fingerprint Phase 1 Implementation (2026-06-14)

Implemented ROS1 discovery fingerprint stale-profile detection.

Files changed:

- `src/fireclaw_core/sensors/discovery.py`
- `src/fireclaw_core/sensors/__init__.py`
- `src/fireclaw_core/agent/robot_profile.py`
- `src/fireclaw_core/ros/ros1_sensor_discovery.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/mission/mission_cli.py`

Behavior:

- Runtime ROS1 topic/type snapshots now produce a deterministic SHA-256 discovery fingerprint.
- Robot profiles may store `[robot.discovery_fingerprint]`.
- ROS1 discovery reports `fresh`, `missing`, `stale`, or `unknown` profile fingerprint status.
- Stale profile confirmations are surfaced on findings with `confirmation_stale=true`.
- SafetyGate remains unchanged and still uses only live verified sensors.
- `robot-profile discover` writes a runtime fingerprint block in suggested output and `--write-profile`.

Verification:

- Focused fingerprint suite: record the exact pytest summary from Step 1.
- Broader sensor/profile suite: record the exact pytest summary from Step 2.
- Full suite: record the exact pytest summary from Step 3, including any unrelated failures by test id.
```

Do not write the memory update until Steps 1-3 have been run. The three verification bullets must contain the exact results observed during this execution.

- [ ] **Step 5: Checkpoint Task 6**

Run:

```bash
git diff --stat

# If the user has explicitly authorized commits for this execution, then run:
git add memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md
git commit -m "docs: record discovery fingerprint verification"
```

---

## Final Acceptance Criteria

- `SensorDiscoveryReport.to_dict()` includes profile/runtime fingerprint diagnostics.
- Profiles without `[robot.discovery_fingerprint]` still load and produce `profile_fingerprint_status = "missing"`.
- Profiles with mismatched fingerprints produce `profile_fingerprint_status = "stale"` and reason `topics_hash_mismatch`.
- Stale confirmed profile rules expose `confirmation_stale = true` in findings.
- Stale or missing profile fingerprints do not put a sensor in `available_sensors` unless live message verification succeeds.
- `robot-profile discover --write-profile` leaves the profile parseable and does not duplicate `[robot.discovery_fingerprint]`.
- SafetyGate remains ROS-agnostic and unchanged.
