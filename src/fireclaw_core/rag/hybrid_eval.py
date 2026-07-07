from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from fireclaw_core.rag.dense_eval import DenseEvalCase
from fireclaw_core.rag.dense_eval import DenseEvalReport
from fireclaw_core.rag.dense_eval import DenseEvalRetrievedHit
from fireclaw_core.rag.dense_eval import evaluate_ranked_hits
from fireclaw_core.rag.dense_eval import hits_from_dense_results
from fireclaw_core.rag.dense_ranking import aggregate_hits_by_parent
from fireclaw_core.rag.dense_ranking import reciprocal_rank_fuse
from fireclaw_core.rag.query_expansion import QueryExpansion
from fireclaw_core.rag.query_expansion import QueryVariant
from fireclaw_core.rag.query_expansion import build_query_variants


def evaluate_hybrid_retrievers_with_expansion(
    dense_retriever: Any,
    bm25_retriever: Any,
    cases: list[DenseEvalCase],
    *,
    query_expansions: Mapping[str, QueryExpansion] | None = None,
    dense_query_variants: Sequence[str] = ("zh", "en", "terms"),
    bm25_query_variants: Sequence[str] = ("en", "terms"),
    ranking_view: str = "parent",
    top_k: int = 10,
    small_top_k: int = 50,
    parent_aggregation: str = "max",
    rrf_k: int = 60,
    require_reviewed_expansions: bool = False,
    query_expansions_path: str | None = None,
) -> DenseEvalReport:
    if ranking_view != "parent":
        raise ValueError("hybrid evaluation currently supports ranking_view='parent' only")
    if top_k < 10:
        raise ValueError("top_k must be at least 10")
    if small_top_k <= 0:
        raise ValueError("small_top_k must be positive")

    selected_dense_variants = _validate_query_variants(dense_query_variants, label="dense")
    selected_bm25_variants = _validate_query_variants(bm25_query_variants, label="bm25")
    expansions = query_expansions or {}
    hits_by_case_id: dict[str, list[DenseEvalRetrievedHit]] = {}
    variants_by_case_id: dict[str, dict[str, str]] = {}

    for case in cases:
        dense_variants = build_query_variants(
            case,
            expansions,
            selected_dense_variants,
            require_reviewed=require_reviewed_expansions,
        )
        bm25_variants = build_query_variants(
            case,
            expansions,
            selected_bm25_variants,
            require_reviewed=require_reviewed_expansions,
        )
        variants_by_case_id[case.case_id] = {
            **_variant_metadata("dense", dense_variants),
            **_variant_metadata("bm25", bm25_variants),
        }

        dense_hits = _fuse_query_variants_by_parent(
            dense_retriever,
            dense_variants,
            top_k=small_top_k,
            parent_aggregation=parent_aggregation,
            rrf_k=rrf_k,
        )
        bm25_hits = _fuse_query_variants_by_parent(
            bm25_retriever,
            bm25_variants,
            top_k=small_top_k,
            parent_aggregation=parent_aggregation,
            rrf_k=rrf_k,
        )
        hits_by_case_id[case.case_id] = reciprocal_rank_fuse(
            {"dense": dense_hits, "bm25": bm25_hits},
            identity="parent",
            top_k=top_k,
            rrf_k=rrf_k,
        )

    report = evaluate_ranked_hits(cases, hits_by_case_id, top_k=max(top_k, 10))
    enriched_results = [
        replace(result, query_variants=variants_by_case_id.get(result.case_id, {}))
        for result in report.results
    ]
    return replace(
        report,
        results=enriched_results,
        retrieval_config={
            "retrieval_method": "hybrid",
            "dense_query_variants": selected_dense_variants,
            "bm25_query_variants": selected_bm25_variants,
            "fusion": "rrf",
            "rrf_k": rrf_k,
            "ranking_view": ranking_view,
            "parent_aggregation": parent_aggregation,
            "small_top_k": small_top_k,
            "final_top_k": top_k,
            "query_expansions_path": query_expansions_path,
            "require_reviewed_expansions": require_reviewed_expansions,
        },
    )


def _fuse_query_variants_by_parent(
    retriever: Any,
    variants: list[QueryVariant],
    *,
    top_k: int,
    parent_aggregation: str,
    rrf_k: int,
) -> list[DenseEvalRetrievedHit]:
    hits_by_variant: dict[str, list[DenseEvalRetrievedHit]] = {}
    for variant in variants:
        hits = hits_from_dense_results(retriever.query(variant.text, top_k=top_k))
        hits_by_variant[variant.name] = aggregate_hits_by_parent(
            hits,
            top_k=top_k,
            method=parent_aggregation,
        )
    return reciprocal_rank_fuse(hits_by_variant, identity="parent", top_k=top_k, rrf_k=rrf_k)


def _validate_query_variants(variants: Sequence[str], *, label: str) -> list[str]:
    selected = list(variants)
    if not selected:
        raise ValueError(f"{label}_query_variants must not be empty")
    seen: set[str] = set()
    for variant_name in selected:
        if variant_name in seen:
            raise ValueError(f"duplicate {label} query variant: {variant_name}")
        seen.add(variant_name)
    return selected


def _variant_metadata(prefix: str, variants: list[QueryVariant]) -> dict[str, str]:
    return {f"{prefix}:{variant.name}": variant.text for variant in variants}
