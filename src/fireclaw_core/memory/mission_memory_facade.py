"""Permission-aware mission memory reads for firefighting agents."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Callable

from fireclaw_core.infra.log_redaction import redact_dict
from fireclaw_core.memory.embodied_memory import (
    EMBODIED_EVENT_TYPES,
    MEMORY_RELATION_TYPES,
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryEvent,
    EmbodiedMemoryStore,
    SpatialMemoryContext,
)
from fireclaw_core.memory.entity_memory import (
    ENTITY_KINDS,
    ENTITY_STATUSES,
    EntityMemoryService,
    FireClawEntity,
)
from fireclaw_core.memory.memory_lifecycle import MissionMemoryLifecycleStore
from fireclaw_core.memory.working_memory import EmbodiedWorkingMemory, WorkingMemoryConfig


MEMORY_RESTRICTED_READ_SCOPE = "memory.restricted.read"
SPATIAL_MEMORY_TYPES = frozenset({"entity", "gist", "observation"})


@dataclass(frozen=True)
class _SpatialMatch:
    center_distance_m: float
    distance_to_uncertainty_m: float
    uncertainty_radius_m: float
    source: str
    dimensions: int
    geometry: dict[str, Any] | None = None

    def sort_key(self) -> tuple[float, float, float, str]:
        return (
            self.distance_to_uncertainty_m,
            self.center_distance_m,
            self.uncertainty_radius_m,
            self.source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "dimensions": self.dimensions,
            "center_distance_m": self.center_distance_m,
            "distance_to_uncertainty_m": self.distance_to_uncertainty_m,
            "uncertainty_radius_m": self.uncertainty_radius_m,
        }


@dataclass
class _IndexedSpatialResult:
    ranked: list[
        tuple[_SpatialMatch, str, str, EmbodiedMemoryEvent | FireClawEntity]
    ]
    restricted_records_omitted: int
    restricted_entities_omitted: int


@dataclass(frozen=True)
class MemoryAccessContext:
    """Server-bound identity and isolation context for one memory read."""

    mission_id: str
    runtime_mode: str
    requester_id: str
    scopes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.mission_id.strip():
            raise ValueError("mission_id must not be empty")
        if self.runtime_mode not in MEMORY_RUNTIME_MODES:
            raise ValueError(
                f"Invalid runtime_mode: {self.runtime_mode}. "
                f"Must be one of: {sorted(MEMORY_RUNTIME_MODES)}"
            )
        if not self.requester_id.strip():
            raise ValueError("requester_id must not be empty")

    @property
    def can_read_restricted(self) -> bool:
        return "admin" in self.scopes or MEMORY_RESTRICTED_READ_SCOPE in self.scopes


@dataclass(frozen=True)
class MissionMemoryFacadeConfig:
    default_limit: int = 20
    max_results: int = 100
    max_context_minutes: float = 60.0
    max_spatial_radius_m: float = 500.0
    max_relation_depth: int = 4
    max_relation_edges: int = 200
    max_relation_neighbors_per_node: int = 50
    working_memory: WorkingMemoryConfig = WorkingMemoryConfig()

    def __post_init__(self) -> None:
        if not 1 <= self.default_limit <= self.max_results:
            raise ValueError("default_limit must be between 1 and max_results")
        if self.max_context_minutes <= 0:
            raise ValueError("max_context_minutes must be positive")
        if self.max_spatial_radius_m <= 0:
            raise ValueError("max_spatial_radius_m must be positive")
        for name, value in (
            ("max_relation_depth", self.max_relation_depth),
            ("max_relation_edges", self.max_relation_edges),
            ("max_relation_neighbors_per_node", self.max_relation_neighbors_per_node),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


class MissionMemoryFacade:
    """One read-only boundary over mission evidence, summaries, and entities.

    Results are evidence for planning only. They never authorize physical action
    and always retain identifiers needed to inspect the original records.
    """

    def __init__(
        self,
        *,
        store: EmbodiedMemoryStore,
        runtime_mode: str,
        working_memory: EmbodiedWorkingMemory | None = None,
        entity_memory: EntityMemoryService | None = None,
        robot_state_provider: Callable[[], dict[str, Any]] | None = None,
        lifecycle: MissionMemoryLifecycleStore | None = None,
        config: MissionMemoryFacadeConfig | None = None,
    ) -> None:
        if runtime_mode not in MEMORY_RUNTIME_MODES:
            raise ValueError(
                f"Invalid runtime_mode: {runtime_mode}. "
                f"Must be one of: {sorted(MEMORY_RUNTIME_MODES)}"
            )
        self._store = store
        self._runtime_mode = runtime_mode
        self._working_memory = working_memory
        self._entity_memory = entity_memory
        self._robot_state_provider = robot_state_provider
        self._lifecycle = lifecycle
        self.config = config or MissionMemoryFacadeConfig()
        self._freshness_config = (
            working_memory.config if working_memory is not None else self.config.working_memory
        )

    @property
    def runtime_mode(self) -> str:
        return self._runtime_mode

    def get_current_context(
        self,
        access: MemoryAccessContext,
        *,
        robot_id: str | None = None,
        recent_minutes: float = 5.0,
        limit: int | None = None,
        reference_at: str | None = None,
    ) -> dict[str, Any]:
        self._validate_access(access)
        if not 0 < recent_minutes <= self.config.max_context_minutes:
            raise ValueError(
                f"recent_minutes must be between 0 and {self.config.max_context_minutes}"
            )
        bounded_limit = self._bounded_limit(limit)
        reference = _parse_timestamp(reference_at) if reference_at else datetime.now(timezone.utc)
        start = reference - timedelta(minutes=recent_minutes)
        events = self._visible_events(
            access,
            robot_id=robot_id,
            start_at=start,
            end_at=reference,
        )
        events = events[-bounded_limit:]
        working_memory_event_ids: list[str] = []
        if self._working_memory is not None:
            snapshot = self._working_memory.snapshot(
                runtime_mode=access.runtime_mode,
                mission_id=access.mission_id,
                robot_id=robot_id,
                reference_at=reference.isoformat(),
                include_restricted=access.can_read_restricted,
                limit=bounded_limit,
            )
            working_memory_event_ids = [event.event_id for event in snapshot.events]
        latest_by_type: dict[str, dict[str, Any]] = {}
        for event in events:
            latest_by_type[event.event_type] = self._event_payload(event, reference)
        omitted = self._restricted_omitted_count(
            access,
            robot_id=robot_id,
            start_at=start,
            end_at=reference,
        )
        current_pose = next(
            (event.pose.to_dict() for event in reversed(events) if event.pose is not None),
            None,
        )
        return self._envelope(
            access,
            {
                "reference_at": reference.isoformat(),
                "window_start_at": start.isoformat(),
                "robot_id": robot_id,
                "current_pose": current_pose,
                "latest_by_type": latest_by_type,
                "events": [self._event_payload(event, reference) for event in events],
                "count": len(events),
                "working_memory_event_ids": working_memory_event_ids,
                "restricted_records_omitted": omitted,
            },
            evidence_ids=[event.event_id for event in events],
            requires_revalidation=(
                _is_cross_robot(events)
                or any(self._requires_revalidation(event, reference) for event in events)
            ),
        )

    def get_episode_summary(
        self,
        access: MemoryAccessContext,
        *,
        episode_id: str | None = None,
        robot_id: str | None = None,
        subtask_id: str | None = None,
        limit: int | None = None,
        reference_at: str | None = None,
    ) -> dict[str, Any]:
        self._validate_access(access)
        reference = _parse_timestamp(reference_at) if reference_at else datetime.now(timezone.utc)
        episodes = self._visible_events(
            access,
            event_types=frozenset({"episode"}),
            robot_id=robot_id,
            subtask_id=subtask_id,
        )
        if episode_id is not None:
            episodes = [event for event in episodes if event.event_id == episode_id]
        episodes = episodes[-self._bounded_limit(limit):]
        gists = self._visible_events(access, event_types=frozenset({"gist"}))
        items: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        for episode in episodes:
            episode_gists = [
                gist for gist in gists
                if gist.episode_id == episode.event_id
                or gist.payload.get("episode_event_id") == episode.event_id
            ]
            item_evidence = _unique_ids([
                episode.event_id,
                *episode.derived_from,
                *(gist.event_id for gist in episode_gists),
                *(source for gist in episode_gists for source in gist.derived_from),
            ])
            evidence_ids.extend(item_evidence)
            items.append({
                "episode": self._event_payload(episode, reference),
                "gists": [self._event_payload(gist, reference) for gist in episode_gists],
                "evidence_event_ids": item_evidence,
                "requires_current_state_revalidation": True,
            })
        return self._envelope(
            access,
            {"episodes": items, "count": len(items)},
            evidence_ids=evidence_ids,
            requires_revalidation=bool(items),
        )

    def query_gists(
        self,
        access: MemoryAccessContext,
        *,
        episode_id: str | None = None,
        robot_id: str | None = None,
        frame_id: str | None = None,
        floor: str | None = None,
        near_x: float | None = None,
        near_y: float | None = None,
        near_z: float | None = None,
        radius_m: float | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        limit: int | None = None,
        reference_at: str | None = None,
    ) -> dict[str, Any]:
        self._validate_access(access)
        spatial_values = (near_x, near_y, radius_m)
        spatial_query = any(value is not None for value in spatial_values)
        if spatial_query and (
            frame_id is None or not all(value is not None for value in spatial_values)
        ):
            raise ValueError(
                "spatial Gist query requires frame_id, near_x, near_y, and radius_m"
            )
        if near_z is not None and not spatial_query:
            raise ValueError("near_z requires a complete spatial Gist query")
        if spatial_query:
            assert frame_id is not None
            assert near_x is not None and near_y is not None and radius_m is not None
            self._validate_spatial_point(
                frame_id=frame_id,
                x=near_x,
                y=near_y,
                z=near_z,
                radius_m=radius_m,
            )
        reference = _parse_timestamp(reference_at) if reference_at else datetime.now(timezone.utc)
        start, end = _optional_time_range(start_at, end_at)
        events = self._visible_events(
            access,
            event_types=frozenset({"gist"}),
            robot_id=robot_id,
            start_at=start,
            end_at=end,
        )
        if episode_id is not None:
            events = [event for event in events if event.episode_id == episode_id]
        if frame_id is not None:
            events = [
                event for event in events
                if _event_matches_frame_floor(event, frame_id=frame_id, floor=None)
            ]
        if floor is not None:
            events = [
                event for event in events
                if _event_matches_frame_floor(event, frame_id=None, floor=floor)
            ]
        matches: list[_SpatialMatch | None]
        if spatial_query:
            assert frame_id is not None
            assert near_x is not None and near_y is not None and radius_m is not None
            ranked = []
            for event in events:
                match = _spatial_match(
                    event,
                    frame_id=frame_id,
                    x=near_x,
                    y=near_y,
                    z=near_z,
                    radius_m=radius_m,
                    floor=floor,
                )
                if match is not None:
                    ranked.append((match, event))
            ranked.sort(
                key=lambda item: (
                    item[0].sort_key(),
                    item[1].observed_at,
                    item[1].event_id,
                )
            )
            ranked = ranked[:self._bounded_limit(limit)]
            events = [event for _, event in ranked]
            matches = [match for match, _ in ranked]
        else:
            events = events[-self._bounded_limit(limit):]
            matches = [None] * len(events)
        payloads = [self._event_payload(event, reference) for event in events]
        for payload, match in zip(payloads, matches):
            if payload is not None and match is not None:
                _add_spatial_match(payload, match)
        return self._envelope(
            access,
            {
                "gists": payloads,
                "count": len(events),
                "retrieval_mode": (
                    "exact_conservative_spatial_intersection"
                    if spatial_query
                    else "structured_filters_only"
                ),
                "query": (
                    {
                        "frame_id": frame_id,
                        "x": near_x,
                        "y": near_y,
                        "z": near_z,
                        "radius_m": radius_m,
                        "floor": floor,
                    }
                    if spatial_query
                    else None
                ),
                "restricted_records_omitted": self._restricted_gist_count(
                    access,
                    episode_id=episode_id,
                    robot_id=robot_id,
                    frame_id=frame_id,
                    floor=floor,
                    start_at=start,
                    end_at=end,
                    x=near_x if spatial_query else None,
                    y=near_y if spatial_query else None,
                    z=near_z if spatial_query else None,
                    radius_m=radius_m if spatial_query else None,
                ),
            },
            evidence_ids=[source for event in events for source in (event.event_id, *event.derived_from)],
            requires_revalidation=bool(events),
        )

    def query_temporal(
        self,
        access: MemoryAccessContext,
        *,
        start_at: str,
        end_at: str,
        event_types: list[str] | tuple[str, ...] | None = None,
        robot_id: str | None = None,
        subtask_id: str | None = None,
        limit: int | None = None,
        reference_at: str | None = None,
    ) -> dict[str, Any]:
        self._validate_access(access)
        start, end = _time_range(start_at, end_at)
        selected_types = self._event_types(event_types)
        reference = _parse_timestamp(reference_at) if reference_at else datetime.now(timezone.utc)
        events = self._visible_events(
            access,
            event_types=selected_types,
            robot_id=robot_id,
            subtask_id=subtask_id,
            start_at=start,
            end_at=end,
        )[-self._bounded_limit(limit):]
        return self._event_query_result(access, events, reference)

    def query_spatial(
        self,
        access: MemoryAccessContext,
        *,
        frame_id: str,
        x: float,
        y: float,
        radius_m: float,
        z: float | None = None,
        floor: str | None = None,
        event_types: list[str] | tuple[str, ...] | None = None,
        robot_id: str | None = None,
        limit: int | None = None,
        reference_at: str | None = None,
    ) -> dict[str, Any]:
        self._validate_access(access)
        self._validate_spatial_point(
            frame_id=frame_id,
            x=x,
            y=y,
            z=z,
            radius_m=radius_m,
        )
        selected_types = self._event_types(event_types)
        reference = _parse_timestamp(reference_at) if reference_at else datetime.now(timezone.utc)
        ranked: list[tuple[_SpatialMatch, EmbodiedMemoryEvent]] = []
        for event in self._visible_events(
            access,
            event_types=selected_types,
            robot_id=robot_id,
        ):
            match = _spatial_match(
                event,
                frame_id=frame_id,
                x=x,
                y=y,
                z=z,
                radius_m=radius_m,
                floor=floor,
            )
            if match is not None:
                ranked.append((match, event))
        ranked.sort(
            key=lambda item: (
                item[0].sort_key(),
                item[1].observed_at,
                item[1].event_id,
            )
        )
        ranked = ranked[:self._bounded_limit(limit)]
        events = [event for _, event in ranked]
        result = self._event_query_result(access, events, reference)
        result["query"] = {
            "frame_id": frame_id,
            "x": x,
            "y": y,
            "z": z,
            "radius_m": radius_m,
            "floor": floor,
        }
        result["retrieval_mode"] = "exact_conservative_spatial_intersection"
        result["restricted_records_omitted"] = self._restricted_spatial_event_count(
            access,
            event_types=selected_types,
            robot_id=robot_id,
            frame_id=frame_id,
            x=x,
            y=y,
            z=z,
            radius_m=radius_m,
            floor=floor,
        )
        for item, (match, _) in zip(result["events"], ranked):
            _add_spatial_match(item, match)
        return result

    def query_nearest(
        self,
        access: MemoryAccessContext,
        *,
        frame_id: str,
        x: float,
        y: float,
        z: float | None = None,
        floor: str | None = None,
        max_distance_m: float | None = None,
        memory_types: list[str] | tuple[str, ...] | None = None,
        entity_kinds: list[str] | tuple[str, ...] | None = None,
        entity_statuses: list[str] | tuple[str, ...] | None = None,
        limit: int | None = None,
        reference_at: str | None = None,
    ) -> dict[str, Any]:
        """Return nearest Observation, Gist, and Entity uncertainty regions."""
        self._validate_access(access)
        search_radius = (
            self.config.max_spatial_radius_m
            if max_distance_m is None
            else max_distance_m
        )
        self._validate_spatial_point(
            frame_id=frame_id,
            x=x,
            y=y,
            z=z,
            radius_m=search_radius,
            radius_name="max_distance_m",
        )
        selected_memory_types = _validated_values(
            "memory_types",
            memory_types,
            SPATIAL_MEMORY_TYPES,
        )
        if selected_memory_types is None:
            selected_memory_types = SPATIAL_MEMORY_TYPES
        selected_entity_kinds = _validated_values(
            "entity_kinds",
            entity_kinds,
            ENTITY_KINDS,
        )
        selected_entity_statuses = _validated_values(
            "entity_statuses",
            entity_statuses,
            ENTITY_STATUSES,
        )
        if "entity" not in selected_memory_types and (
            selected_entity_kinds is not None or selected_entity_statuses is not None
        ):
            raise ValueError(
                "entity_kinds and entity_statuses require entity in memory_types"
            )
        reference = _parse_timestamp(reference_at) if reference_at else datetime.now(timezone.utc)
        selected_event_types = frozenset(
            selected_memory_types & {"gist", "observation"}
        )
        entities: list[FireClawEntity] = []
        if "entity" in selected_memory_types:
            service = self._require_entity_memory()
            entities = service.list_entities(mission_id=access.mission_id)
            if selected_entity_kinds is not None:
                entities = [
                    entity for entity in entities
                    if entity.entity_kind in selected_entity_kinds
                ]
            if selected_entity_statuses is not None:
                entities = [
                    entity for entity in entities
                    if entity.status in selected_entity_statuses
                ]

        indexed = self._indexed_spatial_candidates(
            access,
            frame_id=frame_id,
            x=x,
            y=y,
            z=z,
            radius_m=search_radius,
            floor=floor,
            selected_memory_types=selected_memory_types,
            selected_event_types=selected_event_types,
            selected_entity_kinds=selected_entity_kinds,
            selected_entity_statuses=selected_entity_statuses,
            entities=entities,
        )
        if indexed is not None:
            ranked = indexed.ranked
            restricted_records_omitted = indexed.restricted_records_omitted
            restricted_entities_omitted = indexed.restricted_entities_omitted
            candidate_backend = "sqlite_rtree"
        else:
            ranked = []
            if selected_event_types:
                for event in self._visible_events(access, event_types=selected_event_types):
                    match = _spatial_match(
                        event,
                        frame_id=frame_id,
                        x=x,
                        y=y,
                        z=z,
                        radius_m=search_radius,
                        floor=floor,
                    )
                    if match is not None:
                        ranked.append((match, event.event_type, event.event_id, event))

            restricted_entities_omitted = 0
            if "entity" in selected_memory_types:
                positioned: list[tuple[_SpatialMatch, FireClawEntity]] = []
                for entity in entities:
                    match = _pose_spatial_match(
                        entity.current_pose,
                        frame_id=frame_id,
                        x=x,
                        y=y,
                        z=z,
                        radius_m=search_radius,
                        floor=floor,
                        source="entity_current_pose",
                    )
                    if match is not None:
                        positioned.append((match, entity))
                visible_entities, restricted_entities_omitted = self._filter_entities(
                    access,
                    [entity for _, entity in positioned],
                )
                visible_entity_ids = {entity.entity_id for entity in visible_entities}
                for match, entity in positioned:
                    if entity.entity_id in visible_entity_ids:
                        ranked.append((match, "entity", entity.entity_id, entity))
            restricted_records_omitted = self._restricted_spatial_event_count(
                access,
                event_types=selected_event_types,
                robot_id=None,
                frame_id=frame_id,
                x=x,
                y=y,
                z=z,
                radius_m=search_radius,
                floor=floor,
            )
            candidate_backend = "linear_scan"

        ranked.sort(key=lambda item: (item[0].sort_key(), item[1], item[2]))
        ranked = ranked[:self._bounded_limit(limit)]
        results: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        event_results: list[EmbodiedMemoryEvent] = []
        for match, memory_type, record_id, record in ranked:
            if isinstance(record, EmbodiedMemoryEvent):
                payload = self._event_payload(record, reference)
                assert payload is not None
                record_evidence_ids = _unique_ids([record.event_id, *record.derived_from])
                event_results.append(record)
            else:
                payload = self._entity_payload(record, reference)
                record_evidence_ids = self._entity_evidence_ids(record)
            evidence_ids.extend(record_evidence_ids)
            item = {
                "memory_type": memory_type,
                "record_id": record_id,
                "record": payload,
                "evidence_event_ids": record_evidence_ids,
            }
            _add_spatial_match(item, match)
            results.append(item)
        return self._envelope(
            access,
            {
                "results": results,
                "count": len(results),
                "query": {
                    "frame_id": frame_id,
                    "x": x,
                    "y": y,
                    "z": z,
                    "floor": floor,
                    "max_distance_m": search_radius,
                    "memory_types": sorted(selected_memory_types),
                    "entity_kinds": (
                        sorted(selected_entity_kinds)
                        if selected_entity_kinds is not None
                        else None
                    ),
                    "entity_statuses": (
                        sorted(selected_entity_statuses)
                        if selected_entity_statuses is not None
                        else None
                    ),
                },
                "retrieval_mode": "exact_conservative_spatial_nearest",
                "candidate_backend": candidate_backend,
                "restricted_records_omitted": restricted_records_omitted,
                "restricted_entities_omitted": restricted_entities_omitted,
            },
            evidence_ids=evidence_ids,
            requires_revalidation=(
                bool(results)
                and (
                    any(memory_type in {"entity", "gist"} for _, memory_type, _, _ in ranked)
                    or _is_cross_robot(event_results)
                    or any(
                        self._requires_revalidation(event, reference)
                        for event in event_results
                    )
                )
            ),
        )

    def query_entities(
        self,
        access: MemoryAccessContext,
        *,
        entity_kind: str | None = None,
        status: str | None = None,
        name: str | None = None,
        frame_id: str | None = None,
        near_x: float | None = None,
        near_y: float | None = None,
        near_z: float | None = None,
        radius_m: float | None = None,
        floor: str | None = None,
        last_seen_after: str | None = None,
        limit: int | None = None,
        reference_at: str | None = None,
    ) -> dict[str, Any]:
        self._validate_access(access)
        service = self._require_entity_memory()
        spatial_values = (frame_id, near_x, near_y, radius_m)
        spatial_query = any(value is not None for value in spatial_values)
        if spatial_query and not all(value is not None for value in spatial_values):
            raise ValueError(
                "spatial entity query requires frame_id, near_x, near_y, and radius_m"
            )
        if near_z is not None and not spatial_query:
            raise ValueError("near_z requires a complete spatial entity query")
        if spatial_query:
            assert frame_id is not None
            assert near_x is not None and near_y is not None and radius_m is not None
            self._validate_spatial_point(
                frame_id=frame_id,
                x=near_x,
                y=near_y,
                z=near_z,
                radius_m=radius_m,
            )
        reference = _parse_timestamp(reference_at) if reference_at else datetime.now(timezone.utc)
        entities = service.list_entities(
            mission_id=access.mission_id,
            entity_kind=entity_kind,
            status=status,
            name=name,
            last_seen_after=last_seen_after,
        )
        positioned: list[tuple[_SpatialMatch | None, FireClawEntity]] = []
        for entity in entities:
            if floor is not None and (
                entity.current_pose is None or entity.current_pose.floor != floor
            ):
                continue
            if spatial_query:
                assert frame_id is not None
                assert near_x is not None and near_y is not None and radius_m is not None
                match = _pose_spatial_match(
                    entity.current_pose,
                    frame_id=frame_id,
                    x=near_x,
                    y=near_y,
                    z=near_z,
                    radius_m=radius_m,
                    floor=floor,
                    source="entity_current_pose",
                )
                if match is None:
                    continue
            else:
                match = None
            positioned.append((match, entity))
        visible, omitted = self._filter_entities(
            access,
            [entity for _, entity in positioned],
        )
        visible_ids = {entity.entity_id for entity in visible}
        positioned = [
            (match, entity)
            for match, entity in positioned
            if entity.entity_id in visible_ids
        ]
        if spatial_query:
            positioned.sort(
                key=lambda item: (
                    item[0].sort_key() if item[0] is not None else (math.inf,),
                    item[1].entity_id,
                )
            )
        positioned = positioned[:self._bounded_limit(limit)]
        visible = [entity for _, entity in positioned]
        payloads = [self._entity_payload(entity, reference) for entity in visible]
        for payload, (match, _) in zip(payloads, positioned):
            if match is not None:
                _add_spatial_match(payload, match)
        evidence_ids = [event_id for entity in visible for event_id in self._entity_evidence_ids(entity)]
        return self._envelope(
            access,
            {
                "entities": payloads,
                "count": len(payloads),
                "restricted_entities_omitted": omitted,
                "retrieval_mode": (
                    "exact_conservative_spatial_intersection"
                    if spatial_query
                    else "structured_filters_only"
                ),
                "query": (
                    {
                        "frame_id": frame_id,
                        "x": near_x,
                        "y": near_y,
                        "z": near_z,
                        "radius_m": radius_m,
                        "floor": floor,
                    }
                    if spatial_query or floor is not None
                    else None
                ),
            },
            evidence_ids=evidence_ids,
            requires_revalidation=bool(payloads),
        )

    def query_related_context(
        self,
        access: MemoryAccessContext,
        *,
        entity_id: str | None = None,
        episode_id: str | None = None,
        direction: str = "both",
        relation_types: list[str] | tuple[str, ...] | None = None,
        max_depth: int = 2,
        max_nodes: int | None = None,
        max_edges: int | None = None,
        reference_at: str | None = None,
    ) -> dict[str, Any]:
        """Traverse a bounded, permission-filtered relation graph from one seed."""
        self._validate_access(access)
        if (entity_id is None) == (episode_id is None):
            raise ValueError("provide exactly one of entity_id or episode_id")
        seed_id = entity_id if entity_id is not None else episode_id
        if not isinstance(seed_id, str) or not seed_id.strip():
            raise ValueError("entity_id or episode_id must not be empty")
        if direction not in {"incoming", "outgoing", "both"}:
            raise ValueError("direction must be one of: incoming, outgoing, both")
        if relation_types is None:
            selected_relation_types = frozenset(MEMORY_RELATION_TYPES)
        else:
            if not isinstance(relation_types, (list, tuple)) or any(
                not isinstance(value, str) or not value.strip()
                for value in relation_types
            ):
                raise ValueError(
                    "relation_types must be a list or tuple of non-empty strings"
                )
            selected_relation_types = frozenset(relation_types)
        if not selected_relation_types:
            raise ValueError("relation_types must not be empty")
        invalid_relation_types = selected_relation_types - MEMORY_RELATION_TYPES
        if invalid_relation_types:
            raise ValueError(
                f"Invalid relation_types: {sorted(invalid_relation_types)}"
            )
        if (
            isinstance(max_depth, bool)
            or not isinstance(max_depth, int)
            or not 0 <= max_depth <= self.config.max_relation_depth
        ):
            raise ValueError(
                f"max_depth must be between 0 and {self.config.max_relation_depth}"
            )
        node_limit = self._bounded_limit(max_nodes)
        edge_limit = (
            min(self.config.max_relation_edges, node_limit * 4)
            if max_edges is None
            else max_edges
        )
        if (
            isinstance(edge_limit, bool)
            or not isinstance(edge_limit, int)
            or not 1 <= edge_limit <= self.config.max_relation_edges
        ):
            raise ValueError(
                f"max_edges must be between 1 and {self.config.max_relation_edges}"
            )

        reference = (
            _parse_timestamp(reference_at)
            if reference_at
            else datetime.now(timezone.utc)
        )
        scoped_events = [
            event
            for event in self._store.list_events(mission_id=access.mission_id)
            if event.runtime_mode == access.runtime_mode
        ]
        events_by_id = {event.event_id: event for event in scoped_events}
        visible_event_ids = {
            event.event_id
            for event in scoped_events
            if access.can_read_restricted or event.sensitivity != "restricted"
        }
        restricted_event_ids = set(events_by_id) - visible_event_ids

        seed_payload: dict[str, Any] | None
        seed_record_ids: list[str]
        restricted_omitted: set[str] = set()
        if entity_id is not None:
            service = self._require_entity_memory()
            entity = service.get_entity(
                mission_id=access.mission_id,
                entity_id=entity_id,
            )
            if entity is None or entity.runtime_mode != access.runtime_mode:
                seed_payload = None
                seed_record_ids = []
            else:
                visible_entities, restricted_entities = self._filter_entities(
                    access,
                    [entity],
                )
                if not visible_entities:
                    seed_payload = None
                    seed_record_ids = []
                    restricted_omitted.update(
                        event_id
                        for event_id in self._entity_evidence_ids(entity)
                        if event_id in restricted_event_ids
                    )
                else:
                    seed_payload = {
                        "seed_type": "entity",
                        "entity_id": entity.entity_id,
                        "record": self._entity_payload(entity, reference),
                    }
                    seed_record_ids = [
                        event_id
                        for event_id in self._entity_evidence_ids(entity)
                        if event_id in visible_event_ids
                    ]
                if restricted_entities:
                    restricted_omitted.update(
                        set(self._entity_evidence_ids(entity))
                        & restricted_event_ids
                    )
        else:
            episode = events_by_id.get(str(episode_id))
            if episode is None or episode.event_type != "episode":
                seed_payload = None
                seed_record_ids = []
            elif episode.event_id not in visible_event_ids:
                seed_payload = None
                seed_record_ids = []
                restricted_omitted.add(episode.event_id)
            else:
                seed_payload = {
                    "seed_type": "episode",
                    "episode_id": episode.event_id,
                    "record": self._event_payload(episode, reference),
                }
                seed_record_ids = [episode.event_id]

        seed_record_ids = sorted(
            set(seed_record_ids),
            key=lambda event_id: (
                events_by_id[event_id].observed_at,
                events_by_id[event_id].created_at,
                event_id,
            ),
            reverse=True,
        )
        truncated = len(seed_record_ids) > node_limit
        seed_record_ids = seed_record_ids[:node_limit]

        relations = [
            relation
            for relation in self._store.list_relations(mission_id=access.mission_id)
            if relation.runtime_mode == access.runtime_mode
            and relation.relation_type in selected_relation_types
        ]
        relations.sort(
            key=lambda relation: (
                relation.created_at,
                relation.relation_type,
                relation.relation_id,
            )
        )
        adjacency: dict[str, list[tuple[Any, str, str]]] = {}
        for relation in relations:
            if direction in {"outgoing", "both"}:
                adjacency.setdefault(relation.source_record_id, []).append(
                    (relation, relation.target_record_id, "outgoing")
                )
            if direction in {"incoming", "both"}:
                adjacency.setdefault(relation.target_record_id, []).append(
                    (relation, relation.source_record_id, "incoming")
                )

        depths = {event_id: 0 for event_id in seed_record_ids}
        queue = list(seed_record_ids)
        seen_relation_ids: set[str] = set()
        traversed_edges: list[dict[str, Any]] = []
        cursor = 0
        while cursor < len(queue) and len(traversed_edges) < edge_limit:
            current_id = queue[cursor]
            cursor += 1
            current_depth = depths[current_id]
            if current_depth >= max_depth:
                continue
            neighbors = adjacency.get(current_id, [])
            if len(neighbors) > self.config.max_relation_neighbors_per_node:
                truncated = True
            bounded_neighbors = neighbors[
                : self.config.max_relation_neighbors_per_node
            ]
            for neighbor_index, (
                relation,
                neighbor_id,
                traversal_direction,
            ) in enumerate(bounded_neighbors):
                if relation.relation_id in seen_relation_ids:
                    continue
                if neighbor_id in restricted_event_ids:
                    restricted_omitted.add(neighbor_id)
                    seen_relation_ids.add(relation.relation_id)
                    continue
                if neighbor_id not in visible_event_ids:
                    continue
                if neighbor_id not in depths:
                    if len(depths) >= node_limit:
                        truncated = True
                        continue
                    depths[neighbor_id] = current_depth + 1
                    queue.append(neighbor_id)
                seen_relation_ids.add(relation.relation_id)
                traversed_edges.append({
                    "relation_id": relation.relation_id,
                    "source_record_id": relation.source_record_id,
                    "target_record_id": relation.target_record_id,
                    "relation_type": relation.relation_type,
                    "created_at": relation.created_at,
                    "metadata": redact_dict(dict(relation.metadata or {})),
                    "traversal_direction": traversal_direction,
                    "discovered_at_depth": current_depth + 1,
                })
                if len(traversed_edges) >= edge_limit:
                    truncated = (
                        truncated
                        or neighbor_index + 1 < len(bounded_neighbors)
                        or cursor < len(queue)
                    )
                    break

        ordered_node_ids = sorted(
            depths,
            key=lambda event_id: (
                depths[event_id],
                events_by_id[event_id].observed_at,
                events_by_id[event_id].created_at,
                event_id,
            ),
        )
        nodes = [
            {
                "record_id": event_id,
                "depth": depths[event_id],
                "record": self._event_payload(events_by_id[event_id], reference),
            }
            for event_id in ordered_node_ids
        ]
        node_events = [events_by_id[event_id] for event_id in ordered_node_ids]
        evidence_ids = [
            source_id
            for event in node_events
            for source_id in (event.event_id, *event.derived_from)
        ]
        return self._envelope(
            access,
            {
                "seed": seed_payload,
                "seed_found": seed_payload is not None,
                "seed_record_ids": seed_record_ids,
                "nodes": nodes,
                "edges": traversed_edges,
                "node_count": len(nodes),
                "edge_count": len(traversed_edges),
                "restricted_records_omitted": len(restricted_omitted),
                "truncated": truncated,
                "retrieval_mode": "bounded_permission_filtered_relation_traversal",
                "query": {
                    "direction": direction,
                    "relation_types": sorted(selected_relation_types),
                    "max_depth": max_depth,
                    "max_nodes": node_limit,
                    "max_edges": edge_limit,
                },
            },
            evidence_ids=evidence_ids,
            requires_revalidation=(
                bool(nodes)
                and (
                    entity_id is not None
                    or _is_cross_robot(node_events)
                    or any(
                        self._requires_revalidation(event, reference)
                        for event in node_events
                    )
                )
            ),
        )

    def query_entity_identity_proposals(
        self,
        access: MemoryAccessContext,
        *,
        entity_kind: str | None = None,
        entity_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Read bounded cross-robot identity candidates without merging state."""
        self._validate_access(access)
        service = self._require_entity_memory()
        report = service.list_identity_proposals(
            mission_id=access.mission_id,
            entity_kind=entity_kind,
            entity_id=entity_id,
        )
        all_entities = service.list_entities(mission_id=access.mission_id)
        visible_entities, _ = self._filter_entities(access, all_entities)
        visible_entity_ids = {entity.entity_id for entity in visible_entities}
        visible_proposals = [
            proposal
            for proposal in report.proposals
            if all(entity_value in visible_entity_ids for entity_value in proposal.entity_ids)
        ]
        restricted_omitted = len(report.proposals) - len(visible_proposals)
        bounded = visible_proposals[: self._bounded_limit(limit)]
        payloads = [proposal.to_dict() for proposal in bounded]
        evidence_ids = [
            event_id
            for proposal in bounded
            for event_id in proposal.evidence_event_ids
        ]
        return self._envelope(
            access,
            {
                "proposals": payloads,
                "count": len(payloads),
                "candidate_count": len(report.proposals),
                "evaluated_pair_count": report.evaluated_pair_count,
                "truncated": report.truncated or len(visible_proposals) > len(bounded),
                "restricted_proposals_omitted": restricted_omitted,
                "retrieval_mode": "bounded_conservative_cross_robot_identity_candidates",
                "automatic_merge": False,
                "operator_confirmation_required": bool(payloads),
            },
            evidence_ids=evidence_ids,
            requires_revalidation=bool(payloads),
        )

    def locate_entity(
        self,
        access: MemoryAccessContext,
        *,
        entity_id: str | None = None,
        name: str | None = None,
        entity_kind: str | None = None,
        limit: int | None = None,
        reference_at: str | None = None,
    ) -> dict[str, Any]:
        self._validate_access(access)
        if (entity_id is None) == (name is None):
            raise ValueError("provide exactly one of entity_id or name")
        service = self._require_entity_memory()
        if entity_id is not None:
            entity = service.get_entity(mission_id=access.mission_id, entity_id=entity_id)
            entities = [entity] if entity is not None else []
        else:
            entities = service.list_entities(
                mission_id=access.mission_id,
                name=name,
                entity_kind=entity_kind,
            )
        visible, omitted = self._filter_entities(access, entities)
        visible = visible[:self._bounded_limit(limit)]
        reference = _parse_timestamp(reference_at) if reference_at else datetime.now(timezone.utc)
        locations = [
            {
                "entity_id": entity.entity_id,
                "canonical_name": entity.canonical_name,
                "entity_kind": entity.entity_kind,
                "status": entity.status,
                "current_pose": asdict(entity.current_pose) if entity.current_pose else None,
                "last_seen_at": entity.last_seen_at,
                "freshness": self._freshness_at(entity.last_seen_at, "observation", reference),
                "evidence_event_ids": self._entity_evidence_ids(entity),
                "advisory_only": True,
                "requires_current_state_revalidation": True,
            }
            for entity in visible
        ]
        return self._envelope(
            access,
            {
                "locations": locations,
                "count": len(locations),
                "restricted_entities_omitted": omitted,
                "retrieval_mode": "exact_entity_identity_or_name",
            },
            evidence_ids=[event_id for item in locations for event_id in item["evidence_event_ids"]],
            requires_revalidation=bool(locations),
        )

    def get_robot_status(
        self,
        access: MemoryAccessContext,
        *,
        robot_id: str | None = None,
        reference_at: str | None = None,
    ) -> dict[str, Any]:
        self._validate_access(access)
        reference = _parse_timestamp(reference_at) if reference_at else datetime.now(timezone.utc)
        registry_entries: list[dict[str, Any]] = []
        if self._robot_state_provider is not None:
            state = self._robot_state_provider()
            raw_entries = state.get("entries", []) if isinstance(state, dict) else []
            registry_entries = [
                redact_dict(dict(item))
                for item in raw_entries
                if isinstance(item, dict) and (robot_id is None or item.get("robot_id") == robot_id)
            ]
        body_events = self._visible_events(
            access,
            event_types=frozenset({"body_state"}),
            robot_id=robot_id,
        )
        latest_body: dict[str, EmbodiedMemoryEvent] = {}
        for event in body_events:
            if event.robot_id is not None:
                latest_body[event.robot_id] = event
        omitted = self._restricted_omitted_count(
            access,
            event_types=frozenset({"body_state"}),
            robot_id=robot_id,
        )
        statuses = []
        known_robot_ids = sorted({
            *(str(item.get("robot_id")) for item in registry_entries if item.get("robot_id")),
            *latest_body.keys(),
        })
        registry_by_id = {str(item["robot_id"]): item for item in registry_entries if item.get("robot_id")}
        for known_id in known_robot_ids:
            body = latest_body.get(known_id)
            statuses.append({
                "robot_id": known_id,
                "registry_state": registry_by_id.get(known_id),
                "latest_body_state": self._event_payload(body, reference) if body else None,
                "requires_current_state_revalidation": (
                    body is None or self._requires_revalidation(body, reference)
                ),
            })
        return self._envelope(
            access,
            {
                "robots": statuses,
                "count": len(statuses),
                "reference_at": reference.isoformat(),
                "restricted_body_records_omitted": omitted,
            },
            evidence_ids=[event.event_id for event in latest_body.values()],
            requires_revalidation=any(
                item["requires_current_state_revalidation"] for item in statuses
            ),
        )

    def query_reusable_knowledge(
        self,
        access: MemoryAccessContext,
        *,
        knowledge_type: str | None = None,
        tags: list[str] | tuple[str, ...] = (),
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Read only operator-approved knowledge that may cross missions."""
        self._validate_access(access)
        if self._lifecycle is None:
            raise RuntimeError("Reusable knowledge is not configured")
        records = self._lifecycle.list_knowledge(
            knowledge_type=knowledge_type,
            tags=tags,
            limit=self._bounded_limit(limit),
        )
        return self._envelope(
            access,
            {
                "knowledge": [record.to_dict() for record in records],
                "count": len(records),
                "retrieval_mode": "approved_structured_filters_only",
                "cross_mission_environment_memory_allowed": False,
            },
            evidence_ids=[
                event_id
                for record in records
                for event_id in record.source_event_ids
            ],
            requires_revalidation=bool(records),
        )

    def _indexed_spatial_candidates(
        self,
        access: MemoryAccessContext,
        *,
        frame_id: str,
        x: float,
        y: float,
        z: float | None,
        radius_m: float,
        floor: str | None,
        selected_memory_types: frozenset[str],
        selected_event_types: frozenset[str],
        selected_entity_kinds: frozenset[str] | None,
        selected_entity_statuses: frozenset[str] | None,
        entities: list[FireClawEntity],
    ) -> _IndexedSpatialResult | None:
        index = self._store.index
        if index is None or not index.rtree_available:
            return None
        candidates = index.query_spatial_candidates(
            mission_id=access.mission_id,
            runtime_mode=access.runtime_mode,
            frame_id=frame_id,
            x=x,
            y=y,
            z=z,
            radius_m=radius_m,
            floor=floor,
            memory_types=selected_memory_types,
            entity_kinds=selected_entity_kinds,
            entity_statuses=selected_entity_statuses,
            authority_token=self._store.evidence_store.snapshot_token(),
        )
        if candidates is None:
            return None

        event_candidate_types = {
            candidate["source_id"]: candidate["memory_type"]
            for candidate in candidates
            if candidate["memory_type"] in {"observation", "gist"}
        }
        event_ids = list(event_candidate_types)
        events = self._store.load_indexed_events_by_ids(
            event_ids,
            mission_id=access.mission_id,
            runtime_mode=access.runtime_mode,
            event_types=selected_event_types,
        )
        if events is None:
            return None

        ranked: list[
            tuple[_SpatialMatch, str, str, EmbodiedMemoryEvent | FireClawEntity]
        ] = []
        restricted_records_omitted = 0
        for event in events:
            if event_candidate_types.get(event.event_id) != event.event_type:
                return None
            match = _spatial_match(
                event,
                frame_id=frame_id,
                x=x,
                y=y,
                z=z,
                radius_m=radius_m,
                floor=floor,
            )
            if match is None:
                continue
            if event.sensitivity == "restricted" and not access.can_read_restricted:
                restricted_records_omitted += 1
                continue
            ranked.append((match, event.event_type, event.event_id, event))

        entity_candidate_ids = [
            candidate["source_id"]
            for candidate in candidates
            if candidate["memory_type"] == "entity"
        ]
        entities_by_id = {entity.entity_id: entity for entity in entities}
        if any(entity_id not in entities_by_id for entity_id in entity_candidate_ids):
            return None
        positioned_entities: list[tuple[_SpatialMatch, FireClawEntity]] = []
        for entity_id in entity_candidate_ids:
            entity = entities_by_id[entity_id]
            if selected_entity_kinds is not None and entity.entity_kind not in selected_entity_kinds:
                continue
            if selected_entity_statuses is not None and entity.status not in selected_entity_statuses:
                continue
            match = _pose_spatial_match(
                entity.current_pose,
                frame_id=frame_id,
                x=x,
                y=y,
                z=z,
                radius_m=radius_m,
                floor=floor,
                source="entity_current_pose",
            )
            if match is not None:
                positioned_entities.append((match, entity))

        restricted_entities_omitted = 0
        visible_entity_ids = {entity.entity_id for _, entity in positioned_entities}
        if positioned_entities and not access.can_read_restricted:
            evidence_ids = _unique_ids([
                evidence_id
                for _, entity in positioned_entities
                for evidence_id in self._entity_evidence_ids(entity)
            ])
            evidence_events = self._store.load_indexed_events_by_ids(
                evidence_ids,
                mission_id=access.mission_id,
                runtime_mode=access.runtime_mode,
            )
            if evidence_events is None:
                return None
            evidence_by_id = {event.event_id: event for event in evidence_events}
            if set(evidence_by_id) != set(evidence_ids):
                return None
            visible_entity_ids = set()
            for _, entity in positioned_entities:
                if any(
                    evidence_by_id[event_id].sensitivity == "restricted"
                    for event_id in self._entity_evidence_ids(entity)
                ):
                    restricted_entities_omitted += 1
                else:
                    visible_entity_ids.add(entity.entity_id)

        for match, entity in positioned_entities:
            if entity.entity_id in visible_entity_ids:
                ranked.append((match, "entity", entity.entity_id, entity))
        return _IndexedSpatialResult(
            ranked=ranked,
            restricted_records_omitted=restricted_records_omitted,
            restricted_entities_omitted=restricted_entities_omitted,
        )

    def _validate_access(self, access: MemoryAccessContext) -> None:
        if access.runtime_mode != self._runtime_mode:
            raise ValueError("Memory access runtime_mode does not match the facade runtime")
        if self._lifecycle is not None:
            self._lifecycle.assert_operational(access.mission_id)

    def _bounded_limit(self, limit: int | None) -> int:
        value = self.config.default_limit if limit is None else limit
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("limit must be an integer")
        if not 1 <= value <= self.config.max_results:
            raise ValueError(f"limit must be between 1 and {self.config.max_results}")
        return value

    def _validate_spatial_point(
        self,
        *,
        frame_id: str,
        x: float,
        y: float,
        z: float | None,
        radius_m: float,
        radius_name: str = "radius_m",
    ) -> None:
        if not isinstance(frame_id, str) or not frame_id.strip():
            raise ValueError("frame_id must not be empty")
        for name, value in (("x", x), ("y", y), (radius_name, radius_m)):
            _require_finite(name, value)
        if z is not None:
            _require_finite("z", z)
        if not 0 <= radius_m <= self.config.max_spatial_radius_m:
            raise ValueError(
                f"{radius_name} must be between 0 and "
                f"{self.config.max_spatial_radius_m}"
            )

    def _event_types(
        self,
        values: list[str] | tuple[str, ...] | None,
    ) -> frozenset[str] | None:
        if values is None:
            return None
        selected = frozenset(values)
        invalid = selected - EMBODIED_EVENT_TYPES
        if invalid:
            raise ValueError(f"Invalid event_types: {sorted(invalid)}")
        return selected

    def _visible_events(
        self,
        access: MemoryAccessContext,
        *,
        event_types: frozenset[str] | None = None,
        robot_id: str | None = None,
        subtask_id: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[EmbodiedMemoryEvent]:
        output: list[EmbodiedMemoryEvent] = []
        for event in self._store.list_events(mission_id=access.mission_id):
            if event.runtime_mode != access.runtime_mode:
                continue
            if event.sensitivity == "restricted" and not access.can_read_restricted:
                continue
            if event_types is not None and event.event_type not in event_types:
                continue
            if robot_id is not None and event.robot_id != robot_id:
                continue
            if subtask_id is not None and event.subtask_id != subtask_id:
                continue
            observed = _parse_timestamp(event.observed_at)
            if start_at is not None and observed < start_at:
                continue
            if end_at is not None and observed > end_at:
                continue
            output.append(event)
        output.sort(key=lambda event: (event.observed_at, event.created_at, event.event_id))
        return output

    def _restricted_omitted_count(
        self,
        access: MemoryAccessContext,
        **filters: Any,
    ) -> int:
        if access.can_read_restricted:
            return 0
        privileged = MemoryAccessContext(
            mission_id=access.mission_id,
            runtime_mode=access.runtime_mode,
            requester_id=access.requester_id,
            scopes=frozenset({MEMORY_RESTRICTED_READ_SCOPE}),
        )
        all_events = self._visible_events(privileged, **filters)
        return sum(1 for event in all_events if event.sensitivity == "restricted")

    def _restricted_spatial_event_count(
        self,
        access: MemoryAccessContext,
        *,
        event_types: frozenset[str] | None,
        robot_id: str | None,
        frame_id: str,
        x: float,
        y: float,
        z: float | None,
        radius_m: float,
        floor: str | None,
    ) -> int:
        if access.can_read_restricted or (
            event_types is not None and not event_types
        ):
            return 0
        privileged = MemoryAccessContext(
            mission_id=access.mission_id,
            runtime_mode=access.runtime_mode,
            requester_id=access.requester_id,
            scopes=frozenset({MEMORY_RESTRICTED_READ_SCOPE}),
        )
        return sum(
            1
            for event in self._visible_events(
                privileged,
                event_types=event_types,
                robot_id=robot_id,
            )
            if event.sensitivity == "restricted"
            and _spatial_match(
                event,
                frame_id=frame_id,
                x=x,
                y=y,
                z=z,
                radius_m=radius_m,
                floor=floor,
            )
            is not None
        )

    def _restricted_gist_count(
        self,
        access: MemoryAccessContext,
        *,
        episode_id: str | None,
        robot_id: str | None,
        frame_id: str | None,
        floor: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
        x: float | None,
        y: float | None,
        z: float | None,
        radius_m: float | None,
    ) -> int:
        if access.can_read_restricted:
            return 0
        privileged = MemoryAccessContext(
            mission_id=access.mission_id,
            runtime_mode=access.runtime_mode,
            requester_id=access.requester_id,
            scopes=frozenset({MEMORY_RESTRICTED_READ_SCOPE}),
        )
        events = self._visible_events(
            privileged,
            event_types=frozenset({"gist"}),
            robot_id=robot_id,
            start_at=start_at,
            end_at=end_at,
        )
        if episode_id is not None:
            events = [event for event in events if event.episode_id == episode_id]
        if frame_id is not None:
            events = [
                event for event in events
                if _event_matches_frame_floor(event, frame_id=frame_id, floor=None)
            ]
        if floor is not None:
            events = [
                event for event in events
                if _event_matches_frame_floor(event, frame_id=None, floor=floor)
            ]
        if radius_m is not None:
            assert frame_id is not None and x is not None and y is not None
            events = [
                event for event in events
                if _spatial_match(
                    event,
                    frame_id=frame_id,
                    x=x,
                    y=y,
                    z=z,
                    radius_m=radius_m,
                    floor=floor,
                )
                is not None
            ]
        return sum(event.sensitivity == "restricted" for event in events)

    def _event_payload(
        self,
        event: EmbodiedMemoryEvent | None,
        reference: datetime,
    ) -> dict[str, Any] | None:
        if event is None:
            return None
        evidence_ids = _unique_ids([event.event_id, *event.derived_from])
        return {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "robot_id": event.robot_id,
            "subtask_id": event.subtask_id,
            "episode_id": event.episode_id,
            "observed_at": event.observed_at,
            "source_type": event.source_type,
            "payload": redact_dict(dict(event.payload)),
            "pose": event.pose.to_dict() if event.pose else None,
            "confidence": event.confidence,
            "sensitivity": event.sensitivity,
            "provenance": event.provenance.to_dict() if event.provenance else None,
            "evidence_event_ids": evidence_ids,
            "freshness": self._freshness(event, reference),
            "advisory_only": True,
            "requires_current_state_revalidation": self._requires_revalidation(event, reference),
        }

    def _event_query_result(
        self,
        access: MemoryAccessContext,
        events: list[EmbodiedMemoryEvent],
        reference: datetime,
    ) -> dict[str, Any]:
        return self._envelope(
            access,
            {
                "events": [self._event_payload(event, reference) for event in events],
                "count": len(events),
            },
            evidence_ids=[source for event in events for source in (event.event_id, *event.derived_from)],
            requires_revalidation=(
                _is_cross_robot(events)
                or any(self._requires_revalidation(event, reference) for event in events)
            ),
        )

    def _freshness(self, event: EmbodiedMemoryEvent, reference: datetime) -> dict[str, Any]:
        return self._freshness_at(event.observed_at, event.event_type, reference)

    def _freshness_at(
        self,
        observed_at: str,
        event_type: str,
        reference: datetime,
    ) -> dict[str, Any]:
        observed = _parse_timestamp(observed_at)
        age_seconds = (reference - observed).total_seconds()
        max_age = self._freshness_config.max_age_for(event_type)
        if age_seconds < -self._freshness_config.future_clock_skew_seconds:
            status = "clock_skew"
        elif age_seconds > max_age:
            status = "stale"
        else:
            status = "fresh"
        return {
            "status": status,
            "age_seconds": age_seconds,
            "max_age_seconds": max_age,
            "reference_at": reference.isoformat(),
        }

    def _requires_revalidation(self, event: EmbodiedMemoryEvent, reference: datetime) -> bool:
        if event.event_type in {"episode", "gist", "lesson"}:
            return True
        if event.payload.get("requires_current_state_revalidation") is True:
            return True
        return self._freshness(event, reference)["status"] != "fresh"

    def _filter_entities(
        self,
        access: MemoryAccessContext,
        entities: list[FireClawEntity],
    ) -> tuple[list[FireClawEntity], int]:
        if access.can_read_restricted:
            return entities, 0
        events = {
            event.event_id: event
            for event in self._store.list_events(mission_id=access.mission_id)
            if event.runtime_mode == access.runtime_mode
        }
        visible: list[FireClawEntity] = []
        omitted = 0
        for entity in entities:
            evidence = [events.get(event_id) for event_id in self._entity_evidence_ids(entity)]
            if any(event is not None and event.sensitivity == "restricted" for event in evidence):
                omitted += 1
            else:
                visible.append(entity)
        return visible, omitted

    def _entity_payload(self, entity: FireClawEntity, reference: datetime) -> dict[str, Any]:
        payload = asdict(entity)
        payload.update({
            "evidence_event_ids": self._entity_evidence_ids(entity),
            "freshness": self._freshness_at(entity.last_seen_at, "observation", reference),
            "advisory_only": True,
            "requires_current_state_revalidation": True,
        })
        return payload

    @staticmethod
    def _entity_evidence_ids(entity: FireClawEntity) -> list[str]:
        return _unique_ids([*entity.mention_event_ids, *entity.observation_event_ids])

    def _require_entity_memory(self) -> EntityMemoryService:
        if self._entity_memory is None:
            raise RuntimeError("Entity Memory is not configured")
        return self._entity_memory

    def _envelope(
        self,
        access: MemoryAccessContext,
        payload: dict[str, Any],
        *,
        evidence_ids: list[str],
        requires_revalidation: bool,
    ) -> dict[str, Any]:
        lifecycle = self._lifecycle.state(access.mission_id).to_dict() if self._lifecycle else None
        return {
            "mission_id": access.mission_id,
            "runtime_mode": access.runtime_mode,
            "requester_id": access.requester_id,
            **payload,
            "evidence_event_ids": _unique_ids(evidence_ids),
            "advisory_only": True,
            "requires_current_state_revalidation": requires_revalidation,
            "memory_lifecycle": lifecycle,
            "safety": {
                "can_authorize_action": False,
                "must_pass_current_sensors_and_safety_gate": True,
                "cross_robot_or_derived_claims_are_advisory": True,
            },
        }


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("timestamp must be a valid ISO-8601 string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _time_range(start_at: str, end_at: str) -> tuple[datetime, datetime]:
    start = _parse_timestamp(start_at)
    end = _parse_timestamp(end_at)
    if start > end:
        raise ValueError("start_at must be <= end_at")
    return start, end


def _optional_time_range(
    start_at: str | None,
    end_at: str | None,
) -> tuple[datetime | None, datetime | None]:
    start = _parse_timestamp(start_at) if start_at is not None else None
    end = _parse_timestamp(end_at) if end_at is not None else None
    if start is not None and end is not None and start > end:
        raise ValueError("start_at must be <= end_at")
    return start, end


def _require_finite(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")


def _unique_ids(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def _is_cross_robot(events: list[EmbodiedMemoryEvent]) -> bool:
    return len({event.robot_id for event in events if event.robot_id is not None}) > 1


def _event_matches_frame_floor(
    event: EmbodiedMemoryEvent,
    *,
    frame_id: str | None,
    floor: str | None,
) -> bool:
    if event.pose is not None:
        if (frame_id is None or event.pose.frame_id == frame_id) and (
            floor is None or event.pose.floor == floor
        ):
            return True
    geometries = event.payload.get("spatial_geometries")
    if not isinstance(geometries, list):
        return False
    return any(
        isinstance(geometry, dict)
        and (frame_id is None or geometry.get("frame_id") == frame_id)
        and (floor is None or geometry.get("floor") == floor)
        for geometry in geometries
    )


def _spatial_match(
    event: EmbodiedMemoryEvent,
    *,
    frame_id: str,
    x: float,
    y: float,
    z: float | None,
    radius_m: float,
    floor: str | None,
) -> _SpatialMatch | None:
    matches: list[_SpatialMatch] = []
    geometries = event.payload.get("spatial_geometries")
    if isinstance(geometries, list):
        for geometry in geometries:
            if not isinstance(geometry, dict):
                continue
            match = _geometry_spatial_match(
                geometry,
                frame_id=frame_id,
                x=x,
                y=y,
                z=z,
                radius_m=radius_m,
                floor=floor,
            )
            if match is not None:
                matches.append(match)
    pose_match = _pose_spatial_match(
        event.pose,
        frame_id=frame_id,
        x=x,
        y=y,
        z=z,
        radius_m=radius_m,
        floor=floor,
        source="event_pose",
    )
    if pose_match is not None:
        matches.append(pose_match)
    return min(matches, key=lambda match: match.sort_key()) if matches else None


def _pose_spatial_match(
    pose: SpatialMemoryContext | None,
    *,
    frame_id: str,
    x: float,
    y: float,
    z: float | None,
    radius_m: float,
    floor: str | None,
    source: str,
) -> _SpatialMatch | None:
    if pose is None or pose.frame_id != frame_id:
        return None
    if floor is not None and pose.floor != floor:
        return None
    if z is None:
        center_distance = math.hypot(pose.x - x, pose.y - y)
        dimensions = 2
    elif pose.z is not None:
        center_distance = math.sqrt(
            (pose.x - x) ** 2 + (pose.y - y) ** 2 + (pose.z - z) ** 2
        )
        dimensions = 3
    else:
        return None
    distance_to_uncertainty = max(
        0.0,
        center_distance - pose.uncertainty_radius_m,
    )
    if distance_to_uncertainty > radius_m:
        return None
    return _SpatialMatch(
        center_distance_m=center_distance,
        distance_to_uncertainty_m=distance_to_uncertainty,
        uncertainty_radius_m=pose.uncertainty_radius_m,
        source=source,
        dimensions=dimensions,
    )


def _geometry_spatial_match(
    geometry: dict[str, Any],
    *,
    frame_id: str,
    x: float,
    y: float,
    z: float | None,
    radius_m: float,
    floor: str | None,
) -> _SpatialMatch | None:
    if geometry.get("frame_id") != frame_id:
        return None
    if floor is not None and geometry.get("floor") != floor:
        return None
    center_x = _geometry_float(geometry.get("center_x"))
    center_y = _geometry_float(geometry.get("center_y"))
    uncertainty = _geometry_float(geometry.get("radius_m"))
    if center_x is None or center_y is None or uncertainty is None or uncertainty < 0:
        return None
    horizontal_distance = math.hypot(center_x - x, center_y - y)
    horizontal_envelope_distance = max(0.0, horizontal_distance - uncertainty)
    if z is None:
        center_distance = horizontal_distance
        distance_to_uncertainty = horizontal_envelope_distance
        dimensions = 2
    else:
        bounds = geometry.get("bounds")
        min_z = None
        max_z = None
        if isinstance(bounds, dict):
            min_z = _geometry_float(bounds.get("min_z"))
            max_z = _geometry_float(bounds.get("max_z"))
        center_z = _geometry_float(geometry.get("center_z"))
        if min_z is not None and max_z is not None and min_z <= max_z:
            if center_z is None:
                center_z = (min_z + max_z) / 2.0
            vertical_envelope_distance = max(min_z - z, 0.0, z - max_z)
        elif center_z is not None:
            vertical_envelope_distance = abs(center_z - z)
        else:
            return None
        center_distance = math.hypot(horizontal_distance, center_z - z)
        distance_to_uncertainty = math.hypot(
            horizontal_envelope_distance,
            vertical_envelope_distance,
        )
        dimensions = 3
    if distance_to_uncertainty > radius_m:
        return None
    return _SpatialMatch(
        center_distance_m=center_distance,
        distance_to_uncertainty_m=distance_to_uncertainty,
        uncertainty_radius_m=uncertainty,
        source="conservative_geometry",
        dimensions=dimensions,
        geometry=redact_dict(dict(geometry)),
    )


def _geometry_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _add_spatial_match(payload: dict[str, Any], match: _SpatialMatch) -> None:
    payload.update({
        # Retain the original center-distance field while making envelope
        # distance explicit for conservative nearest-neighbor ranking.
        "distance_m": match.center_distance_m,
        "center_distance_m": match.center_distance_m,
        "distance_to_uncertainty_m": match.distance_to_uncertainty_m,
        "spatial_match": match.to_dict(),
    })
    if match.geometry is not None:
        payload["matched_spatial_geometry"] = match.geometry


def _validated_values(
    field_name: str,
    values: list[str] | tuple[str, ...] | None,
    allowed: frozenset[str],
) -> frozenset[str] | None:
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f"{field_name} must be a non-empty array")
    if any(not isinstance(value, str) for value in values):
        raise ValueError(f"{field_name} values must be strings")
    selected = frozenset(values)
    invalid = selected - allowed
    if invalid:
        raise ValueError(f"Invalid {field_name}: {sorted(invalid)}")
    return selected
