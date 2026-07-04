# Operator Confirmation Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an auditable operator confirmation loop for sensor discovery so FireClaw can distinguish automatically discovered candidates from human-confirmed profile authority.

**Architecture:** Keep ROS1 discovery as the runtime signal producer and keep robot profiles as the deployment record. Move profile discovery TOML rendering/update logic out of `mission_cli.py` into a focused helper module, then add two CLI actions: `robot-profile diff-discovery` for review and `robot-profile confirm-discovery` for explicit write-back with `confirmed_by`, `confirmed_at`, and runtime fingerprint binding.

**Tech Stack:** Python 3.11 dataclasses, current TOML loading through `tomllib`, existing ROS1 discovery abstractions, current mission CLI, pytest with monkeypatched ROS1 providers, no new runtime dependency.

---

## Source Context

- Master plan: `docs/superpowers/plans/2026-06-14-sensor-capability-grounding-master-plan.md`
- Final design: `docs/superpowers/specs/2026-06-14-sensor-capability-grounding-final-design.md`
- Phase 3 review: `memory/2026-06-15/fireclaw-phase3-review.md`
- Current CLI entry: `src/fireclaw_core/mission/mission_cli.py`
- Current profile model: `src/fireclaw_core/agent/robot_profile.py`
- Current discovery model: `src/fireclaw_core/sensors/discovery.py`

## Current Baseline

Implemented before Phase 4:

- `robot-profile discover` can write suggested discovery rules.
- Profile can parse `[robot.discovery_fingerprint]`.
- `SensorMappingRule` has `confirmed`, but not `confirmed_by` or `confirmed_at`.
- `Ros1SensorDiscovery` marks confirmed rules as stale when profile fingerprint is missing/stale/unknown.
- Phase 3 full suite review: `1227 passed, 6 skipped`.

Important semantic change for Phase 4:

- `robot-profile discover` remains a discovery/suggestion command.
- `robot-profile confirm-discovery` becomes the command that records semantic operator confirmation.
- A rule is auditable only when `confirmed=true`, `confirmed_by`, `confirmed_at`, and a matching `[robot.discovery_fingerprint]` are present.

## File Structure

- Modify `src/fireclaw_core/sensors/discovery.py`
  - Add optional `confirmed_by` and `confirmed_at` to `SensorMappingRule`.
  - Include these fields in `to_dict()`.
- Modify `src/fireclaw_core/agent/robot_profile.py`
  - Parse optional rule-level `confirmed_by` and `confirmed_at`.
  - Parse optional fingerprint-level `confirmed_at` while preserving existing `created_at`.
- Create `src/fireclaw_core/agent/profile_discovery.py`
  - Owns TOML escaping, discovery report to candidate rules, profile block replacement, diff summary, and confirmation rendering.
- Modify `src/fireclaw_core/mission/mission_cli.py`
  - Add `robot-profile diff-discovery`.
  - Add `robot-profile confirm-discovery`.
  - Reuse helper functions from `profile_discovery.py`.
- Modify `tests/test_robot_profile.py`
  - Cover parsing of rule-level audit metadata and fingerprint `confirmed_at`.
- Modify `tests/test_sensor_discovery.py`
  - Cover serialization of rule audit metadata.
- Modify `tests/test_mission_cli.py`
  - Cover `diff-discovery`, `confirm-discovery`, changed fingerprint stale behavior, and parseable write-back.
- Modify `docs/deployment/ros1-gazebo-debugging-guide.md`
  - Document the new review/confirm workflow.
- Create `memory/2026-06-15/fireclaw-phase4-operator-confirmation.md`
  - Record implementation and verification results.

---

### Task 1: Extend Discovery Profile Audit Metadata

**Files:**
- Modify: `src/fireclaw_core/sensors/discovery.py`
- Modify: `src/fireclaw_core/agent/robot_profile.py`
- Modify: `tests/test_sensor_discovery.py`
- Modify: `tests/test_robot_profile.py`

