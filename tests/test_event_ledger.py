from fireclaw_core.monitoring.event_ledger import EventLedger


def test_event_ledger_appends_and_lists_events(tmp_path):
    ledger = EventLedger(tmp_path / "events.jsonl")

    event = ledger.append(
        task_id="task-1",
        session_id="session-a",
        type="task.received",
        payload={"command": "去二楼救人"},
    )

    events = ledger.list_events()
    assert event["event_id"].startswith("evt-")
    assert event["task_id"] == "task-1"
    assert event["session_id"] == "session-a"
    assert event["type"] == "task.received"
    assert events == [event]


def test_event_ledger_filters_events_for_task(tmp_path):
    ledger = EventLedger(tmp_path / "events.jsonl")
    ledger.append(task_id="task-1", session_id="session-a", type="task.received", payload={})
    ledger.append(task_id="task-2", session_id="session-a", type="task.received", payload={})
    ledger.append(task_id="task-1", session_id="session-a", type="task.completed", payload={})

    events = ledger.events_for_task("task-1")

    assert [event["type"] for event in events] == ["task.received", "task.completed"]


def test_event_ledger_returns_recent_events_by_session_newest_first(tmp_path):
    ledger = EventLedger(tmp_path / "events.jsonl")
    ledger.append(task_id="task-1", session_id="session-a", type="task.received", payload={})
    ledger.append(task_id="task-2", session_id="session-b", type="task.received", payload={})
    ledger.append(task_id="task-3", session_id="session-a", type="task.completed", payload={})

    events = ledger.latest_events(limit=2, session_id="session-a")

    assert [event["task_id"] for event in events] == ["task-3", "task-1"]


def test_event_ledger_returns_empty_list_for_missing_file(tmp_path):
    ledger = EventLedger(tmp_path / "missing.jsonl")

    assert ledger.list_events() == []
    assert ledger.latest_events(limit=5) == []
    assert ledger.events_for_task("task-1") == []
