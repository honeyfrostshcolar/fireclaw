### Task 3: Expanded Dense Evaluation Orchestration

**Files:**
- Modify: `src/fireclaw_core/rag/dense_eval.py`
- Modify: `tests/test_rag_dense_eval.py`

**Interfaces:**
- Consumes:
  - `build_query_variants` from `query_expansion.py`;
  - `aggregate_hits_by_parent` and `reciprocal_rank_fuse` from `dense_ranking.py`;
  - existing retriever API `query(query: str, top_k: int) -> list[DenseHit]`.
- Produces:
  - `evaluate_dense_retriever_with_expansion(...) -> DenseEvalReport`
  - optional `DenseEvalReport.retrieval_config`
  - optional `DenseEvalCaseResult.query_variants`

- [ ] **Step 1: Write failing tests for expanded evaluation**

Add to `tests/test_rag_dense_eval.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_eval_expanded_red tests/test_rag_dense_eval.py::test_evaluate_dense_retriever_with_parent_ranking_finds_gold_after_dedup tests/test_rag_dense_eval.py::test_evaluate_dense_retriever_with_query_expansion_uses_rrf -q
```

Expected:

```text
ImportError or AttributeError for evaluate_dense_retriever_with_expansion
```

- [ ] **Step 3: Add optional report/case metadata serialization**

Modify `DenseEvalCaseResult` in `src/fireclaw_core/rag/dense_eval.py`:

```python
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
```

Modify `DenseEvalReport`:

```python
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
```

- [ ] **Step 4: Add expanded evaluation function**

Add these imports to `src/fireclaw_core/rag/dense_eval.py`. Keep `dense_ranking` imports local inside the function because `dense_ranking.py` imports `DenseEvalRetrievedHit` from this module.

```python
from collections.abc import Mapping, Sequence

from fireclaw_core.rag.query_expansion import QueryExpansion
from fireclaw_core.rag.query_expansion import build_query_variants
```

Add function:

```python
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
    retrieval_top_k = small_top_k if small_top_k is not None else top_k
    if retrieval_top_k < top_k:
        raise ValueError("small_top_k must be greater than or equal to top_k")
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

    report = evaluate_ranked_hits(cases, hits_by_case_id, top_k=top_k)
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
```

- [ ] **Step 5: Run expanded eval tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_eval_expanded_green tests/test_rag_dense_eval.py -q
```

Expected:

```text
all tests in tests/test_rag_dense_eval.py pass
```

- [ ] **Step 6: Checkpoint without committing**

Run:

```powershell
git diff -- src/fireclaw_core/rag/dense_eval.py tests/test_rag_dense_eval.py
```

Expected:

```text
diff output shows expanded eval orchestration and tests only
```
