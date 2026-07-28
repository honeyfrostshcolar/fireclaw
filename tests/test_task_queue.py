import json

import pytest

from fireclaw_core.task.task_queue import JsonlTaskQueue, TaskQueueRecord


def test_task_queue_appends_and_updates_records(tmp_path):
    queue = JsonlTaskQueue(tmp_path / "tasks.jsonl")

    record = queue.create(
        task_id="task-1",
        session_id="session-1",
        command="去二楼救人",
        created_at="2026-06-08T01:00:00+00:00",
        dedupe_key="retry-1",
    )
    queue.update(
        "task-1",
        status="running",
        started_at="2026-06-08T01:00:01+00:00",
    )
    queue.update(
        "task-1",
        status="completed",
        ended_at="2026-06-08T01:00:02+00:00",
        result={"status": "succeeded"},
    )

    loaded = queue.get("task-1")

    assert record.status == "accepted"
    assert loaded == TaskQueueRecord(
        task_id="task-1",
        session_id="session-1",
        command="去二楼救人",
        status="completed",
        created_at="2026-06-08T01:00:00+00:00",
        started_at="2026-06-08T01:00:01+00:00",
        ended_at="2026-06-08T01:00:02+00:00",
        dedupe_key="retry-1",
        error=None,
        result={"status": "succeeded"},
    )


def test_task_queue_finds_non_terminal_dedupe_record(tmp_path):
    queue = JsonlTaskQueue(tmp_path / "tasks.jsonl")
    queue.create(
        task_id="task-1",
        session_id="session-1",
        command="去二楼救人",
        created_at="2026-06-08T01:00:00+00:00",
        dedupe_key="retry-1",
    )
    queue.create(
        task_id="task-2",
        session_id="session-1",
        command="去三楼救人",
        created_at="2026-06-08T01:00:01+00:00",
        dedupe_key="retry-2",
    )
    queue.update("task-2", status="completed", ended_at="2026-06-08T01:00:02+00:00")

    assert queue.find_non_terminal_by_dedupe_key("retry-1").task_id == "task-1"
    assert queue.find_non_terminal_by_dedupe_key("retry-2") is None
    assert queue.find_non_terminal_by_dedupe_key(None) is None


def test_task_queue_marks_non_terminal_records_lost(tmp_path):
    queue = JsonlTaskQueue(tmp_path / "tasks.jsonl")
    queue.create(
        task_id="task-running",
        session_id="session-1",
        command="去二楼救人",
        created_at="2026-06-08T01:00:00+00:00",
    )
    queue.update("task-running", status="running", started_at="2026-06-08T01:00:01+00:00")
    queue.create(
        task_id="task-done",
        session_id="session-1",
        command="回安全区",
        created_at="2026-06-08T01:00:02+00:00",
    )
    queue.update("task-done", status="completed", ended_at="2026-06-08T01:00:03+00:00")

    lost = queue.mark_non_terminal_lost(
        ended_at="2026-06-08T01:00:04+00:00",
        error="Gateway restarted before terminal result.",
    )

    assert [record.task_id for record in lost] == ["task-running"]
    assert queue.get("task-running").status == "lost"
    assert queue.get("task-running").error == "Gateway restarted before terminal result."
    assert queue.get("task-done").status == "completed"


def test_task_queue_ignores_uncommitted_trailing_record(tmp_path):
    path = tmp_path / "tasks.jsonl"
    queue = JsonlTaskQueue(path)
    queue.create(
        task_id="task-complete",
        session_id="session-1",
        command="return to staging",
        created_at="2026-06-08T01:00:00+00:00",
    )
    with path.open("ab") as handle:
        handle.write(b'{"task_id":"task-truncated')

    assert [record.task_id for record in queue.list_records()] == ["task-complete"]

    queue.create(
        task_id="task-after-recovery",
        session_id="session-1",
        command="resume",
        created_at="2026-06-08T01:00:01+00:00",
    )

    assert [record.task_id for record in queue.list_records()] == [
        "task-complete",
        "task-after-recovery",
    ]


def test_task_queue_rejects_corruption_before_final_record(tmp_path):
    path = tmp_path / "tasks.jsonl"
    path.write_text(
        '{"task_id":"task-1","session_id":"s","command":"hold",'
        '"status":"completed","created_at":"2026-06-08T01:00:00+00:00"}\n'
        '{"task_id":broken}\n'
        '{"task_id":"task-2","session_id":"s","command":"return",'
        '"status":"completed","created_at":"2026-06-08T01:00:01+00:00"}\n',
        encoding="utf-8",
    )

    with pytest.raises(json.JSONDecodeError):
        JsonlTaskQueue(path).list_records()
