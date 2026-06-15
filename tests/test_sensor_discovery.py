from fireclaw_core.sensors.discovery import (
    DEFAULT_SENSOR_MAPPING_RULES,
    DiscoveryFingerprint,
    SensorDiscoveryReport,
    SensorFinding,
    SensorMappingRule,
    compare_fingerprints,
    fingerprint_topic_types,
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


def test_sensor_mapping_rule_serializes_confirmation_audit_metadata() -> None:
    from fireclaw_core.sensors.discovery import SensorMappingRule

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
