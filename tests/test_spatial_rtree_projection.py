from __future__ import annotations

from fireclaw_core.memory.embodied_memory import (
    EmbodiedMemoryEvent,
    EmbodiedMemoryStore,
    SpatialMemoryContext,
)
from fireclaw_core.memory.memory_index import SqliteMemoryIndex
from fireclaw_core.memory.mission_memory_facade import (
    MemoryAccessContext,
    MissionMemoryFacade,
)
from fireclaw_core.memory.spatial_projection import event_spatial_projection_rows


T0 = "2026-07-20T10:00:00+00:00"


def _event_record(
    *,
    event_id: str,
    event_type: str = "observation",
    pose: SpatialMemoryContext | None = None,
    payload: dict | None = None,
) -> dict:
    return EmbodiedMemoryEvent(
        event_id=event_id,
        mission_id="mission-1",
        event_type=event_type,
        payload=payload or {},
        runtime_mode="simulation",
        source_type="test",
        observed_at=T0,
        pose=pose,
    ).to_mission_record().to_dict()


def test_projection_preserves_multi_geometry_and_missing_z():
    record = _event_record(
        event_id="gist-1",
        event_type="gist",
        pose=SpatialMemoryContext(frame_id="map", floor="2", x=0.0, y=0.0),
        payload={
            "spatial_geometries": [
                {
                    "frame_id": "map",
                    "floor": "2",
                    "center_x": 1.0,
                    "center_y": 2.0,
                    "radius_m": 0.5,
                },
                {
                    "frame_id": "map",
                    "floor": "2",
                    "center_x": 10.0,
                    "center_y": 20.0,
                    "center_z": 3.0,
                    "radius_m": 2.0,
                    "bounds": {"min_z": 2.0, "max_z": 4.0},
                },
            ]
        },
    )

    rows = event_spatial_projection_rows(record)

    assert [(row.geometry_source, row.geometry_index) for row in rows] == [
        ("event_pose", 0),
        ("conservative_geometry", 0),
        ("conservative_geometry", 1),
    ]
    assert rows[0].bounds_3d() is None
    assert rows[1].bounds_3d() is None
    assert rows[2].bounds_2d() == (8.0, 12.0, 18.0, 22.0)
    assert rows[2].bounds_3d() == (8.0, 12.0, 18.0, 22.0, 2.0, 4.0)


def test_index_candidates_lazy_rebuild_and_batch_hydration(tmp_path):
    index = SqliteMemoryIndex(tmp_path / "memory.sqlite")
    assert index.rtree_available is True
    index.upsert(_event_record(
        event_id="near",
        pose=SpatialMemoryContext(
            frame_id="map",
            floor="2",
            x=6.0,
            y=0.0,
            uncertainty_radius_m=2.0,
        ),
    ))
    index.upsert(_event_record(
        event_id="wrong-floor",
        pose=SpatialMemoryContext(frame_id="map", floor="1", x=1.0, y=0.0),
    ))

    candidates = index.query_spatial_candidates(
        mission_id="mission-1",
        runtime_mode="simulation",
        frame_id="map",
        floor="2",
        x=0.0,
        y=0.0,
        radius_m=5.0,
        memory_types=frozenset({"observation"}),
    )
    assert candidates == [{"memory_type": "observation", "source_id": "near"}]
    assert [record["record_id"] for record in index.load_records_by_ids(
        ["near"],
        mission_id="mission-1",
        runtime_mode="simulation",
        record_types=frozenset({"observation"}),
    ) or []] == ["near"]

    conn = index._get_conn()
    conn.execute("DELETE FROM spatial_rtree_2d")
    conn.execute("DELETE FROM spatial_rtree_3d")
    conn.execute("DELETE FROM spatial_projection")
    conn.execute("DELETE FROM spatial_projection_meta")
    conn.commit()

    rebuilt = index.query_spatial_candidates(
        mission_id="mission-1",
        runtime_mode="simulation",
        frame_id="map",
        floor="2",
        x=0.0,
        y=0.0,
        radius_m=5.0,
        memory_types=frozenset({"observation"}),
    )
    assert rebuilt == candidates


