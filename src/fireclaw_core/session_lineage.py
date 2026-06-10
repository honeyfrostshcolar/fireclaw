"""Lightweight mission session lineage tracking and resume ownership guard.

Provides a compact JSONL-backed store for recording which operator created
each mission session, enabling resume-ownership checks when an operator
attempts to reuse an existing session_id.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MissionSessionLineage:
    """Compact lineage record for a mission session."""

    session_id: str
    kind: str  # e.g. "mission", "subtask"
    operator_id: str
    parent_session_id: str | None = None
    spawned_by: str | None = None
    spawn_depth: int = 0
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "kind": self.kind,
            "operator_id": self.operator_id,
            "parent_session_id": self.parent_session_id,
            "spawned_by": self.spawned_by,
            "spawn_depth": self.spawn_depth,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissionSessionLineage:
        return cls(
            session_id=str(data.get("session_id", "")),
            kind=str(data.get("kind", "")),
            operator_id=str(data.get("operator_id", "")),
            parent_session_id=data.get("parent_session_id"),
            spawned_by=data.get("spawned_by"),
            spawn_depth=int(data.get("spawn_depth", 0)),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


@dataclass
class ResumeOwnershipDecision:
    """Result of a resume ownership check."""

    ok: bool
    reason: str = ""


class JsonlSessionLineageStore:
    """Append-only JSONL store for session lineage records.

    Each record is written as a single JSON line. On read, the latest
    record for a given session_id is returned (last write wins).
    Corrupt lines are silently skipped.
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def upsert(self, record: MissionSessionLineage) -> None:
        """Append a lineage record (last write wins on get)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def get(self, session_id: str) -> MissionSessionLineage | None:
        """Return the latest lineage record for session_id, or None."""
        latest: MissionSessionLineage | None = None
        try:
            with self._path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    if data.get("session_id") == session_id:
                        latest = MissionSessionLineage.from_dict(data)
        except FileNotFoundError:
            return None
        return latest

    def list_for_operator(self, operator_id: str) -> list[MissionSessionLineage]:
        """Return all lineage records (latest per session) for an operator."""
        by_session: dict[str, MissionSessionLineage] = {}
        try:
            with self._path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    if data.get("operator_id") == operator_id:
                        sid = data.get("session_id", "")
                        by_session[sid] = MissionSessionLineage.from_dict(data)
        except FileNotFoundError:
            return []
        return list(by_session.values())


def validate_resume_ownership(
    store: JsonlSessionLineageStore,
    *,
    session_id: str,
    operator_id: str,
) -> ResumeOwnershipDecision:
    """Check whether operator_id owns the session for resume.

    - Unknown session: allow (no ownership to check)
    - Known session, matching operator: allow
    - Known session, different operator: deny
    """
    record = store.get(session_id)
    if record is None:
        return ResumeOwnershipDecision(ok=True, reason="")
    if record.operator_id == operator_id:
        return ResumeOwnershipDecision(ok=True, reason="")
    return ResumeOwnershipDecision(
        ok=False,
        reason="session resume is not owned by this operator",
    )
