import json

from fireclaw_core.robot import (
    DryRunRobotAdapter,
    MockRos1RobotAdapter,
    MockRos2RobotAdapter,
    Ros1RobotAdapter,
    SimulatorRobotAdapter,
)
from fireclaw_core.runtime_config import create_robot_adapter
from fireclaw_core.ros1_transport import Ros1Transport


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
    assert robot.commands[0].feedback_supported is True
    assert robot.action_feedback("navigate_to_floor", {"floor": 3}) == [
        {
            "progress": 0.25,
            "message": "leaving safe zone",
            "current_floor": 1,
            "target_floor": 3,
        },
        {
            "progress": 0.75,
            "message": "near target floor",
            "current_floor": 1,
            "target_floor": 3,
        },
    ]
    assert robot.action_feedback("report_status", {"floor": 3}) == []


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


def test_mock_ros1_robot_adapter_records_emergency_stop_without_ros_dependency():
    robot = MockRos1RobotAdapter(robot_id="robot-ros1")

    result = robot.emergency_stop(reason="operator hit e-stop")
    state = robot.get_robot_state()

    assert result.ok is True
    assert result.status == "emergency_stopped"
    assert result.action == "emergency_stop"
    assert result.mode == "mock_ros1"
    assert result.data["reason"] == "operator hit e-stop"
    assert result.data["emergency_stopped"] is True
    assert robot.emergency_stopped is True
    assert robot.emergency_stop_reason == "operator hit e-stop"
    assert state.online is False


def test_runtime_config_creates_mock_ros1_adapter_and_keeps_mock_ros2_alias():
    ros1 = create_robot_adapter("mock-ros1", "robot-ros1")
    legacy = create_robot_adapter("mock-ros2", "robot-legacy")

    assert isinstance(ros1, MockRos1RobotAdapter)
    assert ros1.mode == "mock_ros1"
    assert legacy.mode == "mock_ros1"


def test_runtime_config_creates_ros1_adapter_from_config_without_ros_dependency(tmp_path):
    config_path = tmp_path / "ros1.json"
    config_path.write_text(
        json.dumps(
            {
                "robot_id": "robot-config",
                "namespace": "/fireclaw/robot-config",
                "endpoints": {
                    "navigate_to_floor": {
                        "interface": "action",
                        "name": "/fireclaw/robot-config/navigation",
                        "type": "fireclaw_msgs/NavigateFloorAction",
                        "cancel_supported": True,
                        "feedback_supported": True,
                    }
                },
                "emergency_stop": {
                    "interface": "service",
                    "name": "/fireclaw/robot-config/emergency_stop",
                    "type": "std_srvs/Trigger",
                },
            }
        ),
        encoding="utf-8",
    )

    robot = create_robot_adapter("ros1", "robot-cli", config_path=str(config_path))

    assert isinstance(robot, Ros1RobotAdapter)
    assert robot.robot_id == "robot-config"
    assert robot.mode == "ros1"
    assert robot.dry_run is False


