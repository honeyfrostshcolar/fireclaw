from __future__ import annotations

from fireclaw_core.mission_event_aggregator import MissionEventAggregator
from fireclaw_core.mission_registry import (
    JsonlMissionRegistry,
    MissionRecord,
    MissionSubtaskRecord,
)
from fireclaw_core.robot_registry import RobotRegistry, RobotRegistryEntry


class MockSubagentClient:
    def __init__(self, events_by_task: dict[str, list[dict]]):
        self.events_by_task = events_by_task
        self.calls: list[tuple[str, str | None]] = []

    def get_events(self, entry, task_id=None, limit=100):
        self.calls.append((entry.robot_id, task_id))
        return self.events_by_task.get(task_id, [])


def _make_registry(mission: MissionRecord, tmp_path):
    path = tmp_path / "missions.jsonl"
    path.write_text("")
    reg = JsonlMissionRegistry(path)
    for subtask in mission.subtasks:
        reg.create_mission(
            mission_id=mission.mission_id,
            session_id=mission.session_id,
            command=mission.command,
            created_at=mission.created_at,
        )
        reg.record_subtask(
            mission_id=mission.mission_id,
            robot_id=subtask.robot_id,
            task_id=subtask.task_id,
            command=subtask.command,
            status=subtask.status,
            created_at=subtask.created_at,
        )
    return reg


def _two_robot_mission():
    return MissionRecord(
        mission_id="m1",
        session_id="s1",
        command="rescue on floor 2",
        status="running",
        created_at="2026-06-08T10:00:00Z",
        updated_at="2026-06-08T10:00:00Z",
        subtasks=[
            MissionSubtaskRecord(
                robot_id="robot-1",
                task_id="t1",
                command="search east wing",
                status="running",
                created_at="2026-06-08T10:00:00Z",
                updated_at="2026-06-08T10:00:00Z",
            ),
            MissionSubtaskRecord(
                robot_id="robot-2",
                task_id="t2",
                command="search west wing",
                status="running",
                created_at="2026-06-08T10:00:01Z",
                updated_at="2026-06-08T10:00:01Z",
            ),
        ],
    )


def _robot_registry():
    return RobotRegistry(
        [
            RobotRegistryEntry(robot_id="robot-1", base_url="http://r1:8765"),
            RobotRegistryEntry(robot_id="robot-2", base_url="http://r2:8765"),
        ]
    )


def test_aggregator_merges_events_from_multiple_robots(tmp_path):
    mission = _two_robot_mission()
    mission_reg = _make_registry(mission, tmp_path)
    client = MockSubagentClient(
        {
            "t1": [
                {"event_id": "e1", "task_id": "t1", "type": "sensor_reading", "timestamp": "2026-06-08T10:01:00Z", "payload": {}},
            ],
            "t2": [
                {"event_id": "e2", "task_id": "t2", "type": "obstacle_detected", "timestamp": "2026-06-08T10:00:30Z", "payload": {}},
            ],
        }
    )
    agg = MissionEventAggregator(
        registry=_robot_registry(),
        subagent_client=client,
        mission_registry=mission_reg,
    )

    result = agg.aggregate("m1")

    assert result["mission_id"] == "m1"
    assert result["event_count"] == 2
    # Sorted by timestamp ascending: e2 at :30 before e1 at :01:00
    assert result["events"][0]["event_id"] == "e2"
    assert result["events"][1]["event_id"] == "e1"
    # Each event should have robot_id added
    assert result["events"][0]["robot_id"] == "robot-2"
    assert result["events"][1]["robot_id"] == "robot-1"


def test_aggregator_filters_by_robot_id(tmp_path):
    mission = _two_robot_mission()
    mission_reg = _make_registry(mission, tmp_path)
    client = MockSubagentClient(
        {
            "t1": [
                {"event_id": "e1", "task_id": "t1", "type": "sensor_reading", "timestamp": "2026-06-08T10:01:00Z", "payload": {}},
            ],
            "t2": [
                {"event_id": "e2", "task_id": "t2", "type": "obstacle_detected", "timestamp": "2026-06-08T10:00:30Z", "payload": {}},
            ],
        }
    )
    agg = MissionEventAggregator(
        registry=_robot_registry(),
        subagent_client=client,
        mission_registry=mission_reg,
    )

    result = agg.aggregate("m1", robot_id="robot-1")

    assert result["event_count"] == 1
    assert result["events"][0]["event_id"] == "e1"
    assert result["events"][0]["robot_id"] == "robot-1"


def test_aggregator_filters_by_event_type(tmp_path):
    mission = _two_robot_mission()
    mission_reg = _make_registry(mission, tmp_path)
    client = MockSubagentClient(
        {
            "t1": [
                {"event_id": "e1", "task_id": "t1", "type": "sensor_reading", "timestamp": "2026-06-08T10:01:00Z", "payload": {}},
            ],
            "t2": [
                {"event_id": "e2", "task_id": "t2", "type": "obstacle_detected", "timestamp": "2026-06-08T10:00:30Z", "payload": {}},
            ],
        }
    )
    agg = MissionEventAggregator(
        registry=_robot_registry(),
        subagent_client=client,
        mission_registry=mission_reg,
    )

    result = agg.aggregate("m1", event_type="obstacle_detected")

    assert result["event_count"] == 1
    assert result["events"][0]["event_id"] == "e2"


def test_aggregator_respects_limit(tmp_path):
    mission = _two_robot_mission()
    mission_reg = _make_registry(mission, tmp_path)
    client = MockSubagentClient(
        {
            "t1": [
                {"event_id": "e1", "task_id": "t1", "type": "sensor_reading", "timestamp": "2026-06-08T10:01:00Z", "payload": {}},
            ],
            "t2": [
                {"event_id": "e2", "task_id": "t2", "type": "obstacle_detected", "timestamp": "2026-06-08T10:00:30Z", "payload": {}},
            ],
        }
    )
    agg = MissionEventAggregator(
        registry=_robot_registry(),
        subagent_client=client,
        mission_registry=mission_reg,
    )

    result = agg.aggregate("m1", limit=1)

    assert result["event_count"] == 1
    # Should return the oldest event (e2)
    assert result["events"][0]["event_id"] == "e2"


def test_aggregator_returns_empty_for_missing_mission(tmp_path):
    mission_reg = _make_registry(_two_robot_mission(), tmp_path)
    client = MockSubagentClient({})
    agg = MissionEventAggregator(
        registry=_robot_registry(),
        subagent_client=client,
        mission_registry=mission_reg,
    )

    result = agg.aggregate("nonexistent")

    assert result["mission_id"] == "nonexistent"
    assert result["event_count"] == 0
    assert result["events"] == []
