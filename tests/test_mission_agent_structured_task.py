# tests/test_mission_agent_structured_task.py
from __future__ import annotations

from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry


class RecordingClient:
    def __init__(self):
        self.calls = []

    def submit_task(self, entry, **kwargs):
        self.calls.append((entry, kwargs))
        return {"status": "accepted", "task_id": "robot-task-1"}

    def get_task_trace(self, entry, task_id):
        return {"status": "completed", "task_id": task_id, "result": {"status": "completed"}}

    def cancel_task(self, entry, task_id, *, operator=None):
        return {"status": "cancelled", "task_id": task_id}

    def get_events(self, entry, task_id=None, limit=100):
        return []

    def check_presence(self, entry):
        return {"online": True}


def test_mission_agent_submit_subtask_sends_structured_task():
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-1",
            base_url="http://robot-1",
            capabilities=["navigate_to_point"],
        )
    ])
    client = RecordingClient()
    agent = MissionAgent(registry=registry, subagent_client=client)

    result = agent.submit_subtask(
        "robot-1",
        "导航到 map 坐标 (2.0, 1.5)",
        session_id="session-1",
        operator={"operator_id": "operator-1"},
    )

    assert result["status"] == "accepted"
    structured_task = client.calls[0][1]["structured_task"]
    assert structured_task["mission_id"] == "session-1"
    assert structured_task["robot_id"] == "robot-1"
    assert structured_task["target"] == {
        "frame_id": "map",
        "pose": {
            "x": 2.0,
            "y": 1.5,
            "yaw": 0.0,
        }
    }
    assert structured_task["required_skills"] == ["navigate_to_point"]


def test_mission_agent_structured_task_uses_explicit_target_not_command_text():
    from fireclaw_core.mission.mission_planner import MissionSubtask

    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-1",
            base_url="http://robot-1",
            capabilities=["navigate_to_point"],
        )
    ])
    client = RecordingClient()
    agent = MissionAgent(registry=registry, subagent_client=client)

    planned_subtask = MissionSubtask(
        robot_id="robot-1",
        command="navigate to the assigned map pose",
        floor=None,
        capability_required="navigate_to_point",
        execution_group=0,
        task_type="navigate",
        target={
            "frame_id": "map",
            "pose": {"x": 4.0, "y": -1.0, "yaw": 1.57},
        },
    )

    result = agent.submit_subtask(
        "robot-1",
        planned_subtask.command,
        session_id="mission-structured",
        operator={"operator_id": "operator-1"},
        mission_subtask=planned_subtask,
    )

    assert result["status"] == "accepted"
    structured_task = client.calls[0][1]["structured_task"]
    assert structured_task["target"] == {
        "frame_id": "map",
        "pose": {"x": 4.0, "y": -1.0, "yaw": 1.57},
    }
    assert structured_task["command"] == "navigate to the assigned map pose"
    assert structured_task["required_skills"] == ["navigate_to_point"]
