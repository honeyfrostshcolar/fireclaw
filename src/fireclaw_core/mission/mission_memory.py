from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from fireclaw_core.memory.memory_index import SqliteMemoryIndex

MEMORY_RECORD_TYPES = {
    "body_state",
    "command",
    "plan",
    "gist",
    "mission",
    "observation",
    "outcome",
    "correction",
    "entity_mention",
    "entity_resolution",
    "episode",
    "lesson",
    "relation",
    "safety_decision",
    "skill_invocation",
    "subtask",
}

# Default set of record types that are safe to index in the FTS search index.
# Private logs (e.g. raw sensor dumps, operator PII) are NOT included.
# Operators must explicitly configure ``include_types`` to opt-in to
# additional record types.
DEFAULT_INDEXABLE_TYPES: frozenset[str] = frozenset({
    "command",
    "gist",
    "mission",
    "plan",
    "observation",
    "outcome",
    "lesson",
    "safety_decision",
    "skill_invocation",
    "subtask",
})


@dataclass(frozen=True)
class TranscriptIndexingPolicy:
    """Controls which mission transcript records are indexed.

    By default, only non-private record types are indexed.  Callers can
    override ``include_types`` to add or restrict what gets indexed.

    Attributes
    ----------
    include_types:
        Record types that should be indexed.  If ``None``, uses
        ``DEFAULT_INDEXABLE_TYPES``.  Set to an empty set to disable
        indexing entirely.
    """

    include_types: frozenset[str] | None = None

    def should_index(self, record_type: str) -> bool:
        """Return True if *record_type* should be indexed."""
        allowed = self.include_types if self.include_types is not None else DEFAULT_INDEXABLE_TYPES
        return record_type in allowed


