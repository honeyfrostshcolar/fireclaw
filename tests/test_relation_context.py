from __future__ import annotations

from fireclaw_core.memory.embodied_memory import (
    EmbodiedMemoryProducer,
    EmbodiedMemoryStore,
)
from fireclaw_core.memory.entity_extraction import EntityExtractionPipeline
from fireclaw_core.memory.entity_memory import EntityMemoryService
from fireclaw_core.memory.mission_memory_facade import (
    MemoryAccessContext,
    MissionMemoryFacade,
    MissionMemoryFacadeConfig,
)
from fireclaw_core.memory.mission_memory_tools import (
    QUERY_RELATED_CONTEXT_TOOL,
    MissionMemoryTools,
)


T0 = "2026-07-27T10:00:00+00:00"
T1 = "2026-07-27T10:01:00+00:00"
T2 = "2026-07-27T10:02:00+00:00"
T3 = "2026-07-27T10:03:00+00:00"
T4 = "2026-07-27T10:04:00+00:00"


def _store(tmp_path) -> EmbodiedMemoryStore:
    return EmbodiedMemoryStore(
        tmp_path / "relation-context.jsonl",
        index_path=tmp_path / "relation-context.sqlite",
    )


def _access(*, restricted: bool = False) -> MemoryAccessContext:
    return MemoryAccessContext(
        mission_id="mission-1",
        runtime_mode="simulation",
        requester_id="operator-1",
        scopes=(
            frozenset({"memory.restricted.read"})
            if restricted
            else frozenset()
        ),
    )


def _event(
    store: EmbodiedMemoryStore,
    *,
    event_id: str,
    event_type: str,
    observed_at: str,
    sensitivity: str = "standard",
    mission_id: str = "mission-1",
    runtime_mode: str = "simulation",
):
    return store.record_event(
        event_id=event_id,
        mission_id=mission_id,
        event_type=event_type,
        payload={"label": event_id},
        runtime_mode=runtime_mode,
        source_type="test",
        observed_at=observed_at,
        sensitivity=sensitivity,
    )


def _episode_graph(store: EmbodiedMemoryStore) -> None:
    _event(store, event_id="episode-1", event_type="episode", observed_at=T0)
    _event(store, event_id="obs-1", event_type="observation", observed_at=T1)
    _event(
        store,
        event_id="decision-1",
        event_type="safety_decision",
        observed_at=T2,
    )
    _event(store, event_id="outcome-1", event_type="outcome", observed_at=T3)
    _event(
        store,
        event_id="restricted-1",
        event_type="observation",
        observed_at=T2,
        sensitivity="restricted",
    )
    _event(
        store,
        event_id="behind-restricted",
        event_type="outcome",
        observed_at=T4,
    )
    for relation_id, source, target, relation_type, created_at in (
        ("rel-obs-episode", "obs-1", "episode-1", "belongs_to", T1),
        ("rel-decision-obs", "decision-1", "obs-1", "caused_by", T2),
        ("rel-outcome-decision", "outcome-1", "decision-1", "follows", T3),
        ("rel-cycle", "episode-1", "outcome-1", "supports", T4),
        ("rel-restricted", "restricted-1", "obs-1", "supports", T3),
        (
            "rel-behind-restricted",
            "behind-restricted",
            "restricted-1",
            "supports",
            T4,
        ),
    ):
        store.add_relation(
            relation_id=relation_id,
            mission_id="mission-1",
            source_record_id=source,
            target_record_id=target,
            relation_type=relation_type,
            runtime_mode="simulation",
            created_at=created_at,
        )


def _entity_service(store: EmbodiedMemoryStore) -> EntityMemoryService:
    return EntityMemoryService(
        store=store,
        resolver_producer=EmbodiedMemoryProducer(
            store,
            producer_type="entity_resolver",
            producer_id="test-entity-resolver",
        ),
        runtime_mode="simulation",
    )


def _entity_graph(store: EmbodiedMemoryStore, service: EntityMemoryService) -> str:
    observation = store.record_event(
        event_id="entity-observation",
        mission_id="mission-1",
        event_type="observation",
        payload={
            "entities": [{
                "name": "victim-alpha",
                "entity_kind": "victim",
                "confidence": 0.9,
                "source_track_namespace": "thermal",
                "source_track_id": "7",
                "attributes": {"state": "located"},
            }],
        },
        runtime_mode="simulation",
        source_type="thermal_detector",
        robot_id="robot-A",
        observed_at=T0,
        confidence=0.9,
    )
    report = EntityExtractionPipeline(
        store=store,
        entity_memory=service,
        runtime_mode="simulation",
    ).process_observation(observation)
    mention_id = report.mention_event_ids[0]
    entity_id = service.list_entities(mission_id="mission-1")[0].entity_id
    _event(
        store,
        event_id="restricted-neighbor",
        event_type="observation",
        observed_at=T1,
        sensitivity="restricted",
    )
    _event(
        store,
        event_id="visible-behind-restricted",
        event_type="outcome",
        observed_at=T2,
    )
    store.add_relation(
        relation_id="rel-entity-restricted",
        mission_id="mission-1",
        source_record_id=mention_id,
        target_record_id="restricted-neighbor",
        relation_type="supports",
        runtime_mode="simulation",
        created_at=T1,
    )
    store.add_relation(
        relation_id="rel-restricted-visible",
        mission_id="mission-1",
        source_record_id="restricted-neighbor",
        target_record_id="visible-behind-restricted",
        relation_type="supports",
        runtime_mode="simulation",
        created_at=T2,
    )
    return entity_id


