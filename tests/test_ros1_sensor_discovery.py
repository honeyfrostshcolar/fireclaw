from fireclaw_core.ros.ros1_sensor_discovery import (
    Ros1SensorDiscovery,
    StaticRos1GraphProvider,
    StaticRos1MessageProbe,
)
from fireclaw_core.sensors.discovery import (
    DiscoveryFingerprint,
    SensorMappingRule,
    fingerprint_topic_types,
)


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


class CountingGraphProvider:
    def __init__(self) -> None:
        self.calls = 0

    def topic_types(self) -> dict[str, str]:
        self.calls += 1
        return {"/scan": "sensor_msgs/LaserScan"}


class CountingMessageProbe:
    def __init__(self) -> None:
        self.calls = 0

    def has_recent_message(self, topic: str, timeout_seconds: float) -> bool:
        self.calls += 1
        return True


def test_ros1_discovery_uses_cache_within_ttl() -> None:
    graph = CountingGraphProvider()
    probe = CountingMessageProbe()
    discovery = Ros1SensorDiscovery(
        graph_provider=graph,
        message_probe=probe,
        cache_ttl_seconds=30.0,
    )

    first = discovery.discover()
    second = discovery.discover()

    assert first.verified_sensors() == ["lidar"]
    assert second.verified_sensors() == ["lidar"]
    assert graph.calls == 1
    assert probe.calls == 1


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
