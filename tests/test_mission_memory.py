from __future__ import annotations

import pytest

from fireclaw_core.mission.mission_memory import (
    DEFAULT_INDEXABLE_TYPES,
    MEMORY_RECORD_TYPES,
    MissionMemoryRecord,
    MissionMemoryStore,
    TranscriptIndexingPolicy,
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
    assert MEMORY_RECORD_TYPES == {
        "body_state",
        "command",
        "correction",
        "gist",
        "lesson",
        "mission",
        "observation",
        "outcome",
        "plan",
        "relation",
        "safety_decision",
        "skill_invocation",
        "subtask",
    }


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


# ---------------------------------------------------------------------------
# Integration tests: MissionMemoryStore with optional FTS index
# ---------------------------------------------------------------------------


def test_store_with_index_search_returns_results(tmp_path):
    """When index_path is configured, search() uses FTS."""
    store = MissionMemoryStore(
        tmp_path / "mem.jsonl",
        index_path=tmp_path / "mem.db",
    )
    store.append(_make_record(
        record_id="mem-1",
        content={"note": "rescued survivor on floor 2"},
    ))
    store.append(_make_record(
        record_id="mem-2",
        content={"detail": "heavy smoke detected"},
    ))

    results = store.search(keyword="smoke")
    assert len(results) == 1
    assert results[0].record_id == "mem-2"


def test_store_index_search_handles_unsafe_natural_language_query(tmp_path):
    """Index-backed search() accepts operator-like natural-language fragments."""
    store = MissionMemoryStore(
        tmp_path / "mem.jsonl",
        index_path=tmp_path / "mem.db",
    )
    store.append(_make_record(
        record_id="mem-1",
        content={"note": "command: 去二楼搜索"},
    ))

    results = store.search(keyword="command: 去二楼")

    assert len(results) == 1
    assert results[0].record_id == "mem-1"


def test_store_with_index_search_filters(tmp_path):
    """Index-backed search() respects structured filters."""
    store = MissionMemoryStore(
        tmp_path / "mem.jsonl",
        index_path=tmp_path / "mem.db",
    )
    store.append(_make_record(record_id="mem-1", mission_id="m-1", robot_id="r-1"))
    store.append(_make_record(record_id="mem-2", mission_id="m-1", robot_id="r-2"))
    store.append(_make_record(record_id="mem-3", mission_id="m-2", robot_id="r-1"))

    results = store.search(mission_id="m-1")
    assert len(results) == 2
    assert {r.record_id for r in results} == {"mem-1", "mem-2"}

    results = store.search(robot_id="r-1")
    assert len(results) == 2
    assert {r.record_id for r in results} == {"mem-1", "mem-3"}


def test_store_with_index_search_combined_filters(tmp_path):
    """Index-backed search() combines text + structured filters."""
    store = MissionMemoryStore(
        tmp_path / "mem.jsonl",
        index_path=tmp_path / "mem.db",
    )
    store.append(_make_record(
        record_id="mem-1", mission_id="m-1",
        content={"note": "rescued survivor"},
    ))
    store.append(_make_record(
        record_id="mem-2", mission_id="m-2",
        content={"note": "rescued survivor"},
    ))

    results = store.search(keyword="survivor", mission_id="m-1")
    assert len(results) == 1
    assert results[0].record_id == "mem-1"


def test_store_without_index_uses_jsonl_fallback(tmp_path):
    """When no index_path is set, search() falls back to JSONL keyword search."""
    store = MissionMemoryStore(tmp_path / "mem.jsonl")
    store.append(_make_record(
        record_id="mem-1",
        content={"note": "smoke on floor 3"},
    ))
    store.append(_make_record(
        record_id="mem-2",
        content={"note": "cleared stairwell"},
    ))

    results = store.search(keyword="smoke")
    assert len(results) == 1
    assert results[0].record_id == "mem-1"


def test_store_search_indexed_raises_without_index(tmp_path):
    """search_indexed() raises RuntimeError when no index is configured."""
    store = MissionMemoryStore(tmp_path / "mem.jsonl")
    with pytest.raises(RuntimeError, match="No memory index configured"):
        store.search_indexed("anything")


def test_store_search_indexed_with_index(tmp_path):
    """search_indexed() delegates to the FTS index."""
    store = MissionMemoryStore(
        tmp_path / "mem.jsonl",
        index_path=tmp_path / "mem.db",
    )
    store.append(_make_record(
        record_id="mem-1",
        content={"note": "found victim in basement"},
    ))
    results = store.search_indexed("basement")
    assert len(results) == 1
    assert results[0].record_id == "mem-1"


def test_store_index_property_lazy(tmp_path):
    """The index property is lazily created."""
    store = MissionMemoryStore(
        tmp_path / "mem.jsonl",
        index_path=tmp_path / "mem.db",
    )
    # Index not created until first access.
    assert store._index is None
    _ = store.index
    assert store._index is not None


def test_store_without_index_property_is_none(tmp_path):
    """Without index_path, the index property returns None."""
    store = MissionMemoryStore(tmp_path / "mem.jsonl")
    assert store.index is None


# ---------------------------------------------------------------------------
# TranscriptIndexingPolicy tests
# ---------------------------------------------------------------------------


class TestTranscriptIndexingPolicy:
    def test_default_policy_indexes_standard_types(self) -> None:
        policy = TranscriptIndexingPolicy()
        for record_type in ("command", "plan", "observation", "outcome", "lesson"):
            assert policy.should_index(record_type) is True

    def test_default_policy_does_not_index_correction(self) -> None:
        """Correction records contain operator feedback and are not in the default set."""
        policy = TranscriptIndexingPolicy()
        assert policy.should_index("correction") is False

    def test_default_policy_does_not_index_unknown_types(self) -> None:
        policy = TranscriptIndexingPolicy()
        assert policy.should_index("raw_sensor_dump") is False
        assert policy.should_index("private_log") is False

    def test_custom_include_types(self) -> None:
        policy = TranscriptIndexingPolicy(include_types=frozenset({"outcome", "lesson"}))
        assert policy.should_index("outcome") is True
        assert policy.should_index("lesson") is True
        assert policy.should_index("command") is False

    def test_empty_include_types_disables_indexing(self) -> None:
        policy = TranscriptIndexingPolicy(include_types=frozenset())
        assert policy.should_index("outcome") is False
        assert policy.should_index("command") is False

    def test_frozen(self) -> None:
        policy = TranscriptIndexingPolicy()
        import pytest
        with pytest.raises(AttributeError):
            policy.include_types = frozenset()  # type: ignore[misc]

    def test_default_indexable_types_constant(self) -> None:
        """DEFAULT_INDEXABLE_TYPES includes the standard non-private types."""
        assert "correction" not in DEFAULT_INDEXABLE_TYPES
        assert "command" in DEFAULT_INDEXABLE_TYPES
        assert "outcome" in DEFAULT_INDEXABLE_TYPES


class TestMissionMemoryStoreIndexingPolicy:
    def test_store_with_default_policy_indexes_indexable_types(self, tmp_path: Path) -> None:
        """Default policy indexes outcome records."""
        store = MissionMemoryStore(
            tmp_path / "mem.jsonl",
            index_path=tmp_path / "mem.db",
        )
        store.append(_make_record(
            record_id="mem-1", record_type="outcome",
            content={"note": "rescued survivor"},
        ))

        results = store.search_indexed("rescued")
        assert len(results) == 1
        assert results[0].record_id == "mem-1"

    def test_store_with_policy_excludes_non_indexable_types(self, tmp_path: Path) -> None:
        """Default policy does not index correction records."""
        store = MissionMemoryStore(
            tmp_path / "mem.jsonl",
            index_path=tmp_path / "mem.db",
        )
        store.append(_make_record(
            record_id="mem-1", record_type="correction",
            content={"note": "rescued survivor"},
        ))

        # The record is in JSONL but not in the index.
        records = store.list_records()
        assert len(records) == 1
        assert records[0].record_id == "mem-1"

        # search_indexed should find nothing since correction is not indexed.
        results = store.search_indexed("rescued")
        assert len(results) == 0

    def test_store_with_custom_policy(self, tmp_path: Path) -> None:
        """Custom policy can opt-in to correction records."""
        policy = TranscriptIndexingPolicy(
            include_types=frozenset({"outcome", "correction"}),
        )
        store = MissionMemoryStore(
            tmp_path / "mem.jsonl",
            index_path=tmp_path / "mem.db",
            indexing_policy=policy,
        )
        store.append(_make_record(
            record_id="mem-1", record_type="correction",
            content={"note": "rescued survivor"},
        ))

        results = store.search_indexed("rescued")
        assert len(results) == 1

    def test_store_with_empty_policy_disables_indexing(self, tmp_path: Path) -> None:
        """Empty policy set means nothing gets indexed."""
        policy = TranscriptIndexingPolicy(include_types=frozenset())
        store = MissionMemoryStore(
            tmp_path / "mem.jsonl",
            index_path=tmp_path / "mem.db",
            indexing_policy=policy,
        )
        store.append(_make_record(
            record_id="mem-1", record_type="outcome",
            content={"note": "rescued survivor"},
        ))

        records = store.list_records()
        assert len(records) == 1

        results = store.search_indexed("rescued")
        assert len(results) == 0
