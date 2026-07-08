from __future__ import annotations

from fireclaw_core.rag.dense_eval import DenseEvalCase
from fireclaw_core.rag.dense_retrieval import DenseHit
from fireclaw_core.rag.query_expansion import QueryExpansion
from fireclaw_core.rag.reranking import FakeRerankerProvider


def test_hybrid_rerank_eval_promotes_more_relevant_parent() -> None:
    from fireclaw_core.rag.rerank_eval import evaluate_hybrid_retrievers_with_rerank

    class FakeDenseRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(
                    rank=1,
                    score=0.9,
                    record={"chunk_id": "dense_generic", "parent_id": "parent_generic", "doc_id": "doc"},
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
                    score=3.0,
                    record={"chunk_id": "bm25_generic", "parent_id": "parent_generic", "doc_id": "doc"},
                ),
                DenseHit(
                    rank=2,
                    score=2.0,
                    record={"chunk_id": "bm25_gold", "parent_id": "parent_gold", "doc_id": "doc"},
                ),
            ]

    case = DenseEvalCase(
        case_id="case",
        topic="rehab",
        query="中文问题",
        gold_parent_ids=["parent_gold"],
    )
    expansions = {
        "case": QueryExpansion(
            case_id="case",
            query_zh="中文问题",
            reviewed_query_en="When should firefighters enter SCBA rehabilitation?",
            term_query="SCBA rehabilitation NFPA 1584",
            terms=["SCBA", "rehabilitation", "NFPA 1584"],
            status="reviewed",
        )
    }
    parent_texts = {
        "parent_generic": "generic firefighter health information",
        "parent_gold": "SCBA rehabilitation medical evaluation NFPA 1584",
    }

    report = evaluate_hybrid_retrievers_with_rerank(
        FakeDenseRetriever(),
        FakeBM25Retriever(),
        FakeRerankerProvider(),
        [case],
        query_expansions=expansions,
        parent_texts=parent_texts,
        dense_query_variants=["zh", "en"],
        bm25_query_variants=["en", "terms"],
        rerank_pool_size=10,
        top_k=10,
        require_reviewed_expansions=True,
    )

    assert report.hit_at_1 == 1.0
    assert report.results[0].top_hits[0].parent_id == "parent_gold"
    assert report.results[0].top_hits[0].base_rank == 2
    assert report.results[0].top_hits[0].reranker == "fake-reranker"
    assert report.results[0].query_variants["rerank:en"] == "When should firefighters enter SCBA rehabilitation?"
    assert report.retrieval_config is not None
    assert report.retrieval_config["retrieval_method"] == "hybrid_rerank"
    assert report.retrieval_config["candidate_retrieval_method"] == "hybrid"
    assert report.retrieval_config["rerank_pool_size"] == 10
    assert report.retrieval_config["final_top_k"] == 10
    assert report.retrieval_config["reranker"]["provider"] == "fake-reranker"
