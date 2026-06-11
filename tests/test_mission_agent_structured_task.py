# tests/test_mission_agent_structured_task.py
from __future__ import annotations

from fireclaw_core.mission_agent import MissionAgent
from fireclaw_core.robot_registry import RobotRegistry, RobotRegistryEntry


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
            capabilities=["search_for_victims"],
        )
    ])
    client = RecordingClient()
    agent = MissionAgent(registry=registry, subagent_client=client)

    result = agent.submit_subtask(
        "robot-1",
        "去2楼搜索受困人员",
        session_id="session-1",
        operator={"operator_id": "operator-1"},
    )

    assert result["status"] == "accepted"
    structured_task = client.calls[0][1]["structured_task"]
    assert structured_task["mission_id"] == "session-1"
    assert structured_task["robot_id"] == "robot-1"
    assert structured_task["target"] == {"floor": 2}
    assert structured_task["required_skills"] == ["navigate_to_floor", "search_for_victims", "report_status"]
