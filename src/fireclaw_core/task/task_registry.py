"""OpenClaw-inspired task registry for FireClaw.

Provides ``TaskRecord``, ``TaskDeliveryState``, ``TaskRegistrySnapshot``, and
``JsonlTaskRegistryStore`` -- a richer task tracking model than the original
``task_queue`` module, with runtime, delivery, and lifecycle metadata.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any


_UNSET = object()
"""Sentinel for distinguishing ``None`` (explicit clear) from 'not provided'."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_RUNTIMES = {"robot_gateway", "mission_gateway", "cli", "scheduler"}
VALID_STATUSES = {
    "queued",
    "running",
    "succeeded",
    "failed",
    "timed_out",
    "cancelled",
    "lost",
    # FireClaw legacy-compatible statuses
    "accepted",
    "completed",
    "denied",
}
VALID_DELIVERY_STATUSES = {
    "pending",
    "delivered",
    "session_queued",
    "failed",
    "parent_missing",
    "not_applicable",
}
VALID_NOTIFY_POLICIES = {"done_only", "state_changes", "silent"}
VALID_SCOPE_KINDS = {"session", "mission", "subtask", "system"}

TERMINAL_TASK_STATUSES = {
    "completed",
    "cancelled",
    "failed",
    "denied",
    "lost",
    "timed_out",
    "succeeded",
}


# ---------------------------------------------------------------------------
# TaskRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskRecord:
    """Rich task record inspired by OpenClaw's TaskRecord type."""

    task_id: str
    runtime: str
    requester_session_id: str
    owner_id: str
    scope_kind: str
    command: str
    status: str
    delivery_status: str
    notify_policy: str
    created_at: str

    # Optional metadata
    task_kind: str | None = None
    source_id: str | None = None
    child_session_id: str | None = None
    parent_task_id: str | None = None
    agent_id: str | None = None
    run_id: str | None = None
    label: str | None = None

    # Timestamps
    started_at: str | None = None
    ended_at: str | None = None
    last_event_at: str | None = None
    cleanup_after: str | None = None

    # Results / error
    error: str | None = None
    result: dict[str, Any] | None = None
    dedupe_key: str | None = None

    # Summaries
    progress_summary: str | None = None
    terminal_summary: str | None = None
    terminal_outcome: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_TASK_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskRecord:
        return cls(
            task_id=str(data.get("task_id") or ""),
            runtime=str(data.get("runtime") or "cli"),
            requester_session_id=str(data.get("requester_session_id") or ""),
            owner_id=str(data.get("owner_id") or ""),
            scope_kind=str(data.get("scope_kind") or "session"),
            command=str(data.get("command") or ""),
            status=str(data.get("status") or "unknown"),
            delivery_status=str(data.get("delivery_status") or "pending"),
            notify_policy=str(data.get("notify_policy") or "done_only"),
            created_at=str(data.get("created_at") or ""),
            task_kind=_string_or_none(data.get("task_kind")),
            source_id=_string_or_none(data.get("source_id")),
            child_session_id=_string_or_none(data.get("child_session_id")),
            parent_task_id=_string_or_none(data.get("parent_task_id")),
            agent_id=_string_or_none(data.get("agent_id")),
            run_id=_string_or_none(data.get("run_id")),
            label=_string_or_none(data.get("label")),
            started_at=_string_or_none(data.get("started_at")),
            ended_at=_string_or_none(data.get("ended_at")),
            last_event_at=_string_or_none(data.get("last_event_at")),
            cleanup_after=_string_or_none(data.get("cleanup_after")),
            error=_string_or_none(data.get("error")),
            result=data.get("result") if isinstance(data.get("result"), dict) else None,
            dedupe_key=_string_or_none(data.get("dedupe_key")),
            progress_summary=_string_or_none(data.get("progress_summary")),
            terminal_summary=_string_or_none(data.get("terminal_summary")),
            terminal_outcome=_string_or_none(data.get("terminal_outcome")),
        )


# ---------------------------------------------------------------------------
# TaskDeliveryState
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskDeliveryState:
    """Tracks delivery / notification state for a task."""

    task_id: str
    requester: dict[str, Any] | None = None
    last_notified_event_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "requester": self.requester,
            "last_notified_event_at": self.last_notified_event_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskDeliveryState:
        return cls(
            task_id=str(data.get("task_id") or ""),
            requester=data.get("requester") if isinstance(data.get("requester"), dict) else None,
            last_notified_event_at=_string_or_none(data.get("last_notified_event_at")),
        )


# ---------------------------------------------------------------------------
# TaskRegistrySnapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskRegistrySnapshot:
    """Point-in-time summary of the registry."""

    total: int
    active_count: int
    terminal_count: int
    active: list[TaskRecord] = field(default_factory=list)
    terminal: list[TaskRecord] = field(default_factory=list)

    @classmethod
    def from_records(cls, records: list[TaskRecord]) -> TaskRegistrySnapshot:
        active = [r for r in records if not r.is_terminal]
        terminal = [r for r in records if r.is_terminal]
        return cls(
            total=len(records),
            active_count=len(active),
            terminal_count=len(terminal),
            active=active,
            terminal=terminal,
        )


# ---------------------------------------------------------------------------
# JsonlTaskRegistryStore
# ---------------------------------------------------------------------------


