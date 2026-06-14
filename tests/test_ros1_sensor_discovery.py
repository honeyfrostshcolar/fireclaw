import subprocess

from fireclaw_core.ros.ros1_sensor_discovery import (
    Ros1CliMessageProbe,
    Ros1SensorDiscovery,
    StaticRos1GraphProvider,
    StaticRos1MessageProbe,
)
from fireclaw_core.sensors.discovery import (
    DiscoveryFingerprint,
    SensorMappingRule,
    fingerprint_topic_types,
)
from fireclaw_core.sensors.health import SensorObservation


def test_ros1_discovery_verifies_topic_when_recent_message_exists() -> None:
    discovery = Ros1SensorDiscovery(
        graph_provider=StaticRos1GraphProvider({"/scan": "sensor_msgs/LaserScan"}),
        message_probe=StaticRos1MessageProbe(
            {"/scan": True},
            observations={
                "/scan": SensorObservation(
                    observed=True,
                    age_seconds=0.1,
                    finite_range_count=360,
                    frame_id="laser",
                )
            },
        ),
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
    assert "no recent observation" in str(report.findings[0].reason)


def test_ros1_discovery_uses_profile_rule_for_nonstandard_camera_topic() -> None:
    discovery = Ros1SensorDiscovery(
        graph_provider=StaticRos1GraphProvider({"/front_camera/image_raw": "sensor_msgs/Image"}),
        message_probe=StaticRos1MessageProbe(
            {"/front_camera/image_raw": True},
            observations={
                "/front_camera/image_raw": SensorObservation(
                    observed=True,
                    age_seconds=0.1,
                    payload_size=128,
                    frame_id="front_cam",
                )
            },
        ),
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
        message_probe=StaticRos1MessageProbe(
            {
                "/scan": True,
                "/camera/image_raw": False,
                "/debug/image": True,
            },
            observations={
                "/scan": SensorObservation(
                    observed=True,
                    age_seconds=0.1,
                    finite_range_count=360,
                    frame_id="laser",
                ),
            },
        ),
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

    def observe(self, topic: str, sensor: str, timeout_seconds: float) -> SensorObservation:
        self.calls += 1
        return SensorObservation(
            observed=True,
            age_seconds=0.1,
            finite_range_count=360,
            frame_id="laser",
        )


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
        message_probe=StaticRos1MessageProbe(
            {"/scan": True},
            observations={
                "/scan": SensorObservation(
                    observed=True,
                    age_seconds=0.1,
                    finite_range_count=360,
                    frame_id="laser",
                )
            },
        ),
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


def test_ros1_discovery_marks_confirmed_rule_stale_when_fingerprint_missing() -> None:
    discovery = Ros1SensorDiscovery(
        graph_provider=StaticRos1GraphProvider({"/thermal/image_raw": "sensor_msgs/Image"}),
        message_probe=StaticRos1MessageProbe(
            {"/thermal/image_raw": True},
            observations={
                "/thermal/image_raw": SensorObservation(
                    observed=True,
                    age_seconds=0.1,
                    payload_size=64,
                    frame_id="thermal_cam",
                )
            },
        ),
        extra_rules=(
            SensorMappingRule(
                topic_pattern="/thermal/image_raw",
                message_type="sensor_msgs/Image",
                sensor="thermal_camera",
                source="profile",
                confidence=0.85,
                confirmed=True,
            ),
        ),
    )

    report = discovery.discover()
    payload = report.to_dict()

    assert payload["profile_fingerprint_status"] == "missing"
    assert report.verified_sensors() == ["thermal_camera"]
    assert payload["findings"][0]["sensor"] == "thermal_camera"
    assert payload["findings"][0]["confirmed"] is True
    assert payload["findings"][0]["confirmation_stale"] is True


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