def test_entity_spatial_rows_are_atomically_replaced(tmp_path):
    index = SqliteMemoryIndex(tmp_path / "memory.sqlite")
    index.replace_entity_projection(
        mission_id="mission-1",
        runtime_mode="simulation",
        source_token="entity-events-v2:0:0",
        entities=[{
            "entity_id": "entity-1",
            "entity_kind": "victim",
            "status": "candidate",
            "last_seen_at": T0,
            "current_pose": {
                "frame_id": "map",
                "floor": "2",
                "x": 1.0,
                "y": 0.0,
                "z": 2.0,
                "uncertainty_radius_m": 1.0,
            },
        }],
    )
    near_old = index.query_spatial_candidates(
        mission_id="mission-1",
        runtime_mode="simulation",
        frame_id="map",
        floor="2",
        x=0.0,
        y=0.0,
        z=2.0,
        radius_m=2.0,
        memory_types=frozenset({"entity"}),
        entity_kinds=frozenset({"victim"}),
    )
    assert near_old == [{"memory_type": "entity", "source_id": "entity-1"}]

    index.replace_entity_projection(
        mission_id="mission-1",
        runtime_mode="simulation",
        source_token="entity-events-v2:0:0",
        entities=[{
            "entity_id": "entity-1",
            "entity_kind": "victim",
            "status": "candidate",
            "last_seen_at": T0,
            "current_pose": {
                "frame_id": "map",
                "floor": "2",
                "x": 100.0,
                "y": 0.0,
                "z": 2.0,
                "uncertainty_radius_m": 1.0,
            },
        }],
    )
    near_old_after_update = index.query_spatial_candidates(
        mission_id="mission-1",
        runtime_mode="simulation",
        frame_id="map",
        floor="2",
        x=0.0,
        y=0.0,
        z=2.0,
        radius_m=2.0,
        memory_types=frozenset({"entity"}),
    )
    near_new = index.query_spatial_candidates(
        mission_id="mission-1",
        runtime_mode="simulation",
        frame_id="map",
        floor="2",
        x=100.0,
        y=0.0,
        z=2.0,
        radius_m=2.0,
        memory_types=frozenset({"entity"}),
    )
    assert near_old_after_update == []
    assert near_new == [{"memory_type": "entity", "source_id": "entity-1"}]


def test_authority_token_mismatch_falls_back_until_jsonl_rebuild(tmp_path):
    store = EmbodiedMemoryStore(
        tmp_path / "memory.jsonl",
        index_path=tmp_path / "memory.sqlite",
    )
    store.record_event(
        event_id="indexed-event",
        mission_id="mission-1",
        event_type="observation",
        payload={},
        runtime_mode="simulation",
        source_type="test",
        observed_at=T0,
        pose=SpatialMemoryContext(frame_id="map", floor="2", x=1.0, y=0.0),
    )
    access = MemoryAccessContext(
        mission_id="mission-1",
        runtime_mode="simulation",
        requester_id="operator",
    )
    facade = MissionMemoryFacade(store=store, runtime_mode="simulation")
    indexed = facade.query_nearest(
        access,
        frame_id="map",
        floor="2",
        x=0.0,
        y=0.0,
        max_distance_m=5.0,
        memory_types=["observation"],
        reference_at=T0,
    )
    assert indexed["candidate_backend"] == "sqlite_rtree"

    authority_only = EmbodiedMemoryEvent(
        event_id="authority-only-event",
        mission_id="mission-1",
        event_type="observation",
        payload={},
        runtime_mode="simulation",
        source_type="test",
        observed_at=T0,
        pose=SpatialMemoryContext(frame_id="map", floor="2", x=2.0, y=0.0),
    )
    store.evidence_store.append(authority_only.to_mission_record())

    fallback = facade.query_nearest(
        access,
        frame_id="map",
        floor="2",
        x=0.0,
        y=0.0,
        max_distance_m=5.0,
        memory_types=["observation"],
        reference_at=T0,
    )
    assert fallback["candidate_backend"] == "linear_scan"
    assert {item["record_id"] for item in fallback["results"]} == {
        "indexed-event",
        "authority-only-event",
    }

    store.rebuild_index()
    repaired = facade.query_nearest(
        access,
        frame_id="map",
        floor="2",
        x=0.0,
        y=0.0,
        max_distance_m=5.0,
        memory_types=["observation"],
        reference_at=T0,
    )
    assert repaired["candidate_backend"] == "sqlite_rtree"
    assert repaired["results"] == fallback["results"]


