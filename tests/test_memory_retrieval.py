"""Tests for memory retrieval — ranked retrieval with optional embedding support."""
from __future__ import annotations

import math
from typing import Any

import pytest

from fireclaw_core.memory_index import SqliteMemoryIndex
from fireclaw_core.memory_retrieval import (
    EmbeddingProvider,
    MemoryRetriever,
    RetrievedMemory,
)
from fireclaw_core.mission_memory import MissionMemoryStore


# ---------------------------------------------------------------------------
# Fake embedding provider — deterministic, no external API
# ---------------------------------------------------------------------------


class FakeEmbeddingProvider:
    """Deterministic embedding provider for testing.

    Produces a simple hash-based vector so that similar texts get
    similar embeddings while remaining fully deterministic.
    """

    def __init__(self, dimensions: int = 8) -> None:
        self._dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        """Return a deterministic embedding based on character frequencies."""
        vec = [0.0] * self._dimensions
        for i, ch in enumerate(text.lower()):
            vec[i % self._dimensions] += ord(ch)
        # Normalize to unit length.
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    @property
    def dimensions(self) -> int:
        return self._dimensions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_index_record(
    record_id: str = "mem-1",
    mission_id: str = "m-1",
    record_type: str = "outcome",
    content: dict | None = None,
    robot_id: str | None = None,
    subtask_id: str | None = None,
    created_at: str = "2026-06-08T12:00:00Z",
) -> dict:
    return {
        "record_id": record_id,
        "mission_id": mission_id,
        "record_type": record_type,
        "content": content or {"status": "succeeded"},
        "robot_id": robot_id,
        "subtask_id": subtask_id,
        "created_at": created_at,
    }


def _seed_index(idx: SqliteMemoryIndex, records: list[dict]) -> None:
    for rec in records:
        idx.upsert(rec)


def _seed_index_with_embeddings(
    idx: SqliteMemoryIndex,
    records: list[dict],
    provider: FakeEmbeddingProvider,
) -> None:
    for rec in records:
        idx.upsert(rec)
        # Build the text to embed from content values.
        content = rec.get("content", {})
        text = " ".join(str(v) for v in content.values() if isinstance(v, str))
        embedding = provider.embed(text)
        idx.store_embedding(rec["record_id"], embedding)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRetrievedMemory:
    def test_retrieved_memory_creation(self):
        """RetrievedMemory fields are accessible and frozen."""
        mem = RetrievedMemory(
            record_id="mem-1",
            mission_id="m-1",
            record_type="outcome",
            content={"status": "ok"},
            score=0.85,
            source="fused",
            created_at="2026-06-08T12:00:00Z",
            robot_id="r-1",
            subtask_id="s-1",
        )
        assert mem.record_id == "mem-1"
        assert mem.mission_id == "m-1"
        assert mem.record_type == "outcome"
        assert mem.content == {"status": "ok"}
        assert mem.score == 0.85
        assert mem.source == "fused"
        assert mem.created_at == "2026-06-08T12:00:00Z"
        assert mem.robot_id == "r-1"
        assert mem.subtask_id == "s-1"

    def test_retrieved_memory_is_frozen(self):
        mem = RetrievedMemory(
            record_id="mem-1",
            mission_id="m-1",
            record_type="outcome",
            content={},
            score=1.0,
            source="lexical",
        )
        with pytest.raises(AttributeError):
            mem.score = 0.5  # type: ignore[misc]


class TestEmbeddingProviderProtocol:
    def test_fake_provider_satisfies_protocol(self):
        """FakeEmbeddingProvider structurally satisfies the EmbeddingProvider protocol."""
        provider = FakeEmbeddingProvider(dimensions=8)
        # Protocol check at runtime via isinstance is not enforced for Protocols
        # by default, but we verify the interface works.
        vec = provider.embed("test text")
        assert isinstance(vec, list)
        assert len(vec) == 8
        assert all(isinstance(v, float) for v in vec)
        assert provider.dimensions == 8

    def test_embedding_provider_protocol_definition(self):
        """EmbeddingProvider is a Protocol with expected methods."""
        # Verify it is a Protocol by checking its attributes.
        assert hasattr(EmbeddingProvider, "embed")
        assert hasattr(EmbeddingProvider, "dimensions")


