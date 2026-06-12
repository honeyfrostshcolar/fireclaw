"""Robotics task-flow registry for FireClaw.

Provides ``TaskFlowRecord`` and ``JsonlTaskFlowRegistryStore`` -- a
mission-level flow tracker that summarises how a robot experiment
progressed, from dispatch through terminal state.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TaskFlowRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskFlowRecord:
    """Immutable snapshot of a mission-level task flow."""

    flow_id: str
    mission_id: str
    command: str
    status: str
    task_ids: tuple[str, ...]
    robot_ids: tuple[str, ...]
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "mission_id": self.mission_id,
            "command": self.command,
            "status": self.status,
            "task_ids": self.task_ids,
            "robot_ids": self.robot_ids,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskFlowRecord:
        return cls(
            flow_id=d["flow_id"],
            mission_id=d["mission_id"],
            command=d["command"],
            status=d["status"],
            task_ids=tuple(d.get("task_ids", ())),
            robot_ids=tuple(d.get("robot_ids", ())),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )


# ---------------------------------------------------------------------------
# JsonlTaskFlowRegistryStore
# ---------------------------------------------------------------------------


class JsonlTaskFlowRegistryStore:
    """Append-only JSONL store for task flow records.

    The latest record for each ``flow_id`` wins.  An optional ``on_event``
    callback is invoked on every ``upsert`` with a dict of the form
    ``{"kind": "upserted", "flow": <dict>}``.
    """

    def __init__(
        self,
        path: str | Path,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._path = Path(path)
        self._on_event = on_event
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("")
        self._index: dict[str, TaskFlowRecord] = {}
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        """Load all records from disk, keeping only the latest per flow_id."""
        try:
            text = self._path.read_text()
        except FileNotFoundError:
            return
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                record = TaskFlowRecord.from_dict(d)
                self._index[record.flow_id] = record
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                logger.warning("Skipping corrupt line in task-flow store: %s", line[:200])
                continue

    def _append(self, record: TaskFlowRecord) -> None:
        """Append a single record to the JSONL file."""
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    # -- public API ---------------------------------------------------------

    def upsert(self, flow: TaskFlowRecord) -> None:
        """Persist a flow record (append-only) and notify observer."""
        self._index[flow.flow_id] = flow
        self._append(flow)
        if self._on_event is not None:
            try:
                self._on_event({"kind": "upserted", "flow": flow.to_dict()})
            except Exception:
                logger.warning("Task-flow observer callback failed", exc_info=True)

    def get(self, flow_id: str) -> TaskFlowRecord | None:
        """Return the latest record for *flow_id*, or ``None``."""
        return self._index.get(flow_id)

    def list_recent(self, limit: int = 20) -> list[TaskFlowRecord]:
        """Return up to *limit* most-recent records, newest first.

        Records are deduplicated by ``flow_id`` -- only the latest
        version of each flow is returned.
        """
        # _index already holds the latest per flow_id; sort by updated_at descending
        all_flows = sorted(
            self._index.values(),
            key=lambda r: r.updated_at,
            reverse=True,
        )
        return all_flows[:limit]
