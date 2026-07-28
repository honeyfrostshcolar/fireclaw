from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping


BELIEF_STATUSES = frozenset({"confirmed", "uncertain", "conflicted", "stale"})


@dataclass(frozen=True)
class WorldStateObservation:
    """One auditable observation before cross-source belief fusion."""

    observation_id: str
    subject_id: str
    kind: str
    value: str | int | float | bool | None
    source: str
    observed_at: str
    evidence_ids: tuple[str, ...]
    confidence: float | None = None
    expires_at: str | None = None


@dataclass(frozen=True)
class WorldStateBeliefCandidate:
    value: str | int | float | bool | None
    confidence: float
    observation_ids: tuple[str, ...]
    sources: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "confidence": self.confidence,
            "observation_ids": list(self.observation_ids),
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class WorldStateBelief:
    """Deterministic planner-facing projection of related observations."""

    belief_id: str
    subject_id: str
    kind: str
    status: str
    value: str | int | float | bool | None
    confidence: float
    observed_at: str
    reason_code: str
    supporting_observation_ids: tuple[str, ...]
    conflicting_observation_ids: tuple[str, ...]
    stale_observation_ids: tuple[str, ...]
    superseded_observation_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    candidates: tuple[WorldStateBeliefCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "belief_id": self.belief_id,
            "subject_id": self.subject_id,
            "kind": self.kind,
            "status": self.status,
            "value": self.value,
            "confidence": self.confidence,
            "observed_at": self.observed_at,
            "reason_code": self.reason_code,
            "supporting_observation_ids": list(
                self.supporting_observation_ids
            ),
            "conflicting_observation_ids": list(
                self.conflicting_observation_ids
            ),
            "stale_observation_ids": list(self.stale_observation_ids),
            "superseded_observation_ids": list(
                self.superseded_observation_ids
            ),
            "evidence_ids": list(self.evidence_ids),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


class WorldStateBeliefBuilder:
    """Fuse bounded observations without delegating evidence authority to an LLM."""

    def __init__(
        self,
        *,
        confirmation_threshold: float = 0.8,
        conflict_threshold: float = 0.5,
        default_confidence: float = 0.5,
        default_max_age_seconds: float | None = 30.0,
        future_clock_skew_seconds: float = 5.0,
        source_reliability: Mapping[str, float] | None = None,
        max_age_seconds_by_kind: Mapping[str, float | None] | None = None,
    ) -> None:
        self.confirmation_threshold = _probability(
            "confirmation_threshold",
            confirmation_threshold,
        )
        self.conflict_threshold = _probability(
            "conflict_threshold",
            conflict_threshold,
        )
        self.default_confidence = _probability(
            "default_confidence",
            default_confidence,
        )
        if self.conflict_threshold > self.confirmation_threshold:
            raise ValueError(
                "conflict_threshold must not exceed confirmation_threshold"
            )
        self.default_max_age_seconds = _optional_positive_number(
            "default_max_age_seconds",
            default_max_age_seconds,
        )
        self.future_clock_skew_seconds = _non_negative_number(
            "future_clock_skew_seconds",
            future_clock_skew_seconds,
        )
        self.source_reliability = {
            source: _probability(f"source_reliability[{source!r}]", reliability)
            for source, reliability in (source_reliability or {}).items()
        }
        self.max_age_seconds_by_kind = {
            kind: _optional_positive_number(
                f"max_age_seconds_by_kind[{kind!r}]",
                max_age,
            )
            for kind, max_age in (max_age_seconds_by_kind or {}).items()
        }

    def build(
        self,
        observations: tuple[WorldStateObservation, ...],
        *,
        captured_at: str,
    ) -> tuple[WorldStateBelief, ...]:
        reference = _parse_timestamp(captured_at)
        if reference is None:
            raise ValueError("captured_at must be a timezone-aware ISO-8601 timestamp")
        grouped: dict[tuple[str, str], list[WorldStateObservation]] = {}
        for observation in observations:
            grouped.setdefault(
                (observation.subject_id, observation.kind),
                [],
            ).append(observation)
        return tuple(
            self._build_group(
                subject_id=subject_id,
                kind=kind,
                observations=tuple(items),
                reference=reference,
            )
            for (subject_id, kind), items in sorted(grouped.items())
        )

    def _build_group(
        self,
        *,
        subject_id: str,
        kind: str,
        observations: tuple[WorldStateObservation, ...],
        reference: datetime,
    ) -> WorldStateBelief:
        latest_by_source: dict[str, WorldStateObservation] = {}
        superseded: list[WorldStateObservation] = []
        for observation in sorted(
            observations,
            key=lambda item: (
                _parse_timestamp(item.observed_at) or datetime.min.replace(
                    tzinfo=timezone.utc
                ),
                item.observation_id,
            ),
        ):
            previous = latest_by_source.get(observation.source)
            if previous is not None:
                superseded.append(previous)
            latest_by_source[observation.source] = observation

        active: list[WorldStateObservation] = []
        stale: list[WorldStateObservation] = []
        for observation in latest_by_source.values():
            if self._is_stale(observation, kind=kind, reference=reference):
                stale.append(observation)
            else:
                active.append(observation)
        candidates = self._candidates(tuple(active))
        evidence_ids = tuple(
            dict.fromkeys(
                evidence_id
                for observation in observations
                for evidence_id in observation.evidence_ids
            )
        )
        latest_observed_at = max(
            observations,
            key=lambda item: (
                _parse_timestamp(item.observed_at)
                or datetime.min.replace(tzinfo=timezone.utc),
                item.observation_id,
            ),
        ).observed_at
        belief_id = _belief_id(subject_id, kind)

        if not candidates:
            return WorldStateBelief(
                belief_id=belief_id,
                subject_id=subject_id,
                kind=kind,
                status="stale",
                value=None,
                confidence=0.0,
                observed_at=latest_observed_at,
                reason_code="no_fresh_observation",
                supporting_observation_ids=(),
                conflicting_observation_ids=(),
                stale_observation_ids=_observation_ids(stale),
                superseded_observation_ids=_observation_ids(superseded),
                evidence_ids=evidence_ids,
                candidates=(),
            )

        winner = candidates[0]
        winner_observation_ids = set(winner.observation_ids)
        winner_observations = [
            observation
            for observation in active
            if observation.observation_id in winner_observation_ids
        ]
        winner_observed_at = max(
            winner_observations,
            key=lambda item: (
                _parse_timestamp(item.observed_at)
                or datetime.min.replace(tzinfo=timezone.utc),
                item.observation_id,
            ),
        ).observed_at
        credible = tuple(
            candidate
            for candidate in candidates
            if candidate.confidence >= self.conflict_threshold
        )
        if len(credible) > 1:
            status = "conflicted"
            value = None
            reason_code = "credible_sources_disagree"
        elif winner.confidence >= self.confirmation_threshold:
            status = "confirmed"
            value = winner.value
            reason_code = "confirmation_threshold_met"
        else:
            status = "uncertain"
            value = winner.value
            reason_code = "confirmation_threshold_not_met"

        conflicting_ids = tuple(
            observation_id
            for candidate in candidates[1:]
            for observation_id in candidate.observation_ids
        )
        return WorldStateBelief(
            belief_id=belief_id,
            subject_id=subject_id,
            kind=kind,
            status=status,
            value=value,
            confidence=winner.confidence,
            observed_at=winner_observed_at,
            reason_code=reason_code,
            supporting_observation_ids=winner.observation_ids,
            conflicting_observation_ids=conflicting_ids,
            stale_observation_ids=_observation_ids(stale),
            superseded_observation_ids=_observation_ids(superseded),
            evidence_ids=evidence_ids,
            candidates=candidates,
        )

    def _candidates(
        self,
        observations: tuple[WorldStateObservation, ...],
    ) -> tuple[WorldStateBeliefCandidate, ...]:
        grouped: dict[
            tuple[str, str],
            list[WorldStateObservation],
        ] = {}
        for observation in observations:
            grouped.setdefault(_value_key(observation.value), []).append(
                observation
            )
        candidates = [
            WorldStateBeliefCandidate(
                value=items[0].value,
                confidence=_combined_confidence(
                    [
                        self._observation_strength(observation)
                        for observation in items
                    ]
                ),
                observation_ids=_observation_ids(items),
                sources=tuple(sorted({item.source for item in items})),
            )
            for items in grouped.values()
        ]
        candidates.sort(
            key=lambda candidate: (
                -candidate.confidence,
                _value_key(candidate.value),
            )
        )
        return tuple(candidates)

    def _observation_strength(self, observation: WorldStateObservation) -> float:
        confidence = (
            self.default_confidence
            if observation.confidence is None
            else min(max(float(observation.confidence), 0.0), 1.0)
        )
        reliability = self.source_reliability.get(observation.source, 1.0)
        return confidence * reliability

    def _is_stale(
        self,
        observation: WorldStateObservation,
        *,
        kind: str,
        reference: datetime,
    ) -> bool:
        observed = _parse_timestamp(observation.observed_at)
        if observed is None:
            return True
        age_seconds = (reference - observed).total_seconds()
        if age_seconds < -self.future_clock_skew_seconds:
            return True
        expires = _parse_timestamp(observation.expires_at)
        if observation.expires_at is not None and expires is None:
            return True
        if expires is not None and expires <= reference:
            return True
        max_age = self.max_age_seconds_by_kind.get(
            kind,
            self.default_max_age_seconds,
        )
        return max_age is not None and age_seconds > max_age


def _combined_confidence(strengths: list[float]) -> float:
    remaining_uncertainty = 1.0
    for strength in strengths:
        remaining_uncertainty *= 1.0 - strength
    return round(1.0 - remaining_uncertainty, 6)


def _belief_id(subject_id: str, kind: str) -> str:
    digest = sha256(f"{subject_id}\0{kind}".encode("utf-8")).hexdigest()[:16]
    return f"belief:{digest}"


def _observation_ids(
    observations: list[WorldStateObservation]
    | tuple[WorldStateObservation, ...],
) -> tuple[str, ...]:
    return tuple(sorted({item.observation_id for item in observations}))


def _value_key(value: str | int | float | bool | None) -> tuple[str, str]:
    return type(value).__name__, repr(value)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _probability(name: str, value: float) -> float:
    number = float(value)
    if not isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return number


def _optional_positive_number(name: str, value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be positive when provided")
    return number


def _non_negative_number(name: str, value: float) -> float:
    number = float(value)
    if not isfinite(number) or number < 0:
        raise ValueError(f"{name} must be non-negative")
    return number
