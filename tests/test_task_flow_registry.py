from fireclaw_core.task_flow_registry import (
    JsonlTaskFlowRegistryStore,
    TaskFlowRecord,
)


def test_task_flow_record_to_dict():
    flow = TaskFlowRecord(
        flow_id="mission-1",
        mission_id="mission-1",
        command="去二楼救人",
        status="running",
        task_ids=("mission-1:task-1", "mission-1:task-2"),
        robot_ids=("robot-1", "robot-2"),
        created_at="2026-06-10T00:00:00+08:00",
        updated_at="2026-06-10T00:00:01+08:00",
    )
    d = flow.to_dict()
    assert d["flow_id"] == "mission-1"
    assert d["status"] == "running"
    assert d["task_ids"] == ("mission-1:task-1", "mission-1:task-2")
    assert d["robot_ids"] == ("robot-1", "robot-2")
    assert d["command"] == "去二楼救人"
    assert d["mission_id"] == "mission-1"
    assert d["created_at"] == "2026-06-10T00:00:00+08:00"
    assert d["updated_at"] == "2026-06-10T00:00:01+08:00"


def test_task_flow_record_from_dict_roundtrip():
    original = TaskFlowRecord(
        flow_id="mission-1",
        mission_id="mission-1",
        command="去二楼救人",
        status="running",
        task_ids=("mission-1:task-1", "mission-1:task-2"),
        robot_ids=("robot-1", "robot-2"),
        created_at="2026-06-10T00:00:00+08:00",
        updated_at="2026-06-10T00:00:01+08:00",
    )
    d = original.to_dict()
    restored = TaskFlowRecord.from_dict(d)
    assert restored == original


def test_task_flow_store_upsert_and_get(tmp_path):
    store = JsonlTaskFlowRegistryStore(str(tmp_path / "flows.jsonl"))
    flow = TaskFlowRecord(
        flow_id="mission-1",
        mission_id="mission-1",
        command="去二楼救人",
        status="running",
        task_ids=("mission-1:task-1",),
        robot_ids=("robot-1",),
        created_at="2026-06-10T00:00:00+08:00",
        updated_at="2026-06-10T00:00:01+08:00",
    )
    store.upsert(flow)
    got = store.get("mission-1")
    assert got is not None
    assert got.task_ids == ("mission-1:task-1",)
    assert got.status == "running"
    assert got.command == "去二楼救人"


def test_task_flow_store_get_returns_none_for_missing(tmp_path):
    store = JsonlTaskFlowRegistryStore(str(tmp_path / "flows.jsonl"))
    assert store.get("nonexistent") is None


def test_task_flow_store_upsert_overwrites_same_flow_id(tmp_path):
    store = JsonlTaskFlowRegistryStore(str(tmp_path / "flows.jsonl"))
    flow1 = TaskFlowRecord(
        flow_id="mission-1",
        mission_id="mission-1",
        command="去二楼救人",
        status="running",
        task_ids=("mission-1:task-1",),
        robot_ids=("robot-1",),
        created_at="2026-06-10T00:00:00+08:00",
        updated_at="2026-06-10T00:00:01+08:00",
    )
    flow2 = TaskFlowRecord(
        flow_id="mission-1",
        mission_id="mission-1",
        command="去二楼救人",
        status="completed",
        task_ids=("mission-1:task-1",),
        robot_ids=("robot-1",),
        created_at="2026-06-10T00:00:00+08:00",
        updated_at="2026-06-10T00:00:05+08:00",
    )
    store.upsert(flow1)
    store.upsert(flow2)
    got = store.get("mission-1")
    assert got is not None
    assert got.status == "completed"
    assert got.updated_at == "2026-06-10T00:00:05+08:00"


