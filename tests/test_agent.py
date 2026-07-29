from pathlib import Path
import json
import sys

from fireclaw_core.agent.agent import FireClawAgent
from fireclaw_core.memory.memory import JsonlMemoryStore
from fireclaw_core.planner.planner import Plan, PlanningResult, PlanStep
from fireclaw_core.agent.robot import DryRunRobotAdapter, MockRos2RobotAdapter, Ros1RobotAdapter, SimulatorRobotAdapter
from fireclaw_core.ros.ros1_config import Ros1AdapterConfig, Ros1EndpointConfig
from fireclaw_core.ros.ros1_sensor_discovery import (
    Ros1SensorDiscovery,
    StaticRos1GraphProvider,
    StaticRos1MessageProbe,
)
from fireclaw_core.sensors.health import SensorObservation


class FailingMemoryStore:
    def append(self, record):
        raise OSError("memory disk unavailable")


class FailingReadMemoryStore:
    def append(self, record):
        pass

    def latest_records(self, limit=5):
        raise OSError("memory read unavailable")


class RecordingPlanner:
    def __init__(self):
        self.calls = []

    def plan(self, command, context=None):
        self.calls.append({"command": command, "context": context})
        return PlanningResult(
            status="planned",
            message="Recorded planner result.",
            intent="direct_skill_invocation",
            plan=Plan(
                intent="direct_skill_invocation",
                steps=[PlanStep("return_to_safe_zone", {})],
            ),
        )


def _write_high_risk_workspace_skill(skills_dir: Path, name: str = "smoke_entry") -> None:
    skills_dir.mkdir()
    (skills_dir / f"{name}.skill.json").write_text(
        json.dumps(
            {
                "name": name,
                "description": "High-risk dry-run skill.",
                "runtime": "subprocess",
                "command": [
                    sys.executable,
                    "-c",
                    "import json; print(json.dumps({'ok': True, 'data': {'confirmed': True}}))",
                ],
                "dry_run_only": True,
                "risk_level": "high",
            }
        ),
        encoding="utf-8",
    )


def test_agent_runs_full_rescue_flow_and_writes_memory(tmp_path):
    memory_path = tmp_path / "memory.jsonl"
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(memory_path),
    )

    result = agent.run("去坐标 (2.0, 1.5) 救人")

    assert result["status"] == "succeeded"
    assert result["planning"]["target_floor"] is None
    assert result["planning"]["target_pose"]["x"] == 2.0
    assert result["planning"]["target_pose"]["y"] == 1.5
    assert result["safety"]["status"] == "allow"
    assert [step["skill_name"] for step in result["execution"]["steps"]] == [
        "navigate_to_point",
        "search_for_victims",
        "assess_victim",
        "report_status",
        "return_to_safe_zone",
    ]

    records = JsonlMemoryStore(memory_path).list_records()
    assert len(records) == 1
    assert records[0]["command"] == "去坐标 (2.0, 1.5) 救人"
    assert records[0]["status"] == "succeeded"


def test_agent_records_robot_and_environment_state_snapshots(tmp_path):
    memory_path = tmp_path / "memory.jsonl"
    agent = FireClawAgent(
        robot=SimulatorRobotAdapter(
            robot_id="sim-1",
            current_floor=1,
            reachable_floors=[1, 2, 3],
            victims_by_floor={2: 1},
        ),
        memory=JsonlMemoryStore(memory_path),
    )

    result = agent.run("去坐标 (2.0, 1.5) 救人")

    assert result["status"] == "succeeded"
    assert result["robot_state"]["robot_id"] == "sim-1"
    assert result["robot_state"]["mode"] == "simulator"
    assert result["robot_state"]["current_floor"] == 1
    assert result["environment_state"]["reachable_floors"] == [1, 2, 3]
    records = JsonlMemoryStore(memory_path).list_records()
    assert records[-1]["robot_state"]["mode"] == "simulator"
    assert records[-1]["environment_state"]["victims_by_floor"] == {"2": 1}