- [ ] **Step 1: Write failing tests for rule and fingerprint audit metadata**

Append to `tests/test_sensor_discovery.py`:

```python
from fireclaw_core.sensors.discovery import SensorMappingRule


def test_sensor_mapping_rule_serializes_confirmation_audit_metadata() -> None:
    rule = SensorMappingRule(
        topic_pattern="/camera/image_raw",
        message_type="sensor_msgs/Image",
        sensor="rgb_camera",
        source="profile",
        confidence=0.95,
        confirmed=True,
        confirmed_by="operator-1",
        confirmed_at="2026-06-15T12:00:00+08:00",
    )

    assert rule.to_dict()["confirmed_by"] == "operator-1"
    assert rule.to_dict()["confirmed_at"] == "2026-06-15T12:00:00+08:00"
```

Append to `tests/test_robot_profile.py`:

```python
def test_robot_profile_loads_sensor_rule_confirmation_audit_metadata(tmp_path: Path) -> None:
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

[[robot.sensor_discovery.rules]]
topic_pattern = "/camera/image_raw"
message_type = "sensor_msgs/Image"
sensor = "rgb_camera"
confidence = 0.95
confirmed = true
confirmed_by = "operator-1"
confirmed_at = "2026-06-15T12:00:00+08:00"
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)
    rule = profile.sensor_discovery.rules[0]

    assert rule.confirmed is True
    assert rule.confirmed_by == "operator-1"
    assert rule.confirmed_at == "2026-06-15T12:00:00+08:00"
```

```python
def test_load_robot_profile_with_discovery_fingerprint_confirmation_time(tmp_path: Path) -> None:
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
confirmed_by = "operator-1"
confirmed_at = "2026-06-15T12:00:00+08:00"
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)

    assert profile.discovery_fingerprint is not None
    assert profile.discovery_fingerprint.confirmed_by == "operator-1"
    assert profile.discovery_fingerprint.confirmed_at == "2026-06-15T12:00:00+08:00"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_sensor_discovery.py::test_sensor_mapping_rule_serializes_confirmation_audit_metadata \
  tests/test_robot_profile.py::test_robot_profile_loads_sensor_rule_confirmation_audit_metadata \
  tests/test_robot_profile.py::test_load_robot_profile_with_discovery_fingerprint_confirmation_time \
  -q
```

Expected: FAIL because `SensorMappingRule` and `DiscoveryFingerprint` do not yet expose `confirmed_at` and rule-level audit metadata.

- [ ] **Step 3: Extend discovery dataclasses**

Modify `src/fireclaw_core/sensors/discovery.py`.

Update `DiscoveryFingerprint`:

```python
@dataclass(frozen=True)
class DiscoveryFingerprint:
    source: str
    topics_hash: str
    nodes_hash: str | None = None
    created_at: str | None = None
    confirmed_by: str | None = None
    confirmed_at: str | None = None
```

Inside `DiscoveryFingerprint.to_dict()`, add:

```python
        if self.confirmed_at is not None:
            payload["confirmed_at"] = self.confirmed_at
```

Update `SensorMappingRule`:

```python
@dataclass(frozen=True)
class SensorMappingRule:
    topic_pattern: str
    message_type: str
    sensor: str
    source: str = "default"
    confidence: float = 0.8
    confirmed: bool = False
    confirmed_by: str | None = None
    confirmed_at: str | None = None
```

Inside `SensorMappingRule.to_dict()`, build the current payload, then add:

```python
        if self.confirmed_by is not None:
            payload["confirmed_by"] = self.confirmed_by
        if self.confirmed_at is not None:
            payload["confirmed_at"] = self.confirmed_at
```

- [ ] **Step 4: Parse audit metadata from robot profiles**

Modify `src/fireclaw_core/agent/robot_profile.py`.

When constructing `SensorMappingRule`, add:

```python
                confirmed_by=_optional_string(item, "confirmed_by"),
                confirmed_at=_optional_string(item, "confirmed_at"),
```

Inside `_discovery_fingerprint()`, parse:

