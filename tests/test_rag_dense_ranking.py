from __future__ import annotations

import pytest

from fireclaw_core.rag.dense_eval import DenseEvalRetrievedHit
from fireclaw_core.rag.dense_ranking import aggregate_hits_by_parent
from fireclaw_core.rag.dense_ranking import reciprocal_rank_fuse


def _hit(rank: int, score: float, chunk_id: str, parent_id: str) -> DenseEvalRetrievedHit:
    return DenseEvalRetrievedHit(
        rank=rank,
        score=score,
        chunk_id=chunk_id,
        parent_id=parent_id,
        doc_id=f"doc_{parent_id}",
        text_preview=f"text {chunk_id}",
    )


def test_aggregate_hits_by_parent_keeps_best_child_and_resets_rank() -> None:
    hits = [
        _hit(1, 0.90, "chunk_a_1", "parent_a"),
        _hit(2, 0.88, "chunk_a_2", "parent_a"),
        _hit(3, 0.87, "chunk_b_1", "parent_b"),
        _hit(4, 0.95, "chunk_c_1", "parent_c"),
    ]

    aggregated = aggregate_hits_by_parent(hits, top_k=3, method="max")

    assert [(hit.rank, hit.parent_id, hit.chunk_id, hit.score) for hit in aggregated] == [
        (1, "parent_c", "chunk_c_1", 0.95),
        (2, "parent_a", "chunk_a_1", 0.90),
        (3, "parent_b", "chunk_b_1", 0.87),
    ]
    assert aggregated[1].child_hit_count == 2
    assert aggregated[1].child_ranks == [1, 2]


def test_aggregate_hits_by_parent_rejects_unknown_method() -> None:
    with pytest.raises(ValueError, match="Unsupported parent aggregation method: average"):
        aggregate_hits_by_parent([], top_k=10, method="average")


def test_reciprocal_rank_fuse_combines_variant_ranks_by_parent() -> None:
    zh_hits = [
        _hit(1, 0.50, "chunk_a_zh", "parent_a"),
        _hit(2, 0.49, "chunk_b_zh", "parent_b"),
    ]
    en_hits = [
        _hit(1, 0.60, "chunk_b_en", "parent_b"),
        _hit(2, 0.58, "chunk_c_en", "parent_c"),
    ]

    fused = reciprocal_rank_fuse({"zh": zh_hits, "en": en_hits}, identity="parent", top_k=3, rrf_k=60)

    assert [hit.parent_id for hit in fused] == ["parent_b", "parent_a", "parent_c"]
    assert fused[0].rank == 1
    assert fused[0].variant_ranks == {"zh": 2, "en": 1}
    assert fused[0].variant_scores == {"zh": 0.49, "en": 0.60}
    assert fused[0].fusion_score == pytest.approx((1 / 62) + (1 / 61))


def test_reciprocal_rank_fuse_uses_best_contributing_variant_as_representative() -> None:
    zh_hits = [
        _hit(1, 0.90, "chunk_wrong_zh", "parent_wrong"),
        _hit(2, 0.70, "chunk_gold_zh", "parent_gold"),
    ]
    en_hits = [
        _hit(1, 0.80, "chunk_gold_en", "parent_gold"),
        _hit(2, 0.60, "chunk_other_en", "parent_other"),
    ]

    fused = reciprocal_rank_fuse({"zh": zh_hits, "en": en_hits}, identity="parent", top_k=3, rrf_k=60)

    gold_hit = next(hit for hit in fused if hit.parent_id == "parent_gold")
    assert gold_hit.chunk_id == "chunk_gold_en"
    assert gold_hit.text_preview == "text chunk_gold_en"
    assert gold_hit.variant_ranks == {"zh": 2, "en": 1}
    assert gold_hit.variant_scores == {"zh": 0.70, "en": 0.80}


def test_reciprocal_rank_fuse_rejects_unknown_identity() -> None:
    with pytest.raises(ValueError, match="Unsupported fusion identity: doc"):
        reciprocal_rank_fuse({}, identity="doc", top_k=10)
