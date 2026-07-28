"""Runtime composition for FireClaw RAG retrievers.

The concrete BM25, dense, and reranker implementations stay in the RAG
subpackage.  This module gives runtime consumers one stable ``query`` API.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import threading
from typing import Any

from fireclaw_core.rag.bm25_retrieval import BM25Retriever
from fireclaw_core.rag.dense_retrieval import (
    DenseRetriever,
    EmbeddingProvider,
    FakeEmbeddingProvider,
)
from fireclaw_core.rag.reranking import FakeRerankerProvider, RerankerProvider


RAG_BACKENDS = frozenset({"bm25", "dense", "hybrid", "hybrid_rerank"})
RAG_SOURCE_KINDS = frozenset({
    "mission_memory",
    "reusable_memory",
    "external_knowledge",
})


@dataclass(frozen=True)
class RagRuntimeConfig:
    """Paths and ranking policy for one typed RAG corpus."""

    backend: str
    source_kind: str
    bm25_index_dir: Path | None = None
    dense_index_dir: Path | None = None
    generation_root: Path | None = None
    embedding_provider: str | None = None
    embedding_model_path: Path | None = None
    reranker_provider: str | None = None
    reranker_model_path: Path | None = None
    device: str | None = None
    candidate_multiplier: int = 3
    rrf_k: int = 60

    def __post_init__(self) -> None:
        if self.backend not in RAG_BACKENDS:
            raise ValueError(f"Unsupported RAG backend: {self.backend}")
        if self.source_kind not in RAG_SOURCE_KINDS:
            raise ValueError(f"Unsupported RAG source_kind: {self.source_kind}")
        if self.candidate_multiplier <= 0:
            raise ValueError("candidate_multiplier must be positive")
        if self.rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        if self.backend in {"bm25", "hybrid", "hybrid_rerank"} and self.generation_root is None:
            if self.bm25_index_dir is None:
                raise ValueError(f"{self.backend} requires bm25_index_dir")
        if self.backend in {"dense", "hybrid", "hybrid_rerank"} and self.generation_root is None:
            if self.dense_index_dir is None:
                raise ValueError(f"{self.backend} requires dense_index_dir")
        if self.embedding_provider not in {None, "fake", "bge-m3"}:
            raise ValueError(
                f"Unsupported embedding_provider: {self.embedding_provider}"
            )
        if self.embedding_provider == "bge-m3" and self.embedding_model_path is None:
            raise ValueError("bge-m3 requires embedding_model_path")
        if self.reranker_provider not in {None, "fake", "bge-reranker"}:
            raise ValueError(
                f"Unsupported reranker_provider: {self.reranker_provider}"
            )
        if (
            self.reranker_provider == "bge-reranker"
            and self.reranker_model_path is None
        ):
            raise ValueError("bge-reranker requires reranker_model_path")


@dataclass(frozen=True)
class RuntimeRagHit:
    rank: int
    score: float
    record: dict[str, Any]


class HybridRagRetriever:
    """Fuse dense and BM25 rankings with reciprocal-rank fusion."""

    def __init__(
        self,
        dense_retriever: Any,
        bm25_retriever: Any,
        *,
        candidate_multiplier: int = 3,
        rrf_k: int = 60,
    ) -> None:
        if candidate_multiplier <= 0:
            raise ValueError("candidate_multiplier must be positive")
        if rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        self._dense = dense_retriever
        self._bm25 = bm25_retriever
        self._candidate_multiplier = candidate_multiplier
        self._rrf_k = rrf_k

    def query(self, query: str, *, top_k: int = 5) -> list[RuntimeRagHit]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        candidate_k = top_k * self._candidate_multiplier
        rankings = (
            self._dense.query(query, top_k=candidate_k),
            self._bm25.query(query, top_k=candidate_k),
        )
        scores: dict[str, float] = {}
        records: dict[str, dict[str, Any]] = {}
        first_seen: dict[str, tuple[int, int]] = {}
        for source_index, hits in enumerate(rankings):
            for fallback_rank, hit in enumerate(hits, start=1):
                record = _hit_record(hit)
                identity = _record_identity(record)
                if identity is None:
                    continue
                rank = _positive_rank(hit, fallback_rank)
                scores[identity] = scores.get(identity, 0.0) + (
                    1.0 / (self._rrf_k + rank)
                )
                records.setdefault(identity, record)
                first_seen.setdefault(identity, (rank, source_index))

        ordered = sorted(
            scores,
            key=lambda identity: (
                -scores[identity],
                first_seen[identity],
                identity,
            ),
        )
        return [
            RuntimeRagHit(
                rank=rank,
                score=scores[identity],
                record=records[identity],
            )
            for rank, identity in enumerate(ordered[:top_k], start=1)
        ]

    def status(self) -> dict[str, Any]:
        return {
            "backend": "hybrid",
            "fusion": "rrf",
            "rrf_k": self._rrf_k,
            "candidate_multiplier": self._candidate_multiplier,
        }


class RerankingRagRetriever:
    """Rerank a RAG candidate pool without changing record authority."""

    def __init__(
        self,
        retriever: Any,
        reranker: RerankerProvider,
        *,
        candidate_multiplier: int = 3,
    ) -> None:
        if candidate_multiplier <= 0:
            raise ValueError("candidate_multiplier must be positive")
        self._retriever = retriever
        self._reranker = reranker
        self._candidate_multiplier = candidate_multiplier

    def query(self, query: str, *, top_k: int = 5) -> list[RuntimeRagHit]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        hits = list(self._retriever.query(
            query,
            top_k=top_k * self._candidate_multiplier,
        ))
        pairs = [(query, _record_text(_hit_record(hit))) for hit in hits]
        scores = self._reranker.score_pairs(pairs)
        if len(scores) != len(hits):
            raise ValueError(
                f"reranker returned {len(scores)} scores for {len(hits)} hits"
            )
        ranked = sorted(
            zip(hits, scores, strict=True),
            key=lambda item: (
                -float(item[1]),
                _positive_rank(item[0], 1),
                _record_identity(_hit_record(item[0])) or "",
            ),
        )
        return [
            RuntimeRagHit(
                rank=rank,
                score=float(score),
                record=_hit_record(hit),
            )
            for rank, (hit, score) in enumerate(ranked[:top_k], start=1)
        ]

    def status(self) -> dict[str, Any]:
        return {
            "backend": "rerank",
            "candidate_multiplier": self._candidate_multiplier,
            "reranker": self._reranker.model_info.provider,
        }


class ReloadingRagRetriever:
    """Reload a validated generation on pointer changes and retain the last good one."""

    def __init__(
        self,
        config: RagRuntimeConfig,
        *,
        embedding_provider: EmbeddingProvider | None,
        reranker: RerankerProvider | None,
    ) -> None:
        if config.generation_root is None:
            raise ValueError("ReloadingRagRetriever requires generation_root")
        self._config = config
        self._root = Path(config.generation_root)
        self._embedding_provider = embedding_provider
        self._reranker = reranker
        self._active: Any | None = None
        self._generation_id: str | None = None
        self._metadata: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._lock = threading.Lock()

    def query(self, query: str, *, top_k: int = 5) -> list[Any]:
        self._reload_if_needed()
        with self._lock:
            active = self._active
        if active is None:
            return []
        return list(active.query(query, top_k=top_k))

    def status(self) -> dict[str, Any]:
        self._reload_if_needed()
        with self._lock:
            metadata = dict(self._metadata) if self._metadata is not None else None
            generation_id = self._generation_id
            last_error = self._last_error
            available = self._active is not None
        return {
            "backend": self._config.backend,
            "managed_generation": True,
            "available": available,
            "generation_id": generation_id,
            "source_version": (
                metadata.get("source_version") if metadata is not None else None
            ),
            "source_current": _source_matches_metadata(metadata),
            "last_reload_error": last_error,
        }

    def _reload_if_needed(self) -> None:
        try:
            metadata = _read_generation_pointer(self._root)
            generation_id = str(metadata["generation_id"])
            with self._lock:
                if generation_id == self._generation_id:
                    return
            resolved = _config_for_generation(self._config, self._root, metadata)
            loaded = build_runtime_rag_retriever(
                resolved,
                embedding_provider=self._embedding_provider,
                reranker=self._reranker,
            )
            with self._lock:
                self._active = loaded
                self._generation_id = generation_id
                self._metadata = metadata
                self._last_error = None
        except Exception as exc:
            with self._lock:
                self._last_error = type(exc).__name__


def build_runtime_rag_retriever(
    config: RagRuntimeConfig,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    reranker: RerankerProvider | None = None,
) -> Any:
    """Load and compose the configured RAG backend."""
    embedding_provider = embedding_provider or embedding_provider_from_config(config)
    reranker = reranker or _reranker_from_config(config)
    if config.generation_root is not None:
        return ReloadingRagRetriever(
            config,
            embedding_provider=embedding_provider,
            reranker=reranker,
        )
    bm25 = None
    dense = None
    if config.bm25_index_dir is not None:
        bm25 = BM25Retriever.load(config.bm25_index_dir)
    if config.dense_index_dir is not None:
        if embedding_provider is None:
            raise ValueError(f"{config.backend} requires an embedding_provider")
        dense = DenseRetriever.load(config.dense_index_dir, embedding_provider)

    if config.backend == "bm25":
        return bm25
    if config.backend == "dense":
        return dense
    assert dense is not None and bm25 is not None
    hybrid = HybridRagRetriever(
        dense,
        bm25,
        candidate_multiplier=config.candidate_multiplier,
        rrf_k=config.rrf_k,
    )
    if config.backend == "hybrid":
        return hybrid
    if reranker is None:
        raise ValueError("hybrid_rerank requires a reranker")
    return RerankingRagRetriever(
        hybrid,
        reranker,
        candidate_multiplier=config.candidate_multiplier,
    )


def embedding_provider_from_config(
    config: RagRuntimeConfig,
) -> EmbeddingProvider | None:
    if config.embedding_provider is None:
        return None
    if config.embedding_provider == "fake":
        return FakeEmbeddingProvider()
    from fireclaw_core.rag.bge_m3_provider import BGEM3EmbeddingProvider

    assert config.embedding_model_path is not None
    return BGEM3EmbeddingProvider(
        config.embedding_model_path,
        device=config.device,
    )


def _read_generation_pointer(root: Path) -> dict[str, Any]:
    value = json.loads((root / "current.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("invalid RAG generation pointer")
    for key in ("generation_id", "source_version", "source_kind", "backend"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"RAG generation pointer missing {key}")
    return value


def _config_for_generation(
    config: RagRuntimeConfig,
    root: Path,
    metadata: dict[str, Any],
) -> RagRuntimeConfig:
    if metadata["source_kind"] != config.source_kind:
        raise ValueError("RAG generation source_kind mismatch")
    required_bm25 = config.backend in {"bm25", "hybrid", "hybrid_rerank"}
    required_dense = config.backend in {"dense", "hybrid", "hybrid_rerank"}
    generation_id = str(metadata["generation_id"])
    generation_manifest = _read_generation_manifest(
        root,
        generation_id=generation_id,
    )
    for key in (
        "schema_version",
        "generation_id",
        "source_kind",
        "source_version",
        "build_signature",
    ):
        if generation_manifest.get(key) != metadata.get(key):
            raise ValueError(f"RAG generation metadata mismatch: {key}")
    bm25_dir = _managed_index_path(
        root,
        metadata.get("bm25_index_dir"),
        generation_id=generation_id,
    )
    dense_dir = _managed_index_path(
        root,
        metadata.get("dense_index_dir"),
        generation_id=generation_id,
    )
    if required_bm25 and bm25_dir is None:
        raise ValueError("RAG generation is missing BM25 index")
    if required_dense and dense_dir is None:
        raise ValueError("RAG generation is missing dense index")
    return RagRuntimeConfig(
        backend=config.backend,
        source_kind=config.source_kind,
        bm25_index_dir=bm25_dir,
        dense_index_dir=dense_dir,
        embedding_provider=config.embedding_provider,
        embedding_model_path=config.embedding_model_path,
        reranker_provider=config.reranker_provider,
        reranker_model_path=config.reranker_model_path,
        device=config.device,
        candidate_multiplier=config.candidate_multiplier,
        rrf_k=config.rrf_k,
    )


def _read_generation_manifest(
    root: Path,
    *,
    generation_id: str,
) -> dict[str, Any]:
    generation_dir = (root / "generations" / generation_id).resolve()
    resolved_root = root.resolve()
    if not generation_dir.is_relative_to(resolved_root):
        raise ValueError("RAG generation path escapes generation root")
    value = json.loads(
        (generation_dir / "generation.json").read_text(encoding="utf-8")
    )
    if not isinstance(value, dict):
        raise ValueError("invalid RAG generation manifest")
    return value


def _managed_index_path(
    root: Path,
    value: Any,
    *,
    generation_id: str,
) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid managed RAG index path")
    resolved_root = root.resolve()
    expected_generation = (root / "generations" / generation_id).resolve()
    resolved = (root / value).resolve()
    if (
        not resolved.is_relative_to(resolved_root)
        or not resolved.is_relative_to(expected_generation)
    ):
        raise ValueError("managed RAG index path escapes generation root")
    if not resolved.is_dir():
        raise FileNotFoundError(f"managed RAG index directory not found: {resolved}")
    return resolved


def _source_matches_metadata(metadata: dict[str, Any] | None) -> bool:
    if metadata is None:
        return False
    source_path = metadata.get("source_path")
    if not isinstance(source_path, str) or not source_path:
        return False
    try:
        stat = Path(source_path).stat()
    except OSError:
        return False
    return (
        stat.st_size == metadata.get("source_size")
        and stat.st_mtime_ns == metadata.get("source_mtime_ns")
    )


def _reranker_from_config(config: RagRuntimeConfig) -> RerankerProvider | None:
    if config.reranker_provider is None:
        return None
    if config.reranker_provider == "fake":
        return FakeRerankerProvider()
    from fireclaw_core.rag.reranking import BGEFlagRerankerProvider

    assert config.reranker_model_path is not None
    return BGEFlagRerankerProvider(
        config.reranker_model_path,
        device=config.device,
    )


def _hit_record(hit: Any) -> dict[str, Any]:
    record = hit.get("record", hit) if isinstance(hit, dict) else getattr(hit, "record", None)
    if not isinstance(record, dict):
        raise ValueError("RAG hit must contain a record mapping")
    return record


def _positive_rank(hit: Any, fallback: int) -> int:
    value = hit.get("rank") if isinstance(hit, dict) else getattr(hit, "rank", None)
    return value if isinstance(value, int) and value > 0 else fallback


def _record_identity(record: dict[str, Any]) -> str | None:
    for key in ("record_id", "event_id", "knowledge_id", "chunk_id", "parent_id"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _record_text(record: dict[str, Any]) -> str:
    for key in ("clean_text", "text"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    content = record.get("content")
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False, sort_keys=True)
    return json.dumps(record, ensure_ascii=False, sort_keys=True)