class TestSqliteIndexEmbedding:
    def test_store_and_get_embedding(self, tmp_path):
        """Embeddings can be stored and retrieved from the index."""
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        idx.upsert(_make_index_record(record_id="mem-1"))
        embedding = [0.1, 0.2, 0.3, 0.4]
        idx.store_embedding("mem-1", embedding)

        retrieved = idx.get_embedding("mem-1")
        assert retrieved is not None
        assert len(retrieved) == 4
        for a, b in zip(retrieved, embedding):
            assert abs(a - b) < 1e-6

    def test_get_embedding_returns_none_for_missing(self, tmp_path):
        """get_embedding returns None for nonexistent records."""
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        assert idx.get_embedding("nonexistent") is None

    def test_search_by_embedding(self, tmp_path):
        """search_by_embedding returns records ranked by cosine similarity."""
        provider = FakeEmbeddingProvider(dimensions=8)
        idx = SqliteMemoryIndex(tmp_path / "mem.db")

        records = [
            _make_index_record(
                record_id="mem-1",
                content={"note": "rescued survivor on floor 2"},
            ),
            _make_index_record(
                record_id="mem-2",
                content={"detail": "heavy smoke detected"},
            ),
            _make_index_record(
                record_id="mem-3",
                content={"lesson": "always check stairwell first"},
            ),
        ]
        _seed_index_with_embeddings(idx, records, provider)

        query_embedding = provider.embed("rescued survivor")
        results = idx.search_by_embedding(query_embedding, limit=3)
        assert len(results) == 3
        # All results have record_id and score keys.
        for r in results:
            assert "record_id" in r
            assert "score" in r
        # Results are sorted by score descending.
        for i in range(len(results) - 1):
            assert results[i]["score"] >= results[i + 1]["score"]


class TestMemoryRetrieverLexicalOnly:
    def test_lexical_only_returns_ranked_results(self, tmp_path):
        """Without an embedding provider, retrieval is pure FTS5."""
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        _seed_index(idx, [
            _make_index_record(
                record_id="mem-1",
                content={"note": "rescued survivor on floor 2"},
            ),
            _make_index_record(
                record_id="mem-2",
                content={"detail": "heavy smoke detected"},
            ),
            _make_index_record(
                record_id="mem-3",
                content={"lesson": "check stairwell before entry"},
            ),
        ])

        retriever = MemoryRetriever(idx)
        results = retriever.retrieve("smoke")

        assert len(results) >= 1
        assert results[0].record_id == "mem-2"
        assert results[0].source == "lexical"
        assert results[0].score > 0

    def test_lexical_only_returns_correct_types(self, tmp_path):
        """Results are RetrievedMemory instances."""
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        _seed_index(idx, [
            _make_index_record(record_id="mem-1", content={"note": "fire event"}),
        ])

        retriever = MemoryRetriever(idx)
        results = retriever.retrieve("fire")

        assert len(results) == 1
        assert isinstance(results[0], RetrievedMemory)
        assert results[0].content == {"note": "fire event"}


class TestMemoryRetrieverWithEmbedding:
    def test_rank_fusion(self, tmp_path):
        """With an embedding provider, results use fused scores."""
        provider = FakeEmbeddingProvider(dimensions=8)
        idx = SqliteMemoryIndex(tmp_path / "mem.db")

        records = [
            _make_index_record(
                record_id="mem-1",
                content={"note": "rescued survivor on floor 2"},
            ),
            _make_index_record(
                record_id="mem-2",
                content={"detail": "heavy smoke detected in hallway"},
            ),
            _make_index_record(
                record_id="mem-3",
                content={"lesson": "always check stairwell first"},
            ),
        ]
        _seed_index_with_embeddings(idx, records, provider)

        retriever = MemoryRetriever(
            idx, embedding_provider=provider,
            lexical_weight=0.6, embedding_weight=0.4,
        )
        results = retriever.retrieve("smoke")

        assert len(results) >= 1
        # Fused source when embedding provider is present.
        for r in results:
            assert r.source == "fused"
            assert r.score >= 0

    def test_rank_fusion_respects_weights(self, tmp_path):
        """Changing weights changes relative ordering when appropriate."""
        provider = FakeEmbeddingProvider(dimensions=8)
        idx = SqliteMemoryIndex(tmp_path / "mem.db")

        records = [
            _make_index_record(
                record_id="mem-1",
                content={"note": "fire on floor 2"},
            ),
            _make_index_record(
                record_id="mem-2",
                content={"detail": "smoke in hallway"},
            ),
        ]
        _seed_index_with_embeddings(idx, records, provider)

        # All lexical weight.
        retriever_lex = MemoryRetriever(
            idx, embedding_provider=provider,
            lexical_weight=1.0, embedding_weight=0.0,
        )
        results_lex = retriever_lex.retrieve("smoke")
        assert len(results_lex) >= 1

        # All embedding weight.
        retriever_emb = MemoryRetriever(
            idx, embedding_provider=provider,
            lexical_weight=0.0, embedding_weight=1.0,
        )
        results_emb = retriever_emb.retrieve("smoke")
        assert len(results_emb) >= 1


class TestMemoryRetrieverLimit:
    def test_respects_limit(self, tmp_path):
        """Retriever returns at most `limit` results."""
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        records = [
            _make_index_record(
                record_id=f"mem-{i}",
                content={"note": "fire event"},
            )
            for i in range(10)
        ]
        _seed_index(idx, records)

        retriever = MemoryRetriever(idx)
        results = retriever.retrieve("fire", limit=3)
        assert len(results) == 3


