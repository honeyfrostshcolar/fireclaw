"""Tests for SqliteMemoryIndex — FTS5-backed memory index."""
from __future__ import annotations

import json

import pytest

from fireclaw_core.memory_index import SqliteMemoryIndex


def _make_record(
    record_id: str = "mem-1",
    mission_id: str = "m-1",
    record_type: str = "outcome",
    content: dict | None = None,
    robot_id: str | None = None,
    subtask_id: str | None = None,
    created_at: str = "2026-06-08T12:00:00Z",
    *,
    floor: str | None = None,
    capability: str | None = None,
    outcome: str | None = None,
    operator: str | None = None,
    risk_level: str | None = None,
) -> dict:
    """Build a flat dict matching the shape MissionMemoryStore produces."""
    c = content or {"status": "succeeded"}
    d: dict = {
        "record_id": record_id,
        "mission_id": mission_id,
        "record_type": record_type,
        "content": c,
        "robot_id": robot_id,
        "subtask_id": subtask_id,
        "created_at": created_at,
    }
    if floor is not None:
        d["floor"] = floor
    if capability is not None:
        d["capability"] = capability
    if outcome is not None:
        d["outcome"] = outcome
    if operator is not None:
        d["operator"] = operator
    if risk_level is not None:
        d["risk_level"] = risk_level
    return d


