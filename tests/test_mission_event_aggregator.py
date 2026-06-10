from __future__ import annotations

from fireclaw_core.mission_event_aggregator import MissionEventAggregator
from fireclaw_core.mission_registry import (
    JsonlMissionRegistry,
    MissionRecord,
    MissionSubtaskRecord,
)
from fireclaw_core.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.subagent_registry import JsonlSubagentRegistry
from fireclaw_core.task_flow_registry import JsonlTaskFlowRegistryStore, TaskFlowRecord


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


def test_aggregator_routes_terminal_robot_events_to_subagent_registry(tmp_path):
    """Terminal robot events (task.completed, task.failed, etc.) should be
    routed into SubagentRegistry.mark_terminal() during aggregation."""
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")
    subagents.create(
        parent_mission_id="m1",
        parent_subtask_id="subtask-1",
        robot_id="robot-1",
        child_task_id="t1",
        created_at="2026-06-10T00:00:00+00:00",
    )
    subagents.create(
        parent_mission_id="m1",
        parent_subtask_id="subtask-2",
        robot_id="robot-2",
        child_task_id="t2",
        created_at="2026-06-10T00:00:00+00:00",
    )

    mission = _two_robot_mission()
    mission_reg = _make_registry(mission, tmp_path)
    client = MockSubagentClient(
        {
            "t1": [
                {"event_id": "e1", "task_id": "t1", "type": "task.completed", "timestamp": "2026-06-08T10:01:00Z", "payload": {}},
            ],
            "t2": [
                {"event_id": "e2", "task_id": "t2", "type": "task.failed", "timestamp": "2026-06-08T10:00:30Z", "payload": {"error": "obstacle"}},
            ],
        }
    )
    agg = MissionEventAggregator(
        registry=_robot_registry(),
        subagent_client=client,
        mission_registry=mission_reg,
        subagent_registry=subagents,
    )

    result = agg.aggregate("m1")

    assert result["event_count"] == 2
    # Verify terminal routing happened
    r1 = subagents.get_by_child_task_id("t1")
    assert r1 is not None
    assert r1.status == "completed"
    r2 = subagents.get_by_child_task_id("t2")
    assert r2 is not None
    assert r2.status == "failed"


def test_aggregator_routes_terminal_status_from_payload(tmp_path):
    """Terminal status can also come from payload.status field."""
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")
    subagents.create(
        parent_mission_id="m1",
        parent_subtask_id="subtask-1",
        robot_id="robot-1",
        child_task_id="t1",
        created_at="2026-06-10T00:00:00+00:00",
    )

    mission = _two_robot_mission()
    mission_reg = _make_registry(mission, tmp_path)
    client = MockSubagentClient(
        {
            "t1": [
                {"event_id": "e1", "task_id": "t1", "type": "status_update", "timestamp": "2026-06-08T10:01:00Z", "payload": {"status": "cancelled"}},
            ],
            "t2": [],
        }
    )
    agg = MissionEventAggregator(
        registry=_robot_registry(),
        subagent_client=client,
        mission_registry=mission_reg,
        subagent_registry=subagents,
    )

    result = agg.aggregate("m1")

    assert result["event_count"] == 1
    r1 = subagents.get_by_child_task_id("t1")
    assert r1 is not None
    assert r1.status == "cancelled"


def test_aggregator_skips_non_terminal_events(tmp_path):
    """Non-terminal events should not trigger mark_terminal."""
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")
    subagents.create(
        parent_mission_id="m1",
        parent_subtask_id="subtask-1",
        robot_id="robot-1",
        child_task_id="t1",
        created_at="2026-06-10T00:00:00+00:00",
    )

    mission = _two_robot_mission()
    mission_reg = _make_registry(mission, tmp_path)
    client = MockSubagentClient(
        {
            "t1": [
                {"event_id": "e1", "task_id": "t1", "type": "sensor_reading", "timestamp": "2026-06-08T10:01:00Z", "payload": {}},
            ],
            "t2": [],
        }
    )
    agg = MissionEventAggregator(
        registry=_robot_registry(),
        subagent_client=client,
        mission_registry=mission_reg,
        subagent_registry=subagents,
    )

    result = agg.aggregate("m1")

    assert result["event_count"] == 1
    r1 = subagents.get_by_child_task_id("t1")
    assert r1 is not None
    # Should remain at initial status since no terminal event was processed
    assert r1.status == "dispatched"


# --- TaskFlowRegistry terminal status tests ---

def _make_task_flow_store(tmp_path):
    return JsonlTaskFlowRegistryStore(str(tmp_path / "flows.jsonl"))