def test_agent_records_session_metadata_and_turn_index(tmp_path):
    memory_path = tmp_path / "memory.jsonl"
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(memory_path),
        session_id="session-a",
    )

    first = agent.run("去一楼救人")
    second = agent.run("去二楼救人")

    assert first["session"]["session_id"] == "session-a"
    assert first["session"]["turn_index"] == 1
    assert first["session"]["resolved_command"] == "去一楼救人"
    assert first["session"]["context_used"] is False
    assert second["session"]["turn_index"] == 2

    records = JsonlMemoryStore(memory_path).list_records()
    assert records[0]["session"]["session_id"] == "session-a"
    assert records[0]["session"]["turn_index"] == 1
    assert records[1]["session"]["session_id"] == "session-a"
    assert records[1]["session"]["turn_index"] == 2


def test_agent_uses_injected_planner_with_planner_context(tmp_path):
    memory_path = tmp_path / "memory.jsonl"
    memory = JsonlMemoryStore(memory_path)
    memory.append(
        {
            "command": "历史任务",
            "status": "succeeded",
            "session": {"session_id": "session-a", "turn_index": 1},
        }
    )
    planner = RecordingPlanner()
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=memory,
        session_id="session-a",
        planner=planner,
    )

    result = agent.run("自定义计划")

    assert result["status"] == "succeeded"
    assert result["execution"]["steps"][0]["skill_name"] == "return_to_safe_zone"
    assert planner.calls[0]["command"] == "自定义计划"
    context = planner.calls[0]["context"]
    assert context.session_id == "session-a"
    assert context.turn_index == 2
    assert context.recent_records[0]["command"] == "历史任务"
    assert "return_to_safe_zone" in [skill["name"] for skill in context.skills]


def test_agent_serializes_execution_attempt_history(tmp_path):
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
    )

    result = agent.run("去坐标 (2.0, 1.5) 救人")

    first_step = result["execution"]["steps"][0]
    assert first_step["attempt_count"] == 1
    assert first_step["failure_category"] is None
    assert first_step["operator_action"] is None
    assert first_step["attempts"][0]["attempt_number"] == 1
    assert first_step["attempts"][0]["status"] == "succeeded"
    assert first_step["attempts"][0]["output"]["action"] == "navigate_to_point"


def test_agent_emits_robot_action_events_for_default_skills(tmp_path):
    events = []
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
        task_id="task-1",
    )

    result = agent.run("去坐标 (2.0, 1.5) 救人")

    assert result["status"] == "succeeded"
    event_types = [event_type for event_type, _payload in events]
    assert "action.requested" in event_types
    assert "action.started" in event_types
    assert "action.succeeded" in event_types
    requested = [payload for event_type, payload in events if event_type == "action.requested"]
    assert requested[0]["task_id"] == "task-1"
    assert requested[0]["skill_name"] == "navigate_to_point"
    assert requested[0]["action_type"] == "navigate_to_point"


def test_agent_unknown_command_returns_clarification_without_execution(tmp_path):
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
    )

    result = agent.run("随便看看")

    assert result["status"] == "clarify"
    assert result["execution"] is None
    assert "请明确" in result["message"]


def test_agent_resolves_floor_after_previous_clarification_in_same_session(tmp_path):
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(
            robot_id="robot-1",
            reachable_floors=[1, 2, 3],
        ),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        session_id="rescue-session",
    )

    first = agent.run("救人")
    second = agent.run("二楼")

    assert first["status"] == "clarify"
    assert second["command"] == "二楼"
    assert second["status"] == "succeeded"
    assert second["planning"]["target_floor"] == 2
    assert second["session"]["resolved_command"] == "去二楼救人"
    assert second["session"]["context_used"] is True
    assert second["session"]["turn_index"] == 2


