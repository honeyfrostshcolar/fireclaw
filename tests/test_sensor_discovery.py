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
