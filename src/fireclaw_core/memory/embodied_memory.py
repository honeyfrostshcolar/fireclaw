"""Typed embodied-memory events over FireClaw's append-only mission memory.

The JSONL evidence log remains authoritative.  ``SqliteMemoryIndex`` is a
derived projection used for lexical, spatial, temporal, and graph queries and
can be rebuilt from the evidence log at any time.
"""
from __future__ import annotations

import math
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from fireclaw_core.memory.memory_index import SqliteMemoryIndex
from fireclaw_core.mission.mission_memory import MissionMemoryRecord, MissionMemoryStore


logger = logging.getLogger(__name__)


class WorkingMemoryProjection(Protocol):
    def add_persisted(self, event: "EmbodiedMemoryEvent") -> bool:
        ...


EMBODIED_METADATA_KEY = "_embodied"
RELATION_METADATA_KEY = "_relation"

EMBODIED_EVENT_TYPES: frozenset[str] = frozenset({
    "body_state",
    "command",
    "correction",
    "episode",
    "entity_mention",
    "entity_resolution",
    "gist",
    "lesson",
    "mission",
    "observation",
    "outcome",
    "plan",
    "safety_decision",
    "skill_invocation",
    "subtask",
})

MEMORY_RELATION_TYPES: frozenset[str] = frozenset({
    "belongs_to",
    "caused_by",
    "corrects",
    "co_observed_with",
    "follows",
    "observed_in",
    "subtask_of",
    "summarizes",
    "supports",
})

MEMORY_RUNTIME_MODES: frozenset[str] = frozenset({
    "real",
    "replay",
    "simulation",
})

MEMORY_SENSITIVITY_LEVELS: frozenset[str] = frozenset({
    "restricted",
    "standard",
})

MEMORY_EVIDENCE_KINDS: frozenset[str] = frozenset({
    "cognitive_artifact",
    "derived_summary",
    "operator_assertion",
    "runtime_evidence",
    "sensor_evidence",
})

MEMORY_PRODUCER_EVENT_TYPES: dict[str, frozenset[str]] = {
    "approval_runtime": frozenset({"correction", "entity_resolution", "safety_decision"}),
    "entity_resolver": frozenset({"entity_mention", "entity_resolution"}),
    "memory_consolidator": frozenset({"episode", "gist", "lesson"}),
    "mission_agent": frozenset({
        "command",
        "correction",
        "mission",
        "outcome",
        "plan",
        "subtask",
    }),
    "operator_gateway": frozenset({"command", "correction"}),
    "perception": frozenset({"observation"}),
    "replay": frozenset(EMBODIED_EVENT_TYPES - {"gist", "lesson"}),
    "robot_adapter": frozenset({"body_state", "observation", "outcome"}),
    "safety_gate": frozenset({"safety_decision"}),
    "sensor_adapter": frozenset({"body_state", "observation"}),
    "simulator": frozenset({"body_state", "observation", "outcome"}),
    "skill_runtime": frozenset({"skill_invocation", "outcome"}),
}

MEMORY_PRODUCER_EVIDENCE_KINDS: dict[str, frozenset[str]] = {
    "approval_runtime": frozenset({"operator_assertion", "runtime_evidence"}),
    "entity_resolver": frozenset({"cognitive_artifact", "runtime_evidence"}),
    "memory_consolidator": frozenset({"derived_summary"}),
    "mission_agent": frozenset({
        "cognitive_artifact",
        "operator_assertion",
        "runtime_evidence",
    }),
    "operator_gateway": frozenset({"operator_assertion"}),
    "perception": frozenset({"sensor_evidence"}),
    "replay": frozenset(MEMORY_EVIDENCE_KINDS - {"derived_summary"}),
    "robot_adapter": frozenset({"runtime_evidence"}),
    "safety_gate": frozenset({"runtime_evidence"}),
    "sensor_adapter": frozenset({"sensor_evidence"}),
    "simulator": frozenset({"runtime_evidence", "sensor_evidence"}),
    "skill_runtime": frozenset({"runtime_evidence"}),
}

