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
