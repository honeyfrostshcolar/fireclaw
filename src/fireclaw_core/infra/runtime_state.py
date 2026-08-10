from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
from typing import Any, Callable, Iterator
from uuid import uuid4

from fireclaw_core.agent.loop_checkpoint import (
    AgentLoopCheckpoint,
    JsonlAgentLoopCheckpointStore,
)
from fireclaw_core.monitoring.event_ledger import EventLedger
from fireclaw_core.task.task_queue import (
    JsonlTaskQueue,
    TERMINAL_TASK_STATUSES,
    TaskQueueRecord,
)


class StaleRuntimeStateWrite(RuntimeError):
    """Raised when a caller tries to update an obsolete authoritative revision."""


class ResourceLeaseConflict(RuntimeError):
    """Raised when another operation owns a required robot resource."""

    def __init__(
        self,
        *,
        robot_id: str,
        resource_name: str,
        owner_id: str,
        operation_id: str,
    ) -> None:
        super().__init__(
            f"Robot resource {robot_id}:{resource_name} is held by "
            f"owner={owner_id} operation={operation_id}"
        )
        self.robot_id = robot_id
        self.resource_name = resource_name
        self.owner_id = owner_id
        self.operation_id = operation_id


@dataclass(frozen=True)
class ResourceLease:
    robot_id: str
    resource_name: str
    owner_id: str
    operation_id: str
    generation: int
    acquired_at: str
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "resource_name": self.resource_name,
            "owner_id": self.owner_id,
            "operation_id": self.operation_id,
            "generation": self.generation,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
        }


