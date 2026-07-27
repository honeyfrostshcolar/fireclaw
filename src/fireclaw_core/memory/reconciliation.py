"""Incremental robot-to-mission embodied-memory replication and reconciliation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
from typing import Any

from fireclaw_core.memory.embodied_memory import (
    EMBODIED_METADATA_KEY,
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryEvent,
    EmbodiedMemoryProductionPolicy,
    EmbodiedMemoryRelation,
    EmbodiedMemoryStore,
)
from fireclaw_core.memory.replication_security import (
    ReplicationAuthMetadata,
    ReplicationNonceCache,
    ReplicationPeerPolicy,
    ReplicationRequestScope,
    verify_signed_batch,
    verify_signed_payload,
)
from fireclaw_core.mission.mission_memory import MissionMemoryRecord


REPLICATION_SCHEMA_VERSION = 1
REPLICATION_SCHEMA_VERSION_V2 = 2


@dataclass(frozen=True)
class ReplicationEnvelope:
    source_store_id: str
    source_robot_id: str
    source_sequence: int
    source_record_id: str
    mission_id: str
    runtime_mode: str
    record_kind: str
    checksum: str
    record: dict[str, Any]
    schema_version: int = REPLICATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("source_store_id", self.source_store_id),
            ("source_robot_id", self.source_robot_id),
            ("source_record_id", self.source_record_id),
            ("mission_id", self.mission_id),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.source_sequence <= 0:
            raise ValueError("source_sequence must be positive")
        if self.runtime_mode not in MEMORY_RUNTIME_MODES:
            raise ValueError(f"Invalid runtime_mode: {self.runtime_mode}")
        if self.record_kind not in {"event", "relation"}:
            raise ValueError("record_kind must be event or relation")
        if self.schema_version != REPLICATION_SCHEMA_VERSION:
            raise ValueError(f"Unsupported replication schema: {self.schema_version}")
        if not isinstance(self.record, dict):
            raise ValueError("record must be an object")
        if self.checksum != _record_checksum(self.record):
            raise ValueError("replication record checksum mismatch")

    @property
    def origin_key(self) -> str:
        return f"{self.source_store_id}:{self.source_record_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_store_id": self.source_store_id,
            "source_robot_id": self.source_robot_id,
            "source_sequence": self.source_sequence,
            "source_record_id": self.source_record_id,
            "mission_id": self.mission_id,
            "runtime_mode": self.runtime_mode,
            "record_kind": self.record_kind,
            "checksum": self.checksum,
            "record": dict(self.record),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReplicationEnvelope":
        if not isinstance(payload, dict):
            raise ValueError("replication envelope must be an object")
        record = payload.get("record")
        if not isinstance(record, dict):
            raise ValueError("replication envelope record must be an object")
        return cls(
            schema_version=int(payload.get("schema_version", 0)),
            source_store_id=str(payload.get("source_store_id") or ""),
            source_robot_id=str(payload.get("source_robot_id") or ""),
            source_sequence=int(payload.get("source_sequence", 0)),
            source_record_id=str(payload.get("source_record_id") or ""),
            mission_id=str(payload.get("mission_id") or ""),
            runtime_mode=str(payload.get("runtime_mode") or ""),
            record_kind=str(payload.get("record_kind") or ""),
            checksum=str(payload.get("checksum") or ""),
            record=dict(record),
        )


@dataclass(frozen=True)
class ReplicationBatch:
    source_store_id: str
    source_robot_id: str
    cursor: int
    next_cursor: int
    has_more: bool
    envelopes: tuple[ReplicationEnvelope, ...]
    protocol_version: int = REPLICATION_SCHEMA_VERSION
    policy_id: str | None = None
    omission_counts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_store_id.strip() or not self.source_robot_id.strip():
            raise ValueError("replication batch source identities must not be empty")
        if self.cursor < 0 or self.next_cursor < self.cursor:
            raise ValueError("invalid replication batch cursor range")
        if self.protocol_version == REPLICATION_SCHEMA_VERSION_V2:
            if not self.policy_id:
                raise ValueError("v2 batch requires a non-empty policy_id")
            for reason, count in self.omission_counts.items():
                if not reason.strip():
                    raise ValueError("omission reason must not be empty")
                if count < 0:
                    raise ValueError(f"omission count must be non-negative: {reason}")
            if len(self.omission_counts) > 32:
                raise ValueError("too many omission reason keys")
        elif self.protocol_version == REPLICATION_SCHEMA_VERSION:
            if self.policy_id is not None:
                raise ValueError("v1 batch must not have policy_id")
            if self.omission_counts:
                raise ValueError("v1 batch must not have omission_counts")

    def to_dict(self) -> dict[str, Any]:
        result = {
            "source_store_id": self.source_store_id,
            "source_robot_id": self.source_robot_id,
            "cursor": self.cursor,
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
            "envelopes": [item.to_dict() for item in self.envelopes],
        }
        if self.protocol_version != REPLICATION_SCHEMA_VERSION:
            result["protocol_version"] = self.protocol_version
        if self.policy_id is not None:
            result["policy_id"] = self.policy_id
        if self.omission_counts:
            result["omission_counts"] = dict(self.omission_counts)
        return result

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReplicationBatch":
        if not isinstance(payload, dict):
            raise ValueError("replication batch must be an object")
        raw = payload.get("envelopes")
        if not isinstance(raw, list):
            raise ValueError("replication batch envelopes must be a list")
        if any(not isinstance(item, dict) for item in raw):
            raise ValueError("replication batch envelopes must contain objects")
        raw_omission = payload.get("omission_counts", {})
        return cls(
            source_store_id=str(payload.get("source_store_id") or ""),
            source_robot_id=str(payload.get("source_robot_id") or ""),
            cursor=int(payload.get("cursor", 0)),
            next_cursor=int(payload.get("next_cursor", 0)),
            has_more=bool(payload.get("has_more", False)),
            envelopes=tuple(ReplicationEnvelope.from_dict(item) for item in raw),
            protocol_version=int(payload.get("protocol_version", REPLICATION_SCHEMA_VERSION)),
            policy_id=str(payload["policy_id"]) if payload.get("policy_id") is not None else None,
            omission_counts=dict(raw_omission) if isinstance(raw_omission, dict) else {},
        )


@dataclass(frozen=True)
class ReconciliationReport:
    source_store_id: str
    source_robot_id: str
    previous_cursor: int
    next_cursor: int
    imported: int
    duplicates: int
    pending_relations: int
    conflicts: int
    rejected: int
    resolved_pending_relations: int

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class EmbodiedMemoryReplicationExporter:
    """Exports ordered embodied records using an absolute source-store cursor."""

    def __init__(
        self,
        *,
        store: EmbodiedMemoryStore,
        source_store_id: str,
        source_robot_id: str,
    ) -> None:
        if not source_store_id.strip() or not source_robot_id.strip():
            raise ValueError("replication source store and robot ids must not be empty")
        self._store = store
        self.source_store_id = source_store_id
        self.source_robot_id = source_robot_id

    def export_batch(
        self,
        *,
        cursor: int = 0,
        limit: int = 200,
        mission_id: str | None = None,
        runtime_mode: str | None = None,
        peer_policy: ReplicationPeerPolicy | None = None,
    ) -> ReplicationBatch:
        if cursor < 0:
            raise ValueError("cursor cannot be negative")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if runtime_mode is not None and runtime_mode not in MEMORY_RUNTIME_MODES:
            raise ValueError(f"Invalid runtime_mode: {runtime_mode}")
        records = self._store.evidence_store.list_records()
        position = min(cursor, len(records))
        envelopes: list[ReplicationEnvelope] = []
        exportable_ids: set[str] | None = None
        if peer_policy is not None:
            exportable_ids = set()
            for record in records:
                env = self._record_envelope(record, source_sequence=1)
                if env is None:
                    continue
                sensitivity = self._record_sensitivity(record)
                if peer_policy.is_event_exportable(
                    robot_id=env.source_robot_id,
                    runtime_mode=env.runtime_mode,
                    sensitivity=sensitivity,
                ):
                    exportable_ids.add(env.source_record_id)
        while position < len(records) and len(envelopes) < limit:
            record = records[position]
            position += 1
            envelope = self._record_envelope(record, source_sequence=position)
            if envelope is None:
                continue
            if mission_id is not None and envelope.mission_id != mission_id:
                continue
            if runtime_mode is not None and envelope.runtime_mode != runtime_mode:
                continue
            if peer_policy is not None:
                sensitivity = self._record_sensitivity(record)
                if not peer_policy.is_event_exportable(
                    robot_id=envelope.source_robot_id,
                    runtime_mode=envelope.runtime_mode,
                    sensitivity=sensitivity,
                ):
                    continue
                if (
                    envelope.record_kind == "relation"
                    and exportable_ids is not None
                ):
                    relation_meta = record.content.get(EMBODIED_METADATA_KEY, {})
                    rel_source = relation_meta.get("source_record_id", "")
                    rel_target = relation_meta.get("target_record_id", "")
                    if (
                        rel_source not in exportable_ids
                        or rel_target not in exportable_ids
                    ):
                        continue
            envelopes.append(envelope)
        has_more = any(
            self._matches(record, mission_id=mission_id, runtime_mode=runtime_mode)
            for record in records[position:]
        )
        if peer_policy is not None:
            return ReplicationBatch(
                source_store_id=self.source_store_id,
                source_robot_id=self.source_robot_id,
                cursor=cursor,
                next_cursor=position,
                has_more=has_more,
                envelopes=tuple(envelopes),
                protocol_version=REPLICATION_SCHEMA_VERSION_V2,
                policy_id=peer_policy.policy_id,
            )
        return ReplicationBatch(
            source_store_id=self.source_store_id,
            source_robot_id=self.source_robot_id,
            cursor=cursor,
            next_cursor=position,
            has_more=has_more,
            envelopes=tuple(envelopes),
        )

    def _matches(
        self,
        record: MissionMemoryRecord,
        *,
        mission_id: str | None,
        runtime_mode: str | None,
    ) -> bool:
        envelope = self._record_envelope(record, source_sequence=1)
        return (
            envelope is not None
            and (mission_id is None or envelope.mission_id == mission_id)
            and (runtime_mode is None or envelope.runtime_mode == runtime_mode)
        )

    def _record_sensitivity(self, record: MissionMemoryRecord) -> str:
        metadata = record.content.get(EMBODIED_METADATA_KEY)
        if isinstance(metadata, dict):
            return str(metadata.get("sensitivity") or "standard")
        return "standard"

    def _record_envelope(
        self,
        record: MissionMemoryRecord,
        *,
        source_sequence: int,
    ) -> ReplicationEnvelope | None:
        metadata = record.content.get(EMBODIED_METADATA_KEY)
        if not isinstance(metadata, dict):
            return None
        runtime_mode = str(metadata.get("runtime_mode") or "")
        if runtime_mode not in MEMORY_RUNTIME_MODES:
            return None
        payload = record.to_dict()
        return ReplicationEnvelope(
            source_store_id=self.source_store_id,
            source_robot_id=self.source_robot_id,
            source_sequence=source_sequence,
            source_record_id=record.record_id,
            mission_id=record.mission_id,
            runtime_mode=runtime_mode,
            record_kind="relation" if record.record_type == "relation" else "event",
            checksum=_record_checksum(payload),
            record=payload,
        )


class EmbodiedMemoryReconciler:
    """Idempotently imports robot evidence into one mission-level store."""

    def __init__(
        self,
        *,
        destination: EmbodiedMemoryStore,
        state_path: str | Path,
        runtime_mode: str,
        allow_legacy_unsigned: bool = False,
    ) -> None:
        if runtime_mode not in MEMORY_RUNTIME_MODES:
            raise ValueError(f"Invalid runtime_mode: {runtime_mode}")
        self.destination = destination
        self.state_path = Path(state_path)
        self.runtime_mode = runtime_mode
        self.allow_legacy_unsigned = allow_legacy_unsigned
        self._lock = threading.Lock()
        self._production_policy = EmbodiedMemoryProductionPolicy()

    def checkpoint(self, source_store_id: str, *, mission_id: str | None = None) -> int:
        latest = 0
        for entry in self._read_state():
            if entry.get("type") != "checkpoint":
                continue
            if entry.get("source_store_id") != source_store_id:
                continue
            if entry.get("mission_id") != mission_id:
                continue
            if entry.get("runtime_mode") != self.runtime_mode:
                continue
            cursor = entry.get("cursor")
            if isinstance(cursor, int):
                latest = max(latest, cursor)
        return latest

    def ingest_batch(
        self,
        batch: ReplicationBatch,
        *,
        expected_robot_id: str,
        expected_mission_id: str | None = None,
        auth: ReplicationAuthMetadata | None = None,
        key_provider: Any | None = None,
        expected_peer_id: str | None = None,
        nonce_cache: ReplicationNonceCache | None = None,
    ) -> ReconciliationReport:
        """Ingest a batch. Only allowed for explicit simulation legacy path.
        Use ingest_signed_payload for authenticated replication."""
        if batch.source_robot_id != expected_robot_id:
            raise ValueError("replication batch robot identity mismatch")
        if batch.next_cursor < batch.cursor:
            raise ValueError("replication batch next_cursor cannot move backwards")
        if auth is None:
            # Only simulation + allow_legacy_unsigned permits unsigned
            if self.runtime_mode == "real" or not self.allow_legacy_unsigned:
                raise ValueError(
                    "use ingest_signed_payload for authenticated replication"
                )
        else:
            # Auth provided but incomplete config is fail-closed
            if key_provider is None or expected_peer_id is None:
                raise ValueError(
                    "signed replication batch requires key_provider and expected_peer_id"
                )
            scope = ReplicationRequestScope(
                mission_id=expected_mission_id or "",
                runtime_mode=self.runtime_mode,
                cursor=batch.cursor,
                limit=max(1, len(batch.envelopes)),
            )
            result = verify_signed_batch(
                batch=batch,
                auth=auth,
                scope=scope,
                key_provider=key_provider,
                peer_id=expected_peer_id,
                nonce_cache=nonce_cache,
            )
            if not result.verified:
                raise ValueError(
                    f"replication batch verification failed: {result.error_code}"
                )
        return self._ingest_verified_batch(
            batch,
            expected_robot_id=expected_robot_id,
            expected_mission_id=expected_mission_id,
        )

    def ingest_signed_payload(
        self,
        payload: dict[str, Any],
        *,
        expected_robot_id: str,
        expected_mission_id: str,
        expected_scope: ReplicationRequestScope,
        key_provider: ReplicationKeyProvider,
        peer_policy: ReplicationPeerPolicy,
        nonce_cache: ReplicationNonceCache,
    ) -> ReconciliationReport:
        """Verify raw payload signature before parsing. Production entry point."""
        raw_auth = payload.get("auth")
        if not isinstance(raw_auth, dict):
            raise ValueError("signed replication payload requires auth")
        raw_batch = {key: value for key, value in payload.items() if key != "auth"}
        auth = ReplicationAuthMetadata.from_dict(raw_auth)
        verified = verify_signed_payload(
            payload=raw_batch,
            auth=auth,
            scope=expected_scope,
            key_provider=key_provider,
            peer_id=expected_robot_id,
            nonce_cache=nonce_cache,
        )
        if not verified.verified:
            raise ValueError(
                f"replication batch verification failed: {verified.error_code}"
            )
        batch = ReplicationBatch.from_dict(raw_batch)
        if batch.protocol_version == REPLICATION_SCHEMA_VERSION_V2:
            if batch.policy_id != peer_policy.policy_id:
                raise ValueError("replication batch policy_id mismatch")
        return self._ingest_verified_batch(
            batch,
            expected_robot_id=expected_robot_id,
            expected_mission_id=expected_mission_id,
            peer_policy=peer_policy,
        )

    def _ingest_verified_batch(
        self,
        batch: ReplicationBatch,
        *,
        expected_robot_id: str,
        expected_mission_id: str | None = None,
        peer_policy: ReplicationPeerPolicy | None = None,
    ) -> ReconciliationReport:
        sequences = [item.source_sequence for item in batch.envelopes]
        if sequences != sorted(sequences) or any(
            sequence <= batch.cursor or sequence > batch.next_cursor
            for sequence in sequences
        ):
            raise ValueError("replication envelope sequence is outside the batch cursor range")
        with self._lock:
            current_cursor = self.checkpoint(
                batch.source_store_id,
                mission_id=expected_mission_id,
            )
            if batch.cursor != current_cursor:
                raise ValueError(
                    f"replication cursor mismatch: expected {current_cursor}, got {batch.cursor}"
                )
            imported = duplicates = pending = conflicts = rejected = 0
            for envelope in batch.envelopes:
                if (
                    envelope.source_store_id != batch.source_store_id
                    or envelope.source_robot_id != batch.source_robot_id
                ):
                    rejected += 1
                    self._record_state("rejected", envelope, reason="batch source identity mismatch")
                    continue
                if envelope.runtime_mode != self.runtime_mode:
                    rejected += 1
                    self._record_state("rejected", envelope, reason="runtime mode mismatch")
                    continue
                if expected_mission_id is not None and envelope.mission_id != expected_mission_id:
                    rejected += 1
                    self._record_state("rejected", envelope, reason="mission id mismatch")
                    continue
                outcome = self._ingest_envelope(envelope)
                if outcome == "imported":
                    imported += 1
                elif outcome == "duplicate":
                    duplicates += 1
                elif outcome == "pending":
                    pending += 1
                elif outcome == "conflict":
                    conflicts += 1
                else:
                    rejected += 1
            resolved = self._retry_pending()
            self._append_state({
                "type": "checkpoint",
                "source_store_id": batch.source_store_id,
                "source_robot_id": batch.source_robot_id,
                "mission_id": expected_mission_id,
                "runtime_mode": self.runtime_mode,
                "cursor": batch.next_cursor,
                "updated_at": _utc_now(),
            })
            return ReconciliationReport(
                source_store_id=batch.source_store_id,
                source_robot_id=batch.source_robot_id,
                previous_cursor=batch.cursor,
                next_cursor=batch.next_cursor,
                imported=imported,
                duplicates=duplicates,
                pending_relations=pending,
                conflicts=conflicts,
                rejected=rejected,
                resolved_pending_relations=resolved,
            )

    def _ingest_envelope(self, envelope: ReplicationEnvelope) -> str:
        latest = self._latest_origin_states().get(envelope.origin_key)
        if latest is not None:
            if latest.get("checksum") == envelope.checksum and latest.get("status") == "imported":
                return "duplicate"
            if latest.get("checksum") != envelope.checksum:
                self._record_state("conflict", envelope, reason="origin checksum changed")
                return "conflict"

        record = _mission_record_from_dict(envelope.record)
        if (
            record.record_id != envelope.source_record_id
            or record.mission_id != envelope.mission_id
        ):
            self._record_state("rejected", envelope, reason="record identity mismatch")
            return "rejected"
        existing = next(
            (
                item
                for item in self.destination.evidence_store.list_records()
                if item.record_id == record.record_id
            ),
            None,
        )
        if existing is not None:
            if _record_checksum(existing.to_dict()) == envelope.checksum:
                self._record_state("imported", envelope, reason="identical destination record")
                return "duplicate"
            self._record_state("conflict", envelope, reason="destination record id collision")
            return "conflict"
        try:
            if envelope.record_kind == "event":
                event = EmbodiedMemoryEvent.from_mission_record(record)
                if (
                    event.mission_id != envelope.mission_id
                    or event.runtime_mode != envelope.runtime_mode
                ):
                    raise ValueError("event identity does not match replication envelope")
                if event.robot_id is not None and event.robot_id != envelope.source_robot_id:
                    raise ValueError("event robot identity does not match replication source")
                self._production_policy.validate(event)
                self.destination.append_event(event)
            else:
                relation = EmbodiedMemoryRelation.from_mission_record(record)
                if (
                    relation.mission_id != envelope.mission_id
                    or relation.runtime_mode != envelope.runtime_mode
                ):
                    raise ValueError("relation identity does not match replication envelope")
                if not self._relation_endpoints_exist(relation):
                    self._record_state("pending", envelope, reason="relation endpoints missing")
                    return "pending"
                self.destination.append_relation(relation)
        except ValueError as exc:
            self._record_state("rejected", envelope, reason=str(exc))
            return "rejected"
        self._record_state("imported", envelope)
        return "imported"

    def _retry_pending(self) -> int:
        resolved = 0
        latest = self._latest_origin_states()
        for entry in latest.values():
            if entry.get("status") != "pending":
                continue
            raw = entry.get("envelope")
            if not isinstance(raw, dict):
                continue
            try:
                envelope = ReplicationEnvelope.from_dict(raw)
                record = _mission_record_from_dict(envelope.record)
                relation = EmbodiedMemoryRelation.from_mission_record(record)
            except (TypeError, ValueError):
                continue
            if not self._relation_endpoints_exist(relation):
                continue
            try:
                self.destination.append_relation(relation)
            except ValueError:
                continue
            self._record_state("imported", envelope, reason="pending relation resolved")
            resolved += 1
        return resolved

    def _relation_endpoints_exist(self, relation: EmbodiedMemoryRelation) -> bool:
        ids = {event.event_id for event in self.destination.list_events(mission_id=relation.mission_id)}
        return relation.source_record_id in ids and relation.target_record_id in ids

    def _latest_origin_states(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for entry in self._read_state():
            origin_key = entry.get("origin_key")
            if isinstance(origin_key, str):
                latest[origin_key] = entry
        return latest

    def _record_state(
        self,
        status: str,
        envelope: ReplicationEnvelope,
        *,
        reason: str | None = None,
    ) -> None:
        self._append_state({
            "type": "record",
            "status": status,
            "origin_key": envelope.origin_key,
            "source_store_id": envelope.source_store_id,
            "source_robot_id": envelope.source_robot_id,
            "source_sequence": envelope.source_sequence,
            "source_record_id": envelope.source_record_id,
            "mission_id": envelope.mission_id,
            "runtime_mode": envelope.runtime_mode,
            "record_kind": envelope.record_kind,
            "checksum": envelope.checksum,
            "reason": reason,
            "envelope": envelope.to_dict() if status in {"pending", "conflict", "rejected"} else None,
            "updated_at": _utc_now(),
        })

    def _append_state(self, payload: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.state_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def _read_state(self) -> list[dict[str, Any]]:
        if not self.state_path.exists():
            return []
        output: list[dict[str, Any]] = []
        with self.state_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    output.append(value)
        return output


def _mission_record_from_dict(payload: dict[str, Any]) -> MissionMemoryRecord:
    content = payload.get("content")
    return MissionMemoryRecord(
        record_id=str(payload.get("record_id") or ""),
        mission_id=str(payload.get("mission_id") or ""),
        record_type=str(payload.get("record_type") or ""),
        content=dict(content) if isinstance(content, dict) else {},
        robot_id=str(payload["robot_id"]) if payload.get("robot_id") is not None else None,
        subtask_id=(
            str(payload["subtask_id"])
            if payload.get("subtask_id") is not None
            else None
        ),
        created_at=str(payload.get("created_at") or ""),
    )


def _record_checksum(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
