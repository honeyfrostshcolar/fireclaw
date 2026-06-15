from __future__ import annotations

from pathlib import Path

import pytest

from fireclaw_core.agent.robot import Ros1RobotAdapter
from fireclaw_core.ros.ros1_config import Ros1AdapterConfig, load_ros1_adapter_config
from fireclaw_core.ros.ros1_sensor_discovery import (
    Ros1SensorDiscovery,
    StaticRos1GraphProvider,
    StaticRos1MessageProbe,
)
from fireclaw_core.sensors.backends import StaticDeclaredDiscoveryBackend
from fireclaw_core.sensors.discovery import DiscoveryFingerprint


def _write_config(path: Path) -> None:
    path.write_text(
        """{
  "robot_id": "robot-ros1",
  "skill_remap": {
    "navigate_to_floor": {
      "interface": "action",
      "name": "/move_base",
      "type": "move_base_msgs/MoveBaseAction",
      "action": "navigate_to_floor",
      "payload": {"target_pose": {"header": {"frame_id": "map"}}}
    }
  }
}
""",
        encoding="utf-8",
    )


def test_ros1_robot_adapter_reports_unknown_state_as_none(tmp_path: Path) -> None:
    config_path = tmp_path / "ros1.json"
    _write_config(config_path)
    robot = Ros1RobotAdapter(
        config=load_ros1_adapter_config(config_path), transport=None
    )
    state = robot.get_robot_state()
    environment = robot.get_environment_state()
    assert state.supports_real_execution is True
    assert state.battery_percent is None
    assert state.available_sensors is None
    assert environment.reachable_floors is None


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


from fireclaw_core.sensors.health import SensorObservation


def test_ros1_adapter_state_includes_sensor_health_diagnostics() -> None:
    adapter = Ros1RobotAdapter(
        config=Ros1AdapterConfig(robot_id="robot-1"),
        sensor_discovery=Ros1SensorDiscovery(
            graph_provider=StaticRos1GraphProvider({"/camera/image_raw": "sensor_msgs/Image"}),
            message_probe=StaticRos1MessageProbe(
                {"/camera/image_raw": True},
                observations={
                    "/camera/image_raw": SensorObservation(
                        observed=True,
                        age_seconds=0.1,
                        payload_size=0,
                        frame_id="camera",
                    )
                },
            ),
        ),
    )

    state = adapter.get_robot_state()

    assert state.available_sensors == []
    assert state.sensor_diagnostics is not None
    finding = state.sensor_diagnostics["findings"][0]
    assert finding["sensor"] == "rgb_camera"
    assert finding["status"] == "degraded"
    assert finding["health_status"] == "invalid"
    assert finding["health_reason"] == "payload is empty"


def test_ros1_adapter_state_consumes_backend_protocol_not_ros1_class() -> None:
    adapter = Ros1RobotAdapter(
        config=Ros1AdapterConfig(robot_id="robot-1"),
        sensor_discovery=StaticDeclaredDiscoveryBackend(
            sensors=("rgb_camera", "lidar"),
            source="test_static",
            allow_real_mode=True,
        ),
    )

    state = adapter.get_robot_state()

    assert state.available_sensors == ["rgb_camera", "lidar"]
    assert state.sensor_diagnostics is not None
    assert state.sensor_diagnostics["source"] == "test_static"


def test_ros1_adapter_state_rejects_static_backend_for_real_mode() -> None:
    adapter = Ros1RobotAdapter(
        config=Ros1AdapterConfig(robot_id="robot-1"),
        sensor_discovery=StaticDeclaredDiscoveryBackend(
            sensors=("rgb_camera",),
            source="static",
            allow_real_mode=False,
        ),
    )

    with pytest.raises(ValueError, match="Static sensor discovery backend is not allowed for real mode"):
        adapter.get_robot_state()
