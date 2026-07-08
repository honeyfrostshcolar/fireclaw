from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid

import pytest

from fireclaw_core.rag.dense_eval import DenseEvalRetrievedHit
from fireclaw_core.rag.reranking import BGEFlagRerankerProvider
from fireclaw_core.rag.reranking import FakeRerankerProvider
from fireclaw_core.rag.reranking import load_parent_texts
from fireclaw_core.rag.reranking import rerank_parent_hits
from fireclaw_core.rag.reranking import select_rerank_query


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
