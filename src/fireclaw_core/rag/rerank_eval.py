from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from fireclaw_core.rag.dense_eval import DenseEvalCase
from fireclaw_core.rag.dense_eval import DenseEvalReport
from fireclaw_core.rag.dense_eval import DenseEvalRetrievedHit
from fireclaw_core.rag.dense_eval import evaluate_ranked_hits
from fireclaw_core.rag.hybrid_eval import build_hybrid_candidates_with_expansion
from fireclaw_core.rag.hybrid_eval import evaluate_hybrid_retrievers_with_expansion
from fireclaw_core.rag.query_expansion import QueryExpansion
from fireclaw_core.rag.relevance_eval import RelevanceJudgment
from fireclaw_core.rag.reranking import aggregate_reranked_small_hits_by_parent
from fireclaw_core.rag.reranking import fuse_hybrid_and_rerank_hits
from fireclaw_core.rag.reranking import RerankerProvider
from fireclaw_core.rag.reranking import rerank_parent_hits_with_rrf
from fireclaw_core.rag.reranking import rerank_parent_hits
from fireclaw_core.rag.reranking import rerank_small_hits
from fireclaw_core.rag.reranking import rerank_small_hits_with_rrf
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
    relevance_judgments: Mapping[str, Mapping[str, RelevanceJudgment]] | None = None,
    relevance_threshold: int = 2,
    small_texts: Mapping[str, str] | None = None,
    rerank_level: str = "parent",
    dense_query_variants: Sequence[str] = ("zh", "en", "terms"),
    bm25_query_variants: Sequence[str] = ("en", "terms"),
    rerank_query_variant: str = "en",
    rerank_query_variants: Sequence[str] | None = None,
    joint_fusion: str | None = None,
    rerank_pool_size: int = 50,
    top_k: int = 10,
    small_top_k: int = 50,
    parent_aggregation: str = "max",
    rrf_k: int = 60,
    max_passage_chars: int = 6000,
    require_reviewed_expansions: bool = False,
    query_expansions_path: str | None = None,
    parent_chunks_path: str | None = None,
    small_chunks_path: str | None = None,
    relevance_judgments_path: str | None = None,
) -> DenseEvalReport:
    if rerank_level not in {"parent", "small"}:
        raise ValueError("rerank_level must be 'parent' or 'small'")
    if joint_fusion not in {None, "rrf"}:
        raise ValueError("joint_fusion must be None or 'rrf'")
    if rerank_level == "small" and small_texts is None:
        raise ValueError("small_texts is required when rerank_level='small'")
    if rerank_pool_size < top_k:
        raise ValueError("rerank_pool_size must be greater than or equal to top_k")
    if top_k < 10:
        raise ValueError("top_k must be at least 10")

    case_by_id = {case.case_id: case for case in cases}
    reranked_by_case_id: dict[str, list[DenseEvalRetrievedHit]] = {}
    query_variants_by_case_id: dict[str, dict[str, str]] = {}
    selected_rerank_variants = list(rerank_query_variants or [])

    if rerank_level == "parent":
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
                    top_k=rerank_pool_size if joint_fusion == "rrf" else top_k,
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
                    top_k=rerank_pool_size if joint_fusion == "rrf" else top_k,
                    max_passage_chars=max_passage_chars,
                )
                query_variants_by_case_id[result.case_id] = {
                    **result.query_variants,
                    f"rerank:{rerank_query_variant}": rerank_query,
                }
            if joint_fusion == "rrf":
                reranked_by_case_id[result.case_id] = fuse_hybrid_and_rerank_hits(
                    result.top_hits,
                    reranked_by_case_id[result.case_id],
                    top_k=top_k,
                    rrf_k=rrf_k,
                )
    else:
        small_candidate_batch = build_hybrid_candidates_with_expansion(
            dense_retriever,
            bm25_retriever,
            cases,
            query_expansions=query_expansions,
            dense_query_variants=dense_query_variants,
            bm25_query_variants=bm25_query_variants,
            ranking_view="small",
            top_k=rerank_pool_size,
            small_top_k=small_top_k,
            parent_aggregation=parent_aggregation,
            rrf_k=rrf_k,
            require_reviewed_expansions=require_reviewed_expansions,
        )

        for case in cases:
            candidate_hits = small_candidate_batch.hits_by_case_id.get(case.case_id, [])
            hybrid_parent_hits = aggregate_reranked_small_hits_by_parent(
                candidate_hits,
                top_k=rerank_pool_size,
            )
            query_variants = small_candidate_batch.variants_by_case_id.get(case.case_id, {})
            if selected_rerank_variants:
                rerank_queries = select_rerank_queries(
                    query_variants,
                    fallback_query=case.query,
                    variants=selected_rerank_variants,
                )
                reranked_small_hits = rerank_small_hits_with_rrf(
                    rerank_queries,
                    candidate_hits,
                    small_texts or {},
                    reranker,
                    top_k=rerank_pool_size,
                    rrf_k=rrf_k,
                    max_passage_chars=max_passage_chars,
                )
                query_variants_by_case_id[case.case_id] = {
                    **query_variants,
                    **{f"rerank:{variant}": query for variant, query in rerank_queries.items()},
                }
            else:
                rerank_query = select_rerank_query(
                    query_variants,
                    fallback_query=case.query,
                    variant=rerank_query_variant,
                )
                reranked_small_hits = rerank_small_hits(
                    rerank_query,
                    candidate_hits,
                    small_texts or {},
                    reranker,
                    top_k=rerank_pool_size,
                    max_passage_chars=max_passage_chars,
                )
                query_variants_by_case_id[case.case_id] = {
                    **query_variants,
                    f"rerank:{rerank_query_variant}": rerank_query,
                }
            parent_reranked_hits = aggregate_reranked_small_hits_by_parent(
                reranked_small_hits,
                top_k=rerank_pool_size if joint_fusion == "rrf" else top_k,
            )
            if joint_fusion == "rrf":
                reranked_by_case_id[case.case_id] = fuse_hybrid_and_rerank_hits(
                    hybrid_parent_hits,
                    parent_reranked_hits,
                    top_k=top_k,
                    rrf_k=rrf_k,
                )
            else:
                reranked_by_case_id[case.case_id] = parent_reranked_hits

    report = evaluate_ranked_hits(
        cases,
        reranked_by_case_id,
        top_k=max(top_k, 10),
        relevance_judgments=relevance_judgments,
        relevance_threshold=relevance_threshold,
    )
    enriched_results = [
        replace(result, query_variants=query_variants_by_case_id.get(result.case_id, {}))
        for result in report.results
    ]
    retrieval_config = {
        "retrieval_method": "hybrid_rerank",
        "candidate_retrieval_method": "hybrid",
        "dense_query_variants": list(dense_query_variants),
        "bm25_query_variants": list(bm25_query_variants),
        "rerank_level": rerank_level,
        "candidate_ranking_view": "small" if rerank_level == "small" else "parent",
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
        "small_chunks_path": small_chunks_path,
        "require_reviewed_expansions": require_reviewed_expansions,
        "reranker": reranker.model_info.to_dict(),
    }
    if relevance_judgments is not None:
        retrieval_config["relevance_judgments_path"] = relevance_judgments_path
        retrieval_config["relevance_threshold"] = relevance_threshold
    if joint_fusion == "rrf":
        retrieval_config["joint_fusion"] = "rrf"
        retrieval_config["joint_rrf_k"] = rrf_k
    return replace(
        report,
        results=enriched_results,
        retrieval_config=retrieval_config,
    )