def test_task_flow_store_list_recent(tmp_path):
    store = JsonlTaskFlowRegistryStore(str(tmp_path / "flows.jsonl"))
    for i in range(5):
        store.upsert(TaskFlowRecord(
            flow_id=f"mission-{i}",
            mission_id=f"mission-{i}",
            command=f"task {i}",
            status="running",
            task_ids=(),
            robot_ids=(),
            created_at=f"2026-06-10T00:0{i}:00+08:00",
            updated_at=f"2026-06-10T00:0{i}:01+08:00",
        ))
    recent = store.list_recent(limit=3)
    assert len(recent) == 3
    # Most recent first
    assert recent[0].flow_id == "mission-4"
    assert recent[1].flow_id == "mission-3"
    assert recent[2].flow_id == "mission-2"


def test_task_flow_store_list_recent_returns_all_when_limit_exceeds(tmp_path):
    store = JsonlTaskFlowRegistryStore(str(tmp_path / "flows.jsonl"))
    store.upsert(TaskFlowRecord(
        flow_id="mission-1",
        mission_id="mission-1",
        command="test",
        status="running",
        task_ids=(),
        robot_ids=(),
        created_at="2026-06-10T00:00:00+08:00",
        updated_at="2026-06-10T00:00:01+08:00",
    ))
    recent = store.list_recent(limit=10)
    assert len(recent) == 1


def test_task_flow_store_list_recent_deduplicates_by_flow_id(tmp_path):
    """When a flow is upserted multiple times, list_recent returns only the latest."""
    store = JsonlTaskFlowRegistryStore(str(tmp_path / "flows.jsonl"))
    store.upsert(TaskFlowRecord(
        flow_id="mission-1",
        mission_id="mission-1",
        command="test",
        status="running",
        task_ids=(),
        robot_ids=(),
        created_at="2026-06-10T00:00:00+08:00",
        updated_at="2026-06-10T00:00:01+08:00",
    ))
    store.upsert(TaskFlowRecord(
        flow_id="mission-1",
        mission_id="mission-1",
        command="test",
        status="completed",
        task_ids=(),
        robot_ids=(),
        created_at="2026-06-10T00:00:00+08:00",
        updated_at="2026-06-10T00:00:10+08:00",
    ))
    store.upsert(TaskFlowRecord(
        flow_id="mission-2",
        mission_id="mission-2",
        command="test2",
        status="running",
        task_ids=(),
        robot_ids=(),
        created_at="2026-06-10T00:01:00+08:00",
        updated_at="2026-06-10T00:01:01+08:00",
    ))
    recent = store.list_recent(limit=10)
    assert len(recent) == 2
    assert recent[0].flow_id == "mission-2"
    assert recent[1].flow_id == "mission-1"
    assert recent[1].status == "completed"


def test_task_flow_observer_callback(tmp_path):
    events = []
    store = JsonlTaskFlowRegistryStore(
        str(tmp_path / "flows.jsonl"),
        on_event=lambda evt: events.append(evt),
    )
    flow = TaskFlowRecord(
        flow_id="mission-1",
        mission_id="mission-1",
        command="test",
        status="running",
        task_ids=(),
        robot_ids=(),
        created_at="2026-06-10T00:00:00+08:00",
        updated_at="2026-06-10T00:00:01+08:00",
    )
    store.upsert(flow)
    assert len(events) == 1
    assert events[0]["kind"] == "upserted"
    assert events[0]["flow"]["flow_id"] == "mission-1"


def test_task_flow_store_corrupt_line_tolerance(tmp_path):
    """Store should skip corrupt lines and load remaining records."""
    path = tmp_path / "flows.jsonl"
    # Write a corrupt line followed by a valid one
    with open(path, "w") as f:
        f.write("not valid json\n")
        f.write('{"flow_id":"mission-1","mission_id":"mission-1","command":"test","status":"running","task_ids":[],"robot_ids":[],"created_at":"2026-06-10T00:00:00+08:00","updated_at":"2026-06-10T00:00:01+08:00"}\n')

    store = JsonlTaskFlowRegistryStore(str(path))
    got = store.get("mission-1")
    assert got is not None
    assert got.status == "running"


def test_task_flow_store_empty_file(tmp_path):
    path = tmp_path / "flows.jsonl"
    path.write_text("")
    store = JsonlTaskFlowRegistryStore(str(path))
    assert store.get("anything") is None
    assert store.list_recent() == []
