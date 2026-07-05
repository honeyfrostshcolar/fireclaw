from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any


TERMINAL_TASK_STATUSES = {"completed", "cancelled", "failed", "denied", "lost"}


@dataclass(frozen=True)
class TaskQueueRecord:
    task_id: str
    session_id: str
    command: str
    status: str
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    dedupe_key: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_TASK_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JsonlTaskQueue:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def create(
        self,
        *,
        task_id: str,
        session_id: str,
        command: str,
        created_at: str,
        dedupe_key: str | None = None,
    ) -> TaskQueueRecord:
        record = TaskQueueRecord(
            task_id=task_id,
            session_id=session_id,
            command=command,
            status="accepted",
            created_at=created_at,
            dedupe_key=dedupe_key,
        )
        self._append(record.to_dict())
        return record

    def update(
        self,
        task_id: str,
        *,
        status: str,
        started_at: str | None = None,
        ended_at: str | None = None,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> TaskQueueRecord:
        current = self.get(task_id)
        if current is None:
            raise KeyError(f"Task queue record not found: {task_id}")
        record = TaskQueueRecord(
            task_id=current.task_id,
            session_id=current.session_id,
            command=current.command,
            status=status,
            created_at=current.created_at,
            started_at=started_at if started_at is not None else current.started_at,
            ended_at=ended_at if ended_at is not None else current.ended_at,
            dedupe_key=current.dedupe_key,
            error=error if error is not None else current.error,
            result=result if result is not None else current.result,
        )
        self._append(record.to_dict())
        return record

    def get(self, task_id: str) -> TaskQueueRecord | None:
        return self._records_by_task_id().get(task_id)

    def list_records(self) -> list[TaskQueueRecord]:
        return list(self._records_by_task_id().values())

    def find_non_terminal_by_dedupe_key(self, dedupe_key: str | None) -> TaskQueueRecord | None:
        if not dedupe_key:
            return None
        for record in reversed(self.list_records()):
            if record.dedupe_key == dedupe_key and not record.is_terminal:
                return record
        return None

    def mark_non_terminal_lost(self, *, ended_at: str, error: str) -> list[TaskQueueRecord]:
        lost = []
        for record in self.list_records():
            if record.is_terminal:
                continue
            lost.append(
                self.update(
                    record.task_id,
                    status="lost",
                    ended_at=ended_at,
                    error=error,
                )
            )
        return lost

    def compact(self, keep_terminal: int = 100) -> int:
        """Remove old terminal records, keeping only the most recent ones.

        Returns the number of records removed.
        """
        records = self.list_records()
        non_terminal = [r for r in records if not r.is_terminal]
        terminal = [r for r in records if r.is_terminal]

        # Keep only the last `keep_terminal` terminal records by created_at
        terminal.sort(key=lambda r: r.created_at)
        removed_count = max(0, len(terminal) - keep_terminal)
        kept_terminal = terminal[removed_count:]

        kept = non_terminal + kept_terminal

        if removed_count == 0:
            return 0

        # Rewrite the file with only kept records
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            for record in kept:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
                handle.write("\n")

        return removed_count

    def summary(self) -> dict[str, Any]:
        records = self.list_records()
        active = [record for record in records if not record.is_terminal]
        return {
            "task_count": len(records),
            "active_task_count": len(active),
            "terminal_task_count": len(records) - len(active),
            "active_tasks": [record.to_dict() for record in active],
        }

    def _records_by_task_id(self) -> dict[str, TaskQueueRecord]:
        records: dict[str, TaskQueueRecord] = {}
        for entry in self._read_entries():
            task_id = entry.get("task_id")
            if not isinstance(task_id, str):
                continue
            records[task_id] = TaskQueueRecord(
                task_id=task_id,
                session_id=str(entry.get("session_id") or ""),
                command=str(entry.get("command") or ""),
                status=str(entry.get("status") or "unknown"),
                created_at=str(entry.get("created_at") or ""),
                started_at=_string_or_none(entry.get("started_at")),
                ended_at=_string_or_none(entry.get("ended_at")),
                dedupe_key=_string_or_none(entry.get("dedupe_key")),
                error=_string_or_none(entry.get("error")),
                result=entry.get("result") if isinstance(entry.get("result"), dict) else None,
            )
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
                value = json.loads(stripped)
                if isinstance(value, dict):
                    entries.append(value)
        return entries

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def queue_record_to_task_record(
    record: TaskQueueRecord,
    *,
    owner_id: str,
    runtime: str = "robot_gateway",
) -> "TaskRecord":
    """Convert a legacy ``TaskQueueRecord`` into a ``TaskRecord``.

    Imports ``TaskRecord`` lazily to avoid circular imports.
    """
    from fireclaw_core.task.task_registry import TaskRecord  # local import

    return TaskRecord(
        task_id=record.task_id,
        runtime=runtime,
        requester_session_id=record.session_id,
        owner_id=owner_id,
        scope_kind="mission",
        command=record.command,
        status=record.status,
        delivery_status="not_applicable",
        notify_policy="done_only",
        created_at=record.created_at,
        started_at=record.started_at,
        ended_at=record.ended_at,
        error=record.error,
        result=record.result,
        dedupe_key=record.dedupe_key,
    )


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None
