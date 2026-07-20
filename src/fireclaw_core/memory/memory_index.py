"""Lightweight SQLite FTS5-backed index over mission memory records.

This module provides ``SqliteMemoryIndex``, an optional full-text search
index that can be layered on top of the JSONL-based ``MissionMemoryStore``.

The index is **not** required for basic memory operations.  When no index
path is configured the store falls back to JSONL keyword search.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

# Columns stored in the main table alongside the FTS content blob.
_FILTER_COLUMNS: list[str] = [
    "record_id",
    "mission_id",
    "record_type",
    "robot_id",
    "floor",
    "capability",
    "outcome",
    "operator",
    "risk_level",
    "runtime_mode",
    "source_type",
    "episode_id",
    "created_at",
]

_FILTER_COLUMN_MAP: dict[str, str] = {
    "mission_id": "mission_id",
    "robot_id": "robot_id",
    "floor": "floor",
    "capability": "capability",
    "outcome": "outcome_val",
    "operator": "operator",
    "risk_level": "risk_level",
    "record_type": "record_type",
    "runtime_mode": "runtime_mode",
    "source_type": "source_type",
    "episode_id": "episode_id",
}


class SqliteMemoryIndex:
    """FTS5-backed full-text search index for mission memory records.

    Parameters
    ----------
    path:
        Filesystem path for the SQLite database.  Parent directories are
        created automatically.  If the file does not exist it will be
        created on first write.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None

    # -- connection helpers ---------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema(conn)
        self._conn = conn
        return conn

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        """Create tables if they do not already exist."""
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_records (
                record_id    TEXT PRIMARY KEY,
                mission_id   TEXT,
                record_type  TEXT,
                robot_id     TEXT,
                subtask_id   TEXT,
                floor        TEXT,
                capability   TEXT,
                outcome_val  TEXT,
                operator     TEXT,
                risk_level   TEXT,
                runtime_mode TEXT,
                source_type  TEXT,
                episode_id   TEXT,
                observed_at  TEXT,
                observed_at_epoch REAL,
                frame_id     TEXT,
                position_x   REAL,
                position_y   REAL,
                position_z   REAL,
                uncertainty_radius_m REAL,
                confidence   REAL,
                sensitivity  TEXT,
                created_at   TEXT,
                content_json TEXT
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                record_id UNINDEXED,
                text_blob
            );

            CREATE TABLE IF NOT EXISTS memory_embeddings (
                record_id TEXT PRIMARY KEY,
                embedding BLOB NOT NULL,
                dimensions INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_relations (
                relation_id     TEXT PRIMARY KEY,
                mission_id      TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                target_record_id TEXT NOT NULL,
                relation_type   TEXT NOT NULL,
                runtime_mode    TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                metadata_json   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entity_projection_meta (
                mission_id      TEXT NOT NULL,
                runtime_mode    TEXT NOT NULL,
                source_token    TEXT NOT NULL,
                entity_count    INTEGER NOT NULL,
                rebuilt_at      TEXT NOT NULL,
                PRIMARY KEY (mission_id, runtime_mode)
            );

            CREATE TABLE IF NOT EXISTS entity_projection (
                mission_id      TEXT NOT NULL,
                runtime_mode    TEXT NOT NULL,
                entity_id       TEXT NOT NULL,
                entity_kind     TEXT NOT NULL,
                status          TEXT NOT NULL,
                last_seen_at    TEXT NOT NULL,
                frame_id        TEXT,
                floor           TEXT,
                position_x      REAL,
                position_y      REAL,
                uncertainty_radius_m REAL,
                payload_json    TEXT NOT NULL,
                PRIMARY KEY (mission_id, runtime_mode, entity_id)
            );

            """
        )
        _ensure_columns(
            conn,
            "memory_records",
            {
                "runtime_mode": "TEXT",
                "source_type": "TEXT",
                "episode_id": "TEXT",
                "observed_at": "TEXT",
                "observed_at_epoch": "REAL",
                "frame_id": "TEXT",
                "position_x": "REAL",
                "position_y": "REAL",
                "position_z": "REAL",
                "uncertainty_radius_m": "REAL",
                "confidence": "REAL",
                "sensitivity": "TEXT",
            },
        )
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS memory_records_spatial_idx
                ON memory_records(runtime_mode, frame_id, position_x, position_y);
            CREATE INDEX IF NOT EXISTS memory_records_temporal_idx
                ON memory_records(runtime_mode, observed_at_epoch);
            CREATE INDEX IF NOT EXISTS memory_relations_source_idx
                ON memory_relations(runtime_mode, source_record_id, relation_type);
            CREATE INDEX IF NOT EXISTS memory_relations_target_idx
                ON memory_relations(runtime_mode, target_record_id, relation_type);
            CREATE INDEX IF NOT EXISTS memory_records_entity_source_idx
                ON memory_records(mission_id, runtime_mode, record_type);
            CREATE INDEX IF NOT EXISTS entity_projection_filter_idx
                ON entity_projection(
                    mission_id, runtime_mode, entity_kind, status, last_seen_at
                );
            CREATE INDEX IF NOT EXISTS entity_projection_spatial_idx
                ON entity_projection(
                    mission_id, runtime_mode, frame_id, floor, position_x, position_y
                );
            """
        )

    # -- public API -----------------------------------------------------------

    def upsert(self, record: dict[str, Any], *, index_text: bool = True) -> None:
        """Insert or update a single record in the derived index.

        ``index_text=False`` still stores structured metadata for spatial and
        temporal queries while keeping the payload out of FTS5.
        """
        record_id = record.get("record_id")
        if not record_id:
            return
        conn = self._get_conn()
        content = record.get("content", {})
        if not isinstance(content, dict):
            content = {}
        fields = _extract_index_fields(record, content)
        text_blob = _build_text_blob(record, content)

        conn.execute(
            """
            INSERT OR REPLACE INTO memory_records
                (record_id, mission_id, record_type, robot_id, subtask_id,
                 floor, capability, outcome_val, operator, risk_level,
                 runtime_mode, source_type, episode_id, observed_at,
                 observed_at_epoch, frame_id, position_x, position_y,
                 position_z, uncertainty_radius_m, confidence, sensitivity,
                 created_at, content_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                record.get("mission_id"),
                record.get("record_type"),
                record.get("robot_id"),
                record.get("subtask_id"),
                fields["floor"],
                fields["capability"],
                fields["outcome"],
                fields["operator"],
                fields["risk_level"],
                fields["runtime_mode"],
                fields["source_type"],
                fields["episode_id"],
                fields["observed_at"],
                fields["observed_at_epoch"],
                fields["frame_id"],
                fields["position_x"],
                fields["position_y"],
                fields["position_z"],
                fields["uncertainty_radius_m"],
                fields["confidence"],
                fields["sensitivity"],
                record.get("created_at", ""),
                json.dumps(content, ensure_ascii=False, sort_keys=True),
            ),
        )

        # Upsert into FTS — delete then insert (FTS5 does not support
        # INSERT OR REPLACE on content-synced tables cleanly).
        conn.execute(
            "DELETE FROM memory_fts WHERE record_id = ?",
            (record_id,),
        )
        if index_text:
            conn.execute(
                "INSERT INTO memory_fts (record_id, text_blob) VALUES (?, ?)",
                (record_id, text_blob),
            )
        conn.commit()

    def search(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Full-text search with optional structured filters.

        Parameters
        ----------
        query:
            FTS5 query string.  Use ``"*"`` to match all records.
        filters:
            Optional mapping of column name to exact-match value.
            Supported keys: ``mission_id``, ``robot_id``, ``floor``,
            ``capability``, ``outcome``, ``operator``, ``risk_level``,
            ``record_type``, ``runtime_mode``, ``source_type``, and
            ``episode_id``.
        limit:
            Maximum number of results to return.
        """
        if limit <= 0:
            return []
        conn = self._get_conn()
        active_filters = _normalize_filters(filters)

        return self._search_with_filters(
            conn,
            query=query,
            active_filters=active_filters,
            limit=limit,
        )

    def _search_with_filters(
        self,
        conn: sqlite3.Connection,
        *,
        query: str,
        active_filters: list[tuple[str, str]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Run FTS search with a LIKE fallback for operator natural language."""
        safe_query = _safe_fts_query(query)
        try:
            rows = self._run_search_query(
                conn,
                record_id_subquery=(
                    "SELECT record_id FROM memory_fts"
                    if safe_query == "*"
                    else "SELECT record_id FROM memory_fts WHERE memory_fts MATCH ?"
                ),
                subquery_params=[] if safe_query == "*" else [safe_query],
                active_filters=active_filters,
                limit=limit,
            )
        except sqlite3.OperationalError:
            rows = []
        if rows or safe_query == "*":
            return [_row_to_dict(row) for row in rows]

        terms = _plain_search_terms(query)
        if not terms:
            return []
        like_clauses = " AND ".join("text_blob LIKE ?" for _ in terms)
        like_params = [f"%{term}%" for term in terms]
        rows = self._run_search_query(
            conn,
            record_id_subquery=f"SELECT record_id FROM memory_fts WHERE {like_clauses}",
            subquery_params=like_params,
            active_filters=active_filters,
            limit=limit,
        )
        return [_row_to_dict(row) for row in rows]

    @staticmethod
    def _run_search_query(
        conn: sqlite3.Connection,
        *,
        record_id_subquery: str,
        subquery_params: list[Any],
        active_filters: list[tuple[str, str]],
        limit: int,
    ) -> list[sqlite3.Row]:
        where_parts: list[str] = [f"mr.record_id IN ({record_id_subquery})"]
        params: list[Any] = list(subquery_params)

        for col, value in active_filters:
            where_parts.append(f"mr.{col} = ?")
            params.append(value)

        where_clause = " AND ".join(where_parts)
        params.append(limit)

        sql = f"""
            SELECT mr.* FROM memory_records mr
            WHERE {where_clause}
            ORDER BY mr.created_at DESC
            LIMIT ?
        """
        return conn.execute(sql, params).fetchall()

    def search_spatial(
        self,
        *,
        runtime_mode: str,
        frame_id: str,
        x: float,
        y: float,
        radius_m: float,
        z: float | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return events whose uncertainty region intersects a search radius."""
        if limit <= 0:
            return []
        _require_query_text("runtime_mode", runtime_mode)
        _require_query_text("frame_id", frame_id)
        x_value = _query_float("x", x)
        y_value = _query_float("y", y)
        radius_value = _query_float("radius_m", radius_m)
        if radius_value < 0:
            raise ValueError("radius_m must be >= 0")
        z_value = _query_float("z", z) if z is not None else None

        if z_value is None:
            distance_sql = (
                "((mr.position_x - ?) * (mr.position_x - ?) + "
                "(mr.position_y - ?) * (mr.position_y - ?))"
            )
            distance_params: list[Any] = [x_value, x_value, y_value, y_value]
            position_guard = "mr.position_x IS NOT NULL AND mr.position_y IS NOT NULL"
        else:
            distance_sql = (
                "((mr.position_x - ?) * (mr.position_x - ?) + "
                "(mr.position_y - ?) * (mr.position_y - ?) + "
                "(mr.position_z - ?) * (mr.position_z - ?))"
            )
            distance_params = [
                x_value,
                x_value,
                y_value,
                y_value,
                z_value,
                z_value,
            ]
            position_guard = (
                "mr.position_x IS NOT NULL AND mr.position_y IS NOT NULL "
                "AND mr.position_z IS NOT NULL"
            )

        where_parts = [
            position_guard,
            f"{distance_sql} <= "
            "((? + COALESCE(mr.uncertainty_radius_m, 0.0)) * "
            " (? + COALESCE(mr.uncertainty_radius_m, 0.0)))",
            "mr.runtime_mode = ?",
            "mr.frame_id = ?",
        ]
        params: list[Any] = list(distance_params)
        params.extend(distance_params)
        params.extend([radius_value, radius_value, runtime_mode, frame_id])
        for column, value in _normalize_filters(filters):
            if column in {"runtime_mode"}:
                continue
            where_parts.append(f"mr.{column} = ?")
            params.append(value)
        params.append(limit)

        rows = self._get_conn().execute(
            f"""
            SELECT mr.*, {distance_sql} AS distance_squared
            FROM memory_records mr
            WHERE {' AND '.join(where_parts)}
            ORDER BY distance_squared ASC, mr.observed_at_epoch DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            result = _row_to_dict(row)
            result["distance_m"] = math.sqrt(max(0.0, float(row["distance_squared"])))
            results.append(result)
        return results

    def search_temporal(
        self,
        *,
        runtime_mode: str,
        start_at: str,
        end_at: str,
        filters: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return events observed in an inclusive timezone-aware interval."""
        if limit <= 0:
            return []
        _require_query_text("runtime_mode", runtime_mode)
        start_epoch = _timestamp_to_epoch(start_at, field_name="start_at")
        end_epoch = _timestamp_to_epoch(end_at, field_name="end_at")
        if start_epoch > end_epoch:
            raise ValueError("start_at must be <= end_at")

        where_parts = [
            "mr.runtime_mode = ?",
            "mr.observed_at_epoch IS NOT NULL",
            "mr.observed_at_epoch >= ?",
            "mr.observed_at_epoch <= ?",
        ]
        params: list[Any] = [runtime_mode, start_epoch, end_epoch]
        for column, value in _normalize_filters(filters):
            if column in {"runtime_mode"}:
                continue
            where_parts.append(f"mr.{column} = ?")
            params.append(value)
        params.append(limit)

        rows = self._get_conn().execute(
            f"""
            SELECT mr.*
            FROM memory_records mr
            WHERE {' AND '.join(where_parts)}
            ORDER BY mr.observed_at_epoch DESC, mr.created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def upsert_relation(self, relation: dict[str, Any]) -> None:
        """Project a validated relation between two indexed embodied events."""
        required = (
            "relation_id",
            "mission_id",
            "source_record_id",
            "target_record_id",
            "relation_type",
            "runtime_mode",
            "created_at",
        )
        values: dict[str, str] = {}
        for field_name in required:
            value = relation.get(field_name)
            _require_query_text(field_name, value)
            values[field_name] = str(value)
        if values["source_record_id"] == values["target_record_id"]:
            raise ValueError("A memory relation cannot point to itself")

        conn = self._get_conn()
        endpoints = conn.execute(
            """
            SELECT record_id, mission_id, runtime_mode
            FROM memory_records
            WHERE record_id IN (?, ?)
            """,
            (values["source_record_id"], values["target_record_id"]),
        ).fetchall()
        endpoint_map = {row["record_id"]: row for row in endpoints}
        if len(endpoint_map) != 2:
            raise ValueError("Memory relation endpoints must already exist in the index")
        for endpoint_id in (values["source_record_id"], values["target_record_id"]):
            endpoint = endpoint_map[endpoint_id]
            if endpoint["mission_id"] != values["mission_id"]:
                raise ValueError("Memory relation endpoints must belong to the relation mission")
            if endpoint["runtime_mode"] != values["runtime_mode"]:
                raise ValueError("Memory relation endpoints cannot cross runtime modes")

        metadata = relation.get("metadata")
        conn.execute(
            """
            INSERT OR REPLACE INTO memory_relations
                (relation_id, mission_id, source_record_id, target_record_id,
                 relation_type, runtime_mode, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["relation_id"],
                values["mission_id"],
                values["source_record_id"],
                values["target_record_id"],
                values["relation_type"],
                values["runtime_mode"],
                values["created_at"],
                json.dumps(metadata if isinstance(metadata, dict) else {}, ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.commit()

    def neighbors(
        self,
        record_id: str,
        *,
        runtime_mode: str,
        direction: str = "both",
        relation_types: frozenset[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return relation records adjacent to ``record_id``."""
        if limit <= 0:
            return []
        _require_query_text("record_id", record_id)
        _require_query_text("runtime_mode", runtime_mode)
        if direction not in {"incoming", "outgoing", "both"}:
            raise ValueError("direction must be one of: incoming, outgoing, both")

        params: list[Any] = [runtime_mode]
        where_parts = ["runtime_mode = ?"]
        if direction == "incoming":
            where_parts.append("target_record_id = ?")
            params.append(record_id)
        elif direction == "outgoing":
            where_parts.append("source_record_id = ?")
            params.append(record_id)
        else:
            where_parts.append("(source_record_id = ? OR target_record_id = ?)")
            params.extend([record_id, record_id])
        if relation_types:
            placeholders = ", ".join("?" for _ in relation_types)
            where_parts.append(f"relation_type IN ({placeholders})")
            params.extend(sorted(relation_types))
        params.append(limit)

        rows = self._get_conn().execute(
            f"""
            SELECT * FROM memory_relations
            WHERE {' AND '.join(where_parts)}
            ORDER BY created_at ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            outgoing = row["source_record_id"] == record_id
            results.append({
                "relation_id": row["relation_id"],
                "mission_id": row["mission_id"],
                "source_record_id": row["source_record_id"],
                "target_record_id": row["target_record_id"],
                "neighbor_record_id": (
                    row["target_record_id"] if outgoing else row["source_record_id"]
                ),
                "relation_type": row["relation_type"],
                "runtime_mode": row["runtime_mode"],
                "direction": "outgoing" if outgoing else "incoming",
                "created_at": row["created_at"],
                "metadata": json.loads(row["metadata_json"]),
            })
        return results

    def clear(self) -> None:
        """Clear all derived memory projections without touching JSONL evidence."""
        conn = self._get_conn()
        conn.execute("DELETE FROM memory_relations")
        conn.execute("DELETE FROM memory_records")
        conn.execute("DELETE FROM memory_fts")
        conn.execute("DELETE FROM memory_embeddings")
        conn.execute("DELETE FROM entity_projection")
        conn.execute("DELETE FROM entity_projection_meta")
        conn.commit()

    def entity_source_token(self, *, mission_id: str, runtime_mode: str) -> str:
        """Return a stable token for Entity mention/resolution source rows."""
        _require_query_text("mission_id", mission_id)
        _require_query_text("runtime_mode", runtime_mode)
        row = self._get_conn().execute(
            """
            SELECT COUNT(*) AS source_count, COALESCE(MAX(rowid), 0) AS max_rowid
            FROM memory_records
            WHERE mission_id = ? AND runtime_mode = ?
              AND record_type IN ('entity_mention', 'entity_resolution')
            """,
            (mission_id, runtime_mode),
        ).fetchone()
        return f"entity-events-v2:{int(row['source_count'])}:{int(row['max_rowid'])}"

    def load_entity_projection(
        self,
        *,
        mission_id: str,
        runtime_mode: str,
        source_token: str,
    ) -> list[dict[str, Any]] | None:
        """Load a complete Entity projection only when its source token matches."""
        conn = self._get_conn()
        meta = conn.execute(
            """
            SELECT source_token, entity_count
            FROM entity_projection_meta
            WHERE mission_id = ? AND runtime_mode = ?
            """,
            (mission_id, runtime_mode),
        ).fetchone()
        if meta is None or meta["source_token"] != source_token:
            return None
        rows = conn.execute(
            """
            SELECT payload_json
            FROM entity_projection
            WHERE mission_id = ? AND runtime_mode = ?
            ORDER BY entity_id ASC
            """,
            (mission_id, runtime_mode),
        ).fetchall()
        if len(rows) != int(meta["entity_count"]):
            return None
        payloads: list[dict[str, Any]] = []
        for row in rows:
            try:
                value = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                return None
            if not isinstance(value, dict):
                return None
            payloads.append(value)
        return payloads

    def replace_entity_projection(
        self,
        *,
        mission_id: str,
        runtime_mode: str,
        source_token: str,
        entities: Iterable[dict[str, Any]],
    ) -> int:
        """Atomically replace one mission/runtime Entity derived projection."""
        _require_query_text("mission_id", mission_id)
        _require_query_text("runtime_mode", runtime_mode)
        _require_query_text("source_token", source_token)
        payloads = [dict(entity) for entity in entities]
        conn = self._get_conn()
        with conn:
            conn.execute(
                "DELETE FROM entity_projection WHERE mission_id = ? AND runtime_mode = ?",
                (mission_id, runtime_mode),
            )
            for payload in payloads:
                entity_id = str(payload.get("entity_id") or "")
                if not entity_id:
                    raise ValueError("Entity projection payload requires entity_id")
                pose = payload.get("current_pose")
                if not isinstance(pose, dict):
                    pose = {}
                conn.execute(
                    """
                    INSERT INTO entity_projection (
                        mission_id, runtime_mode, entity_id, entity_kind, status,
                        last_seen_at, frame_id, floor, position_x, position_y,
                        uncertainty_radius_m, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mission_id,
                        runtime_mode,
                        entity_id,
                        str(payload.get("entity_kind") or "unknown"),
                        str(payload.get("status") or "candidate"),
                        str(payload.get("last_seen_at") or ""),
                        pose.get("frame_id"),
                        pose.get("floor"),
                        _optional_float(pose.get("x")),
                        _optional_float(pose.get("y")),
                        _optional_float(pose.get("uncertainty_radius_m")),
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    ),
                )
            conn.execute(
                """
                INSERT OR REPLACE INTO entity_projection_meta (
                    mission_id, runtime_mode, source_token, entity_count, rebuilt_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    runtime_mode,
                    source_token,
                    len(payloads),
                    datetime.now().astimezone().isoformat(),
                ),
            )
        return len(payloads)

    def store_embedding(
        self, record_id: str, embedding: list[float]
    ) -> None:
        """Store an embedding vector for a record.

        The embedding is stored as a compact byte array.  If an embedding
        already exists for the given ``record_id`` it is replaced.
        """
        if not record_id or not embedding:
            return
        conn = self._get_conn()
        blob = _floats_to_bytes(embedding)
        conn.execute(
            """
            INSERT OR REPLACE INTO memory_embeddings
                (record_id, embedding, dimensions)
            VALUES (?, ?, ?)
            """,
            (record_id, blob, len(embedding)),
        )
        conn.commit()

    def get_embedding(self, record_id: str) -> list[float] | None:
        """Retrieve the stored embedding vector for a record.

        Returns ``None`` when no embedding exists for the given record.
        """
        if not record_id:
            return None
        conn = self._get_conn()
        row = conn.execute(
            "SELECT embedding FROM memory_embeddings WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            return None
        return _bytes_to_floats(row["embedding"])

    def search_by_embedding(
        self,
        query_embedding: list[float],
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return records ranked by cosine similarity to the query embedding.

        Each result dict contains ``record_id`` and ``score`` keys, sorted
        by descending score.
        """
        if limit <= 0:
            return []
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT record_id, embedding FROM memory_embeddings"
        ).fetchall()
        scored: list[tuple[str, float]] = []
        for row in rows:
            stored = _bytes_to_floats(row["embedding"])
            sim = _cosine_similarity(query_embedding, stored)
            scored.append((row["record_id"], sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [
            {"record_id": rid, "score": score}
            for rid, score in scored[:limit]
        ]

    def rebuild(self, records: Iterable[dict[str, Any]]) -> int:
        """Rebuild the entire index from a sequence of records.

        Clears all existing data first.  Returns the number of records
        indexed.
        """
        self.clear()
        count = 0
        for record in records:
            record_id = record.get("record_id")
            if not record_id:
                continue
            self.upsert(record)
            count += 1
        return count


# -- helpers ------------------------------------------------------------------


def _ensure_columns(
    conn: sqlite3.Connection,
    table_name: str,
    columns: dict[str, str],
) -> None:
    """Add newly introduced columns to databases created by older versions."""
    existing = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, declaration in columns.items():
        if column_name not in existing:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {declaration}"
            )


def _normalize_filters(filters: dict[str, Any] | None) -> list[tuple[str, str]]:
    active_filters: list[tuple[str, str]] = []
    if not filters:
        return active_filters
    for key, value in filters.items():
        column = _FILTER_COLUMN_MAP.get(key)
        if column is not None and value is not None:
            active_filters.append((column, str(value)))
    return active_filters


def _extract_index_fields(
    record: dict[str, Any],
    content: dict[str, Any],
) -> dict[str, Any]:
    metadata = content.get("_embodied")
    if not isinstance(metadata, dict):
        metadata = {}
    pose = metadata.get("pose")
    if not isinstance(pose, dict):
        pose = {}

    observed_at = _first_present(
        record.get("observed_at"),
        metadata.get("observed_at"),
        record.get("created_at"),
    )
    return {
        "floor": _first_present(record.get("floor"), content.get("floor"), pose.get("floor")),
        "capability": _first_present(record.get("capability"), content.get("capability")),
        "outcome": _first_present(record.get("outcome"), content.get("outcome")),
        "operator": _first_present(record.get("operator"), content.get("operator")),
        "risk_level": _first_present(record.get("risk_level"), content.get("risk_level")),
        "runtime_mode": _first_present(record.get("runtime_mode"), metadata.get("runtime_mode")),
        "source_type": _first_present(record.get("source_type"), metadata.get("source_type")),
        "episode_id": _first_present(record.get("episode_id"), metadata.get("episode_id")),
        "observed_at": observed_at,
        "observed_at_epoch": _try_timestamp_to_epoch(observed_at),
        "frame_id": _first_present(record.get("frame_id"), pose.get("frame_id")),
        "position_x": _optional_float(_first_present(record.get("position_x"), pose.get("x"))),
        "position_y": _optional_float(_first_present(record.get("position_y"), pose.get("y"))),
        "position_z": _optional_float(_first_present(record.get("position_z"), pose.get("z"))),
        "uncertainty_radius_m": _optional_float(
            _first_present(
                record.get("uncertainty_radius_m"),
                pose.get("uncertainty_radius_m"),
            )
        ),
        "confidence": _optional_float(
            _first_present(record.get("confidence"), metadata.get("confidence"))
        ),
        "sensitivity": _first_present(record.get("sensitivity"), metadata.get("sensitivity")),
    }


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _try_timestamp_to_epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _timestamp_to_epoch(value, field_name="timestamp")
    except ValueError:
        return None


def _timestamp_to_epoch(value: str, *, field_name: str) -> float:
    _require_query_text(field_name, value)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.timestamp()


def _query_float(field_name: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite number")
    return parsed


def _require_query_text(field_name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _build_text_blob(record: dict[str, Any], content: dict[str, Any]) -> str:
    """Concatenate searchable text fields into a single blob for FTS."""
    parts = list(_iter_searchable_text(content))
    # Structured fields that are useful for full-text search.
    for key in ("floor", "capability", "outcome", "operator", "risk_level"):
        val = _first_present(record.get(key), content.get(key))
        if isinstance(val, str):
            parts.append(val)
    return " ".join(parts)


def _iter_searchable_text(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and key.startswith("_"):
                continue
            yield from _iter_searchable_text(nested)
        return
    if isinstance(value, (list, tuple)):
        for nested in value:
            yield from _iter_searchable_text(nested)


def _plain_search_terms(query: str) -> list[str]:
    """Extract literal natural-language terms from a user query string."""
    return [
        token.strip('"')
        for token in query.replace(":", " ").split()
        if token.strip('"') and token.upper() not in {"AND", "OR", "NOT", "NEAR"}
    ]


def _safe_fts_query(query: str) -> str:
    """Convert operator text into a safe FTS5 phrase query."""
    stripped = query.strip()
    if not stripped or stripped == "*":
        return "*"
    terms = _plain_search_terms(stripped)
    if not terms:
        return "*"
    return " ".join(f'"{term.replace(chr(34), chr(34) + chr(34))}"' for term in terms)


def _floats_to_bytes(values: list[float]) -> bytes:
    """Pack a list of floats into a compact byte string (big-endian doubles)."""
    import struct
    return struct.pack(f">{len(values)}d", *values)


def _bytes_to_floats(data: bytes) -> list[float]:
    """Unpack a byte string back into a list of floats."""
    import struct
    count = len(data) // 8
    return list(struct.unpack(f">{count}d", data))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two equal-length vectors."""
    import math
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row back to a plain dict."""
    d: dict[str, Any] = {
        "record_id": row["record_id"],
        "mission_id": row["mission_id"],
        "record_type": row["record_type"],
        "robot_id": row["robot_id"],
        "subtask_id": row["subtask_id"],
        "created_at": row["created_at"],
        "content": json.loads(row["content_json"]) if row["content_json"] else {},
    }
    # Include optional filter fields if set.
    if row["floor"]:
        d["floor"] = row["floor"]
    if row["capability"]:
        d["capability"] = row["capability"]
    if row["outcome_val"]:
        d["outcome"] = row["outcome_val"]
    if row["operator"]:
        d["operator"] = row["operator"]
    if row["risk_level"]:
        d["risk_level"] = row["risk_level"]
    if row["runtime_mode"]:
        d["runtime_mode"] = row["runtime_mode"]
    if row["source_type"]:
        d["source_type"] = row["source_type"]
    if row["episode_id"]:
        d["episode_id"] = row["episode_id"]
    if row["observed_at"]:
        d["observed_at"] = row["observed_at"]
    if row["frame_id"]:
        pose: dict[str, Any] = {
            "frame_id": row["frame_id"],
            "x": row["position_x"],
            "y": row["position_y"],
            "z": row["position_z"],
            "uncertainty_radius_m": row["uncertainty_radius_m"] or 0.0,
        }
        if row["floor"]:
            pose["floor"] = row["floor"]
        d["pose"] = pose
    if row["confidence"] is not None:
        d["confidence"] = row["confidence"]
    if row["sensitivity"]:
        d["sensitivity"] = row["sensitivity"]
    return d
