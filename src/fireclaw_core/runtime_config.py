from __future__ import annotations

from fireclaw_core.robot import DryRunRobotAdapter, MockRos1RobotAdapter, SimulatorRobotAdapter


ADAPTER_CHOICES = ("dry-run", "simulator", "mock-ros1", "mock-ros2")


def create_robot_adapter(adapter: str, robot_id: str):
    if adapter == "dry-run":
        return DryRunRobotAdapter(robot_id=robot_id)
    if adapter == "simulator":
        return SimulatorRobotAdapter(robot_id=robot_id)
    if adapter in {"mock-ros1", "mock-ros2"}:
        return MockRos1RobotAdapter(robot_id=robot_id)
    raise ValueError(f"Unknown adapter: {adapter}")
