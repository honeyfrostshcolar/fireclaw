from __future__ import annotations

import pytest

from fireclaw_core.memory.embodied_memory import (
    EmbodiedMemoryProducer,
    EmbodiedMemoryStore,
    SpatialMemoryContext,
)
from fireclaw_core.memory.entity_extraction import EntityExtractionPipeline
from fireclaw_core.memory.entity_memory import EntityMemoryService
from fireclaw_core.memory.mission_memory_facade import (
    MemoryAccessContext,
    MissionMemoryFacade,
)


T0 = "2026-07-20T10:00:00+00:00"
T1 = "2026-07-20T10:01:00+00:00"
T2 = "2026-07-20T10:02:00+00:00"


def _store(tmp_path) -> EmbodiedMemoryStore:
    return EmbodiedMemoryStore(
        tmp_path / "embodied-memory.jsonl",
        index_path=tmp_path / "embodied-memory.sqlite",
    )


def _entity_service(store: EmbodiedMemoryStore) -> EntityMemoryService:
    return EntityMemoryService(
        store=store,
        resolver_producer=EmbodiedMemoryProducer(
            store,
            producer_type="entity_resolver",
            producer_id="test-entity-resolver",
        ),
        operator_producer=EmbodiedMemoryProducer(
            store,
            producer_type="approval_runtime",
            producer_id="test-operator-approval",
        ),
        runtime_mode="simulation",
    )


def _access() -> MemoryAccessContext:
    return MemoryAccessContext(
        mission_id="mission-1",
        runtime_mode="simulation",
        requester_id="operator-1",
        scopes=frozenset({"memory.restricted.read"}),
    )


def _observation_with_entity(
    store: EmbodiedMemoryStore,
    *,
    event_id: str,
    robot_id: str,
    name: str = "victim-alpha",
    track_id: str,
    x: float,
    y: float,
    observed_at: str,
    floor: str = "2",
    frame_id: str = "building-map",
    z: float | None = None,
    confidence: float = 0.86,
):
    pose = {
        "frame_id": frame_id,
        "floor": floor,
        "x": x,
        "y": y,
        "z": z,
        "uncertainty_radius_m": 1.0,
    }
    return store.record_event(
        event_id=event_id,
        mission_id="mission-1",
        event_type="observation",
        payload={
            "spatial_frame_scope": "mission",
            "entities": [
                {
                    "name": name,
                    "entity_kind": "victim",
                    "confidence": confidence,
                    "source_track_namespace": "thermal-camera",
                    "source_track_id": track_id,
                    "pose": pose,
                    "attributes": {"detector_class": "person"},
                }
            ],
        },
        runtime_mode="simulation",
        source_type="thermal_detector",
        robot_id=robot_id,
        observed_at=observed_at,
        pose=SpatialMemoryContext(
            frame_id=frame_id,
            floor=floor,
            x=x - 1.0,
            y=y,
        ),
        confidence=confidence,
    )


def test_structured_observation_entities_are_ingested_with_evidence_links(tmp_path):
    store = _store(tmp_path)
    service = _entity_service(store)
    pipeline = EntityExtractionPipeline(
        store=store,
        entity_memory=service,
        runtime_mode="simulation",
    )
    observation = _observation_with_entity(
        store,
        event_id="obs-victim-a",
        robot_id="robot-A",
        track_id="7",
        x=12.4,
        y=6.8,
        observed_at=T0,
    )

    report = pipeline.process_observation(observation)

    assert report.observation_event_id == "obs-victim-a"
    assert len(report.mention_event_ids) == 1
    assert report.issues == ()
    entities = service.list_entities(mission_id="mission-1", entity_kind="victim")
    assert len(entities) == 1
    entity = entities[0]
    assert entity.canonical_name == "victim-alpha"
    assert entity.observation_event_ids == ("obs-victim-a",)
    assert entity.mention_event_ids == report.mention_event_ids
    assert entity.current_pose is not None
    assert entity.current_pose.x == pytest.approx(12.4)
    assert entity.current_pose.floor == "2"
    assert entity.tracking_identities == ("robot-A:thermal-camera:7",)

    relations = store.list_relations(mission_id="mission-1")
    assert [(rel.source_record_id, rel.target_record_id, rel.relation_type) for rel in relations] == [
        (report.mention_event_ids[0], "obs-victim-a", "observed_in")
    ]

    second_report = pipeline.process_observation(observation)
    assert second_report.mention_event_ids == ()
    assert len(second_report.skipped_extraction_keys) == 1
    assert len(service.list_entities(mission_id="mission-1")) == 1