class TestUpsert:
    def test_upsert_adds_record(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        rec = _make_record()
        idx.upsert(rec)
        results = idx.search("*")
        assert len(results) == 1
        assert results[0]["record_id"] == "mem-1"

    def test_upsert_updates_existing_record(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        rec = _make_record()
        idx.upsert(rec)
        updated = _make_record(content={"status": "updated"})
        idx.upsert(updated)
        results = idx.search("*")
        assert len(results) == 1
        assert results[0]["content"]["status"] == "updated"


class TestSearch:
    def test_search_by_text(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(
            record_id="mem-1",
            content={"note": "rescued survivor on floor 2"},
        ))
        idx.upsert(_make_record(
            record_id="mem-2",
            content={"detail": "heavy smoke detected"},
        ))
        idx.upsert(_make_record(
            record_id="mem-3",
            content={"lesson": "always check stairwell first"},
        ))

        results = idx.search("smoke")
        assert len(results) == 1
        assert results[0]["record_id"] == "mem-2"

        results = idx.search("floor")
        assert len(results) == 1
        assert results[0]["record_id"] == "mem-1"

    def test_search_no_match(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record())
        results = idx.search("nonexistent_term_xyz")
        assert len(results) == 0

    def test_search_escapes_colon_query(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(
            record_id="mem-1",
            content={"note": "command: 去二楼搜索"},
        ))

        results = idx.search("command: 去二楼")

        assert len(results) == 1
        assert results[0]["record_id"] == "mem-1"

    def test_search_escapes_unterminated_quote(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(
            record_id="mem-1",
            content={"note": 'operator said "search second floor'},
        ))

        results = idx.search('"search second floor')

        assert len(results) == 1
        assert results[0]["record_id"] == "mem-1"

    def test_search_escapes_trailing_operator(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(
            record_id="mem-1",
            content={"note": "二楼 OR 搜索"},
        ))

        results = idx.search("二楼 OR")

        assert len(results) == 1
        assert results[0]["record_id"] == "mem-1"

    def test_search_respects_limit(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        for i in range(5):
            idx.upsert(_make_record(record_id=f"mem-{i}", content={"note": "fire event"}))
        results = idx.search("fire", limit=3)
        assert len(results) == 3

    def test_search_wildcard_returns_all(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(record_id="mem-1"))
        idx.upsert(_make_record(record_id="mem-2"))
        idx.upsert(_make_record(record_id="mem-3"))
        results = idx.search("*")
        assert len(results) == 3


class TestFilters:
    def test_filter_mission_id(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(record_id="mem-1", mission_id="m-1"))
        idx.upsert(_make_record(record_id="mem-2", mission_id="m-2"))
        idx.upsert(_make_record(record_id="mem-3", mission_id="m-1"))

        results = idx.search("*", filters={"mission_id": "m-1"})
        assert len(results) == 2
        assert {r["record_id"] for r in results} == {"mem-1", "mem-3"}

    def test_filter_robot_id(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(record_id="mem-1", robot_id="robot-1"))
        idx.upsert(_make_record(record_id="mem-2", robot_id="robot-2"))
        idx.upsert(_make_record(record_id="mem-3", robot_id="robot-1"))

        results = idx.search("*", filters={"robot_id": "robot-1"})
        assert len(results) == 2
        assert {r["record_id"] for r in results} == {"mem-1", "mem-3"}

    def test_filter_floor(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(record_id="mem-1", floor="2"))
        idx.upsert(_make_record(record_id="mem-2", floor="3"))
        idx.upsert(_make_record(record_id="mem-3", floor="2"))

        results = idx.search("*", filters={"floor": "2"})
        assert len(results) == 2

    def test_filter_capability(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(record_id="mem-1", capability="rescue"))
        idx.upsert(_make_record(record_id="mem-2", capability="recon"))
        idx.upsert(_make_record(record_id="mem-3", capability="rescue"))

        results = idx.search("*", filters={"capability": "rescue"})
        assert len(results) == 2

    def test_filter_outcome(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(record_id="mem-1", outcome="success"))
        idx.upsert(_make_record(record_id="mem-2", outcome="failure"))

        results = idx.search("*", filters={"outcome": "success"})
        assert len(results) == 1
        assert results[0]["record_id"] == "mem-1"

    def test_filter_operator(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(record_id="mem-1", operator="op-1"))
        idx.upsert(_make_record(record_id="mem-2", operator="op-2"))

        results = idx.search("*", filters={"operator": "op-1"})
        assert len(results) == 1

    def test_filter_risk_level(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(record_id="mem-1", risk_level="high"))
        idx.upsert(_make_record(record_id="mem-2", risk_level="low"))

        results = idx.search("*", filters={"risk_level": "high"})
        assert len(results) == 1
        assert results[0]["record_id"] == "mem-1"

    def test_combined_filters(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(
            record_id="mem-1", mission_id="m-1", robot_id="r-1", floor="2",
        ))
        idx.upsert(_make_record(
            record_id="mem-2", mission_id="m-1", robot_id="r-2", floor="2",
        ))
        idx.upsert(_make_record(
            record_id="mem-3", mission_id="m-2", robot_id="r-1", floor="3",
        ))

        results = idx.search("*", filters={"mission_id": "m-1", "floor": "2"})
        assert len(results) == 2
        assert {r["record_id"] for r in results} == {"mem-1", "mem-2"}

    def test_filter_with_text_search(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(
            record_id="mem-1", mission_id="m-1",
            content={"note": "rescued survivor on floor 2"},
        ))
        idx.upsert(_make_record(
            record_id="mem-2", mission_id="m-2",
            content={"note": "rescued survivor on floor 3"},
        ))

        results = idx.search("survivor", filters={"mission_id": "m-1"})
        assert len(results) == 1
        assert results[0]["record_id"] == "mem-1"


class TestRebuild:
    def test_rebuild_from_records(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        records = [
            _make_record(record_id="mem-1", content={"note": "first event"}),
            _make_record(record_id="mem-2", content={"note": "second event"}),
            _make_record(record_id="mem-3", content={"note": "third event"}),
        ]
        count = idx.rebuild(records)
        assert count == 3
        results = idx.search("event")
        assert len(results) == 3

    def test_rebuild_clears_old_data(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(record_id="mem-old", content={"note": "old data"}))
        records = [
            _make_record(record_id="mem-new", content={"note": "new data"}),
        ]
        count = idx.rebuild(records)
        assert count == 1
        results = idx.search("*")
        assert len(results) == 1
        assert results[0]["record_id"] == "mem-new"

    def test_rebuild_empty_list_clears_index(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record())
        count = idx.rebuild([])
        assert count == 0
        results = idx.search("*")
        assert len(results) == 0

    def test_rebuild_skips_records_without_record_id(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        records = [
            {"mission_id": "m-1", "record_type": "outcome", "content": {}},
            _make_record(record_id="mem-valid"),
        ]
        count = idx.rebuild(records)
        assert count == 1
        results = idx.search("*")
        assert len(results) == 1


class TestMissingIndex:
    def test_tolerates_missing_index_file(self, tmp_path):
        """Opening a non-existent path should not crash — index is created lazily."""
        idx = SqliteMemoryIndex(tmp_path / "nonexistent" / "mem.db")
        results = idx.search("*")
        assert results == []

    def test_upsert_creates_parent_dirs(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "deep" / "nested" / "mem.db")
        idx.upsert(_make_record())
        results = idx.search("*")
        assert len(results) == 1


class TestRecordFieldCoverage:
    def test_index_stores_content_for_fts(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(
            record_id="mem-1",
            content={"status": "succeeded", "note": "found victim in basement"},
        ))
        results = idx.search("basement")
        assert len(results) == 1
        assert results[0]["record_id"] == "mem-1"

    def test_index_stores_record_type(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_record(record_id="mem-1", record_type="outcome"))
        idx.upsert(_make_record(record_id="mem-2", record_type="lesson"))
        results = idx.search("*", filters={"record_type": "lesson"})
        assert len(results) == 1
        assert results[0]["record_id"] == "mem-2"

    def test_search_returns_full_record_dict(self, tmp_path):
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        rec = _make_record(
            record_id="mem-1",
            mission_id="m-1",
            record_type="outcome",
            content={"status": "ok"},
            robot_id="r-1",
            subtask_id="s-1",
            created_at="2026-06-08T12:00:00Z",
        )
        idx.upsert(rec)
        results = idx.search("*")
        assert len(results) == 1
        assert results[0]["record_id"] == "mem-1"
        assert results[0]["mission_id"] == "m-1"
        assert results[0]["record_type"] == "outcome"
        assert results[0]["content"] == {"status": "ok"}
        assert results[0]["robot_id"] == "r-1"
        assert results[0]["subtask_id"] == "s-1"
        assert results[0]["created_at"] == "2026-06-08T12:00:00Z"
