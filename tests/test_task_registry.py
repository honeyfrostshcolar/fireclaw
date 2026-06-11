from fireclaw_core.task_registry import (
    JsonlTaskRegistryStore,
    TaskDeliveryState,
    TaskRecord,
    TaskRegistrySnapshot,
    VALID_SCOPE_KINDS,
)
from fireclaw_core.task_queue import TaskQueueRecord, queue_record_to_task_record


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


def test_task_record_tracks_runtime_owner_and_delivery_state():
    record = TaskRecord(
        task_id="task-1",
        runtime="robot_gateway",
        requester_session_id="mission-1",
        owner_id="robot-a",
        scope_kind="mission",
        command="搜索二楼",
        status="queued",
        delivery_status="pending",
        notify_policy="state_changes",
        created_at="2026-06-09T00:00:00+00:00",
    )

    assert record.runtime == "robot_gateway"
    assert record.owner_id == "robot-a"
    assert record.delivery_status == "pending"
    assert record.scope_kind == "mission"


def test_valid_scope_kinds_include_subtask_projection_scope():
    assert "subtask" in VALID_SCOPE_KINDS


def test_task_record_is_terminal_property():
    for status in ("completed", "cancelled", "failed", "denied", "lost", "timed_out", "succeeded"):
        record = TaskRecord(
            task_id=f"task-{status}",
            runtime="cli",
            requester_session_id="session-1",
            owner_id="user-1",
            scope_kind="session",
            command="test",
            status=status,
            delivery_status="not_applicable",
            notify_policy="done_only",
            created_at="2026-06-09T00:00:00+00:00",
        )
        assert record.is_terminal, f"{status} should be terminal"

    for status in ("queued", "running", "unknown"):
        record = TaskRecord(
            task_id=f"task-{status}",
            runtime="cli",
            requester_session_id="session-1",
            owner_id="user-1",
            scope_kind="session",
            command="test",
            status=status,
            delivery_status="not_applicable",
            notify_policy="done_only",
            created_at="2026-06-09T00:00:00+00:00",
        )
        assert not record.is_terminal, f"{status} should not be terminal"


def test_task_record_to_dict_roundtrip():
    record = TaskRecord(
        task_id="task-1",
        runtime="robot_gateway",
        task_kind="navigation",
        source_id="operator-console",
        requester_session_id="mission-1",
        owner_id="robot-a",
        scope_kind="mission",
        child_session_id="sub-session-1",
        parent_task_id="parent-task-1",
        agent_id="agent-1",
        run_id="run-1",
        label="search-floor-2",
        command="搜索二楼",
        status="running",
        delivery_status="delivered",
        notify_policy="state_changes",
        created_at="2026-06-09T00:00:00+00:00",
        started_at="2026-06-09T00:00:01+00:00",
        last_event_at="2026-06-09T00:00:02+00:00",
        cleanup_after="2026-06-10T00:00:00+00:00",
        progress_summary="1/3 rooms searched",
    )

    d = record.to_dict()
    restored = TaskRecord.from_dict(d)

    assert restored == record
    assert restored.runtime == "robot_gateway"
    assert restored.task_kind == "navigation"
    assert restored.child_session_id == "sub-session-1"
    assert restored.progress_summary == "1/3 rooms searched"


def test_task_record_from_dict_handles_missing_optional_fields():
    d = {
        "task_id": "task-minimal",
        "runtime": "cli",
        "requester_session_id": "session-1",
        "owner_id": "user-1",
        "scope_kind": "session",
        "command": "test",
        "status": "queued",
        "delivery_status": "pending",
        "notify_policy": "done_only",
        "created_at": "2026-06-09T00:00:00+00:00",
    }
    record = TaskRecord.from_dict(d)
    assert record.task_id == "task-minimal"
    assert record.task_kind is None
    assert record.started_at is None
    assert record.error is None


def test_delivery_state_serializes_requester_context():
    state = TaskDeliveryState(
        task_id="task-1",
        requester={"operator_id": "op-a", "role": "operator"},
        last_notified_event_at="2026-06-09T00:00:01+00:00",
    )

    d = state.to_dict()
    assert d["task_id"] == "task-1"
    assert d["requester"]["operator_id"] == "op-a"
    assert d["last_notified_event_at"] == "2026-06-09T00:00:01+00:00"

    restored = TaskDeliveryState.from_dict(d)
    assert restored == state