@dataclass(frozen=True)
class MissionMemoryRecord:
    record_id: str
    mission_id: str
    record_type: str
    content: dict[str, Any]
    robot_id: str | None = None
    subtask_id: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MissionMemoryStore:
    def __init__(
        self,
        path: str | Path,
        *,
        index_path: str | Path | None = None,
        indexing_policy: TranscriptIndexingPolicy | None = None,
    ) -> None:
        self.path = Path(path)
        self._index: SqliteMemoryIndex | None = None
        self._index_path: Path | None = Path(index_path) if index_path else None
        self._indexing_policy = indexing_policy or TranscriptIndexingPolicy()
        self._lock = threading.RLock()
        self._write_guard: Callable[[str], None] | None = None
        # The JSONL file remains the authority, but physical execution needs
        # point lookups (duplicate IDs and relation endpoints) on every event.
        # Keep a process-local projection and invalidate it when the authority
        # file's O(1) stat token changes.  This turns repeated read-before-write
        # scans into one lazy load per authority version.
        self._record_cache: list[MissionMemoryRecord] | None = None
        self._record_cache_by_id: dict[str, MissionMemoryRecord] | None = None
        self._record_cache_token: str | None = None

    def set_write_guard(self, guard: Callable[[str], None] | None) -> None:
        """Install a mission-level lifecycle guard for future appends."""
        self._write_guard = guard

    @property
    def index(self) -> SqliteMemoryIndex | None:
        """Return the optional FTS index, lazily instantiated."""
        if self._index is not None:
            return self._index
        if self._index_path is not None:
            from fireclaw_core.memory.memory_index import SqliteMemoryIndex
            self._index = SqliteMemoryIndex(self._index_path)
        return self._index

    def snapshot_token(self) -> str:
        """Return an O(1) token that changes when the JSONL authority changes."""
        with self._lock:
            return self._snapshot_token_unlocked()

    def append(self, record: MissionMemoryRecord) -> None:
        if record.record_type not in MEMORY_RECORD_TYPES:
            raise ValueError(
                f"Invalid record type: {record.record_type}. "
                f"Must be one of: {sorted(MEMORY_RECORD_TYPES)}"
            )
        with self._lock:
            if self._write_guard is not None:
                self._write_guard(record.mission_id)
            # If this store has already materialized its cache, detect an
            # external append before extending the local projection.  The
            # normal single-process path only performs an O(1) stat check.
            if self._record_cache is not None:
                self._ensure_record_cache_unlocked()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
                handle.write("\n")
            if self._record_cache is not None:
                self._record_cache.append(record)
                self._record_cache_by_id[record.record_id] = record
                self._record_cache_token = self._snapshot_token_unlocked()
            if self.index is not None:
                authority_token = self.snapshot_token()
                if self._indexing_policy.should_index(record.record_type):
                    self.index.upsert(
                        record.to_dict(),
                        authority_token=authority_token,
                    )
                else:
                    self.index.sync_spatial_authority_token(authority_token)

    def purge_mission(self, mission_id: str) -> int:
        """Atomically remove one mission from the hot JSONL store.

        Lifecycle audit data must be written before this method is called. Any
        malformed or unrelated JSONL lines are preserved byte-for-byte.
        """
        if not mission_id.strip():
            raise ValueError("mission_id must not be empty")
        with self._lock:
            if not self.path.exists():
                self._invalidate_record_cache_unlocked()
                return 0
            self.path.parent.mkdir(parents=True, exist_ok=True)
            removed = 0
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as output, self.path.open(
                    "r", encoding="utf-8"
                ) as source:
                    for line in source:
                        try:
                            value = json.loads(line)
                        except json.JSONDecodeError:
                            output.write(line)
                            continue
                        if isinstance(value, dict) and value.get("mission_id") == mission_id:
                            removed += 1
                            continue
                        output.write(line)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary_path, self.path)
            except Exception:
                temporary_path.unlink(missing_ok=True)
                raise
            self._invalidate_record_cache_unlocked()
            if self.index is not None:
                records = self._read_all_unlocked()
                self.index.clear()
                authority_token = self.snapshot_token()
                for record in records:
                    if self._indexing_policy.should_index(record.record_type):
                        self.index.upsert(
                            record.to_dict(),
                            authority_token=authority_token,
                            finalize_spatial=False,
                            commit=False,
                        )
                self.index.finalize_spatial_projection(
                    authority_token=authority_token
                )
            return removed

    def ingest_transcript(
        self,
        *,
        mission_id: str,
        entry_type: str,
        content: dict[str, Any],
        robot_id: str | None = None,
        subtask_id: str | None = None,
        created_at: str | None = None,
    ) -> MissionMemoryRecord:
        """Append a structured transcript entry to memory.

        This is a convenience wrapper around :meth:`append` that generates
        a ``record_id`` and defaults ``created_at`` to the current UTC time
        when not provided.

        Parameters
        ----------
        mission_id:
            The mission this entry belongs to.
        entry_type:
            One of the allowed record types, including transcript records and
            typed embodied-memory events such as ``"skill_invocation"``,
            ``"safety_decision"``, and ``"body_state"``.
        content:
            Arbitrary structured content for this entry.
        robot_id:
            Optional identifier of the robot associated with this entry.
        subtask_id:
            Optional identifier of the subtask associated with this entry.
        created_at:
            ISO-8601 timestamp.  Defaults to current UTC time when ``None``.
        """
        record = MissionMemoryRecord(
            record_id=uuid.uuid4().hex,
            mission_id=mission_id,
            record_type=entry_type,
            content=content,
            robot_id=robot_id,
            subtask_id=subtask_id,
            created_at=created_at or datetime.now(timezone.utc).isoformat(),
        )
        self.append(record)
        return record

    def list_records(
        self,
        *,
        mission_id: str | None = None,
        record_type: str | None = None,
    ) -> list[MissionMemoryRecord]:
        with self._lock:
            self._ensure_record_cache_unlocked()
            records = list(self._record_cache or ())
        if mission_id is not None:
            records = [r for r in records if r.mission_id == mission_id]
        if record_type is not None:
            records = [r for r in records if r.record_type == record_type]
        return records

    def search(
        self,
        *,
        mission_id: str | None = None,
        record_type: str | None = None,
        robot_id: str | None = None,
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[MissionMemoryRecord]:
        if limit <= 0:
            return []
        # If an FTS index is configured, delegate to it.
        if self.index is not None:
            return self._search_via_index(
                mission_id=mission_id,
                record_type=record_type,
                robot_id=robot_id,
                keyword=keyword,
                limit=limit,
            )
        records = self.list_records()
        matches: list[MissionMemoryRecord] = []
        for record in records:
            if mission_id is not None and record.mission_id != mission_id:
                continue
            if record_type is not None and record.record_type != record_type:
                continue
            if robot_id is not None and record.robot_id != robot_id:
                continue
            if keyword is not None and not _content_contains(record.content, keyword):
                continue
            matches.append(record)
        return list(reversed(matches))[:limit]

    def search_indexed(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[MissionMemoryRecord]:
        """Full-text search using the optional FTS index.

        Raises ``RuntimeError`` if no index is configured.
        """
        if self.index is None:
            raise RuntimeError(
                "No memory index configured. Pass index_path to MissionMemoryStore."
            )
        raw = self.index.search(query, filters=filters, limit=limit)
        return [_record_from_dict(r) for r in raw]

    def _search_via_index(
        self,
        *,
        mission_id: str | None = None,
        record_type: str | None = None,
        robot_id: str | None = None,
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[MissionMemoryRecord]:
        """Translate the existing search() keyword API into index queries."""
        filters: dict[str, Any] = {}
        if mission_id is not None:
            filters["mission_id"] = mission_id
        if record_type is not None:
            filters["record_type"] = record_type
        if robot_id is not None:
            filters["robot_id"] = robot_id
        query = keyword if keyword else "*"
        raw = self.index.search(query, filters=filters or None, limit=limit)
        return [_record_from_dict(r) for r in raw]

    def summary(self, mission_id: str | None = None) -> dict[str, Any]:
        records = self.list_records()
        if mission_id is not None:
            records = [r for r in records if r.mission_id == mission_id]
        by_type: dict[str, int] = {}
        for record in records:
            by_type[record.record_type] = by_type.get(record.record_type, 0) + 1
        return {
            "total": len(records),
            "by_type": by_type,
        }

    def _read_all(self) -> list[MissionMemoryRecord]:
        with self._lock:
            return self._read_all_unlocked()

    def has_record_id(self, record_id: str) -> bool:
        """Return whether *record_id* exists without rescanning JSONL per call."""
        with self._lock:
            self._ensure_record_cache_unlocked()
            return record_id in (self._record_cache_by_id or {})

    def get_record(self, record_id: str) -> MissionMemoryRecord | None:
        """Load one record by ID from the authority-backed local projection."""
        with self._lock:
            self._ensure_record_cache_unlocked()
            return (self._record_cache_by_id or {}).get(record_id)

    def _snapshot_token_unlocked(self) -> str:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return "missing"
        return ":".join(str(value) for value in (
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        ))

    def _ensure_record_cache_unlocked(self) -> None:
        token = self._snapshot_token_unlocked()
        if self._record_cache is not None and self._record_cache_token == token:
            return
        records = self._read_all_unlocked()
        self._record_cache = records
        # Keep the last record for a duplicate ID, matching list_events()'s
        # existing dict-comprehension behavior on malformed historical data.
        self._record_cache_by_id = {record.record_id: record for record in records}
        self._record_cache_token = token

    def _invalidate_record_cache_unlocked(self) -> None:
        self._record_cache = None
        self._record_cache_by_id = None
        self._record_cache_token = None

    def _read_all_unlocked(self) -> list[MissionMemoryRecord]:
        if not self.path.exists():
            return []
        records: list[MissionMemoryRecord] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    records.append(_record_from_dict(data))
        return records


def _record_from_dict(data: dict[str, Any]) -> MissionMemoryRecord:
    return MissionMemoryRecord(
        record_id=str(data.get("record_id") or ""),
        mission_id=str(data.get("mission_id") or ""),
        record_type=str(data.get("record_type") or ""),
        content=data.get("content") if isinstance(data.get("content"), dict) else {},
        robot_id=data.get("robot_id") if isinstance(data.get("robot_id"), str) else None,
        subtask_id=data.get("subtask_id") if isinstance(data.get("subtask_id"), str) else None,
        created_at=str(data.get("created_at") or ""),
    )


def _content_contains(content: dict[str, Any], keyword: str) -> bool:
    keyword_lower = keyword.lower()
    for value in content.values():
        if isinstance(value, str) and keyword_lower in value.lower():
            return True
    return False
