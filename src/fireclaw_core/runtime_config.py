from __future__ import annotations

from fireclaw_core.robot import DryRunRobotAdapter
from fireclaw_core.robot import MockRos1RobotAdapter
from fireclaw_core.robot import Ros1RobotAdapter
from fireclaw_core.robot import SimulatorRobotAdapter
from fireclaw_core.ros1_config import load_ros1_adapter_config


ADAPTER_CHOICES = ("dry-run", "simulator", "mock-ros1", "mock-ros2", "ros1")


def create_robot_adapter(adapter: str, robot_id: str, *, config_path: str | None = None):
    if adapter == "dry-run":
        return DryRunRobotAdapter(robot_id=robot_id)
    if adapter == "simulator":
        return SimulatorRobotAdapter(robot_id=robot_id)
    if adapter in {"mock-ros1", "mock-ros2"}:
        return MockRos1RobotAdapter(robot_id=robot_id)
    if adapter == "ros1":
        if config_path is None:
            raise ValueError("ros1 adapter requires config_path.")
        return Ros1RobotAdapter(config=load_ros1_adapter_config(config_path))
    raise ValueError(f"Unknown adapter: {adapter}")