def test_observation_without_entities_skips_historical_entity_scan(tmp_path, monkeypatch):
    store = _store(tmp_path)
    service = _entity_service(store)
    pipeline = EntityExtractionPipeline(
        store=store,
        entity_memory=service,
        runtime_mode="simulation",
    )
    observation = store.record_event(
        event_id="plain-observation",
        mission_id="mission-1",
        event_type="observation",
        payload={"observation_kind": "sensor_discovery"},
        runtime_mode="simulation",
        source_type="sensor_discovery",
        robot_id="robot-A",
        observed_at=T0,
    )

    def fail_list_events(*args, **kwargs):
        raise AssertionError("observations without entities must not scan history")

    monkeypatch.setattr(store, "list_events", fail_list_events)

    report = pipeline.process_observation(observation)

    assert report.mention_event_ids == ()
    assert report.entity_ids == ()
    assert report.skipped_extraction_keys == ()
    assert report.issues == ()


def test_malformed_sqlite_entity_projection_is_rebuilt_from_jsonl_authority(tmp_path):
    store = _store(tmp_path)
    service = _entity_service(store)
    pipeline = EntityExtractionPipeline(
        store=store,
        entity_memory=service,
        runtime_mode="simulation",
    )
    observation = _observation_with_entity(
        store,
        event_id="obs-victim-a",
        robot_id="robot-A",
        track_id="7",
        x=12.4,
        y=6.8,
        observed_at=T0,
    )
    pipeline.process_observation(observation)
    original = service.list_entities(mission_id="mission-1")
    assert len(original) == 1
    assert store.index is not None
    source_token = store.index.entity_source_token(
        mission_id="mission-1",
        runtime_mode="simulation",
    )
    store.index.replace_entity_projection(
        mission_id="mission-1",
        runtime_mode="simulation",
        source_token=source_token,
        entities=({"entity_id": "malformed-projection-row"},),
    )

    reloaded = _entity_service(store).list_entities(mission_id="mission-1")

    assert len(reloaded) == 1
    assert reloaded[0].entity_id == original[0].entity_id
    assert reloaded[0].tracking_identities == ("robot-A:thermal-camera:7",)


