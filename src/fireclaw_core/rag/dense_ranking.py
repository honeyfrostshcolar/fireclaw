from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from fireclaw_core.rag.dense_eval import DenseEvalRetrievedHit


def aggregate_hits_by_parent(
    hits: list[DenseEvalRetrievedHit],
    *,
    top_k: int,
    method: str = "max",
) -> list[DenseEvalRetrievedHit]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if method != "max":
        raise ValueError(f"Unsupported parent aggregation method: {method}")

    grouped: dict[str, list[DenseEvalRetrievedHit]] = {}
    for hit in sorted(hits, key=lambda item: item.rank):
        grouped.setdefault(hit.parent_id, []).append(hit)

    representatives: list[DenseEvalRetrievedHit] = []
    for parent_hits in grouped.values():
        best = max(parent_hits, key=lambda item: (item.score, -item.rank))
        representatives.append(
            replace(
                best,
                child_hit_count=len(parent_hits),
                child_ranks=[hit.rank for hit in parent_hits],
            )
        )

    representatives.sort(key=lambda item: (-item.score, item.rank, item.parent_id))
    return [replace(hit, rank=rank) for rank, hit in enumerate(representatives[:top_k], start=1)]


def reciprocal_rank_fuse(
    hit_lists_by_variant: Mapping[str, list[DenseEvalRetrievedHit]],
    *,
    identity: str,
    top_k: int,
    rrf_k: int = 60,
) -> list[DenseEvalRetrievedHit]:
    if identity not in {"chunk", "parent"}:
        raise ValueError(f"Unsupported fusion identity: {identity}")
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")

    items: dict[str, DenseEvalRetrievedHit] = {}
    item_sort_keys: dict[str, tuple[int, float, str]] = {}
    fusion_scores: dict[str, float] = {}
    variant_ranks: dict[str, dict[str, int]] = {}
    variant_scores: dict[str, dict[str, float]] = {}

    for variant_name, hits in hit_lists_by_variant.items():
        seen_in_variant: set[str] = set()
        for hit in sorted(hits, key=lambda item: item.rank):
            key = hit.chunk_id if identity == "chunk" else hit.parent_id
            if key in seen_in_variant:
                continue
            seen_in_variant.add(key)
            representative_key = (hit.rank, -hit.score, hit.chunk_id)
            if key not in item_sort_keys or representative_key < item_sort_keys[key]:
                items[key] = hit
                item_sort_keys[key] = representative_key
            fusion_scores[key] = fusion_scores.get(key, 0.0) + (1.0 / (rrf_k + hit.rank))
            variant_ranks.setdefault(key, {})[variant_name] = hit.rank
            variant_scores.setdefault(key, {})[variant_name] = hit.score

    ranked_keys = sorted(
        items,
        key=lambda key: (-fusion_scores[key], min(variant_ranks[key].values()), key),
    )

    fused: list[DenseEvalRetrievedHit] = []
    for rank, key in enumerate(ranked_keys[:top_k], start=1):
        hit = items[key]
        fused.append(
            replace(
                hit,
                rank=rank,
                score=fusion_scores[key],
                fusion_score=fusion_scores[key],
                variant_ranks=dict(sorted(variant_ranks[key].items())),
                variant_scores=dict(sorted(variant_scores[key].items())),
            )
        )
    return fused
