from __future__ import annotations

import sqlite3

import pytest

from fireclaw_core.memory.embodied_memory import (
    EMBODIED_METADATA_KEY,
    EmbodiedMemoryEvent,
    EmbodiedMemoryStore,
    SpatialMemoryContext,
)
from fireclaw_core.memory.memory_index import SqliteMemoryIndex


T0 = "2026-07-14T10:00:00+00:00"
T1 = "2026-07-14T10:01:00+00:00"
T2 = "2026-07-14T10:02:00+00:00"


def _store(tmp_path) -> EmbodiedMemoryStore:
    return EmbodiedMemoryStore(
        tmp_path / "memory.jsonl",
        index_path=tmp_path / "memory.sqlite",
    )


def _observation(
    store: EmbodiedMemoryStore,
    *,
    event_id: str,
    runtime_mode: str = "simulation",
    observed_at: str = T0,
    frame_id: str = "map",
    x: float = 0.0,
    y: float = 0.0,
    uncertainty_radius_m: float = 0.0,
    note: str = "thermal hotspot detected",
):
    return store.record_event(
        event_id=event_id,
        mission_id="mission-1",
        event_type="observation",
        payload={"note": note, "floor": "2"},
        runtime_mode=runtime_mode,
        source_type="thermal_camera",
        robot_id="robot-1",
        episode_id="episode-1",
        observed_at=observed_at,
        pose=SpatialMemoryContext(
            frame_id=frame_id,
            x=x,
            y=y,
            floor="2",
            uncertainty_radius_m=uncertainty_radius_m,
        ),
        confidence=0.9,
    )


def test_event_persists_evidence_and_searchable_projection(tmp_path):
    store = _store(tmp_path)
    event = _observation(store, event_id="obs-1")

    records = store.evidence_store.list_records()
    assert len(records) == 1
    assert records[0].record_id == event.event_id
    assert records[0].content[EMBODIED_METADATA_KEY]["runtime_mode"] == "simulation"
    assert records[0].content[EMBODIED_METADATA_KEY]["pose"]["frame_id"] == "map"

    results = store.search_text("thermal hotspot", runtime_mode="simulation")
    assert [result["record_id"] for result in results] == ["obs-1"]
    assert results[0]["episode_id"] == "episode-1"
    assert results[0]["pose"]["x"] == 0.0


def test_spatial_query_partitions_runtime_and_coordinate_frame(tmp_path):
    store = _store(tmp_path)
    _observation(store, event_id="near", x=0.5)
    _observation(
        store,
        event_id="uncertain-edge",
        x=2.4,
        uncertainty_radius_m=0.5,
    )
    _observation(store, event_id="outside", x=3.0)
    _observation(store, event_id="real-near", runtime_mode="real", x=0.1)
    _observation(store, event_id="other-frame", frame_id="building-b/map", x=0.1)

    results = store.query_spatial(
        runtime_mode="simulation",
        frame_id="map",
        x=0.0,
        y=0.0,
        radius_m=2.0,
        filters={"record_type": "observation"},
    )

    assert [result["record_id"] for result in results] == ["near", "uncertain-edge"]
    assert results[0]["distance_m"] == pytest.approx(0.5)
    assert results[1]["distance_m"] == pytest.approx(2.4)


def test_temporal_query_is_inclusive_and_filterable(tmp_path):
    store = _store(tmp_path)
    _observation(store, event_id="before", observed_at=T0)
    _observation(store, event_id="inside", observed_at=T1)
    _observation(store, event_id="end", observed_at=T2)
    _observation(store, event_id="real", runtime_mode="real", observed_at=T1)

    results = store.query_temporal(
        runtime_mode="simulation",
        start_at=T1,
        end_at=T2,
        filters={"robot_id": "robot-1", "record_type": "observation"},
    )

    assert [result["record_id"] for result in results] == ["end", "inside"]


def test_sensitive_event_is_structurally_indexed_but_not_full_text_indexed(tmp_path):
    store = _store(tmp_path)
    store.record_event(
        event_id="body-1",
        mission_id="mission-1",
        event_type="body_state",
        payload={"diagnostic": "battery pulse anomaly"},
        runtime_mode="simulation",
        source_type="robot_state",
        robot_id="robot-1",
        observed_at=T1,
        pose=SpatialMemoryContext(frame_id="base_link", x=0.0, y=0.0),
    )

    assert store.search_text("battery pulse", runtime_mode="simulation") == []
    temporal = store.query_temporal(
        runtime_mode="simulation",
        start_at=T0,
        end_at=T2,
        filters={"record_type": "body_state"},
    )
    assert [result["record_id"] for result in temporal] == ["body-1"]


