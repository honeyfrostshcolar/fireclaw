"""Admission boundary for external RAG knowledge used during planning."""
from __future__ import annotations

import math
from typing import Any, Protocol, Sequence, runtime_checkable


EXTERNAL_KNOWLEDGE_SOURCE_KIND = "external_knowledge"
DEFAULT_MAX_EXCERPT_CHARS = 1800
MAX_KNOWLEDGE_ID_CHARS = 256
MAX_PROVENANCE_CHARS = 2048
MAX_METADATA_CHARS = 512
MAX_LANGUAGE_CHARS = 64


@runtime_checkable
class KnowledgeRagRetriever(Protocol):
    def query(self, query: str, *, top_k: int = 5) -> Sequence[Any]:
        ...


class ExternalKnowledgeRagAdapter:
    """Convert typed external RAG hits into bounded planner references."""

    def __init__(
        self,
        retriever: KnowledgeRagRetriever,
        *,
        candidate_multiplier: int = 3,
        max_excerpt_chars: int = DEFAULT_MAX_EXCERPT_CHARS,
    ) -> None:
        if candidate_multiplier <= 0:
            raise ValueError("candidate_multiplier must be positive")
        if max_excerpt_chars <= 0:
            raise ValueError("max_excerpt_chars must be positive")
        self._retriever = retriever
        self._candidate_multiplier = candidate_multiplier
        self._max_excerpt_chars = max_excerpt_chars

    def status(self) -> dict[str, Any]:
        nested_status = None
        status_fn = getattr(self._retriever, "status", None)
        if callable(status_fn):
            try:
                nested_status = status_fn()
            except Exception:
                nested_status = {"available": False}
        return {
            "configured": True,
            "source_kind": EXTERNAL_KNOWLEDGE_SOURCE_KIND,
            "candidate_multiplier": self._candidate_multiplier,
            "max_excerpt_chars": self._max_excerpt_chars,
            "rag_status": nested_status,
        }

    def retrieve(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        hits = self._retriever.query(
            query,
            top_k=max(limit, 1) * self._candidate_multiplier,
        )
        accepted: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hit in hits:
            record = _hit_record(hit)
            canonical = self._canonicalize(record, score=_hit_score(hit))
            if canonical is None:
                continue
            knowledge_id = canonical["knowledge_id"]
            if knowledge_id in seen:
                continue
            seen.add(knowledge_id)
            accepted.append(canonical)
            if len(accepted) >= limit:
                break
        return accepted

    def _canonicalize(
        self,
        record: dict[str, Any] | None,
        *,
        score: float,
    ) -> dict[str, Any] | None:
        if record is None:
            return None
        if record.get("source_kind") != EXTERNAL_KNOWLEDGE_SOURCE_KIND:
            return None
        if record.get("indexable") is False:
            return None

        knowledge_id = _bounded_string(
            record.get("chunk_id"),
            max_chars=MAX_KNOWLEDGE_ID_CHARS,
        )
        text = _nonempty_string(record.get("clean_text"), record.get("text"))
        allowed_use = _bounded_string(
            record.get("allowed_use"),
            max_chars=MAX_METADATA_CHARS,
        )
        source_url = _bounded_string(
            record.get("source_url"),
            max_chars=MAX_PROVENANCE_CHARS,
        )
        source_file = _bounded_string(
            record.get("source_file"),
            max_chars=MAX_PROVENANCE_CHARS,
        )
        if knowledge_id is None or text is None or allowed_use is None:
            return None
        if source_url is None and source_file is None:
            return None

        page_start = _positive_int_or_none(record.get("page_start"))
        page_end = _positive_int_or_none(record.get("page_end")) or page_start
        if page_start is not None and page_end is not None and page_end < page_start:
            return None
        excerpt = text
        truncated = len(excerpt) > self._max_excerpt_chars
        if truncated:
            excerpt = excerpt[: self._max_excerpt_chars].rstrip() + "..."

        return {
            "knowledge_scope": EXTERNAL_KNOWLEDGE_SOURCE_KIND,
            "knowledge_id": knowledge_id,
            "doc_id": _bounded_string(
                record.get("doc_id"),
                max_chars=MAX_KNOWLEDGE_ID_CHARS,
            ),
            "title": _bounded_string(
                record.get("title"),
                max_chars=MAX_METADATA_CHARS,
            ),
            "heading": _bounded_string(
                record.get("heading"),
                max_chars=MAX_METADATA_CHARS,
            ),
            "excerpt": excerpt,
            "excerpt_truncated": truncated,
            "citation": _citation(
                source_url=source_url,
                source_file=source_file,
                page_start=page_start,
                page_end=page_end,
            ),
            "source_url": source_url,
            "source_file": source_file,
            "page_start": page_start,
            "page_end": page_end,
            "publisher": _bounded_string(
                record.get("publisher"),
                max_chars=MAX_METADATA_CHARS,
            ),
            "authority_level": _bounded_string(
                record.get("authority_level"),
                max_chars=MAX_METADATA_CHARS,
            ),
            "allowed_use": allowed_use,
            "domain": _bounded_string(
                record.get("domain"),
                max_chars=MAX_METADATA_CHARS,
            ),
            "language": _bounded_string(
                record.get("language"),
                max_chars=MAX_LANGUAGE_CHARS,
            ) or "unknown",
            "score": score,
            "advisory_only": True,
            "can_authorize_action": False,
            "can_assert_current_state": False,
            "requires_current_state_revalidation": True,
            "content_trust": "external_reference",
        }


def _hit_record(hit: Any) -> dict[str, Any] | None:
    if isinstance(hit, dict):
        candidate = hit.get("record", hit)
    else:
        candidate = getattr(hit, "record", None)
    return candidate if isinstance(candidate, dict) else None


def _hit_score(hit: Any) -> float:
    value = hit.get("score") if isinstance(hit, dict) else getattr(hit, "score", None)
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return score if math.isfinite(score) else 0.0


def _nonempty_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _bounded_string(value: Any, *, max_chars: int) -> str | None:
    normalized = _nonempty_string(value)
    if normalized is None or len(normalized) > max_chars:
        return None
    return normalized


def _positive_int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _citation(
    *,
    source_url: str | None,
    source_file: str | None,
    page_start: int | None,
    page_end: int | None,
) -> str:
    source = source_url or source_file or "unknown"
    if page_start is None:
        return source
    if page_end is None or page_end == page_start:
        return f"{source}#page={page_start}"
    return f"{source}#pages={page_start}-{page_end}"