def test_episode_relation_traversal_is_deterministic_and_cycle_safe(tmp_path):
    store = _store(tmp_path)
    _episode_graph(store)
    facade = MissionMemoryFacade(store=store, runtime_mode="simulation")

    first = facade.query_related_context(
        _access(),
        episode_id="episode-1",
        max_depth=4,
        max_nodes=10,
        max_edges=20,
        reference_at=T4,
    )
    second = facade.query_related_context(
        _access(),
        episode_id="episode-1",
        max_depth=4,
        max_nodes=10,
        max_edges=20,
        reference_at=T4,
    )

    assert first == second
    assert first["seed_found"] is True
    assert first["seed"]["seed_type"] == "episode"
    assert {node["record_id"] for node in first["nodes"]} == {
        "episode-1",
        "obs-1",
        "decision-1",
        "outcome-1",
    }
    assert len(first["nodes"]) == len({
        node["record_id"] for node in first["nodes"]
    })
    assert first["restricted_records_omitted"] == 1
    assert first["truncated"] is False
    assert "behind-restricted" not in {
        node["record_id"] for node in first["nodes"]
    }
    node_ids = {node["record_id"] for node in first["nodes"]}
    assert all(
        edge["source_record_id"] in node_ids
        and edge["target_record_id"] in node_ids
        for edge in first["edges"]
    )

    edge_bounded = facade.query_related_context(
        _access(),
        episode_id="episode-1",
        max_depth=4,
        max_nodes=10,
        max_edges=1,
        reference_at=T4,
    )
    assert edge_bounded["edge_count"] == 1
    assert edge_bounded["truncated"] is True


def test_relation_filters_direction_and_bounds_are_enforced(tmp_path):
    store = _store(tmp_path)
    _episode_graph(store)
    facade = MissionMemoryFacade(
        store=store,
        runtime_mode="simulation",
        config=MissionMemoryFacadeConfig(
            max_relation_depth=3,
            max_relation_edges=10,
            max_relation_neighbors_per_node=10,
        ),
    )

    result = facade.query_related_context(
        _access(),
        episode_id="episode-1",
        direction="incoming",
        relation_types=["belongs_to", "caused_by"],
        max_depth=2,
        max_nodes=2,
        max_edges=5,
        reference_at=T4,
    )

    assert [node["record_id"] for node in result["nodes"]] == [
        "episode-1",
        "obs-1",
    ]
    assert [edge["relation_id"] for edge in result["edges"]] == [
        "rel-obs-episode",
    ]
    assert result["truncated"] is True
    assert result["query"] == {
        "direction": "incoming",
        "relation_types": ["belongs_to", "caused_by"],
        "max_depth": 2,
        "max_nodes": 2,
        "max_edges": 5,
    }


def test_entity_traversal_does_not_cross_restricted_bridge(tmp_path):
    store = _store(tmp_path)
    service = _entity_service(store)
    entity_id = _entity_graph(store, service)
    facade = MissionMemoryFacade(
        store=store,
        runtime_mode="simulation",
        entity_memory=service,
    )

    public = facade.query_related_context(
        _access(),
        entity_id=entity_id,
        relation_types=["observed_in", "supports"],
        max_depth=3,
        max_nodes=10,
        max_edges=20,
        reference_at=T4,
    )
    privileged = facade.query_related_context(
        _access(restricted=True),
        entity_id=entity_id,
        relation_types=["observed_in", "supports"],
        max_depth=3,
        max_nodes=10,
        max_edges=20,
        reference_at=T4,
    )

    public_ids = {node["record_id"] for node in public["nodes"]}
    privileged_ids = {node["record_id"] for node in privileged["nodes"]}
    assert public["seed"]["entity_id"] == entity_id
    assert public["restricted_records_omitted"] == 1
    assert "restricted-neighbor" not in public_ids
    assert "visible-behind-restricted" not in public_ids
    assert {"restricted-neighbor", "visible-behind-restricted"} <= privileged_ids


def test_related_context_tool_schema_and_dispatch(tmp_path):
    store = _store(tmp_path)
    _episode_graph(store)
    facade = MissionMemoryFacade(store=store, runtime_mode="simulation")
    tools = MissionMemoryTools(facade)
    schemas = {
        schema["function"]["name"]: schema
        for schema in tools.tool_schemas()
    }

    schema = schemas[QUERY_RELATED_CONTEXT_TOOL]["function"]["parameters"]
    assert schema["properties"]["max_depth"]["maximum"] == 4
    assert schema["properties"]["max_edges"]["maximum"] == 200
    result = tools.execute(
        QUERY_RELATED_CONTEXT_TOOL,
        {
            "episode_id": "episode-1",
            "direction": "incoming",
            "relation_types": ["belongs_to"],
            "max_depth": 1,
            "max_nodes": 5,
            "max_edges": 5,
        },
        access=_access(),
    )
    assert [node["record_id"] for node in result["nodes"]] == [
        "episode-1",
        "obs-1",
    ]