class SqliteAuthoritativeRuntimeStore:
    """Shared SQLite WAL authority for one Robot Gateway.

    JSONL files remain audit/export mirrors. Runtime decisions are made only
    from rows committed in this database.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._audit_lock = threading.RLock()
        self._visibility_lock = threading.RLock()
        self._initialize()
        self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    dedupe_key TEXT,
                    error TEXT,
                    result_json TEXT,
                    revision INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tasks_dedupe_idx
                    ON tasks(dedupe_key, status);
                CREATE TABLE IF NOT EXISTS task_transitions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_task_idx
                    ON events(task_id, sequence);
                CREATE INDEX IF NOT EXISTS events_session_idx
                    ON events(session_id, sequence);
                CREATE TABLE IF NOT EXISTS loop_checkpoints (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    checkpoint_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS loop_checkpoints_key_idx
                    ON loop_checkpoints(checkpoint_key, sequence);
                CREATE TABLE IF NOT EXISTS authorization_requests (
                    request_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    revision INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS authorization_session_idx
                    ON authorization_requests(session_id, status);
                CREATE TABLE IF NOT EXISTS execution_authorizations (
                    authorization_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    revision INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS authorization_uses (
                    authorization_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    action_hash TEXT NOT NULL,
                    used_at TEXT NOT NULL,
                    PRIMARY KEY(authorization_id, operation_id),
                    FOREIGN KEY(authorization_id)
                        REFERENCES execution_authorizations(authorization_id)
                );
                CREATE TABLE IF NOT EXISTS resource_leases (
                    robot_id TEXT NOT NULL,
                    resource_name TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY(robot_id, resource_name)
                );
                CREATE TABLE IF NOT EXISTS runtime_flags (
                    key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    revision INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_secrets (
                    name TEXT PRIMARY KEY,
                    value BLOB NOT NULL
                );
                """
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        active = getattr(self._local, "connection", None)
        if active is not None:
            self._local.depth += 1
            try:
                yield active
            finally:
                self._local.depth -= 1
            return

        with self._visibility_lock:
            connection = self._connect()
            self._local.connection = connection
            self._local.depth = 1
            self._local.after_commit = []
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
                callbacks = list(self._local.after_commit)
            except Exception:
                connection.rollback()
                raise
            finally:
                self._local.connection = None
                self._local.depth = 0
                self._local.after_commit = []
                connection.close()
            for callback in callbacks:
                callback()

    def after_commit(self, callback: Callable[[], None]) -> None:
        if getattr(self._local, "connection", None) is None:
            callback()
            return
        self._local.after_commit.append(callback)

    def read(self, query: str, parameters: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        active = getattr(self._local, "connection", None)
        if active is not None:
            return list(active.execute(query, parameters))
        with self._visibility_lock:
            with self._connect() as connection:
                return list(connection.execute(query, parameters))

    def append_audit_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        encoded = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        with self._audit_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            path.chmod(0o600)

    def get_or_create_secret(self, name: str, *, size: int = 32) -> bytes:
        if not name.strip():
            raise ValueError("runtime secret name must not be empty")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT value FROM runtime_secrets WHERE name = ?",
                (name,),
            ).fetchone()
            if row is not None:
                return bytes(row["value"])
            value = secrets.token_bytes(size)
            connection.execute(
                "INSERT INTO runtime_secrets(name, value) VALUES (?, ?)",
                (name, value),
            )
            return value

    def set_flag(self, key: str, payload: dict[str, Any]) -> int:
        encoded = _json(payload)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT revision FROM runtime_flags WHERE key = ?",
                (key,),
            ).fetchone()
            revision = int(row["revision"]) + 1 if row is not None else 1
            connection.execute(
                """
                INSERT INTO runtime_flags(key, payload_json, revision)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    revision = excluded.revision
                """,
                (key, encoded, revision),
            )
            return revision

    def get_flag(self, key: str) -> dict[str, Any] | None:
        rows = self.read(
            "SELECT payload_json FROM runtime_flags WHERE key = ?",
            (key,),
        )
        return _object(rows[0]["payload_json"]) if rows else None

    def create_authorization_request(self, payload: dict[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO authorization_requests(
                    request_id, session_id, task_id, status,
                    payload_json, revision
                ) VALUES (?, ?, ?, ?, ?, 1)
                """,
                (
                    str(payload["request_id"]),
                    str(payload["session_id"]),
                    str(payload["task_id"]),
                    str(payload.get("status") or "pending"),
                    _json(payload),
                ),
            )

    def pending_authorization_request(
        self,
        session_id: str,
    ) -> dict[str, Any] | None:
        rows = self.read(
            """
            SELECT payload_json
            FROM authorization_requests
            WHERE session_id = ? AND status = 'pending'
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (session_id,),
        )
        return _object(rows[0]["payload_json"]) if rows else None

    def pending_authorization_request_for_task(
        self,
        task_id: str,
    ) -> dict[str, Any] | None:
        rows = self.read(
            """
            SELECT payload_json
            FROM authorization_requests
            WHERE task_id = ? AND status = 'pending'
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (task_id,),
        )
        return _object(rows[0]["payload_json"]) if rows else None

    def resolve_authorization_request(
        self,
        request_id: str,
        *,
        status: str,
        decided_by: dict[str, Any],
        decided_at: str,
        expected_revision: int = 1,
    ) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT payload_json, revision, status
                FROM authorization_requests
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Authorization request not found: {request_id}")
            if int(row["revision"]) != expected_revision or row["status"] != "pending":
                raise StaleRuntimeStateWrite(
                    "Authorization request changed before the decision was committed"
                )
            payload = _object(row["payload_json"])
            payload.update(
                {
                    "status": status,
                    "decided_by": dict(decided_by),
                    "decided_at": decided_at,
                }
            )
            connection.execute(
                """
                UPDATE authorization_requests
                SET status = ?, payload_json = ?, revision = revision + 1
                WHERE request_id = ? AND revision = ?
                """,
                (status, _json(payload), request_id, expected_revision),
            )
            return payload

    def persist_execution_authorization(self, payload: dict[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO execution_authorizations(
                    authorization_id, status, payload_json, revision
                ) VALUES (?, 'active', ?, 1)
                """,
                (str(payload["authorization_id"]), _json(payload)),
            )

    def record_authorization_use(
        self,
        *,
        authorization_id: str,
        operation_id: str,
        action_hash: str,
        used_at: str,
    ) -> bool:
        """Consume one authorized physical operation before its side effect.

        A repeated operation id fails closed even when its action hash is
        unchanged. Physical effects are not assumed to be idempotent; crash
        recovery must reconcile the prepared operation instead of replaying it.
        """

        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT action_hash
                FROM authorization_uses
                WHERE authorization_id = ? AND operation_id = ?
                """,
                (authorization_id, operation_id),
            ).fetchone()
            if row is not None:
                return False
            auth = connection.execute(
                """
                SELECT status
                FROM execution_authorizations
                WHERE authorization_id = ?
                """,
                (authorization_id,),
            ).fetchone()
            if auth is None or auth["status"] != "active":
                return False
            connection.execute(
                """
                INSERT INTO authorization_uses(
                    authorization_id, operation_id, action_hash, used_at
                ) VALUES (?, ?, ?, ?)
                """,
                (authorization_id, operation_id, action_hash, used_at),
            )
            return True


class SqliteTaskQueue:
    def __init__(
        self,
        store: SqliteAuthoritativeRuntimeStore,
        *,
        audit_path: str | Path,
    ) -> None:
        self.store = store
        self.path = Path(audit_path)
        self._import_legacy()

    def _import_legacy(self) -> None:
        if self.store.read("SELECT task_id FROM tasks LIMIT 1") or not self.path.exists():
            return
        for record in JsonlTaskQueue(self.path).list_records():
            self._insert_imported(record)

    def _insert_imported(self, record: TaskQueueRecord) -> None:
        payload = record.to_dict()
        with self.store.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO tasks(
                    task_id, session_id, command, status, created_at,
                    started_at, ended_at, dedupe_key, error, result_json,
                    revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                _task_values(record, revision=False),
            )
            connection.execute(
                """
                INSERT INTO task_transitions(task_id, revision, payload_json)
                VALUES (?, 1, ?)
                """,
                (record.task_id, _json(payload)),
            )

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
        with self.store.transaction() as connection:
            connection.execute(
                """
                INSERT INTO tasks(
                    task_id, session_id, command, status, created_at,
                    started_at, ended_at, dedupe_key, error, result_json,
                    revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                _task_values(record, revision=False),
            )
            connection.execute(
                """
                INSERT INTO task_transitions(task_id, revision, payload_json)
                VALUES (?, 1, ?)
                """,
                (task_id, _json(record.to_dict())),
            )
            self.store.after_commit(
                lambda: self.store.append_audit_jsonl(
                    self.path,
                    record.to_dict(),
                )
            )
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
        expected_revision: int | None = None,
    ) -> TaskQueueRecord:
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Task queue record not found: {task_id}")
            current_revision = int(row["revision"])
            if (
                expected_revision is not None
                and expected_revision != current_revision
            ):
                raise StaleRuntimeStateWrite(
                    f"Task {task_id} revision changed before update"
                )
            current = _task_record(row)
            if current.is_terminal and status != current.status:
                raise StaleRuntimeStateWrite(
                    f"Task {task_id} is already terminal: {current.status}"
                )
            record = TaskQueueRecord(
                task_id=current.task_id,
                session_id=current.session_id,
                command=current.command,
                status=status,
                created_at=current.created_at,
                started_at=(
                    started_at if started_at is not None else current.started_at
                ),
                ended_at=ended_at if ended_at is not None else current.ended_at,
                dedupe_key=current.dedupe_key,
                error=error if error is not None else current.error,
                result=result if result is not None else current.result,
            )
            revision = current_revision + 1
            changed = connection.execute(
                """
                UPDATE tasks SET
                    status = ?, started_at = ?, ended_at = ?,
                    error = ?, result_json = ?, revision = ?
                WHERE task_id = ? AND revision = ?
                """,
                (
                    record.status,
                    record.started_at,
                    record.ended_at,
                    record.error,
                    _json(record.result) if record.result is not None else None,
                    revision,
                    task_id,
                    current_revision,
                ),
            )
            if changed.rowcount != 1:
                raise StaleRuntimeStateWrite(
                    f"Task {task_id} changed during update"
                )
            connection.execute(
                """
                INSERT INTO task_transitions(task_id, revision, payload_json)
                VALUES (?, ?, ?)
                """,
                (task_id, revision, _json(record.to_dict())),
            )
            self.store.after_commit(
                lambda: self.store.append_audit_jsonl(
                    self.path,
                    record.to_dict(),
                )
            )
            return record

    def revision(self, task_id: str) -> int | None:
        rows = self.store.read(
            "SELECT revision FROM tasks WHERE task_id = ?",
            (task_id,),
        )
        return int(rows[0]["revision"]) if rows else None

    def get(self, task_id: str) -> TaskQueueRecord | None:
        rows = self.store.read(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        )
        return _task_record(rows[0]) if rows else None

    def list_records(self) -> list[TaskQueueRecord]:
        return [
            _task_record(row)
            for row in self.store.read(
                "SELECT * FROM tasks ORDER BY rowid"
            )
        ]

    def find_non_terminal_by_dedupe_key(
        self,
        dedupe_key: str | None,
    ) -> TaskQueueRecord | None:
        if not dedupe_key:
            return None
        placeholders = ",".join("?" for _ in TERMINAL_TASK_STATUSES)
        rows = self.store.read(
            f"""
            SELECT * FROM tasks
            WHERE dedupe_key = ? AND status NOT IN ({placeholders})
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (dedupe_key, *sorted(TERMINAL_TASK_STATUSES)),
        )
        return _task_record(rows[0]) if rows else None

    def mark_non_terminal_lost(
        self,
        *,
        ended_at: str,
        error: str,
    ) -> list[TaskQueueRecord]:
        lost: list[TaskQueueRecord] = []
        for record in self.list_records():
            if not record.is_terminal:
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
        terminal = [record for record in self.list_records() if record.is_terminal]
        terminal.sort(key=lambda record: record.created_at)
        remove = terminal[: max(0, len(terminal) - keep_terminal)]
        if not remove:
            return 0
        with self.store.transaction() as connection:
            for record in remove:
                connection.execute(
                    "DELETE FROM task_transitions WHERE task_id = ?",
                    (record.task_id,),
                )
                connection.execute(
                    "DELETE FROM tasks WHERE task_id = ?",
                    (record.task_id,),
                )
        return len(remove)

    def summary(self) -> dict[str, Any]:
        records = self.list_records()
        active = [record for record in records if not record.is_terminal]
        return {
            "task_count": len(records),
            "active_task_count": len(active),
            "terminal_task_count": len(records) - len(active),
            "active_tasks": [record.to_dict() for record in active],
        }


class SqliteEventLedger:
    def __init__(
        self,
        store: SqliteAuthoritativeRuntimeStore,
        *,
        audit_path: str | Path,
    ) -> None:
        self.store = store
        self.path = Path(audit_path)
        self._import_legacy()

    def _import_legacy(self) -> None:
        if self.store.read("SELECT event_id FROM events LIMIT 1") or not self.path.exists():
            return
        for event in EventLedger(self.path).list_events():
            with self.store.transaction() as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO events(
                        event_id, task_id, session_id, type,
                        timestamp, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(event.get("event_id") or f"evt-{uuid4().hex}"),
                        str(event.get("task_id") or ""),
                        str(event.get("session_id") or ""),
                        str(event.get("type") or ""),
                        str(event.get("timestamp") or ""),
                        _json(
                            event.get("payload")
                            if isinstance(event.get("payload"), dict)
                            else {}
                        ),
                    ),
                )

    def append(
        self,
        *,
        task_id: str,
        session_id: str,
        type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        event = {
            "event_id": f"evt-{uuid4().hex}",
            "task_id": task_id,
            "session_id": session_id,
            "type": type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": dict(payload),
        }
        with self.store.transaction() as connection:
            connection.execute(
                """
                INSERT INTO events(
                    event_id, task_id, session_id, type,
                    timestamp, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    task_id,
                    session_id,
                    type,
                    event["timestamp"],
                    _json(event["payload"]),
                ),
            )
            self.store.after_commit(
                lambda: self.store.append_audit_jsonl(self.path, event)
            )
        return event

    def list_events(self) -> list[dict[str, Any]]:
        return [_event(row) for row in self.store.read("SELECT * FROM events ORDER BY sequence")]

    def events_for_task(self, task_id: str) -> list[dict[str, Any]]:
        return [
            _event(row)
            for row in self.store.read(
                "SELECT * FROM events WHERE task_id = ? ORDER BY sequence",
                (task_id,),
            )
        ]

    def latest_events(
        self,
        *,
        limit: int = 20,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        if session_id is None:
            rows = self.store.read(
                "SELECT * FROM events ORDER BY sequence DESC LIMIT ?",
                (limit,),
            )
        else:
            rows = self.store.read(
                """
                SELECT * FROM events
                WHERE session_id = ?
                ORDER BY sequence DESC
                LIMIT ?
                """,
                (session_id, limit),
            )
        return [_event(row) for row in rows]


class SqliteAgentLoopCheckpointStore:
    def __init__(
        self,
        store: SqliteAuthoritativeRuntimeStore,
        *,
        audit_path: str | Path,
    ) -> None:
        self.store = store
        self.path = Path(audit_path)
        self._import_legacy()

    def _import_legacy(self) -> None:
        if (
            self.store.read("SELECT checkpoint_key FROM loop_checkpoints LIMIT 1")
            or not self.path.exists()
        ):
            return
        for checkpoint in JsonlAgentLoopCheckpointStore(
            self.path
        ).latest_by_key().values():
            with self.store.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO loop_checkpoints(checkpoint_key, payload_json)
                    VALUES (?, ?)
                    """,
                    (checkpoint.checkpoint_key, _json(checkpoint.to_dict())),
                )

    def append(self, checkpoint: AgentLoopCheckpoint) -> None:
        payload = checkpoint.to_dict()
        with self.store.transaction() as connection:
            connection.execute(
                """
                INSERT INTO loop_checkpoints(checkpoint_key, payload_json)
                VALUES (?, ?)
                """,
                (checkpoint.checkpoint_key, _json(payload)),
            )
            self.store.after_commit(
                lambda: self.store.append_audit_jsonl(self.path, payload)
            )

    def latest(self, checkpoint_key: str) -> AgentLoopCheckpoint | None:
        rows = self.store.read(
            """
            SELECT payload_json FROM loop_checkpoints
            WHERE checkpoint_key = ?
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (checkpoint_key,),
        )
        return (
            AgentLoopCheckpoint.from_dict(_object(rows[0]["payload_json"]))
            if rows
            else None
        )

    def latest_by_key(self) -> dict[str, AgentLoopCheckpoint]:
        result: dict[str, AgentLoopCheckpoint] = {}
        for row in self.store.read(
            "SELECT checkpoint_key, payload_json FROM loop_checkpoints ORDER BY sequence"
        ):
            result[str(row["checkpoint_key"])] = AgentLoopCheckpoint.from_dict(
                _object(row["payload_json"])
            )
        return result

    def recoverable(self) -> dict[str, AgentLoopCheckpoint]:
        return {
            key: checkpoint
            for key, checkpoint in self.latest_by_key().items()
            if checkpoint.is_recoverable
        }


