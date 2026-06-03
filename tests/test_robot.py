from fireclaw_core.robot import DryRunRobotAdapter, MockRos1RobotAdapter, MockRos2RobotAdapter, SimulatorRobotAdapter
from fireclaw_core.runtime_config import create_robot_adapter


def test_dry_run_robot_adapter_returns_structured_success_result():
    robot = DryRunRobotAdapter(robot_id="robot-1")

    result = robot.navigate_to_floor(2)

    assert robot.mode == "dry_run"
    assert result.ok is True
    assert result.status == "succeeded"
    assert result.robot_id == "robot-1"
    assert result.mode == "dry_run"
    assert result.action == "navigate_to_floor"
    assert result.dry_run is True
    assert result.data["floor"] == 2
    assert result.error is None
    assert isinstance(result.timestamp, str)
    assert robot.actions == [{"action": "navigate_to_floor", "floor": 2, "dry_run": True}]


def test_dry_run_robot_adapter_returns_structured_failure_result():
    robot = DryRunRobotAdapter(robot_id="robot-1", fail_actions={"search_for_victims"})

    result = robot.search_for_victims(2)

    assert result.ok is False
    assert result.status == "failed"
    assert result.robot_id == "robot-1"
    assert result.mode == "dry_run"
    assert result.action == "search_for_victims"
    assert result.dry_run is True
    assert result.data["floor"] == 2
    assert "search_for_victims" in result.error


def test_mock_ros2_robot_adapter_records_ros_like_commands_without_ros_dependency():
    robot = MockRos2RobotAdapter(robot_id="robot-ros2")

    result = robot.navigate_to_floor(3)

    assert robot.mode == "mock_ros2"
    assert result.ok is True
    assert result.status == "succeeded"
    assert result.robot_id == "robot-ros2"
    assert result.mode == "mock_ros2"
    assert result.action == "navigate_to_floor"
    assert result.dry_run is True
    assert result.data["floor"] == 3
    assert result.data["topic"] == "/fireclaw/robot-ros2/navigation"
    assert robot.commands == [
        {
            "topic": "/fireclaw/robot-ros2/navigation",
            "action": "navigate_to_floor",
            "payload": {"floor": 3},
            "dry_run": True,
        }
    ]


def test_mock_ros1_robot_adapter_records_ros1_command_specs_without_ros_dependency():
    robot = MockRos1RobotAdapter(robot_id="robot-ros1")

    result = robot.navigate_to_floor(3)

    assert robot.mode == "mock_ros1"
    assert result.ok is True
    assert result.status == "succeeded"
    assert result.robot_id == "robot-ros1"
    assert result.mode == "mock_ros1"
    assert result.action == "navigate_to_floor"
    assert result.dry_run is True
    assert result.data["floor"] == 3
    assert result.data["ros1_interface"] == "topic"
    assert result.data["ros1_name"] == "/fireclaw/robot-ros1/navigation"
    assert robot.commands[0].interface == "topic"
    assert robot.commands[0].name == "/fireclaw/robot-ros1/navigation"
    assert robot.commands[0].action == "navigate_to_floor"
    assert robot.commands[0].payload == {"floor": 3}
    assert robot.commands[0].cancel_supported is True
    assert robot.commands[0].feedback_supported is False


def test_mock_ros2_robot_adapter_implements_rescue_action_methods():
    robot = MockRos2RobotAdapter(robot_id="robot-ros2")

    assert robot.search_for_victims(2).mode == "mock_ros2"
    assert robot.assess_victim(2).mode == "mock_ros2"
    assert robot.report_status(2).mode == "mock_ros2"
    assert robot.return_to_safe_zone().mode == "mock_ros2"
    assert len(robot.commands) == 4


def test_dry_run_adapter_exposes_robot_and_environment_state():
    robot = DryRunRobotAdapter(robot_id="robot-1")

    robot_state = robot.get_robot_state()
    environment_state = robot.get_environment_state()

    assert robot_state.robot_id == "robot-1"
    assert robot_state.mode == "dry_run"
    assert robot_state.dry_run is True
    assert robot_state.online is True
    assert robot_state.current_floor == 1
    assert robot_state.supports_real_execution is False
    assert environment_state.reachable_floors == [1, 2, 3]
    assert environment_state.victims_by_floor == {2: 1}


def test_mock_ros2_adapter_exposes_state_without_ros_dependency():
    robot = MockRos2RobotAdapter(robot_id="robot-ros2")

    robot_state = robot.get_robot_state()

    assert robot_state.robot_id == "robot-ros2"
    assert robot_state.mode == "mock_ros2"
    assert robot_state.dry_run is True
    assert robot_state.supports_real_execution is False


def test_mock_ros1_adapter_exposes_state_without_ros_dependency():
    robot = MockRos1RobotAdapter(robot_id="robot-ros1")

    robot_state = robot.get_robot_state()

    assert robot_state.robot_id == "robot-ros1"
    assert robot_state.mode == "mock_ros1"
    assert robot_state.dry_run is True
    assert robot_state.supports_real_execution is False


def test_runtime_config_creates_mock_ros1_adapter_and_keeps_mock_ros2_alias():
    ros1 = create_robot_adapter("mock-ros1", "robot-ros1")
    legacy = create_robot_adapter("mock-ros2", "robot-legacy")

    assert isinstance(ros1, MockRos1RobotAdapter)
    assert ros1.mode == "mock_ros1"
    assert legacy.mode == "mock_ros1"


def test_simulator_adapter_updates_floor_and_reports_victims():
    robot = SimulatorRobotAdapter(
        robot_id="sim-1",
        current_floor=1,
        reachable_floors=[1, 2, 4],
        victims_by_floor={2: 2},
    )

    nav = robot.navigate_to_floor(2)
    search = robot.search_for_victims(2)
    state = robot.get_robot_state()

    assert robot.mode == "simulator"
    assert nav.ok is True
    assert nav.mode == "simulator"
    assert nav.data["from_floor"] == 1
    assert nav.data["floor"] == 2
    assert search.data["victims_found"] == 2
    assert state.current_floor == 2


def test_simulator_adapter_fails_unreachable_floor_without_state_change():
    robot = SimulatorRobotAdapter(
        robot_id="sim-1",
        current_floor=1,
        reachable_floors=[1, 2],
    )

    result = robot.navigate_to_floor(5)

    assert result.ok is False
    assert result.status == "failed"
    assert result.data["floor"] == 5
    assert "unreachable" in str(result.error)
    assert robot.get_robot_state().current_floor == 1
