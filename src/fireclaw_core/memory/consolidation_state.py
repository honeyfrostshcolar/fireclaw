"""Durable consolidation boundary and watermark state journal."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fireclaw_core.memory.embodied_memory import MEMORY_RUNTIME_MODES


CONSOLIDATION_BOUNDARY_STATUSES = frozenset({
    "queued",
    "running",
    "completed",
    "covered_without_episode",
    "failed",
})

_TERMINAL_STATUSES = frozenset({"completed", "covered_without_episode"})


@dataclass(frozen=True)
class ConsolidationBoundary:
    """Immutable identity of a closed source range eligible for consolidation."""

    boundary_id: str
    mission_id: str
    runtime_mode: str
    scope_kind: str
    robot_id: str | None
    subtask_id: str | None
    after_sequence: int
    through_sequence: int
    terminal_event_id: str
    trigger_reason: str

    def __post_init__(self) -> None:
        if not self.mission_id.strip():
            raise ValueError("mission_id must not be empty")
        if self.runtime_mode not in MEMORY_RUNTIME_MODES:
            raise ValueError(
                f"Invalid runtime_mode: {self.runtime_mode}. "
                f"Must be one of: {sorted(MEMORY_RUNTIME_MODES)}"
            )
        if self.scope_kind not in {"subtask", "mission"}:
            raise ValueError("scope_kind must be mission or subtask")
        if self.scope_kind == "subtask":
            if not self.robot_id or not self.subtask_id:
                raise ValueError("subtask boundary requires robot_id and subtask_id")
        elif self.scope_kind == "mission":
            if self.subtask_id is not None:
                raise ValueError("mission boundary cannot name subtask_id")
        if self.after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if self.through_sequence <= self.after_sequence:
            raise ValueError("through_sequence must be greater than after_sequence")
        if not self.terminal_event_id.strip():
            raise ValueError("terminal_event_id must not be empty")
        if not self.trigger_reason.strip():
            raise ValueError("trigger_reason must not be empty")
        if self.boundary_id == "auto":
            object.__setattr__(
                self,
                "boundary_id",
                _stable_boundary_id(
                    self.mission_id,
                    self.runtime_mode,
                    self.scope_kind,
                    self.robot_id,
                    self.subtask_id,
                    self.terminal_event_id,
                    self.through_sequence,
                ),
            )

    def scope_key(self) -> tuple[str, str, str, str | None, str | None]:
        return (self.mission_id, self.runtime_mode, self.scope_kind, self.robot_id, self.subtask_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary_id": self.boundary_id,
            "mission_id": self.mission_id,
            "runtime_mode": self.runtime_mode,
            "scope_kind": self.scope_kind,
            "robot_id": self.robot_id,
            "subtask_id": self.subtask_id,
            "after_sequence": self.after_sequence,
            "through_sequence": self.through_sequence,
            "terminal_event_id": self.terminal_event_id,
            "trigger_reason": self.trigger_reason,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ConsolidationBoundary:
        raw_scope_kind = value.get("scope_kind")
        if raw_scope_kind:
            scope_kind = str(raw_scope_kind)
        else:
            # Derive from old journal lines
            scope_kind = "subtask" if value.get("subtask_id") else "mission"
        return cls(
            boundary_id=str(value.get("boundary_id") or "auto"),
            mission_id=str(value.get("mission_id") or ""),
            runtime_mode=str(value.get("runtime_mode") or ""),
            scope_kind=scope_kind,
            robot_id=value.get("robot_id") if isinstance(value.get("robot_id"), str) else None,
            subtask_id=value.get("subtask_id") if isinstance(value.get("subtask_id"), str) else None,
            after_sequence=int(value.get("after_sequence", 0)),
            through_sequence=int(value.get("through_sequence", 0)),
            terminal_event_id=str(value.get("terminal_event_id") or ""),
            trigger_reason=str(value.get("trigger_reason") or ""),
        )


@dataclass(frozen=True)
class ConsolidationBoundaryState:
    """A single replayable state transition for a consolidation boundary."""

    boundary_id: str
    status: str
    through_sequence: int | None = None
    error_code: str | None = None
    error_class: str | None = None
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.boundary_id.strip():
            raise ValueError("boundary_id must not be empty")
        if self.status not in CONSOLIDATION_BOUNDARY_STATUSES:
            raise ValueError(f"invalid consolidation boundary status: {self.status}")
        if not self.updated_at:
            object.__setattr__(
                self, "updated_at", datetime.now(timezone.utc).isoformat()
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "boundary_state",
            "boundary_id": self.boundary_id,
            "status": self.status,
            "updated_at": self.updated_at,
        }
        if self.through_sequence is not None:
            payload["through_sequence"] = self.through_sequence
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        if self.error_class is not None:
            payload["error_class"] = self.error_class
        return payload

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ConsolidationBoundaryState:
        return cls(
            boundary_id=str(value.get("boundary_id") or ""),
            status=str(value.get("status") or ""),
            through_sequence=(
                int(value["through_sequence"])
                if value.get("through_sequence") is not None
                else None
            ),
            error_code=value.get("error_code") if isinstance(value.get("error_code"), str) else None,
            error_class=value.get("error_class") if isinstance(value.get("error_class"), str) else None,
            updated_at=str(value.get("updated_at") or ""),
        )


@dataclass(frozen=True)
class ConsolidationWatermark:
    """Largest successfully covered through_sequence for a scope key."""

    mission_id: str
    runtime_mode: str
    scope_kind: str
    robot_id: str | None
    subtask_id: str | None
    through_sequence: int
    boundary_id: str
    updated_at: str

    def scope_key(self) -> tuple[str, str, str, str | None, str | None]:
        return (self.mission_id, self.runtime_mode, self.scope_kind, self.robot_id, self.subtask_id)


class ConsolidationStateStore:
    """Append-only JSONL journal for boundary transitions and watermarks."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def ensure_boundary(self, boundary: ConsolidationBoundary) -> None:
        """Record a new boundary if not already known; reject identity conflicts."""
        with self._lock:
            boundaries = self._boundaries_by_id()
            existing = boundaries.get(boundary.boundary_id)
            if existing is not None:
                if existing.to_dict() != boundary.to_dict():
                    raise ValueError(
                        f"consolidation boundary identity conflict: {boundary.boundary_id}"
                    )
                return
            self._append(boundary.to_dict())

    def transition(
        self,
        boundary_id: str,
        *,
        status: str,
        through_sequence: int | None = None,
        error_code: str | None = None,
        error_class: str | None = None,
    ) -> ConsolidationBoundaryState:
        """Append a state transition for a boundary."""
        state = ConsolidationBoundaryState(
            boundary_id=boundary_id,
            status=status,
            through_sequence=through_sequence,
            error_code=error_code,
            error_class=error_class,
        )
        with self._lock:
            boundaries = self._boundaries_by_id()
            if boundary_id not in boundaries:
                raise KeyError(f"consolidation boundary not found: {boundary_id}")
            if status in _TERMINAL_STATUSES and through_sequence is not None:
                scope = boundaries[boundary_id].scope_key()
                current_wm = self._watermarks().get(scope)
                if current_wm is not None and through_sequence < current_wm.through_sequence:
                    raise ValueError(
                        f"watermark regression: {through_sequence} < {current_wm.through_sequence}"
                    )
            self._append(state.to_dict())
            return state

    def boundaries(self) -> dict[str, ConsolidationBoundary]:
        """Public access to all boundaries by ID."""
        with self._lock:
            return self._boundaries_by_id()

    def latest_status(self, boundary_id: str) -> ConsolidationBoundaryState:
        """Return the latest state for a boundary."""
        with self._lock:
            statuses = self._latest_boundary_state_objects()
            state = statuses.get(boundary_id)
            if state is None:
                raise KeyError(f"consolidation boundary not found: {boundary_id}")
            return state

    def reserve_boundary(
        self,
        *,
        mission_id: str,
        runtime_mode: str,
        scope_kind: str,
        robot_id: str | None,
        subtask_id: str | None,
        terminal_event_id: str,
        trigger_reason: str,
        through_sequence: int,
    ) -> ConsolidationBoundary | None:
        """Atomically reserve the next range for a scope. Returns None if
        the terminal event is already covered or through_sequence is too low."""
        with self._lock:
            boundaries = self._boundaries_by_id()
            # Check idempotency
            for boundary in boundaries.values():
                if (
                    boundary.terminal_event_id == terminal_event_id
                    and boundary.mission_id == mission_id
                    and boundary.runtime_mode == runtime_mode
                ):
                    return None
            scope = (mission_id, runtime_mode, scope_kind, robot_id, subtask_id)
            watermark = self._watermarks().get(scope)
            reserved_tail = max(
                (
                    b.through_sequence
                    for b in boundaries.values()
                    if b.scope_key() == scope
                ),
                default=0,
            )
            after_sequence = max(
                watermark.through_sequence if watermark is not None else 0,
                reserved_tail,
            )
            if through_sequence <= after_sequence:
                return None
            boundary = ConsolidationBoundary(
                boundary_id="auto",
                mission_id=mission_id,
                runtime_mode=runtime_mode,
                scope_kind=scope_kind,
                robot_id=robot_id,
                subtask_id=subtask_id,
                after_sequence=after_sequence,
                through_sequence=through_sequence,
                terminal_event_id=terminal_event_id,
                trigger_reason=trigger_reason,
            )
            self._append(boundary.to_dict())
            return boundary

    def pending_boundaries(self) -> list[ConsolidationBoundary]:
        """Return boundaries that are queued or running."""
        with self._lock:
            boundaries = self._boundaries_by_id()
            statuses = self._latest_boundary_statuses()
            result: list[ConsolidationBoundary] = []
            for bid, boundary in boundaries.items():
                st = statuses.get(bid, "queued")
                if st in {"queued", "running"}:
                    result.append(boundary)
            return result

    def watermark(
        self,
        mission_id: str,
        runtime_mode: str,
        robot_id: str | None,
        subtask_id: str | None,
        scope_kind: str | None = None,
    ) -> ConsolidationWatermark | None:
        with self._lock:
            actual_scope_kind = scope_kind or ("subtask" if subtask_id is not None else "mission")
            scope = (mission_id, runtime_mode, actual_scope_kind, robot_id, subtask_id)
            return self._watermarks().get(scope)

    def _watermarks(self) -> dict[tuple[str, str, str | None, str | None], ConsolidationWatermark]:
        watermarks: dict[tuple[str, str, str | None, str | None], ConsolidationWatermark] = {}
        boundaries = self._boundaries_by_id()
        for entry in self._read_entries():
            if entry.get("type") != "boundary_state":
                continue
            state = ConsolidationBoundaryState.from_dict(entry)
            if state.status not in _TERMINAL_STATUSES:
                continue
            if state.through_sequence is None:
                continue
            boundary = boundaries.get(state.boundary_id)
            if boundary is None:
                continue
            scope = boundary.scope_key()
            existing = watermarks.get(scope)
            if existing is None or state.through_sequence > existing.through_sequence:
                watermarks[scope] = ConsolidationWatermark(
                    mission_id=boundary.mission_id,
                    runtime_mode=boundary.runtime_mode,
                    scope_kind=boundary.scope_kind,
                    robot_id=boundary.robot_id,
                    subtask_id=boundary.subtask_id,
                    through_sequence=state.through_sequence,
                    boundary_id=state.boundary_id,
                    updated_at=state.updated_at,
                )
        return watermarks

    def _boundaries_by_id(self) -> dict[str, ConsolidationBoundary]:
        boundaries: dict[str, ConsolidationBoundary] = {}
        for entry in self._read_entries():
            if entry.get("type") == "boundary_state":
                continue
            bid = entry.get("boundary_id")
            if isinstance(bid, str) and bid:
                try:
                    boundary = ConsolidationBoundary.from_dict(entry)
                    boundaries[boundary.boundary_id] = boundary
                except (TypeError, ValueError):
                    continue
        return boundaries

    def _latest_boundary_statuses(self) -> dict[str, str]:
        statuses: dict[str, str] = {}
        for entry in self._read_entries():
            if entry.get("type") != "boundary_state":
                continue
            bid = entry.get("boundary_id")
            status = entry.get("status")
            if isinstance(bid, str) and isinstance(status, str):
                statuses[bid] = status
        return statuses

    def _latest_boundary_state_objects(self) -> dict[str, ConsolidationBoundaryState]:
        states: dict[str, ConsolidationBoundaryState] = {}
        for entry in self._read_entries():
            if entry.get("type") != "boundary_state":
                continue
            try:
                state = ConsolidationBoundaryState.from_dict(entry)
                states[state.boundary_id] = state
            except (TypeError, ValueError):
                continue
        return states

    def _read_entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    value = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    entries.append(value)
        return entries

    def _append(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())


def _stable_boundary_id(
    mission_id: str,
    runtime_mode: str,
    scope_kind: str,
    robot_id: str | None,
    subtask_id: str | None,
    terminal_event_id: str,
    through_sequence: int,
) -> str:
    canonical = json.dumps(
        {
            "mission_id": mission_id,
            "runtime_mode": runtime_mode,
            "scope_kind": scope_kind,
            "robot_id": robot_id,
            "subtask_id": subtask_id,
            "terminal_event_id": terminal_event_id,
            "through_sequence": through_sequence,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"boundary-{digest}"
