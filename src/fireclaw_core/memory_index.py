"""Lightweight SQLite FTS5-backed index over mission memory records.

This module provides ``SqliteMemoryIndex``, an optional full-text search
index that can be layered on top of the JSONL-based ``MissionMemoryStore``.

The index is **not** required for basic memory operations.  When no index
path is configured the store falls back to JSONL keyword search.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
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
    "created_at",
]


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
            """
        )

    # -- public API -----------------------------------------------------------

    def upsert(self, record: dict[str, Any]) -> None:
        """Insert or update a single record in the index."""
        record_id = record.get("record_id")
        if not record_id:
            return
        conn = self._get_conn()
        content = record.get("content", {})
        text_blob = _build_text_blob(record, content)

        conn.execute(
            """
            INSERT OR REPLACE INTO memory_records
                (record_id, mission_id, record_type, robot_id, subtask_id,
                 floor, capability, outcome_val, operator, risk_level,
                 created_at, content_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                record.get("mission_id"),
                record.get("record_type"),
                record.get("robot_id"),
                record.get("subtask_id"),
                record.get("floor"),
                record.get("capability"),
                record.get("outcome"),
                record.get("operator"),
                record.get("risk_level"),
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
            ``record_type``.
        limit:
            Maximum number of results to return.
        """
        if limit <= 0:
            return []
        conn = self._get_conn()
        # Map filter key -> actual column name in memory_records.
        filter_map = {
            "mission_id": "mission_id",
            "robot_id": "robot_id",
            "floor": "floor",
            "capability": "capability",
            "outcome": "outcome_val",
            "operator": "operator",
            "risk_level": "risk_level",
            "record_type": "record_type",
        }
        active_filters: list[tuple[str, str]] = []
        if filters:
            for key, value in filters.items():
                col = filter_map.get(key)
                if col is not None and value is not None:
                    active_filters.append((col, str(value)))

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
        conn = self._get_conn()
        conn.execute("DELETE FROM memory_records")
        conn.execute("DELETE FROM memory_fts")
        conn.execute("DELETE FROM memory_embeddings")
        count = 0
        for record in records:
            record_id = record.get("record_id")
            if not record_id:
                continue
            content = record.get("content", {})
            text_blob = _build_text_blob(record, content)
            conn.execute(
                """
                INSERT INTO memory_records
                    (record_id, mission_id, record_type, robot_id, subtask_id,
                     floor, capability, outcome_val, operator, risk_level,
                     created_at, content_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    record.get("mission_id"),
                    record.get("record_type"),
                    record.get("robot_id"),
                    record.get("subtask_id"),
                    record.get("floor"),
                    record.get("capability"),
                    record.get("outcome"),
                    record.get("operator"),
                    record.get("risk_level"),
                    record.get("created_at", ""),
                    json.dumps(content, ensure_ascii=False, sort_keys=True),
                ),
            )
            conn.execute(
                "INSERT INTO memory_fts (record_id, text_blob) VALUES (?, ?)",
                (record_id, text_blob),
            )
            count += 1
        conn.commit()
        return count


# -- helpers ------------------------------------------------------------------


def _build_text_blob(record: dict[str, Any], content: dict[str, Any]) -> str:
    """Concatenate searchable text fields into a single blob for FTS."""
    parts: list[str] = []
    # Content values.
    for value in content.values():
        if isinstance(value, str):
            parts.append(value)
    # Structured fields that are useful for full-text search.
    for key in ("floor", "capability", "outcome", "operator", "risk_level"):
        val = record.get(key)
        if isinstance(val, str):
            parts.append(val)
    return " ".join(parts)


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
    return d
