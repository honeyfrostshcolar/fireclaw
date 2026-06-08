from __future__ import annotations

import pytest

from fireclaw_core.mission_memory import (
    MEMORY_RECORD_TYPES,
    MissionMemoryRecord,
    MissionMemoryStore,
)


def _make_record(
    record_id: str = "mem-1",
    mission_id: str = "m-1",
    record_type: str = "outcome",
    content: dict | None = None,
    robot_id: str | None = None,
    subtask_id: str | None = None,
    created_at: str = "2026-06-08T12:00:00Z",
) -> MissionMemoryRecord:
    return MissionMemoryRecord(
        record_id=record_id,
        mission_id=mission_id,
        record_type=record_type,
        content=content or {"status": "succeeded"},
        robot_id=robot_id,
        subtask_id=subtask_id,
        created_at=created_at,
    )


def test_mission_memory_store_appends_and_lists(tmp_path):
    store = MissionMemoryStore(tmp_path / "mission_memory.jsonl")
    record = _make_record()
    store.append(record)
    records = store.list_records()
    assert len(records) == 1
    assert records[0].record_type == "outcome"
    assert records[0].record_id == "mem-1"
    assert records[0].content == {"status": "succeeded"}


def test_mission_memory_store_rejects_invalid_type(tmp_path):
    store = MissionMemoryStore(tmp_path / "mission_memory.jsonl")
    with pytest.raises(ValueError, match="Invalid record type"):
        store.append(_make_record(record_type="invalid"))


def test_mission_memory_store_filters_by_mission_and_type(tmp_path):
    store = MissionMemoryStore(tmp_path / "mission_memory.jsonl")
    store.append(_make_record(record_id="mem-1", mission_id="m-1", record_type="outcome"))
    store.append(_make_record(record_id="mem-2", mission_id="m-1", record_type="lesson"))
    store.append(_make_record(record_id="mem-3", mission_id="m-2", record_type="outcome"))
    store.append(_make_record(record_id="mem-4", mission_id="m-2", record_type="observation"))

    # Filter by mission_id only
    m1 = store.list_records(mission_id="m-1")
    assert len(m1) == 2
    assert {r.record_id for r in m1} == {"mem-1", "mem-2"}

    # Filter by record_type only
    outcomes = store.list_records(record_type="outcome")
    assert len(outcomes) == 2
    assert {r.record_id for r in outcomes} == {"mem-1", "mem-3"}

    # Filter by both
    m2_observations = store.list_records(mission_id="m-2", record_type="observation")
    assert len(m2_observations) == 1
    assert m2_observations[0].record_id == "mem-4"


def test_mission_memory_store_search_by_keyword(tmp_path):
    store = MissionMemoryStore(tmp_path / "mission_memory.jsonl")
    store.append(_make_record(
        record_id="mem-1", mission_id="m-1", record_type="outcome",
        content={"status": "succeeded", "note": "rescued survivor on floor 2"},
    ))
    store.append(_make_record(
        record_id="mem-2", mission_id="m-1", record_type="observation",
        content={"location": "floor 3", "detail": "heavy smoke detected"},
    ))
    store.append(_make_record(
        record_id="mem-3", mission_id="m-1", record_type="lesson",
        content={"lesson": "always check stairwell first"},
    ))

    # Keyword matching in content values
    results = store.search(keyword="smoke")
    assert len(results) == 1
    assert results[0].record_id == "mem-2"

    results = store.search(keyword="floor")
    assert len(results) == 2
    assert {r.record_id for r in results} == {"mem-1", "mem-2"}

    # No match
    results = store.search(keyword="nonexistent")
    assert len(results) == 0


def test_mission_memory_store_search_by_robot_id(tmp_path):
    store = MissionMemoryStore(tmp_path / "mission_memory.jsonl")
    store.append(_make_record(record_id="mem-1", mission_id="m-1", robot_id="robot-1"))
    store.append(_make_record(record_id="mem-2", mission_id="m-1", robot_id="robot-2"))
    store.append(_make_record(record_id="mem-3", mission_id="m-1", robot_id="robot-1"))

    results = store.search(robot_id="robot-1")
    assert len(results) == 2
    assert {r.record_id for r in results} == {"mem-1", "mem-3"}

    results = store.search(robot_id="robot-2")
    assert len(results) == 1
    assert results[0].record_id == "mem-2"


