"""Conservative automatic extraction from structured Observation payloads."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Protocol

from fireclaw_core.memory.embodied_memory import (
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryEvent,
    EmbodiedMemoryStore,
    SpatialMemoryContext,
)
from fireclaw_core.memory.entity_memory import (
    EntityIngestResult,
    EntityMemoryService,
    ExtractedEntityMention,
)


STRUCTURED_ENTITY_EXTRACTOR_METHOD_ID = (
    "fireclaw.structured-observation-entity-extractor:v1"
)


@dataclass(frozen=True)
class EntityExtractionIssue:
    item_index: int | None
    message: str


@dataclass(frozen=True)
class EntityExtractionBatch:
    mentions: tuple[ExtractedEntityMention, ...]
    issues: tuple[EntityExtractionIssue, ...] = ()


@dataclass(frozen=True)
class EntityExtractionReport:
    observation_event_id: str
    mention_event_ids: tuple[str, ...]
    entity_ids: tuple[str, ...]
    skipped_extraction_keys: tuple[str, ...]
    issues: tuple[EntityExtractionIssue, ...]


class ObservationEntityExtractor(Protocol):
    method_id: str

    def extract(self, observation: EmbodiedMemoryEvent) -> EntityExtractionBatch:
        ...


class StructuredObservationEntityExtractor:
    """Parses only the explicit ``payload.entities`` observation contract."""

    method_id = STRUCTURED_ENTITY_EXTRACTOR_METHOD_ID

    def extract(self, observation: EmbodiedMemoryEvent) -> EntityExtractionBatch:
        if observation.event_type != "observation":
            raise ValueError("entity extraction requires an observation event")
        raw_entities = observation.payload.get("entities")
        if raw_entities is None:
            return EntityExtractionBatch(mentions=())
        if not isinstance(raw_entities, list):
            return EntityExtractionBatch(
                mentions=(),
                issues=(EntityExtractionIssue(None, "observation payload.entities must be a list"),),
            )

        mentions: list[ExtractedEntityMention] = []
        issues: list[EntityExtractionIssue] = []
        for index, raw in enumerate(raw_entities):
            if not isinstance(raw, dict):
                issues.append(EntityExtractionIssue(index, "entity item must be an object"))
                continue
            try:
                mentions.append(self._parse_mention(observation, raw, index))
            except (TypeError, ValueError) as exc:
                issues.append(EntityExtractionIssue(index, str(exc)))
        return EntityExtractionBatch(mentions=tuple(mentions), issues=tuple(issues))

    def _parse_mention(
        self,
        observation: EmbodiedMemoryEvent,
        raw: dict[str, Any],
        index: int,
    ) -> ExtractedEntityMention:
        name = raw.get("name")
        entity_kind = raw.get("entity_kind")
        confidence = raw.get("confidence")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("entity name must be a non-empty string")
        if not isinstance(entity_kind, str) or not entity_kind.strip():
            raise ValueError("entity_kind must be a non-empty string")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("entity confidence must be an explicit number")
        pose_data = raw.get("pose")
        if pose_data is not None and not isinstance(pose_data, dict):
            raise ValueError("entity pose must be an object when provided")
        pose = SpatialMemoryContext.from_dict(pose_data) if pose_data is not None else None
        attributes = raw.get("attributes", {})
        if not isinstance(attributes, dict):
            raise ValueError("entity attributes must be an object")
        track_namespace = _optional_string(raw, "source_track_namespace")
        track_id = _optional_string(raw, "source_track_id")
        extraction_key = _extraction_key(
            method_id=self.method_id,
            observation_event_id=observation.event_id,
            item_index=index,
            raw=raw,
        )
        return ExtractedEntityMention(
            name=name.strip(),
            entity_kind=entity_kind.strip(),
            observation_event_id=observation.event_id,
            confidence=float(confidence),
            pose=pose,
            source_track_namespace=track_namespace,
            source_track_id=track_id,
            attributes=dict(attributes),
            extraction_key=extraction_key,
        )


class EntityExtractionPipeline:
    """Runs a validated extractor and idempotently persists candidate mentions."""

    def __init__(
        self,
        *,
        store: EmbodiedMemoryStore,
        entity_memory: EntityMemoryService,
        runtime_mode: str,
        extractor: ObservationEntityExtractor | None = None,
    ) -> None:
        if runtime_mode not in MEMORY_RUNTIME_MODES:
            raise ValueError(
                f"Invalid runtime_mode: {runtime_mode}. "
                f"Must be one of: {sorted(MEMORY_RUNTIME_MODES)}"
            )
        self._store = store
        self._entity_memory = entity_memory
        self._runtime_mode = runtime_mode
        self.extractor = extractor or StructuredObservationEntityExtractor()

    def process_observation(self, observation: EmbodiedMemoryEvent) -> EntityExtractionReport:
        if observation.event_type != "observation":
            raise ValueError("entity extraction requires an observation event")
        if observation.runtime_mode != self._runtime_mode:
            raise ValueError("observation runtime_mode does not match entity extraction pipeline")
        batch = self.extractor.extract(observation)
        existing_keys = self._existing_extraction_keys(observation.mission_id)
        ingested: list[EntityIngestResult] = []
        skipped: list[str] = []
        issues = list(batch.issues)
        for mention in batch.mentions:
            key = mention.extraction_key
            if key is None:
                issues.append(EntityExtractionIssue(None, "extractor did not provide extraction_key"))
                continue
            if key in existing_keys:
                skipped.append(key)
                continue
            try:
                result = self._entity_memory.record_mention(
                    mission_id=observation.mission_id,
                    mention=mention,
                    extractor_method_id=self.extractor.method_id,
                    robot_id=observation.robot_id,
                    subtask_id=observation.subtask_id,
                )
            except (TypeError, ValueError) as exc:
                issues.append(EntityExtractionIssue(None, str(exc)))
                continue
            ingested.append(result)
            existing_keys.add(key)
        return EntityExtractionReport(
            observation_event_id=observation.event_id,
            mention_event_ids=tuple(result.mention_event_id for result in ingested),
            entity_ids=tuple(result.entity.entity_id for result in ingested),
            skipped_extraction_keys=tuple(skipped),
            issues=tuple(issues),
        )

    def _existing_extraction_keys(self, mission_id: str) -> set[str]:
        return {
            key
            for event in self._store.list_events(
                mission_id=mission_id,
                event_type="entity_mention",
            )
            if event.runtime_mode == self._runtime_mode
            and event.provenance is not None
            and event.provenance.method_id == self.extractor.method_id
            for key in (event.payload.get("extraction_key"),)
            if isinstance(key, str) and key
        }


def _extraction_key(
    *,
    method_id: str,
    observation_event_id: str,
    item_index: int,
    raw: dict[str, Any],
) -> str:
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    value = f"{method_id}\n{observation_event_id}\n{item_index}\n{canonical}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string when provided")
    return value.strip()