def test_agent_resolves_point_after_previous_clarification_in_same_session(tmp_path):
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        session_id="point-rescue-session",
    )

    first = agent.run("救人")
    second = agent.run("坐标 (2.0, 1.5)")

    assert first["status"] == "clarify"
    assert second["status"] == "succeeded"
    assert second["planning"]["target_floor"] is None
    assert second["planning"]["target_pose"] == {
        "x": 2.0,
        "y": 1.5,
        "yaw": 0.0,
        "frame_id": "map",
    }
    assert second["session"]["resolved_command"] == "去坐标 (2.0, 1.5) 救人"
    assert second["session"]["context_used"] is True


def test_agent_reports_memory_write_errors():
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=FailingMemoryStore(),
    )

    result = agent.run("去坐标 (2.0, 1.5) 救人")

    assert result["status"] == "succeeded"
    assert result["memory_error"] == "memory disk unavailable"


def test_agent_recalls_recent_tasks_without_executing_or_rewriting_memory(tmp_path):
    memory_path = tmp_path / "memory.jsonl"
    memory = JsonlMemoryStore(memory_path)
    memory.append({"command": "去一楼救人", "status": "succeeded"})
    memory.append({"command": "去二楼救人", "status": "succeeded"})
    robot = DryRunRobotAdapter(robot_id="robot-1")
    agent = FireClawAgent(robot=robot, memory=memory)

    result = agent.run("之前做过什么")

    assert result["status"] == "recalled"
    assert result["execution"] is None
    assert result["memory"]["records"] == [
        {"command": "去一楼救人", "status": "succeeded"},
        {"command": "去二楼救人", "status": "succeeded"},
    ]
    assert robot.actions == []
    assert len(JsonlMemoryStore(memory_path).list_records()) == 2


def test_agent_recalls_only_current_session_records(tmp_path):
    memory_path = tmp_path / "memory.jsonl"
    memory = JsonlMemoryStore(memory_path)
    memory.append(
        {
            "command": "去一楼救人",
            "status": "succeeded",
            "session": {"session_id": "session-a", "turn_index": 1},
        }
    )
    memory.append(
        {
            "command": "去二楼救人",
            "status": "succeeded",
            "session": {"session_id": "session-b", "turn_index": 1},
        }
    )
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=memory,
        session_id="session-a",
    )

    result = agent.run("之前做过什么")

    assert result["status"] == "recalled"
    assert [record["command"] for record in result["memory"]["records"]] == ["去一楼救人"]


def test_agent_retrieves_successful_rescue_record_for_floor_in_current_session(tmp_path):
    memory = JsonlMemoryStore(tmp_path / "memory.jsonl")
    memory.append(
        {
            "command": "去二楼救人",
            "status": "succeeded",
            "session": {"session_id": "session-a", "turn_index": 1},
            "planning": {"intent": "rescue_victim", "target_floor": 2},
        }
    )
    memory.append(
        {
            "command": "去二楼救人",
            "status": "succeeded",
            "session": {"session_id": "session-b", "turn_index": 1},
            "planning": {"intent": "rescue_victim", "target_floor": 2},
        }
    )
    robot = DryRunRobotAdapter(robot_id="robot-1")
    agent = FireClawAgent(robot=robot, memory=memory, session_id="session-a")

    result = agent.run("之前二楼救人成功了吗")

    assert result["status"] == "retrieved"
    assert result["execution"] is None
    assert result["memory"]["query"] == {
        "session_id": "session-a",
        "status": "succeeded",
        "intent": "rescue_victim",
        "target_floor": 2,
        "command_contains": None,
        "limit": 5,
    }
    assert [record["session"]["session_id"] for record in result["memory"]["records"]] == ["session-a"]
    assert robot.actions == []


