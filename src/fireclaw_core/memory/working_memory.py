"""Bounded, non-authoritative working-memory projection for live missions."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import threading
from typing import Iterable

from fireclaw_core.memory.embodied_memory import (
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryEvent,
    SpatialMemoryContext,
)


@dataclass(frozen=True)
class WorkingMemoryConfig:
    capacity: int = 100
    max_event_size_bytes: int = 65_536
    default_max_age_seconds: float = 120.0
    body_state_max_age_seconds: float = 15.0
    observation_max_age_seconds: float = 30.0
    safety_decision_max_age_seconds: float = 300.0
    skill_invocation_max_age_seconds: float = 300.0
    task_context_max_age_seconds: float = 600.0
    future_clock_skew_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("working-memory capacity must be positive")
        if self.max_event_size_bytes <= 0:
            raise ValueError("working-memory max_event_size_bytes must be positive")
        for field_name, value in (
            ("default_max_age_seconds", self.default_max_age_seconds),
            ("body_state_max_age_seconds", self.body_state_max_age_seconds),
            ("observation_max_age_seconds", self.observation_max_age_seconds),
            ("safety_decision_max_age_seconds", self.safety_decision_max_age_seconds),
            ("skill_invocation_max_age_seconds", self.skill_invocation_max_age_seconds),
            ("task_context_max_age_seconds", self.task_context_max_age_seconds),
            ("future_clock_skew_seconds", self.future_clock_skew_seconds),
        ):
            if value < 0:
                raise ValueError(f"working-memory {field_name} cannot be negative")

    def max_age_for(self, event_type: str) -> float:
        if event_type == "body_state":
            return self.body_state_max_age_seconds
        if event_type == "observation":
            return self.observation_max_age_seconds
        if event_type == "safety_decision":
            return self.safety_decision_max_age_seconds
        if event_type == "skill_invocation":
            return self.skill_invocation_max_age_seconds
        if event_type in {"command", "mission", "plan", "subtask"}:
            return self.task_context_max_age_seconds
        return self.default_max_age_seconds


@dataclass(frozen=True)
class WorkingMemorySnapshot:
    runtime_mode: str
    mission_id: str
    reference_at: str
    events: tuple[EmbodiedMemoryEvent, ...]
    stale_events: tuple[EmbodiedMemoryEvent, ...]
    latest_by_type: dict[str, EmbodiedMemoryEvent]
    current_pose: SpatialMemoryContext | None


class EmbodiedWorkingMemory:
    """Thread-safe write-through projection over already-persisted events."""

    def __init__(self, config: WorkingMemoryConfig | None = None) -> None:
        self.config = config or WorkingMemoryConfig()
        self._events: deque[EmbodiedMemoryEvent] = deque(maxlen=self.config.capacity)
        self._lock = threading.RLock()

    def add_persisted(self, event: EmbodiedMemoryEvent) -> bool:
        """Project an event after evidence persistence; never persists by itself."""
        encoded = json.dumps(
            event.to_mission_record().to_dict(),
            ensure_ascii=True,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        if len(encoded) > self.config.max_event_size_bytes:
            return False
        with self._lock:
            self._events.append(event)
        return True

    def hydrate(self, events: Iterable[EmbodiedMemoryEvent]) -> int:
        added = 0
        for event in events:
            if self.add_persisted(event):
                added += 1
        return added

    def snapshot(
        self,
        *,
        runtime_mode: str,
        mission_id: str,
        robot_id: str | None = None,
        subtask_id: str | None = None,
        event_types: frozenset[str] | None = None,
        reference_at: str | None = None,
        include_restricted: bool = False,
        include_stale: bool = False,
        limit: int | None = None,
    ) -> WorkingMemorySnapshot:
        if runtime_mode not in MEMORY_RUNTIME_MODES:
            raise ValueError(
                f"Invalid runtime_mode: {runtime_mode}. "
                f"Must be one of: {sorted(MEMORY_RUNTIME_MODES)}"
            )
        if not mission_id.strip():
            raise ValueError("mission_id must not be empty")
        reference = _parse_reference_time(reference_at)
        with self._lock:
            candidates = list(self._events)

        fresh: list[EmbodiedMemoryEvent] = []
        stale: list[EmbodiedMemoryEvent] = []
        for event in candidates:
            if event.runtime_mode != runtime_mode or event.mission_id != mission_id:
                continue
            if robot_id is not None and event.robot_id != robot_id:
                continue
            if subtask_id is not None and event.subtask_id != subtask_id:
                continue
            if event_types is not None and event.event_type not in event_types:
                continue
            if event.sensitivity == "restricted" and not include_restricted:
                continue
            if self._is_stale(event, reference):
                stale.append(event)
            else:
                fresh.append(event)

        if limit is not None:
            if limit < 0:
                raise ValueError("working-memory limit cannot be negative")
            fresh = fresh[-limit:] if limit else []
            stale = stale[-limit:] if limit else []
        latest_by_type: dict[str, EmbodiedMemoryEvent] = {}
        for event in fresh:
            latest_by_type[event.event_type] = event
        current_pose = next(
            (event.pose for event in reversed(fresh) if event.pose is not None),
            None,
        )
        return WorkingMemorySnapshot(
            runtime_mode=runtime_mode,
            mission_id=mission_id,
            reference_at=reference.isoformat(),
            events=tuple(fresh),
            stale_events=tuple(stale) if include_stale else (),
            latest_by_type=latest_by_type,
            current_pose=current_pose,
        )

    def _is_stale(self, event: EmbodiedMemoryEvent, reference: datetime) -> bool:
        observed = datetime.fromisoformat(event.observed_at)
        age_seconds = (reference - observed).total_seconds()
        if age_seconds < -self.config.future_clock_skew_seconds:
            return True
        return age_seconds > self.config.max_age_for(event.event_type)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._events)


def _parse_reference_time(value: str | None) -> datetime:
    reference = datetime.now(timezone.utc) if value is None else datetime.fromisoformat(value)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("working-memory reference_at must include a timezone")
    return reference