def test_delivery_state_with_no_optional_fields():
    state = TaskDeliveryState(task_id="task-2")
    d = state.to_dict()
    assert d == {"task_id": "task-2", "requester": None, "last_notified_event_at": None}

    restored = TaskDeliveryState.from_dict(d)
    assert restored.task_id == "task-2"
    assert restored.requester is None


# ---------------------------------------------------------------------------
# Snapshot tests
# ---------------------------------------------------------------------------


def test_task_registry_snapshot_from_records():
    records = [
        TaskRecord(
            task_id="t1", runtime="cli", requester_session_id="s1", owner_id="u1",
            scope_kind="session", command="a", status="queued",
            delivery_status="pending", notify_policy="done_only",
            created_at="2026-06-09T00:00:00+00:00",
        ),
        TaskRecord(
            task_id="t2", runtime="robot_gateway", requester_session_id="s1", owner_id="r1",
            scope_kind="mission", command="b", status="running",
            delivery_status="delivered", notify_policy="state_changes",
            created_at="2026-06-09T00:00:01+00:00",
            started_at="2026-06-09T00:00:02+00:00",
        ),
        TaskRecord(
            task_id="t3", runtime="cli", requester_session_id="s1", owner_id="u1",
            scope_kind="session", command="c", status="completed",
            delivery_status="delivered", notify_policy="done_only",
            created_at="2026-06-09T00:00:03+00:00",
            ended_at="2026-06-09T00:00:04+00:00",
        ),
    ]

    snap = TaskRegistrySnapshot.from_records(records)

    assert snap.total == 3
    assert snap.active_count == 2  # queued + running
    assert snap.terminal_count == 1  # completed
    assert len(snap.active) == 2
    assert len(snap.terminal) == 1


# ---------------------------------------------------------------------------
# JSONL store tests
# ---------------------------------------------------------------------------


def _make_record(task_id: str, **overrides) -> TaskRecord:
    defaults = dict(
        task_id=task_id,
        runtime="robot_gateway",
        requester_session_id="mission-1",
        owner_id="robot-a",
        scope_kind="mission",
        command="搜索二楼",
        status="queued",
        delivery_status="pending",
        notify_policy="state_changes",
        created_at="2026-06-09T00:00:00+00:00",
    )
    defaults.update(overrides)
    return TaskRecord(**defaults)


def test_jsonl_store_creates_and_gets(tmp_path):
    store = JsonlTaskRegistryStore(tmp_path / "registry.jsonl")

    record = store.create(
        task_id="task-1",
        runtime="robot_gateway",
        requester_session_id="mission-1",
        owner_id="robot-a",
        scope_kind="mission",
        command="搜索二楼",
    )

    assert record.status == "queued"
    assert record.delivery_status == "pending"
    assert record.notify_policy == "state_changes"

    loaded = store.get("task-1")
    assert loaded == record


def test_jsonl_store_update(tmp_path):
    store = JsonlTaskRegistryStore(tmp_path / "registry.jsonl")
    store.create(
        task_id="task-1",
        runtime="robot_gateway",
        requester_session_id="mission-1",
        owner_id="robot-a",
        scope_kind="mission",
        command="搜索二楼",
    )

    updated = store.update(
        "task-1",
        status="running",
        started_at="2026-06-09T00:00:01+00:00",
        delivery_status="delivered",
    )

    assert updated.status == "running"
    assert updated.started_at == "2026-06-09T00:00:01+00:00"
    assert updated.delivery_status == "delivered"

    loaded = store.get("task-1")
    assert loaded == updated


def test_jsonl_store_list_records(tmp_path):
    store = JsonlTaskRegistryStore(tmp_path / "registry.jsonl")
    store.create(
        task_id="task-1", runtime="cli", requester_session_id="s1",
        owner_id="u1", scope_kind="session", command="a",
    )
    store.create(
        task_id="task-2", runtime="robot_gateway", requester_session_id="s1",
        owner_id="r1", scope_kind="mission", command="b",
    )

    records = store.list_records()
    assert len(records) == 2
    assert {r.task_id for r in records} == {"task-1", "task-2"}


def test_jsonl_store_get_missing_returns_none(tmp_path):
    store = JsonlTaskRegistryStore(tmp_path / "registry.jsonl")
    assert store.get("nonexistent") is None