def test_nearest_query_uses_frame_floor_and_uncertainty_boundaries(tmp_path):
    store = _store(tmp_path)
    store.record_event(
        event_id="hazard-center-six",
        mission_id="mission-1",
        event_type="observation",
        payload={"hazard": "thermal hotspot"},
        runtime_mode="simulation",
        source_type="thermal_detector",
        robot_id="robot-A",
        observed_at=T0,
        pose=SpatialMemoryContext(
            frame_id="building-map",
            floor="2",
            x=6.0,
            y=0.0,
            uncertainty_radius_m=2.0,
        ),
        confidence=0.9,
    )
    store.record_event(
        event_id="same-center-without-uncertainty",
        mission_id="mission-1",
        event_type="observation",
        payload={"hazard": "point estimate only"},
        runtime_mode="simulation",
        source_type="thermal_detector",
        robot_id="robot-A",
        observed_at=T1,
        pose=SpatialMemoryContext(frame_id="building-map", floor="2", x=6.0, y=0.0),
        confidence=0.9,
    )
    store.record_event(
        event_id="wrong-floor",
        mission_id="mission-1",
        event_type="observation",
        payload={"hazard": "first floor hotspot"},
        runtime_mode="simulation",
        source_type="thermal_detector",
        robot_id="robot-B",
        observed_at=T1,
        pose=SpatialMemoryContext(
            frame_id="building-map",
            floor="1",
            x=1.0,
            y=0.0,
            uncertainty_radius_m=1.0,
        ),
        confidence=0.9,
    )
    store.record_event(
        event_id="multi-area-gist",
        mission_id="mission-1",
        event_type="gist",
        payload={
            "summary": "smoke observed in two separated second-floor areas",
            "spatial_geometries": [
                {
                    "frame_id": "building-map",
                    "floor": "2",
                    "center_x": 30.0,
                    "center_y": 30.0,
                    "radius_m": 2.0,
                },
                {
                    "frame_id": "building-map",
                    "floor": "2",
                    "center_x": 1.0,
                    "center_y": 0.0,
                    "radius_m": 0.5,
                },
            ],
        },
        runtime_mode="simulation",
        source_type="memory_consolidation",
        robot_id="robot-A",
        observed_at=T2,
        confidence=0.8,
    )
    facade = MissionMemoryFacade(store=store, runtime_mode="simulation")

    result = facade.query_nearest(
        _access(),
        frame_id="building-map",
        floor="2",
        x=0.0,
        y=0.0,
        max_distance_m=5.0,
        memory_types=["observation", "gist"],
        limit=10,
        reference_at=T2,
    )

    result_ids = [item["record_id"] for item in result["results"]]
    assert result_ids == ["multi-area-gist", "hazard-center-six"]
    assert result["candidate_backend"] == "sqlite_rtree"
    hazard = result["results"][1]
    assert hazard["center_distance_m"] == pytest.approx(6.0)
    assert hazard["distance_to_uncertainty_m"] == pytest.approx(4.0)
    assert "same-center-without-uncertainty" not in result_ids
    assert "wrong-floor" not in result_ids
    assert result["results"][0]["spatial_match"]["source"] == "conservative_geometry"
    assert result["results"][0]["matched_spatial_geometry"]["center_x"] == 1.0

    assert store.index is not None
    store.index._rtree_available = False
    scan_result = facade.query_nearest(
        _access(),
        frame_id="building-map",
        floor="2",
        x=0.0,
        y=0.0,
        max_distance_m=5.0,
        memory_types=["observation", "gist"],
        limit=10,
        reference_at=T2,
    )
    assert scan_result["candidate_backend"] == "linear_scan"
    assert scan_result["results"] == result["results"]
    assert scan_result["restricted_records_omitted"] == result[
        "restricted_records_omitted"
    ]


def test_cross_robot_identity_proposal_is_advisory_and_rejectable(tmp_path):
    store = _store(tmp_path)
    service = _entity_service(store)
    pipeline = EntityExtractionPipeline(
        store=store,
        entity_memory=service,
        runtime_mode="simulation",
    )
    pipeline.process_observation(_observation_with_entity(
        store,
        event_id="obs-victim-a",
        robot_id="robot-A",
        track_id="7",
        x=12.0,
        y=6.0,
        observed_at=T0,
    ))
    pipeline.process_observation(_observation_with_entity(
        store,
        event_id="obs-victim-b",
        robot_id="robot-B",
        track_id="3",
        x=12.5,
        y=6.2,
        observed_at=T1,
    ))
    entities = service.list_entities(mission_id="mission-1", entity_kind="victim")
    assert len(entities) == 2

    report = service.list_identity_proposals(mission_id="mission-1", entity_kind="victim")

    assert len(report.proposals) == 1
    proposal = report.proposals[0]
    assert proposal.automatic_merge is False
    assert proposal.requires_operator_confirmation is True
    assert proposal.source_robot_ids == ("robot-A", "robot-B")
    assert proposal.shared_tracking_namespaces == ("thermal-camera",)

    facade = MissionMemoryFacade(
        store=store,
        runtime_mode="simulation",
        entity_memory=service,
    )
    facade_result = facade.query_entity_identity_proposals(
        _access(),
        entity_kind="victim",
    )
    assert facade_result["operator_confirmation_required"] is True
    assert facade_result["automatic_merge"] is False
    assert facade_result["count"] == 1

    service.apply_operator_resolution(
        mission_id="mission-1",
        action="reject_merge",
        entity_ids=proposal.entity_ids,
        operator_id="operator-1",
        reason="operator identified two distinct victims",
    )

    assert service.list_identity_proposals(
        mission_id="mission-1",
        entity_kind="victim",
    ).proposals == ()
