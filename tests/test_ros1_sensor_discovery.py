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


class _FailingGraphProvider:
    def topic_types(self) -> dict[str, str]:
        raise RuntimeError("roscore not reachable")


def test_ros1_discovery_returns_degraded_when_graph_provider_fails() -> None:
    discovery = Ros1SensorDiscovery(
        graph_provider=_FailingGraphProvider(),  # type: ignore[arg-type]
        message_probe=StaticRos1MessageProbe({}),
    )

    report = discovery.discover()

    assert report.verified_sensors() == []
    assert len(report.findings) == 1
    assert report.findings[0].status == "degraded"
    assert "roscore not reachable" in str(report.findings[0].reason)


def test_ros1_discovery_handles_multiple_topics_with_mixed_statuses() -> None:
    discovery = Ros1SensorDiscovery(
        graph_provider=StaticRos1GraphProvider({
            "/scan": "sensor_msgs/LaserScan",
            "/camera/image_raw": "sensor_msgs/Image",
            "/debug/image": "custom_msgs/DebugImage",
        }),
        message_probe=StaticRos1MessageProbe({
            "/scan": True,
            "/camera/image_raw": False,
            "/debug/image": True,
        }),
    )

    report = discovery.discover()

    assert report.verified_sensors() == ["lidar"]
    statuses = {f.topic: f.status for f in report.findings}
    assert statuses["/scan"] == "verified"
    assert statuses["/camera/image_raw"] == "degraded"
    assert statuses["/debug/image"] == "rejected"