MEMORY_EVIDENCE_EVENT_TYPES: dict[str, frozenset[str]] = {
    "cognitive_artifact": frozenset({"entity_mention", "mission", "outcome", "plan", "subtask"}),
    "derived_summary": frozenset({"episode", "gist", "lesson"}),
    "operator_assertion": frozenset({
        "command",
        "correction",
        "entity_resolution",
        "safety_decision",
    }),
    "runtime_evidence": frozenset({
        "body_state",
        "entity_resolution",
        "mission",
        "observation",
        "outcome",
        "safety_decision",
        "skill_invocation",
    }),
    "sensor_evidence": frozenset({"body_state", "observation"}),
}


@dataclass(frozen=True)
class MemoryEventProvenance:
    """Identifies who asserted an event and what kind of evidence it is."""

    producer_type: str
    producer_id: str
    evidence_kind: str
    source_id: str | None = None
    method_id: str | None = None
    sensor_id: str | None = None

    def __post_init__(self) -> None:
        if self.producer_type not in MEMORY_PRODUCER_EVENT_TYPES:
            raise ValueError(
                f"Invalid memory producer_type: {self.producer_type}. "
                f"Must be one of: {sorted(MEMORY_PRODUCER_EVENT_TYPES)}"
            )
        _require_nonempty("producer_id", self.producer_id)
        if self.evidence_kind not in MEMORY_EVIDENCE_KINDS:
            raise ValueError(
                f"Invalid memory evidence_kind: {self.evidence_kind}. "
                f"Must be one of: {sorted(MEMORY_EVIDENCE_KINDS)}"
            )
        for field_name, value in (
            ("source_id", self.source_id),
            ("method_id", self.method_id),
            ("sensor_id", self.sensor_id),
        ):
            if value is not None:
                _require_nonempty(field_name, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "producer_type": self.producer_type,
            "producer_id": self.producer_id,
            "evidence_kind": self.evidence_kind,
            "source_id": self.source_id,
            "method_id": self.method_id,
            "sensor_id": self.sensor_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEventProvenance:
        return cls(
            producer_type=str(data.get("producer_type") or ""),
            producer_id=str(data.get("producer_id") or ""),
            evidence_kind=str(data.get("evidence_kind") or ""),
            source_id=str(data["source_id"]) if data.get("source_id") is not None else None,
            method_id=str(data["method_id"]) if data.get("method_id") is not None else None,
            sensor_id=str(data["sensor_id"]) if data.get("sensor_id") is not None else None,
        )


@dataclass(frozen=True)
class SpatialMemoryContext:
    """Position attached to a memory event.

    ``frame_id`` is mandatory so coordinates from different maps are never
    compared implicitly.  ``uncertainty_radius_m`` models positional
    uncertainty and is considered by radius queries.
    """

    frame_id: str
    x: float
    y: float
    z: float | None = None
    floor: str | None = None
    uncertainty_radius_m: float = 0.0

    def __post_init__(self) -> None:
        _require_nonempty("frame_id", self.frame_id)
        _require_finite("x", self.x)
        _require_finite("y", self.y)
        if self.z is not None:
            _require_finite("z", self.z)
        _require_finite("uncertainty_radius_m", self.uncertainty_radius_m)
        if self.uncertainty_radius_m < 0:
            raise ValueError("uncertainty_radius_m must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "x": float(self.x),
            "y": float(self.y),
            "z": float(self.z) if self.z is not None else None,
            "floor": self.floor,
            "uncertainty_radius_m": float(self.uncertainty_radius_m),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpatialMemoryContext:
        return cls(
            frame_id=str(data.get("frame_id") or ""),
            x=_as_float(data.get("x"), field_name="x"),
            y=_as_float(data.get("y"), field_name="y"),
            z=(
                _as_float(data.get("z"), field_name="z")
                if data.get("z") is not None
                else None
            ),
            floor=str(data["floor"]) if data.get("floor") is not None else None,
            uncertainty_radius_m=_as_float(
                data.get("uncertainty_radius_m", 0.0),
                field_name="uncertainty_radius_m",
            ),
        )


@dataclass(frozen=True)
class EmbodiedMemoryEvent:
    """A typed, auditable event captured during an embodied mission."""

    event_id: str
    mission_id: str
    event_type: str
    payload: dict[str, Any]
    runtime_mode: str
    source_type: str
    observed_at: str
    robot_id: str | None = None
    subtask_id: str | None = None
    episode_id: str | None = None
    created_at: str = ""
    pose: SpatialMemoryContext | None = None
    confidence: float | None = None
    sensitivity: str = "standard"
    derived_from: tuple[str, ...] = ()
    provenance: MemoryEventProvenance | None = None

    def __post_init__(self) -> None:
        _require_nonempty("event_id", self.event_id)
        _require_nonempty("mission_id", self.mission_id)
        if self.event_type not in EMBODIED_EVENT_TYPES:
            raise ValueError(
                f"Invalid embodied event type: {self.event_type}. "
                f"Must be one of: {sorted(EMBODIED_EVENT_TYPES)}"
            )
        _validate_runtime_mode(self.runtime_mode)
        _require_nonempty("source_type", self.source_type)
        _validate_timestamp("observed_at", self.observed_at)
        if not self.created_at:
            object.__setattr__(self, "created_at", self.observed_at)
        _validate_timestamp("created_at", self.created_at)
        if not isinstance(self.payload, dict):
            raise ValueError("payload must be a dictionary")
        if EMBODIED_METADATA_KEY in self.payload or RELATION_METADATA_KEY in self.payload:
            raise ValueError("payload contains a reserved embodied-memory key")
        if self.confidence is not None:
            _require_finite("confidence", self.confidence)
            if not 0.0 <= self.confidence <= 1.0:
                raise ValueError("confidence must be between 0 and 1")
        if self.sensitivity not in MEMORY_SENSITIVITY_LEVELS:
            raise ValueError(
                f"Invalid sensitivity: {self.sensitivity}. "
                f"Must be one of: {sorted(MEMORY_SENSITIVITY_LEVELS)}"
            )
        if self.event_id in self.derived_from:
            raise ValueError("derived_from cannot contain the event itself")
        for record_id in self.derived_from:
            _require_nonempty("derived_from record id", record_id)
        if len(set(self.derived_from)) != len(self.derived_from):
            raise ValueError("derived_from cannot contain duplicate record ids")

    def to_mission_record(self) -> MissionMemoryRecord:
        content = dict(self.payload)
        content[EMBODIED_METADATA_KEY] = {
            "schema_version": 2 if self.provenance is not None else 1,
            "runtime_mode": self.runtime_mode,
            "source_type": self.source_type,
            "observed_at": self.observed_at,
            "episode_id": self.episode_id,
            "pose": self.pose.to_dict() if self.pose is not None else None,
            "confidence": self.confidence,
            "sensitivity": self.sensitivity,
            "derived_from": list(self.derived_from),
            "provenance": self.provenance.to_dict() if self.provenance is not None else None,
        }
        return MissionMemoryRecord(
            record_id=self.event_id,
            mission_id=self.mission_id,
            record_type=self.event_type,
            content=content,
            robot_id=self.robot_id,
            subtask_id=self.subtask_id,
            created_at=self.created_at,
        )

    @classmethod
    def from_mission_record(cls, record: MissionMemoryRecord) -> EmbodiedMemoryEvent:
        metadata = record.content.get(EMBODIED_METADATA_KEY)
        if not isinstance(metadata, dict):
            raise ValueError(f"Record {record.record_id} is not an embodied-memory event")
        pose_data = metadata.get("pose")
        pose = SpatialMemoryContext.from_dict(pose_data) if isinstance(pose_data, dict) else None
        derived_from_raw = metadata.get("derived_from")
        derived_from = (
            tuple(str(value) for value in derived_from_raw if isinstance(value, str))
            if isinstance(derived_from_raw, list)
            else ()
        )
        provenance_data = metadata.get("provenance")
        provenance = (
            MemoryEventProvenance.from_dict(provenance_data)
            if isinstance(provenance_data, dict)
            else None
        )
        return cls(
            event_id=record.record_id,
            mission_id=record.mission_id,
            event_type=record.record_type,
            payload={
                key: value
                for key, value in record.content.items()
                if key not in {EMBODIED_METADATA_KEY, RELATION_METADATA_KEY}
            },
            runtime_mode=str(metadata.get("runtime_mode") or ""),
            source_type=str(metadata.get("source_type") or ""),
            observed_at=str(metadata.get("observed_at") or record.created_at),
            robot_id=record.robot_id,
            subtask_id=record.subtask_id,
            episode_id=(
                str(metadata["episode_id"])
                if metadata.get("episode_id") is not None
                else None
            ),
            created_at=record.created_at,
            pose=pose,
            confidence=(
                _as_float(metadata.get("confidence"), field_name="confidence")
                if metadata.get("confidence") is not None
                else None
            ),
            sensitivity=str(metadata.get("sensitivity") or "standard"),
            derived_from=derived_from,
            provenance=provenance,
        )


@dataclass(frozen=True)
class EmbodiedMemoryProductionPolicy:
    """Validates runtime-produced events before evidence is appended."""

    def validate(self, event: EmbodiedMemoryEvent) -> None:
        provenance = event.provenance
        if provenance is None:
            raise ValueError("Runtime-produced embodied events require provenance")

        allowed_events = MEMORY_PRODUCER_EVENT_TYPES[provenance.producer_type]
        if event.event_type not in allowed_events:
            raise ValueError(
                f"Producer {provenance.producer_type} cannot assert event type "
                f"{event.event_type}"
            )

        allowed_evidence = MEMORY_PRODUCER_EVIDENCE_KINDS[provenance.producer_type]
        if provenance.evidence_kind not in allowed_evidence:
            raise ValueError(
                f"Producer {provenance.producer_type} cannot assert evidence kind "
                f"{provenance.evidence_kind}"
            )

        allowed_evidence_events = MEMORY_EVIDENCE_EVENT_TYPES[provenance.evidence_kind]
        if event.event_type not in allowed_evidence_events:
            raise ValueError(
                f"Evidence kind {provenance.evidence_kind} cannot describe event type "
                f"{event.event_type}"
            )

        if provenance.evidence_kind == "operator_assertion" and provenance.source_id is None:
            raise ValueError("operator_assertion requires an opaque source_id")
        if provenance.evidence_kind == "sensor_evidence":
            if provenance.sensor_id is None:
                raise ValueError("sensor_evidence requires sensor_id")
            if event.confidence is None:
                raise ValueError("sensor_evidence requires confidence")
        if provenance.evidence_kind in {"cognitive_artifact", "derived_summary"}:
            if provenance.method_id is None:
                raise ValueError(f"{provenance.evidence_kind} requires method_id")
        if provenance.evidence_kind == "derived_summary":
            if not event.derived_from:
                raise ValueError("derived_summary requires derived_from evidence ids")
            if event.confidence is None:
                raise ValueError("derived_summary requires confidence")


@dataclass(frozen=True)
class EmbodiedMemoryRelation:
    """A directed relation between two embodied-memory events."""

    relation_id: str
    mission_id: str
    source_record_id: str
    target_record_id: str
    relation_type: str
    runtime_mode: str
    created_at: str
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        _require_nonempty("relation_id", self.relation_id)
        _require_nonempty("mission_id", self.mission_id)
        _require_nonempty("source_record_id", self.source_record_id)
        _require_nonempty("target_record_id", self.target_record_id)
        if self.source_record_id == self.target_record_id:
            raise ValueError("A memory relation cannot point to itself")
        if self.relation_type not in MEMORY_RELATION_TYPES:
            raise ValueError(
                f"Invalid memory relation type: {self.relation_type}. "
                f"Must be one of: {sorted(MEMORY_RELATION_TYPES)}"
            )
        _validate_runtime_mode(self.runtime_mode)
        _validate_timestamp("created_at", self.created_at)
        if self.metadata is not None and not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dictionary")

    def to_mission_record(self) -> MissionMemoryRecord:
        return MissionMemoryRecord(
            record_id=self.relation_id,
            mission_id=self.mission_id,
            record_type="relation",
            content={
                EMBODIED_METADATA_KEY: {
                    "schema_version": 1,
                    "runtime_mode": self.runtime_mode,
                    "source_type": "memory_graph",
                    "observed_at": self.created_at,
                    "sensitivity": "standard",
                },
                RELATION_METADATA_KEY: {
                    "source_record_id": self.source_record_id,
                    "target_record_id": self.target_record_id,
                    "relation_type": self.relation_type,
                    "metadata": dict(self.metadata or {}),
                },
            },
            created_at=self.created_at,
        )

    def to_index_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "mission_id": self.mission_id,
            "source_record_id": self.source_record_id,
            "target_record_id": self.target_record_id,
            "relation_type": self.relation_type,
            "runtime_mode": self.runtime_mode,
            "created_at": self.created_at,
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_mission_record(cls, record: MissionMemoryRecord) -> EmbodiedMemoryRelation:
        embodied = record.content.get(EMBODIED_METADATA_KEY)
        relation = record.content.get(RELATION_METADATA_KEY)
        if (
            record.record_type != "relation"
            or not isinstance(embodied, dict)
            or not isinstance(relation, dict)
        ):
            raise ValueError(f"Record {record.record_id} is not an embodied-memory relation")
        metadata = relation.get("metadata")
        return cls(
            relation_id=record.record_id,
            mission_id=record.mission_id,
            source_record_id=str(relation.get("source_record_id") or ""),
            target_record_id=str(relation.get("target_record_id") or ""),
            relation_type=str(relation.get("relation_type") or ""),
            runtime_mode=str(embodied.get("runtime_mode") or ""),
            created_at=record.created_at,
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
        )


@dataclass(frozen=True)
class EmbodiedMemoryIndexingPolicy:
    """Controls which event payloads are exposed to full-text search."""

    text_excluded_event_types: frozenset[str] = frozenset({"body_state", "correction"})
    text_excluded_sensitivities: frozenset[str] = frozenset({"restricted"})

    def should_index_text(self, event: EmbodiedMemoryEvent) -> bool:
        return (
            event.event_type not in self.text_excluded_event_types
            and event.sensitivity not in self.text_excluded_sensitivities
        )


class EmbodiedMemoryStore:
    """Append evidence first, then maintain a rebuildable SQLite projection."""

    def __init__(
        self,
        evidence_path: str | Path,
        *,
        index_path: str | Path | None = None,
        indexing_policy: EmbodiedMemoryIndexingPolicy | None = None,
    ) -> None:
        self._evidence = MissionMemoryStore(evidence_path)
        self._index = SqliteMemoryIndex(index_path) if index_path is not None else None
        self._indexing_policy = indexing_policy or EmbodiedMemoryIndexingPolicy()

    @property
    def evidence_store(self) -> MissionMemoryStore:
        return self._evidence

    @property
    def index(self) -> SqliteMemoryIndex | None:
        return self._index

    def record_event(
        self,
        *,
        mission_id: str,
        event_type: str,
        payload: dict[str, Any],
        runtime_mode: str,
        source_type: str,
        robot_id: str | None = None,
        subtask_id: str | None = None,
        episode_id: str | None = None,
        observed_at: str | None = None,
        created_at: str | None = None,
        pose: SpatialMemoryContext | None = None,
        confidence: float | None = None,
        sensitivity: str = "standard",
        derived_from: tuple[str, ...] = (),
        event_id: str | None = None,
    ) -> EmbodiedMemoryEvent:
        timestamp = observed_at or _utc_now()
        event = EmbodiedMemoryEvent(
            event_id=event_id or uuid.uuid4().hex,
            mission_id=mission_id,
            event_type=event_type,
            payload=payload,
            runtime_mode=runtime_mode,
            source_type=source_type,
            observed_at=timestamp,
            robot_id=robot_id,
            subtask_id=subtask_id,
            episode_id=episode_id,
            created_at=created_at or timestamp,
            pose=pose,
            confidence=confidence,
            sensitivity=sensitivity,
            derived_from=derived_from,
        )
        self.append_event(event)
        return event

    def append_event(self, event: EmbodiedMemoryEvent) -> None:
        self._assert_record_id_unused(event.event_id)
        record = event.to_mission_record()
        self._evidence.append(record)
        if self._index is not None:
            self._index.upsert(
                record.to_dict(),
                index_text=self._indexing_policy.should_index_text(event),
            )

    def add_relation(
        self,
        *,
        mission_id: str,
        source_record_id: str,
        target_record_id: str,
        relation_type: str,
        runtime_mode: str,
        created_at: str | None = None,
        metadata: dict[str, Any] | None = None,
        relation_id: str | None = None,
    ) -> EmbodiedMemoryRelation:
        relation = EmbodiedMemoryRelation(
            relation_id=relation_id or uuid.uuid4().hex,
            mission_id=mission_id,
            source_record_id=source_record_id,
            target_record_id=target_record_id,
            relation_type=relation_type,
            runtime_mode=runtime_mode,
            created_at=created_at or _utc_now(),
            metadata=metadata,
        )
        self.append_relation(relation)
        return relation

    def append_relation(self, relation: EmbodiedMemoryRelation) -> None:
        self._assert_record_id_unused(relation.relation_id)
        self._validate_relation_endpoints(relation)
        self._evidence.append(relation.to_mission_record())
        if self._index is not None:
            self._index.upsert_relation(relation.to_index_dict())

    def list_events(
        self,
        *,
        mission_id: str | None = None,
        event_type: str | None = None,
    ) -> list[EmbodiedMemoryEvent]:
        events: list[EmbodiedMemoryEvent] = []
        for record in self._evidence.list_records(mission_id=mission_id):
            if record.record_type == "relation" or EMBODIED_METADATA_KEY not in record.content:
                continue
            try:
                event = EmbodiedMemoryEvent.from_mission_record(record)
            except ValueError:
                continue
            if event_type is None or event.event_type == event_type:
                events.append(event)
        return events

    def list_relations(self, *, mission_id: str | None = None) -> list[EmbodiedMemoryRelation]:
        relations: list[EmbodiedMemoryRelation] = []
        for record in self._evidence.list_records(mission_id=mission_id, record_type="relation"):
            try:
                relations.append(EmbodiedMemoryRelation.from_mission_record(record))
            except ValueError:
                continue
        return relations

    def search_text(
        self,
        query: str,
        *,
        runtime_mode: str,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        index = self._require_index()
        _validate_runtime_mode(runtime_mode)
        active_filters = dict(filters or {})
        active_filters["runtime_mode"] = runtime_mode
        return index.search(query, filters=active_filters, limit=limit)

    def query_spatial(
        self,
        *,
        runtime_mode: str,
        frame_id: str,
        x: float,
        y: float,
        radius_m: float,
        z: float | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        _validate_runtime_mode(runtime_mode)
        return self._require_index().search_spatial(
            runtime_mode=runtime_mode,
            frame_id=frame_id,
            x=x,
            y=y,
            z=z,
            radius_m=radius_m,
            filters=filters,
            limit=limit,
        )

    def query_temporal(
        self,
        *,
        runtime_mode: str,
        start_at: str,
        end_at: str,
        filters: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        _validate_runtime_mode(runtime_mode)
        return self._require_index().search_temporal(
            runtime_mode=runtime_mode,
            start_at=start_at,
            end_at=end_at,
            filters=filters,
            limit=limit,
        )

    def neighbors(
        self,
        record_id: str,
        *,
        runtime_mode: str,
        direction: str = "both",
        relation_types: frozenset[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        _validate_runtime_mode(runtime_mode)
        return self._require_index().neighbors(
            record_id,
            runtime_mode=runtime_mode,
            direction=direction,
            relation_types=relation_types,
            limit=limit,
        )

    def rebuild_index(self) -> int:
        index = self._require_index()
        records = self._evidence.list_records()
        relations: list[EmbodiedMemoryRelation] = []
        index.clear()
        count = 0
        for record in records:
            if record.record_type == "relation":
                try:
                    relations.append(EmbodiedMemoryRelation.from_mission_record(record))
                except ValueError:
                    continue
                continue
            try:
                event = EmbodiedMemoryEvent.from_mission_record(record)
            except ValueError:
                index.upsert(record.to_dict())
            else:
                index.upsert(
                    record.to_dict(),
                    index_text=self._indexing_policy.should_index_text(event),
                )
            count += 1
        for relation in relations:
            index.upsert_relation(relation.to_index_dict())
            count += 1
        return count

    def _require_index(self) -> SqliteMemoryIndex:
        if self._index is None:
            raise RuntimeError("No embodied memory index configured. Pass index_path.")
        return self._index

    def _assert_record_id_unused(self, record_id: str) -> None:
        if any(record.record_id == record_id for record in self._evidence.list_records()):
            raise ValueError(f"Memory record id already exists: {record_id}")

    def _validate_relation_endpoints(self, relation: EmbodiedMemoryRelation) -> None:
        events = {event.event_id: event for event in self.list_events()}
        source = events.get(relation.source_record_id)
        target = events.get(relation.target_record_id)
        if source is None or target is None:
            raise ValueError("Memory relation endpoints must reference existing embodied events")
        if source.mission_id != relation.mission_id or target.mission_id != relation.mission_id:
            raise ValueError("Memory relation endpoints must belong to the relation mission")
        if source.runtime_mode != relation.runtime_mode or target.runtime_mode != relation.runtime_mode:
            raise ValueError("Memory relation endpoints cannot cross runtime modes")


class EmbodiedMemoryProducer:
    """Policy-enforced event writer for live FireClaw runtime boundaries."""

    def __init__(
        self,
        store: EmbodiedMemoryStore,
        *,
        producer_type: str,
        producer_id: str,
        policy: EmbodiedMemoryProductionPolicy | None = None,
        working_memory: WorkingMemoryProjection | None = None,
    ) -> None:
        if producer_type not in MEMORY_PRODUCER_EVENT_TYPES:
            raise ValueError(
                f"Invalid memory producer_type: {producer_type}. "
                f"Must be one of: {sorted(MEMORY_PRODUCER_EVENT_TYPES)}"
            )
        _require_nonempty("producer_id", producer_id)
        self._store = store
        self._producer_type = producer_type
        self._producer_id = producer_id
        self._policy = policy or EmbodiedMemoryProductionPolicy()
        self._working_memory = working_memory

    @property
    def producer_type(self) -> str:
        return self._producer_type

    @property
    def producer_id(self) -> str:
        return self._producer_id

    @property
    def store(self) -> EmbodiedMemoryStore:
        return self._store

    def record_event(
        self,
        *,
        mission_id: str,
        event_type: str,
        evidence_kind: str,
        payload: dict[str, Any],
        runtime_mode: str,
        source_type: str,
        source_id: str | None = None,
        method_id: str | None = None,
        sensor_id: str | None = None,
        robot_id: str | None = None,
        subtask_id: str | None = None,
        episode_id: str | None = None,
        observed_at: str | None = None,
        created_at: str | None = None,
        pose: SpatialMemoryContext | None = None,
        confidence: float | None = None,
        sensitivity: str = "standard",
        derived_from: tuple[str, ...] = (),
        event_id: str | None = None,
    ) -> EmbodiedMemoryEvent:
        timestamp = observed_at or _utc_now()
        event = EmbodiedMemoryEvent(
            event_id=event_id or uuid.uuid4().hex,
            mission_id=mission_id,
            event_type=event_type,
            payload=payload,
            runtime_mode=runtime_mode,
            source_type=source_type,
            observed_at=timestamp,
            robot_id=robot_id,
            subtask_id=subtask_id,
            episode_id=episode_id,
            created_at=created_at or timestamp,
            pose=pose,
            confidence=confidence,
            sensitivity=sensitivity,
            derived_from=derived_from,
            provenance=MemoryEventProvenance(
                producer_type=self._producer_type,
                producer_id=self._producer_id,
                evidence_kind=evidence_kind,
                source_id=source_id,
                method_id=method_id,
                sensor_id=sensor_id,
            ),
        )
        self._policy.validate(event)
        self._store.append_event(event)
        if self._working_memory is not None:
            try:
                self._working_memory.add_persisted(event)
            except Exception:
                logger.warning("Failed to update embodied working memory", exc_info=True)
        return event

    def add_relation(
        self,
        *,
        mission_id: str,
        source_record_id: str,
        target_record_id: str,
        relation_type: str,
        runtime_mode: str,
        created_at: str | None = None,
        metadata: dict[str, Any] | None = None,
        relation_id: str | None = None,
    ) -> EmbodiedMemoryRelation:
        """Write a relation whose endpoints are explicit, existing local events."""
        _validate_runtime_mode(runtime_mode)
        relation_metadata = dict(metadata or {})
        relation_metadata["asserted_by"] = {
            "producer_type": self._producer_type,
            "producer_id": self._producer_id,
        }
        return self._store.add_relation(
            mission_id=mission_id,
            source_record_id=source_record_id,
            target_record_id=target_record_id,
            relation_type=relation_type,
            runtime_mode=runtime_mode,
            created_at=created_at,
            metadata=relation_metadata,
            relation_id=relation_id,
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_runtime_mode(runtime_mode: str) -> None:
    if runtime_mode not in MEMORY_RUNTIME_MODES:
        raise ValueError(
            f"Invalid runtime_mode: {runtime_mode}. "
            f"Must be one of: {sorted(MEMORY_RUNTIME_MODES)}"
        )


def _validate_timestamp(field_name: str, value: str) -> None:
    _require_nonempty(field_name, value)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")


def _require_nonempty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_finite(field_name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number")


def _as_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite number")
    return parsed