class SqliteResourceLeaseManager:
    def __init__(self, store: SqliteAuthoritativeRuntimeStore) -> None:
        self.store = store

    def acquire(
        self,
        *,
        robot_id: str,
        resource_names: tuple[str, ...],
        owner_id: str,
        operation_id: str,
        ttl_seconds: float,
    ) -> tuple[ResourceLease, ...]:
        resources = tuple(sorted(set(resource_names)))
        if not resources:
            return ()
        if not robot_id.strip() or not owner_id.strip() or not operation_id.strip():
            raise ValueError("resource lease identity must be complete")
        now = datetime.now(timezone.utc)
        acquired_at = now.isoformat()
        expires_at = (
            now + timedelta(seconds=max(1.0, ttl_seconds))
        ).isoformat()
        leases: list[ResourceLease] = []
        with self.store.transaction() as connection:
            flag = connection.execute(
                "SELECT payload_json FROM runtime_flags WHERE key = 'resource_admission'"
            ).fetchone()
            if flag is not None and _object(flag["payload_json"]).get("closed"):
                raise ResourceLeaseConflict(
                    robot_id=robot_id,
                    resource_name="resource_admission",
                    owner_id="emergency-stop",
                    operation_id="emergency-stop",
                )
            for resource_name in resources:
                row = connection.execute(
                    """
                    SELECT * FROM resource_leases
                    WHERE robot_id = ? AND resource_name = ?
                    """,
                    (robot_id, resource_name),
                ).fetchone()
                generation = 1
                if row is not None:
                    current = _resource_lease(row)
                    if (
                        current.owner_id == owner_id
                        and current.operation_id == operation_id
                        and datetime.fromisoformat(current.expires_at) > now
                    ):
                        generation = current.generation
                    elif datetime.fromisoformat(current.expires_at) > now:
                        raise ResourceLeaseConflict(
                            robot_id=robot_id,
                            resource_name=resource_name,
                            owner_id=current.owner_id,
                            operation_id=current.operation_id,
                        )
                    else:
                        generation = current.generation + 1
                lease = ResourceLease(
                    robot_id=robot_id,
                    resource_name=resource_name,
                    owner_id=owner_id,
                    operation_id=operation_id,
                    generation=generation,
                    acquired_at=acquired_at,
                    expires_at=expires_at,
                )
                connection.execute(
                    """
                    INSERT INTO resource_leases(
                        robot_id, resource_name, owner_id, operation_id,
                        generation, acquired_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(robot_id, resource_name) DO UPDATE SET
                        owner_id = excluded.owner_id,
                        operation_id = excluded.operation_id,
                        generation = excluded.generation,
                        acquired_at = excluded.acquired_at,
                        expires_at = excluded.expires_at
                    """,
                    (
                        lease.robot_id,
                        lease.resource_name,
                        lease.owner_id,
                        lease.operation_id,
                        lease.generation,
                        lease.acquired_at,
                        lease.expires_at,
                    ),
                )
                leases.append(lease)
        return tuple(leases)

    def release(
        self,
        *,
        robot_id: str,
        owner_id: str,
        operation_id: str,
        resource_names: tuple[str, ...] | None = None,
        generations: dict[str, int] | None = None,
    ) -> int:
        with self.store.transaction() as connection:
            if generations is not None:
                released = 0
                selected = (
                    set(resource_names)
                    if resource_names is not None
                    else set(generations)
                )
                for resource_name in sorted(selected):
                    generation = generations.get(resource_name)
                    if generation is None:
                        continue
                    result = connection.execute(
                        """
                        DELETE FROM resource_leases
                        WHERE robot_id = ? AND resource_name = ?
                          AND owner_id = ? AND operation_id = ?
                          AND generation = ?
                        """,
                        (
                            robot_id,
                            resource_name,
                            owner_id,
                            operation_id,
                            generation,
                        ),
                    )
                    released += int(result.rowcount)
                return released
            parameters: list[Any] = [robot_id, owner_id, operation_id]
            resource_clause = ""
            if resource_names:
                names = tuple(sorted(set(resource_names)))
                resource_clause = (
                    " AND resource_name IN ("
                    + ",".join("?" for _ in names)
                    + ")"
                )
                parameters.extend(names)
            result = connection.execute(
                """
                DELETE FROM resource_leases
                WHERE robot_id = ? AND owner_id = ? AND operation_id = ?
                """
                + resource_clause,
                tuple(parameters),
            )
            return int(result.rowcount)

    def active(self, *, robot_id: str | None = None) -> list[ResourceLease]:
        now = datetime.now(timezone.utc).isoformat()
        if robot_id is None:
            rows = self.store.read(
                """
                SELECT * FROM resource_leases
                WHERE expires_at > ?
                ORDER BY robot_id, resource_name
                """,
                (now,),
            )
        else:
            rows = self.store.read(
                """
                SELECT * FROM resource_leases
                WHERE robot_id = ? AND expires_at > ?
                ORDER BY resource_name
                """,
                (robot_id, now),
            )
        return [_resource_lease(row) for row in rows]

    def close_admission(
        self,
        *,
        reason: str | None,
        task_id: str,
        closed_at: str,
    ) -> None:
        self.store.set_flag(
            "resource_admission",
            {
                "closed": True,
                "reason": reason,
                "task_id": task_id,
                "closed_at": closed_at,
            },
        )

    def admission_state(self) -> dict[str, Any]:
        return self.store.get_flag("resource_admission") or {"closed": False}


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("authoritative runtime payload must be an object")
    return parsed


