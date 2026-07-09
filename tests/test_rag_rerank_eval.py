from __future__ import annotations

from fireclaw_core.rag.dense_eval import DenseEvalCase
from fireclaw_core.rag.dense_retrieval import DenseHit
from fireclaw_core.rag.query_expansion import QueryExpansion
from fireclaw_core.rag.relevance_eval import RelevanceJudgment
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


def test_hybrid_rerank_eval_supports_multi_query_rrf() -> None:
    from fireclaw_core.rag.rerank_eval import evaluate_hybrid_retrievers_with_rerank

    class FakeDenseRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(rank=1, score=0.9, record={"chunk_id": "dense_en", "parent_id": "parent_en", "doc_id": "doc"}),
                DenseHit(rank=2, score=0.8, record={"chunk_id": "dense_terms", "parent_id": "parent_terms", "doc_id": "doc"}),
                DenseHit(rank=3, score=0.7, record={"chunk_id": "dense_both", "parent_id": "parent_both", "doc_id": "doc"}),
            ]

    class FakeBM25Retriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(rank=1, score=3.0, record={"chunk_id": "bm25_terms", "parent_id": "parent_terms", "doc_id": "doc"}),
                DenseHit(rank=2, score=2.0, record={"chunk_id": "bm25_both", "parent_id": "parent_both", "doc_id": "doc"}),
                DenseHit(rank=3, score=1.0, record={"chunk_id": "bm25_en", "parent_id": "parent_en", "doc_id": "doc"}),
            ]

    case = DenseEvalCase(
        case_id="case",
        topic="smokeview",
        query="ventilation explanation LOAD3DSMOKE HRRPUV smokeview",
        gold_parent_ids=["parent_both"],
    )
    expansions = {
        "case": QueryExpansion(
            case_id="case",
            query_zh="ventilation explanation LOAD3DSMOKE HRRPUV smokeview",
            reviewed_query_en="ventilation explanation",
            term_query="LOAD3DSMOKE HRRPUV",
            terms=["LOAD3DSMOKE", "HRRPUV"],
            status="reviewed",
        )
    }
    parent_texts = {
        "parent_en": "ventilation natural language explanation",
        "parent_terms": "LOAD3DSMOKE HRRPUV smokeview command",
        "parent_both": "ventilation explanation LOAD3DSMOKE HRRPUV smokeview",
    }

    report = evaluate_hybrid_retrievers_with_rerank(
        FakeDenseRetriever(),
        FakeBM25Retriever(),
        FakeRerankerProvider(),
        [case],
        query_expansions=expansions,
        parent_texts=parent_texts,
        dense_query_variants=["zh", "en", "terms"],
        bm25_query_variants=["en", "terms"],
        rerank_query_variants=["zh", "en", "terms"],
        rerank_pool_size=10,
        top_k=10,
        require_reviewed_expansions=True,
    )

    assert report.hit_at_1 == 1.0
    assert report.results[0].top_hits[0].parent_id == "parent_both"
    assert report.results[0].top_hits[0].reranker == "fake-reranker:rrf"
    assert report.results[0].query_variants["rerank:zh"] == "ventilation explanation LOAD3DSMOKE HRRPUV smokeview"
    assert report.results[0].query_variants["rerank:en"] == "ventilation explanation"
    assert report.results[0].query_variants["rerank:terms"] == "LOAD3DSMOKE HRRPUV"
    assert report.retrieval_config is not None
    assert report.retrieval_config["retrieval_method"] == "hybrid_rerank"
    assert report.retrieval_config["rerank_query_variants"] == ["zh", "en", "terms"]
    assert report.retrieval_config["rerank_fusion"] == "rrf"
    assert report.retrieval_config["rerank_rrf_k"] == 60