```python
    confirmed_at = raw.get("confirmed_at")
    if confirmed_at is not None and not isinstance(confirmed_at, str):
        raise ValueError("robot.discovery_fingerprint.confirmed_at must be a string when provided.")
```

And pass it into `DiscoveryFingerprint`:

```python
        confirmed_at=confirmed_at.strip() if isinstance(confirmed_at, str) else None,
```

- [ ] **Step 5: Run audit metadata tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_sensor_discovery.py tests/test_robot_profile.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/fireclaw_core/sensors/discovery.py src/fireclaw_core/agent/robot_profile.py tests/test_sensor_discovery.py tests/test_robot_profile.py
git commit -m "feat: add sensor discovery confirmation audit metadata"
```

---

### Task 2: Add Profile Discovery Helper Module

**Files:**
- Create: `src/fireclaw_core/agent/profile_discovery.py`
- Create: `tests/test_profile_discovery.py`

- [ ] **Step 1: Write failing helper tests**

Create `tests/test_profile_discovery.py`:

```python
from fireclaw_core.agent.profile_discovery import (
    DiscoveryDiff,
    build_discovery_diff,
    render_confirmed_discovery_blocks,
    replace_robot_table_block,
)
from fireclaw_core.sensors.discovery import (
    DiscoveryFingerprint,
    FingerprintComparison,
    SensorDiscoveryReport,
    SensorFinding,
    SensorMappingRule,
)


def test_build_discovery_diff_classifies_added_and_confirmed_rules() -> None:
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
            SensorFinding(
                sensor="rgb_camera",
                topic="/camera/image_raw",
                message_type="sensor_msgs/Image",
                status="verified",
                confidence=0.9,
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
            confirmed=True,
        ),
    )

    diff = build_discovery_diff(report, profile_rules)

    assert diff.status == "changed"
    assert diff.added == ["/camera/image_raw"]
    assert diff.confirmed == ["/scan"]
    assert diff.stale_confirmation is False
```

```python
def test_render_confirmed_discovery_blocks_writes_audit_metadata() -> None:
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
    )

    text = render_confirmed_discovery_blocks(
        report=report,
        message_timeout_seconds=2.0,
        confirmed_by="operator-1",
        confirmed_at="2026-06-15T12:00:00+08:00",
    )

    assert "[robot.discovery_fingerprint]" in text
    assert 'confirmed_by = "operator-1"' in text
    assert 'confirmed_at = "2026-06-15T12:00:00+08:00"' in text
    assert "[[robot.sensor_discovery.rules]]" in text
    assert 'sensor = "lidar"' in text
    assert "confirmed = true" in text
```

```python
def test_replace_robot_table_block_replaces_nested_array_tables() -> None:
    existing = """
[robot]
id = "robot-1"

[robot.sensor_discovery]
enabled = true
message_timeout_seconds = 1.0

[[robot.sensor_discovery.rules]]
topic_pattern = "/old"
message_type = "sensor_msgs/Image"
sensor = "rgb_camera"
""".strip()

    updated = replace_robot_table_block(
        existing,
        "[robot.sensor_discovery]",
        [
            "[robot.sensor_discovery]",
            "enabled = true",
            "message_timeout_seconds = 2.0",
            "",
            "[[robot.sensor_discovery.rules]]",
            'topic_pattern = "/scan"',
            'message_type = "sensor_msgs/LaserScan"',
            'sensor = "lidar"',
            "confirmed = true",
        ],
    )

    assert 'topic_pattern = "/old"' not in updated
    assert 'topic_pattern = "/scan"' in updated
    assert updated.count("[robot.sensor_discovery]") == 1
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_profile_discovery.py -q
```

Expected: FAIL because `fireclaw_core.agent.profile_discovery` does not exist.

- [ ] **Step 3: Implement helper module**

Create `src/fireclaw_core/agent/profile_discovery.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field

from fireclaw_core.sensors.discovery import SensorDiscoveryReport, SensorMappingRule