def test_agent_retrieves_failed_records_without_executing(tmp_path):
    memory = JsonlMemoryStore(tmp_path / "memory.jsonl")
    memory.append(
        {
            "command": "去三楼救人",
            "status": "failed",
            "session": {"session_id": "session-a", "turn_index": 1},
            "planning": {"intent": "rescue_victim", "target_floor": 3},
            "execution": {
                "steps": [
                    {
                        "skill_name": "search_for_victims",
                        "status": "failed",
                        "error": "thermal camera unavailable",
                    }
                ]
            },
        }
    )
    robot = DryRunRobotAdapter(robot_id="robot-1")
    agent = FireClawAgent(robot=robot, memory=memory, session_id="session-a")

    result = agent.run("上次失败原因是什么")

    assert result["status"] == "retrieved"
    assert result["memory"]["query"]["status"] == "failed"
    assert result["memory"]["records"][0]["execution"]["steps"][0]["error"] == "thermal camera unavailable"
    assert robot.actions == []


def test_agent_returns_empty_memory_retrieval_result(tmp_path):
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        session_id="session-a",
    )

    result = agent.run("查一下二楼救人记录")

    assert result["status"] == "retrieved"
    assert result["memory"]["records"] == []
    assert "没有找到" in result["message"]


def test_agent_reports_empty_memory_for_recall_command(tmp_path):
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
    )

    result = agent.run("回忆之前任务")

    assert result["status"] == "recalled"
    assert result["memory"]["records"] == []
    assert "没有找到" in result["message"]


def test_agent_reports_memory_read_errors_for_recall_command():
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=FailingReadMemoryStore(),
    )

    result = agent.run("回忆之前任务")

    assert result["status"] == "failed"
    assert result["execution"] is None
    assert result["memory_error"] == "memory read unavailable"


def test_agent_lists_available_skills_without_executing_or_writing_memory(tmp_path):
    memory_path = tmp_path / "memory.jsonl"
    robot = DryRunRobotAdapter(robot_id="robot-1")
    agent = FireClawAgent(robot=robot, memory=JsonlMemoryStore(memory_path))

    result = agent.run("你有哪些技能")

    assert result["status"] == "skills"
    assert result["execution"] is None
    assert [skill["name"] for skill in result["skills"]] == [
        "assess_victim",
        "navigate_to_floor",
        "navigate_to_point",
        "report_status",
        "return_to_safe_zone",
        "search_for_victims",
    ]
    assert {skill["runtime"] for skill in result["skills"]} == {"in_process"}
    assert robot.actions == []
    assert JsonlMemoryStore(memory_path).list_records() == []


def test_agent_lists_workspace_skills_with_builtins(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "workspace.skill.json").write_text(
        json.dumps(
            {
                "name": "workspace_policy",
                "description": "Workspace policy skill.",
                "runtime": "subprocess",
                "command": [sys.executable, "-c", "print('{\"ok\": true, \"data\": {}}')"],
                "timeout_seconds": 2,
                "dry_run_only": True,
            }
        ),
        encoding="utf-8",
    )
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        workspace_skills_dir=skills_dir,
    )

    result = agent.run("你有哪些技能")

    skill_names = [skill["name"] for skill in result["skills"]]
    assert "workspace_policy" in skill_names
    assert "navigate_to_floor" in skill_names
    assert result["skill_load_errors"] == []


def test_agent_lists_workspace_skill_execution_and_safety_metadata(tmp_path):
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        workspace_skills_dir="skills",
    )

    result = agent.run("你有哪些技能")

    echo_policy = next(skill for skill in result["skills"] if skill["name"] == "echo_policy")
    assert echo_policy["runtime"] == "subprocess"
    assert echo_policy["dry_run_only"] is True
    assert echo_policy["max_attempts"] == 1
    assert echo_policy["idempotent"] is True
    assert echo_policy["required_sensors"] == []
    assert echo_policy["failure_categories"] == ["invalid_input", "subprocess_error"]
    assert echo_policy["allow_real_robot"] is False
    assert echo_policy["timeout_seconds"] == 5.0


