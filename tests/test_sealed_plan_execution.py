from __future__ import annotations

from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_planner import MissionPlan, MissionSubtask
from fireclaw_core.mission.mission_registry import JsonlMissionRegistry


class _PlannerMustNotRun:
    def __init__(self) -> None:
        self.calls = 0

    def plan(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("sealed-plan execution called the planner")


class _RobotClient:
    def __init__(self, *, terminal_status="succeeded") -> None:
        self.terminal_status = terminal_status
        self.submissions = []

    def check_presence(self, entry):
        return {
            "robot_id": entry.robot_id,
            "online": True,
            "last_seen_at": "2026-08-20T00:00:00+00:00",
            "state": {},
        }

    def submit_task(self, entry, **kwargs):
        self.submissions.append((entry.robot_id, kwargs))
        task_id = kwargs.get("task_id") or kwargs["structured_task"]["task_id"]
        return {
            "status": "accepted",
            "task_id": task_id,
            "robot_id": entry.robot_id,
        }

    def get_task_trace(self, entry, task_id):
        return {
            "status": self.terminal_status,
            "task_id": task_id,
            "robot_id": entry.robot_id,
            "result": {"status": self.terminal_status},
        }


def _plan() -> MissionPlan:
    return MissionPlan(
        intent="search",
        command="前往坐标 (2.0, 1.5) 搜索受困人员",
        subtasks=[
            MissionSubtask(
                robot_id="robot-1",
                command="在当前地图坐标 (2.0, 1.5) 搜索受困人员",
                floor=None,
                capability_required="victim_search",
                target={
                    "frame_id": "map",
                    "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
                },
            )
        ],
    )


def _agent(tmp_path, *, terminal_status="succeeded"):
    registry = RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot-1.local:8765",
                capabilities=("victim_search",),
            ),
            RobotRegistryEntry(
                robot_id="robot-2",
                base_url="http://robot-2.local:8765",
                capabilities=("victim_search",),
            ),
        ]
    )
    planner = _PlannerMustNotRun()
    client = _RobotClient(terminal_status=terminal_status)
    return (
        MissionAgent(
            registry=registry,
            subagent_client=client,
            planner=planner,
            mission_registry=JsonlMissionRegistry(tmp_path / "missions.jsonl"),
        ),
        planner,
        client,
    )


def test_sealed_execution_dispatches_exact_plan_without_planner_call(tmp_path):
    agent, planner, client = _agent(tmp_path)
    plan = _plan()

    result = agent.execute_sealed_plan(
        plan,
        session_id="mission-1",
        operator={"operator_id": "operator-1"},
        artifact_id="a" * 32,
        plan_digest="sha256:" + "b" * 64,
    )

    assert result["status"] == "succeeded"
    assert result["plan"] == plan.to_dict()
    assert result["plan_source"] == "sealed_plan_artifact"
    assert planner.calls == 0
    assert [(robot_id, call["command"]) for robot_id, call in client.submissions] == [
        ("robot-1", "在当前地图坐标 (2.0, 1.5) 搜索受困人员")
    ]


def test_sealed_execution_failure_never_reassigns_to_another_robot(tmp_path):
    agent, planner, client = _agent(tmp_path, terminal_status="failed")

    result = agent.execute_sealed_plan(
        _plan(),
        session_id="mission-2",
        operator={"operator_id": "operator-1"},
        artifact_id="c" * 32,
        plan_digest="sha256:" + "d" * 64,
    )

    assert result["status"] == "failed"
    assert planner.calls == 0
    assert [robot_id for robot_id, _ in client.submissions] == ["robot-1"]
    assert result["failure_decisions"][0]["reason"] == (
        "sealed_plan_requires_new_confirmation"
    )
