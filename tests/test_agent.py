from __future__ import annotations

from pathlib import Path
from typing import Any

from fireclaw_core.agent.agent import FireClawAgent
from fireclaw_core.agent.robot import (
    DryRunRobotAdapter,
    MockRos2RobotAdapter,
    Ros1RobotAdapter,
    SimulatorRobotAdapter,
)
from fireclaw_core.memory.memory import JsonlMemoryStore
from fireclaw_core.planner.planner import Plan, PlanningResult, PlanStep
from fireclaw_core.ros.ros1_config import Ros1AdapterConfig
from fireclaw_core.ros.ros1_sensor_discovery import (
    Ros1SensorDiscovery,
    StaticRos1GraphProvider,
    StaticRos1MessageProbe,
)
from fireclaw_core.sensors.health import SensorObservation


EXTENSIONS = Path(__file__).resolve().parents[1] / "extensions"


def _agent(**kwargs: Any) -> FireClawAgent:
    robot = kwargs.get("robot")
    if isinstance(robot, SimulatorRobotAdapter):
        adapter = "simulator"
    elif isinstance(robot, MockRos2RobotAdapter):
        adapter = "mock-ros2"
    elif isinstance(robot, Ros1RobotAdapter):
        adapter = "ros1"
    else:
        adapter = "dry-run"
    kwargs.setdefault("extension_paths", (EXTENSIONS,))
    kwargs.setdefault("plugin_services", {"adapter": adapter})
    return FireClawAgent(**kwargs)


class FailingMemoryStore:
    def append(self, _record: dict[str, Any]) -> None:
        raise OSError("memory disk unavailable")


class FailingReadMemoryStore:
    def append(self, _record: dict[str, Any]) -> None:
        return None

    def latest_records(self, limit: int = 5) -> list[dict[str, Any]]:
        raise OSError("memory read unavailable")


class RecordingPlanner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def plan(self, command: str, context: Any = None) -> PlanningResult:
        self.calls.append({"command": command, "context": context})
        return PlanningResult(
            status="planned",
            message="Recorded planner result.",
            intent="point_navigation",
            plan=Plan(
                intent="point_navigation",
                steps=[
                    PlanStep(
                        "navigate_to_point",
                        {"x": 1.0, "y": 2.0, "yaw": 0.0, "frame_id": "map"},
                    )
                ],
            ),
        )


def test_agent_runs_plugin_owned_point_navigation_and_writes_memory(tmp_path: Path) -> None:
    memory_path = tmp_path / "memory.jsonl"
    agent = _agent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(memory_path),
    )

    result = agent.run("去坐标 (2.0, 1.5)")

    assert result["status"] == "succeeded"
    assert result["planning"]["target_floor"] is None
    assert result["planning"]["target_pose"] == {
        "x": 2.0,
        "y": 1.5,
        "yaw": 0.0,
        "frame_id": "map",
    }
    assert result["safety"]["status"] == "allow"
    assert [step["skill_name"] for step in result["execution"]["steps"]] == [
        "navigate_to_point"
    ]
    assert result["execution"]["steps"][0]["output"]["goal_reached"] is True
    owner = agent.plugin_host.get("physical_capability", "navigate_to_point")
    assert owner is not None
    assert owner.owner_plugin_id == "fireclaw.navigation.move-base"

    records = JsonlMemoryStore(memory_path).list_records()
    assert len(records) == 1
    assert records[0]["status"] == "succeeded"


def test_agent_records_robot_and_environment_state_snapshots(tmp_path: Path) -> None:
    memory_path = tmp_path / "memory.jsonl"
    agent = _agent(
        robot=SimulatorRobotAdapter(
            robot_id="sim-1",
            current_floor=1,
            reachable_floors=[1],
            victims_by_floor={1: 1},
        ),
        memory=JsonlMemoryStore(memory_path),
    )

    result = agent.run("去坐标 (2.0, 1.5)")

    assert result["status"] == "succeeded"
    assert result["robot_state"]["robot_id"] == "sim-1"
    assert result["robot_state"]["mode"] == "simulator"
    assert result["environment_state"]["reachable_floors"] == [1]
    record = JsonlMemoryStore(memory_path).list_records()[-1]
    assert record["robot_state"]["mode"] == "simulator"
    assert record["environment_state"]["victims_by_floor"] == {"1": 1}


def test_agent_records_session_metadata_and_turn_index(tmp_path: Path) -> None:
    agent = _agent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        session_id="session-a",
    )

    first = agent.run("随便看看")
    second = agent.run("还是随便看看")

    assert first["session"]["session_id"] == "session-a"
    assert first["session"]["turn_index"] == 1
    assert second["session"]["turn_index"] == 2