def test_jsonl_store_update_missing_raises(tmp_path):
    store = JsonlTaskRegistryStore(tmp_path / "registry.jsonl")
    try:
        store.update("nonexistent", status="running")
        assert False, "Should have raised KeyError"
    except KeyError:
        pass


def test_jsonl_store_update_clears_fields_to_none(tmp_path):
    """update() must allow clearing optional fields back to None."""
    store = JsonlTaskRegistryStore(tmp_path / "registry.jsonl")
    store.create(
        task_id="t1",
        runtime="robot_gateway",
        requester_session_id="s1",
        owner_id="o1",
        scope_kind="mission",
        command="搜索",
    )
    # Set error and result
    store.update("t1", status="failed", error="boom", result={"code": 1})
    rec = store.get("t1")
    assert rec is not None
    assert rec.error == "boom"
    assert rec.result == {"code": 1}

    # Clear them back to None
    store.update("t1", status="running", error=None, result=None)
    rec = store.get("t1")
    assert rec is not None
    assert rec.error is None
    assert rec.result is None
    assert rec.status == "running"


def test_jsonl_store_corrupt_line_tolerance(tmp_path):
    path = tmp_path / "registry.jsonl"
    path.write_text(
        '{"task_id": "t1", "runtime": "cli", "requester_session_id": "s1", '
        '"owner_id": "u1", "scope_kind": "session", "command": "a", "status": "queued", '
        '"delivery_status": "pending", "notify_policy": "done_only", '
        '"created_at": "2026-06-09T00:00:00+00:00"}\n'
        "NOT JSON\n"
        "123\n"
        '{"task_id": "t2", "runtime": "cli", "requester_session_id": "s1", '
        '"owner_id": "u1", "scope_kind": "session", "command": "b", "status": "queued", '
        '"delivery_status": "pending", "notify_policy": "done_only", '
        '"created_at": "2026-06-09T00:00:01+00:00"}\n',
        encoding="utf-8",
    )

    store = JsonlTaskRegistryStore(path)
    records = store.list_records()
    assert len(records) == 2
    assert records[0].task_id == "t1"
    assert records[1].task_id == "t2"


def test_jsonl_store_snapshot(tmp_path):
    store = JsonlTaskRegistryStore(tmp_path / "registry.jsonl")
    store.create(
        task_id="t1", runtime="cli", requester_session_id="s1",
        owner_id="u1", scope_kind="session", command="a",
    )
    store.create(
        task_id="t2", runtime="robot_gateway", requester_session_id="s1",
        owner_id="r1", scope_kind="mission", command="b",
    )
    store.update("t2", status="running", started_at="2026-06-09T00:00:01+00:00")

    snap = store.snapshot()
    assert snap.total == 2
    assert snap.active_count == 2  # both non-terminal
    assert snap.terminal_count == 0


# ---------------------------------------------------------------------------
# project_task_state tests
# ---------------------------------------------------------------------------


def test_task_registry_projects_mission_subtask_lifecycle(tmp_path):
    store = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")

    record = store.project_task_state(
        task_id="mission-1:subtask-1",
        requester_session_id="mission-1",
        owner_id="robot-1",
        command="搜索二楼",
        runtime="robot_gateway",
        scope_kind="mission",
        status="queued",
        delivery_status="pending",
        notify_policy="state_changes",
        created_at="2026-06-10T00:00:00+00:00",
        parent_task_id="mission-1",
        child_session_id="robot-1:task-1",
    )

    assert record.task_id == "mission-1:subtask-1"
    assert store.get("mission-1:subtask-1").status == "queued"

    updated = store.project_task_state(
        task_id="mission-1:subtask-1",
        requester_session_id="mission-1",
        owner_id="robot-1",
        command="搜索二楼",
        runtime="robot_gateway",
        scope_kind="mission",
        status="completed",
        delivery_status="delivered",
        notify_policy="state_changes",
        created_at="2026-06-10T00:00:00+00:00",
        ended_at="2026-06-10T00:00:10+00:00",
        terminal_outcome="succeeded",
    )

    assert updated.status == "completed"
    assert store.get("mission-1:subtask-1").terminal_outcome == "succeeded"


