"""Ranked memory retrieval with optional embedding support.

This module provides ``MemoryRetriever``, which combines FTS5 lexical search
with optional embedding-based similarity search using rank fusion.

When no ``EmbeddingProvider`` is configured the retriever falls back to pure
FTS5 lexical retrieval, matching the existing ``SqliteMemoryIndex.search()``
behaviour exactly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fireclaw_core.memory.embodied_memory import (
    MEMORY_RUNTIME_MODES,
    MEMORY_SENSITIVITY_LEVELS,
)
from fireclaw_core.memory.memory_index import SqliteMemoryIndex, _cosine_similarity


# ---------------------------------------------------------------------------
# Protocols and result types
# ---------------------------------------------------------------------------


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for optional embedding providers."""

    def embed(self, text: str) -> list[float]:
        """Return an embedding vector for the given text."""
        ...

    @property
    def dimensions(self) -> int:
        """Return the dimensionality of embeddings."""
        ...


@dataclass(frozen=True)
class MemoryRetrievalScope:
    """Explicit boundaries for planner-facing memory retrieval."""

    mission_ids: tuple[str, ...]
    runtime_modes: tuple[str, ...]
    allowed_sensitivities: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.mission_ids or any(
            not isinstance(value, str) or not value.strip()
            for value in self.mission_ids
        ):
            raise ValueError("mission_ids must contain non-empty strings")
        if not self.runtime_modes or set(self.runtime_modes) - MEMORY_RUNTIME_MODES:
            raise ValueError("runtime_modes must contain valid memory runtime modes")
        if (
            not self.allowed_sensitivities
            or set(self.allowed_sensitivities) - MEMORY_SENSITIVITY_LEVELS
        ):
            raise ValueError(
                "allowed_sensitivities must contain valid memory sensitivity levels"
            )

    def admits(self, value: dict[str, Any]) -> bool:
        return (
            value.get("mission_id") in self.mission_ids
            and value.get("runtime_mode") in self.runtime_modes
            and value.get("sensitivity") in self.allowed_sensitivities
        )


@dataclass(frozen=True)
class RetrievedMemory:
    """A single memory result with its relevance score."""

    record_id: str
    mission_id: str
    record_type: str
    content: dict[str, Any]
    score: float  # combined relevance score (higher = more relevant)
    source: str   # "lexical", "embedding", or "fused"
    created_at: str = ""
    robot_id: str | None = None
    subtask_id: str | None = None


# ---------------------------------------------------------------------------
# Rank fusion retriever
# ---------------------------------------------------------------------------


