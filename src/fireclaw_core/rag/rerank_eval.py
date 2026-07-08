from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from fireclaw_core.rag.dense_eval import DenseEvalCase
from fireclaw_core.rag.dense_eval import DenseEvalReport
from fireclaw_core.rag.dense_eval import DenseEvalRetrievedHit
from fireclaw_core.rag.dense_eval import evaluate_ranked_hits
from fireclaw_core.rag.hybrid_eval import evaluate_hybrid_retrievers_with_expansion
from fireclaw_core.rag.query_expansion import QueryExpansion
from fireclaw_core.rag.reranking import RerankerProvider
from fireclaw_core.rag.reranking import rerank_parent_hits_with_rrf
from fireclaw_core.rag.reranking import rerank_parent_hits
from fireclaw_core.rag.reranking import select_rerank_queries
from fireclaw_core.rag.reranking import select_rerank_query


def evaluate_hybrid_retrievers_with_rerank(
    dense_retriever: Any,
    bm25_retriever: Any,
    reranker: RerankerProvider,
    cases: list[DenseEvalCase],
    *,
    query_expansions: Mapping[str, QueryExpansion] | None = None,
    parent_texts: Mapping[str, str],
    dense_query_variants: Sequence[str] = ("zh", "en", "terms"),
    bm25_query_variants: Sequence[str] = ("en", "terms"),
    rerank_query_variant: str = "en",
    rerank_query_variants: Sequence[str] | None = None,
    rerank_pool_size: int = 50,
    top_k: int = 10,
    small_top_k: int = 50,
    parent_aggregation: str = "max",
    rrf_k: int = 60,
    max_passage_chars: int = 6000,
    require_reviewed_expansions: bool = False,
    query_expansions_path: str | None = None,
    parent_chunks_path: str | None = None,
) -> DenseEvalReport:
    if rerank_pool_size < top_k:
        raise ValueError("rerank_pool_size must be greater than or equal to top_k")
    if top_k < 10:
        raise ValueError("top_k must be at least 10")

    hybrid_report = evaluate_hybrid_retrievers_with_expansion(
        dense_retriever,
        bm25_retriever,
        cases,
        query_expansions=query_expansions,
        dense_query_variants=dense_query_variants,
        bm25_query_variants=bm25_query_variants,
        ranking_view="parent",
        top_k=rerank_pool_size,
        small_top_k=small_top_k,
        parent_aggregation=parent_aggregation,
        rrf_k=rrf_k,
        require_reviewed_expansions=require_reviewed_expansions,
        query_expansions_path=query_expansions_path,
    )

    case_by_id = {case.case_id: case for case in cases}
    reranked_by_case_id: dict[str, list[DenseEvalRetrievedHit]] = {}
    query_variants_by_case_id: dict[str, dict[str, str]] = {}
    selected_rerank_variants = list(rerank_query_variants or [])

    for result in hybrid_report.results:
        case = case_by_id[result.case_id]
        if selected_rerank_variants:
            rerank_queries = select_rerank_queries(
                result.query_variants,
                fallback_query=case.query,
                variants=selected_rerank_variants,
            )
            reranked_by_case_id[result.case_id] = rerank_parent_hits_with_rrf(
                rerank_queries,
                result.top_hits,
                parent_texts,
                reranker,
                top_k=top_k,
                rrf_k=rrf_k,
                max_passage_chars=max_passage_chars,
            )
            query_variants_by_case_id[result.case_id] = {
                **result.query_variants,
                **{f"rerank:{variant}": query for variant, query in rerank_queries.items()},
            }
        else:
            rerank_query = select_rerank_query(
                result.query_variants,
                fallback_query=case.query,
                variant=rerank_query_variant,
            )
            reranked_by_case_id[result.case_id] = rerank_parent_hits(
                rerank_query,
                result.top_hits,
                parent_texts,
                reranker,
                top_k=top_k,
                max_passage_chars=max_passage_chars,
            )
            query_variants_by_case_id[result.case_id] = {
                **result.query_variants,
                f"rerank:{rerank_query_variant}": rerank_query,
            }

    report = evaluate_ranked_hits(cases, reranked_by_case_id, top_k=max(top_k, 10))
    enriched_results = [
        replace(result, query_variants=query_variants_by_case_id.get(result.case_id, {}))
        for result in report.results
    ]
    return replace(
        report,
        results=enriched_results,
        retrieval_config={
            "retrieval_method": "hybrid_rerank",
            "candidate_retrieval_method": "hybrid",
            "dense_query_variants": list(dense_query_variants),
            "bm25_query_variants": list(bm25_query_variants),
            "rerank_query_variant": rerank_query_variant,
            "rerank_query_variants": selected_rerank_variants or None,
            "rerank_fusion": "rrf" if selected_rerank_variants else "score",
            "rerank_rrf_k": rrf_k if selected_rerank_variants else None,
            "fusion": "rrf",
            "rrf_k": rrf_k,
            "ranking_view": "parent",
            "parent_aggregation": parent_aggregation,
            "small_top_k": small_top_k,
            "rerank_pool_size": rerank_pool_size,
            "final_top_k": top_k,
            "max_passage_chars": max_passage_chars,
            "query_expansions_path": query_expansions_path,
            "parent_chunks_path": parent_chunks_path,
            "require_reviewed_expansions": require_reviewed_expansions,
            "reranker": reranker.model_info.to_dict(),
        },
    )
