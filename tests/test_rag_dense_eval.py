from __future__ import annotations

import json
from pathlib import Path

import pytest

from fireclaw_core.rag.dense_eval import (
    DenseEvalCase,
    DenseEvalReport,
    DenseEvalRetrievedHit,
    evaluate_dense_retriever,
    evaluate_ranked_hits,
    hits_from_dense_results,
    load_dense_eval_cases,
)
from fireclaw_core.rag.dense_retrieval import DenseHit


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_load_dense_eval_cases_accepts_valid_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    _write_jsonl(
        path,
        [
            {
                "case_id": "dense_zh_001",
                "topic": "smoke_victim_search",
                "query": "烟雾很大时如何找到被困人员？",
                "gold_parent_ids": ["parent_a"],
                "gold_chunk_ids": [],
                "expected_evidence_summary": "Victim search under smoke.",
                "source_doc_id": "doc_a",
                "notes": "Chinese task-style query.",
            }
        ],
    )

    cases = load_dense_eval_cases(path)

    assert cases == [
        DenseEvalCase(
            case_id="dense_zh_001",
            topic="smoke_victim_search",
            query="烟雾很大时如何找到被困人员？",
            gold_parent_ids=["parent_a"],
            gold_chunk_ids=[],
            expected_evidence_summary="Victim search under smoke.",
            source_doc_id="doc_a",
            notes="Chinese task-style query.",
        )
    ]


def test_load_dense_eval_cases_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    _write_jsonl(
        path,
        [
            {"case_id": "dup", "topic": "a", "query": "q1", "gold_parent_ids": ["p1"]},
            {"case_id": "dup", "topic": "b", "query": "q2", "gold_parent_ids": ["p2"]},
        ],
    )

    with pytest.raises(ValueError, match="duplicate case_id: dup"):
        load_dense_eval_cases(path)


