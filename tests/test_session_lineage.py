"""Tests for lightweight mission session lineage and resume guard."""

import json
from pathlib import Path

from fireclaw_core.session_lineage import (
    JsonlSessionLineageStore,
    MissionSessionLineage,
    ResumeOwnershipDecision,
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