@dataclass(frozen=True)
class DiscoveryDiff:
    status: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)
    stale_confirmation: bool = False
    fingerprint_status: str | None = None
    fingerprint_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status,
            "added": list(self.added),
            "removed": list(self.removed),
            "changed": list(self.changed),
            "confirmed": list(self.confirmed),
            "unconfirmed": list(self.unconfirmed),
            "stale_confirmation": self.stale_confirmation,
        }
        if self.fingerprint_status is not None:
            payload["fingerprint_status"] = self.fingerprint_status
        if self.fingerprint_reason is not None:
            payload["fingerprint_reason"] = self.fingerprint_reason
        return payload


def build_discovery_diff(
    report: SensorDiscoveryReport,
    profile_rules: tuple[SensorMappingRule, ...],
) -> DiscoveryDiff:
    runtime = {
        (finding.topic, finding.message_type): finding
        for finding in report.findings
        if finding.status in {"verified", "degraded"}
    }
    profile = {
        (rule.topic_pattern, rule.message_type): rule
        for rule in profile_rules
    }
    added = sorted(topic for (topic, _message_type) in runtime.keys() - profile.keys())
    removed = sorted(topic for (topic, _message_type) in profile.keys() - runtime.keys())
    changed: list[str] = []
    confirmed: list[str] = []
    unconfirmed: list[str] = []
    for key in sorted(runtime.keys() & profile.keys()):
        finding = runtime[key]
        rule = profile[key]
        if finding.sensor != rule.sensor:
            changed.append(finding.topic)
        elif rule.confirmed:
            confirmed.append(finding.topic)
        else:
            unconfirmed.append(finding.topic)
    comparison = report.fingerprint_comparison
    fingerprint_status = comparison.status if comparison is not None else None
    fingerprint_reason = comparison.reason if comparison is not None else None
    stale_confirmation = fingerprint_status in {"missing", "stale", "unknown"}
    status = "fresh" if not added and not removed and not changed and not stale_confirmation else "changed"
    return DiscoveryDiff(
        status=status,
        added=added,
        removed=removed,
        changed=changed,
        confirmed=confirmed,
        unconfirmed=unconfirmed,
        stale_confirmation=stale_confirmation,
        fingerprint_status=fingerprint_status,
        fingerprint_reason=fingerprint_reason,
    )


