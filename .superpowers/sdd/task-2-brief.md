### Task 2: Hybrid Rerank Evaluation Wrapper

**Files:**
- Create: `src/fireclaw_core/rag/rerank_eval.py`
- Create: `tests/test_rag_rerank_eval.py`

**Interfaces:**
- Consumes:
  - `evaluate_hybrid_retrievers_with_expansion(...)`
  - `evaluate_ranked_hits(...)`
  - `select_rerank_query(...)`
  - `rerank_parent_hits(...)`
- Produces:
  - `evaluate_hybrid_retrievers_with_rerank(...) -> DenseEvalReport`

- [ ] **Step 1: Write failing hybrid rerank eval test**

Create `tests/test_rag_rerank_eval.py`:

```python
from __future__ import annotations

from fireclaw_core.rag.dense_eval import DenseEvalCase
from fireclaw_core.rag.dense_retrieval import DenseHit
from fireclaw_core.rag.query_expansion import QueryExpansion
from fireclaw_core.rag.reranking import FakeRerankerProvider


def test_hybrid_rerank_eval_promotes_more_relevant_parent() -> None:
    from fireclaw_core.rag.rerank_eval import evaluate_hybrid_retrievers_with_rerank

    class FakeDenseRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(
                    rank=1,
                    score=0.9,
                    record={"chunk_id": "dense_generic", "parent_id": "parent_generic", "doc_id": "doc"},
                ),
                DenseHit(
                    rank=2,
                    score=0.8,
                    record={"chunk_id": "dense_gold", "parent_id": "parent_gold", "doc_id": "doc"},
                ),
            ]

    class FakeBM25Retriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(
                    rank=1,
                    score=3.0,
                    record={"chunk_id": "bm25_generic", "parent_id": "parent_generic", "doc_id": "doc"},
                ),
                DenseHit(
                    rank=2,
                    score=2.0,
                    record={"chunk_id": "bm25_gold", "parent_id": "parent_gold", "doc_id": "doc"},
                ),
            ]

    case = DenseEvalCase(
        case_id="case",
        topic="rehab",
        query="中文问题",
        gold_parent_ids=["parent_gold"],
    )
    expansions = {
        "case": QueryExpansion(
            case_id="case",
            query_zh="中文问题",
            reviewed_query_en="When should firefighters enter SCBA rehabilitation?",
            term_query="SCBA rehabilitation NFPA 1584",
            terms=["SCBA", "rehabilitation", "NFPA 1584"],
            status="reviewed",
        )
    }
    parent_texts = {
        "parent_generic": "generic firefighter health information",
        "parent_gold": "SCBA rehabilitation medical evaluation NFPA 1584",
    }

    report = evaluate_hybrid_retrievers_with_rerank(
        FakeDenseRetriever(),
        FakeBM25Retriever(),
        FakeRerankerProvider(),
        [case],
        query_expansions=expansions,
        parent_texts=parent_texts,
        dense_query_variants=["zh", "en"],
        bm25_query_variants=["en", "terms"],
        rerank_pool_size=10,
        top_k=10,
        require_reviewed_expansions=True,
    )

    assert report.hit_at_1 == 1.0
    assert report.results[0].top_hits[0].parent_id == "parent_gold"
    assert report.results[0].top_hits[0].base_rank == 2
    assert report.results[0].top_hits[0].reranker == "fake-reranker"
    assert report.results[0].query_variants["rerank:en"] == "When should firefighters enter SCBA rehabilitation?"
    assert report.retrieval_config is not None
    assert report.retrieval_config["retrieval_method"] == "hybrid_rerank"
    assert report.retrieval_config["candidate_retrieval_method"] == "hybrid"
    assert report.retrieval_config["rerank_pool_size"] == 10
    assert report.retrieval_config["final_top_k"] == 10
    assert report.retrieval_config["reranker"]["provider"] == "fake-reranker"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_eval_red tests/test_rag_rerank_eval.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'fireclaw_core.rag.rerank_eval'
```

- [ ] **Step 3: Implement hybrid rerank eval**

Create `src/fireclaw_core/rag/rerank_eval.py`:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from fireclaw_core.rag.dense_eval import DenseEvalCase
from fireclaw_core.rag.dense_eval import DenseEvalReport
from fireclaw_core.rag.dense_eval import DenseEvalRetrievedHit
from fireclaw_core.rag.dense_eval import evaluate_ranked_hits
from fireclaw_core.rag.hybrid_eval import evaluate_hybrid_retrievers_with_expansion
from fireclaw_core.rag.query_expansion import QueryExpansion
from fireclaw_core.rag.reranking import RerankerProvider
from fireclaw_core.rag.reranking import rerank_parent_hits
from fireclaw_core.rag.reranking import select_rerank_query


def evaluate_hybrid_retrievers_with_rerank(
    dense_retriever: Any,
    bm25_retriever: Any,
    reranker: RerankerProvider,
    cases: list[DenseEvalCase],
    *,
    query_expansions: Mapping[str, QueryExpansion] | None = None,
    parent_texts: Mapping[str, str],
    dense_query_variants: Sequence[str] = ("zh", "en", "terms"),
    bm25_query_variants: Sequence[str] = ("en", "terms"),
    rerank_query_variant: str = "en",
    rerank_pool_size: int = 50,
    top_k: int = 10,
    small_top_k: int = 50,
    parent_aggregation: str = "max",
    rrf_k: int = 60,
    max_passage_chars: int = 6000,
    require_reviewed_expansions: bool = False,
    query_expansions_path: str | None = None,
    parent_chunks_path: str | None = None,
) -> DenseEvalReport:
    if rerank_pool_size < top_k:
        raise ValueError("rerank_pool_size must be greater than or equal to top_k")
    if top_k < 10:
        raise ValueError("top_k must be at least 10")

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

    case_by_id = {case.case_id: case for case in cases}
    reranked_by_case_id: dict[str, list[DenseEvalRetrievedHit]] = {}
    query_variants_by_case_id: dict[str, dict[str, str]] = {}
    for result in hybrid_report.results:
        case = case_by_id[result.case_id]
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
            top_k=top_k,
            max_passage_chars=max_passage_chars,
        )
        query_variants_by_case_id[result.case_id] = {
            **result.query_variants,
            f"rerank:{rerank_query_variant}": rerank_query,
        }

    report = evaluate_ranked_hits(cases, reranked_by_case_id, top_k=max(top_k, 10))
    enriched_results = [
        replace(result, query_variants=query_variants_by_case_id.get(result.case_id, {}))
        for result in report.results
    ]
    return replace(
        report,
        results=enriched_results,
        retrieval_config={
            "retrieval_method": "hybrid_rerank",
            "candidate_retrieval_method": "hybrid",
            "dense_query_variants": list(dense_query_variants),
            "bm25_query_variants": list(bm25_query_variants),
            "rerank_query_variant": rerank_query_variant,
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
            "require_reviewed_expansions": require_reviewed_expansions,
            "reranker": reranker.model_info.to_dict(),
        },
    )
```

- [ ] **Step 4: Run rerank eval tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_eval_green tests/test_rag_rerank_eval.py tests/test_rag_hybrid_eval.py tests/test_rag_reranking.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Checkpoint without committing**

Run:

```powershell
git diff -- src/fireclaw_core/rag/rerank_eval.py tests/test_rag_rerank_eval.py
```

Expected:

```text
diff output shows the hybrid rerank evaluation wrapper and tests
```

---
