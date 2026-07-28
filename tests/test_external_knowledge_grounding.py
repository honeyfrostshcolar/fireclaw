from __future__ import annotations

from dataclasses import dataclass

from fireclaw_core.memory.planner_memory_context import (
    PlannerMemoryContextBuilder,
    PlannerMemoryContextRequest,
)
from fireclaw_core.rag.knowledge_grounding import ExternalKnowledgeRagAdapter


@dataclass(frozen=True)
class _Hit:
    record: dict
    score: float = 0.75


class _Retriever:
    def __init__(self, hits):
        self.hits = list(hits)
        self.calls = []

    def query(self, query: str, *, top_k: int = 5):
        self.calls.append((query, top_k))
        return self.hits[:top_k]

    def status(self):
        return {"available": True}


def _record(**overrides):
    value = {
        "source_kind": "external_knowledge",
        "chunk_id": "nfpa-search-001",
        "doc_id": "nfpa-search",
        "clean_text": "Use a systematic primary search while monitoring structural conditions.",
        "indexable": True,
        "source_file": "manuals/search.pdf",
        "source_url": "https://example.test/search.pdf",
        "page_start": 12,
        "page_end": 13,
        "title": "Search Operations",
        "publisher": "Example Fire Academy",
        "authority_level": "training_standard",
        "allowed_use": "planning_reference",
        "domain": "fireground_operations",
        "language": "en",
    }
    value.update(overrides)
    return value


def test_external_knowledge_adapter_canonicalizes_provenance_and_bounds_text():
    retriever = _Retriever([
        _Hit(_record(clean_text="x" * 100)),
        _Hit(_record(clean_text="duplicate")),
    ])
    adapter = ExternalKnowledgeRagAdapter(
        retriever,
        candidate_multiplier=2,
        max_excerpt_chars=20,
    )

    results = adapter.retrieve("primary search", limit=2)

    assert retriever.calls == [("primary search", 4)]
    assert len(results) == 1
    assert results[0]["knowledge_id"] == "nfpa-search-001"
    assert results[0]["citation"] == "https://example.test/search.pdf#pages=12-13"
    assert results[0]["excerpt"] == ("x" * 20) + "..."
    assert results[0]["excerpt_truncated"] is True
    assert results[0]["can_authorize_action"] is False
    assert results[0]["can_assert_current_state"] is False


def test_external_knowledge_adapter_fails_closed_for_untyped_or_unlicensed_hits():
    retriever = _Retriever([
        _Hit(_record(chunk_id="mission", source_kind="mission_memory")),
        _Hit(_record(chunk_id="no-license", allowed_use=None)),
        _Hit(_record(chunk_id="no-source", source_url=None, source_file=None)),
        _Hit(_record(chunk_id="accepted")),
    ])

    results = ExternalKnowledgeRagAdapter(retriever).retrieve("search", limit=5)

    assert [item["knowledge_id"] for item in results] == ["accepted"]


def test_external_knowledge_adapter_bounds_metadata_and_rejects_bad_provenance():
    retriever = _Retriever([
        _Hit(_record(chunk_id="x" * 257)),
        _Hit(_record(chunk_id="bad-pages", page_start=8, page_end=7)),
        _Hit(_record(chunk_id="no-bounded-source", source_url="x" * 2049, source_file=None)),
        _Hit(_record(
            chunk_id="accepted",
            title="x" * 513,
            language="x" * 65,
        ), score=float("nan")),
    ])

    results = ExternalKnowledgeRagAdapter(retriever).retrieve("search", limit=5)

    assert [item["knowledge_id"] for item in results] == ["accepted"]
    assert results[0]["title"] is None
    assert results[0]["language"] == "unknown"
    assert results[0]["score"] == 0.0


def test_planner_context_retrieves_external_knowledge_without_embodied_memory():
    adapter = ExternalKnowledgeRagAdapter(_Retriever([_Hit(_record())]))
    builder = PlannerMemoryContextBuilder(external_knowledge_retriever=adapter)

    result = builder.build(PlannerMemoryContextRequest(
        command="plan a primary search",
        mission_id="mission-1",
        runtime_mode=None,
        requester_id="operator-1",
    ))

    assert [item["knowledge_id"] for item in result.external_knowledge] == [
        "nfpa-search-001"
    ]
    assert result.external_knowledge_available is True
    assert result.memories == ()
    decision = result.guard_decision()
    assert decision.details["accepted_external_knowledge"] == 1
    assert decision.details["external_knowledge_ids"] == ["nfpa-search-001"]