def test_agent_blocks_workspace_skill_when_required_sensor_is_unavailable(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "thermal_policy.skill.json").write_text(
        json.dumps(
            {
                "name": "thermal_policy",
                "description": "Requires thermal camera.",
                "runtime": "subprocess",
                "command": [sys.executable, "-c", "print('{\"ok\": true, \"data\": {}}')"],
                "dry_run_only": True,
                "required_sensors": ["thermal_camera"],
            }
        ),
        encoding="utf-8",
    )
    robot = DryRunRobotAdapter(robot_id="robot-1")
    agent = FireClawAgent(
        robot=robot,
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        workspace_skills_dir=skills_dir,
        available_sensors={"rgb_camera"},
    )

    result = agent.run("运行 thermal_policy")

    assert result["status"] == "block"
    assert result["execution"] is None
    assert "thermal_camera" in result["message"]
    assert robot.actions == []


def test_agent_reports_workspace_skill_load_errors_in_skill_listing(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "bad.skill.json").write_text(
        json.dumps(
            {
                "name": "bad_policy",
                "description": "Bad policy.",
                "runtime": "external_conda",
                "command": [sys.executable],
            }
        ),
        encoding="utf-8",
    )
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        workspace_skills_dir=skills_dir,
    )

    result = agent.run("你有哪些技能")

    assert result["status"] == "skills"
    assert len(result["skill_load_errors"]) == 1
    assert result["skill_load_errors"][0]["path"].endswith("bad.skill.json")


def test_agent_directly_invokes_loaded_workspace_skill_and_writes_memory(tmp_path):
    memory_path = tmp_path / "memory.jsonl"
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(memory_path),
        workspace_skills_dir="skills",
    )

    result = agent.run("运行 echo_policy 处理 二楼")

    assert result["status"] == "succeeded"
    assert result["planning"]["intent"] == "direct_skill_invocation"
    assert result["execution"]["steps"][0]["skill_name"] == "echo_policy"
    assert result["execution"]["steps"][0]["output"]["received"] == {"text": "二楼"}
    records = JsonlMemoryStore(memory_path).list_records()
    assert records[-1]["command"] == "运行 echo_policy 处理 二楼"
    assert records[-1]["status"] == "succeeded"


def test_agent_blocks_missing_direct_skill_before_execution_and_writes_memory(tmp_path):
    memory_path = tmp_path / "memory.jsonl"
    robot = DryRunRobotAdapter(robot_id="robot-1")
    agent = FireClawAgent(robot=robot, memory=JsonlMemoryStore(memory_path))

    result = agent.run("运行 missing_skill")

    assert result["status"] == "block"
    assert result["execution"] is None
    assert "Missing skill: missing_skill" in result["message"]
    assert robot.actions == []
    records = JsonlMemoryStore(memory_path).list_records()
    assert records[-1]["command"] == "运行 missing_skill"
    assert records[-1]["status"] == "block"


def test_agent_requires_confirmation_for_high_risk_skill_and_writes_pending_memory(tmp_path):
    memory_path = tmp_path / "memory.jsonl"
    skills_dir = tmp_path / "skills"
    _write_high_risk_workspace_skill(skills_dir)
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(memory_path),
        workspace_skills_dir=skills_dir,
        session_id="session-a",
    )

    result = agent.run("运行 smoke_entry")

    assert result["status"] == "awaiting_confirmation"
    assert result["safety"]["status"] == "require_confirmation"
    assert result["execution"] is None
    assert result["confirmation"]["status"] == "pending"
    assert "smoke_entry" in result["message"]
    records = JsonlMemoryStore(memory_path).list_records()
    assert records[-1]["status"] == "awaiting_confirmation"
    assert records[-1]["confirmation"]["status"] == "pending"
    assert records[-1]["planning"]["plan"]["steps"][0]["skill_name"] == "smoke_entry"


