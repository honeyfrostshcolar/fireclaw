import json
from pathlib import Path

import pytest

from fireclaw_core.ros.ros1_config import load_ros1_adapter_config


def test_load_ros1_adapter_config_parses_action_and_service_endpoints(tmp_path):
    config_path = tmp_path / "ros1.json"
    config_path.write_text(
        json.dumps(
            {
                "robot_id": "fireclaw-01",
                "namespace": "/fireclaw/fireclaw-01",
                "endpoints": {
                    "navigate_to_floor": {
                        "interface": "action",
                        "name": "/fireclaw/fireclaw-01/navigation",
                        "type": "fireclaw_msgs/NavigateFloorAction",
                        "cancel_supported": True,
                        "feedback_supported": True,
                    },
                    "search_for_victims": {
                        "interface": "service",
                        "name": "/fireclaw/fireclaw-01/search_victims",
                        "type": "fireclaw_msgs/SearchVictims",
                    },
                },
                "emergency_stop": {
                    "interface": "service",
                    "name": "/fireclaw/fireclaw-01/emergency_stop",
                    "type": "std_srvs/Trigger",
                },
                "timeouts": {"default_seconds": 12.5},
            }
        ),
        encoding="utf-8",
    )

    config = load_ros1_adapter_config(config_path)

    assert config.robot_id == "fireclaw-01"
    assert config.namespace == "/fireclaw/fireclaw-01"
    assert config.endpoints["navigate_to_floor"].interface == "action"
    assert config.endpoints["navigate_to_floor"].cancel_supported is True
    assert config.endpoints["navigate_to_floor"].feedback_supported is True
    assert config.endpoints["search_for_victims"].interface == "service"
    assert config.emergency_stop.name == "/fireclaw/fireclaw-01/emergency_stop"
    assert config.timeouts.default_seconds == 12.5


def test_load_ros1_adapter_config_rejects_invalid_endpoint_interface(tmp_path):
    config_path = tmp_path / "ros1.json"
    config_path.write_text(
        json.dumps(
            {
                "robot_id": "fireclaw-01",
                "endpoints": {
                    "navigate_to_floor": {
                        "interface": "publisher",
                        "name": "/fireclaw/fireclaw-01/navigation",
                        "type": "fireclaw_msgs/NavigateFloorAction",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="navigate_to_floor.interface"):
        load_ros1_adapter_config(config_path)


def test_load_ros1_adapter_config_parses_yaml_remap_profiles_and_targets(tmp_path):
    config_path = tmp_path / "ros1.yaml"
    config_path.write_text(
        """
robot_id: fireclaw-01
namespace: /fireclaw/fireclaw-01
remap:
  navigate_to_floor:
    profile: move_base
    name: /move_base
    goal_template:
      target_pose:
        header:
          frame_id: map
        pose:
          position:
            x: "{{ targets.floor_${floor}.x }}"
            y: "{{ targets.floor_${floor}.y }}"
            z: 0.0
          orientation:
            yaw: "{{ targets.floor_${floor}.yaw }}"
  emergency_stop:
    profile: trigger_service
    name: /fireclaw/emergency_stop
targets:
  floor_2:
    frame_id: map
    x: 12.4
    y: -3.8
    yaw: 1.57
""".lstrip(),
        encoding="utf-8",
    )

    config = load_ros1_adapter_config(config_path)

    nav = config.endpoints["navigate_to_floor"]
    assert nav.profile == "move_base"
    assert nav.interface == "action"
    assert nav.name == "/move_base"
    assert nav.type == "move_base_msgs/MoveBaseAction"
    assert nav.cancel_supported is True
    assert nav.feedback_supported is True
    assert nav.goal_template["target_pose"]["header"]["frame_id"] == "map"
    assert config.emergency_stop.name == "/fireclaw/emergency_stop"
    assert config.emergency_stop.type == "std_srvs/Trigger"
    assert config.targets["floor_2"]["x"] == 12.4


def test_load_ros1_adapter_config_allows_custom_skill_remap(tmp_path):
    config_path = tmp_path / "ros1.yaml"
    config_path.write_text(
        """
robot_id: fireclaw-01
remap:
  spray_water:
    interface: service
    name: /fireclaw/fireclaw-01/spray_water
    type: fireclaw_msgs/SprayWater
    request_template:
      target_id: "{{ target_id }}"
      duration_seconds: "{{ duration_seconds }}"
""".lstrip(),
        encoding="utf-8",
    )

    config = load_ros1_adapter_config(config_path)

    spray = config.endpoints["spray_water"]
    assert spray.interface == "service"
    assert spray.name == "/fireclaw/fireclaw-01/spray_water"
    assert spray.type == "fireclaw_msgs/SprayWater"
    assert spray.request_template["target_id"] == "{{ target_id }}"


def test_load_ros1_adapter_config_parses_transport_settings(tmp_path):
    config_path = tmp_path / "ros1.yaml"
    config_path.write_text(
        """
robot_id: fireclaw-01
transport:
  enabled: true
  wait_for_server_seconds: 2.5
  wait_for_result_seconds: 7.0
remap:
  navigate_to_floor:
    profile: move_base
    name: /move_base
""".lstrip(),
        encoding="utf-8",
    )

    config = load_ros1_adapter_config(config_path)

    assert config.transport.enabled is True
    assert config.transport.wait_for_server_seconds == 2.5
    assert config.transport.wait_for_result_seconds == 7.0


def test_load_gazebo_turtlebot3_move_base_config():
    config_path = (
        Path(__file__).resolve().parent.parent
        / "examples"
        / "ros1_configs"
        / "gazebo_turtlebot3_move_base.yaml"
    )
    config = load_ros1_adapter_config(config_path)

    assert config.robot_id == "gazebo_turtlebot3"
    assert config.transport.enabled is True
    assert config.transport.wait_for_server_seconds == 10.0
    assert config.transport.wait_for_result_seconds == 120.0

    nav = config.endpoints["navigate_to_floor"]
    assert nav.profile == "move_base"
    assert nav.interface == "action"
    assert nav.name == "/move_base"
    assert nav.type == "move_base_msgs/MoveBaseAction"
    assert nav.cancel_supported is True
    assert nav.feedback_supported is True

    search = config.endpoints["search_for_victims"]
    assert search.interface == "topic"
    assert search.name == "/fireclaw/search_request"

    report = config.endpoints["report_status"]
    assert report.interface == "topic"
    assert report.name == "/fireclaw/operator_report"

    return_to = config.endpoints["return_to_safe_zone"]
    assert return_to.profile == "move_base"
    assert return_to.interface == "action"

    assert config.emergency_stop is not None
    assert config.emergency_stop.interface == "service"
    assert config.emergency_stop.name == "/fireclaw/emergency_stop"
    assert config.emergency_stop.type == "std_srvs/Trigger"

    assert "floor_1" in config.targets
    assert "floor_2" in config.targets
    assert "safe_zone" in config.targets
    assert config.targets["floor_1"]["position"]["x"] == 0.0
    assert config.targets["floor_2"]["position"]["x"] == 2.0
    assert config.targets["safe_zone"]["orientation"]["w"] == 1.0