class MemoryRetriever:
    """Ranked memory retrieval with optional embedding support.

    Parameters
    ----------
    index:
        The FTS5-backed memory index to search.
    embedding_provider:
        Optional provider for generating query embeddings and enabling
        embedding-based similarity search.
    lexical_weight:
        Weight for the lexical (FTS5 BM25) score in rank fusion.
    embedding_weight:
        Weight for the embedding cosine similarity score in rank fusion.
    """

    def __init__(
        self,
        index: SqliteMemoryIndex | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        *,
        lexical_weight: float = 0.6,
        embedding_weight: float = 0.4,
    ) -> None:
        self._index = index
        self._embedding_provider = embedding_provider
        self._lexical_weight = lexical_weight
        self._embedding_weight = embedding_weight

    def status(self) -> dict[str, Any]:
        """Return a cheap status report for preflight checks."""
        index_available = self._index is not None
        embedding_configured = self._embedding_provider is not None
        embedding_available: bool | None = None

        if embedding_configured:
            try:
                # Cheap probe: check dimensions property.
                dims = self._embedding_provider.dimensions  # type: ignore[union-attr]
                embedding_available = dims > 0
            except Exception:
                embedding_available = False

        last_indexing_timestamp: str | None = None
        if index_available:
            try:
                index_path = getattr(self._index, "_path", None)
                if index_path is not None:
                    mtime = Path(index_path).stat().st_mtime
                    last_indexing_timestamp = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
            except Exception:
                pass

        return {
            "lexical_index_available": index_available,
            "embedding_provider_configured": embedding_configured,
            "embedding_provider_available": embedding_available,
            "last_indexing_timestamp": last_indexing_timestamp,
            "lexical_weight": self._lexical_weight,
            "embedding_weight": self._embedding_weight,
        }

    def retrieve(
        self,
        query: str,
        *,
        scope: MemoryRetrievalScope,
        limit: int = 10,
    ) -> list[RetrievedMemory]:
        """Retrieve memories ranked by relevance.

        If no embedding provider is configured, falls back to pure lexical
        (FTS5).  If an embedding provider is configured, uses rank fusion
        of lexical + embedding scores.
        """
        if limit <= 0:
            return []

        if self._index is None:
            return []

        # Step 1: Lexical search — always run for candidate retrieval.
        lexical_hits = self._scoped_lexical_hits(
            query,
            scope=scope,
            candidate_limit=limit * 3,
        )
        if not lexical_hits:
            return []

        if self._embedding_provider is None:
            # Pure lexical mode.
            results: list[RetrievedMemory] = []
            for hit in lexical_hits[:limit]:
                results.append(
                    RetrievedMemory(
                        record_id=hit["record_id"],
                        mission_id=hit.get("mission_id", ""),
                        record_type=hit.get("record_type", ""),
                        content=hit.get("content", {}),
                        score=1.0,  # FTS5 doesn't expose raw BM25 in a
                        # normalized way; treat as relevance.
                        source="lexical",
                        created_at=hit.get("created_at", ""),
                        robot_id=hit.get("robot_id"),
                        subtask_id=hit.get("subtask_id"),
                    )
                )
            return results

        # Step 2: Rank fusion with embeddings.
        return self._rank_fusion(query, lexical_hits, limit)

    # -- internal helpers ----------------------------------------------------

    def _scoped_lexical_hits(
        self,
        query: str,
        *,
        scope: MemoryRetrievalScope,
        candidate_limit: int,
    ) -> list[dict[str, Any]]:
        """Run scoped FTS5 queries and return deduplicated, ordered hits.

        .. note::

            **Combinatorial cost.** This method issues one ``search()`` call
            per ``(mission_id, runtime_mode, sensitivity)`` triple, giving a
            total cost of
            ``O(|mission_ids| x |runtime_modes| x |sensitivities|)`` queries.
            For the typical single-mission, single-mode, single-sensitivity
            case this is a single query.  Caller should be aware that
            expanding these tuples (e.g. passing all modes) multiplies the
            number of FTS5 queries linearly.
        """
        assert self._index is not None
        by_id: dict[str, dict[str, Any]] = {}
        for mission_id in scope.mission_ids:
            for runtime_mode in scope.runtime_modes:
                for sensitivity in scope.allowed_sensitivities:
                    hits = self._index.search(
                        query,
                        filters={
                            "mission_id": mission_id,
                            "runtime_mode": runtime_mode,
                            "sensitivity": sensitivity,
                        },
                        limit=candidate_limit,
                    )
                    for hit in hits:
                        if scope.admits(hit):
                            by_id[str(hit["record_id"])] = hit
        ordered = sorted(
            by_id.values(),
            key=lambda hit: (
                str(hit.get("created_at") or ""),
                str(hit.get("record_id") or ""),
            ),
            reverse=True,
        )
        return ordered[:candidate_limit]

    def _rank_fusion(
        self,
        query: str,
        lexical_hits: list[dict[str, Any]],
        limit: int,
    ) -> list[RetrievedMemory]:
        """Combine lexical and embedding scores via weighted rank fusion."""
        provider = self._embedding_provider
        assert provider is not None

        query_embedding = provider.embed(query)

        # Normalize lexical scores to [0, 1].  FTS5 results come back in
        # relevance order but without raw BM25 scores exposed.  We assign
        # rank-based scores: highest rank gets 1.0, lowest gets 0.0.
        n = len(lexical_hits)
        lexical_scores: dict[str, float] = {}
        for i, hit in enumerate(lexical_hits):
            rid = hit["record_id"]
            lexical_scores[rid] = (n - i) / n if n > 1 else 1.0

        # Compute embedding similarities for each candidate.
        embedding_scores: dict[str, float] = {}
        for hit in lexical_hits:
            rid = hit["record_id"]
            stored = self._index.get_embedding(rid)
            if stored is not None:
                embedding_scores[rid] = _cosine_similarity(query_embedding, stored)
            else:
                embedding_scores[rid] = 0.0

        # Normalize embedding scores to [0, 1].
        emb_values = list(embedding_scores.values())
        if emb_values:
            emb_min = min(emb_values)
            emb_max = max(emb_values)
            emb_range = emb_max - emb_min
            for rid in embedding_scores:
                if emb_range > 0:
                    embedding_scores[rid] = (
                        (embedding_scores[rid] - emb_min) / emb_range
                    )
                else:
                    embedding_scores[rid] = 1.0

        # Combine scores.
        combined: list[tuple[float, dict[str, Any]]] = []
        for hit in lexical_hits:
            rid = hit["record_id"]
            score = (
                self._lexical_weight * lexical_scores.get(rid, 0.0)
                + self._embedding_weight * embedding_scores.get(rid, 0.0)
            )
            combined.append((score, hit))

        # Sort by combined score descending.
        combined.sort(key=lambda x: x[0], reverse=True)

        results: list[RetrievedMemory] = []
        for score, hit in combined[:limit]:
            results.append(
                RetrievedMemory(
                    record_id=hit["record_id"],
                    mission_id=hit.get("mission_id", ""),
                    record_type=hit.get("record_type", ""),
                    content=hit.get("content", {}),
                    score=score,
                    source="fused",
                    created_at=hit.get("created_at", ""),
                    robot_id=hit.get("robot_id"),
                    subtask_id=hit.get("subtask_id"),
                )
            )
        return results
