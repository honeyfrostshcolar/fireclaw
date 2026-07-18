"""Tests for lightweight mission session lineage and resume guard."""

import json
from pathlib import Path

from fireclaw_core.infra.session_lineage import (
    JsonlSessionLineageStore,
    MissionSessionLineage,
    validate_resume_ownership,
)


def test_lineage_record_to_dict():
    record = MissionSessionLineage(
        session_id="mission-1",
        kind="mission",
        operator_id="operator-a",
        parent_session_id=None,
        spawned_by=None,
        spawn_depth=0,
    )
    d = record.to_dict()
    assert d["session_id"] == "mission-1"
    assert d["kind"] == "mission"
    assert d["operator_id"] == "operator-a"
    assert d["spawn_depth"] == 0


def test_lineage_store_upsert_and_get(tmp_path):
    store = JsonlSessionLineageStore(str(tmp_path / "lineage.jsonl"))
    record = MissionSessionLineage(
        session_id="mission-1",
        kind="mission",
        operator_id="operator-a",
        parent_session_id=None,
        spawned_by=None,
        spawn_depth=0,
    )
    store.upsert(record)
    got = store.get("mission-1")
    assert got is not None
    assert got.session_id == "mission-1"


def test_validate_resume_ownership(tmp_path):
    store = JsonlSessionLineageStore(str(tmp_path / "lineage.jsonl"))
    record = MissionSessionLineage(
        session_id="mission-1",
        kind="mission",
        operator_id="operator-a",
        parent_session_id=None,
        spawned_by=None,
        spawn_depth=0,
    )
    store.upsert(record)
    assert validate_resume_ownership(store, session_id="mission-1", operator_id="operator-a").ok is True
    assert validate_resume_ownership(store, session_id="mission-1", operator_id="operator-b").ok is False


def test_validate_resume_ownership_unknown_session(tmp_path):
    store = JsonlSessionLineageStore(str(tmp_path / "lineage.jsonl"))
    result = validate_resume_ownership(store, session_id="unknown", operator_id="operator-a")
    assert result.ok is True  # unknown session = allow (no ownership to check)


def test_lineage_store_list_for_operator(tmp_path):
    store = JsonlSessionLineageStore(str(tmp_path / "lineage.jsonl"))
    store.upsert(MissionSessionLineage(
        session_id="mission-1", kind="mission", operator_id="op-a",
        parent_session_id=None, spawned_by=None, spawn_depth=0,
    ))
    store.upsert(MissionSessionLineage(
        session_id="mission-2", kind="mission", operator_id="op-a",
        parent_session_id=None, spawned_by=None, spawn_depth=0,
    ))
    store.upsert(MissionSessionLineage(
        session_id="mission-3", kind="mission", operator_id="op-b",
        parent_session_id=None, spawned_by=None, spawn_depth=0,
    ))
    op_a_sessions = store.list_for_operator("op-a")
    assert len(op_a_sessions) == 2
    assert all(s.operator_id == "op-a" for s in op_a_sessions)


def test_lineage_store_skips_corrupt_lines(tmp_path):
    path = tmp_path / "lineage.jsonl"
    # Write a corrupt line followed by a valid line
    with open(path, "w") as f:
        f.write("not valid json\n")
        f.write(json.dumps({
            "session_id": "mission-1", "kind": "mission", "operator_id": "op-a",
            "parent_session_id": None, "spawned_by": None, "spawn_depth": 0,
            "created_at": "2026-06-10T00:00:00Z", "updated_at": "2026-06-10T00:00:00Z",
        }) + "\n")
    store = JsonlSessionLineageStore(str(path))
    got = store.get("mission-1")
    assert got is not None
    assert got.session_id == "mission-1"


def test_lineage_store_last_write_wins(tmp_path):
    store = JsonlSessionLineageStore(str(tmp_path / "lineage.jsonl"))
    store.upsert(MissionSessionLineage(
        session_id="mission-1", kind="mission", operator_id="op-a",
        parent_session_id=None, spawned_by=None, spawn_depth=0,
        created_at="2026-06-10T00:00:00Z", updated_at="2026-06-10T00:00:00Z",
    ))
    store.upsert(MissionSessionLineage(
        session_id="mission-1", kind="mission", operator_id="op-b",
        parent_session_id=None, spawned_by=None, spawn_depth=0,
        created_at="2026-06-10T00:00:00Z", updated_at="2026-06-10T01:00:00Z",
    ))
    got = store.get("mission-1")
    assert got is not None
    assert got.operator_id == "op-b"  # last write wins
    assert got.updated_at == "2026-06-10T01:00:00Z"