def test_non_spatial_event_and_relation_keep_projection_current(tmp_path):
    store = EmbodiedMemoryStore(
        tmp_path / "memory.jsonl",
        index_path=tmp_path / "memory.sqlite",
    )
    for event_id, x in (("near", 1.0), ("far", 20.0)):
        store.record_event(
            event_id=event_id,
            mission_id="mission-1",
            event_type="observation",
            payload={},
            runtime_mode="simulation",
            source_type="test",
            observed_at=T0,
            pose=SpatialMemoryContext(frame_id="map", floor="2", x=x, y=0.0),
        )
    store.record_event(
        event_id="command-1",
        mission_id="mission-1",
        event_type="command",
        payload={"text": "search nearby"},
        runtime_mode="simulation",
        source_type="test",
        observed_at=T0,
    )
    store.add_relation(
        relation_id="relation-1",
        mission_id="mission-1",
        source_record_id="near",
        target_record_id="far",
        relation_type="co_observed_with",
        runtime_mode="simulation",
        created_at=T0,
    )

    result = MissionMemoryFacade(
        store=store,
        runtime_mode="simulation",
    ).query_nearest(
        MemoryAccessContext(
            mission_id="mission-1",
            runtime_mode="simulation",
            requester_id="operator",
        ),
        frame_id="map",
        floor="2",
        x=0.0,
        y=0.0,
        max_distance_m=5.0,
        memory_types=["observation"],
        reference_at=T0,
    )

    assert result["candidate_backend"] == "sqlite_rtree"
    assert [item["record_id"] for item in result["results"]] == ["near"]