def test_hybrid_rerank_eval_supports_joint_fusion_rrf() -> None:
    from fireclaw_core.rag.rerank_eval import evaluate_hybrid_retrievers_with_rerank

    class FakeDenseRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(rank=1, score=0.9, record={"chunk_id": "dense_hybrid", "parent_id": "parent_hybrid", "doc_id": "doc"}),
                DenseHit(rank=2, score=0.8, record={"chunk_id": "dense_middle", "parent_id": "parent_middle", "doc_id": "doc"}),
                DenseHit(rank=3, score=0.7, record={"chunk_id": "dense_rerank", "parent_id": "parent_rerank", "doc_id": "doc"}),
            ]

    class FakeBM25Retriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(rank=1, score=3.0, record={"chunk_id": "bm25_hybrid", "parent_id": "parent_hybrid", "doc_id": "doc"}),
                DenseHit(rank=2, score=2.0, record={"chunk_id": "bm25_middle", "parent_id": "parent_middle", "doc_id": "doc"}),
                DenseHit(rank=3, score=1.0, record={"chunk_id": "bm25_rerank", "parent_id": "parent_rerank", "doc_id": "doc"}),
            ]

    case = DenseEvalCase(
        case_id="case",
        topic="joint_fusion",
        query="alpha beta gamma",
        gold_parent_ids=["parent_hybrid"],
    )
    expansions = {
        "case": QueryExpansion(
            case_id="case",
            query_zh="alpha beta gamma",
            reviewed_query_en="alpha beta gamma",
            term_query="alpha beta gamma",
            terms=["alpha", "beta", "gamma"],
            status="reviewed",
        )
    }
    parent_texts = {
        "parent_hybrid": "alpha",
        "parent_middle": "alpha beta",
        "parent_rerank": "alpha beta gamma",
    }

    rerank_only = evaluate_hybrid_retrievers_with_rerank(
        FakeDenseRetriever(),
        FakeBM25Retriever(),
        FakeRerankerProvider(),
        [case],
        query_expansions=expansions,
        parent_texts=parent_texts,
        dense_query_variants=["zh"],
        bm25_query_variants=["en"],
        rerank_pool_size=10,
        top_k=10,
        require_reviewed_expansions=True,
    )
    joint = evaluate_hybrid_retrievers_with_rerank(
        FakeDenseRetriever(),
        FakeBM25Retriever(),
        FakeRerankerProvider(),
        [case],
        query_expansions=expansions,
        parent_texts=parent_texts,
        dense_query_variants=["zh"],
        bm25_query_variants=["en"],
        rerank_pool_size=10,
        top_k=10,
        require_reviewed_expansions=True,
        joint_fusion="rrf",
    )

    assert [hit.parent_id for hit in rerank_only.results[0].top_hits] == [
        "parent_rerank",
        "parent_middle",
        "parent_hybrid",
    ]
    assert [hit.parent_id for hit in joint.results[0].top_hits] == [
        "parent_hybrid",
        "parent_rerank",
        "parent_middle",
    ]
    assert joint.results[0].top_hits[0].variant_ranks["hybrid"] == 1
    assert joint.results[0].top_hits[0].variant_ranks["rerank"] == 3
    assert joint.results[0].top_hits[0].variant_scores["rerank"] == 1.0 / 3.0
    assert rerank_only.retrieval_config is not None
    assert "joint_fusion" not in rerank_only.retrieval_config
    assert joint.retrieval_config is not None
    assert joint.retrieval_config["joint_fusion"] == "rrf"
    assert joint.retrieval_config["joint_rrf_k"] == 60


def test_hybrid_rerank_eval_joint_fusion_threads_relevance_judgments() -> None:
    from fireclaw_core.rag.rerank_eval import evaluate_hybrid_retrievers_with_rerank

    class FakeDenseRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(rank=1, score=0.9, record={"chunk_id": "dense_hybrid", "parent_id": "parent_hybrid", "doc_id": "doc"}),
                DenseHit(rank=2, score=0.8, record={"chunk_id": "dense_middle", "parent_id": "parent_middle", "doc_id": "doc"}),
                DenseHit(rank=3, score=0.7, record={"chunk_id": "dense_rerank", "parent_id": "parent_rerank", "doc_id": "doc"}),
            ]

    class FakeBM25Retriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(rank=1, score=3.0, record={"chunk_id": "bm25_hybrid", "parent_id": "parent_hybrid", "doc_id": "doc"}),
                DenseHit(rank=2, score=2.0, record={"chunk_id": "bm25_middle", "parent_id": "parent_middle", "doc_id": "doc"}),
                DenseHit(rank=3, score=1.0, record={"chunk_id": "bm25_rerank", "parent_id": "parent_rerank", "doc_id": "doc"}),
            ]

    case = DenseEvalCase(
        case_id="case",
        topic="joint_fusion_relevance",
        query="alpha beta gamma",
        gold_parent_ids=["parent_hybrid"],
    )
    expansions = {
        "case": QueryExpansion(
            case_id="case",
            query_zh="alpha beta gamma",
            reviewed_query_en="alpha beta gamma",
            term_query="alpha beta gamma",
            terms=["alpha", "beta", "gamma"],
            status="reviewed",
        )
    }
    parent_texts = {
        "parent_hybrid": "alpha",
        "parent_middle": "alpha beta",
        "parent_rerank": "alpha beta gamma",
    }

    report_without_judgments = evaluate_hybrid_retrievers_with_rerank(
        FakeDenseRetriever(),
        FakeBM25Retriever(),
        FakeRerankerProvider(),
        [case],
        query_expansions=expansions,
        parent_texts=parent_texts,
        dense_query_variants=["zh"],
        bm25_query_variants=["en"],
        rerank_pool_size=10,
        top_k=10,
        require_reviewed_expansions=True,
        joint_fusion="rrf",
    )

    assert report_without_judgments.ndcg_at_10 is None
    assert "ndcg_at_10" not in report_without_judgments.to_dict()
    assert "ndcg_at_10" not in report_without_judgments.to_dict()["results"][0]

    report_with_judgments = evaluate_hybrid_retrievers_with_rerank(
        FakeDenseRetriever(),
        FakeBM25Retriever(),
        FakeRerankerProvider(),
        [case],
        query_expansions=expansions,
        parent_texts=parent_texts,
        dense_query_variants=["zh"],
        bm25_query_variants=["en"],
        rerank_pool_size=10,
        top_k=10,
        require_reviewed_expansions=True,
        joint_fusion="rrf",
        relevance_judgments={
            "case": {
                "parent_hybrid": RelevanceJudgment(case_id="case", parent_id="parent_hybrid", grade=3),
                "parent_rerank": RelevanceJudgment(case_id="case", parent_id="parent_rerank", grade=2),
                "parent_middle": RelevanceJudgment(case_id="case", parent_id="parent_middle", grade=0),
            }
        },
    )

    assert report_with_judgments.ndcg_at_10 == 1.0
    assert report_with_judgments.to_dict()["ndcg_at_10"] == 1.0
    assert report_with_judgments.to_dict()["results"][0]["ndcg_at_10"] == 1.0