def test_load_dense_eval_cases_rejects_empty_gold_parent_ids(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    _write_jsonl(path, [{"case_id": "bad", "topic": "a", "query": "q", "gold_parent_ids": []}])

    with pytest.raises(ValueError, match="gold_parent_ids"):
        load_dense_eval_cases(path)


def test_evaluate_ranked_hits_computes_hit_mrr_and_gold_recall() -> None:
    cases = [
        DenseEvalCase(
            case_id="case_1",
            topic="thermal",
            query="烟雾中如何用热成像找人？",
            gold_parent_ids=["parent_gold"],
        ),
        DenseEvalCase(
            case_id="case_2",
            topic="mobility",
            query="机器人如何通过碎石地形？",
            gold_parent_ids=["parent_missing"],
        ),
    ]
    hits = {
        "case_1": [
            DenseEvalRetrievedHit(rank=1, score=0.9, chunk_id="c1", parent_id="parent_wrong", doc_id="d1"),
            DenseEvalRetrievedHit(rank=2, score=0.8, chunk_id="c2", parent_id="parent_gold", doc_id="d2"),
        ],
        "case_2": [
            DenseEvalRetrievedHit(rank=1, score=0.7, chunk_id="c3", parent_id="parent_other", doc_id="d3"),
        ],
    }

    report = evaluate_ranked_hits(cases, hits, top_k=10)

    assert report.case_count == 2
    assert report.hit_at_1 == 0.0
    assert report.hit_at_5 == 0.5
    assert report.hit_at_10 == 0.5
    assert report.mrr_at_10 == 0.25
    assert report.gold_recall_at_10 == 0.5
    assert report.results[0].first_gold_rank == 2
    assert report.results[1].first_gold_rank is None


def test_evaluate_ranked_hits_handles_multiple_gold_parents() -> None:
    cases = [
        DenseEvalCase(
            case_id="case_multi",
            topic="usar",
            query="废墟搜救需要哪些能力？",
            gold_parent_ids=["parent_a", "parent_b"],
        )
    ]
    hits = {
        "case_multi": [
            DenseEvalRetrievedHit(rank=1, score=0.9, chunk_id="c1", parent_id="parent_a", doc_id="d1"),
            DenseEvalRetrievedHit(rank=2, score=0.8, chunk_id="c2", parent_id="parent_other", doc_id="d2"),
        ]
    }

    report = evaluate_ranked_hits(cases, hits, top_k=10)

    assert report.hit_at_1 == 1.0
    assert report.gold_recall_at_10 == 0.5
    assert report.results[0].retrieved_gold_parent_ids == ["parent_a"]


def test_evaluate_ranked_hits_rejects_top_k_below_10() -> None:
    case = DenseEvalCase(
        case_id="case_guard",
        topic="smoke",
        query="query",
        gold_parent_ids=["parent_gold"],
    )

    with pytest.raises(ValueError, match="top_k must be at least 10"):
        evaluate_ranked_hits([case], {}, top_k=5)


def test_evaluate_ranked_hits_keeps_at_10_metrics_when_top_k_is_larger() -> None:
    case = DenseEvalCase(
        case_id="case_extended",
        topic="smoke",
        query="query",
        gold_parent_ids=["parent_gold"],
    )
    hits = [
        DenseEvalRetrievedHit(
            rank=rank,
            score=1.0 / rank,
            chunk_id=f"chunk_{rank}",
            parent_id="parent_gold" if rank == 12 else f"parent_{rank}",
            doc_id="doc",
        )
        for rank in range(1, 13)
    ]

    report = evaluate_ranked_hits([case], {"case_extended": hits}, top_k=12)

    assert len(report.results[0].top_hits) == 12
    assert report.results[0].first_gold_rank is None
    assert report.hit_at_10 == 0.0
    assert report.mrr_at_10 == 0.0
    assert report.gold_recall_at_10 == 0.0


def test_dense_eval_report_serializes_to_dict() -> None:
    case = DenseEvalCase(
        case_id="case_1",
        topic="smoke",
        query="烟雾中如何找人？",
        gold_parent_ids=["parent_gold"],
    )
    report = evaluate_ranked_hits(
        [case],
        {
            "case_1": [
                DenseEvalRetrievedHit(rank=1, score=0.9, chunk_id="c1", parent_id="parent_gold", doc_id="d1"),
            ]
        },
        top_k=10,
    )

    payload = report.to_dict()

    assert isinstance(report, DenseEvalReport)
    assert payload["case_count"] == 1
    assert payload["hit_at_1"] == 1.0
    assert payload["results"][0]["top_hits"][0]["parent_id"] == "parent_gold"


def test_hits_from_dense_results_preserves_parent_id_and_preview() -> None:
    dense_hit = DenseHit(
        rank=1,
        score=0.77,
        record={
            "chunk_id": "chunk_1",
            "parent_id": "parent_1",
            "doc_id": "doc_1",
            "source_file": "raw/source.pdf",
            "page_start": 3,
            "page_end": 4,
            "heading": "Victim Search",
            "clean_text": "A" * 300,
        },
    )

    converted = hits_from_dense_results([dense_hit])

    assert converted[0].rank == 1
    assert converted[0].score == 0.77
    assert converted[0].chunk_id == "chunk_1"
    assert converted[0].parent_id == "parent_1"
    assert converted[0].text_preview == "A" * 240


def test_evaluate_dense_retriever_runs_queries() -> None:
    class FakeRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            assert query == "机器人如何在烟雾中找人？"
            assert top_k == 10
            return [
                DenseHit(
                    rank=1,
                    score=0.5,
                    record={"chunk_id": "chunk_gold", "parent_id": "parent_gold", "doc_id": "doc"},
                )
            ]

    case = DenseEvalCase(
        case_id="case_1",
        topic="smoke",
        query="机器人如何在烟雾中找人？",
        gold_parent_ids=["parent_gold"],
    )

    report = evaluate_dense_retriever(FakeRetriever(), [case], top_k=10)

    assert report.hit_at_1 == 1.0
    assert report.results[0].top_hits[0].chunk_id == "chunk_gold"

def test_evaluate_dense_retriever_rejects_top_k_below_10_without_querying() -> None:
    class FakeRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            raise AssertionError("query should not be called")

    case = DenseEvalCase(
        case_id="case_guard",
        topic="smoke",
        query="鏈哄櫒浜哄浣曞湪鐑熼浘涓壘浜猴紵",
        gold_parent_ids=["parent_gold"],
    )

    with pytest.raises(ValueError, match="top_k must be at least 10"):
        evaluate_dense_retriever(FakeRetriever(), [case], top_k=5)


def test_evaluate_dense_retriever_with_parent_ranking_finds_gold_after_dedup() -> None:
    from fireclaw_core.rag.dense_eval import evaluate_dense_retriever_with_expansion

    class FakeRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            assert query == "query zh"
            assert top_k == 4
            return [
                DenseHit(rank=1, score=0.95, record={"chunk_id": "wrong_1", "parent_id": "parent_wrong", "doc_id": "doc"}),
                DenseHit(rank=2, score=0.94, record={"chunk_id": "wrong_2", "parent_id": "parent_wrong", "doc_id": "doc"}),
                DenseHit(rank=3, score=0.93, record={"chunk_id": "gold_1", "parent_id": "parent_gold", "doc_id": "doc"}),
                DenseHit(rank=4, score=0.92, record={"chunk_id": "other_1", "parent_id": "parent_other", "doc_id": "doc"}),
            ]

    case = DenseEvalCase(case_id="case_1", topic="t", query="query zh", gold_parent_ids=["parent_gold"])

    report = evaluate_dense_retriever_with_expansion(
        FakeRetriever(),
        [case],
        top_k=10,
        small_top_k=4,
        query_variants=["zh"],
        ranking_view="parent",
    )

    assert report.hit_at_1 == 0.0
    assert report.hit_at_5 == 1.0
    assert report.results[0].first_gold_rank == 2
    assert report.results[0].top_hits[1].parent_id == "parent_gold"
    assert report.to_dict()["retrieval_config"]["ranking_view"] == "parent"


def test_evaluate_dense_retriever_with_expansion_rejects_top_k_below_10_without_querying() -> None:
    from fireclaw_core.rag.dense_eval import evaluate_dense_retriever_with_expansion

    class FakeRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            raise AssertionError("query should not be called")

    case = DenseEvalCase(
        case_id="case_guard",
        topic="smoke",
        query="query zh",
        gold_parent_ids=["parent_gold"],
    )

    with pytest.raises(ValueError, match="top_k must be at least 10"):
        evaluate_dense_retriever_with_expansion(
            FakeRetriever(),
            [case],
            top_k=5,
            small_top_k=2,
            query_variants=["zh"],
            ranking_view="parent",
        )


def test_evaluate_dense_retriever_with_expansion_rejects_duplicate_query_variants_without_querying() -> None:
    from fireclaw_core.rag.dense_eval import evaluate_dense_retriever_with_expansion

    class FakeRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            raise AssertionError("query should not be called")

    case = DenseEvalCase(
        case_id="case_guard",
        topic="smoke",
        query="query zh",
        gold_parent_ids=["parent_gold"],
    )

    with pytest.raises(ValueError, match="duplicate query variant: zh"):
        evaluate_dense_retriever_with_expansion(
            FakeRetriever(),
            [case],
            top_k=10,
            query_variants=["zh", "zh"],
        )


def test_evaluate_dense_retriever_with_query_expansion_uses_rrf() -> None:
    from fireclaw_core.rag.dense_eval import evaluate_dense_retriever_with_expansion
    from fireclaw_core.rag.query_expansion import QueryExpansion

    class FakeRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            if query == "中文":
                return [
                    DenseHit(rank=1, score=0.8, record={"chunk_id": "chunk_wrong", "parent_id": "parent_wrong", "doc_id": "doc"}),
                    DenseHit(rank=2, score=0.7, record={"chunk_id": "chunk_gold_zh", "parent_id": "parent_gold", "doc_id": "doc"}),
                ]
            if query == "English reviewed":
                return [
                    DenseHit(rank=1, score=0.9, record={"chunk_id": "chunk_gold_en", "parent_id": "parent_gold", "doc_id": "doc"}),
                    DenseHit(rank=2, score=0.6, record={"chunk_id": "chunk_other", "parent_id": "parent_other", "doc_id": "doc"}),
                ]
            raise AssertionError(f"unexpected query: {query}")

    case = DenseEvalCase(case_id="case_fusion", topic="t", query="中文", gold_parent_ids=["parent_gold"])
    expansions = {
        "case_fusion": QueryExpansion(
            case_id="case_fusion",
            query_zh="中文",
            llm_query_en="English llm",
            reviewed_query_en="English reviewed",
            status="candidate",
        )
    }

    report = evaluate_dense_retriever_with_expansion(
        FakeRetriever(),
        [case],
        query_expansions=expansions,
        query_variants=["zh", "en"],
        ranking_view="parent",
        small_top_k=2,
        top_k=10,
    )

    result = report.results[0]
    assert result.top_hits[0].parent_id == "parent_gold"
    assert result.top_hits[0].variant_ranks == {"en": 1, "zh": 2}
    assert result.query_variants == {"zh": "中文", "en": "English reviewed"}


def test_dense_eval_retrieved_hit_omits_empty_optional_metadata() -> None:
    hit = DenseEvalRetrievedHit(rank=1, score=0.5, chunk_id="chunk", parent_id="parent", doc_id="doc")

    payload = hit.to_dict()

    assert "fusion_score" not in payload
    assert "variant_ranks" not in payload
    assert "variant_scores" not in payload
    assert "child_hit_count" not in payload
    assert "child_ranks" not in payload