def test_ros1_robot_adapter_records_configured_endpoint_but_refuses_live_execution(tmp_path):
    config_path = tmp_path / "ros1.json"
    config_path.write_text(
        json.dumps(
            {
                "robot_id": "robot-ros1-real",
                "endpoints": {
                    "navigate_to_floor": {
                        "interface": "action",
                        "name": "/fireclaw/robot-ros1-real/navigation",
                        "type": "fireclaw_msgs/NavigateFloorAction",
                        "cancel_supported": True,
                        "feedback_supported": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    robot = create_robot_adapter("ros1", "ignored", config_path=str(config_path))

    result = robot.navigate_to_floor(2)

    assert result.ok is False
    assert result.status == "not_configured"
    assert result.mode == "ros1"
    assert result.dry_run is False
    assert result.data["ros1_interface"] == "action"
    assert result.data["ros1_name"] == "/fireclaw/robot-ros1-real/navigation"
    assert result.data["ros1_type"] == "fireclaw_msgs/NavigateFloorAction"
    assert "disabled" in str(result.error).lower()
    assert "not implemented" not in str(result.error).lower()
    assert robot.commands[0].name == "/fireclaw/robot-ros1-real/navigation"
    assert robot.commands[0].feedback_supported is True


def test_ros1_robot_adapter_result_includes_remap_template_metadata(tmp_path):
    config_path = tmp_path / "ros1.yaml"
    config_path.write_text(
        """
robot_id: robot-ros1-real
remap:
  navigate_to_floor:
    profile: move_base
    name: /move_base
    goal_template:
      target_pose:
        header:
          frame_id: map
targets:
  floor_2:
    frame_id: map
    x: 12.4
    y: -3.8
    yaw: 1.57
""".lstrip(),
        encoding="utf-8",
    )
    robot = create_robot_adapter("ros1", "ignored", config_path=str(config_path))

    result = robot.navigate_to_floor(2)

    assert result.data["ros1_profile"] == "move_base"
    assert result.data["goal_template"]["target_pose"]["header"]["frame_id"] == "map"
    assert result.data["targets"]["floor_2"]["x"] == 12.4


class _FakeRos1Module:
    def __init__(self):
        self.action_goals = []

    def create_action_client(self, name, type_name):
        self.action_args = (name, type_name)
        return self

    def wait_for_server(self, timeout=None):
        self.server_timeout = timeout
        return True

    def send_goal(self, goal, feedback_cb=None):
        self.action_goals.append(goal)
        if feedback_cb is not None:
            feedback_cb({"progress": 0.5, "message": "halfway"})

    def wait_for_result(self, timeout=None):
        self.result_timeout = timeout
        return True

    def get_result(self):
        return {"arrived": True}

    def duration(self, seconds):
        return seconds


class _CancellableFakeRos1Module(_FakeRos1Module):
    def __init__(self):
        super().__init__()
        self.cancelled = False

    def wait_for_result(self, timeout=None):
        self.result_timeout = timeout
        return False

    def cancel_goal(self):
        self.cancelled = True


def test_ros1_robot_adapter_executes_transport_enabled_action_with_rendered_goal(tmp_path):
    config_path = tmp_path / "ros1.yaml"
    config_path.write_text(
        """
robot_id: robot-ros1-real
transport:
  enabled: true
  wait_for_server_seconds: 2.0
  wait_for_result_seconds: 3.0
remap:
  navigate_to_floor:
    profile: move_base
    name: /move_base
    goal_template:
      target_pose:
        header:
          frame_id: "{{ targets.floor_${floor}.frame_id }}"
        pose:
          position:
            x: "{{ targets.floor_${floor}.x }}"
            y: "{{ targets.floor_${floor}.y }}"
targets:
  floor_2:
    frame_id: map
    x: 12.4
    y: -3.8
""".lstrip(),
        encoding="utf-8",
    )
    fake = _FakeRos1Module()
    feedback = []
    robot = create_robot_adapter(
        "ros1",
        "ignored",
        config_path=str(config_path),
        ros1_transport=Ros1Transport(module=fake, feedback_sink=feedback.append),
    )

    result = robot.navigate_to_floor(2)

    assert result.ok is True
    assert result.status == "succeeded"
    assert result.data["ros1_payload"]["target_pose"]["header"]["frame_id"] == "map"
    assert result.data["ros1_payload"]["target_pose"]["pose"]["position"]["x"] == 12.4
    assert result.data["ros1_response"] == {"arrived": True}
    assert fake.action_goals == [result.data["ros1_payload"]]
    assert feedback == [{"progress": 0.5, "message": "halfway"}]


def test_ros1_robot_adapter_cancels_transport_enabled_action(tmp_path):
    config_path = tmp_path / "ros1.yaml"
    config_path.write_text(
        """
robot_id: robot-ros1-real
transport:
  enabled: true
  wait_for_result_seconds: 5.0
remap:
  navigate_to_floor:
    profile: move_base
    name: /move_base
    goal_template:
      floor: "{{ floor }}"
""".lstrip(),
        encoding="utf-8",
    )
    fake = _CancellableFakeRos1Module()
    robot = create_robot_adapter(
        "ros1",
        "ignored",
        config_path=str(config_path),
        ros1_transport=Ros1Transport(module=fake),
    )
    checks = iter([False, True])

    result = robot.navigate_to_floor(2, cancellation_requested=lambda: next(checks, True))

    assert result.ok is False
    assert result.status == "cancelled"
    assert fake.cancelled is True


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
