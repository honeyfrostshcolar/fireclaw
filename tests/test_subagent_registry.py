"""Tests for SubagentRunRecord and JsonlSubagentRegistry."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fireclaw_core.subagent.subagent_registry import (
    JsonlSubagentRegistry,
    SubagentRunRecord,
)


# ---------------------------------------------------------------------------
# SubagentRunRecord dataclass tests
# ---------------------------------------------------------------------------


def test_subagent_run_record_creation():
    record = SubagentRunRecord(
        run_id="run-001",
        parent_mission_id="mission-alpha",
        parent_subtask_id="subtask-3",
        robot_id="robot-a",
        child_task_id="task-child-1",
        status="dispatched",
        delivery_status="pending",
        created_at="2026-06-09T12:00:00+00:00",
        updated_at=None,
        error=None,
    )

    assert record.run_id == "run-001"
    assert record.parent_mission_id == "mission-alpha"
    assert record.parent_subtask_id == "subtask-3"
    assert record.robot_id == "robot-a"
    assert record.child_task_id == "task-child-1"
    assert record.status == "dispatched"
    assert record.delivery_status == "pending"
    assert record.created_at == "2026-06-09T12:00:00+00:00"
    assert record.updated_at is None
    assert record.error is None


def test_subagent_run_record_creation_minimal():
    """parent_subtask_id, updated_at, error should default to None."""
    record = SubagentRunRecord(
        run_id="run-002",
        parent_mission_id="mission-beta",
        robot_id="robot-b",
        child_task_id="task-child-2",
        status="dispatched",
        delivery_status="pending",
        created_at="2026-06-09T12:00:00+00:00",
    )

    assert record.parent_subtask_id is None
    assert record.updated_at is None
    assert record.error is None


def test_subagent_run_record_is_terminal():
    """Terminal statuses: completed, failed, cancelled, timed_out, lost."""
    terminal_statuses = ("completed", "failed", "cancelled", "timed_out", "lost")
    for status in terminal_statuses:
        record = SubagentRunRecord(
            run_id=f"run-{status}",
            parent_mission_id="m1",
            robot_id="r1",
            child_task_id=f"t-{status}",
            status=status,
            delivery_status="delivered",
            created_at="2026-06-09T12:00:00+00:00",
        )
        assert record.is_terminal, f"Status '{status}' should be terminal"

    non_terminal_statuses = ("dispatched", "accepted", "running")
    for status in non_terminal_statuses:
        record = SubagentRunRecord(
            run_id=f"run-{status}",
            parent_mission_id="m1",
            robot_id="r1",
            child_task_id=f"t-{status}",
            status=status,
            delivery_status="pending",
            created_at="2026-06-09T12:00:00+00:00",
        )
        assert not record.is_terminal, f"Status '{status}' should not be terminal"


def test_subagent_run_record_to_dict_from_dict():
    record = SubagentRunRecord(
        run_id="run-rt",
        parent_mission_id="mission-rt",
        parent_subtask_id="sub-rt",
        robot_id="robot-rt",
        child_task_id="child-rt",
        status="running",
        delivery_status="delivered",
        created_at="2026-06-09T12:00:00+00:00",
        updated_at="2026-06-09T12:05:00+00:00",
        error=None,
    )

    d = record.to_dict()
    assert d["run_id"] == "run-rt"
    assert d["parent_mission_id"] == "mission-rt"
    assert d["status"] == "running"
    assert d["delivery_status"] == "delivered"
    assert d["updated_at"] == "2026-06-09T12:05:00+00:00"
    assert d["error"] is None

    restored = SubagentRunRecord.from_dict(d)
    assert restored == record


def test_subagent_run_record_from_dict_handles_missing_optionals():
    """from_dict should tolerate missing optional fields."""
    d = {
        "run_id": "run-min",
        "parent_mission_id": "m-min",
        "robot_id": "r-min",
        "child_task_id": "t-min",
        "status": "dispatched",
        "delivery_status": "pending",
        "created_at": "2026-06-09T12:00:00+00:00",
    }
    record = SubagentRunRecord.from_dict(d)
    assert record.run_id == "run-min"
    assert record.parent_subtask_id is None
    assert record.updated_at is None
    assert record.error is None


# ---------------------------------------------------------------------------
# JsonlSubagentRegistry tests
# ---------------------------------------------------------------------------


def test_registry_create_and_get(tmp_path):
    store = JsonlSubagentRegistry(tmp_path / "registry.jsonl")

    record = store.create(
        parent_mission_id="mission-1",
        parent_subtask_id="sub-1",
        robot_id="robot-a",
        child_task_id="child-task-1",
        created_at="2026-06-09T12:00:00+00:00",
    )

    assert record.status == "dispatched"
    assert record.delivery_status == "pending"
    assert record.parent_mission_id == "mission-1"
    assert record.run_id  # auto-generated UUID

    # Get by run_id
    loaded = store.get_by_run_id(record.run_id)
    assert loaded == record

    # Get by child_task_id
    loaded_by_child = store.get_by_child_task_id("child-task-1")
    assert loaded_by_child == record


def test_registry_create_without_subtask(tmp_path):
    store = JsonlSubagentRegistry(tmp_path / "registry.jsonl")

    record = store.create(
        parent_mission_id="mission-2",
        robot_id="robot-b",
        child_task_id="child-task-2",
        created_at="2026-06-09T12:00:00+00:00",
    )

    assert record.parent_subtask_id is None
    loaded = store.get_by_child_task_id("child-task-2")
    assert loaded is not None
    assert loaded.parent_subtask_id is None


def test_registry_update_status(tmp_path):
    store = JsonlSubagentRegistry(tmp_path / "registry.jsonl")
    store.create(
        parent_mission_id="mission-u",
        robot_id="robot-u",
        child_task_id="child-u",
        created_at="2026-06-09T12:00:00+00:00",
    )

    record = store.get_by_child_task_id("child-u")
    assert record is not None

    updated = store.update(
        record.run_id,
        status="running",
        delivery_status="delivered",
        updated_at="2026-06-09T12:01:00+00:00",
    )

    assert updated.status == "running"
    assert updated.delivery_status == "delivered"
    assert updated.updated_at == "2026-06-09T12:01:00+00:00"
    assert updated.parent_mission_id == "mission-u"  # preserved

    # Verify get returns the latest
    loaded = store.get_by_run_id(record.run_id)
    assert loaded == updated


def test_registry_update_error(tmp_path):
    store = JsonlSubagentRegistry(tmp_path / "registry.jsonl")
    rec = store.create(
        parent_mission_id="mission-e",
        robot_id="robot-e",
        child_task_id="child-e",
        created_at="2026-06-09T12:00:00+00:00",
    )

    updated = store.update(
        rec.run_id,
        status="failed",
        updated_at="2026-06-09T12:10:00+00:00",
        error="Navigation blocked by debris",
    )

    assert updated.status == "failed"
    assert updated.error == "Navigation blocked by debris"

    loaded = store.get_by_run_id(rec.run_id)
    assert loaded is not None
    assert loaded.error == "Navigation blocked by debris"


def test_registry_update_missing_raises(tmp_path):
    store = JsonlSubagentRegistry(tmp_path / "registry.jsonl")

    try:
        store.update("nonexistent-run", status="running")
        assert False, "Should have raised KeyError"
    except KeyError:
        pass


def test_registry_list_records(tmp_path):
    store = JsonlSubagentRegistry(tmp_path / "registry.jsonl")
    store.create(
        parent_mission_id="mission-1",
        robot_id="robot-a",
        child_task_id="child-1",
        created_at="2026-06-09T12:00:00+00:00",
    )
    store.create(
        parent_mission_id="mission-2",
        robot_id="robot-b",
        child_task_id="child-2",
        created_at="2026-06-09T12:01:00+00:00",
    )

    records = store.list_records()
    assert len(records) == 2
    ids = {r.child_task_id for r in records}
    assert ids == {"child-1", "child-2"}


def test_registry_list_by_parent_mission(tmp_path):
    store = JsonlSubagentRegistry(tmp_path / "registry.jsonl")
    store.create(
        parent_mission_id="mission-alpha",
        robot_id="robot-a",
        child_task_id="child-a1",
        created_at="2026-06-09T12:00:00+00:00",
    )
    store.create(
        parent_mission_id="mission-alpha",
        robot_id="robot-b",
        child_task_id="child-a2",
        created_at="2026-06-09T12:01:00+00:00",
    )
    store.create(
        parent_mission_id="mission-beta",
        robot_id="robot-c",
        child_task_id="child-b1",
        created_at="2026-06-09T12:02:00+00:00",
    )

    alpha_records = store.list_by_parent_mission("mission-alpha")
    assert len(alpha_records) == 2
    assert {r.child_task_id for r in alpha_records} == {"child-a1", "child-a2"}

    beta_records = store.list_by_parent_mission("mission-beta")
    assert len(beta_records) == 1
    assert beta_records[0].child_task_id == "child-b1"

    # Nonexistent mission
    gamma_records = store.list_by_parent_mission("mission-gamma")
    assert gamma_records == []


def test_registry_corrupt_line_tolerance(tmp_path):
    path = tmp_path / "registry.jsonl"
    path.write_text(
        '{"run_id": "r1", "parent_mission_id": "m1", "robot_id": "ro1", '
        '"child_task_id": "c1", "status": "dispatched", "delivery_status": "pending", '
        '"created_at": "2026-06-09T12:00:00+00:00"}\n'
        "NOT JSON\n"
        "123\n"
        '{"run_id": "r2", "parent_mission_id": "m2", "robot_id": "ro2", '
        '"child_task_id": "c2", "status": "dispatched", "delivery_status": "pending", '
        '"created_at": "2026-06-09T12:01:00+00:00"}\n',
        encoding="utf-8",
    )

    store = JsonlSubagentRegistry(path)
    records = store.list_records()
    assert len(records) == 2
    assert records[0].run_id == "r1"
    assert records[1].run_id == "r2"


def test_registry_get_missing_returns_none(tmp_path):
    store = JsonlSubagentRegistry(tmp_path / "registry.jsonl")
    assert store.get_by_run_id("nonexistent") is None
    assert store.get_by_child_task_id("nonexistent") is None


# ---------------------------------------------------------------------------
# mark_terminal tests
# ---------------------------------------------------------------------------


def test_subagent_registry_mark_terminal_is_idempotent(tmp_path):
    store = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")
    record = store.create(
        parent_mission_id="mission-1",
        parent_subtask_id="subtask-1",
        robot_id="robot-1",
        child_task_id="task-1",
        created_at="2026-06-10T00:00:00+00:00",
    )

    first = store.mark_terminal(
        child_task_id="task-1",
        status="completed",
        updated_at="2026-06-10T00:00:10+00:00",
    )
    second = store.mark_terminal(
        child_task_id="task-1",
        status="failed",
        updated_at="2026-06-10T00:00:11+00:00",
    )

    assert first.status == "completed"
    assert second.status == "completed"  # idempotent -- does not overwrite terminal
    assert store.get_by_run_id(record.run_id).status == "completed"


def test_subagent_registry_mark_terminal_returns_none_for_unknown(tmp_path):
    store = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")

    result = store.mark_terminal(
        child_task_id="nonexistent",
        status="completed",
        updated_at="2026-06-10T00:00:10+00:00",
    )

    assert result is None


def test_subagent_registry_mark_terminal_sets_error(tmp_path):
    store = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")
    store.create(
        parent_mission_id="mission-1",
        robot_id="robot-1",
        child_task_id="task-err",
        created_at="2026-06-10T00:00:00+00:00",
    )

    result = store.mark_terminal(
        child_task_id="task-err",
        status="failed",
        updated_at="2026-06-10T00:00:10+00:00",
        error="Navigation blocked",
    )

    assert result is not None
    assert result.status == "failed"
    assert result.error == "Navigation blocked"
    assert result.delivery_status == "delivered"


# ---------------------------------------------------------------------------
# RobotSubagentClient integration tests
# ---------------------------------------------------------------------------


def test_subagent_client_records_to_registry_when_configured(tmp_path):
    """When registry is wired in, submit_task creates a record and cancel_task updates it."""
    from fireclaw_core.agent.robot_registry import RobotRegistryEntry
    from fireclaw_core.subagent.subagent_client import RobotSubagentClient

    registry_path = tmp_path / "subagent_registry.jsonl"
    registry = JsonlSubagentRegistry(registry_path)
    client = RobotSubagentClient(registry=registry)
    entry = RobotRegistryEntry(robot_id="robot-test", base_url="http://localhost:9999")

    # Patch _request_json to avoid actual HTTP calls
    with patch.object(client, "_request_json", return_value={
        "task_id": "remote-task-1",
        "status": "accepted",
    }) as mock_req:
        result = client.submit_task(
            entry,
            command="搜索二楼",
            mission={"mission_id": "mission-x", "subtask_id": "sub-x"},
        )

    assert result["task_id"] == "remote-task-1"

    # Verify registry was written
    records = registry.list_records()
    assert len(records) == 1
    rec = records[0]
    assert rec.parent_mission_id == "mission-x"
    assert rec.parent_subtask_id == "sub-x"
    assert rec.robot_id == "robot-test"
    assert rec.child_task_id == "remote-task-1"
    assert rec.status == "dispatched"
    assert rec.delivery_status == "pending"

    # Now cancel
    with patch.object(client, "_request_json", return_value={
        "task_id": "remote-task-1",
        "status": "cancelled",
    }):
        client.cancel_task(entry, "remote-task-1")

    # Verify registry updated
    updated = registry.get_by_run_id(rec.run_id)
    assert updated is not None
    assert updated.status == "cancelled"


def test_subagent_client_does_not_treat_cancel_request_as_terminal(tmp_path):
    from fireclaw_core.agent.robot_registry import RobotRegistryEntry
    from fireclaw_core.subagent.subagent_client import RobotSubagentClient

    registry = JsonlSubagentRegistry(tmp_path / "subagent_registry.jsonl")
    client = RobotSubagentClient(registry=registry)
    entry = RobotRegistryEntry(
        robot_id="robot-test",
        base_url="http://localhost:9999",
    )
    with patch.object(
        client,
        "_request_json",
        return_value={"task_id": "remote-task-1", "status": "accepted"},
    ):
        client.submit_task(
            entry,
            command="导航到入口",
            mission={"mission_id": "mission-cancel"},
        )

    with patch.object(
        client,
        "_request_json",
        return_value={
            "task_id": "remote-task-1",
            "status": "cancel_requested",
        },
    ):
        client.cancel_task(entry, "remote-task-1")

    record = registry.get_by_child_task_id("remote-task-1")
    assert record is not None
    assert record.status == "cancel_requested"


def test_subagent_client_no_registry_is_noop():
    """Without a registry, client methods still work normally."""
    from fireclaw_core.agent.robot_registry import RobotRegistryEntry
    from fireclaw_core.subagent.subagent_client import RobotSubagentClient

    client = RobotSubagentClient()  # No registry
    entry = RobotRegistryEntry(robot_id="robot-2", base_url="http://localhost:9999")

    with patch.object(client, "_request_json", return_value={
        "task_id": "remote-task-2",
        "status": "accepted",
    }):
        result = client.submit_task(entry, command="test")
        assert result["task_id"] == "remote-task-2"


def test_subagent_client_get_trace_updates_existing_registry_record_on_terminal_status(tmp_path):
    """Observed terminal trace should update an existing child run mapping."""
    from fireclaw_core.agent.robot_registry import RobotRegistryEntry
    from fireclaw_core.subagent.subagent_client import RobotSubagentClient

    registry = JsonlSubagentRegistry(tmp_path / "subagent_registry.jsonl")
    client = RobotSubagentClient(registry=registry)
    entry = RobotRegistryEntry(robot_id="robot-3", base_url="http://localhost:9999")
    record = registry.create(
        parent_mission_id="mission-1",
        parent_subtask_id="subtask-1",
        robot_id="robot-3",
        child_task_id="remote-task-3",
        created_at="2026-06-10T00:00:00+00:00",
    )

    with patch.object(client, "_request_json", return_value={
        "task_id": "remote-task-3",
        "status": "completed",
    }):
        client.get_task_trace(entry, "remote-task-3")

    updated = registry.get_by_run_id(record.run_id)
    assert updated is not None
    assert updated.status == "completed"
    assert updated.delivery_status == "delivered"
    assert updated.updated_at is not None


def test_subagent_client_get_trace_without_existing_mapping_does_not_create_registry_record(tmp_path):
    """Trace observation should not invent lineage when no child mapping exists."""
    from fireclaw_core.agent.robot_registry import RobotRegistryEntry
    from fireclaw_core.subagent.subagent_client import RobotSubagentClient

    registry = JsonlSubagentRegistry(tmp_path / "subagent_registry.jsonl")
    client = RobotSubagentClient(registry=registry)
    entry = RobotRegistryEntry(robot_id="robot-3", base_url="http://localhost:9999")

    with patch.object(client, "_request_json", return_value={
        "task_id": "remote-task-3",
        "status": "completed",
    }):
        client.get_task_trace(entry, "remote-task-3")

    assert registry.list_records() == []