def test_agent_confirms_latest_pending_plan_and_executes(tmp_path):
    memory_path = tmp_path / "memory.jsonl"
    skills_dir = tmp_path / "skills"
    _write_high_risk_workspace_skill(skills_dir)
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(memory_path),
        workspace_skills_dir=skills_dir,
        session_id="session-a",
    )

    pending = agent.run("运行 smoke_entry")
    result = agent.run("确认执行")

    assert pending["status"] == "awaiting_confirmation"
    assert result["status"] == "succeeded"
    assert result["command"] == "确认执行"
    assert result["planning"]["plan"]["steps"][0]["skill_name"] == "smoke_entry"
    assert result["execution"]["steps"][0]["output"]["confirmed"] is True
    assert result["confirmation"] == {
        "status": "confirmed",
        "pending_turn_index": 1,
        "pending_command": "运行 smoke_entry",
    }
    records = JsonlMemoryStore(memory_path).list_records()
    assert [record["status"] for record in records] == ["awaiting_confirmation", "succeeded"]
    assert records[-1]["confirmation"]["pending_turn_index"] == 1


def test_agent_cancels_latest_pending_plan_without_execution(tmp_path):
    memory_path = tmp_path / "memory.jsonl"
    skills_dir = tmp_path / "skills"
    _write_high_risk_workspace_skill(skills_dir)
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(memory_path),
        workspace_skills_dir=skills_dir,
        session_id="session-a",
    )

    agent.run("运行 smoke_entry")
    result = agent.run("取消")

    assert result["status"] == "cancelled"
    assert result["execution"] is None
    assert result["confirmation"] == {
        "status": "cancelled",
        "pending_turn_index": 1,
        "pending_command": "运行 smoke_entry",
    }
    records = JsonlMemoryStore(memory_path).list_records()
    assert [record["status"] for record in records] == ["awaiting_confirmation", "cancelled"]


def test_agent_confirm_without_pending_plan_requests_clarification(tmp_path):
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        session_id="session-a",
    )

    result = agent.run("确认执行")

    assert result["status"] == "clarify"
    assert result["execution"] is None
    assert "没有待确认" in result["message"]


def test_agent_rescue_plan_runs_workspace_policy_before_rescue_steps(tmp_path):
    memory_path = tmp_path / "memory.jsonl"
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(memory_path),
        workspace_skills_dir="skills",
    )

    result = agent.run("去坐标 (2.0, 1.5) 救人 使用 echo_policy")

    assert result["status"] == "succeeded"
    assert [step["skill_name"] for step in result["execution"]["steps"]] == [
        "echo_policy",
        "navigate_to_point",
        "search_for_victims",
        "assess_victim",
        "report_status",
        "return_to_safe_zone",
    ]
    assert result["execution"]["steps"][0]["output"]["received"]["target"] == {
        "x": 2.0,
        "y": 1.5,
        "yaw": 0.0,
        "frame_id": "map",
    }
    assert result["execution"]["steps"][0]["output"]["received"]["command"] == (
        "去坐标 (2.0, 1.5) 救人 使用 echo_policy"
    )
    records = JsonlMemoryStore(memory_path).list_records()
    assert records[-1]["status"] == "succeeded"
    assert records[-1]["execution"]["steps"][0]["skill_name"] == "echo_policy"


def test_agent_blocks_missing_rescue_policy_before_execution_and_writes_memory(tmp_path):
    memory_path = tmp_path / "memory.jsonl"
    robot = DryRunRobotAdapter(robot_id="robot-1")
    agent = FireClawAgent(robot=robot, memory=JsonlMemoryStore(memory_path))

    result = agent.run("去坐标 (2.0, 1.5) 救人 使用 missing_policy")

    assert result["status"] == "block"
    assert result["execution"] is None
    assert "Missing skill: missing_policy" in result["message"]
    assert robot.actions == []
    records = JsonlMemoryStore(memory_path).list_records()
    assert records[-1]["command"] == "去坐标 (2.0, 1.5) 救人 使用 missing_policy"
    assert records[-1]["status"] == "block"


