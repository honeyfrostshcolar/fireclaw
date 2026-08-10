from __future__ import annotations

import json
from pathlib import Path

import pytest

from fireclaw_core.agent.robot import (
    DryRunRobotAdapter,
    MockRos1RobotAdapter,
    MockRos2RobotAdapter,
    Ros1RobotAdapter,
    SimulatorRobotAdapter,
)
from fireclaw_core.execution.runtime_config import create_robot_adapter
from fireclaw_core.ros.ros1_config import load_ros1_adapter_config


@pytest.mark.parametrize(
    "robot",
    [
        DryRunRobotAdapter(robot_id="dry-run"),
        MockRos1RobotAdapter(robot_id="mock-ros1"),
        MockRos2RobotAdapter(robot_id="mock-ros2"),
        SimulatorRobotAdapter(robot_id="simulator"),
    ],
)
def test_robot_adapters_expose_state_but_no_domain_action_methods(robot) -> None:
    state = robot.get_robot_state()

    assert state.robot_id == robot.robot_id
    assert state.online is True
    assert state.dry_run is True
    assert not hasattr(robot, "navigate_to_point")
    assert not hasattr(robot, "navigate_to_waypoint")
    assert not hasattr(robot, "victim_search")


def test_dry_run_adapter_exposes_environment_state() -> None:
    robot = DryRunRobotAdapter(robot_id="robot-1")

    environment = robot.get_environment_state()

    assert environment.reachable_floors == [1]
    assert environment.victims_by_floor == {1: 1}


def test_mock_ros1_adapter_records_emergency_stop_without_ros() -> None:
    robot = MockRos1RobotAdapter(robot_id="robot-ros1")

    result = robot.emergency_stop(reason="operator hit e-stop")

    assert result.ok is True
    assert result.status == "emergency_stopped"
    assert result.action == "emergency_stop"
    assert result.data["reason"] == "operator hit e-stop"
    assert robot.get_robot_state().online is False


def test_runtime_config_creates_mock_state_adapters() -> None:
    ros1 = create_robot_adapter("mock-ros1", "robot-ros1")
    ros2_alias = create_robot_adapter("mock-ros2", "robot-ros2")

    assert isinstance(ros1, MockRos1RobotAdapter)
    assert ros1.mode == "mock_ros1"
    assert isinstance(ros2_alias, MockRos1RobotAdapter)
    assert ros2_alias.mode == "mock_ros1"


def test_runtime_config_creates_transport_only_ros1_adapter(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "ros1.json"
    config_path.write_text(
        json.dumps(
            {
                "robot_id": "robot-config",
                "namespace": "/fireclaw/robot-config",
                "emergency_stop": {
                    "interface": "service",
                    "name": "/fireclaw/robot-config/emergency_stop",
                    "type": "std_srvs/Trigger",
                },
            }
        ),
        encoding="utf-8",
    )

    robot = create_robot_adapter(
        "ros1",
        "ignored",
        config_path=str(config_path),
    )

    assert isinstance(robot, Ros1RobotAdapter)
    assert robot.robot_id == "robot-config"
    assert robot.mode == "ros1"
    assert robot.dry_run is False
    assert robot.get_robot_state().supports_real_execution is True
    assert not hasattr(robot, "navigate_to_point")


@pytest.mark.parametrize("field", ["endpoints", "remap", "targets"])
def test_ros1_core_config_rejects_plugin_owned_domain_configuration(
    tmp_path: Path,
    field: str,
) -> None:
    config_path = tmp_path / "ros1.json"
    config_path.write_text(
        json.dumps({"robot_id": "robot-1", field: {"anything": {}}}),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="configure runtime endpoints in the owning Plugin",
    ):
        load_ros1_adapter_config(config_path)


def test_simulator_adapter_keeps_state_observation_separate_from_motion() -> None:
    robot = SimulatorRobotAdapter(
        robot_id="sim-1",
        current_floor=1,
        reachable_floors=[1],
        victims_by_floor={1: 2},
    )

    state = robot.get_robot_state()
    environment = robot.get_environment_state()

    assert state.mode == "simulator"
    assert state.current_floor == 1
    assert environment.victims_by_floor == {1: 2}
    assert not hasattr(robot, "navigate_to_point")
