from __future__ import annotations

from pathlib import Path

from fireclaw_core.robot import Ros1RobotAdapter
from fireclaw_core.ros1_config import load_ros1_adapter_config


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
