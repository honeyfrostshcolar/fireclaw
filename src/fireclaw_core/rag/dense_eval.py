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

    _require_min_top_k(top_k)
    if ranking_view not in {"small", "parent"}:
        raise ValueError(f"Unsupported ranking view: {ranking_view}")
    selected_variants = list(query_variants)
    if not selected_variants:
        raise ValueError("query_variants must not be empty")
    seen_variants: set[str] = set()
    for variant_name in selected_variants:
        if variant_name in seen_variants:
            raise ValueError(f"duplicate query variant: {variant_name}")
        seen_variants.add(variant_name)
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
