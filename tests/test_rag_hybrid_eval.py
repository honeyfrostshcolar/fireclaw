from __future__ import annotations

from fireclaw_core.rag.dense_eval import DenseEvalCase
from fireclaw_core.rag.dense_retrieval import DenseHit
from fireclaw_core.rag.query_expansion import QueryExpansion


def test_hybrid_eval_fuses_dense_and_bm25_parent_lists() -> None:
    from fireclaw_core.rag.hybrid_eval import evaluate_hybrid_retrievers_with_expansion

    class FakeDenseRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(
                    rank=1,
                    score=0.9,
                    record={"chunk_id": "dense_wrong", "parent_id": "parent_wrong", "doc_id": "doc"},
                ),
                DenseHit(
                    rank=2,
                    score=0.8,
                    record={"chunk_id": "dense_gold", "parent_id": "parent_gold", "doc_id": "doc"},
                ),
            ]

    class FakeBM25Retriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(
                    rank=1,
                    score=4.0,
                    record={"chunk_id": "bm25_gold", "parent_id": "parent_gold", "doc_id": "doc"},
                ),
                DenseHit(
                    rank=2,
                    score=2.0,
                    record={"chunk_id": "bm25_other", "parent_id": "parent_other", "doc_id": "doc"},
                ),
            ]

    case = DenseEvalCase(
        case_id="case_hybrid",
        topic="rehab",
        query="\u4e2d\u6587",
        gold_parent_ids=["parent_gold"],
    )
    expansions = {
        "case_hybrid": QueryExpansion(
            case_id="case_hybrid",
            query_zh="\u4e2d\u6587",
            reviewed_query_en="English SCBA query",
            term_query="SCBA rehabilitation",
            terms=["SCBA", "rehabilitation"],
            status="reviewed",
        )
    }

    report = evaluate_hybrid_retrievers_with_expansion(
        FakeDenseRetriever(),
        FakeBM25Retriever(),
        [case],
        query_expansions=expansions,
        dense_query_variants=["zh", "en"],
        bm25_query_variants=["en", "terms"],
        ranking_view="parent",
        small_top_k=10,
        top_k=10,
        require_reviewed_expansions=True,
    )

    assert report.hit_at_1 == 1.0
    top_hit = report.results[0].top_hits[0]
    assert top_hit.parent_id == "parent_gold"
    assert top_hit.variant_ranks == {"bm25": 1, "dense": 2}
    assert report.retrieval_config is not None
    assert report.retrieval_config["retrieval_method"] == "hybrid"
    assert report.retrieval_config["dense_query_variants"] == ["zh", "en"]
    assert report.retrieval_config["bm25_query_variants"] == ["en", "terms"]