def test_nearest_rtree_and_linear_scan_are_equivalent_for_mixed_events(tmp_path):
    store = EmbodiedMemoryStore(
        tmp_path / "memory.jsonl",
        index_path=tmp_path / "memory.sqlite",
    )
    store.record_event(
        event_id="near-observation",
        mission_id="mission-1",
        event_type="observation",
        payload={"kind": "hazard"},
        runtime_mode="simulation",
        source_type="thermal_detector",
        observed_at=T0,
        pose=SpatialMemoryContext(
            frame_id="map",
            floor="2",
            x=3.0,
            y=0.0,
            uncertainty_radius_m=1.0,
        ),
    )
    store.record_event(
        event_id="restricted-near",
        mission_id="mission-1",
        event_type="observation",
        payload={"kind": "victim"},
        runtime_mode="simulation",
        source_type="vision_detector",
        observed_at=T0,
        pose=SpatialMemoryContext(frame_id="map", floor="2", x=1.0, y=0.0),
        sensitivity="restricted",
    )
    store.record_event(
        event_id="wrong-frame",
        mission_id="mission-1",
        event_type="observation",
        payload={},
        runtime_mode="simulation",
        source_type="test",
        observed_at=T0,
        pose=SpatialMemoryContext(frame_id="robot-local", floor="2", x=0.0, y=0.0),
    )
    store.record_event(
        event_id="multi-geometry-gist",
        mission_id="mission-1",
        event_type="gist",
        payload={
            "spatial_geometries": [
                {
                    "frame_id": "map",
                    "floor": "2",
                    "center_x": 40.0,
                    "center_y": 0.0,
                    "radius_m": 2.0,
                },
                {
                    "frame_id": "map",
                    "floor": "2",
                    "center_x": 0.5,
                    "center_y": 0.0,
                    "radius_m": 0.5,
                },
            ]
        },
        runtime_mode="simulation",
        source_type="memory_consolidation",
        observed_at=T0,
    )
    access = MemoryAccessContext(
        mission_id="mission-1",
        runtime_mode="simulation",
        requester_id="operator",
    )
    facade = MissionMemoryFacade(store=store, runtime_mode="simulation")

    indexed = facade.query_nearest(
        access,
        frame_id="map",
        floor="2",
        x=0.0,
        y=0.0,
        max_distance_m=5.0,
        memory_types=["observation", "gist"],
        limit=10,
        reference_at=T0,
    )
    assert indexed["candidate_backend"] == "sqlite_rtree"

    assert store.index is not None
    store.index._rtree_available = False
    linear = facade.query_nearest(
        access,
        frame_id="map",
        floor="2",
        x=0.0,
        y=0.0,
        max_distance_m=5.0,
        memory_types=["observation", "gist"],
        limit=10,
        reference_at=T0,
    )

    assert linear["candidate_backend"] == "linear_scan"
    assert _nearest_equivalence(indexed) == _nearest_equivalence(linear)
    assert indexed["restricted_records_omitted"] == linear["restricted_records_omitted"] == 1


def test_nearest_rtree_and_linear_scan_are_equivalent_for_3d_fail_closed(tmp_path):
    store = EmbodiedMemoryStore(
        tmp_path / "memory.jsonl",
        index_path=tmp_path / "memory.sqlite",
    )
    store.record_event(
        event_id="missing-z",
        mission_id="mission-1",
        event_type="observation",
        payload={},
        runtime_mode="simulation",
        source_type="test",
        observed_at=T0,
        pose=SpatialMemoryContext(frame_id="map", floor="2", x=0.0, y=0.0),
    )
    store.record_event(
        event_id="with-z",
        mission_id="mission-1",
        event_type="observation",
        payload={},
        runtime_mode="simulation",
        source_type="test",
        observed_at=T0,
        pose=SpatialMemoryContext(frame_id="map", floor="2", x=0.0, y=0.0, z=2.0),
    )
    access = MemoryAccessContext(
        mission_id="mission-1",
        runtime_mode="simulation",
        requester_id="operator",
        scopes=frozenset({"memory.restricted.read"}),
    )
    facade = MissionMemoryFacade(store=store, runtime_mode="simulation")

    indexed = facade.query_nearest(
        access,
        frame_id="map",
        floor="2",
        x=0.0,
        y=0.0,
        z=2.0,
        max_distance_m=1.0,
        memory_types=["observation"],
        limit=10,
        reference_at=T0,
    )
    assert indexed["candidate_backend"] == "sqlite_rtree"

    assert store.index is not None
    store.index._rtree_available = False
    linear = facade.query_nearest(
        access,
        frame_id="map",
        floor="2",
        x=0.0,
        y=0.0,
        z=2.0,
        max_distance_m=1.0,
        memory_types=["observation"],
        limit=10,
        reference_at=T0,
    )

    assert _nearest_equivalence(indexed) == _nearest_equivalence(linear)
    assert [item["record_id"] for item in indexed["results"]] == ["with-z"]


def _nearest_equivalence(result: dict) -> tuple:
    return tuple(
        (
            item["memory_type"],
            item["record_id"],
            round(float(item["center_distance_m"]), 6),
            round(float(item["distance_to_uncertainty_m"]), 6),
            item["spatial_match"]["source"],
        )
        for item in result["results"]
    )