def test_mission_memory_store_summary(tmp_path):
    store = MissionMemoryStore(tmp_path / "mission_memory.jsonl")
    store.append(_make_record(record_id="mem-1", mission_id="m-1", record_type="outcome"))
    store.append(_make_record(record_id="mem-2", mission_id="m-1", record_type="outcome"))
    store.append(_make_record(record_id="mem-3", mission_id="m-1", record_type="lesson"))
    store.append(_make_record(record_id="mem-4", mission_id="m-2", record_type="observation"))

    # Summary for all missions
    total = store.summary()
    assert total["total"] == 4
    assert total["by_type"]["outcome"] == 2
    assert total["by_type"]["lesson"] == 1
    assert total["by_type"]["observation"] == 1

    # Summary for one mission
    m1 = store.summary(mission_id="m-1")
    assert m1["total"] == 3
    assert m1["by_type"]["outcome"] == 2
    assert m1["by_type"]["lesson"] == 1


def test_mission_memory_store_empty_for_missing_file(tmp_path):
    store = MissionMemoryStore(tmp_path / "nonexistent.jsonl")
    assert store.list_records() == []
    assert store.search() == []
    s = store.summary()
    assert s["total"] == 0


def test_mission_memory_store_returns_newest_first(tmp_path):
    store = MissionMemoryStore(tmp_path / "mission_memory.jsonl")
    store.append(_make_record(
        record_id="mem-old", mission_id="m-1", record_type="outcome",
        content={"note": "first"}, created_at="2026-06-08T10:00:00Z",
    ))
    store.append(_make_record(
        record_id="mem-mid", mission_id="m-1", record_type="outcome",
        content={"note": "second"}, created_at="2026-06-08T11:00:00Z",
    ))
    store.append(_make_record(
        record_id="mem-new", mission_id="m-1", record_type="outcome",
        content={"note": "third"}, created_at="2026-06-08T12:00:00Z",
    ))

    results = store.search(limit=10)
    assert results[0].record_id == "mem-new"
    assert results[1].record_id == "mem-mid"
    assert results[2].record_id == "mem-old"


def test_mission_memory_record_types_constant():
    assert MEMORY_RECORD_TYPES == {"outcome", "observation", "correction", "lesson"}


def test_mission_memory_store_skips_corrupt_jsonl_lines(tmp_path):
    path = tmp_path / "mission_memory.jsonl"
    # Write one valid line, one corrupt line, one valid line
    path.write_text(
        '{"record_id":"m1","mission_id":"m-1","record_type":"outcome","content":{"status":"ok"},"robot_id":null,"subtask_id":null,"created_at":"2026-06-08T12:00:00Z"}\n'
        'NOT VALID JSON\n'
        '{"record_id":"m2","mission_id":"m-1","record_type":"lesson","content":{"note":"learned"},"robot_id":null,"subtask_id":null,"created_at":"2026-06-08T12:01:00Z"}\n',
        encoding="utf-8",
    )
    store = MissionMemoryStore(path)

    records = store.list_records()

    assert len(records) == 2
    assert records[0].record_id == "m1"
    assert records[1].record_id == "m2"


def test_mission_memory_record_to_dict():
    record = _make_record(
        record_id="mem-1",
        mission_id="m-1",
        record_type="outcome",
        content={"status": "ok"},
        robot_id="r-1",
        subtask_id="s-1",
        created_at="2026-06-08T12:00:00Z",
    )
    d = record.to_dict()
    assert d["record_id"] == "mem-1"
    assert d["mission_id"] == "m-1"
    assert d["record_type"] == "outcome"
    assert d["content"] == {"status": "ok"}
    assert d["robot_id"] == "r-1"
    assert d["subtask_id"] == "s-1"
    assert d["created_at"] == "2026-06-08T12:00:00Z"
