from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from fireclaw_core.agent.robot import Ros1RobotAdapter
from fireclaw_core.ros.ros1_config import Ros1AdapterConfig, load_ros1_adapter_config
from fireclaw_core.ros.ros1_runtime import Ros1RuntimeLifecycle
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
    "navigate_to_waypoint": {
      "interface": "action",
      "name": "/move_base",
      "type": "move_base_msgs/MoveBaseAction",
      "action": "navigate_to_waypoint",
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
    assert state.pose is None
    assert environment.reachable_floors is None


def test_ros1_state_probe_does_not_initialize_ros_node(monkeypatch) -> None:
    init_calls: list[str] = []
    fake_rospy = SimpleNamespace(
        core=SimpleNamespace(is_initialized=lambda: False),
        init_node=lambda *args, **kwargs: init_calls.append("init_node"),
    )
    real_import_module = importlib.import_module

    def fake_import_module(name: str):
        if name == "rospy":
            return fake_rospy
        return real_import_module(name)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    adapter = Ros1RobotAdapter(config=Ros1AdapterConfig(robot_id="robot-1"))

    state = adapter.get_robot_state()

    assert state.pose is None
    assert init_calls == []


def test_ros1_runtime_start_makes_pose_available_before_first_task(
    monkeypatch,
) -> None:
    initialized = {"value": False}
    shutdown_calls: list[str] = []
    fake_rospy = SimpleNamespace(
        core=SimpleNamespace(
            is_initialized=lambda: initialized["value"],
        ),
        init_node=lambda *args, **kwargs: initialized.__setitem__(
            "value",
            True,
        ),
        signal_shutdown=lambda reason: shutdown_calls.append(reason),
        Time=lambda value=0: value,
        Duration=lambda seconds: seconds,
    )
    transform = SimpleNamespace(
        header=SimpleNamespace(frame_id="map"),
        transform=SimpleNamespace(
            translation=SimpleNamespace(x=-1.946, y=-0.502),
            rotation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
        ),
    )

    class FakeBuffer:
        def lookup_transform(self, target, source, stamp, timeout):
            assert target == "map"
            assert source == "base_footprint"
            return transform

    fake_tf2 = SimpleNamespace(
        Buffer=FakeBuffer,
        TransformListener=lambda buffer: SimpleNamespace(buffer=buffer),
    )
    real_import_module = importlib.import_module

    def fake_import_module(name: str):
        if name == "rospy":
            return fake_rospy
        if name == "tf2_ros":
            return fake_tf2
        return real_import_module(name)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    adapter = Ros1RobotAdapter(
        config=Ros1AdapterConfig(robot_id="robot-1"),
        runtime_lifecycle=Ros1RuntimeLifecycle(
            startup_timeout_seconds=0.2,
            master_probe=lambda uri, timeout: (True, None),
            rospy_loader=lambda: fake_rospy,
        ),
        pose_lookup_timeout_seconds=0.05,
        pose_startup_timeout_seconds=0.1,
    )

    runtime_state = adapter.start_runtime()
    robot_state = adapter.get_robot_state()

    assert runtime_state["status"] == "ready"
    assert runtime_state["pose_status"] == "ready"
    assert robot_state.online is True
    assert robot_state.pose == {
        "x": -1.946,
        "y": -0.502,
        "yaw": 0.0,
        "frame_id": "map",
    }

    adapter.stop_runtime()
    assert len(shutdown_calls) == 1


def test_ros1_runtime_failure_marks_real_adapter_offline() -> None:
    fake_rospy = SimpleNamespace(
        core=SimpleNamespace(is_initialized=lambda: False),
    )
    adapter = Ros1RobotAdapter(
        config=Ros1AdapterConfig(robot_id="robot-1"),
        runtime_lifecycle=Ros1RuntimeLifecycle(
            startup_timeout_seconds=0.2,
            master_probe=lambda uri, timeout: (False, "connection refused"),
            rospy_loader=lambda: fake_rospy,
        ),
        pose_startup_timeout_seconds=0.0,
    )

    runtime_state = adapter.start_runtime()
    robot_state = adapter.get_robot_state()

    assert runtime_state["status"] == "degraded"
    assert runtime_state["reason_code"] == "ros_master_unavailable"
    assert robot_state.online is False
    assert robot_state.pose is None


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
