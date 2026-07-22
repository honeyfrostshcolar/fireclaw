"""Evidence-preserving Episode/Gist/Lesson consolidation for FireClaw."""
from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fireclaw_core.memory.consolidation_jobs import (
    ConsolidationJob,
    ConsolidationJobStore,
)
from fireclaw_core.memory.embodied_memory import (
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryEvent,
    EmbodiedMemoryProducer,
    EmbodiedMemoryStore,
    SpatialMemoryContext,
)
from fireclaw_core.memory.safety_consolidation import (
    DeterministicGistSummarizer,
    GistSummarizer,
    assess_cross_robot_claims,
    build_evidence_layers,
    build_spatial_geometries,
    gist_pose_from_geometries,
)


@dataclass(frozen=True)
class ConsolidationConfig:
    max_gap_seconds: float = 300.0
    max_events_per_episode: int = 100
    min_events_per_episode: int = 2
    spatial_cluster_margin_m: float = 3.0
    cross_robot_window_seconds: float = 60.0
    cross_robot_spatial_margin_m: float = 2.0
    max_cross_robot_assessments: int = 50

    def __post_init__(self) -> None:
        if self.max_gap_seconds <= 0:
            raise ValueError("max_gap_seconds must be positive")
        if self.max_events_per_episode <= 0:
            raise ValueError("max_events_per_episode must be positive")
        if self.min_events_per_episode <= 0:
            raise ValueError("min_events_per_episode must be positive")
        if self.spatial_cluster_margin_m < 0:
            raise ValueError("spatial_cluster_margin_m must be >= 0")
        if self.cross_robot_window_seconds < 0:
            raise ValueError("cross_robot_window_seconds must be >= 0")
        if self.cross_robot_spatial_margin_m < 0:
            raise ValueError("cross_robot_spatial_margin_m must be >= 0")
        if self.max_cross_robot_assessments <= 0:
            raise ValueError("max_cross_robot_assessments must be positive")


@dataclass(frozen=True)
class ConsolidationResult:
    episode_event_ids: tuple[str, ...]
    gist_event_ids: tuple[str, ...]
    source_event_count: int
    job_ids: tuple[str, ...] = ()


