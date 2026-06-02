from fireclaw_core.memory import JsonlMemoryStore


def test_jsonl_memory_store_appends_and_reads_records(tmp_path):
    memory_path = tmp_path / "runs" / "memory.jsonl"
    store = JsonlMemoryStore(memory_path)

    store.append({"command": "去二楼救人", "status": "succeeded", "dry_run": True})

    assert store.list_records() == [
        {"command": "去二楼救人", "status": "succeeded", "dry_run": True}
    ]
    assert memory_path.read_text(encoding="utf-8").endswith("\n")


def test_jsonl_memory_store_returns_empty_list_for_missing_file(tmp_path):
    store = JsonlMemoryStore(tmp_path / "missing.jsonl")

    assert store.list_records() == []


def test_jsonl_memory_store_returns_latest_records(tmp_path):
    store = JsonlMemoryStore(tmp_path / "memory.jsonl")
    store.append({"command": "task-1"})
    store.append({"command": "task-2"})
    store.append({"command": "task-3"})

    assert store.latest_records(limit=2) == [
        {"command": "task-2"},
        {"command": "task-3"},
    ]


def test_jsonl_memory_store_returns_latest_records_for_session(tmp_path):
    store = JsonlMemoryStore(tmp_path / "memory.jsonl")
    store.append({"command": "a-1", "session": {"session_id": "session-a"}})
    store.append({"command": "b-1", "session": {"session_id": "session-b"}})
    store.append({"command": "a-2", "session": {"session_id": "session-a"}})

    assert store.latest_records(limit=5, session_id="session-a") == [
        {"command": "a-1", "session": {"session_id": "session-a"}},
        {"command": "a-2", "session": {"session_id": "session-a"}},
    ]


def test_jsonl_memory_store_searches_structured_records(tmp_path):
    store = JsonlMemoryStore(tmp_path / "memory.jsonl")
    store.append(
        {
            "command": "去二楼救人",
            "status": "succeeded",
            "session": {"session_id": "session-a"},
            "planning": {"intent": "rescue_victim", "target_floor": 2},
        }
    )
    store.append(
        {
            "command": "去三楼救人",
            "status": "failed",
            "session": {"session_id": "session-a"},
            "planning": {"intent": "rescue_victim", "target_floor": 3},
        }
    )
    store.append(
        {
            "command": "去二楼巡检",
            "status": "succeeded",
            "session": {"session_id": "session-b"},
            "planning": {"intent": "inspect", "target_floor": 2},
        }
    )

    assert store.search_records(
        session_id="session-a",
        status="succeeded",
        intent="rescue_victim",
        target_floor=2,
        command_contains="救人",
        limit=5,
    ) == [
        {
            "command": "去二楼救人",
            "status": "succeeded",
            "session": {"session_id": "session-a"},
            "planning": {"intent": "rescue_victim", "target_floor": 2},
        }
    ]


def test_jsonl_memory_store_search_returns_newest_matches_first(tmp_path):
    store = JsonlMemoryStore(tmp_path / "memory.jsonl")
    store.append({"command": "first", "status": "failed"})
    store.append({"command": "second", "status": "failed"})
    store.append({"command": "third", "status": "failed"})

    assert [record["command"] for record in store.search_records(status="failed", limit=2)] == [
        "third",
        "second",
    ]