def test_agent_uses_injected_planner_with_plugin_catalog_context(tmp_path: Path) -> None:
    memory = JsonlMemoryStore(tmp_path / "memory.jsonl")
    memory.append(
        {
            "command": "历史任务",
            "status": "succeeded",
            "session": {"session_id": "session-a", "turn_index": 1},
        }
    )
    planner = RecordingPlanner()
    agent = _agent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=memory,
        session_id="session-a",
        planner=planner,
    )

    result = agent.run("自定义计划")

    assert result["status"] == "succeeded"
    assert result["execution"]["steps"][0]["skill_name"] == "navigate_to_point"
    context = planner.calls[0]["context"]
    assert context.session_id == "session-a"
    assert context.turn_index == 2
    assert context.recent_records[0]["command"] == "历史任务"
    assert [skill["name"] for skill in context.skills] == ["navigate_to_point"]


def test_agent_serializes_execution_attempt_history(tmp_path: Path) -> None:
    agent = _agent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
    )

    result = agent.run("去坐标 (2.0, 1.5)")

    first_step = result["execution"]["steps"][0]
    assert first_step["attempt_count"] == 1
    assert first_step["failure_category"] is None
    assert first_step["attempts"][0]["status"] == "succeeded"
    assert first_step["attempts"][0]["output"]["action"] == "navigate_to_point"


def test_agent_emits_plugin_owned_robot_action_events(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    agent = _agent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
        task_id="task-1",
    )

    result = agent.run("去坐标 (2.0, 1.5)")

    assert result["status"] == "succeeded"
    event_types = [event_type for event_type, _payload in events]
    assert {"action.requested", "action.started", "action.succeeded"} <= set(
        event_types
    )
    requested = [
        payload for event_type, payload in events if event_type == "action.requested"
    ][0]
    assert requested["task_id"] == "task-1"
    assert requested["skill_name"] == "navigate_to_point"
    assert requested["action_type"] == "navigate_to_point"


def test_agent_unknown_command_returns_clarification_without_execution(
    tmp_path: Path,
) -> None:
    agent = _agent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
    )

    result = agent.run("随便看看")

    assert result["status"] == "clarify"
    assert result["execution"] is None


def test_agent_floor_followup_still_requires_absolute_map_coordinates(
    tmp_path: Path,
) -> None:
    agent = _agent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        session_id="navigation-session",
    )

    first = agent.run("去一个地方")
    second = agent.run("二楼")

    assert first["status"] == "clarify"
    assert second["status"] == "clarify"
    assert second["execution"] is None
    assert "map" in second["message"]


def test_agent_resolves_point_after_previous_clarification(tmp_path: Path) -> None:
    agent = _agent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        session_id="point-navigation-session",
    )

    first = agent.run("去一个地方")
    second = agent.run("坐标 (2.0, 1.5)")

    assert first["status"] == "clarify"
    assert second["status"] == "succeeded"
    assert second["planning"]["target_pose"]["frame_id"] == "map"
    assert second["session"]["context_used"] is True


def test_agent_reports_memory_write_errors() -> None:
    agent = _agent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=FailingMemoryStore(),
    )

    result = agent.run("去坐标 (2.0, 1.5)")

    assert result["status"] == "succeeded"
    assert result["memory_error"] == "memory disk unavailable"


def test_agent_recalls_recent_tasks_without_executing(tmp_path: Path) -> None:
    memory = JsonlMemoryStore(tmp_path / "memory.jsonl")
    memory.append({"command": "任务一", "status": "succeeded"})
    memory.append({"command": "任务二", "status": "failed"})
    agent = _agent(robot=DryRunRobotAdapter(robot_id="robot-1"), memory=memory)

    result = agent.run("之前做过什么")

    assert result["status"] == "recalled"
    assert result["execution"] is None
    assert [item["command"] for item in result["memory"]["records"]] == [
        "任务一",
        "任务二",
    ]


def test_agent_retrieves_only_current_session_records(tmp_path: Path) -> None:
    memory = JsonlMemoryStore(tmp_path / "memory.jsonl")
    for session_id in ("session-a", "session-b"):
        memory.append(
            {
                "command": "去二楼救人",
                "status": "succeeded",
                "session": {"session_id": session_id, "turn_index": 1},
                "planning": {"intent": "rescue_victim", "target_floor": 2},
            }
        )
    agent = _agent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=memory,
        session_id="session-a",
    )

    result = agent.run("之前二楼救人成功了吗")

    assert result["status"] == "retrieved"
    assert result["execution"] is None
    assert [
        record["session"]["session_id"] for record in result["memory"]["records"]
    ] == ["session-a"]