def test_agent_runs_rescue_flow_with_mock_ros2_adapter(tmp_path):
    robot = MockRos2RobotAdapter(robot_id="robot-ros2")
    agent = FireClawAgent(
        robot=robot,
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        dry_run=True,
    )

    result = agent.run("去坐标 (2.0, 1.5) 救人")

    assert result["status"] == "succeeded"
    assert result["execution"]["steps"][0]["output"]["mode"] == "mock_ros2"
    assert result["execution"]["steps"][0]["output"]["topic"] == "/fireclaw/robot-ros2/navigation"
    assert len(robot.commands) == 5


def test_agent_forwards_executor_live_events(tmp_path):
    events = []
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    result = agent.run("去坐标 (2.0, 1.5) 救人")

    assert result["status"] == "succeeded"
    assert [event_type for event_type, _payload in events[:8]] == [
        "task.planned",
        "safety.decided",
        "skill.started",
        "action.requested",
        "action.started",
        "action.succeeded",
        "skill.attempted",
        "skill.succeeded",
    ]
    assert events[0][1]["intent"] == "rescue_victim"
    assert events[2][1]["skill_name"] == "navigate_to_point"
    assert events[3][1]["action_type"] == "navigate_to_point"
    assert [event_type for event_type, _payload in events].count("skill.started") == 5


def test_agent_uses_robot_state_verified_sensors_when_no_override() -> None:
    endpoint = Ros1EndpointConfig(interface="topic", name="/fireclaw/test", type="std_msgs/String")
    robot = Ros1RobotAdapter(
        config=Ros1AdapterConfig(
            robot_id="robot-1",
            endpoints={
                "navigate_to_floor": endpoint,
                "search_for_victims": endpoint,
                "assess_victim": endpoint,
                "report_status": endpoint,
                "return_to_safe_zone": endpoint,
            },
        ),
        sensor_discovery=Ros1SensorDiscovery(
            graph_provider=StaticRos1GraphProvider({
                "/camera/image_raw": "sensor_msgs/Image",
                "/thermal/image_raw": "sensor_msgs/Image",
                "/scan": "sensor_msgs/LaserScan",
            }),
            message_probe=StaticRos1MessageProbe({
                "/camera/image_raw": True,
                "/thermal/image_raw": True,
                "/scan": True,
            }),
        ),
        dry_run=True,
    )
    agent = FireClawAgent(robot=robot, workspace_skills_dir=None, dry_run=True)

    result = agent.run("去二楼救人")

    assert result["status"] in {"succeeded", "completed"}
    assert sorted(result["robot_state"]["available_sensors"]) == sorted(["rgb_camera", "thermal_camera", "lidar"])


def test_agent_blocks_search_when_discovered_camera_health_is_invalid() -> None:
    endpoint = Ros1EndpointConfig(interface="topic", name="/fireclaw/test", type="std_msgs/String")
    robot = Ros1RobotAdapter(
        config=Ros1AdapterConfig(
            robot_id="robot-1",
            endpoints={
                "navigate_to_floor": endpoint,
                "search_for_victims": endpoint,
                "assess_victim": endpoint,
                "report_status": endpoint,
                "return_to_safe_zone": endpoint,
            },
        ),
        sensor_discovery=Ros1SensorDiscovery(
            graph_provider=StaticRos1GraphProvider({
                "/camera/image_raw": "sensor_msgs/Image",
                "/scan": "sensor_msgs/LaserScan",
            }),
            message_probe=StaticRos1MessageProbe(
                {
                    "/camera/image_raw": True,
                    "/scan": True,
                },
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
    agent = FireClawAgent(robot=robot, workspace_skills_dir=None, dry_run=True)

    result = agent.run("去二楼救人")

    assert result["status"] == "block"
    assert "rgb_camera" in result["message"]
    assert sorted(result["robot_state"]["available_sensors"]) == sorted(["lidar"])
