"""Legacy-named Robot Agent run-lineage registry for FireClaw.

Provides ``SubagentRunRecord`` and ``JsonlSubagentRegistry`` -- a durable
append-only JSONL store that tracks parent/child lineage for every robot
dispatch coordinated by the Mission Coordinator. Public type and field names
retain ``subagent`` for compatibility with existing APIs and persisted state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any
import uuid

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_SUBAGENT_STATUSES = {
    "dispatched",
    "accepted",
    "running",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "lost",
    "orphaned",
}

VALID_SUBAGENT_DELIVERY_STATUSES = {
    "pending",
    "delivered",
    "failed",
}

TERMINAL_SUBAGENT_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "lost",
    "orphaned",
}


# ---------------------------------------------------------------------------
# SubagentRunRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubagentRunRecord:
    """Tracks one Robot Agent dispatch; the type name is legacy-compatible."""

    run_id: str
    parent_mission_id: str
    robot_id: str
    child_task_id: str
    status: str
    delivery_status: str
    created_at: str

    parent_subtask_id: str | None = None
    updated_at: str | None = None
    error: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_SUBAGENT_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubagentRunRecord:
        return cls(
            run_id=str(data.get("run_id") or ""),
            parent_mission_id=str(data.get("parent_mission_id") or ""),
            robot_id=str(data.get("robot_id") or ""),
            child_task_id=str(data.get("child_task_id") or ""),
            status=str(data.get("status") or "dispatched"),
            delivery_status=str(data.get("delivery_status") or "pending"),
            created_at=str(data.get("created_at") or ""),
            parent_subtask_id=_string_or_none(data.get("parent_subtask_id")),
            updated_at=_string_or_none(data.get("updated_at")),
            error=_string_or_none(data.get("error")),
        )


# ---------------------------------------------------------------------------
# JsonlSubagentRegistry
# ---------------------------------------------------------------------------

_UNSET = object()
"""Sentinel for distinguishing ``None`` from 'not provided'."""


class JsonlSubagentRegistry:
    """Append-only Robot Agent run store with a legacy-compatible name.

    Follows the same corrupt-line-tolerant pattern as ``JsonlTaskRegistryStore``.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- create / update / get / list ----------------------------------------

    def create(
        self,
        *,
        parent_mission_id: str,
        robot_id: str,
        child_task_id: str,
        created_at: str,
        parent_subtask_id: str | None = None,
    ) -> SubagentRunRecord:
        record = SubagentRunRecord(
            run_id=str(uuid.uuid4()),
            parent_mission_id=parent_mission_id,
            parent_subtask_id=parent_subtask_id,
            robot_id=robot_id,
            child_task_id=child_task_id,
            status="dispatched",
            delivery_status="pending",
            created_at=created_at,
        )
        self._append(record.to_dict())
        return record

    def update(
        self,
        run_id: str,
        *,
        status: str | None | object = _UNSET,
        delivery_status: str | None | object = _UNSET,
        updated_at: str | None | object = _UNSET,
        error: str | None | object = _UNSET,
    ) -> SubagentRunRecord:
        """Update a registry record.

        Fields set to ``_UNSET`` (the default) preserve the current value.
        Fields explicitly set to ``None`` clear the field.
        """

        def _resolve(new: Any, current: Any) -> Any:
            return current if new is _UNSET else new

        current = self.get_by_run_id(run_id)
        if current is None:
            raise KeyError(f"Subagent registry record not found: {run_id}")

        record = SubagentRunRecord(
            run_id=current.run_id,
            parent_mission_id=current.parent_mission_id,
            parent_subtask_id=current.parent_subtask_id,
            robot_id=current.robot_id,
            child_task_id=current.child_task_id,
            status=_resolve(status, current.status),
            delivery_status=_resolve(delivery_status, current.delivery_status),
            created_at=current.created_at,
            updated_at=_resolve(updated_at, current.updated_at),
            error=_resolve(error, current.error),
        )
        self._append(record.to_dict())
        return record

    def mark_terminal(
        self,
        *,
        child_task_id: str,
        status: str,
        updated_at: str,
        error: str | None = None,
    ) -> SubagentRunRecord | None:
        """Idempotently mark a subagent run as terminal.

        Returns the record after the update, or ``None`` if no record
        matches *child_task_id*.  If the record is already terminal,
        returns the current record without appending a new entry.
        """
        current = self.get_by_child_task_id(child_task_id)
        if current is None:
            return None
        if current.is_terminal:
            return current
        return self.update(
            current.run_id,
            status=status,
            delivery_status="delivered",
            updated_at=updated_at,
            error=error,
        )

    def get_by_run_id(self, run_id: str) -> SubagentRunRecord | None:
        return self._records_by_key("run_id").get(run_id)

    def get_by_child_task_id(self, child_task_id: str) -> SubagentRunRecord | None:
        return self._records_by_key("child_task_id").get(child_task_id)

    def list_records(self) -> list[SubagentRunRecord]:
        return list(self._records_by_key("run_id").values())

    def list_by_parent_mission(self, mission_id: str) -> list[SubagentRunRecord]:
        return [r for r in self.list_records() if r.parent_mission_id == mission_id]

    # -- internal storage helpers --------------------------------------------

    def _records_by_key(self, key_field: str) -> dict[str, SubagentRunRecord]:
        records: dict[str, SubagentRunRecord] = {}
        for entry in self._read_entries():
            key = entry.get(key_field)
            if not isinstance(key, str):
                continue
            # For run_id (the primary key), later entries overwrite earlier ones
            # so the latest state wins.
            records[key] = SubagentRunRecord.from_dict(entry)
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