def test_aggregator_updates_task_flow_to_completed_when_all_tasks_terminal(tmp_path):
    """When all projected task_ids have terminal events, flow should become 'completed'."""
    store = _make_task_flow_store(tmp_path)
    store.upsert(TaskFlowRecord(
        flow_id="m1",
        mission_id="m1",
        command="rescue on floor 2",
        status="running",
        task_ids=("t1", "t2"),
        robot_ids=("robot-1", "robot-2"),
        created_at="2026-06-10T00:00:00+00:00",
        updated_at="2026-06-10T00:00:00+00:00",
    ))

    mission = _two_robot_mission()
    mission_reg = _make_registry(mission, tmp_path)
    client = MockSubagentClient({
        "t1": [
            {"event_id": "e1", "task_id": "t1", "type": "task.completed", "timestamp": "2026-06-10T00:01:00Z", "payload": {}},
        ],
        "t2": [
            {"event_id": "e2", "task_id": "t2", "type": "task.completed", "timestamp": "2026-06-10T00:01:01Z", "payload": {}},
        ],
    })
    agg = MissionEventAggregator(
        registry=_robot_registry(),
        subagent_client=client,
        mission_registry=mission_reg,
        task_flow_store=store,
    )

    result = agg.aggregate("m1")

    assert result["event_count"] == 2
    flow = store.get("m1")
    assert flow is not None
    assert flow.status == "completed"


def test_aggregator_updates_task_flow_to_failed_when_any_task_failed(tmp_path):
    """If any task fails, flow status should be 'failed'."""
    store = _make_task_flow_store(tmp_path)
    store.upsert(TaskFlowRecord(
        flow_id="m1",
        mission_id="m1",
        command="rescue",
        status="running",
        task_ids=("t1", "t2"),
        robot_ids=("robot-1", "robot-2"),
        created_at="2026-06-10T00:00:00+00:00",
        updated_at="2026-06-10T00:00:00+00:00",
    ))

    mission = _two_robot_mission()
    mission_reg = _make_registry(mission, tmp_path)
    client = MockSubagentClient({
        "t1": [
            {"event_id": "e1", "task_id": "t1", "type": "task.completed", "timestamp": "2026-06-10T00:01:00Z", "payload": {}},
        ],
        "t2": [
            {"event_id": "e2", "task_id": "t2", "type": "task.failed", "timestamp": "2026-06-10T00:01:01Z", "payload": {"error": "obstacle"}},
        ],
    })
    agg = MissionEventAggregator(
        registry=_robot_registry(),
        subagent_client=client,
        mission_registry=mission_reg,
        task_flow_store=store,
    )

    agg.aggregate("m1")

    flow = store.get("m1")
    assert flow is not None
    assert flow.status == "failed"


def test_aggregator_does_not_update_flow_when_tasks_still_running(tmp_path):
    """Flow should stay 'running' if not all task_ids have terminal events."""
    store = _make_task_flow_store(tmp_path)
    store.upsert(TaskFlowRecord(
        flow_id="m1",
        mission_id="m1",
        command="rescue",
        status="running",
        task_ids=("t1", "t2"),
        robot_ids=("robot-1", "robot-2"),
        created_at="2026-06-10T00:00:00+00:00",
        updated_at="2026-06-10T00:00:00+00:00",
    ))

    mission = _two_robot_mission()
    mission_reg = _make_registry(mission, tmp_path)
    client = MockSubagentClient({
        "t1": [
            {"event_id": "e1", "task_id": "t1", "type": "task.completed", "timestamp": "2026-06-10T00:01:00Z", "payload": {}},
        ],
        "t2": [
            {"event_id": "e2", "task_id": "t2", "type": "sensor_reading", "timestamp": "2026-06-10T00:01:01Z", "payload": {}},
        ],
    })
    agg = MissionEventAggregator(
        registry=_robot_registry(),
        subagent_client=client,
        mission_registry=mission_reg,
        task_flow_store=store,
    )

    agg.aggregate("m1")

    flow = store.get("m1")
    assert flow is not None
    assert flow.status == "running"


def test_aggregator_skips_flow_update_when_no_flow_exists(tmp_path):
    """No crash when there is no flow record for the mission."""
    store = _make_task_flow_store(tmp_path)
    mission = _two_robot_mission()
    mission_reg = _make_registry(mission, tmp_path)
    client = MockSubagentClient({"t1": [], "t2": []})
    agg = MissionEventAggregator(
        registry=_robot_registry(),
        subagent_client=client,
        mission_registry=mission_reg,
        task_flow_store=store,
    )

    # Should not raise
    result = agg.aggregate("m1")
    assert result["event_count"] == 0


def test_aggregator_skips_flow_update_when_already_terminal(tmp_path):
    """Flow should not be re-upserted if already in a terminal status."""
    store = _make_task_flow_store(tmp_path)
    store.upsert(TaskFlowRecord(
        flow_id="m1",
        mission_id="m1",
        command="rescue",
        status="completed",
        task_ids=("t1",),
        robot_ids=("robot-1",),
        created_at="2026-06-10T00:00:00+00:00",
        updated_at="2026-06-10T00:00:00+00:00",
    ))

    mission = _two_robot_mission()
    mission_reg = _make_registry(mission, tmp_path)
    client = MockSubagentClient({
        "t1": [
            {"event_id": "e1", "task_id": "t1", "type": "task.completed", "timestamp": "2026-06-10T00:01:00Z", "payload": {}},
        ],
        "t2": [],
    })
    agg = MissionEventAggregator(
        registry=_robot_registry(),
        subagent_client=client,
        mission_registry=mission_reg,
        task_flow_store=store,
    )

    agg.aggregate("m1")

    flow = store.get("m1")
    assert flow is not None
    # Should still be "completed" (not re-upserted)
    assert flow.status == "completed"