def render_confirmed_discovery_blocks(
    *,
    report: SensorDiscoveryReport,
    message_timeout_seconds: float,
    confirmed_by: str,
    confirmed_at: str,
) -> str:
    lines: list[str] = []
    if report.runtime_fingerprint is not None:
        fingerprint = report.runtime_fingerprint
        lines.extend([
            "[robot.discovery_fingerprint]",
            f'source = "{_toml_escape(fingerprint.source)}"',
            f'topics_hash = "{_toml_escape(fingerprint.topics_hash)}"',
        ])
        if fingerprint.nodes_hash is not None:
            lines.append(f'nodes_hash = "{_toml_escape(fingerprint.nodes_hash)}"')
        lines.extend([
            f'confirmed_by = "{_toml_escape(confirmed_by)}"',
            f'confirmed_at = "{_toml_escape(confirmed_at)}"',
            "",
        ])
    lines.extend([
        "[robot.sensor_discovery]",
        "enabled = true",
        f"message_timeout_seconds = {message_timeout_seconds:.1f}",
        "",
    ])
    for finding in report.findings:
        if finding.status != "verified":
            continue
        lines.extend([
            "[[robot.sensor_discovery.rules]]",
            f'topic_pattern = "{_toml_escape(finding.topic)}"',
            f'message_type = "{_toml_escape(finding.message_type)}"',
            f'sensor = "{_toml_escape(finding.sensor)}"',
            f"confidence = {finding.confidence:.2f}",
            "confirmed = true",
            f'confirmed_by = "{_toml_escape(confirmed_by)}"',
            f'confirmed_at = "{_toml_escape(confirmed_at)}"',
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def replace_robot_table_block(existing: str, table_header: str, replacement_lines: list[str]) -> str:
    lines = existing.splitlines()
    result: list[str] = []
    index = 0
    found = False
    while index < len(lines):
        if lines[index].strip() == table_header:
            found = True
            result.extend(replacement_lines)
            index += 1
            while index < len(lines):
                stripped = lines[index].strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    if table_header == "[robot.sensor_discovery]" and stripped == "[[robot.sensor_discovery.rules]]":
                        index += 1
                        while index < len(lines):
                            nested = lines[index].strip()
                            if nested.startswith("[") and nested.endswith("]"):
                                break
                            index += 1
                        continue
                    break
                index += 1
            continue
        result.append(lines[index])
        index += 1
    if not found:
        result.extend(["", *replacement_lines])
    return "\n".join(result).rstrip() + "\n"


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
```

- [ ] **Step 4: Run helper tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_profile_discovery.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/fireclaw_core/agent/profile_discovery.py tests/test_profile_discovery.py
git commit -m "feat: add profile discovery confirmation helpers"
```

---

### Task 3: Add `robot-profile diff-discovery`

**Files:**
- Modify: `src/fireclaw_core/mission/mission_cli.py`
- Modify: `tests/test_mission_cli.py`

- [ ] **Step 1: Write failing CLI tests for diff-discovery**

Append to `tests/test_mission_cli.py`:

```python
def test_robot_profile_diff_discovery_reports_added_candidate(tmp_path, monkeypatch):
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
            "diff-discovery",
            "--profile",
            str(profile_path),
        ],
    )

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "changed"
    assert payload["added"] == ["/scan"]
```

This test uses `capsys`; add `capsys` to the function signature:

```python
def test_robot_profile_diff_discovery_reports_added_candidate(tmp_path, monkeypatch, capsys):
```

Append another test:

```python
def test_robot_profile_diff_discovery_reports_stale_fingerprint(tmp_path, monkeypatch, capsys):
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
confirmed_by = "operator-1"
confirmed_at = "2026-06-15T12:00:00+08:00"

[[robot.sensor_discovery.rules]]
topic_pattern = "/scan"
message_type = "sensor_msgs/LaserScan"
sensor = "lidar"
confirmed = true
confirmed_by = "operator-1"
confirmed_at = "2026-06-15T12:00:00+08:00"
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
            "diff-discovery",
            "--profile",
            str(profile_path),
        ],
    )

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stale_confirmation"] is True
    assert payload["fingerprint_status"] == "stale"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mission_cli.py::test_robot_profile_diff_discovery_reports_added_candidate \
  tests/test_mission_cli.py::test_robot_profile_diff_discovery_reports_stale_fingerprint \
  -q
```

Expected: FAIL because `diff-discovery` is not a known subcommand.

- [ ] **Step 3: Add CLI parser and handler**

Modify `src/fireclaw_core/mission/mission_cli.py`.

After the existing `profile_discover` parser block, add:

```python
    profile_diff = robot_profile_sub.add_parser(
        "diff-discovery",
        help="Compare current ROS1 sensor discovery against profile rules and fingerprint.",
    )
    profile_diff.add_argument("--profile", required=True, help="Path to robot profile TOML.")
```

Inside `_handle_robot_profile()`, import helpers near the existing `load_robot_capability_profile` import:

```python
    from fireclaw_core.agent.profile_discovery import build_discovery_diff
```

Add this branch before the final unknown subcommand error:

```python
    if args.robot_profile_command == "diff-discovery":
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
            profile_fingerprint=profile.discovery_fingerprint,
        )
        report = discovery.discover()
        diff = build_discovery_diff(report, profile.sensor_discovery.rules)
        payload = diff.to_dict()
        payload["verified_sensors"] = report.verified_sensors()
        payload["findings"] = [finding.to_dict() for finding in report.findings]
        _print_json(payload)
        return 0
```

- [ ] **Step 4: Run diff tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mission_cli.py::test_robot_profile_diff_discovery_reports_added_candidate \
  tests/test_mission_cli.py::test_robot_profile_diff_discovery_reports_stale_fingerprint \
  -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/fireclaw_core/mission/mission_cli.py tests/test_mission_cli.py
git commit -m "feat: add robot profile discovery diff command"
```

---

### Task 4: Add `robot-profile confirm-discovery`

**Files:**
- Modify: `src/fireclaw_core/mission/mission_cli.py`
- Modify: `tests/test_mission_cli.py`

- [ ] **Step 1: Write failing CLI tests for confirm-discovery**

Append to `tests/test_mission_cli.py`:

```python
def test_robot_profile_confirm_discovery_writes_confirmed_rules_and_audit_metadata(tmp_path, monkeypatch):
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
            "confirm-discovery",
            "--profile",
            str(profile_path),
            "--confirmed-by",
            "operator-1",
            "--confirmed-at",
            "2026-06-15T12:00:00+08:00",
        ],
    )

    assert main() == 0

    from fireclaw_core.agent.robot_profile import load_robot_capability_profile

    profile = load_robot_capability_profile(profile_path)
    assert profile.discovery_fingerprint is not None
    assert profile.discovery_fingerprint.confirmed_by == "operator-1"
    assert profile.discovery_fingerprint.confirmed_at == "2026-06-15T12:00:00+08:00"
    assert profile.sensor_discovery.rules[0].sensor == "lidar"
    assert profile.sensor_discovery.rules[0].confirmed is True
    assert profile.sensor_discovery.rules[0].confirmed_by == "operator-1"
    assert profile.sensor_discovery.rules[0].confirmed_at == "2026-06-15T12:00:00+08:00"
