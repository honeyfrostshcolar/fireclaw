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