class JsonlTaskRegistryStore:
    """Append-only JSONL store for ``TaskRecord``.

    Follows the same corrupt-line-tolerant pattern as ``JsonlTaskQueue``.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- create / update / get / list ----------------------------------------

    def create(
        self,
        *,
        task_id: str,
        runtime: str,
        requester_session_id: str,
        owner_id: str,
        scope_kind: str,
        command: str,
        created_at: str | None = None,
        delivery_status: str = "pending",
        notify_policy: str = "state_changes",
        dedupe_key: str | None = None,
        **extra: Any,
    ) -> TaskRecord:
        from datetime import datetime, timezone

        record = TaskRecord(
            task_id=task_id,
            runtime=runtime,
            requester_session_id=requester_session_id,
            owner_id=owner_id,
            scope_kind=scope_kind,
            command=command,
            status="queued",
            delivery_status=delivery_status,
            notify_policy=notify_policy,
            created_at=created_at or datetime.now(timezone.utc).isoformat(),
            dedupe_key=dedupe_key,
            **extra,
        )
        self._append(record.to_dict())
        return record

    def update(
        self,
        task_id: str,
        *,
        status: str | None = _UNSET,  # type: ignore[assignment]
        started_at: str | None | object = _UNSET,
        ended_at: str | None | object = _UNSET,
        error: str | None | object = _UNSET,
        result: dict[str, Any] | None | object = _UNSET,
        delivery_status: str | None | object = _UNSET,
        last_event_at: str | None | object = _UNSET,
        progress_summary: str | None | object = _UNSET,
        terminal_summary: str | None | object = _UNSET,
        terminal_outcome: str | None | object = _UNSET,
    ) -> TaskRecord:
        """Update a registry record.

        Fields set to ``_UNSET`` (the default) preserve the current value.
        Fields explicitly set to ``None`` clear the field.  This allows
        callers to reset ``error``, ``result``, etc. after a retry succeeds.
        """

        def _resolve(new: Any, current: Any) -> Any:
            return current if new is _UNSET else new

        current = self.get(task_id)
        if current is None:
            raise KeyError(f"Task registry record not found: {task_id}")

        record = TaskRecord(
            task_id=current.task_id,
            runtime=current.runtime,
            requester_session_id=current.requester_session_id,
            owner_id=current.owner_id,
            scope_kind=current.scope_kind,
            command=current.command,
            status=_resolve(status, current.status),
            delivery_status=_resolve(delivery_status, current.delivery_status),
            notify_policy=current.notify_policy,
            created_at=current.created_at,
            task_kind=current.task_kind,
            source_id=current.source_id,
            child_session_id=current.child_session_id,
            parent_task_id=current.parent_task_id,
            agent_id=current.agent_id,
            run_id=current.run_id,
            label=current.label,
            started_at=_resolve(started_at, current.started_at),
            ended_at=_resolve(ended_at, current.ended_at),
            last_event_at=_resolve(last_event_at, current.last_event_at),
            cleanup_after=current.cleanup_after,
            error=_resolve(error, current.error),
            result=_resolve(result, current.result),
            dedupe_key=current.dedupe_key,
            progress_summary=_resolve(progress_summary, current.progress_summary),
            terminal_summary=_resolve(terminal_summary, current.terminal_summary),
            terminal_outcome=_resolve(terminal_outcome, current.terminal_outcome),
        )
        self._append(record.to_dict())
        return record

    def project_task_state(
        self,
        *,
        task_id: str,
        requester_session_id: str,
        owner_id: str,
        command: str,
        runtime: str,
        scope_kind: str,
        status: str,
        delivery_status: str,
        notify_policy: str,
        created_at: str,
        parent_task_id: str | None = None,
        child_session_id: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        error: str | None = None,
        result: dict[str, Any] | None = None,
        terminal_outcome: str | None = None,
    ) -> TaskRecord:
        """Create or update a task record for mission lifecycle projection.

        If the record already exists, preserves ``created_at`` and merges
        optional fields from the existing record when the caller does not
        provide them (i.e. passes ``None``).
        """
        current = self.get(task_id)
        record = TaskRecord(
            task_id=task_id,
            runtime=runtime,
            requester_session_id=requester_session_id,
            owner_id=owner_id,
            scope_kind=scope_kind,
            command=command,
            status=status,
            delivery_status=delivery_status,
            notify_policy=notify_policy,
            created_at=current.created_at if current is not None and current.created_at else created_at,
            parent_task_id=parent_task_id if parent_task_id is not None else (current.parent_task_id if current else None),
            child_session_id=child_session_id if child_session_id is not None else (current.child_session_id if current else None),
            started_at=started_at if started_at is not None else (current.started_at if current else None),
            ended_at=ended_at if ended_at is not None else (current.ended_at if current else None),
            error=error if error is not None else (current.error if current else None),
            result=result if result is not None else (current.result if current else None),
            terminal_outcome=terminal_outcome if terminal_outcome is not None else (current.terminal_outcome if current else None),
            last_event_at=ended_at or started_at or created_at,
        )
        self._append(record.to_dict())
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        return self._records_by_task_id().get(task_id)

    def list_records(self) -> list[TaskRecord]:
        return list(self._records_by_task_id().values())

    def snapshot(self) -> TaskRegistrySnapshot:
        return TaskRegistrySnapshot.from_records(self.list_records())

    # -- internal storage helpers --------------------------------------------

    def _records_by_task_id(self) -> dict[str, TaskRecord]:
        records: dict[str, TaskRecord] = {}
        for entry in self._read_entries():
            task_id = entry.get("task_id")
            if not isinstance(task_id, str):
                continue
            records[task_id] = TaskRecord.from_dict(entry)
        return records

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
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(value, dict):
                    entries.append(value)
        return entries

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None