```

```python
def test_robot_profile_confirm_discovery_refuses_when_runtime_fingerprint_unavailable(tmp_path, monkeypatch, capsys):
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
""".format(data_dir=tmp_path / "robot-data").strip(),
        encoding="utf-8",
    )

    from fireclaw_core.ros import ros1_sensor_discovery

    def fail_topic_types(self):
        raise RuntimeError("roscore not reachable")

    monkeypatch.setattr(ros1_sensor_discovery.Ros1CliGraphProvider, "topic_types", fail_topic_types)

    from fireclaw_core.mission.mission_cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "mission-cli",
            "robot-profile",
            "confirm-discovery",
            "--profile",
            str(profile_path),
            "--confirmed-by",
            "operator-1",
        ],
    )

    assert main() == 1
    assert "runtime fingerprint unavailable" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mission_cli.py::test_robot_profile_confirm_discovery_writes_confirmed_rules_and_audit_metadata \
  tests/test_mission_cli.py::test_robot_profile_confirm_discovery_refuses_when_runtime_fingerprint_unavailable \
  -q
```

Expected: FAIL because `confirm-discovery` is not a known subcommand.

- [ ] **Step 3: Add CLI parser**

Modify `src/fireclaw_core/mission/mission_cli.py`.

After the `diff-discovery` parser block, add:

```python
    profile_confirm = robot_profile_sub.add_parser(
        "confirm-discovery",
        help="Confirm current ROS1 sensor discovery and write auditable profile rules.",
    )
    profile_confirm.add_argument("--profile", required=True, help="Path to robot profile TOML.")
    profile_confirm.add_argument("--confirmed-by", required=True, help="Operator ID that reviewed the discovery mapping.")
    profile_confirm.add_argument("--confirmed-at", default=None, help="ISO-8601 confirmation time. Defaults to current UTC time.")
```

- [ ] **Step 4: Add confirm-discovery handler**

In `_handle_robot_profile()`, import:

```python
    from fireclaw_core.agent.profile_discovery import (
        build_discovery_diff,
        render_confirmed_discovery_blocks,
        replace_robot_table_block,
    )
```

Add this branch before the final unknown subcommand error:

```python
    if args.robot_profile_command == "confirm-discovery":
        from datetime import datetime, timezone
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
            profile_fingerprint=profile.discovery_fingerprint,
        )
        report = discovery.discover()
        if report.runtime_fingerprint is None:
            print("Error: runtime fingerprint unavailable; refusing to confirm discovery.", file=sys.stderr)
            return 1
        if not report.verified_sensors():
            print("Error: no verified sensors discovered; refusing to confirm discovery.", file=sys.stderr)
            return 1
        confirmed_at = args.confirmed_at or datetime.now(timezone.utc).isoformat()
        block_text = render_confirmed_discovery_blocks(
            report=report,
            message_timeout_seconds=profile.sensor_discovery.message_timeout_seconds,
            confirmed_by=args.confirmed_by,
            confirmed_at=confirmed_at,
        )
        replacement_lines = block_text.splitlines()
        profile_path = Path(args.profile)
        existing = profile_path.read_text(encoding="utf-8")
        updated = replace_robot_table_block(existing, "[robot.discovery_fingerprint]", [])
        updated = replace_robot_table_block(updated, "[robot.sensor_discovery]", replacement_lines)
        profile_path.write_text(updated, encoding="utf-8")
        diff = build_discovery_diff(report, profile.sensor_discovery.rules)
        _print_json({
            "status": "confirmed",
            "profile": str(profile_path),
            "confirmed_by": args.confirmed_by,
            "confirmed_at": confirmed_at,
            "verified_sensors": report.verified_sensors(),
            "diff": diff.to_dict(),
        })
        return 0
```

Important: if `replace_robot_table_block(existing, "[robot.discovery_fingerprint]", [])` leaves a blank section marker or malformed TOML, adjust the helper so empty replacement removes the table block entirely.

- [ ] **Step 5: Run confirm tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mission_cli.py::test_robot_profile_confirm_discovery_writes_confirmed_rules_and_audit_metadata \
  tests/test_mission_cli.py::test_robot_profile_confirm_discovery_refuses_when_runtime_fingerprint_unavailable \
  -q
```

Expected: all tests pass.

- [ ] **Step 6: Run existing robot-profile CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_cli.py -q
```

Expected: all tests pass. If existing `discover --write-profile` tests fail because semantics changed, update them so `discover` writes candidates and `confirm-discovery` writes confirmed audit metadata.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/fireclaw_core/mission/mission_cli.py tests/test_mission_cli.py
git commit -m "feat: add robot profile discovery confirmation command"
```

---

### Task 5: Document Phase 4 Workflow And Verify

**Files:**
- Modify: `docs/deployment/ros1-gazebo-debugging-guide.md`
- Create: `memory/2026-06-15/fireclaw-phase4-operator-confirmation.md`

- [ ] **Step 1: Document operator confirmation workflow**

Append to `docs/deployment/ros1-gazebo-debugging-guide.md`:

```markdown
## Operator Confirmation Workflow

Sensor discovery is not the same as operator confirmation.

Use this sequence when onboarding or changing a ROS1/Gazebo robot profile:

```bash
.venv/bin/python -m fireclaw_core robot-profile discover \
  --profile examples/robot_profiles/gazebo_turtlebot3.toml \
  --output data/debug-gazebo/discovered-sensors.toml

.venv/bin/python -m fireclaw_core robot-profile diff-discovery \
  --profile examples/robot_profiles/gazebo_turtlebot3.toml

.venv/bin/python -m fireclaw_core robot-profile confirm-discovery \
  --profile examples/robot_profiles/gazebo_turtlebot3.toml \
  --confirmed-by operator-id
```

`discover` produces candidates. `diff-discovery` compares current runtime discovery against the profile. `confirm-discovery` is the auditable action that writes confirmed rules and the runtime fingerprint.

Confirmed rules include:

- `confirmed = true`
- `confirmed_by`
- `confirmed_at`

The profile fingerprint includes:

- `source`
- `topics_hash`
- `confirmed_by`
- `confirmed_at`

If the runtime fingerprint later changes, FireClaw marks the confirmation stale and SafetyGate continues to trust only live verified sensors.
```

- [ ] **Step 2: Create Phase 4 memory record**

Create `memory/2026-06-15/fireclaw-phase4-operator-confirmation.md`:

```markdown
# FireClaw Phase 4 Operator Confirmation

## Task Goal

Add auditable operator confirmation for ROS1 sensor discovery.

## Files Modified

- `src/fireclaw_core/sensors/discovery.py`
- `src/fireclaw_core/agent/robot_profile.py`
- `src/fireclaw_core/agent/profile_discovery.py`
- `src/fireclaw_core/mission/mission_cli.py`
- `tests/test_sensor_discovery.py`
- `tests/test_robot_profile.py`
- `tests/test_profile_discovery.py`
- `tests/test_mission_cli.py`
- `docs/deployment/ros1-gazebo-debugging-guide.md`

## Verification Commands

- `.venv/bin/python -m pytest tests/test_sensor_discovery.py tests/test_robot_profile.py tests/test_profile_discovery.py tests/test_mission_cli.py -q`
- `.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py tests/test_gateway_robot_profile_config.py tests/test_ros1_adapter_state.py -q`
- `.venv/bin/python -m pytest -q`

## Current Conclusion

Fill this section with actual verification results after implementation.
```

- [ ] **Step 3: Run focused Phase 4 verification**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_sensor_discovery.py \
  tests/test_robot_profile.py \
  tests/test_profile_discovery.py \
  tests/test_mission_cli.py \
  tests/test_ros1_sensor_discovery.py \
  tests/test_gateway_robot_profile_config.py \
  tests/test_ros1_adapter_state.py \
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

Update `memory/2026-06-15/fireclaw-phase4-operator-confirmation.md`:

```markdown
## Actual Verification Results

- Focused Phase 4 command: replace this sentence with the exact pass/fail count.
- Full suite command: replace this sentence with the exact pass/fail/skip count.

## Remaining Gaps

- Phase 5 adapter-agnostic discovery backend is still not implemented.
- Confirmation is profile-file based; no separate append-only deployment audit log exists yet.
- ROS1 discovery still uses ROS1-specific backend code; Phase 5 should put it behind a common backend protocol.
```

- [ ] **Step 6: Commit Task 5**

```bash
git add docs/deployment/ros1-gazebo-debugging-guide.md memory/2026-06-15/fireclaw-phase4-operator-confirmation.md
git commit -m "docs: document discovery confirmation workflow"
```

---

## Final Verification Checklist

- [ ] Profile rules can store `confirmed_by` and `confirmed_at`.
- [ ] Profile fingerprint can store `confirmed_at`.
- [ ] `robot-profile diff-discovery` reports added/removed/changed/stale confirmation information.
- [ ] `robot-profile confirm-discovery` writes confirmed rules with audit metadata.
- [ ] `confirm-discovery` refuses to confirm when runtime fingerprint is unavailable.
- [ ] Confirmed mappings become stale when runtime fingerprint changes.
- [ ] Profile write-back remains parseable TOML.
- [ ] Existing SafetyGate behavior still trusts only live verified sensors.
- [ ] Full suite has been run in the implementation session.

## Self-Review

Spec coverage:

- `diff-discovery` is covered by Task 3.
- `confirm-discovery` is covered by Task 4.
- `confirmed_by` and `confirmed_at` are covered by Task 1 and Task 4.
- Fingerprint binding and stale confirmation behavior are covered by Task 3.
- Documentation and memory are covered by Task 5.

Placeholder scan:

- The only "replace this sentence" text is in the memory template for actual verification output and must be replaced during implementation.
- There are no `TBD` markers or open-ended implementation steps.

Type consistency:

- `SensorMappingRule.confirmed_by` and `SensorMappingRule.confirmed_at` are optional strings.
- `DiscoveryFingerprint.confirmed_at` is an optional string.
- `DiscoveryDiff.to_dict()` is the JSON-facing shape for `diff-discovery`.
- `render_confirmed_discovery_blocks()` is the single path for auditable confirmed profile write-back.