def test_relation_is_auditable_queryable_and_rebuildable(tmp_path):
    store = _store(tmp_path)
    _observation(store, event_id="obs-1", observed_at=T0)
    store.record_event(
        event_id="decision-1",
        mission_id="mission-1",
        event_type="safety_decision",
        payload={"decision": "hold", "reason": "temperature threshold exceeded"},
        runtime_mode="simulation",
        source_type="safety_gate",
        robot_id="robot-1",
        observed_at=T1,
    )
    store.add_relation(
        relation_id="rel-1",
        mission_id="mission-1",
        source_record_id="obs-1",
        target_record_id="decision-1",
        relation_type="supports",
        runtime_mode="simulation",
        created_at=T2,
        metadata={"rule_id": "thermal-stop-v1"},
    )

    neighbors = store.neighbors("obs-1", runtime_mode="simulation")
    assert len(neighbors) == 1
    assert neighbors[0]["neighbor_record_id"] == "decision-1"
    assert neighbors[0]["direction"] == "outgoing"
    assert neighbors[0]["metadata"] == {"rule_id": "thermal-stop-v1"}
    assert store.evidence_store.summary() == {
        "total": 3,
        "by_type": {"observation": 1, "safety_decision": 1, "relation": 1},
    }

    assert store.index is not None
    store.index.clear()
    assert store.neighbors("obs-1", runtime_mode="simulation") == []
    assert store.rebuild_index() == 3
    assert store.neighbors("obs-1", runtime_mode="simulation")[0]["relation_id"] == "rel-1"


def test_relation_rejects_cross_runtime_modes_before_evidence_append(tmp_path):
    store = _store(tmp_path)
    _observation(store, event_id="sim", runtime_mode="simulation")
    _observation(store, event_id="real", runtime_mode="real")

    with pytest.raises(ValueError, match="cannot cross runtime modes"):
        store.add_relation(
            relation_id="rel-invalid",
            mission_id="mission-1",
            source_record_id="sim",
            target_record_id="real",
            relation_type="follows",
            runtime_mode="simulation",
            created_at=T2,
        )

    assert {record.record_id for record in store.evidence_store.list_records()} == {"sim", "real"}


def test_event_validation_rejects_ambiguous_runtime_and_reserved_payload(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="Invalid runtime_mode"):
        _observation(store, event_id="unknown-mode", runtime_mode="unknown")

    with pytest.raises(ValueError, match="reserved"):
        store.record_event(
            event_id="reserved",
            mission_id="mission-1",
            event_type="observation",
            payload={EMBODIED_METADATA_KEY: {"runtime_mode": "real"}},
            runtime_mode="simulation",
            source_type="sensor",
            observed_at=T0,
        )

    with pytest.raises(ValueError, match="include a timezone"):
        EmbodiedMemoryEvent(
            event_id="no-timezone",
            mission_id="mission-1",
            event_type="observation",
            payload={},
            runtime_mode="simulation",
            source_type="sensor",
            observed_at="2026-07-14T10:00:00",
        )


def test_duplicate_evidence_id_is_rejected(tmp_path):
    store = _store(tmp_path)
    _observation(store, event_id="obs-1")

    with pytest.raises(ValueError, match="already exists"):
        _observation(store, event_id="obs-1")

    assert len(store.evidence_store.list_records()) == 1


def test_existing_fts_database_schema_is_migrated_for_embodied_fields(tmp_path):
    index_path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(index_path)
    conn.executescript(
        """
        CREATE TABLE memory_records (
            record_id TEXT PRIMARY KEY,
            mission_id TEXT,
            record_type TEXT,
            robot_id TEXT,
            subtask_id TEXT,
            floor TEXT,
            capability TEXT,
            outcome_val TEXT,
            operator TEXT,
            risk_level TEXT,
            created_at TEXT,
            content_json TEXT
        );
        CREATE VIRTUAL TABLE memory_fts USING fts5(record_id UNINDEXED, text_blob);
        CREATE TABLE memory_embeddings (
            record_id TEXT PRIMARY KEY,
            embedding BLOB NOT NULL,
            dimensions INTEGER NOT NULL
        );
        """
    )
    conn.close()

    event = EmbodiedMemoryEvent(
        event_id="obs-migrated",
        mission_id="mission-1",
        event_type="observation",
        payload={"note": "legacy database upgraded"},
        runtime_mode="simulation",
        source_type="sensor",
        observed_at=T1,
        pose=SpatialMemoryContext(frame_id="map", x=1.0, y=2.0),
    )
    index = SqliteMemoryIndex(index_path)
    index.upsert(event.to_mission_record().to_dict())

    results = index.search_spatial(
        runtime_mode="simulation",
        frame_id="map",
        x=1.0,
        y=2.0,
        radius_m=0.1,
    )
    assert [result["record_id"] for result in results] == ["obs-migrated"]


def test_query_requires_configured_index(tmp_path):
    store = EmbodiedMemoryStore(tmp_path / "memory.jsonl")
    with pytest.raises(RuntimeError, match="No embodied memory index"):
        store.query_temporal(
            runtime_mode="simulation",
            start_at=T0,
            end_at=T1,
        )