def test_agent_reports_memory_read_errors_for_recall_command() -> None:
    agent = _agent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=FailingReadMemoryStore(),
    )

    result = agent.run("回忆之前任务")

    assert result["status"] == "failed"
    assert result["execution"] is None
    assert result["memory_error"] == "memory read unavailable"


def test_agent_lists_only_manifest_loaded_physical_tools(tmp_path: Path) -> None:
    agent = _agent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
    )

    result = agent.run("你有哪些技能")

    assert result["status"] == "skills"
    assert result["execution"] is None
    assert [skill["name"] for skill in result["skills"]] == ["navigate_to_point"]
    assert result["skills"][0]["metadata"]["plugin_id"] == (
        "fireclaw.navigation.move-base"
    )
    assert "skill_load_errors" not in result


def test_agent_blocks_missing_direct_tool_before_execution(tmp_path: Path) -> None:
    memory_path = tmp_path / "memory.jsonl"
    agent = _agent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(memory_path),
    )

    result = agent.run("运行 missing_tool")

    assert result["status"] == "block"
    assert result["execution"] is None
    assert "Missing skill: missing_tool" in result["message"]
    assert JsonlMemoryStore(memory_path).list_records()[-1]["status"] == "block"


def test_agent_confirm_without_pending_plan_requests_clarification(
    tmp_path: Path,
) -> None:
    agent = _agent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        session_id="session-a",
    )

    result = agent.run("确认执行")

    assert result["status"] == "clarify"
    assert result["execution"] is None


def test_agent_runs_point_navigation_with_mock_ros2_state_adapter(
    tmp_path: Path,
) -> None:
    robot = MockRos2RobotAdapter(robot_id="robot-ros2")
    agent = _agent(
        robot=robot,
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        dry_run=True,
    )

    result = agent.run("去坐标 (2.0, 1.5)")

    assert result["status"] == "succeeded"
    assert result["execution"]["steps"][0]["output"]["mode"] == "mock_ros2"
    assert result["execution"]["steps"][0]["output"]["goal_reached"] is True
    assert not hasattr(robot, "navigate_to_point")


def test_agent_forwards_executor_live_events(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    agent = _agent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    result = agent.run("去坐标 (2.0, 1.5)")

    assert result["status"] == "succeeded"
    event_types = [event_type for event_type, _payload in events]
    assert event_types[:5] == [
        "task.planned",
        "safety.decided",
        "capability.policy_preflight",
        "capability.policy_decided",
        "skill.started",
    ]
    assert event_types.count("skill.started") == 1
    assert event_types.count("skill.succeeded") == 1


def test_agent_uses_robot_state_verified_sensors_without_domain_endpoints(
    tmp_path: Path,
) -> None:
    robot = Ros1RobotAdapter(
        config=Ros1AdapterConfig(robot_id="robot-1"),
        sensor_discovery=Ros1SensorDiscovery(
            graph_provider=StaticRos1GraphProvider(
                {
                    "/camera/image_raw": "sensor_msgs/Image",
                    "/thermal/image_raw": "sensor_msgs/Image",
                    "/scan": "sensor_msgs/LaserScan",
                }
            ),
            message_probe=StaticRos1MessageProbe(
                {
                    "/camera/image_raw": True,
                    "/thermal/image_raw": True,
                    "/scan": True,
                }
            ),
        ),
        dry_run=True,
    )
    agent = _agent(
        robot=robot,
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        dry_run=True,
    )

    result = agent.run("随便看看")

    assert result["status"] == "clarify"
    assert sorted(result["robot_state"]["available_sensors"]) == [
        "lidar",
        "rgb_camera",
        "thermal_camera",
    ]


def test_agent_excludes_unhealthy_camera_from_verified_sensor_state(
    tmp_path: Path,
) -> None:
    robot = Ros1RobotAdapter(
        config=Ros1AdapterConfig(robot_id="robot-1"),
        sensor_discovery=Ros1SensorDiscovery(
            graph_provider=StaticRos1GraphProvider(
                {
                    "/camera/image_raw": "sensor_msgs/Image",
                    "/scan": "sensor_msgs/LaserScan",
                }
            ),
            message_probe=StaticRos1MessageProbe(
                {"/camera/image_raw": True, "/scan": True},
                observations={
                    "/camera/image_raw": SensorObservation(
                        observed=True,
                        age_seconds=0.1,
                        payload_size=0,
                        frame_id="camera",
                    ),
                    "/scan": SensorObservation(
                        observed=True,
                        age_seconds=0.1,
                        finite_range_count=100,
                        frame_id="lidar",
                    ),
                },
            ),
        ),
        dry_run=True,
    )
    agent = _agent(
        robot=robot,
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        dry_run=True,
    )

    result = agent.run("随便看看")

    assert result["status"] == "clarify"
    assert result["robot_state"]["available_sensors"] == ["lidar"]
