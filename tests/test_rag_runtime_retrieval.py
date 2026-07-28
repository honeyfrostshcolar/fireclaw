from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pytest

from fireclaw_core.rag.reranking import FakeRerankerProvider
from fireclaw_core.rag.runtime_retrieval import (
    HybridRagRetriever,
    RagRuntimeConfig,
    RerankingRagRetriever,
    build_runtime_rag_retriever,
)


@dataclass(frozen=True)
class FakeHit:
    rank: int
    score: float
    record: dict[str, Any]


class FakeRetriever:
    def __init__(self, hits: list[FakeHit]) -> None:
        self.hits = hits
        self.top_ks: list[int] = []

    def query(self, query: str, *, top_k: int = 5) -> list[FakeHit]:
        self.top_ks.append(top_k)
        return self.hits[:top_k]


def _hit(record_id: str, rank: int, text: str) -> FakeHit:
    return FakeHit(
        rank=rank,
        score=1.0 / rank,
        record={
            "record_id": record_id,
            "source_kind": "mission_memory",
            "clean_text": text,
        },
    )


def test_hybrid_retriever_fuses_rankings_by_record_id() -> None:
    dense = FakeRetriever([_hit("dense-only", 1, "thermal"), _hit("both", 2, "victim")])
    bm25 = FakeRetriever([_hit("both", 1, "victim"), _hit("bm25-only", 2, "stairs")])
    retriever = HybridRagRetriever(dense, bm25, candidate_multiplier=2, rrf_k=60)

    hits = retriever.query("victim", top_k=2)

    assert [hit.record["record_id"] for hit in hits] == ["both", "dense-only"]
    assert dense.top_ks == [4]
    assert bm25.top_ks == [4]


def test_reranking_retriever_uses_rag_record_text() -> None:
    base = FakeRetriever([
        _hit("generic", 1, "general equipment"),
        _hit("gold", 2, "victim thermal search"),
    ])
    retriever = RerankingRagRetriever(
        base,
        FakeRerankerProvider(),
        candidate_multiplier=2,
    )

    hits = retriever.query("victim thermal", top_k=1)

    assert hits[0].record["record_id"] == "gold"
    assert base.top_ks == [2]


def test_runtime_config_requires_backend_paths() -> None:
    with pytest.raises(ValueError, match="requires bm25_index_dir"):
        RagRuntimeConfig(backend="bm25", source_kind="mission_memory")

    with pytest.raises(ValueError, match="Unsupported RAG source_kind"):
        RagRuntimeConfig(
            backend="bm25",
            source_kind="mixed",
            bm25_index_dir=Path("unused"),
        )

    managed = RagRuntimeConfig(
        backend="hybrid",
        source_kind="mission_memory",
        generation_root=Path("managed"),
        embedding_provider="fake",
    )
    assert managed.bm25_index_dir is None
    assert managed.dense_index_dir is None


def test_runtime_config_can_construct_fake_providers(tmp_path: Path) -> None:
    from fireclaw_core.memory.rag_indexing import build_memory_rag_indexes
    from fireclaw_core.mission.mission_memory import MissionMemoryRecord
    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider

    memory_path = tmp_path / "memory.jsonl"
    record = MissionMemoryRecord(
        record_id="event-1",
        mission_id="mission-1",
        record_type="observation",
        content={"note": "victim thermal"},
    )
    memory_path.write_text(
        json.dumps(record.to_dict()) + "\n",
        encoding="utf-8",
    )
    build_memory_rag_indexes(
        memory_path,
        tmp_path / "rag",
        embedding_provider=FakeEmbeddingProvider(),
        build_dense=True,
    )
    config = RagRuntimeConfig(
        backend="hybrid_rerank",
        source_kind="mission_memory",
        bm25_index_dir=tmp_path / "rag" / "bm25",
        dense_index_dir=tmp_path / "rag" / "dense",
        embedding_provider="fake",
        reranker_provider="fake",
    )

    hits = build_runtime_rag_retriever(config).query("victim", top_k=1)

    assert hits[0].record["record_id"] == "event-1"