class FireClawConsolidationEngine:
    """Creates derived nodes without modifying or suppressing raw evidence."""

    METHOD_ID = "fireclaw.memory.deterministic_consolidation:v2"
    JOB_PROTOCOL_ID = "fireclaw.memory.reliable_consolidation_job:v2"

    def __init__(
        self,
        *,
        store: EmbodiedMemoryStore,
        producer: EmbodiedMemoryProducer,
        config: ConsolidationConfig | None = None,
        job_store: ConsolidationJobStore | None = None,
        summarizer: GistSummarizer | None = None,
    ) -> None:
        if producer.producer_type != "memory_consolidator":
            raise ValueError("consolidation requires a memory_consolidator producer")
        self._store = store
        self._producer = producer
        self.config = config or ConsolidationConfig()
        self._summarizer = summarizer or DeterministicGistSummarizer()
        if not self._summarizer.method_id:
            raise ValueError("consolidation summarizer requires a non-empty method_id")
        self._profile_id = _stable_id(
            "consolidation-profile",
            {
                "method": self.METHOD_ID,
                "summarizer_method": self._summarizer.method_id,
                "spatial_cluster_margin_m": self.config.spatial_cluster_margin_m,
                "cross_robot_window_seconds": self.config.cross_robot_window_seconds,
                "cross_robot_spatial_margin_m": self.config.cross_robot_spatial_margin_m,
                "max_cross_robot_assessments": self.config.max_cross_robot_assessments,
            },
        )
        self._job_store = job_store or ConsolidationJobStore(
            _default_job_path(store.evidence_store.path)
        )
        self._consolidation_lock = threading.RLock()

    def get_job(self, job_id: str) -> ConsolidationJob | None:
        return self._job_store.get(job_id)

    def list_jobs(
        self,
        *,
        mission_id: str | None = None,
        status: str | None = None,
    ) -> list[ConsolidationJob]:
        return self._job_store.list_jobs(mission_id=mission_id, status=status)

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
        existing_events = self._store.list_events(mission_id=mission_id)
        existing_gists: dict[
            tuple[tuple[str, ...], tuple[str, ...], str | None, str | None],
            list[EmbodiedMemoryEvent],
        ] = defaultdict(list)
        existing_episodes: dict[tuple[str, ...], list[EmbodiedMemoryEvent]] = defaultdict(list)
        for event in existing_events:
            if event.runtime_mode != runtime_mode:
                continue
            if event.event_type == "gist":
                source_ids = tuple(event.payload.get("source_event_ids") or ())
                method_id = (
                    event.provenance.method_id
                    if event.provenance is not None
                    else None
                )
                summary_provenance = event.payload.get("summary_provenance")
                profile_id = (
                    summary_provenance.get("consolidation_profile_id")
                    if isinstance(summary_provenance, dict)
                    else None
                )
                supporting_ids = tuple(event.payload.get("supporting_event_ids") or ())
                existing_gists[
                    (source_ids, supporting_ids, method_id, profile_id)
                ].append(event)
            elif event.event_type == "episode":
                existing_episodes[event.derived_from].append(event)
        episode_ids: list[str] = []
        gist_ids: list[str] = []
        job_ids: list[str] = []
        for chunk in chunks:
            if len(chunk) < self.config.min_events_per_episode:
                continue
            source_ids = tuple(event.event_id for event in chunk)
            cross_robot_assessment = assess_cross_robot_claims(
                chunk,
                events,
                window_seconds=self.config.cross_robot_window_seconds,
                spatial_margin_m=self.config.cross_robot_spatial_margin_m,
                max_items=self.config.max_cross_robot_assessments,
            )
            supporting_event_ids = tuple(sorted(
                set(cross_robot_assessment.get("compared_event_ids") or ())
                - set(source_ids)
            ))
            matching_gists = existing_gists.get(
                (
                    source_ids,
                    supporting_event_ids,
                    self._summarizer.method_id,
                    self._profile_id,
                ),
                [],
            )
            matching_episodes = existing_episodes.get(source_ids, [])
            if len(matching_gists) > 1:
                raise ValueError(
                    "multiple gists already exist for the same consolidation sources"
                )
            if len(matching_episodes) > 1:
                raise ValueError(
                    "multiple episodes already exist for the same consolidation sources"
                )
            existing_gist = matching_gists[0] if matching_gists else None
            existing_episode = matching_episodes[0] if matching_episodes else None
            episode, gist, job = self._consolidate_chunk(
                chunk,
                mission_events=events,
                cross_robot_assessment=cross_robot_assessment,
                supporting_event_ids=supporting_event_ids,
                existing_episode=existing_episode,
                existing_gist=existing_gist,
            )
            episode_ids.append(episode.event_id)
            gist_ids.append(gist.event_id)
            job_ids.append(job.job_id)
        return ConsolidationResult(
            episode_event_ids=tuple(episode_ids),
            gist_event_ids=tuple(gist_ids),
            source_event_count=sum(len(chunk) for chunk in chunks),
            job_ids=tuple(job_ids),
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
        *,
        mission_events: list[EmbodiedMemoryEvent] | None = None,
        cross_robot_assessment: dict[str, Any] | None = None,
        supporting_event_ids: tuple[str, ...] = (),
        existing_episode: EmbodiedMemoryEvent | None = None,
        existing_gist: EmbodiedMemoryEvent | None = None,
    ) -> tuple[EmbodiedMemoryEvent, EmbodiedMemoryEvent, ConsolidationJob]:
        with self._consolidation_lock:
            return self._consolidate_chunk_locked(
                events,
                mission_events=mission_events,
                cross_robot_assessment=cross_robot_assessment,
                supporting_event_ids=supporting_event_ids,
                existing_episode=existing_episode,
                existing_gist=existing_gist,
            )

    def _consolidate_chunk_locked(
        self,
        events: list[EmbodiedMemoryEvent],
        *,
        mission_events: list[EmbodiedMemoryEvent] | None = None,
        cross_robot_assessment: dict[str, Any] | None = None,
        supporting_event_ids: tuple[str, ...] = (),
        existing_episode: EmbodiedMemoryEvent | None = None,
        existing_gist: EmbodiedMemoryEvent | None = None,
    ) -> tuple[EmbodiedMemoryEvent, EmbodiedMemoryEvent, ConsolidationJob]:
        first = events[0]
        source_ids = tuple(event.event_id for event in events)
        job_id = _stable_id(
            "consolidation-job",
            {
                "protocol": self.JOB_PROTOCOL_ID,
                "consolidation_profile_id": self._profile_id,
                "mission_id": first.mission_id,
                "runtime_mode": first.runtime_mode,
                "source_event_ids": source_ids,
                "supporting_event_ids": supporting_event_ids,
            },
        )
        legacy_episode_id = (
            existing_gist.payload.get("episode_event_id")
            if existing_gist is not None
            else None
        )
        if legacy_episode_id is not None and not isinstance(legacy_episode_id, str):
            raise ValueError("existing gist has an invalid episode_event_id")
        if (
            existing_episode is not None
            and legacy_episode_id
            and existing_episode.event_id != legacy_episode_id
        ):
            raise ValueError("existing episode and gist disagree for the same source events")
        episode_event_id = (
            existing_episode.event_id
            if existing_episode is not None
            else legacy_episode_id
            or _stable_id(
                "episode",
                {
                    "method": self.METHOD_ID,
                    "mission_id": first.mission_id,
                    "runtime_mode": first.runtime_mode,
                    "source_event_ids": source_ids,
                },
            )
        )
        gist_event_id = (
            existing_gist.event_id
            if existing_gist is not None
            else _stable_id("gist", {"job_id": job_id})
        )
        job = self._job_store.ensure_job(
            job_id=job_id,
            mission_id=first.mission_id,
            runtime_mode=first.runtime_mode,
            source_event_ids=source_ids,
            supporting_event_ids=supporting_event_ids,
            episode_event_id=episode_event_id,
            gist_event_id=gist_event_id,
        )
        if job.status != "completed":
            job = self._job_store.transition(job.job_id, status="running")

        episode_sensitivity = (
            "restricted" if any(event.sensitivity == "restricted" for event in events)
            else "standard"
        )
        episode_confidence = _derived_confidence(events)
        mission_events_by_id = {
            event.event_id: event for event in (mission_events or events)
        }
        missing_supporting_ids = [
            event_id for event_id in supporting_event_ids
            if event_id not in mission_events_by_id
        ]
        if missing_supporting_ids:
            exc = ValueError("cross-robot supporting events are missing from the mission store")
            self._mark_job_failed(job, exc)
            raise exc
        supporting_events = [
            mission_events_by_id[event_id] for event_id in supporting_event_ids
        ]
        gist_evidence = [*events, *supporting_events]
        gist_derived_from = tuple(event.event_id for event in gist_evidence)
        gist_sensitivity = (
            "restricted"
            if any(event.sensitivity == "restricted" for event in gist_evidence)
            else "standard"
        )
        gist_confidence = _derived_confidence(gist_evidence)
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
        try:
            enrichment = self._build_safety_enrichment(
                events=events,
                mission_events=mission_events or events,
                type_counts=type_counts,
                safety_records=safety_records,
                correction_ids=correction_ids,
                cross_robot_assessment=cross_robot_assessment,
            )
        except Exception as exc:
            self._mark_job_failed(job, exc)
            raise
        evidence_layers = enrichment["evidence_layers"]
        spatial_geometries = enrichment["spatial_geometries"]
        cross_robot_assessment = enrichment["cross_robot_assessment"]
        summary = enrichment["summary"]
        episode_payload = {
            "status": "completed",
            "start_at": events[0].observed_at,
            "end_at": events[-1].observed_at,
            "event_count": len(events),
            "event_type_counts": dict(sorted(type_counts.items())),
            "robot_ids": sorted({event.robot_id for event in events if event.robot_id}),
            "subtask_ids": sorted({event.subtask_id for event in events if event.subtask_id}),
            "positioned_event_count": sum(1 for event in events if event.pose is not None),
            "spatial_geometry_count": len(spatial_geometries),
        }
        gist_payload = {
            "summary": summary,
            "summary_provenance": {
                "method_id": self._summarizer.method_id,
                "consolidation_profile_id": self._profile_id,
                "evidence_kind": "derived_summary",
                "generated_from_structured_context": True,
                "not_sensor_fact": True,
            },
            "episode_event_id": job.episode_event_id,
            "source_event_count": len(events),
            "source_event_ids": list(source_ids),
            "supporting_event_count": len(supporting_event_ids),
            "supporting_event_ids": list(supporting_event_ids),
            "all_evidence_event_ids": list(gist_derived_from),
            "evidence_start_at": min(
                gist_evidence,
                key=lambda event: datetime.fromisoformat(
                    event.observed_at.replace("Z", "+00:00")
                ),
            ).observed_at,
            "evidence_end_at": max(
                gist_evidence,
                key=lambda event: datetime.fromisoformat(
                    event.observed_at.replace("Z", "+00:00")
                ),
            ).observed_at,
            "safety_decisions": safety_records,
            "operator_correction_event_ids": correction_ids,
            "contradictory_safety_decisions": len({
                record["decision"] for record in safety_records if record["decision"] is not None
            }) > 1,
            "raw_evidence_retained": True,
            "evidence_layers": evidence_layers,
            "spatial_geometries": spatial_geometries,
            "unpositioned_source_event_count": sum(
                1 for event in events if event.pose is None
            ),
            "cross_robot_assessment": cross_robot_assessment,
            "advisory_only": True,
            "requires_current_state_revalidation": True,
            "consolidation_job_id": job.job_id,
        }
        gist_pose = gist_pose_from_geometries(spatial_geometries)

        try:
            episode = self._ensure_derived_event(
                event_id=job.episode_event_id,
                mission_id=first.mission_id,
                event_type="episode",
                payload=episode_payload,
                runtime_mode=first.runtime_mode,
                confidence=episode_confidence,
                sensitivity=episode_sensitivity,
                derived_from=source_ids,
                observed_at=events[-1].observed_at,
                method_id=self.METHOD_ID,
                robot_id=first.robot_id,
                subtask_id=first.subtask_id,
            )
            self._checkpoint(job, "episode")

            relations = {
                (
                    relation.source_record_id,
                    relation.target_record_id,
                    relation.relation_type,
                    relation.runtime_mode,
                )
                for relation in self._store.list_relations(mission_id=first.mission_id)
            }
            for event in events:
                self._ensure_relation(
                    job=job,
                    source_record_id=event.event_id,
                    target_record_id=episode.event_id,
                    relation_type="belongs_to",
                    relations=relations,
                )
                self._checkpoint(job, f"belongs_to:{event.event_id}")

            gist = self._ensure_derived_event(
                event_id=job.gist_event_id,
                mission_id=first.mission_id,
                event_type="gist",
                payload=gist_payload,
                runtime_mode=first.runtime_mode,
                confidence=gist_confidence,
                sensitivity=gist_sensitivity,
                derived_from=gist_derived_from,
                episode_id=episode.event_id,
                observed_at=max(
                    gist_evidence,
                    key=lambda event: datetime.fromisoformat(
                        event.observed_at.replace("Z", "+00:00")
                    ),
                ).observed_at,
                pose=gist_pose,
                method_id=self._summarizer.method_id,
                robot_id=first.robot_id,
                subtask_id=first.subtask_id,
            )
            self._checkpoint(job, "gist")

            for event in gist_evidence:
                self._ensure_relation(
                    job=job,
                    source_record_id=gist.event_id,
                    target_record_id=event.event_id,
                    relation_type="summarizes",
                    relations=relations,
                )
                self._checkpoint(job, f"summarizes:{event.event_id}")

            current = self._job_store.get(job.job_id)
            if current is not None and current.status == "completed":
                job = current
            else:
                job = self._job_store.transition(
                    job.job_id,
                    status="completed",
                    completed_step="completed",
                )
            return episode, gist, job
        except Exception as exc:
            self._mark_job_failed(job, exc)
            raise

    def _ensure_derived_event(
        self,
        *,
        event_id: str,
        mission_id: str,
        event_type: str,
        payload: dict[str, Any],
        runtime_mode: str,
        confidence: float,
        sensitivity: str,
        derived_from: tuple[str, ...],
        observed_at: str,
        episode_id: str | None = None,
        pose: SpatialMemoryContext | None = None,
        method_id: str,
        robot_id: str | None = None,
        subtask_id: str | None = None,
    ) -> EmbodiedMemoryEvent:
        existing = next(
            (event for event in self._store.list_events(mission_id=mission_id)
             if event.event_id == event_id),
            None,
        )
        if existing is not None:
            if (
                existing.event_type != event_type
                or existing.runtime_mode != runtime_mode
                or existing.derived_from != derived_from
                or existing.episode_id != episode_id
                or existing.confidence != confidence
                or existing.sensitivity != sensitivity
                or existing.pose != pose
                or existing.robot_id not in {None, robot_id}
                or existing.subtask_id not in {None, subtask_id}
            ):
                raise ValueError(f"consolidation artifact identity conflict: {event_id}")
            for key, expected_value in payload.items():
                if key == "consolidation_job_id":
                    continue
                if (
                    event_type == "episode"
                    and key in {"positioned_event_count", "spatial_geometry_count"}
                ):
                    continue
                if existing.payload.get(key) != expected_value:
                    raise ValueError(f"consolidation artifact payload conflict: {event_id}")
            if event_type == "gist":
                all_evidence_ids = tuple(
                    existing.payload.get("all_evidence_event_ids")
                    or existing.payload.get("source_event_ids")
                    or ()
                )
                if all_evidence_ids != derived_from:
                    raise ValueError(f"gist source identity conflict: {event_id}")
            return existing
        return self._producer.record_event(
            mission_id=mission_id,
            event_type=event_type,
            evidence_kind="derived_summary",
            payload=payload,
            runtime_mode=runtime_mode,
            source_type="memory_consolidation",
            method_id=method_id,
            confidence=confidence,
            sensitivity=sensitivity,
            derived_from=derived_from,
            episode_id=episode_id,
            pose=pose,
            robot_id=robot_id,
            subtask_id=subtask_id,
            observed_at=observed_at,
            event_id=event_id,
        )

    def _ensure_relation(
        self,
        *,
        job: ConsolidationJob,
        source_record_id: str,
        target_record_id: str,
        relation_type: str,
        relations: set[tuple[str, str, str, str]],
    ) -> None:
        key = (source_record_id, target_record_id, relation_type, job.runtime_mode)
        if key in relations:
            return
        self._producer.add_relation(
            mission_id=job.mission_id,
            source_record_id=source_record_id,
            target_record_id=target_record_id,
            relation_type=relation_type,
            runtime_mode=job.runtime_mode,
            relation_id=_stable_id(
                "relation",
                {
                    "job_id": job.job_id,
                    "source_record_id": source_record_id,
                    "target_record_id": target_record_id,
                    "relation_type": relation_type,
                },
            ),
        )
        relations.add(key)

    def _checkpoint(self, job: ConsolidationJob, step: str) -> None:
        current = self._job_store.get(job.job_id)
        if current is not None and current.status != "completed":
            self._job_store.transition(
                job.job_id,
                status="running",
                completed_step=step,
            )

    def _build_safety_enrichment(
        self,
        *,
        events: list[EmbodiedMemoryEvent],
        mission_events: list[EmbodiedMemoryEvent],
        type_counts: Counter[str],
        safety_records: list[dict[str, Any]],
        correction_ids: list[str],
        cross_robot_assessment: dict[str, Any] | None,
    ) -> dict[str, Any]:
        evidence_layers = build_evidence_layers(events)
        spatial_geometries = build_spatial_geometries(
            events,
            cluster_margin_m=self.config.spatial_cluster_margin_m,
        )
        if cross_robot_assessment is None:
            cross_robot_assessment = assess_cross_robot_claims(
                events,
                mission_events,
                window_seconds=self.config.cross_robot_window_seconds,
                spatial_margin_m=self.config.cross_robot_spatial_margin_m,
                max_items=self.config.max_cross_robot_assessments,
            )
        summary_context = {
            "event_type_counts": dict(sorted(type_counts.items())),
            "safety_decisions": safety_records,
            "operator_correction_event_ids": correction_ids,
            "evidence_layers": evidence_layers,
            "spatial_geometries": spatial_geometries,
            "cross_robot_assessment": cross_robot_assessment,
        }
        summary = self._summarizer.summarize(summary_context)
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("consolidation summarizer returned an empty summary")
        return {
            "summary": summary.strip(),
            "evidence_layers": evidence_layers,
            "spatial_geometries": spatial_geometries,
            "cross_robot_assessment": cross_robot_assessment,
        }

    def _mark_job_failed(self, job: ConsolidationJob, exc: Exception) -> None:
        current = self._job_store.get(job.job_id)
        if current is not None and current.status != "completed":
            self._job_store.transition(
                job.job_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}"[:1000],
            )

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
                datetime.fromisoformat(event.observed_at.replace("Z", "+00:00"))
                - datetime.fromisoformat(
                    chunks[-1][-1].observed_at.replace("Z", "+00:00")
                )
            ).total_seconds()
            if gap > self.config.max_gap_seconds or len(chunks[-1]) >= self.config.max_events_per_episode:
                chunks.append([event])
            else:
                chunks[-1].append(event)
        return chunks


def _derived_confidence(events: list[EmbodiedMemoryEvent]) -> float:
    asserted = [event.confidence for event in events if event.confidence is not None]
    return min(asserted) if asserted else 1.0


def _validate_mode(runtime_mode: str) -> None:
    if runtime_mode not in MEMORY_RUNTIME_MODES:
        raise ValueError(
            f"Invalid runtime_mode: {runtime_mode}. "
            f"Must be one of: {sorted(MEMORY_RUNTIME_MODES)}"
        )


def _stable_id(prefix: str, value: dict[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest}"


def _default_job_path(evidence_path: Path) -> Path:
    return evidence_path.with_name(f"{evidence_path.name}.consolidation-jobs.jsonl")
