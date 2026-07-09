from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil
import uuid

import pytest

from fireclaw_core.rag.dense_eval import DenseEvalRetrievedHit
from fireclaw_core.rag.reranking import BGEFlagRerankerProvider
from fireclaw_core.rag.reranking import FakeRerankerProvider
from fireclaw_core.rag.reranking import aggregate_reranked_small_hits_by_parent
from fireclaw_core.rag.reranking import fuse_hybrid_and_rerank_hits
from fireclaw_core.rag.reranking import load_parent_texts
from fireclaw_core.rag.reranking import load_small_texts
from fireclaw_core.rag.reranking import rerank_parent_hits
from fireclaw_core.rag.reranking import rerank_parent_hits_with_rrf
from fireclaw_core.rag.reranking import rerank_small_hits
from fireclaw_core.rag.reranking import rerank_small_hits_with_rrf
from fireclaw_core.rag.reranking import select_rerank_query
from fireclaw_core.rag.reranking import select_rerank_queries


def _hit(rank: int, score: float, parent_id: str, chunk_id: str) -> DenseEvalRetrievedHit:
    return DenseEvalRetrievedHit(
        rank=rank,
        score=score,
        chunk_id=chunk_id,
        parent_id=parent_id,
        doc_id="doc",
        text_preview=f"preview {chunk_id}",
        fusion_score=score,
        variant_ranks={"hybrid": rank},
        variant_scores={"hybrid": score},
    )