def test_task_registry_project_preserves_existing_optional_fields(tmp_path):
    """project_task_state preserves existing optional fields when caller omits them."""
    store = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")

    store.project_task_state(
        task_id="t-proj-1",
        requester_session_id="s1",
        owner_id="r1",
        command="搜索",
        runtime="robot_gateway",
        scope_kind="mission",
        status="queued",
        delivery_status="pending",
        notify_policy="state_changes",
        created_at="2026-06-10T00:00:00+00:00",
        parent_task_id="mission-1",
        child_session_id="robot-1:task-1",
    )

    # Update without providing parent_task_id or child_session_id
    updated = store.project_task_state(
        task_id="t-proj-1",
        requester_session_id="s1",
        owner_id="r1",
        command="搜索",
        runtime="robot_gateway",
        scope_kind="mission",
        status="running",
        delivery_status="delivered",
        notify_policy="state_changes",
        created_at="2026-06-10T00:00:00+00:00",
        started_at="2026-06-10T00:00:01+00:00",
    )

    assert updated.parent_task_id == "mission-1"
    assert updated.child_session_id == "robot-1:task-1"
    assert updated.started_at == "2026-06-10T00:00:01+00:00"


def test_task_registry_project_preserves_created_at_from_existing_record(tmp_path):
    """project_task_state preserves created_at from existing record."""
    store = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")

    store.project_task_state(
        task_id="t-proj-2",
        requester_session_id="s1",
        owner_id="r1",
        command="搜索",
        runtime="robot_gateway",
        scope_kind="mission",
        status="queued",
        delivery_status="pending",
        notify_policy="state_changes",
        created_at="2026-06-10T00:00:00+00:00",
    )

    # Call again with a different created_at -- existing one should be preserved
    updated = store.project_task_state(
        task_id="t-proj-2",
        requester_session_id="s1",
        owner_id="r1",
        command="搜索",
        runtime="robot_gateway",
        scope_kind="mission",
        status="completed",
        delivery_status="delivered",
        notify_policy="state_changes",
        created_at="2026-06-10T99:99:99+00:00",
        ended_at="2026-06-10T00:00:10+00:00",
    )

    assert updated.created_at == "2026-06-10T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Conversion from existing queue record
# ---------------------------------------------------------------------------


def test_queue_record_to_task_record_basic():
    queue_rec = TaskQueueRecord(
        task_id="task-1",
        session_id="session-1",
        command="去二楼救人",
        status="running",
        created_at="2026-06-09T00:00:00+00:00",
        started_at="2026-06-09T00:00:01+00:00",
    )

    task_rec = queue_record_to_task_record(queue_rec, owner_id="robot-a")

    assert task_rec.task_id == "task-1"
    assert task_rec.runtime == "robot_gateway"
    assert task_rec.requester_session_id == "session-1"
    assert task_rec.owner_id == "robot-a"
    assert task_rec.scope_kind == "mission"
    assert task_rec.command == "去二楼救人"
    assert task_rec.status == "running"
    assert task_rec.created_at == "2026-06-09T00:00:00+00:00"
    assert task_rec.started_at == "2026-06-09T00:00:01+00:00"
    assert task_rec.ended_at is None
    assert task_rec.dedupe_key is None
    assert task_rec.error is None
    assert task_rec.result is None


def test_queue_record_to_task_record_preserves_all_fields():
    queue_rec = TaskQueueRecord(
        task_id="task-2",
        session_id="mission-7",
        command="搜索三楼",
        status="completed",
        created_at="2026-06-09T00:00:00+00:00",
        started_at="2026-06-09T00:00:01+00:00",
        ended_at="2026-06-09T00:00:05+00:00",
        dedupe_key="retry-abc",
        error=None,
        result={"rooms_searched": 3},
    )

    task_rec = queue_record_to_task_record(
        queue_rec, owner_id="robot-b", runtime="mission_gateway",
    )

    assert task_rec.runtime == "mission_gateway"
    assert task_rec.owner_id == "robot-b"
    assert task_rec.dedupe_key == "retry-abc"
    assert task_rec.result == {"rooms_searched": 3}
    assert task_rec.ended_at == "2026-06-09T00:00:05+00:00"


def test_queue_record_to_task_record_failed_status():
    queue_rec = TaskQueueRecord(
        task_id="task-3",
        session_id="session-1",
        command="进入火场",
        status="failed",
        created_at="2026-06-09T00:00:00+00:00",
        ended_at="2026-06-09T00:00:02+00:00",
        error="Navigation blocked by debris",
    )

    task_rec = queue_record_to_task_record(queue_rec, owner_id="robot-a")

    assert task_rec.status == "failed"
    assert task_rec.error == "Navigation blocked by debris"
    assert task_rec.is_terminal
