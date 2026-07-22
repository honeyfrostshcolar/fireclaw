# Review package: Query Expansion Task 3

## Scoped status
```text
?? src/fireclaw_core/rag/dense_eval.py
?? tests/test_rag_dense_eval.py
```

## File: src\fireclaw_core\rag\dense_eval.py
```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from fireclaw_core.rag.dense_retrieval import DenseHit
from fireclaw_core.rag.query_expansion import QueryExpansion
from fireclaw_core.rag.query_expansion import build_query_variants


@dataclass(frozen=True)
class DenseEvalCase:
    case_id: str
    topic: str
    query: str
    gold_parent_ids: list[str]
    gold_chunk_ids: list[str] = field(default_factory=list)
    expected_evidence_summary: str = ""
    source_doc_id: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DenseEvalRetrievedHit:
    rank: int
    score: float
    chunk_id: str
    parent_id: str
    doc_id: str
    source_file: str = ""
    page_start: int | None = None
    page_end: int | None = None
    heading: str = ""
    text_preview: str = ""
    fusion_score: float | None = None
    variant_ranks: dict[str, int] = field(default_factory=dict)
    variant_scores: dict[str, float] = field(default_factory=dict)
    child_hit_count: int | None = None
    child_ranks: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.fusion_score is None:
            data.pop("fusion_score")
        if not self.variant_ranks:
            data.pop("variant_ranks")
        if not self.variant_scores:
            data.pop("variant_scores")
        if self.child_hit_count is None:
            data.pop("child_hit_count")
        if not self.child_ranks:
            data.pop("child_ranks")
        return data


@dataclass(frozen=True)
class DenseEvalCaseResult:
    case_id: str
    topic: str
    query: str
    gold_parent_ids: list[str]
    retrieved_parent_ids: list[str]
    retrieved_gold_parent_ids: list[str]
    first_gold_rank: int | None
    hit_at_1: bool
    hit_at_5: bool
    hit_at_10: bool
    mrr_at_10: float
    gold_recall_at_10: float
    top_hits: list[DenseEvalRetrievedHit]
    expected_evidence_summary: str = ""
    source_doc_id: str = ""
    notes: str = ""
    query_variants: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["top_hits"] = [hit.to_dict() for hit in self.top_hits]
        if not self.query_variants:
            data.pop("query_variants")
        return data


@dataclass(frozen=True)
class DenseEvalReport:
    case_count: int
    hit_at_1: float
    hit_at_5: float
    hit_at_10: float
    mrr_at_10: float
    gold_recall_at_10: float
    results: list[DenseEvalCaseResult]
    retrieval_config: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "case_count": self.case_count,
            "hit_at_1": round(self.hit_at_1, 6),
            "hit_at_5": round(self.hit_at_5, 6),
            "hit_at_10": round(self.hit_at_10, 6),
            "mrr_at_10": round(self.mrr_at_10, 6),
            "gold_recall_at_10": round(self.gold_recall_at_10, 6),
            "results": [result.to_dict() for result in self.results],
        }
        if self.retrieval_config is not None:
            data["retrieval_config"] = self.retrieval_config
        return data


def load_dense_eval_cases(path: Path) -> list[DenseEvalCase]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Dense eval case file not found: {path}")

    cases: list[DenseEvalCase] = []
    seen_case_ids: set[str] = set()
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL in {path}:{line_no}: {exc}") from exc

            case_id = str(data.get("case_id") or "").strip()
            topic = str(data.get("topic") or "").strip()
            query = str(data.get("query") or "").strip()
            gold_parent_ids = _coerce_str_list(data.get("gold_parent_ids"))
            gold_chunk_ids = _coerce_str_list(data.get("gold_chunk_ids"))

            if not case_id:
                raise ValueError(f"Missing case_id in {path}:{line_no}")
            if case_id in seen_case_ids:
                raise ValueError(f"duplicate case_id: {case_id}")
            if not query:
                raise ValueError(f"Missing query for case_id: {case_id}")
            if not gold_parent_ids:
                raise ValueError(f"Missing gold_parent_ids for case_id: {case_id}")

            seen_case_ids.add(case_id)
            cases.append(
                DenseEvalCase(
                    case_id=case_id,
                    topic=topic,
                    query=query,
                    gold_parent_ids=gold_parent_ids,
                    gold_chunk_ids=gold_chunk_ids,
                    expected_evidence_summary=str(data.get("expected_evidence_summary") or ""),
                    source_doc_id=str(data.get("source_doc_id") or ""),
                    notes=str(data.get("notes") or ""),
                )
            )

    return cases


def evaluate_ranked_hits(
    cases: list[DenseEvalCase],
    hits_by_case_id: dict[str, list[DenseEvalRetrievedHit]],
    *,
    top_k: int = 10,
) -> DenseEvalReport:
    _require_min_top_k(top_k)

    results: list[DenseEvalCaseResult] = []
    for case in cases:
        hits = sorted(hits_by_case_id.get(case.case_id, []), key=lambda hit: hit.rank)[:top_k]
        metric_hits = hits[:10]
        gold_parent_ids = list(dict.fromkeys(case.gold_parent_ids))
        gold_parent_set = set(gold_parent_ids)
        retrieved_parent_ids = [hit.parent_id for hit in hits]
        metric_parent_ids = [hit.parent_id for hit in metric_hits]
        retrieved_gold_parent_ids = list(dict.fromkeys(parent_id for parent_id in metric_parent_ids if parent_id in gold_parent_set))
        first_gold_rank = next((hit.rank for hit in metric_hits if hit.parent_id in gold_parent_set), None)
        hit_at_1 = any(hit.parent_id in gold_parent_set for hit in metric_hits[:1])
        hit_at_5 = any(hit.parent_id in gold_parent_set for hit in metric_hits[:5])
        hit_at_10 = any(hit.parent_id in gold_parent_set for hit in metric_hits)
        mrr_at_10 = (1.0 / first_gold_rank) if first_gold_rank is not None and first_gold_rank <= 10 else 0.0
        gold_recall_at_10 = len(retrieved_gold_parent_ids) / len(gold_parent_ids)
        results.append(
            DenseEvalCaseResult(
                case_id=case.case_id,
                topic=case.topic,
                query=case.query,
                gold_parent_ids=gold_parent_ids,
                retrieved_parent_ids=retrieved_parent_ids,
                retrieved_gold_parent_ids=retrieved_gold_parent_ids,
                first_gold_rank=first_gold_rank,
                hit_at_1=hit_at_1,
                hit_at_5=hit_at_5,
                hit_at_10=hit_at_10,
                mrr_at_10=mrr_at_10,
                gold_recall_at_10=gold_recall_at_10,
                top_hits=hits,
                expected_evidence_summary=case.expected_evidence_summary,
                source_doc_id=case.source_doc_id,
                notes=case.notes,
            )
        )

    case_count = len(results)
    if case_count == 0:
        return DenseEvalReport(
            case_count=0,
            hit_at_1=0.0,
            hit_at_5=0.0,
            hit_at_10=0.0,
            mrr_at_10=0.0,
            gold_recall_at_10=0.0,
            results=[],
        )

    return DenseEvalReport(
        case_count=case_count,
        hit_at_1=sum(1 for result in results if result.hit_at_1) / case_count,
        hit_at_5=sum(1 for result in results if result.hit_at_5) / case_count,
        hit_at_10=sum(1 for result in results if result.hit_at_10) / case_count,
        mrr_at_10=sum(result.mrr_at_10 for result in results) / case_count,
        gold_recall_at_10=sum(result.gold_recall_at_10 for result in results) / case_count,
        results=results,
    )


def hits_from_dense_results(hits: list[DenseHit]) -> list[DenseEvalRetrievedHit]:
    converted: list[DenseEvalRetrievedHit] = []
    for hit in hits:
        record = hit.record
        text = str(record.get("clean_text") or record.get("text") or "")
        converted.append(
            DenseEvalRetrievedHit(
                rank=int(hit.rank),
                score=float(hit.score),
                chunk_id=str(record.get("chunk_id") or ""),
                parent_id=str(record.get("parent_id") or ""),
                doc_id=str(record.get("doc_id") or ""),
                source_file=str(record.get("source_file") or ""),
                page_start=record.get("page_start"),
                page_end=record.get("page_end"),
                heading=str(record.get("heading") or ""),
                text_preview=text[:240],
            )
        )
    return converted


def evaluate_dense_retriever(
    retriever: Any,
    cases: list[DenseEvalCase],
    *,
    top_k: int = 10,
) -> DenseEvalReport:
    _require_min_top_k(top_k)
    hits_by_case_id: dict[str, list[DenseEvalRetrievedHit]] = {}
    for case in cases:
        hits = retriever.query(case.query, top_k=top_k)
        hits_by_case_id[case.case_id] = hits_from_dense_results(hits)
    return evaluate_ranked_hits(cases, hits_by_case_id, top_k=top_k)


def evaluate_dense_retriever_with_expansion(
    retriever: Any,
    cases: list[DenseEvalCase],
    *,
    query_expansions: Mapping[str, QueryExpansion] | None = None,
    query_variants: Sequence[str] = ("zh",),
    ranking_view: str = "small",
    top_k: int = 10,
    small_top_k: int | None = None,
    parent_aggregation: str = "max",
    fusion: str | None = None,
    rrf_k: int = 60,
    require_reviewed_expansions: bool = False,
    query_expansions_path: str | None = None,
) -> DenseEvalReport:
    from fireclaw_core.rag.dense_ranking import aggregate_hits_by_parent
    from fireclaw_core.rag.dense_ranking import reciprocal_rank_fuse

    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if ranking_view not in {"small", "parent"}:
        raise ValueError(f"Unsupported ranking view: {ranking_view}")
    selected_variants = list(query_variants)
    if not selected_variants:
        raise ValueError("query_variants must not be empty")
    retrieval_top_k = small_top_k if small_top_k is not None else top_k
    if retrieval_top_k <= 0:
        raise ValueError("small_top_k must be positive")
    fusion_method = fusion or ("rrf" if len(selected_variants) > 1 else "none")
    if fusion_method not in {"none", "rrf"}:
        raise ValueError(f"Unsupported fusion method: {fusion_method}")
    if fusion_method == "none" and len(selected_variants) > 1:
        raise ValueError("fusion must be rrf when multiple query variants are selected")

    expansions = query_expansions or {}
    hits_by_case_id: dict[str, list[DenseEvalRetrievedHit]] = {}
    variants_by_case_id: dict[str, dict[str, str]] = {}
    for case in cases:
        variants = build_query_variants(
            case,
            expansions,
            selected_variants,
            require_reviewed=require_reviewed_expansions,
        )
        variants_by_case_id[case.case_id] = {variant.name: variant.text for variant in variants}
        hits_by_variant: dict[str, list[DenseEvalRetrievedHit]] = {}
        for variant in variants:
            dense_hits = retriever.query(variant.text, top_k=retrieval_top_k)
            eval_hits = hits_from_dense_results(dense_hits)
            if ranking_view == "parent":
                eval_hits = aggregate_hits_by_parent(eval_hits, top_k=retrieval_top_k, method=parent_aggregation)
            hits_by_variant[variant.name] = eval_hits

        if len(variants) == 1:
            final_hits = hits_by_variant[variants[0].name][:top_k]
        else:
            identity = "parent" if ranking_view == "parent" else "chunk"
            final_hits = reciprocal_rank_fuse(hits_by_variant, identity=identity, top_k=top_k, rrf_k=rrf_k)
        hits_by_case_id[case.case_id] = final_hits

    report = evaluate_ranked_hits(cases, hits_by_case_id, top_k=max(top_k, 10))
    enriched_results = [
        result.__class__(
            **{
                **result.__dict__,
                "query_variants": variants_by_case_id.get(result.case_id, {}),
            }
        )
        for result in report.results
    ]
    return DenseEvalReport(
        case_count=report.case_count,
        hit_at_1=report.hit_at_1,
        hit_at_5=report.hit_at_5,
        hit_at_10=report.hit_at_10,
        mrr_at_10=report.mrr_at_10,
        gold_recall_at_10=report.gold_recall_at_10,
        results=enriched_results,
        retrieval_config={
            "query_variants": selected_variants,
            "fusion": fusion_method,
            "rrf_k": rrf_k,
            "ranking_view": ranking_view,
            "parent_aggregation": parent_aggregation,
            "small_top_k": retrieval_top_k,
            "final_top_k": top_k,
            "query_expansions_path": query_expansions_path,
            "require_reviewed_expansions": require_reviewed_expansions,
        },
    )


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Expected a JSON list")
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _require_min_top_k(top_k: int) -> None:
    if top_k < 10:
        raise ValueError("top_k must be at least 10")
```

## File: tests\test_rag_dense_eval.py
```python
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
        top_k=2,
        small_top_k=4,
        query_variants=["zh"],
        ranking_view="parent",
    )

    assert report.hit_at_1 == 0.0
    assert report.hit_at_5 == 1.0
    assert report.results[0].first_gold_rank == 2
    assert report.results[0].top_hits[1].parent_id == "parent_gold"
    assert report.to_dict()["retrieval_config"]["ranking_view"] == "parent"


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
```
