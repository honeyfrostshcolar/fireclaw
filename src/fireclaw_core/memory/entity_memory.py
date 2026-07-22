"""Safety-oriented persistent entity projection over embodied observations."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import math
import re
import threading
from typing import Any
from uuid import uuid4

from fireclaw_core.memory.embodied_memory import (
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryEvent,
    EmbodiedMemoryProducer,
    EmbodiedMemoryStore,
    SpatialMemoryContext,
)


ENTITY_KINDS: frozenset[str] = frozenset({
    "equipment",
    "exit",
    "fire_source",
    "hazardous_material",
    "obstacle",
    "responder",
    "robot",
    "room_or_zone",
    "smoke_source",
    "unknown",
    "victim",
})

ENTITY_STATUSES: frozenset[str] = frozenset({
    "candidate",
    "confirmed",
    "contradicted",
    "corroborated",
    "resolved",
    "stale",
})

ENTITY_RESOLUTION_ACTIONS: frozenset[str] = frozenset({
    "confirm",
    "contradict",
    "merge",
    "reject_merge",
    "resolve",
    "split",
})


@dataclass(frozen=True)
class ExtractedEntityMention:
    name: str
    entity_kind: str
    observation_event_id: str
    confidence: float
    pose: SpatialMemoryContext | None = None
    source_track_namespace: str | None = None
    source_track_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    extraction_key: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("entity mention name must not be empty")
        if self.entity_kind not in ENTITY_KINDS:
            raise ValueError(
                f"Invalid entity_kind: {self.entity_kind}. "
                f"Must be one of: {sorted(ENTITY_KINDS)}"
            )
        if not self.observation_event_id.strip():
            raise ValueError("observation_event_id must not be empty")
        if isinstance(self.confidence, bool) or not math.isfinite(self.confidence):
            raise ValueError("entity mention confidence must be finite")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("entity mention confidence must be between 0 and 1")
        tracking_values = (self.source_track_namespace, self.source_track_id)
        if any(tracking_values) and not all(tracking_values):
            raise ValueError(
                "source_track_namespace and source_track_id must be provided together"
            )
        if not isinstance(self.attributes, dict):
            raise ValueError("entity mention attributes must be a dictionary")
        if self.extraction_key is not None and not self.extraction_key.strip():
            raise ValueError("extraction_key must not be empty when provided")

    @property
    def tracking_key(self) -> str | None:
        if self.source_track_namespace is None or self.source_track_id is None:
            return None
        return f"{self.source_track_namespace}:{self.source_track_id}"


@dataclass(frozen=True)
class EntityLocationAssertion:
    mention_event_id: str
    observation_event_id: str
    observed_at: str
    pose: SpatialMemoryContext
    confidence: float
    robot_id: str | None = None
    spatial_frame_scope: str | None = None


@dataclass(frozen=True)
class FireClawEntity:
    entity_id: str
    mission_id: str
    runtime_mode: str
    entity_kind: str
    canonical_name: str
    aliases: tuple[str, ...]
    status: str
    first_seen_at: str
    last_seen_at: str
    observation_count: int
    mention_event_ids: tuple[str, ...]
    observation_event_ids: tuple[str, ...]
    tracking_keys: tuple[str, ...]
    evidence_source_ids: tuple[str, ...]
    confidence_min: float
    confidence_max: float
    latest_confidence: float
    current_pose: SpatialMemoryContext | None
    location_history: tuple[EntityLocationAssertion, ...]
    attributes: tuple[dict[str, Any], ...]
    merge_proposal_entity_ids: tuple[str, ...] = ()
    tracking_identities: tuple[str, ...] = ()
    source_robot_ids: tuple[str, ...] = ()
    spatial_frame_scopes: tuple[str, ...] = ()
    rejected_merge_entity_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityIdentityProposal:
    proposal_id: str
    mission_id: str
    runtime_mode: str
    entity_ids: tuple[str, str]
    entity_kind: str
    status: str
    source_robot_ids: tuple[str, ...]
    shared_normalized_aliases: tuple[str, ...]
    frame_id: str
    floor: str | None
    center_distance_m: float
    uncertainty_overlap_threshold_m: float
    time_gap_seconds: float
    evidence_event_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    shared_tracking_namespaces: tuple[str, ...] = ()
    automatic_merge: bool = False
    requires_operator_confirmation: bool = True
    advisory_only: bool = True
    requires_current_state_revalidation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EntityIdentityProposalReport:
    proposals: tuple[EntityIdentityProposal, ...]
    entity_count: int
    evaluated_pair_count: int
    truncated: bool


@dataclass(frozen=True)
class EntityIngestResult:
    entity: FireClawEntity
    mention_event_id: str
    resolution: str
    merge_proposal_entity_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityResolverConfig:
    proposal_spatial_margin_m: float = 1.0
    proposal_max_time_gap_seconds: float = 300.0
    cross_robot_spatial_margin_m: float = 1.0
    cross_robot_max_time_gap_seconds: float = 120.0
    max_identity_pair_evaluations: int = 50_000
    max_identity_proposals: int = 500

    def __post_init__(self) -> None:
        for name, value in (
            ("proposal_spatial_margin_m", self.proposal_spatial_margin_m),
            ("proposal_max_time_gap_seconds", self.proposal_max_time_gap_seconds),
            ("cross_robot_spatial_margin_m", self.cross_robot_spatial_margin_m),
            ("cross_robot_max_time_gap_seconds", self.cross_robot_max_time_gap_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{name} must be a finite number")
        for name, value in (
            ("max_identity_pair_evaluations", self.max_identity_pair_evaluations),
            ("max_identity_proposals", self.max_identity_proposals),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        if self.proposal_spatial_margin_m < 0:
            raise ValueError("proposal_spatial_margin_m cannot be negative")
        if self.proposal_max_time_gap_seconds <= 0:
            raise ValueError("proposal_max_time_gap_seconds must be positive")
        if self.cross_robot_spatial_margin_m < 0:
            raise ValueError("cross_robot_spatial_margin_m cannot be negative")
        if self.cross_robot_max_time_gap_seconds <= 0:
            raise ValueError("cross_robot_max_time_gap_seconds must be positive")
        if self.max_identity_pair_evaluations <= 0:
            raise ValueError("max_identity_pair_evaluations must be positive")
        if self.max_identity_proposals <= 0:
            raise ValueError("max_identity_proposals must be positive")


class EntityMemoryService:
    """Records mentions and projects conservative, auditable entity state."""

    def __init__(
        self,
        *,
        store: EmbodiedMemoryStore,
        resolver_producer: EmbodiedMemoryProducer,
        runtime_mode: str,
        operator_producer: EmbodiedMemoryProducer | None = None,
        config: EntityResolverConfig | None = None,
    ) -> None:
        if resolver_producer.producer_type != "entity_resolver":
            raise ValueError("resolver_producer must use producer_type entity_resolver")
        if operator_producer is not None and operator_producer.producer_type != "approval_runtime":
            raise ValueError("operator_producer must use producer_type approval_runtime")
        if runtime_mode not in MEMORY_RUNTIME_MODES:
            raise ValueError(
                f"Invalid runtime_mode: {runtime_mode}. "
                f"Must be one of: {sorted(MEMORY_RUNTIME_MODES)}"
            )
        self._store = store
        self._resolver_producer = resolver_producer
        self._operator_producer = operator_producer
        self._runtime_mode = runtime_mode
        self.config = config or EntityResolverConfig()
        self._projection_lock = threading.RLock()
        self._projection_cache: dict[
            str,
            tuple[str, tuple[FireClawEntity, ...]],
        ] = {}

    def record_mention(
        self,
        *,
        mission_id: str,
        mention: ExtractedEntityMention,
        extractor_method_id: str,
        robot_id: str | None = None,
        subtask_id: str | None = None,
    ) -> EntityIngestResult:
        observation = self._require_observation(
            mission_id=mission_id,
            event_id=mention.observation_event_id,
        )
        if (
            robot_id is not None
            and observation.robot_id is not None
            and robot_id != observation.robot_id
        ):
            raise ValueError("entity mention robot_id must match its observation robot_id")
        tracking_robot_id = robot_id or observation.robot_id
        tracking_identity = _tracking_identity(
            tracking_robot_id,
            mention.tracking_key,
        )
        entities = self.list_entities(mission_id=mission_id)
        exact_track_matches = [
            entity
            for entity in entities
            if tracking_identity is not None
            and tracking_identity in entity.tracking_identities
            and entity.entity_kind == mention.entity_kind
        ]
        if len(exact_track_matches) > 1:
            raise ValueError("tracking key resolves to multiple entities; operator resolution required")
        proposal_matches = self._proposal_matches(
            mention,
            entities,
            observation.observed_at,
            source_robot_id=tracking_robot_id,
            source_spatial_frame_scope=(
                observation.payload.get("spatial_frame_scope")
                if isinstance(observation.payload.get("spatial_frame_scope"), str)
                else None
            ),
            source_tracking_namespace=_tracking_namespace(mention.tracking_key),
        )
        if exact_track_matches:
            entity_id = exact_track_matches[0].entity_id
            resolution = "tracked"
            proposal_ids: tuple[str, ...] = ()
        else:
            entity_id = f"entity-{uuid4().hex}"
            proposal_ids = tuple(entity.entity_id for entity in proposal_matches)
            resolution = "merge_proposed" if proposal_ids else "created"

        mention_event = self._resolver_producer.record_event(
            mission_id=mission_id,
            event_type="entity_mention",
            evidence_kind="cognitive_artifact",
            payload={
                "entity_id": entity_id,
                "name": mention.name.strip(),
                "entity_kind": mention.entity_kind,
                "observation_event_id": mention.observation_event_id,
                "tracking_key": mention.tracking_key,
                "tracking_robot_id": tracking_robot_id,
                "tracking_identity": tracking_identity,
                "attributes": dict(mention.attributes),
                "extraction_key": mention.extraction_key,
                "resolution": resolution,
                "merge_proposal_entity_ids": list(proposal_ids),
            },
            runtime_mode=self._runtime_mode,
            source_type="entity_extractor",
            method_id=extractor_method_id,
            robot_id=robot_id or observation.robot_id,
            subtask_id=subtask_id or observation.subtask_id,
            observed_at=observation.observed_at,
            pose=mention.pose,
            confidence=mention.confidence,
            sensitivity=observation.sensitivity,
            derived_from=(observation.event_id,),
            event_id=(
                _mention_event_id(
                    mission_id=mission_id,
                    extractor_method_id=extractor_method_id,
                    extraction_key=mention.extraction_key,
                )
                if mention.extraction_key is not None
                else None
            ),
        )
        self._resolver_producer.add_relation(
            mission_id=mission_id,
            source_record_id=mention_event.event_id,
            target_record_id=observation.event_id,
            relation_type="observed_in",
            runtime_mode=self._runtime_mode,
        )
        self._link_co_observed_mentions(mention_event, observation.event_id)
        if proposal_ids:
            resolution_event = self._resolver_producer.record_event(
                mission_id=mission_id,
                event_type="entity_resolution",
                evidence_kind="runtime_evidence",
                payload={
                    "action": "merge_proposed",
                    "entity_id": entity_id,
                    "candidate_entity_ids": list(proposal_ids),
                    "automatic_merge": False,
                    "reason": "same kind/name with compatible time and spatial uncertainty",
                },
                runtime_mode=self._runtime_mode,
                source_type="entity_resolver",
                robot_id=mention_event.robot_id,
                subtask_id=mention_event.subtask_id,
                observed_at=mention_event.observed_at,
                derived_from=(mention_event.event_id,),
            )
            self._resolver_producer.add_relation(
                mission_id=mission_id,
                source_record_id=resolution_event.event_id,
                target_record_id=mention_event.event_id,
                relation_type="caused_by",
                runtime_mode=self._runtime_mode,
            )
        entity = self.get_entity(mission_id=mission_id, entity_id=entity_id)
        if entity is None:
            raise RuntimeError("entity projection did not include the persisted mention")
        return EntityIngestResult(
            entity=entity,
            mention_event_id=mention_event.event_id,
            resolution=resolution,
            merge_proposal_entity_ids=proposal_ids,
        )

    def apply_operator_resolution(
        self,
        *,
        mission_id: str,
        action: str,
        entity_ids: tuple[str, ...],
        operator_id: str,
        target_entity_id: str | None = None,
        mention_event_ids: tuple[str, ...] = (),
        reason: str | None = None,
    ) -> str:
        if self._operator_producer is None:
            raise RuntimeError("operator entity resolution is not configured")
        if action not in ENTITY_RESOLUTION_ACTIONS:
            raise ValueError(
                f"Invalid entity resolution action: {action}. "
                f"Must be one of: {sorted(ENTITY_RESOLUTION_ACTIONS)}"
            )
        if not entity_ids:
            raise ValueError("entity resolution requires at least one entity_id")
        existing = {entity.entity_id for entity in self.list_entities(mission_id=mission_id)}
        missing = set(entity_ids) - existing
        if target_entity_id is not None and target_entity_id not in existing:
            missing.add(target_entity_id)
        if missing:
            raise ValueError(f"Unknown entity ids: {sorted(missing)}")
        if action == "merge" and (target_entity_id is None or len(set(entity_ids)) < 2):
            raise ValueError("merge requires at least two entities and target_entity_id")
        entities_by_id = {
            entity.entity_id: entity for entity in self.list_entities(mission_id=mission_id)
        }
        if action == "merge":
            if target_entity_id not in entity_ids:
                raise ValueError("merge target_entity_id must be included in entity_ids")
            kinds = {entities_by_id[entity_id].entity_kind for entity_id in entity_ids}
            if len(kinds) != 1:
                raise ValueError("entities of different kinds cannot be merged")
        if action == "split" and not mention_event_ids:
            raise ValueError("split requires mention_event_ids")
        source_mentions = self._mention_ids_for_entities(mission_id, set(entity_ids))
        if action == "split" and not set(mention_event_ids).issubset(source_mentions):
            raise ValueError("split mention_event_ids must belong to the selected entities")
        event = self._operator_producer.record_event(
            mission_id=mission_id,
            event_type="entity_resolution",
            evidence_kind="operator_assertion",
            payload={
                "action": action,
                "entity_ids": list(entity_ids),
                "target_entity_id": target_entity_id,
                "mention_event_ids": list(mention_event_ids),
                "reason": reason,
                "automatic_merge": False,
            },
            runtime_mode=self._runtime_mode,
            source_type="operator",
            source_id=operator_id,
            derived_from=source_mentions,
        )
        for mention_id in source_mentions:
            self._operator_producer.add_relation(
                mission_id=mission_id,
                source_record_id=event.event_id,
                target_record_id=mention_id,
                relation_type="corrects",
                runtime_mode=self._runtime_mode,
            )
        return event.event_id

    def list_entities(
        self,
        *,
        mission_id: str,
        entity_kind: str | None = None,
        status: str | None = None,
        name: str | None = None,
        frame_id: str | None = None,
        near_x: float | None = None,
        near_y: float | None = None,
        radius_m: float | None = None,
        last_seen_after: str | None = None,
    ) -> list[FireClawEntity]:
        if entity_kind is not None and entity_kind not in ENTITY_KINDS:
            raise ValueError(f"Invalid entity_kind: {entity_kind}")
        if status is not None and status not in ENTITY_STATUSES:
            raise ValueError(f"Invalid entity status: {status}")
        spatial_values = (frame_id, near_x, near_y, radius_m)
        if any(value is not None for value in spatial_values) and not all(
            value is not None for value in spatial_values
        ):
            raise ValueError("spatial entity query requires frame_id, near_x, near_y, and radius_m")
        entities = self._project_entities(mission_id)
        output: list[FireClawEntity] = []
        normalized_name = _normalize_name(name) if name is not None else None
        last_seen = datetime.fromisoformat(last_seen_after) if last_seen_after else None
        if last_seen is not None and (last_seen.tzinfo is None or last_seen.utcoffset() is None):
            raise ValueError("last_seen_after must include a timezone")
        for entity in entities:
            if entity_kind is not None and entity.entity_kind != entity_kind:
                continue
            if status is not None and entity.status != status:
                continue
            if normalized_name is not None and normalized_name not in {
                _normalize_name(alias) for alias in entity.aliases
            }:
                continue
            if last_seen is not None and datetime.fromisoformat(entity.last_seen_at) < last_seen:
                continue
            if frame_id is not None:
                pose = entity.current_pose
                assert near_x is not None and near_y is not None and radius_m is not None
                if pose is None or pose.frame_id != frame_id:
                    continue
                distance = math.hypot(pose.x - near_x, pose.y - near_y)
                if distance > radius_m + pose.uncertainty_radius_m:
                    continue
            output.append(entity)
        output.sort(key=lambda entity: (entity.last_seen_at, entity.entity_id), reverse=True)
        return output

    def get_entity(self, *, mission_id: str, entity_id: str) -> FireClawEntity | None:
        return next(
            (entity for entity in self._project_entities(mission_id) if entity.entity_id == entity_id),
            None,
        )

    def get_entity_observations(
        self,
        *,
        mission_id: str,
        entity_id: str,
    ) -> list[EmbodiedMemoryEvent]:
        entity = self.get_entity(mission_id=mission_id, entity_id=entity_id)
        if entity is None:
            return []
        observation_ids = set(entity.observation_event_ids)
        return sorted(
            [
                event
                for event in self._store.list_events(mission_id=mission_id, event_type="observation")
                if event.runtime_mode == self._runtime_mode and event.event_id in observation_ids
            ],
            key=lambda event: (event.observed_at, event.event_id),
        )

    def get_co_observed_entities(
        self,
        *,
        mission_id: str,
        entity_id: str,
    ) -> list[FireClawEntity]:
        entities = self.list_entities(mission_id=mission_id)
        entity = next((item for item in entities if item.entity_id == entity_id), None)
        if entity is None:
            return []
        mention_owner = {
            mention_id: item.entity_id
            for item in entities
            for mention_id in item.mention_event_ids
        }
        mention_ids = set(entity.mention_event_ids)
        related_entity_ids: set[str] = set()
        for relation in self._store.list_relations(mission_id=mission_id):
            if relation.runtime_mode != self._runtime_mode:
                continue
            if relation.relation_type != "co_observed_with":
                continue
            if relation.source_record_id in mention_ids:
                owner = mention_owner.get(relation.target_record_id)
                if owner is not None and owner != entity_id:
                    related_entity_ids.add(owner)
            if relation.target_record_id in mention_ids:
                owner = mention_owner.get(relation.source_record_id)
                if owner is not None and owner != entity_id:
                    related_entity_ids.add(owner)
        return [item for item in entities if item.entity_id in related_entity_ids]

    def list_identity_proposals(
        self,
        *,
        mission_id: str,
        entity_kind: str | None = None,
        entity_id: str | None = None,
    ) -> EntityIdentityProposalReport:
        """Return bounded, advisory cross-robot identity candidates.

        The comparison is deliberately narrower than local tracker matching:
        both latest assertions must use the shared mission frame, have a known
        robot owner, agree on kind and normalized name, and be compatible in
        time and uncertainty-aware position.  No candidate changes ownership.
        """
        if entity_kind is not None and entity_kind not in ENTITY_KINDS:
            raise ValueError(f"Invalid entity_kind: {entity_kind}")
        if entity_id is not None and not entity_id.strip():
            raise ValueError("entity_id must not be empty")

        entities = self.list_entities(
            mission_id=mission_id,
            entity_kind=entity_kind,
        )
        if entity_id is not None and not any(entity.entity_id == entity_id for entity in entities):
            return EntityIdentityProposalReport(
                proposals=(),
                entity_count=0,
                evaluated_pair_count=0,
                truncated=False,
            )

        # Bucket by exact map coordinates' frame and floor before pairwise
        # evaluation.  This keeps the deterministic baseline bounded and never
        # compares robot-local frames that happen to share a label.
        buckets: dict[tuple[str, str | None, str], list[FireClawEntity]] = {}
        for entity in entities:
            if entity.status == "contradicted":
                continue
            location = _latest_mission_location(entity)
            if location is None or location.robot_id is None:
                continue
            pose = location.pose
            robot_ids = set(entity.source_robot_ids) or {location.robot_id}
            if not robot_ids:
                continue
            key = (pose.frame_id, pose.floor, entity.entity_kind)
            buckets.setdefault(key, []).append(entity)

        candidates: list[EntityIdentityProposal] = []
        evaluated_pair_count = 0
        truncated = False
        for bucket_key in sorted(
            buckets,
            key=lambda value: (value[0], value[1] or "", value[2]),
        ):
            bucket = sorted(buckets[bucket_key], key=lambda entity: entity.entity_id)
            for left_index, left in enumerate(bucket):
                left_location = _latest_mission_location(left)
                if left_location is None or left_location.robot_id is None:
                    continue
                left_robots = set(left.source_robot_ids) or {left_location.robot_id}
                for right in bucket[left_index + 1:]:
                    if (
                        entity_id is not None
                        and left.entity_id != entity_id
                        and right.entity_id != entity_id
                    ):
                        continue
                    if evaluated_pair_count >= self.config.max_identity_pair_evaluations:
                        truncated = True
                        break
                    evaluated_pair_count += 1
                    right_location = _latest_mission_location(right)
                    if right_location is None or right_location.robot_id is None:
                        continue
                    right_robots = set(right.source_robot_ids) or {right_location.robot_id}
                    if left_robots & right_robots:
                        continue
                    if right.entity_id in left.rejected_merge_entity_ids:
                        continue
                    if left.entity_id in right.rejected_merge_entity_ids:
                        continue
                    shared_aliases = tuple(sorted(
                        set(_normalize_name(alias) for alias in left.aliases)
                        & set(_normalize_name(alias) for alias in right.aliases)
                    ))
                    if not shared_aliases:
                        continue
                    left_namespaces = _tracking_namespaces(left.tracking_keys)
                    right_namespaces = _tracking_namespaces(right.tracking_keys)
                    shared_namespaces = tuple(sorted(left_namespaces & right_namespaces))
                    if left_namespaces and right_namespaces and not shared_namespaces:
                        continue
                    left_pose = left_location.pose
                    right_pose = right_location.pose
                    if (left_pose.z is None) != (right_pose.z is None):
                        continue
                    time_gap = abs(
                        (
                            datetime.fromisoformat(left_location.observed_at)
                            - datetime.fromisoformat(right_location.observed_at)
                        ).total_seconds()
                    )
                    if time_gap > self.config.cross_robot_max_time_gap_seconds:
                        continue
                    center_distance = _pose_distance(left_pose, right_pose)
                    overlap_threshold = (
                        left_pose.uncertainty_radius_m
                        + right_pose.uncertainty_radius_m
                        + self.config.cross_robot_spatial_margin_m
                    )
                    if center_distance > overlap_threshold:
                        continue
                    entity_ids = tuple(sorted((left.entity_id, right.entity_id)))
                    evidence_event_ids = tuple(sorted(set(
                        (*left.mention_event_ids,
                         *left.observation_event_ids,
                         *right.mention_event_ids,
                         *right.observation_event_ids)
                    )))
                    source_robot_ids = tuple(sorted(left_robots | right_robots))
                    proposal_id = _identity_proposal_id(
                        mission_id=mission_id,
                        runtime_mode=self._runtime_mode,
                        entity_ids=entity_ids,
                        frame_id=left_pose.frame_id,
                        floor=left_pose.floor,
                    )
                    candidates.append(EntityIdentityProposal(
                        proposal_id=proposal_id,
                        mission_id=mission_id,
                        runtime_mode=self._runtime_mode,
                        entity_ids=entity_ids,
                        entity_kind=left.entity_kind,
                        status="candidate",
                        source_robot_ids=source_robot_ids,
                        shared_normalized_aliases=shared_aliases,
                        frame_id=left_pose.frame_id,
                        floor=left_pose.floor,
                        center_distance_m=center_distance,
                        uncertainty_overlap_threshold_m=overlap_threshold,
                        time_gap_seconds=time_gap,
                        evidence_event_ids=evidence_event_ids,
                        reason_codes=(
                            "different_robot_evidence",
                            "exact_normalized_alias",
                            "mission_shared_frame",
                            "spatial_uncertainty_overlap",
                            "temporal_compatibility",
                            *(
                                ("shared_tracker_namespace",)
                                if shared_namespaces
                                else ("tracker_namespace_missing_on_one_side",)
                            ),
                        ),
                        shared_tracking_namespaces=shared_namespaces,
                    ))
                if truncated:
                    break
            if truncated:
                break

        degree: dict[str, int] = {}
        for proposal in candidates:
            for value in proposal.entity_ids:
                degree[value] = degree.get(value, 0) + 1
        candidates = [
            EntityIdentityProposal(
                **{
                    **proposal.to_dict(),
                    "entity_ids": tuple(proposal.entity_ids),
                    "source_robot_ids": tuple(proposal.source_robot_ids),
                    "shared_normalized_aliases": tuple(proposal.shared_normalized_aliases),
                    "evidence_event_ids": tuple(proposal.evidence_event_ids),
                    "reason_codes": tuple(
                        (*proposal.reason_codes,
                         "ambiguous_entity_candidate_set")
                        if any(degree.get(value, 0) > 1 for value in proposal.entity_ids)
                        else proposal.reason_codes
                    ),
                    "status": (
                        "ambiguous"
                        if any(degree.get(value, 0) > 1 for value in proposal.entity_ids)
                        else "candidate"
                    ),
                }
            )
            for proposal in candidates
        ]
        candidates.sort(key=lambda proposal: proposal.proposal_id)
        if len(candidates) > self.config.max_identity_proposals:
            candidates = candidates[:self.config.max_identity_proposals]
            truncated = True
        return EntityIdentityProposalReport(
            proposals=tuple(candidates),
            entity_count=len(entities),
            evaluated_pair_count=evaluated_pair_count,
            truncated=truncated,
        )

    def _project_entities(self, mission_id: str) -> list[FireClawEntity]:
        source_token = self._entity_source_token(mission_id)
        with self._projection_lock:
            cached = self._projection_cache.get(mission_id)
            if cached is not None and cached[0] == source_token:
                return list(cached[1])

            index = self._store.index
            if index is not None:
                persisted = index.load_entity_projection(
                    mission_id=mission_id,
                    runtime_mode=self._runtime_mode,
                    source_token=source_token,
                )
                if persisted is not None:
                    try:
                        projected = tuple(_entity_from_projection_payload(value) for value in persisted)
                    except (TypeError, ValueError, KeyError):
                        projected = ()
                    else:
                        self._projection_cache[mission_id] = (source_token, projected)
                        return list(projected)

            projected = tuple(self._rebuild_entities(mission_id))
            if index is not None:
                index.replace_entity_projection(
                    mission_id=mission_id,
                    runtime_mode=self._runtime_mode,
                    source_token=source_token,
                    entities=(_entity_to_projection_payload(entity) for entity in projected),
                )
            self._projection_cache[mission_id] = (source_token, projected)
            return list(projected)

    def _entity_source_token(self, mission_id: str) -> str:
        index = self._store.index
        if index is not None:
            return index.entity_source_token(
                mission_id=mission_id,
                runtime_mode=self._runtime_mode,
            )
        return f"authority-v1:{self._store.evidence_store.snapshot_token()}"

    def _rebuild_entities(self, mission_id: str) -> list[FireClawEntity]:
        events = sorted(
            [
                event
                for event in self._store.list_events(mission_id=mission_id)
                if event.runtime_mode == self._runtime_mode
                and event.event_type in {"entity_mention", "entity_resolution"}
            ],
            key=lambda event: (event.observed_at, event.created_at, event.event_id),
        )
        mention_events = [event for event in events if event.event_type == "entity_mention"]
        parent: dict[str, str] = {}
        mention_assignment: dict[str, str] = {}
        status_assertions: list[tuple[str, str]] = []
        rejected_merge_pairs: set[frozenset[str]] = set()

        def find(entity_id: str) -> str:
            parent.setdefault(entity_id, entity_id)
            if parent[entity_id] != entity_id:
                parent[entity_id] = find(parent[entity_id])
            return parent[entity_id]

        for event in mention_events:
            entity_id = str(event.payload.get("entity_id") or "")
            if entity_id:
                parent.setdefault(entity_id, entity_id)
                mention_assignment[event.event_id] = entity_id
        for event in events:
            if event.event_type != "entity_resolution":
                continue
            action = event.payload.get("action")
            entity_ids = [str(value) for value in event.payload.get("entity_ids") or []]
            target = event.payload.get("target_entity_id")
            if action == "merge" and isinstance(target, str) and target:
                root = find(target)
                for entity_id in entity_ids:
                    parent[find(entity_id)] = root
            elif action == "split":
                new_entity_id = f"entity-split-{event.event_id}"
                parent[new_entity_id] = new_entity_id
                for mention_id in event.payload.get("mention_event_ids") or []:
                    if isinstance(mention_id, str) and mention_id in mention_assignment:
                        mention_assignment[mention_id] = new_entity_id
            elif action in {"confirm", "contradict", "resolve"}:
                mapped = {
                    "confirm": "confirmed",
                    "contradict": "contradicted",
                    "resolve": "resolved",
                }[str(action)]
                for entity_id in entity_ids:
                    status_assertions.append((entity_id, mapped))
            elif action == "reject_merge" and len(entity_ids) >= 2:
                for index, entity_id in enumerate(entity_ids):
                    for other_id in entity_ids[index + 1:]:
                        rejected_merge_pairs.add(frozenset({entity_id, other_id}))

        explicit_status: dict[str, str] = {}
        for entity_id, mapped in status_assertions:
            explicit_status[find(entity_id)] = mapped

        grouped: dict[str, list[EmbodiedMemoryEvent]] = {}
        for event in mention_events:
            assigned = mention_assignment.get(event.event_id)
            if assigned is None:
                continue
            grouped.setdefault(find(assigned), []).append(event)
        observations = {
            event.event_id: event
            for event in self._store.list_events(mission_id=mission_id, event_type="observation")
            if event.runtime_mode == self._runtime_mode
        }
        projected: list[FireClawEntity] = []
        for entity_id, mentions in grouped.items():
            mentions.sort(key=lambda event: (event.observed_at, event.event_id))
            observation_ids = tuple(dict.fromkeys(
                str(event.payload.get("observation_event_id") or "") for event in mentions
            ))
            evidence_ids = tuple(sorted({
                evidence_identity
                for observation_id in observation_ids
                if observations.get(observation_id) is not None
                for evidence_identity in (_evidence_identity(observations.get(observation_id)),)
                if evidence_identity is not None
            }))
            aliases = tuple(dict.fromkeys(
                str(event.payload.get("name") or "") for event in mentions
            ))
            locations_list: list[EntityLocationAssertion] = []
            tracking_identity_values: set[str] = set()
            source_robot_values: set[str] = set()
            for event in mentions:
                observation_id = str(event.payload.get("observation_event_id") or "")
                observation = observations.get(observation_id)
                owner_robot_id = event.robot_id or (observation.robot_id if observation else None)
                if owner_robot_id:
                    source_robot_values.add(owner_robot_id)
                tracking_identity = event.payload.get("tracking_identity")
                if not isinstance(tracking_identity, str) or not tracking_identity:
                    tracking_identity = _tracking_identity(
                        event.payload.get("tracking_robot_id") or owner_robot_id,
                        str(event.payload.get("tracking_key"))
                        if event.payload.get("tracking_key")
                        else None,
                    )
                if tracking_identity is not None:
                    tracking_identity_values.add(tracking_identity)
                scope = observation.payload.get("spatial_frame_scope") if observation else None
                if event.pose is not None and event.confidence is not None:
                    locations_list.append(EntityLocationAssertion(
                        mention_event_id=event.event_id,
                        observation_event_id=observation_id,
                        observed_at=event.observed_at,
                        pose=event.pose,
                        confidence=float(event.confidence),
                        robot_id=owner_robot_id,
                        spatial_frame_scope=scope if isinstance(scope, str) else None,
                    ))
            locations = tuple(locations_list)
            confidences = [float(event.confidence) for event in mentions if event.confidence is not None]
            status = explicit_status.get(entity_id)
            if status is None:
                status = "corroborated" if len(evidence_ids) >= 2 else "candidate"
            normalized_proposals: set[str] = set()
            for event in mentions:
                for candidate_id_value in event.payload.get("merge_proposal_entity_ids") or []:
                    candidate_id = str(candidate_id_value)
                    if frozenset({entity_id, candidate_id}) in rejected_merge_pairs:
                        continue
                    candidate_root = find(candidate_id)
                    if candidate_root != entity_id:
                        normalized_proposals.add(candidate_root)
            proposal_ids = tuple(sorted(normalized_proposals))
            tracking_identities = tuple(sorted(tracking_identity_values))
            source_robot_ids = tuple(sorted(source_robot_values))
            spatial_frame_scopes = tuple(sorted({
                location.spatial_frame_scope
                for location in locations
                if location.spatial_frame_scope
            }))
            rejected_entity_ids = tuple(sorted({
                other_root
                for pair in rejected_merge_pairs
                if entity_id in {find(value) for value in pair}
                for other_root in {find(value) for value in pair}
                if other_root != entity_id
            }))
            projected.append(FireClawEntity(
                entity_id=entity_id,
                mission_id=mission_id,
                runtime_mode=self._runtime_mode,
                entity_kind=str(mentions[-1].payload.get("entity_kind") or "unknown"),
                canonical_name=aliases[-1],
                aliases=aliases,
                status=status,
                first_seen_at=mentions[0].observed_at,
                last_seen_at=mentions[-1].observed_at,
                observation_count=len(set(observation_ids)),
                mention_event_ids=tuple(event.event_id for event in mentions),
                observation_event_ids=observation_ids,
                tracking_keys=tuple(sorted({
                    str(event.payload["tracking_key"])
                    for event in mentions if event.payload.get("tracking_key")
                })),
                evidence_source_ids=evidence_ids,
                confidence_min=min(confidences),
                confidence_max=max(confidences),
                latest_confidence=confidences[-1],
                current_pose=locations[-1].pose if locations else None,
                location_history=locations,
                attributes=tuple(dict(event.payload.get("attributes") or {}) for event in mentions),
                merge_proposal_entity_ids=proposal_ids,
                tracking_identities=tracking_identities,
                source_robot_ids=source_robot_ids,
                spatial_frame_scopes=spatial_frame_scopes,
                rejected_merge_entity_ids=rejected_entity_ids,
            ))
        return projected

    def _proposal_matches(
        self,
        mention: ExtractedEntityMention,
        entities: list[FireClawEntity],
        observed_at: str,
        *,
        source_robot_id: str | None,
        source_spatial_frame_scope: str | None,
        source_tracking_namespace: str | None,
    ) -> list[FireClawEntity]:
        if mention.pose is None:
            return []
        mention_time = datetime.fromisoformat(observed_at)
        matches: list[FireClawEntity] = []
        for entity in entities:
            if entity.entity_kind != mention.entity_kind or entity.current_pose is None:
                continue
            if source_robot_id is not None and not entity.source_robot_ids:
                # Unknown ownership cannot establish either local continuity
                # or an auditable cross-robot comparison.
                continue
            cross_robot = (
                source_robot_id is not None
                and source_robot_id not in entity.source_robot_ids
            )
            comparison_pose = entity.current_pose
            comparison_time = entity.last_seen_at
            if cross_robot:
                if source_spatial_frame_scope != "mission":
                    continue
                mission_location = _latest_mission_location(entity)
                if mission_location is None:
                    continue
                comparison_pose = mission_location.pose
                comparison_time = mission_location.observed_at
                entity_namespaces = _tracking_namespaces(entity.tracking_keys)
                if (
                    source_tracking_namespace is not None
                    and entity_namespaces
                    and source_tracking_namespace not in entity_namespaces
                ):
                    continue
            if _normalize_name(mention.name) not in {_normalize_name(alias) for alias in entity.aliases}:
                continue
            if comparison_pose.frame_id != mention.pose.frame_id:
                continue
            if comparison_pose.floor != mention.pose.floor:
                continue
            if (comparison_pose.z is None) != (mention.pose.z is None):
                continue
            age = abs((mention_time - datetime.fromisoformat(comparison_time)).total_seconds())
            if age > self.config.proposal_max_time_gap_seconds:
                continue
            distance = _pose_distance(comparison_pose, mention.pose)
            threshold = (
                comparison_pose.uncertainty_radius_m
                + mention.pose.uncertainty_radius_m
                + self.config.proposal_spatial_margin_m
            )
            if distance <= threshold:
                matches.append(entity)
        return matches

    def _require_observation(self, *, mission_id: str, event_id: str) -> EmbodiedMemoryEvent:
        event = next(
            (
                item
                for item in self._store.list_events(mission_id=mission_id, event_type="observation")
                if item.event_id == event_id and item.runtime_mode == self._runtime_mode
            ),
            None,
        )
        if event is None:
            raise ValueError(
                "entity mention must reference an existing observation in the same mission/runtime"
            )
        return event

    def _link_co_observed_mentions(
        self,
        mention_event: EmbodiedMemoryEvent,
        observation_event_id: str,
    ) -> None:
        prior_mentions = [
            event
            for event in self._store.list_events(
                mission_id=mention_event.mission_id,
                event_type="entity_mention",
            )
            if event.runtime_mode == self._runtime_mode
            and event.event_id != mention_event.event_id
            and event.payload.get("observation_event_id") == observation_event_id
        ]
        for prior in prior_mentions:
            self._resolver_producer.add_relation(
                mission_id=mention_event.mission_id,
                source_record_id=mention_event.event_id,
                target_record_id=prior.event_id,
                relation_type="co_observed_with",
                runtime_mode=self._runtime_mode,
            )

    def _mention_ids_for_entities(
        self,
        mission_id: str,
        entity_ids: set[str],
    ) -> tuple[str, ...]:
        return tuple(
            event.event_id
            for event in self._store.list_events(mission_id=mission_id, event_type="entity_mention")
            if event.runtime_mode == self._runtime_mode
            and event.payload.get("entity_id") in entity_ids
        )


def _mention_event_id(
    *,
    mission_id: str,
    extractor_method_id: str,
    extraction_key: str,
) -> str:
    value = f"{mission_id}\n{extractor_method_id}\n{extraction_key}"
    return f"entity-mention-{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _normalize_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().casefold())


def _tracking_identity(robot_id: str | None, tracking_key: str | None) -> str | None:
    """Namespace tracker IDs by their owning robot before exact matching."""
    if tracking_key is None or not tracking_key.strip():
        return None
    if not isinstance(robot_id, str) or not robot_id.strip():
        # An unowned tracker cannot be used for exact identity attachment.
        return None
    owner = robot_id.strip()
    return f"{owner}:{tracking_key}"


def _tracking_namespaces(tracking_keys: tuple[str, ...]) -> set[str]:
    namespaces: set[str] = set()
    for tracking_key in tracking_keys:
        if not isinstance(tracking_key, str) or ":" not in tracking_key:
            continue
        namespace, _ = tracking_key.split(":", 1)
        if namespace:
            namespaces.add(namespace)
    return namespaces


def _tracking_namespace(tracking_key: str | None) -> str | None:
    if not isinstance(tracking_key, str) or ":" not in tracking_key:
        return None
    namespace, _ = tracking_key.split(":", 1)
    return namespace or None


def _latest_mission_location(entity: FireClawEntity) -> EntityLocationAssertion | None:
    """Return the newest location assertion explicitly in the mission frame."""
    for location in reversed(entity.location_history):
        if location.spatial_frame_scope == "mission" and location.robot_id:
            return location
    return None


def _pose_distance(left: SpatialMemoryContext, right: SpatialMemoryContext) -> float:
    values = [left.x - right.x, left.y - right.y]
    if left.z is not None and right.z is not None:
        values.append(left.z - right.z)
    return math.sqrt(sum(value * value for value in values))


def _identity_proposal_id(
    *,
    mission_id: str,
    runtime_mode: str,
    entity_ids: tuple[str, str],
    frame_id: str,
    floor: str | None,
) -> str:
    value = "\n".join((
        "entity-identity-proposal-v1",
        mission_id,
        runtime_mode,
        *entity_ids,
        frame_id,
        floor or "",
    ))
    return f"entity-proposal-{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _entity_to_projection_payload(entity: FireClawEntity) -> dict[str, Any]:
    return asdict(entity)


def _entity_from_projection_payload(payload: dict[str, Any]) -> FireClawEntity:
    if not isinstance(payload, dict):
        raise TypeError("Entity projection payload must be a dictionary")

    def required_string(key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Entity projection field {key} must be a non-empty string")
        return value

    def string_tuple(key: str) -> tuple[str, ...]:
        value = payload.get(key, ())
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"Entity projection field {key} must be a sequence")
        if any(not isinstance(item, str) or not item for item in value):
            raise ValueError(f"Entity projection field {key} contains an invalid value")
        return tuple(value)

    def finite_number(key: str, default: float = 0.0) -> float:
        value = payload.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Entity projection field {key} must be numeric")
        if not math.isfinite(float(value)):
            raise ValueError(f"Entity projection field {key} must be finite")
        return float(value)

    current_pose_data = payload.get("current_pose")
    if current_pose_data is not None and not isinstance(current_pose_data, dict):
        raise ValueError("Entity projection current_pose must be a dictionary or null")
    current_pose = (
        SpatialMemoryContext.from_dict(current_pose_data)
        if isinstance(current_pose_data, dict)
        else None
    )
    raw_locations = payload.get("location_history", ())
    if not isinstance(raw_locations, (list, tuple)):
        raise ValueError("Entity projection location_history must be a sequence")
    locations: list[EntityLocationAssertion] = []
    for raw_location in raw_locations:
        if not isinstance(raw_location, dict):
            raise ValueError("Entity projection location assertion must be a dictionary")
        pose_data = raw_location.get("pose")
        if not isinstance(pose_data, dict):
            raise ValueError("Entity projection location assertion requires pose")
        confidence = raw_location.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
        ):
            raise ValueError("Entity projection location confidence must be numeric")
        mention_event_id = raw_location.get("mention_event_id")
        observation_event_id = raw_location.get("observation_event_id")
        observed_at = raw_location.get("observed_at")
        if not all(
            isinstance(value, str) and value
            for value in (mention_event_id, observation_event_id, observed_at)
        ):
            raise ValueError("Entity projection location assertion has invalid identifiers")
        locations.append(EntityLocationAssertion(
            mention_event_id=mention_event_id,
            observation_event_id=observation_event_id,
            observed_at=observed_at,
            pose=SpatialMemoryContext.from_dict(pose_data),
            confidence=float(confidence),
            robot_id=(
                raw_location["robot_id"]
                if isinstance(raw_location.get("robot_id"), str)
                and raw_location["robot_id"]
                else None
            ),
            spatial_frame_scope=(
                raw_location["spatial_frame_scope"]
                if isinstance(raw_location.get("spatial_frame_scope"), str)
                and raw_location["spatial_frame_scope"]
                else None
            ),
        ))
    raw_attributes = payload.get("attributes", ())
    if not isinstance(raw_attributes, (list, tuple)):
        raise ValueError("Entity projection attributes must be a sequence")
    if any(not isinstance(item, dict) for item in raw_attributes):
        raise ValueError("Entity projection attributes contain an invalid value")
    attributes = tuple(dict(item) for item in raw_attributes)
    observation_count = payload.get("observation_count", 0)
    if isinstance(observation_count, bool) or not isinstance(observation_count, int):
        raise ValueError("Entity projection observation_count must be an integer")
    return FireClawEntity(
        entity_id=required_string("entity_id"),
        mission_id=required_string("mission_id"),
        runtime_mode=required_string("runtime_mode"),
        entity_kind=required_string("entity_kind"),
        canonical_name=required_string("canonical_name"),
        aliases=string_tuple("aliases"),
        status=required_string("status"),
        first_seen_at=required_string("first_seen_at"),
        last_seen_at=required_string("last_seen_at"),
        observation_count=observation_count,
        mention_event_ids=string_tuple("mention_event_ids"),
        observation_event_ids=string_tuple("observation_event_ids"),
        tracking_keys=string_tuple("tracking_keys"),
        evidence_source_ids=string_tuple("evidence_source_ids"),
        confidence_min=finite_number("confidence_min"),
        confidence_max=finite_number("confidence_max"),
        latest_confidence=finite_number("latest_confidence"),
        current_pose=current_pose,
        location_history=tuple(locations),
        attributes=attributes,
        merge_proposal_entity_ids=string_tuple("merge_proposal_entity_ids"),
        tracking_identities=string_tuple("tracking_identities"),
        source_robot_ids=string_tuple("source_robot_ids"),
        spatial_frame_scopes=string_tuple("spatial_frame_scopes"),
        rejected_merge_entity_ids=string_tuple("rejected_merge_entity_ids"),
    )


def _evidence_identity(event: EmbodiedMemoryEvent | None) -> str | None:
    if event is None or event.provenance is None:
        return None
    if event.provenance.sensor_id is not None:
        return f"sensor:{event.provenance.sensor_id}"
    if event.provenance.source_id is not None:
        return f"source:{event.provenance.source_id}"
    return f"producer:{event.provenance.producer_id}"