def test_hybrid_rerank_eval_supports_small_chunk_rerank_to_parent() -> None:
    from fireclaw_core.rag.rerank_eval import evaluate_hybrid_retrievers_with_rerank

    class FakeDenseRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(
                    rank=1,
                    score=0.9,
                    record={
                        "chunk_id": "chunk_generic",
                        "parent_id": "parent_generic",
                        "doc_id": "doc",
                        "clean_text": "thermal victim evidence only in preview",
                    },
                ),
                DenseHit(
                    rank=2,
                    score=0.8,
                    record={
                        "chunk_id": "chunk_gold_evidence",
                        "parent_id": "parent_gold",
                        "doc_id": "doc",
                        "clean_text": "generic preview",
                    },
                ),
                DenseHit(
                    rank=3,
                    score=0.7,
                    record={
                        "chunk_id": "chunk_gold_context",
                        "parent_id": "parent_gold",
                        "doc_id": "doc",
                        "clean_text": "background context",
                    },
                ),
            ]

    class FakeBM25Retriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(
                    rank=1,
                    score=3.0,
                    record={"chunk_id": "chunk_generic", "parent_id": "parent_generic", "doc_id": "doc"},
                ),
                DenseHit(
                    rank=2,
                    score=2.0,
                    record={"chunk_id": "chunk_gold_evidence", "parent_id": "parent_gold", "doc_id": "doc"},
                ),
                DenseHit(
                    rank=3,
                    score=1.0,
                    record={"chunk_id": "chunk_gold_context", "parent_id": "parent_gold", "doc_id": "doc"},
                ),
            ]

    case = DenseEvalCase(
        case_id="case",
        topic="thermal_search",
        query="thermal victim evidence",
        gold_parent_ids=["parent_gold"],
    )
    parent_texts = {
        "parent_generic": "thermal victim evidence in the full parent text",
        "parent_gold": "generic parent text",
    }
    small_texts = {
        "chunk_generic": "generic operational context",
        "chunk_gold_evidence": "thermal victim evidence behind the closed door",
        "chunk_gold_context": "background context for the same parent",
    }
    expansions = {
        "case": QueryExpansion(
            case_id="case",
            query_zh="thermal victim evidence",
            reviewed_query_en="thermal victim evidence",
            term_query="thermal victim evidence",
            terms=["thermal", "victim", "evidence"],
            status="reviewed",
        )
    }

    report = evaluate_hybrid_retrievers_with_rerank(
        FakeDenseRetriever(),
        FakeBM25Retriever(),
        FakeRerankerProvider(),
        [case],
        query_expansions=expansions,
        parent_texts=parent_texts,
        small_texts=small_texts,
        rerank_level="small",
        dense_query_variants=["zh"],
        bm25_query_variants=["en"],
        rerank_pool_size=10,
        top_k=10,
    )

    top_hit = report.results[0].top_hits[0]
    assert report.hit_at_1 == 1.0
    assert top_hit.parent_id == "parent_gold"
    assert top_hit.chunk_id == "chunk_gold_evidence"
    assert top_hit.base_rank == 2
    assert top_hit.child_hit_count == 2
    assert top_hit.child_ranks == [1, 3]
    assert report.retrieval_config is not None
    assert report.retrieval_config["rerank_level"] == "small"
    assert report.retrieval_config["candidate_ranking_view"] == "small"