def _task_values(
    record: TaskQueueRecord,
    *,
    revision: bool,
) -> tuple[Any, ...]:
    values: tuple[Any, ...] = (
        record.task_id,
        record.session_id,
        record.command,
        record.status,
        record.created_at,
        record.started_at,
        record.ended_at,
        record.dedupe_key,
        record.error,
        _json(record.result) if record.result is not None else None,
    )
    return (*values, 1) if revision else values


def _task_record(row: sqlite3.Row) -> TaskQueueRecord:
    result = json.loads(row["result_json"]) if row["result_json"] else None
    return TaskQueueRecord(
        task_id=str(row["task_id"]),
        session_id=str(row["session_id"]),
        command=str(row["command"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        dedupe_key=row["dedupe_key"],
        error=row["error"],
        result=result if isinstance(result, dict) else None,
    )


def _event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": str(row["event_id"]),
        "task_id": str(row["task_id"]),
        "session_id": str(row["session_id"]),
        "type": str(row["type"]),
        "timestamp": str(row["timestamp"]),
        "payload": _object(row["payload_json"]),
    }


def _resource_lease(row: sqlite3.Row) -> ResourceLease:
    return ResourceLease(
        robot_id=str(row["robot_id"]),
        resource_name=str(row["resource_name"]),
        owner_id=str(row["owner_id"]),
        operation_id=str(row["operation_id"]),
        generation=int(row["generation"]),
        acquired_at=str(row["acquired_at"]),
        expires_at=str(row["expires_at"]),
    )
