"""Mission-scoped retention, audit archives, and reusable knowledge."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fireclaw_core.infra.log_redaction import redact_dict
from fireclaw_core.memory.embodied_memory import (
    EMBODIED_METADATA_KEY,
    RELATION_METADATA_KEY,
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryStore,
)
from fireclaw_core.mission.mission_memory import MissionMemoryRecord


MISSION_MEMORY_STATES = frozenset({"active", "audit_only", "deleted"})
TERMINAL_MISSION_STATUSES = frozenset({"succeeded", "completed", "failed", "cancelled"})
REUSABLE_KNOWLEDGE_TYPES = frozenset({
    "capability_constraint",
    "failure_mode",
    "operator_preference",
    "procedure",
    "safety_rule",
    "skill",
})


@dataclass(frozen=True)
class MissionMemoryLifecycleState:
    mission_id: str
    state: str
    runtime_mode: str | None = None
    changed_at: str | None = None
    changed_by: str | None = None
    reason: str | None = None
    archive_id: str | None = None
    record_count: int = 0
    content_sha256: str | None = None

    @property
    def operational(self) -> bool:
        return self.state == "active"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "operational": self.operational}


@dataclass(frozen=True)
class ReusableKnowledgeRecord:
    knowledge_id: str
    knowledge_type: str
    title: str
    content: dict[str, Any]
    tags: tuple[str, ...]
    source_mission_id: str
    source_runtime_mode: str
    source_event_ids: tuple[str, ...]
    applicable_runtime_modes: tuple[str, ...]
    approved_by: str
    approved_at: str
    approval_reason: str
    status: str = "approved"
    revoked_by: str | None = None
    revoked_at: str | None = None
    revocation_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["tags"] = list(self.tags)
        value["source_event_ids"] = list(self.source_event_ids)
        value["applicable_runtime_modes"] = list(self.applicable_runtime_modes)
        value.update({
            "knowledge_scope": "cross_mission",
            "operator_approved": True,
            "advisory_only": True,
            "can_authorize_action": False,
            "requires_current_state_revalidation": True,
        })
        return value


class MissionMemoryLifecycleStore:
    """Controls which mission memory may influence a live Agent.

    Active evidence lives in the hot append store. Archiving writes an exact,
    hashed per-mission audit bundle before removing that mission from the hot
    store and its SQLite projection. Deletion removes the bundle but preserves
    a content-free tombstone in the lifecycle journal.
    """

    def __init__(
        self,
        *,
        store: EmbodiedMemoryStore,
        lifecycle_path: str | Path,
        audit_dir: str | Path,
        knowledge_path: str | Path,
        runtime_mode: str,
    ) -> None:
        self._store = store
        self.lifecycle_path = Path(lifecycle_path)
        self.audit_dir = Path(audit_dir)
        self.knowledge_path = Path(knowledge_path)
        self.runtime_mode = runtime_mode
        self._lock = threading.RLock()
        self._store.evidence_store.set_write_guard(self.assert_writable)

    def state(self, mission_id: str) -> MissionMemoryLifecycleState:
        _require_text("mission_id", mission_id)
        latest: dict[str, Any] | None = None
        for entry in self._read_jsonl(self.lifecycle_path):
            if (
                entry.get("mission_id") == mission_id
                and entry.get("runtime_mode") == self.runtime_mode
                and entry.get("kind") == "transition"
            ):
                latest = entry
        if latest is None:
            return MissionMemoryLifecycleState(mission_id=mission_id, state="active")
        state = str(latest.get("to_state") or "")
        if state not in MISSION_MEMORY_STATES:
            raise ValueError(f"Invalid persisted mission memory state: {state}")
        return MissionMemoryLifecycleState(
            mission_id=mission_id,
            state=state,
            runtime_mode=_optional_text(latest.get("runtime_mode")),
            changed_at=_optional_text(latest.get("occurred_at")),
            changed_by=_optional_text(latest.get("actor_id")),
            reason=_optional_text(latest.get("reason")),
            archive_id=_optional_text(latest.get("archive_id")),
            record_count=_nonnegative_int(latest.get("record_count")),
            content_sha256=_optional_text(latest.get("content_sha256")),
        )

    def assert_writable(self, mission_id: str) -> None:
        state = self.state(mission_id)
        if not state.operational:
            raise RuntimeError(
                f"Mission memory is {state.state}; new operational evidence is rejected"
            )

    def assert_operational(self, mission_id: str) -> None:
        state = self.state(mission_id)
        if not state.operational:
            raise RuntimeError(
                f"Mission memory is {state.state}; decision-facing memory access is disabled"
            )

    def archive_mission(
        self,
        mission_id: str,
        *,
        actor_id: str,
        reason: str,
        mission_status: str,
    ) -> dict[str, Any]:
        _require_text("mission_id", mission_id)
        _require_text("actor_id", actor_id)
        _require_text("reason", reason)
        if mission_status not in TERMINAL_MISSION_STATUSES:
            raise ValueError(
                "Mission memory can be archived only after a terminal mission status"
            )
        with self._lock:
            current = self.state(mission_id)
            if current.state == "audit_only":
                self._read_archive(self._archive_path(current.archive_id or ""))
                removed = self._store.evidence_store.purge_mission(mission_id)
                if self._store.index is not None and removed:
                    self._store.rebuild_index()
                return {
                    "status": "already_archived",
                    **current.to_dict(),
                    "hot_records_removed": removed,
                }
            if current.state == "deleted":
                raise RuntimeError("Deleted mission memory cannot be archived")
            records = self._store.evidence_store.list_records(mission_id=mission_id)
            if not records:
                raise ValueError(f"No mission memory records found: {mission_id}")
            archive_id = hashlib.sha256(
                f"{self.runtime_mode}:{mission_id}".encode("utf-8")
            ).hexdigest()[:32]
            archive_path = self._archive_path(archive_id)
            content_sha256, sensitivity_counts = self._write_archive(
                archive_path,
                mission_id=mission_id,
                archive_id=archive_id,
                records=records,
                mission_status=mission_status,
            )
            occurred_at = _utc_now()
            transition = {
                "kind": "transition",
                "transition_id": uuid.uuid4().hex,
                "mission_id": mission_id,
                "runtime_mode": self.runtime_mode,
                "from_state": "active",
                "to_state": "audit_only",
                "actor_id": actor_id,
                "reason": reason,
                "occurred_at": occurred_at,
                "archive_id": archive_id,
                "record_count": len(records),
                "content_sha256": content_sha256,
                "sensitivity_counts": sensitivity_counts,
                "mission_status": mission_status,
            }
            self._append_jsonl(self.lifecycle_path, transition)
            removed = self._store.evidence_store.purge_mission(mission_id)
            if self._store.index is not None:
                self._store.rebuild_index()
            return {
                "status": "archived",
                **self.state(mission_id).to_dict(),
                "hot_records_removed": removed,
                "sensitivity_counts": sensitivity_counts,
            }

    def read_audit(
        self,
        mission_id: str,
        *,
        include_restricted: bool,
        limit: int = 200,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        state = self.state(mission_id)
        if state.state == "active":
            raise RuntimeError("Active mission memory must be read through mission memory tools")
        if state.state == "deleted":
            return {
                "status": "deleted",
                "lifecycle": state.to_dict(),
                "records": [],
                "count": 0,
                "restricted_records_omitted": 0,
            }
        archive_path = self._archive_path(state.archive_id or "")
        manifest, records = self._read_archive(archive_path)
        visible: list[dict[str, Any]] = []
        omitted = 0
        restricted_ids = {
            record.record_id
            for record in records
            if _record_sensitivity(record) == "restricted"
        }
        for record in records:
            if (
                _record_sensitivity(record) == "restricted"
                or _relation_references(record, restricted_ids)
            ) and not include_restricted:
                omitted += 1
                continue
            visible.append(redact_dict(record.to_dict()))
        visible = visible[-limit:]
        return {
            "status": "audit_only",
            "lifecycle": state.to_dict(),
            "manifest": manifest,
            "records": visible,
            "count": len(visible),
            "restricted_records_omitted": omitted,
            "operational_use_allowed": False,
            "advisory_only": True,
        }

    def delete_audit(
        self,
        mission_id: str,
        *,
        actor_id: str,
        reason: str,
        confirmation: str,
    ) -> dict[str, Any]:
        _require_text("actor_id", actor_id)
        _require_text("reason", reason)
        if confirmation != mission_id:
            raise ValueError("confirmation must exactly match mission_id")
        with self._lock:
            current = self.state(mission_id)
            if current.state == "deleted":
                return {"status": "already_deleted", **current.to_dict()}
            if current.state != "audit_only":
                raise RuntimeError("Mission memory must be audit_only before deletion")
            archive_path = self._archive_path(current.archive_id or "")
            self._append_jsonl(self.lifecycle_path, {
                "kind": "deletion_intent",
                "intent_id": uuid.uuid4().hex,
                "mission_id": mission_id,
                "runtime_mode": self.runtime_mode,
                "actor_id": actor_id,
                "reason": reason,
                "occurred_at": _utc_now(),
                "archive_id": current.archive_id,
                "content_sha256": current.content_sha256,
            })
            archive_path.unlink(missing_ok=True)
            transition = {
                "kind": "transition",
                "transition_id": uuid.uuid4().hex,
                "mission_id": mission_id,
                "runtime_mode": self.runtime_mode,
                "from_state": "audit_only",
                "to_state": "deleted",
                "actor_id": actor_id,
                "reason": reason,
                "occurred_at": _utc_now(),
                "archive_id": current.archive_id,
                "record_count": current.record_count,
                "content_sha256": current.content_sha256,
                "tombstone": True,
                "recoverable": False,
            }
            self._append_jsonl(self.lifecycle_path, transition)
            return {"status": "deleted", **self.state(mission_id).to_dict()}

    def lifecycle_history(self, mission_id: str) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self._read_jsonl(self.lifecycle_path)
            if entry.get("mission_id") == mission_id
            and entry.get("runtime_mode") == self.runtime_mode
        ]

    def approve_knowledge(
        self,
        *,
        source_mission_id: str,
        source_event_ids: list[str] | tuple[str, ...],
        knowledge_type: str,
        title: str,
        content: dict[str, Any],
        tags: list[str] | tuple[str, ...],
        applicable_runtime_modes: list[str] | tuple[str, ...] | None = None,
        actor_id: str,
        reason: str,
        knowledge_id: str | None = None,
    ) -> ReusableKnowledgeRecord:
        if knowledge_type not in REUSABLE_KNOWLEDGE_TYPES:
            raise ValueError(
                f"Invalid knowledge_type: {knowledge_type}. "
                f"Must be one of: {sorted(REUSABLE_KNOWLEDGE_TYPES)}"
            )
        for field_name, value in (
            ("source_mission_id", source_mission_id),
            ("title", title),
            ("actor_id", actor_id),
            ("reason", reason),
        ):
            _require_text(field_name, value)
        if not isinstance(content, dict) or not content:
            raise ValueError("content must be a non-empty object")
        source_ids = tuple(dict.fromkeys(source_event_ids))
        if not source_ids or any(not isinstance(value, str) or not value.strip() for value in source_ids):
            raise ValueError("source_event_ids must contain non-empty strings")
        source_records = {
            record.record_id: record
            for record in self._mission_records(source_mission_id)
            if record.record_type != "relation"
        }
        missing = [event_id for event_id in source_ids if event_id not in source_records]
        if missing:
            raise ValueError(f"Unknown source_event_ids: {missing}")
        normalized_tags = tuple(sorted({
            tag.strip().lower()
            for tag in tags
            if isinstance(tag, str) and tag.strip()
        }))
        if len(normalized_tags) > 20:
            raise ValueError("tags cannot contain more than 20 values")
        applicable_modes = tuple(sorted(set(
            applicable_runtime_modes
            if applicable_runtime_modes is not None
            else (self.runtime_mode,)
        )))
        if not applicable_modes or set(applicable_modes) - MEMORY_RUNTIME_MODES:
            raise ValueError(
                "applicable_runtime_modes must contain valid memory runtime modes"
            )
        record = ReusableKnowledgeRecord(
            knowledge_id=knowledge_id or uuid.uuid4().hex,
            knowledge_type=knowledge_type,
            title=title.strip(),
            content=redact_dict(dict(content)),
            tags=normalized_tags,
            source_mission_id=source_mission_id,
            source_runtime_mode=self.runtime_mode,
            source_event_ids=source_ids,
            applicable_runtime_modes=applicable_modes,
            approved_by=actor_id,
            approved_at=_utc_now(),
            approval_reason=reason.strip(),
        )
        with self._lock:
            if any(
                value.get("knowledge_id") == record.knowledge_id
                for value in self._read_jsonl(self.knowledge_path)
            ):
                raise ValueError(f"Reusable knowledge id already exists: {record.knowledge_id}")
            self._append_jsonl(self.knowledge_path, {"kind": "approved", **record.to_dict()})
        return record

    def revoke_knowledge(
        self,
        knowledge_id: str,
        *,
        actor_id: str,
        reason: str,
    ) -> ReusableKnowledgeRecord:
        _require_text("knowledge_id", knowledge_id)
        _require_text("actor_id", actor_id)
        _require_text("reason", reason)
        with self._lock:
            records = {
                item.knowledge_id: item
                for item in self.list_knowledge(include_revoked=True)
            }
            current = records.get(knowledge_id)
            if current is None:
                raise KeyError(f"Reusable knowledge not found: {knowledge_id}")
            if current.status == "revoked":
                return current
            revoked = ReusableKnowledgeRecord(
                **{
                    **{key: value for key, value in asdict(current).items() if key not in {
                        "status", "revoked_by", "revoked_at", "revocation_reason"
                    }},
                    "status": "revoked",
                    "revoked_by": actor_id,
                    "revoked_at": _utc_now(),
                    "revocation_reason": reason.strip(),
                }
            )
            self._append_jsonl(
                self.knowledge_path,
                {"kind": "revoked", **revoked.to_dict()},
            )
            return revoked

    def list_knowledge(
        self,
        *,
        knowledge_type: str | None = None,
        tags: list[str] | tuple[str, ...] = (),
        include_revoked: bool = False,
        limit: int = 100,
    ) -> list[ReusableKnowledgeRecord]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if knowledge_type is not None and knowledge_type not in REUSABLE_KNOWLEDGE_TYPES:
            raise ValueError(f"Invalid knowledge_type: {knowledge_type}")
        latest: dict[str, ReusableKnowledgeRecord] = {}
        for value in self._read_jsonl(self.knowledge_path):
            try:
                record = _knowledge_from_dict(value)
            except ValueError:
                continue
            latest[record.knowledge_id] = record
        required_tags = {tag.strip().lower() for tag in tags if isinstance(tag, str) and tag.strip()}
        output = [
            record
            for record in latest.values()
            if (include_revoked or record.status == "approved")
            and self.runtime_mode in record.applicable_runtime_modes
            and (knowledge_type is None or record.knowledge_type == knowledge_type)
            and required_tags.issubset(set(record.tags))
        ]
        output.sort(key=lambda record: (record.approved_at, record.knowledge_id))
        return output[-limit:]

    def _mission_records(self, mission_id: str) -> list[MissionMemoryRecord]:
        state = self.state(mission_id)
        if state.state == "deleted":
            raise RuntimeError("Deleted mission evidence cannot support new reusable knowledge")
        if state.state == "active":
            return self._store.evidence_store.list_records(mission_id=mission_id)
        _, records = self._read_archive(self._archive_path(state.archive_id or ""))
        return records

    def _write_archive(
        self,
        path: Path,
        *,
        mission_id: str,
        archive_id: str,
        records: list[MissionMemoryRecord],
        mission_status: str,
    ) -> tuple[str, dict[str, int]]:
        path.parent.mkdir(parents=True, exist_ok=True)
        record_lines = [
            json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for record in records
        ]
        digest = hashlib.sha256(("\n".join(record_lines) + "\n").encode("utf-8")).hexdigest()
        sensitivity_counts: dict[str, int] = {}
        for record in records:
            sensitivity = _record_sensitivity(record)
            sensitivity_counts[sensitivity] = sensitivity_counts.get(sensitivity, 0) + 1
        manifest = {
            "kind": "manifest",
            "archive_id": archive_id,
            "mission_id": mission_id,
            "runtime_mode": self.runtime_mode,
            "mission_status": mission_status,
            "archived_at": _utc_now(),
            "record_count": len(records),
            "content_sha256": digest,
            "sensitivity_counts": sensitivity_counts,
            "format_version": 1,
        }
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n")
                for line in record_lines:
                    handle.write(json.dumps({"kind": "record", "record": json.loads(line)}, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return digest, sensitivity_counts

    def _read_archive(
        self,
        path: Path,
    ) -> tuple[dict[str, Any], list[MissionMemoryRecord]]:
        if not path.exists():
            raise RuntimeError("Mission audit archive is missing")
        entries = self._read_jsonl(path)
        manifest = next((entry for entry in entries if entry.get("kind") == "manifest"), None)
        if manifest is None:
            raise RuntimeError("Mission audit archive manifest is missing")
        records: list[MissionMemoryRecord] = []
        for entry in entries:
            value = entry.get("record")
            if entry.get("kind") != "record" or not isinstance(value, dict):
                continue
            records.append(_record_from_dict(value))
        canonical = [
            json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for record in records
        ]
        digest = hashlib.sha256(("\n".join(canonical) + "\n").encode("utf-8")).hexdigest()
        if digest != manifest.get("content_sha256"):
            raise RuntimeError("Mission audit archive integrity check failed")
        return redact_dict(dict(manifest)), records

    def _archive_path(self, archive_id: str) -> Path:
        if not archive_id or any(character not in "0123456789abcdef" for character in archive_id):
            raise ValueError("Invalid archive_id")
        return self.audit_dir / f"{archive_id}.jsonl"

    def _append_jsonl(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        output: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    output.append(value)
        return output


def _knowledge_from_dict(value: dict[str, Any]) -> ReusableKnowledgeRecord:
    content = value.get("content")
    tags = value.get("tags")
    source_ids = value.get("source_event_ids")
    applicable_modes = value.get("applicable_runtime_modes")
    if (
        not isinstance(content, dict)
        or not isinstance(tags, list)
        or not isinstance(source_ids, list)
        or not isinstance(applicable_modes, list)
    ):
        raise ValueError("Invalid reusable knowledge record")
    return ReusableKnowledgeRecord(
        knowledge_id=str(value.get("knowledge_id") or ""),
        knowledge_type=str(value.get("knowledge_type") or ""),
        title=str(value.get("title") or ""),
        content=content,
        tags=tuple(str(item) for item in tags),
        source_mission_id=str(value.get("source_mission_id") or ""),
        source_runtime_mode=str(value.get("source_runtime_mode") or ""),
        source_event_ids=tuple(str(item) for item in source_ids),
        applicable_runtime_modes=tuple(str(item) for item in applicable_modes),
        approved_by=str(value.get("approved_by") or ""),
        approved_at=str(value.get("approved_at") or ""),
        approval_reason=str(value.get("approval_reason") or ""),
        status=str(value.get("status") or "approved"),
        revoked_by=_optional_text(value.get("revoked_by")),
        revoked_at=_optional_text(value.get("revoked_at")),
        revocation_reason=_optional_text(value.get("revocation_reason")),
    )


def _record_from_dict(value: dict[str, Any]) -> MissionMemoryRecord:
    content = value.get("content")
    return MissionMemoryRecord(
        record_id=str(value.get("record_id") or ""),
        mission_id=str(value.get("mission_id") or ""),
        record_type=str(value.get("record_type") or ""),
        content=content if isinstance(content, dict) else {},
        robot_id=_optional_text(value.get("robot_id")),
        subtask_id=_optional_text(value.get("subtask_id")),
        created_at=str(value.get("created_at") or ""),
    )


def _record_sensitivity(record: MissionMemoryRecord) -> str:
    metadata = record.content.get(EMBODIED_METADATA_KEY)
    if isinstance(metadata, dict) and metadata.get("sensitivity") == "restricted":
        return "restricted"
    return "standard"


def _relation_references(record: MissionMemoryRecord, record_ids: set[str]) -> bool:
    if record.record_type != "relation":
        return False
    relation = record.content.get(RELATION_METADATA_KEY)
    if not isinstance(relation, dict):
        return False
    return any(
        relation.get(field_name) in record_ids
        for field_name in ("source_record_id", "target_record_id")
    )


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _nonnegative_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
