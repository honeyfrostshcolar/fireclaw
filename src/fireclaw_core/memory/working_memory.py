"""Bounded, non-authoritative working-memory projection for live missions."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import threading
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from fireclaw_core.mission.mission_registry import JsonlMissionRegistry

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


@dataclass(frozen=True)
class WorkingMemoryHydrationReport:
    considered: int = 0
    selected: int = 0
    added: int = 0
    stale: int = 0
    wrong_scope: int = 0
    oversized: int = 0


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

    def hydrate_recent(
        self,
        *,
        store: Any,
        registry: "JsonlMissionRegistry",
        runtime_mode: str,
        reference_at: str,
    ) -> WorkingMemoryHydrationReport:
        """Hydrate from authoritative store for active missions at startup.

        Selection rules:
        - only missions currently 'created' or 'running';
        - exact configured runtime;
        - only events fresh under existing per-type freshness rules;
        - future timestamps beyond allowed skew are excluded;
        - deterministic ordering by (observed_at, event_id);
        - total admitted events remain bounded by capacity;
        - multiple active missions receive a fair quota.
        """
        if runtime_mode not in MEMORY_RUNTIME_MODES:
            raise ValueError(
                f"Invalid runtime_mode: {runtime_mode}. "
                f"Must be one of: {sorted(MEMORY_RUNTIME_MODES)}"
            )
        reference = _parse_reference_time(reference_at)
        active_statuses = {"created", "running"}

        try:
            missions = registry.list_missions()
        except Exception:
            return WorkingMemoryHydrationReport()

        active_mission_ids = frozenset(
            m.mission_id for m in missions if m.status in active_statuses
        )
        if not active_mission_ids:
            return WorkingMemoryHydrationReport()

        try:
            all_events = store.list_events()
        except Exception:
            return WorkingMemoryHydrationReport()

        # Filter to active missions and runtime
        candidates: list[EmbodiedMemoryEvent] = []
        wrong_scope = 0
        for event in all_events:
            if event.mission_id not in active_mission_ids:
                wrong_scope += 1
                continue
            if event.runtime_mode != runtime_mode:
                wrong_scope += 1
                continue
            candidates.append(event)

        # Sort deterministically by (observed_at, event_id)
        candidates.sort(key=lambda e: (e.observed_at, e.event_id))

        # Apply freshness and future-skew filtering
        fresh: list[EmbodiedMemoryEvent] = []
        stale = 0
        for event in candidates:
            if self._is_stale(event, reference):
                stale += 1
                continue
            fresh.append(event)

        # Newest-first round-robin selection
        capacity = self.config.capacity
        by_mission: dict[str, deque[EmbodiedMemoryEvent]] = {}
        for event in fresh:
            by_mission.setdefault(event.mission_id, deque()).append(event)
        # Sort each mission's events newest-first
        for events in by_mission.values():
            ordered = sorted(
                events,
                key=lambda event: (event.observed_at, event.event_id),
                reverse=True,
            )
            events.clear()
            events.extend(ordered)

        # Sort missions by their newest event (newest mission first)
        mission_order = sorted(
            by_mission,
            key=lambda mission_id: (
                by_mission[mission_id][0].observed_at,
                by_mission[mission_id][0].event_id,
                mission_id,
            ),
            reverse=True,
        )
        # Round-robin: pick one from each mission per round
        selected_newest_first: list[EmbodiedMemoryEvent] = []
        while len(selected_newest_first) < capacity:
            admitted_this_round = 0
            for mission_id in mission_order:
                queue = by_mission[mission_id]
                if not queue:
                    continue
                selected_newest_first.append(queue.popleft())
                admitted_this_round += 1
                if len(selected_newest_first) == capacity:
                    break
            if admitted_this_round == 0:
                break
        # Insert oldest-to-newest so deque preserves chronological order
        selected = sorted(
            selected_newest_first,
            key=lambda event: (event.observed_at, event.event_id),
        )

        # Add to working memory
        added = 0
        oversized = 0
        for event in selected:
            if self.add_persisted(event):
                added += 1
            else:
                oversized += 1

        return WorkingMemoryHydrationReport(
            considered=len(candidates),
            selected=len(selected),
            added=added,
            stale=stale,
            wrong_scope=wrong_scope,
            oversized=oversized,
        )

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
