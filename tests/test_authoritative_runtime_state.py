import sqlite3
import stat

import pytest

from fireclaw_core.infra.runtime_state import (
    SqliteAuthoritativeRuntimeStore,
    SqliteEventLedger,
    SqliteTaskQueue,
    StaleRuntimeStateWrite,
)


def _runtime(tmp_path):
    store = SqliteAuthoritativeRuntimeStore(tmp_path / "runtime.sqlite3")
    queue = SqliteTaskQueue(store, audit_path=tmp_path / "tasks.jsonl")
    events = SqliteEventLedger(store, audit_path=tmp_path / "events.jsonl")
    return store, queue, events


def test_runtime_store_uses_wal_and_full_synchronous_mode(tmp_path):
    store, _, _ = _runtime(tmp_path)

    with sqlite3.connect(store.path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]

    assert journal_mode.lower() == "wal"
    assert synchronous == 2
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_task_and_event_rollback_together_without_audit_mirror(tmp_path):
    store, queue, events = _runtime(tmp_path)

    with pytest.raises(RuntimeError, match="simulated crash"):
        with store.transaction():
            queue.create(
                task_id="task-1",
                session_id="mission-1",
                command="hold position",
                created_at="2026-07-29T10:00:00+00:00",
            )
            events.append(
                task_id="task-1",
                session_id="mission-1",
                type="task.received",
                payload={"command": "hold position"},
            )
            raise RuntimeError("simulated crash")

    assert queue.get("task-1") is None
    assert events.list_events() == []
    assert not (tmp_path / "tasks.jsonl").exists()
    assert not (tmp_path / "events.jsonl").exists()


def test_task_update_rejects_stale_revision_and_terminal_overwrite(tmp_path):
    _, queue, _ = _runtime(tmp_path)
    queue.create(
        task_id="task-1",
        session_id="mission-1",
        command="inspect corridor",
        created_at="2026-07-29T10:00:00+00:00",
    )
    queue.update("task-1", status="running", expected_revision=1)

    with pytest.raises(StaleRuntimeStateWrite):
        queue.update("task-1", status="cancel_requested", expected_revision=1)

    queue.update(
        "task-1",
        status="completed",
        ended_at="2026-07-29T10:01:00+00:00",
        expected_revision=2,
    )
    with pytest.raises(StaleRuntimeStateWrite):
        queue.update("task-1", status="failed")


def test_runtime_secret_and_state_survive_store_reconstruction(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    first = SqliteAuthoritativeRuntimeStore(path)
    first_secret = first.get_or_create_secret("execution_authorization_hmac")
    first.set_flag("resource_admission", {"closed": True, "reason": "test"})

    second = SqliteAuthoritativeRuntimeStore(path)

    assert second.get_or_create_secret("execution_authorization_hmac") == first_secret
    assert second.get_flag("resource_admission") == {
        "closed": True,
        "reason": "test",
    }


def test_execution_authorization_operation_is_consumed_once(tmp_path):
    store = SqliteAuthoritativeRuntimeStore(tmp_path / "runtime.sqlite3")
    store.persist_execution_authorization(
        {"authorization_id": "exec-auth-1"}
    )

    first = store.record_authorization_use(
        authorization_id="exec-auth-1",
        operation_id="exec-auth-1:step:1",
        action_hash="action-a",
        used_at="2026-07-29T10:00:00+00:00",
    )
    replay = store.record_authorization_use(
        authorization_id="exec-auth-1",
        operation_id="exec-auth-1:step:1",
        action_hash="action-a",
        used_at="2026-07-29T10:00:01+00:00",
    )
    next_step = store.record_authorization_use(
        authorization_id="exec-auth-1",
        operation_id="exec-auth-1:step:2",
        action_hash="action-b",
        used_at="2026-07-29T10:00:02+00:00",
    )

    assert first is True
    assert replay is False
    assert next_step is True