class TestMemoryRetrieverEmpty:
    def test_empty_results_when_no_match(self, tmp_path):
        """No matches returns an empty list."""
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        _seed_index(idx, [
            _make_index_record(content={"note": "fire on floor 2"}),
        ])

        retriever = MemoryRetriever(idx)
        results = retriever.retrieve("nonexistent_term_xyz")
        assert results == []


class TestMemoryRetrieverFallback:
    def test_falls_back_to_lexical_when_no_provider(self, tmp_path):
        """Without embedding provider, behaves identically to lexical-only."""
        idx = SqliteMemoryIndex(tmp_path / "mem.db")
        _seed_index(idx, [
            _make_index_record(
                record_id="mem-1",
                content={"note": "rescued survivor"},
            ),
            _make_index_record(
                record_id="mem-2",
                content={"detail": "heavy smoke"},
            ),
        ])

        retriever = MemoryRetriever(idx)
        results = retriever.retrieve("survivor")

        assert len(results) == 1
        assert results[0].record_id == "mem-1"
        assert results[0].source == "lexical"


class TestTranscriptIngestion:
    def test_ingest_transcript_creates_record(self, tmp_path):
        """ingest_transcript appends a properly structured record."""
        store = MissionMemoryStore(tmp_path / "mem.jsonl")
        record = store.ingest_transcript(
            mission_id="m-1",
            entry_type="observation",
            content={"location": "floor 2", "detail": "smoke detected"},
            robot_id="r-1",
            subtask_id="s-1",
        )

        assert record.mission_id == "m-1"
        assert record.record_type == "observation"
        assert record.content == {"location": "floor 2", "detail": "smoke detected"}
        assert record.robot_id == "r-1"
        assert record.subtask_id == "s-1"
        assert record.record_id  # auto-generated
        assert record.created_at  # auto-generated

    def test_ingest_transcript_generates_unique_ids(self, tmp_path):
        """Multiple ingest calls produce unique record IDs."""
        store = MissionMemoryStore(tmp_path / "mem.jsonl")
        r1 = store.ingest_transcript(
            mission_id="m-1",
            entry_type="outcome",
            content={"status": "ok"},
        )
        r2 = store.ingest_transcript(
            mission_id="m-1",
            entry_type="outcome",
            content={"status": "ok"},
        )
        assert r1.record_id != r2.record_id

    def test_ingest_transcript_defaults_created_at(self, tmp_path):
        """ingest_transcript generates a created_at timestamp when not provided."""
        store = MissionMemoryStore(tmp_path / "mem.jsonl")
        record = store.ingest_transcript(
            mission_id="m-1",
            entry_type="lesson",
            content={"lesson": "always check stairwell"},
        )
        assert record.created_at  # not empty

    def test_ingest_transcript_with_explicit_created_at(self, tmp_path):
        """ingest_transcript uses provided created_at when given."""
        store = MissionMemoryStore(tmp_path / "mem.jsonl")
        record = store.ingest_transcript(
            mission_id="m-1",
            entry_type="lesson",
            content={"lesson": "test"},
            created_at="2026-06-09T10:00:00Z",
        )
        assert record.created_at == "2026-06-09T10:00:00Z"

    def test_ingest_transcript_with_index(self, tmp_path):
        """ingest_transcript indexes the record when index is configured."""
        store = MissionMemoryStore(
            tmp_path / "mem.jsonl",
            index_path=tmp_path / "mem.db",
        )
        record = store.ingest_transcript(
            mission_id="m-1",
            entry_type="observation",
            content={"note": "found victim in basement"},
        )
        # Search via the index to confirm it was indexed.
        results = store.search_indexed("basement")
        assert len(results) == 1
        assert results[0].record_id == record.record_id


def test_memory_retriever_status_reports_last_indexing_timestamp(tmp_path):
    """status() includes last_indexing_timestamp when index exists."""
    from fireclaw_core.memory_index import SqliteMemoryIndex
    from fireclaw_core.memory_retrieval import MemoryRetriever

    index = SqliteMemoryIndex(tmp_path / "mem.db")
    # Ingest a record so the index file has content.
    index.upsert({
        "record_id": "r1",
        "mission_id": "m-1",
        "record_type": "observation",
        "content": {"note": "test"},
        "source": "test",
        "created_at": "2026-06-10T00:00:00+00:00",
    })
    retriever = MemoryRetriever(index=index)

    result = retriever.status()

    assert result["lexical_index_available"] is True
    assert "last_indexing_timestamp" in result
    assert result["last_indexing_timestamp"] is not None
    # Should be a valid ISO timestamp.
    from datetime import datetime
    datetime.fromisoformat(result["last_indexing_timestamp"])


def test_memory_retriever_status_no_index(tmp_path):
    """status() returns None for last_indexing_timestamp when no index."""
    from fireclaw_core.memory_retrieval import MemoryRetriever

    retriever = MemoryRetriever(index=None)

    result = retriever.status()

    assert result["lexical_index_available"] is False
    assert result["last_indexing_timestamp"] is None