def _workspace_temp_dir() -> Path:
    path = Path(".pytest_tmp_reranking_cases") / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_load_parent_texts_reads_parent_chunk_jsonl() -> None:
    temp_dir = _workspace_temp_dir()
    try:
        path = temp_dir / "parent_chunks.jsonl"
        path.write_text(
            json.dumps({"parent_id": "parent_a", "text": "alpha rescue text"}, ensure_ascii=False) + "\n"
            + json.dumps({"parent_id": "parent_b", "text": "bravo SCBA text"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        texts = load_parent_texts(path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert texts == {"parent_a": "alpha rescue text", "parent_b": "bravo SCBA text"}


def test_load_parent_texts_rejects_duplicate_parent_id() -> None:
    temp_dir = _workspace_temp_dir()
    try:
        path = temp_dir / "parent_chunks.jsonl"
        path.write_text(
            json.dumps({"parent_id": "dup", "text": "first"}, ensure_ascii=False) + "\n"
            + json.dumps({"parent_id": "dup", "text": "second"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="duplicate parent_id in parent chunks: dup"):
            load_parent_texts(path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_load_parent_texts_rejects_missing_text() -> None:
    temp_dir = _workspace_temp_dir()
    try:
        path = temp_dir / "parent_chunks.jsonl"
        path.write_text(
            json.dumps({"parent_id": "parent_a"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="missing text in parent chunks for parent_id: parent_a"):
            load_parent_texts(path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_load_parent_texts_rejects_empty_text() -> None:
    temp_dir = _workspace_temp_dir()
    try:
        path = temp_dir / "parent_chunks.jsonl"
        path.write_text(
            json.dumps({"parent_id": "parent_a", "text": "   "}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="empty text in parent chunks for parent_id: parent_a"):
            load_parent_texts(path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_load_small_texts_reads_small_chunk_jsonl() -> None:
    temp_dir = _workspace_temp_dir()
    try:
        path = temp_dir / "small_chunks.jsonl"
        path.write_text(
            json.dumps({"chunk_id": "small_a", "clean_text": "alpha rescue text"}, ensure_ascii=False) + "\n"
            + json.dumps({"chunk_id": "small_b", "text": "bravo SCBA text"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        texts = load_small_texts(path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert texts == {"small_a": "alpha rescue text", "small_b": "bravo SCBA text"}


def test_select_rerank_query_prefers_requested_english_variant() -> None:
    query = select_rerank_query(
        {
            "dense:zh": "中文问题",
            "dense:en": "When should firefighters enter SCBA rehabilitation?",
            "bm25:terms": "SCBA rehabilitation NFPA 1584",
        },
        fallback_query="中文问题",
        variant="en",
    )

    assert query == "When should firefighters enter SCBA rehabilitation?"


def test_select_rerank_query_prefers_requested_zh_variant_when_present() -> None:
    query = select_rerank_query(
        {
            "dense:zh": "消防员什么时候进入康复区？",
            "bm25:zh": "消防员 康复区 NFPA 1584",
            "zh": "进入康复区条件",
        },
        fallback_query="原始中文问题",
        variant="zh",
    )

    assert query == "消防员什么时候进入康复区？"


def test_select_rerank_query_falls_back_to_original_query() -> None:
    query = select_rerank_query({}, fallback_query="中文问题", variant="en")

    assert query == "中文问题"


def test_rerank_parent_hits_uses_parent_text_and_preserves_base_metadata() -> None:
    parent_texts = {
        "parent_generic": "generic firefighter health information",
        "parent_gold": "SCBA rehabilitation medical evaluation NFPA 1584",
    }
    hits = [
        _hit(1, 0.90, "parent_generic", "chunk_generic"),
        _hit(2, 0.70, "parent_gold", "chunk_gold"),
    ]
    reranker = FakeRerankerProvider()

    reranked = rerank_parent_hits(
        "When should firefighters enter SCBA rehabilitation?",
        hits,
        parent_texts,
        reranker,
        top_k=2,
    )

    assert [hit.parent_id for hit in reranked] == ["parent_gold", "parent_generic"]
    assert reranked[0].rank == 1
    assert reranked[0].rerank_score > reranked[1].rerank_score
    assert reranked[0].base_rank == 2
    assert reranked[0].base_score == 0.70
    assert reranked[0].base_fusion_score == 0.70
    assert reranked[0].reranker == "fake-reranker"
    assert reranked[0].score == reranked[0].rerank_score
    assert reranked[0].variant_ranks == {"hybrid": 2}


def test_rerank_small_hits_uses_full_small_text() -> None:
    hits = [
        replace(
            _hit(1, 0.90, "parent_generic", "chunk_generic"),
            text_preview="thermal victim evidence only appears in preview",
        ),
        replace(_hit(2, 0.70, "parent_gold", "chunk_gold"), text_preview="generic preview"),
    ]
    small_texts = {
        "chunk_generic": "generic operational context without the evidence terms",
        "chunk_gold": "thermal victim evidence behind a closed door",
    }

    reranked = rerank_small_hits(
        "thermal victim evidence",
        hits,
        small_texts,
        FakeRerankerProvider(),
        top_k=2,
    )
    aggregated = aggregate_reranked_small_hits_by_parent(reranked, top_k=2)

    assert [hit.chunk_id for hit in reranked] == ["chunk_gold", "chunk_generic"]
    assert [hit.parent_id for hit in aggregated] == ["parent_gold", "parent_generic"]
    assert aggregated[0].base_rank == 2
    assert aggregated[0].child_ranks == [1]
    assert aggregated[0].child_hit_count == 1


def test_rerank_small_hits_rejects_missing_small_text() -> None:
    with pytest.raises(ValueError, match="missing small text for chunk_id: chunk_missing"):
        rerank_small_hits(
            "thermal victim evidence",
            [_hit(1, 0.90, "parent_missing", "chunk_missing")],
            {},
            FakeRerankerProvider(),
            top_k=1,
        )


def test_rerank_small_hits_with_rrf_fuses_variant_rankings() -> None:
    small_texts = {
        "chunk_en": "ventilation natural language explanation",
        "chunk_terms": "LOAD3DSMOKE HRRPUV smokeview command",
        "chunk_both": "ventilation LOAD3DSMOKE smokeview",
    }
    hits = [
        _hit(1, 0.90, "parent_en", "chunk_en"),
        _hit(2, 0.80, "parent_terms", "chunk_terms"),
        _hit(3, 0.70, "parent_both", "chunk_both"),
    ]

    reranked = rerank_small_hits_with_rrf(
        {
            "en": "ventilation explanation",
            "terms": "LOAD3DSMOKE HRRPUV",
            "zh": "ventilation LOAD3DSMOKE",
        },
        hits,
        small_texts,
        FakeRerankerProvider(),
        top_k=3,
        rrf_k=60,
    )

    assert [hit.chunk_id for hit in reranked] == ["chunk_both", "chunk_en", "chunk_terms"]
    assert reranked[0].parent_id == "parent_both"
    assert reranked[0].base_rank == 3
    assert reranked[0].reranker == "fake-reranker:rrf"
    assert reranked[0].variant_ranks["rerank:en"] == 2
    assert reranked[0].variant_ranks["rerank:terms"] == 2
    assert reranked[0].variant_ranks["rerank:zh"] == 1


def test_aggregate_reranked_small_hits_by_parent() -> None:
    hits = [
        replace(
            _hit(1, 0.95, "parent_b", "chunk_b_best"),
            rerank_score=0.95,
            base_rank=5,
            base_score=0.50,
            base_fusion_score=0.05,
        ),
        replace(
            _hit(2, 0.90, "parent_a", "chunk_a_best"),
            rerank_score=0.90,
            base_rank=1,
            base_score=0.99,
            base_fusion_score=0.10,
        ),
        replace(
            _hit(3, 0.20, "parent_a", "chunk_a_other"),
            rerank_score=0.20,
            base_rank=3,
            base_score=0.30,
            base_fusion_score=0.03,
        ),
    ]

    aggregated = aggregate_reranked_small_hits_by_parent(hits, top_k=2)

    assert [(hit.rank, hit.parent_id, hit.chunk_id) for hit in aggregated] == [
        (1, "parent_b", "chunk_b_best"),
        (2, "parent_a", "chunk_a_best"),
    ]
    assert aggregated[0].child_hit_count == 1
    assert aggregated[0].child_ranks == [1]
    assert aggregated[1].child_hit_count == 2
    assert aggregated[1].child_ranks == [2, 3]
    assert aggregated[1].base_rank == 1
    assert aggregated[1].base_score == 0.99
    assert aggregated[1].base_fusion_score == 0.10


def test_select_rerank_queries_returns_requested_variants() -> None:
    queries = select_rerank_queries(
        {
            "dense:zh": "SCBA 涓枃闂",
            "dense:en": "When should firefighters enter SCBA rehabilitation?",
            "bm25:terms": "SCBA rehabilitation NFPA 1584",
        },
        fallback_query="fallback original query",
        variants=["zh", "en", "terms"],
    )

    assert queries == {
        "zh": "SCBA 涓枃闂",
        "en": "When should firefighters enter SCBA rehabilitation?",
        "terms": "SCBA rehabilitation NFPA 1584",
    }


def test_select_rerank_queries_rejects_duplicate_variants() -> None:
    with pytest.raises(ValueError, match="duplicate rerank query variant: zh"):
        select_rerank_queries(
            {"dense:zh": "SCBA duplicate variant"},
            fallback_query="fallback original query",
            variants=["zh", "zh"],
        )


def test_select_rerank_queries_rejects_unsupported_variants() -> None:
    with pytest.raises(ValueError, match="Unsupported rerank query variant: bad"):
        select_rerank_queries(
            {"dense:zh": "SCBA unsupported variant"},
            fallback_query="fallback original query",
            variants=["zh", "bad"],
        )


def test_rerank_parent_hits_with_rrf_fuses_variant_rankings() -> None:
    parent_texts = {
        "parent_en": "ventilation natural language explanation",
        "parent_terms": "LOAD3DSMOKE HRRPUV smokeview command",
        "parent_both": "ventilation LOAD3DSMOKE smokeview",
    }
    hits = [
        _hit(1, 0.90, "parent_en", "chunk_en"),
        _hit(2, 0.80, "parent_terms", "chunk_terms"),
        _hit(3, 0.70, "parent_both", "chunk_both"),
    ]

    reranked = rerank_parent_hits_with_rrf(
        {
            "en": "ventilation explanation",
            "terms": "LOAD3DSMOKE HRRPUV",
            "zh": "ventilation LOAD3DSMOKE",
        },
        hits,
        parent_texts,
        FakeRerankerProvider(),
        top_k=3,
        rrf_k=60,
    )

    assert [hit.parent_id for hit in reranked] == ["parent_both", "parent_en", "parent_terms"]
    assert reranked[0].rank == 1
    assert reranked[0].base_rank == 3
    assert reranked[0].base_score == 0.70
    assert reranked[0].base_fusion_score == 0.70
    assert reranked[0].reranker == "fake-reranker:rrf"
    assert reranked[0].variant_ranks["rerank:en"] == 2
    assert reranked[0].variant_ranks["rerank:terms"] == 2
    assert reranked[0].variant_ranks["rerank:zh"] == 1
    assert reranked[0].variant_scores["rerank:en"] == 0.5
    assert reranked[0].variant_scores["rerank:terms"] == 0.5
    assert reranked[0].variant_scores["rerank:zh"] == 1.0
    assert reranked[0].score == reranked[0].rerank_score


def test_fuse_hybrid_and_rerank_hits_uses_rank_level_rrf() -> None:
    hybrid_hits = [
        _hit(1, 0.90, "parent_a", "chunk_a"),
        _hit(2, 9999.0, "parent_b", "chunk_b"),
        _hit(3, 0.70, "parent_c", "chunk_c"),
    ]
    reranked_hits = [
        replace(
            _hit(1, 0.20, "parent_c", "chunk_c"),
            rerank_score=0.20,
            base_rank=3,
            base_score=0.70,
            base_fusion_score=0.07,
            reranker="fake-reranker",
        ),
        replace(
            _hit(2, 9999.0, "parent_b", "chunk_b"),
            rerank_score=9999.0,
            base_rank=2,
            base_score=9999.0,
            base_fusion_score=0.06,
            reranker="fake-reranker",
        ),
        replace(
            _hit(3, 0.10, "parent_a", "chunk_a"),
            rerank_score=0.10,
            base_rank=1,
            base_score=0.90,
            base_fusion_score=0.05,
            reranker="fake-reranker",
        ),
    ]

    fused = fuse_hybrid_and_rerank_hits(hybrid_hits, reranked_hits, top_k=3, rrf_k=60)

    assert [hit.parent_id for hit in fused] == ["parent_a", "parent_c", "parent_b"]
    assert fused[0].score == pytest.approx((1.0 / 61) + (1.0 / 63))
    assert fused[0].fusion_score == fused[0].score
    assert fused[0].rerank_score == 0.10
    assert fused[0].base_rank == 1
    assert fused[0].base_score == 0.90
    assert fused[0].base_fusion_score == 0.05
    assert fused[0].reranker == "fake-reranker"
    assert fused[0].variant_ranks["hybrid"] == 1
    assert fused[0].variant_ranks["rerank"] == 3
    assert fused[0].variant_scores["hybrid"] == 0.90
    assert fused[0].variant_scores["rerank"] == 0.10
    assert fused[2].variant_scores["rerank"] == 9999.0


def test_rerank_parent_hits_rejects_missing_parent_text() -> None:
    reranker = FakeRerankerProvider()

    with pytest.raises(ValueError, match="missing parent text for parent_id: parent_missing"):
        rerank_parent_hits(
            "SCBA rehabilitation",
            [_hit(1, 0.5, "parent_missing", "chunk")],
            {},
            reranker,
            top_k=1,
        )


def test_dense_eval_retrieved_hit_to_dict_omits_unset_rerank_metadata() -> None:
    hit = DenseEvalRetrievedHit(
        rank=1,
        score=0.5,
        chunk_id="chunk",
        parent_id="parent",
        doc_id="doc",
    )

    payload = hit.to_dict()

    assert "rerank_score" not in payload
    assert "base_rank" not in payload
    assert "base_score" not in payload
    assert "base_fusion_score" not in payload
    assert "reranker" not in payload


def test_bge_flag_reranker_provider_rejects_missing_model_path_before_backend_import(monkeypatch: pytest.MonkeyPatch) -> None:
    missing_path = Path(".pytest_tmp_reranking_cases") / f"missing-{uuid.uuid4().hex}"
    provider = BGEFlagRerankerProvider(missing_path)
    original_import = __import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name in {"torch", "FlagEmbedding"}:
            raise AssertionError(f"backend import attempted before model path check: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)

    with pytest.raises(FileNotFoundError, match="BGE reranker model path not found"):
        provider.score_pairs([("query", "passage")])
