"""Evidence-preserving Episode/Gist/Lesson consolidation for FireClaw."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fireclaw_core.memory.embodied_memory import (
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryEvent,
    EmbodiedMemoryProducer,
    EmbodiedMemoryStore,
)


@dataclass(frozen=True)
class ConsolidationConfig:
    max_gap_seconds: float = 300.0
    max_events_per_episode: int = 100
    min_events_per_episode: int = 2

    def __post_init__(self) -> None:
        if self.max_gap_seconds <= 0:
            raise ValueError("max_gap_seconds must be positive")
        if self.max_events_per_episode <= 0:
            raise ValueError("max_events_per_episode must be positive")
        if self.min_events_per_episode <= 0:
            raise ValueError("min_events_per_episode must be positive")


@dataclass(frozen=True)
class ConsolidationResult:
    episode_event_ids: tuple[str, ...]
    gist_event_ids: tuple[str, ...]
    source_event_count: int


class FireClawConsolidationEngine:
    """Creates derived nodes without modifying or suppressing raw evidence."""

    METHOD_ID = "fireclaw.memory.deterministic_consolidation:v1"

    def __init__(
        self,
        *,
        store: EmbodiedMemoryStore,
        producer: EmbodiedMemoryProducer,
        config: ConsolidationConfig | None = None,
    ) -> None:
        if producer.producer_type != "memory_consolidator":
            raise ValueError("consolidation requires a memory_consolidator producer")
        self._store = store
        self._producer = producer
        self.config = config or ConsolidationConfig()

    def consolidate_mission(
        self,
        *,
        mission_id: str,
        runtime_mode: str,
        subtask_id: str | None = None,
    ) -> ConsolidationResult:
        _validate_mode(runtime_mode)
        events = [
            event
            for event in self._store.list_events(mission_id=mission_id)
            if event.runtime_mode == runtime_mode
            and (subtask_id is None or event.subtask_id == subtask_id)
            and event.event_type not in {"episode", "gist", "lesson"}
        ]
        grouped: dict[tuple[str | None, str | None], list[EmbodiedMemoryEvent]] = defaultdict(list)
        for event in events:
            grouped[(event.robot_id, event.subtask_id)].append(event)
        chunks = [
            chunk
            for group_events in grouped.values()
            for chunk in self._temporal_chunks(group_events)
        ]
        existing_gists = {
            tuple(event.payload.get("source_event_ids") or ()): event
            for event in self._store.list_events(mission_id=mission_id, event_type="gist")
            if event.runtime_mode == runtime_mode
        }
        episode_ids: list[str] = []
        gist_ids: list[str] = []
        for chunk in chunks:
            if len(chunk) < self.config.min_events_per_episode:
                continue
            source_ids = tuple(event.event_id for event in chunk)
            existing_gist = existing_gists.get(source_ids)
            if existing_gist is not None:
                episode_id = existing_gist.payload.get("episode_event_id")
                if isinstance(episode_id, str) and episode_id:
                    episode_ids.append(episode_id)
                gist_ids.append(existing_gist.event_id)
                continue
            episode, gist = self._consolidate_chunk(chunk)
            episode_ids.append(episode.event_id)
            gist_ids.append(gist.event_id)
        return ConsolidationResult(
            episode_event_ids=tuple(episode_ids),
            gist_event_ids=tuple(gist_ids),
            source_event_count=sum(len(chunk) for chunk in chunks),
        )

    def derive_lesson(
        self,
        *,
        mission_id: str,
        runtime_mode: str,
        gist_event_ids: tuple[str, ...],
        lesson: str,
        confidence: float,
        method_id: str,
    ) -> EmbodiedMemoryEvent:
        _validate_mode(runtime_mode)
        if len(set(gist_event_ids)) < 2:
            raise ValueError("a lesson requires at least two distinct gist events")
        if not lesson.strip():
            raise ValueError("lesson must not be empty")
        events = {event.event_id: event for event in self._store.list_events(mission_id=mission_id)}
        gists: list[EmbodiedMemoryEvent] = []
        for event_id in gist_event_ids:
            event = events.get(event_id)
            if event is None or event.event_type != "gist":
                raise ValueError("lesson sources must be existing gist events")
            if event.runtime_mode != runtime_mode:
                raise ValueError("lesson sources cannot cross runtime modes")
            gists.append(event)
        lesson_event = self._producer.record_event(
            mission_id=mission_id,
            event_type="lesson",
            evidence_kind="derived_summary",
            payload={
                "lesson": lesson.strip(),
                "advisory_only": True,
                "requires_runtime_revalidation": True,
                "source_gist_count": len(gists),
            },
            runtime_mode=runtime_mode,
            source_type="memory_consolidation",
            method_id=method_id,
            confidence=confidence,
            sensitivity=(
                "restricted" if any(gist.sensitivity == "restricted" for gist in gists)
                else "standard"
            ),
            derived_from=tuple(gist_event_ids),
        )
        for gist in gists:
            self._producer.add_relation(
                mission_id=mission_id,
                source_record_id=lesson_event.event_id,
                target_record_id=gist.event_id,
                relation_type="summarizes",
                runtime_mode=runtime_mode,
                metadata={"advisory_only": True},
            )
        return lesson_event

    def _consolidate_chunk(
        self,
        events: list[EmbodiedMemoryEvent],
    ) -> tuple[EmbodiedMemoryEvent, EmbodiedMemoryEvent]:
        first = events[0]
        source_ids = tuple(event.event_id for event in events)
        sensitivity = (
            "restricted" if any(event.sensitivity == "restricted" for event in events)
            else "standard"
        )
        confidence = _derived_confidence(events)
        type_counts = Counter(event.event_type for event in events)
        safety_records = [
            {
                "event_id": event.event_id,
                "decision": event.payload.get("decision"),
                "reason_count": len(event.payload.get("reasons") or []),
            }
            for event in events
            if event.event_type == "safety_decision"
        ]
        correction_ids = [event.event_id for event in events if event.event_type == "correction"]
        episode = self._producer.record_event(
            mission_id=first.mission_id,
            event_type="episode",
            evidence_kind="derived_summary",
            payload={
                "status": "completed",
                "start_at": events[0].observed_at,
                "end_at": events[-1].observed_at,
                "event_count": len(events),
                "event_type_counts": dict(sorted(type_counts.items())),
                "robot_ids": sorted({event.robot_id for event in events if event.robot_id}),
                "subtask_ids": sorted({event.subtask_id for event in events if event.subtask_id}),
            },
            runtime_mode=first.runtime_mode,
            source_type="memory_consolidation",
            method_id=self.METHOD_ID,
            confidence=confidence,
            sensitivity=sensitivity,
            derived_from=source_ids,
            observed_at=events[-1].observed_at,
        )
        for event in events:
            self._producer.add_relation(
                mission_id=first.mission_id,
                source_record_id=event.event_id,
                target_record_id=episode.event_id,
                relation_type="belongs_to",
                runtime_mode=first.runtime_mode,
            )
        gist = self._producer.record_event(
            mission_id=first.mission_id,
            event_type="gist",
            evidence_kind="derived_summary",
            payload={
                "summary": _deterministic_summary(type_counts, safety_records, correction_ids),
                "episode_event_id": episode.event_id,
                "source_event_count": len(events),
                "source_event_ids": list(source_ids),
                "safety_decisions": safety_records,
                "operator_correction_event_ids": correction_ids,
                "contradictory_safety_decisions": len({
                    record["decision"] for record in safety_records if record["decision"] is not None
                }) > 1,
                "raw_evidence_retained": True,
            },
            runtime_mode=first.runtime_mode,
            source_type="memory_consolidation",
            method_id=self.METHOD_ID,
            confidence=confidence,
            sensitivity=sensitivity,
            derived_from=source_ids,
            episode_id=episode.event_id,
            observed_at=events[-1].observed_at,
        )
        for event in events:
            self._producer.add_relation(
                mission_id=first.mission_id,
                source_record_id=gist.event_id,
                target_record_id=event.event_id,
                relation_type="summarizes",
                runtime_mode=first.runtime_mode,
            )
        return episode, gist

    def _temporal_chunks(
        self,
        events: list[EmbodiedMemoryEvent],
    ) -> list[list[EmbodiedMemoryEvent]]:
        ordered = sorted(events, key=lambda event: (event.observed_at, event.created_at, event.event_id))
        chunks: list[list[EmbodiedMemoryEvent]] = []
        for event in ordered:
            if not chunks:
                chunks.append([event])
                continue
            gap = (
                datetime.fromisoformat(event.observed_at)
                - datetime.fromisoformat(chunks[-1][-1].observed_at)
            ).total_seconds()
            if gap > self.config.max_gap_seconds or len(chunks[-1]) >= self.config.max_events_per_episode:
                chunks.append([event])
            else:
                chunks[-1].append(event)
        return chunks


def _derived_confidence(events: list[EmbodiedMemoryEvent]) -> float:
    asserted = [event.confidence for event in events if event.confidence is not None]
    return min(asserted) if asserted else 1.0


def _deterministic_summary(
    type_counts: Counter[str],
    safety_records: list[dict[str, Any]],
    correction_ids: list[str],
) -> str:
    counts = ", ".join(f"{name}={count}" for name, count in sorted(type_counts.items()))
    decisions = ", ".join(
        str(record["decision"]) for record in safety_records if record["decision"] is not None
    ) or "none"
    return (
        f"Recorded events: {counts}. Safety decisions: {decisions}. "
        f"Operator corrections retained: {len(correction_ids)}."
    )


def _validate_mode(runtime_mode: str) -> None:
    if runtime_mode not in MEMORY_RUNTIME_MODES:
        raise ValueError(
            f"Invalid runtime_mode: {runtime_mode}. "
            f"Must be one of: {sorted(MEMORY_RUNTIME_MODES)}"
        )
