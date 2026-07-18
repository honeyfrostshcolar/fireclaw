"""Evaluation metrics for FireClaw embodied retrieval and incident graphs."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from pathlib import Path
from statistics import mean
import time
from typing import Any

from fireclaw_core.memory.embodied_memory import (
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryEvent,
    EmbodiedMemoryStore,
)


@dataclass(frozen=True)
class EmbodiedRetrievalCase:
    query: str
    runtime_mode: str
    expected_record_ids: tuple[str, ...]
    forbidden_record_ids: tuple[str, ...] = ()
    mission_id: str | None = None
    record_type: str | None = None


@dataclass(frozen=True)
class EmbodiedRetrievalCaseResult:
    query: str
    returned_ids: tuple[str, ...]
    relevant_ids: tuple[str, ...]
    missing_ids: tuple[str, ...]
    forbidden_ids: tuple[str, ...]
    contamination_ids: tuple[str, ...]
    precision: float
    recall: float
    latency_ms: float


@dataclass(frozen=True)
class EmbodiedRetrievalReport:
    case_count: int
    mean_precision: float
    mean_recall: float
    false_retrieval_rate: float
    runtime_contamination_count: int
    mean_latency_ms: float
    p95_latency_ms: float
    results: tuple[EmbodiedRetrievalCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IncidentReconstructionReport:
    mission_id: str
    runtime_mode: str
    event_count: int
    required_event_type_coverage: float
    missing_event_types: tuple[str, ...]
    provenance_coverage: float
    relation_chain_coverage: float
    missing_relation_transitions: tuple[str, ...]
    runtime_contamination_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IndexScalingAssessment:
    recommend_hnsw: bool
    recommend_rtree: bool
    evidence_sufficient: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_INCIDENT_EVENT_TYPES = frozenset({
    "command",
    "plan",
    "subtask",
    "observation",
    "safety_decision",
    "skill_invocation",
    "outcome",
})

DEFAULT_RELATION_TRANSITIONS = (
    ("plan", "caused_by", frozenset({"command"})),
    ("subtask", "subtask_of", frozenset({"plan"})),
    ("observation", "supports", frozenset({"safety_decision"})),
    ("skill_invocation", "follows", frozenset({"safety_decision", "skill_invocation"})),
    ("outcome", "caused_by", frozenset({"plan", "subtask"})),
)


def evaluate_embodied_retrieval(
    store: EmbodiedMemoryStore,
    cases: list[EmbodiedRetrievalCase],
    *,
    limit: int = 10,
) -> EmbodiedRetrievalReport:
    results: list[EmbodiedRetrievalCaseResult] = []
    total_returned = 0
    total_false = 0
    for case in cases:
        _validate_mode(case.runtime_mode)
        filters: dict[str, Any] = {}
        if case.mission_id is not None:
            filters["mission_id"] = case.mission_id
        if case.record_type is not None:
            filters["record_type"] = case.record_type
        started = time.perf_counter()
        hits = store.search_text(
            case.query,
            runtime_mode=case.runtime_mode,
            filters=filters,
            limit=limit,
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        returned = tuple(str(hit.get("record_id") or "") for hit in hits)
        returned_set = set(returned)
        expected = set(case.expected_record_ids)
        relevant = tuple(record_id for record_id in returned if record_id in expected)
        missing = tuple(record_id for record_id in case.expected_record_ids if record_id not in returned_set)
        forbidden = tuple(record_id for record_id in case.forbidden_record_ids if record_id in returned_set)
        contamination = tuple(
            str(hit.get("record_id") or "")
            for hit in hits
            if hit.get("runtime_mode") != case.runtime_mode
        )
        false_count = len(returned_set - expected) if expected else len(forbidden)
        total_returned += len(returned)
        total_false += false_count
        results.append(EmbodiedRetrievalCaseResult(
            query=case.query,
            returned_ids=returned,
            relevant_ids=relevant,
            missing_ids=missing,
            forbidden_ids=forbidden,
            contamination_ids=contamination,
            precision=(len(relevant) / len(returned) if returned else (1.0 if not expected else 0.0)),
            recall=(len(set(relevant)) / len(expected) if expected else 1.0),
            latency_ms=latency_ms,
        ))
    latencies = [result.latency_ms for result in results]
    return EmbodiedRetrievalReport(
        case_count=len(results),
        mean_precision=mean([result.precision for result in results]) if results else 0.0,
        mean_recall=mean([result.recall for result in results]) if results else 0.0,
        false_retrieval_rate=(total_false / total_returned if total_returned else 0.0),
        runtime_contamination_count=sum(len(result.contamination_ids) for result in results),
        mean_latency_ms=mean(latencies) if latencies else 0.0,
        p95_latency_ms=_percentile(latencies, 0.95),
        results=tuple(results),
    )


def evaluate_incident_reconstruction(
    store: EmbodiedMemoryStore,
    *,
    mission_id: str,
    runtime_mode: str,
    required_event_types: frozenset[str] = DEFAULT_INCIDENT_EVENT_TYPES,
    relation_transitions: tuple[
        tuple[str, str, frozenset[str]], ...
    ] = DEFAULT_RELATION_TRANSITIONS,
) -> IncidentReconstructionReport:
    _validate_mode(runtime_mode)
    mission_events = store.list_events(mission_id=mission_id)
    events = [event for event in mission_events if event.runtime_mode == runtime_mode]
    event_by_id = {event.event_id: event for event in events}
    present_types = {event.event_type for event in events}
    missing_types = tuple(sorted(required_event_types - present_types))
    provenance_count = sum(event.provenance is not None for event in events)
    relations = [
        relation
        for relation in store.list_relations(mission_id=mission_id)
        if relation.runtime_mode == runtime_mode
    ]
    observed_transitions: set[tuple[str, str, str]] = set()
    for relation in relations:
        source = event_by_id.get(relation.source_record_id)
        target = event_by_id.get(relation.target_record_id)
        if source is not None and target is not None:
            observed_transitions.add((source.event_type, relation.relation_type, target.event_type))
    missing_transitions: list[str] = []
    for source_type, relation_type, target_types in relation_transitions:
        if not any(
            (source_type, relation_type, target_type) in observed_transitions
            for target_type in target_types
        ):
            missing_transitions.append(
                f"{source_type} --{relation_type}--> {'|'.join(sorted(target_types))}"
            )
    return IncidentReconstructionReport(
        mission_id=mission_id,
        runtime_mode=runtime_mode,
        event_count=len(events),
        required_event_type_coverage=(
            (len(required_event_types) - len(missing_types)) / len(required_event_types)
            if required_event_types else 1.0
        ),
        missing_event_types=missing_types,
        provenance_coverage=(provenance_count / len(events) if events else 0.0),
        relation_chain_coverage=(
            (len(relation_transitions) - len(missing_transitions)) / len(relation_transitions)
            if relation_transitions else 1.0
        ),
        missing_relation_transitions=tuple(missing_transitions),
        runtime_contamination_count=sum(
            event.runtime_mode != runtime_mode for event in mission_events
        ),
    )


def assess_index_scaling(
    *,
    event_count: int | None,
    embedding_record_count: int | None,
    spatial_record_count: int | None,
    embedding_p95_latency_ms: float | None,
    spatial_p95_latency_ms: float | None,
) -> IndexScalingAssessment:
    measurements = (
        event_count,
        embedding_record_count,
        spatial_record_count,
        embedding_p95_latency_ms,
        spatial_p95_latency_ms,
    )
    if any(value is None for value in measurements):
        return IndexScalingAssessment(
            recommend_hnsw=False,
            recommend_rtree=False,
            evidence_sufficient=False,
            reasons=("Index changes require complete volume and p95 latency measurements.",),
        )
    assert embedding_record_count is not None
    assert spatial_record_count is not None
    assert embedding_p95_latency_ms is not None
    assert spatial_p95_latency_ms is not None
    recommend_hnsw = embedding_record_count >= 10_000 and embedding_p95_latency_ms >= 100.0
    recommend_rtree = spatial_record_count >= 50_000 and spatial_p95_latency_ms >= 100.0
    reasons = (
        f"embedding_records={embedding_record_count}, embedding_p95_ms={embedding_p95_latency_ms:.3f}",
        f"spatial_records={spatial_record_count}, spatial_p95_ms={spatial_p95_latency_ms:.3f}",
    )
    return IndexScalingAssessment(
        recommend_hnsw=recommend_hnsw,
        recommend_rtree=recommend_rtree,
        evidence_sufficient=True,
        reasons=reasons,
    )


def artifact_sizes(*paths: str | Path | None) -> dict[str, int]:
    return {
        str(Path(path)): Path(path).stat().st_size
        for path in paths
        if path is not None and Path(path).exists()
    }


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


def _validate_mode(runtime_mode: str) -> None:
    if runtime_mode not in MEMORY_RUNTIME_MODES:
        raise ValueError(
            f"Invalid runtime_mode: {runtime_mode}. "
            f"Must be one of: {sorted(MEMORY_RUNTIME_MODES)}"
        )
