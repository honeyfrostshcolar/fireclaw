# FireClaw Multi-Query Rerank RRF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an evaluation-only multi-query rerank mode that reranks the same hybrid parent candidate pool with `zh`, `en`, and `terms` query variants, then fuses the per-query rerank rankings with RRF.

**Architecture:** Keep the existing hybrid retrieval stage unchanged: dense `zh,en,terms` plus BM25 `en,terms` still produce the parent candidate pool. Add a second-stage rerank fusion utility that calls the existing reranker once per selected rerank query variant, converts each score list into ranks, and fuses those ranks with RRF. Keep the existing single-query rerank path and CLI flag for backward-compatible ablations.

**Tech Stack:** Python, dataclasses, pytest, existing FireClaw RAG modules, `FlagEmbedding.FlagReranker` through `BGEFlagRerankerProvider`, deterministic `FakeRerankerProvider` for tests.

## Global Constraints

- Evaluation-only change: do not modify dense embeddings, BM25 scoring, indexes, chunking, query expansion files, or gold labels in this plan.
- Preserve existing `eval-hybrid-rerank-index --rerank-query-variant en` behavior unless the new multi-query flag is provided.
- Use rank-level RRF for multi-query rerank fusion, not raw reranker score addition.
- Reuse the existing `rrf_k` default value `60`.
- Do not silently download reranker models; real-model runs must use the existing local `.cache/models/bge-reranker-v2-m3`.
- Generated real reports must be saved under `data/rag/fire_rescue/eval/runs/`.
- Commit steps are execution checkpoints; run them only when the user has explicitly asked for commits in that execution session.

## Scope Boundary

This plan implements the first next optimization only:

```text
hybrid top50 parent candidates
-> rerank with zh query
-> rerank with en query
-> rerank with terms query
-> RRF over rerank ranks
-> final top10 parents
```

Follow-up plans should handle these separately so metric changes remain interpretable:

- expanded `gold_parent_ids` / graded relevance labels;
- small-chunk rerank followed by parent aggregation;
- hybrid-score plus rerank-score joint scoring or high-confidence candidate protection.

---

## File Structure

- Modify `src/fireclaw_core/rag/reranking.py`
  - Add query selection for multiple rerank variants.
  - Add parent-level multi-query rerank RRF fusion.
  - Preserve `rerank_parent_hits` for single-query reports.
- Modify `src/fireclaw_core/rag/rerank_eval.py`
  - Add optional `rerank_query_variants`.
  - Use single-query rerank when this option is absent.
  - Use multi-query RRF when this option is present.
- Modify `src/fireclaw_core/rag/rag_cli.py`
  - Add `--rerank-query-variants`.
  - Pass it through without changing the existing `--rerank-query-variant` default.
- Modify `tests/test_rag_reranking.py`
  - Add focused unit tests for query selection and RRF fusion.
- Modify `tests/test_rag_rerank_eval.py`
  - Add wrapper-level regression for multi-query rerank RRF.
- Modify `tests/test_rag_bm25_cli.py`
  - Add CLI regression proving the new flag reaches the report and changes ranking through fake rerank fusion.
- Modify `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
  - Document the difference between hybrid retrieval RRF and multi-query rerank RRF.
- Modify `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`
  - Record implementation, commands, metrics, and remaining research caveats.

---

### Task 1: Core Multi-Query Rerank RRF Utility

**Files:**
- Modify: `src/fireclaw_core/rag/reranking.py`
- Test: `tests/test_rag_reranking.py`

**Interfaces:**
- Consumes: `DenseEvalRetrievedHit`, `RerankerProvider.score_pairs(pairs: Sequence[tuple[str, str]]) -> list[float]`
- Produces:
  - `select_rerank_queries(query_variants: Mapping[str, str], fallback_query: str, variants: Sequence[str]) -> dict[str, str]`
  - `rerank_parent_hits_with_rrf(queries_by_variant: Mapping[str, str], hits: list[DenseEvalRetrievedHit], parent_texts: Mapping[str, str], reranker: RerankerProvider, *, top_k: int = 10, rrf_k: int = 60, max_passage_chars: int = 6000) -> list[DenseEvalRetrievedHit]`

- [ ] **Step 1: Add failing tests for multi-query selection and RRF fusion**

Append these tests to `tests/test_rag_reranking.py`:

```python
from fireclaw_core.rag.reranking import rerank_parent_hits_with_rrf
from fireclaw_core.rag.reranking import select_rerank_queries


def test_select_rerank_queries_returns_requested_variants() -> None:
    queries = select_rerank_queries(
        {
            "dense:zh": "SCBA 中文问题",
            "dense:en": "When should firefighters enter SCBA rehabilitation?",
            "bm25:terms": "SCBA rehabilitation NFPA 1584",
        },
        fallback_query="fallback original query",
        variants=["zh", "en", "terms"],
    )

    assert queries == {
        "zh": "SCBA 中文问题",
        "en": "When should firefighters enter SCBA rehabilitation?",
        "terms": "SCBA rehabilitation NFPA 1584",
    }


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_multi_rerank_rrf_red tests/test_rag_reranking.py -q
```

Expected: FAIL with import errors for `select_rerank_queries` or `rerank_parent_hits_with_rrf`.

- [ ] **Step 3: Implement multi-query selection and RRF rerank**

Add these functions to `src/fireclaw_core/rag/reranking.py` after the existing `rerank_parent_hits` function:

```python
def select_rerank_queries(
    query_variants: Mapping[str, str],
    fallback_query: str,
    variants: Sequence[str],
) -> dict[str, str]:
    selected: dict[str, str] = {}
    for variant in variants:
        name = str(variant).strip()
        if not name:
            raise ValueError("rerank query variants must not contain empty values")
        selected[name] = select_rerank_query(query_variants, fallback_query, variant=name)
    if not selected:
        raise ValueError("at least one rerank query variant is required")
    return selected


def rerank_parent_hits_with_rrf(
    queries_by_variant: Mapping[str, str],
    hits: list[DenseEvalRetrievedHit],
    parent_texts: Mapping[str, str],
    reranker: RerankerProvider,
    *,
    top_k: int = 10,
    rrf_k: int = 60,
    max_passage_chars: int = 6000,
) -> list[DenseEvalRetrievedHit]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")
    if max_passage_chars <= 0:
        raise ValueError("max_passage_chars must be positive")

    selected_queries = {
        str(variant).strip(): str(query).strip()
        for variant, query in queries_by_variant.items()
        if str(variant).strip() and str(query).strip()
    }
    if not selected_queries:
        raise ValueError("at least one non-empty rerank query is required")

    parent_passages: dict[str, str] = {}
    for hit in hits:
        parent_text = parent_texts.get(hit.parent_id)
        if parent_text is None:
            raise ValueError(f"missing parent text for parent_id: {hit.parent_id}")
        parent_passages[hit.parent_id] = parent_text[:max_passage_chars]

    rrf_scores: dict[str, float] = {hit.parent_id: 0.0 for hit in hits}
    per_variant_ranks: dict[str, dict[str, int]] = {hit.parent_id: {} for hit in hits}
    per_variant_scores: dict[str, dict[str, float]] = {hit.parent_id: {} for hit in hits}

    for variant, query in selected_queries.items():
        pairs = [(query, parent_passages[hit.parent_id]) for hit in hits]
        scores = reranker.score_pairs(pairs)
        if len(scores) != len(hits):
            raise ValueError(f"reranker returned {len(scores)} scores for {len(hits)} hits")

        scored = list(zip(hits, scores, strict=True))
        scored.sort(key=lambda item: (-float(item[1]), item[0].rank, item[0].parent_id))
        rank_key = f"rerank:{variant}"
        for rank, (hit, score) in enumerate(scored, start=1):
            rrf_scores[hit.parent_id] += 1.0 / (rrf_k + rank)
            per_variant_ranks[hit.parent_id][rank_key] = rank
            per_variant_scores[hit.parent_id][rank_key] = float(score)

    ranked_hits = sorted(
        hits,
        key=lambda hit: (-rrf_scores[hit.parent_id], hit.rank, hit.parent_id),
    )

    reranked: list[DenseEvalRetrievedHit] = []
    for new_rank, hit in enumerate(ranked_hits[:top_k], start=1):
        parent_id = hit.parent_id
        reranked.append(
            replace(
                hit,
                rank=new_rank,
                score=rrf_scores[parent_id],
                rerank_score=rrf_scores[parent_id],
                base_rank=hit.rank,
                base_score=hit.score,
                base_fusion_score=hit.fusion_score,
                variant_ranks={**hit.variant_ranks, **per_variant_ranks[parent_id]},
                variant_scores={**hit.variant_scores, **per_variant_scores[parent_id]},
                reranker=f"{reranker.model_info.provider}:rrf",
            )
        )
    return reranked
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_multi_rerank_rrf_green tests/test_rag_reranking.py -q
```

Expected: PASS. If the Windows sandbox reports `PermissionError` during pytest cleanup, rerun with the established project style and record both outputs in memory.

- [ ] **Step 5: Review and commit if approved**

Run:

```powershell
git diff -- src/fireclaw_core/rag/reranking.py tests/test_rag_reranking.py
git diff --check -- src/fireclaw_core/rag/reranking.py tests/test_rag_reranking.py
```

If the user has explicitly asked for commits in this execution session:

```powershell
git add src/fireclaw_core/rag/reranking.py tests/test_rag_reranking.py
git commit -m "Add multi-query rerank RRF utility"
```

---

### Task 2: Evaluation Wrapper Support

**Files:**
- Modify: `src/fireclaw_core/rag/rerank_eval.py`
- Test: `tests/test_rag_rerank_eval.py`

**Interfaces:**
- Consumes:
  - `select_rerank_queries` returns `dict[str, str]`
  - `rerank_parent_hits_with_rrf` returns `list[DenseEvalRetrievedHit]`
- Produces:
  - `evaluate_hybrid_retrievers_with_rerank` accepts keyword `rerank_query_variants: Sequence[str] | None = None` and returns `DenseEvalReport`
  - Report config keys for multi mode: `rerank_query_variants`, `rerank_fusion`, `rerank_rrf_k`

- [ ] **Step 1: Add failing wrapper-level regression**

Append this test to `tests/test_rag_rerank_eval.py`:

```python
def test_hybrid_rerank_eval_supports_multi_query_rrf() -> None:
    from fireclaw_core.rag.rerank_eval import evaluate_hybrid_retrievers_with_rerank

    class FakeDenseRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(rank=1, score=0.9, record={"chunk_id": "dense_en", "parent_id": "parent_en", "doc_id": "doc"}),
                DenseHit(rank=2, score=0.8, record={"chunk_id": "dense_terms", "parent_id": "parent_terms", "doc_id": "doc"}),
                DenseHit(rank=3, score=0.7, record={"chunk_id": "dense_both", "parent_id": "parent_both", "doc_id": "doc"}),
            ]

    class FakeBM25Retriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(rank=1, score=3.0, record={"chunk_id": "bm25_terms", "parent_id": "parent_terms", "doc_id": "doc"}),
                DenseHit(rank=2, score=2.0, record={"chunk_id": "bm25_both", "parent_id": "parent_both", "doc_id": "doc"}),
                DenseHit(rank=3, score=1.0, record={"chunk_id": "bm25_en", "parent_id": "parent_en", "doc_id": "doc"}),
            ]

    case = DenseEvalCase(
        case_id="case",
        topic="smokeview",
        query="中文问题",
        gold_parent_ids=["parent_both"],
    )
    expansions = {
        "case": QueryExpansion(
            case_id="case",
            query_zh="ventilation LOAD3DSMOKE",
            reviewed_query_en="ventilation explanation",
            term_query="LOAD3DSMOKE HRRPUV",
            terms=["LOAD3DSMOKE", "HRRPUV"],
            status="reviewed",
        )
    }
    parent_texts = {
        "parent_en": "ventilation natural language explanation",
        "parent_terms": "LOAD3DSMOKE HRRPUV smokeview command",
        "parent_both": "ventilation LOAD3DSMOKE smokeview",
    }

    report = evaluate_hybrid_retrievers_with_rerank(
        FakeDenseRetriever(),
        FakeBM25Retriever(),
        FakeRerankerProvider(),
        [case],
        query_expansions=expansions,
        parent_texts=parent_texts,
        dense_query_variants=["zh", "en", "terms"],
        bm25_query_variants=["en", "terms"],
        rerank_query_variants=["zh", "en", "terms"],
        rerank_pool_size=10,
        top_k=10,
        require_reviewed_expansions=True,
    )

    assert report.hit_at_1 == 1.0
    assert report.results[0].top_hits[0].parent_id == "parent_both"
    assert report.results[0].top_hits[0].reranker == "fake-reranker:rrf"
    assert report.results[0].query_variants["rerank:zh"] == "ventilation LOAD3DSMOKE"
    assert report.results[0].query_variants["rerank:en"] == "ventilation explanation"
    assert report.results[0].query_variants["rerank:terms"] == "LOAD3DSMOKE HRRPUV"
    assert report.retrieval_config is not None
    assert report.retrieval_config["retrieval_method"] == "hybrid_rerank"
    assert report.retrieval_config["rerank_query_variants"] == ["zh", "en", "terms"]
    assert report.retrieval_config["rerank_fusion"] == "rrf"
    assert report.retrieval_config["rerank_rrf_k"] == 60
```

- [ ] **Step 2: Run the focused RED test**

Run:

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_multi_rerank_eval_red tests/test_rag_rerank_eval.py::test_hybrid_rerank_eval_supports_multi_query_rrf -q
```

Expected: FAIL because `evaluate_hybrid_retrievers_with_rerank` does not accept `rerank_query_variants`.

- [ ] **Step 3: Implement the wrapper branching**

Modify imports in `src/fireclaw_core/rag/rerank_eval.py`:

```python
from fireclaw_core.rag.reranking import rerank_parent_hits_with_rrf
from fireclaw_core.rag.reranking import select_rerank_queries
```

Extend the function signature:

```python
    rerank_query_variant: str = "en",
    rerank_query_variants: Sequence[str] | None = None,
```

Replace the single-query-only loop body with this branching pattern:

```python
    selected_rerank_variants = list(rerank_query_variants or [])

    for result in hybrid_report.results:
        case = case_by_id[result.case_id]
        if selected_rerank_variants:
            rerank_queries = select_rerank_queries(
                result.query_variants,
                fallback_query=case.query,
                variants=selected_rerank_variants,
            )
            reranked_by_case_id[result.case_id] = rerank_parent_hits_with_rrf(
                rerank_queries,
                result.top_hits,
                parent_texts,
                reranker,
                top_k=top_k,
                rrf_k=rrf_k,
                max_passage_chars=max_passage_chars,
            )
            query_variants_by_case_id[result.case_id] = {
                **result.query_variants,
                **{f"rerank:{variant}": query for variant, query in rerank_queries.items()},
            }
        else:
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
```

In `retrieval_config`, add these keys while keeping existing keys:

```python
            "rerank_query_variant": rerank_query_variant,
            "rerank_query_variants": selected_rerank_variants or None,
            "rerank_fusion": "rrf" if selected_rerank_variants else "score",
            "rerank_rrf_k": rrf_k if selected_rerank_variants else None,
```

- [ ] **Step 4: Run wrapper and utility tests**

Run:

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_multi_rerank_eval_green tests/test_rag_rerank_eval.py tests/test_rag_reranking.py -q
```

Expected: PASS.

- [ ] **Step 5: Review and commit if approved**

Run:

```powershell
git diff -- src/fireclaw_core/rag/rerank_eval.py tests/test_rag_rerank_eval.py
git diff --check -- src/fireclaw_core/rag/rerank_eval.py tests/test_rag_rerank_eval.py
```

If the user has explicitly asked for commits in this execution session:

```powershell
git add src/fireclaw_core/rag/rerank_eval.py tests/test_rag_rerank_eval.py
git commit -m "Add multi-query hybrid rerank evaluation"
```

---

### Task 3: CLI Integration

**Files:**
- Modify: `src/fireclaw_core/rag/rag_cli.py`
- Test: `tests/test_rag_bm25_cli.py`

**Interfaces:**
- Consumes: `evaluate_hybrid_retrievers_with_rerank` with the `rerank_query_variants` keyword
- Produces: CLI flag `--rerank-query-variants`, CSV parsed with `_parse_csv_arg`, optional so existing `--rerank-query-variant` still works.

- [ ] **Step 1: Add failing CLI regression**

Append this test to `tests/test_rag_bm25_cli.py`:

```python
def test_cli_eval_hybrid_rerank_index_with_multi_query_rrf(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider
    from fireclaw_core.rag.dense_retrieval import build_dense_index

    records_path = tmp_path / "records.jsonl"
    _write_jsonl(
        records_path,
        [
            _record("en", "ventilation explanation rescue"),
            _record("terms", "LOAD3DSMOKE HRRPUV rescue"),
            _record("both", "ventilation LOAD3DSMOKE rescue"),
        ],
    )
    dense_index_dir = tmp_path / "dense_index"
    build_dense_index(records_path, dense_index_dir, FakeEmbeddingProvider(), batch_size=2)

    bm25_index_dir = tmp_path / "bm25_index"
    assert main(["build-bm25-index", "--records", str(records_path), "--index-dir", str(bm25_index_dir)]) == 0
    capsys.readouterr()

    parent_chunks_path = tmp_path / "parent_chunks.jsonl"
    _write_jsonl(
        parent_chunks_path,
        [
            {"parent_id": "en__parent", "text": "ventilation natural language explanation"},
            {"parent_id": "terms__parent", "text": "LOAD3DSMOKE HRRPUV smokeview command"},
            {"parent_id": "both__parent", "text": "ventilation LOAD3DSMOKE smokeview"},
        ],
    )
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [{"case_id": "case", "topic": "smokeview", "query": "rescue", "gold_parent_ids": ["both__parent"]}],
    )
    expansions_path = tmp_path / "expansions.jsonl"
    _write_jsonl(
        expansions_path,
        [
            {
                "case_id": "case",
                "query_zh": "ventilation LOAD3DSMOKE",
                "reviewed_query_en": "ventilation explanation",
                "term_query": "LOAD3DSMOKE HRRPUV",
                "terms": ["LOAD3DSMOKE", "HRRPUV"],
                "status": "reviewed",
            }
        ],
    )
    output_path = tmp_path / "multi_rerank_report.json"

    exit_code = main(
        [
            "eval-hybrid-rerank-index",
            "--provider",
            "fake",
            "--dense-index-dir",
            str(dense_index_dir),
            "--bm25-index-dir",
            str(bm25_index_dir),
            "--reranker-provider",
            "fake",
            "--parent-chunks",
            str(parent_chunks_path),
            "--cases",
            str(cases_path),
            "--query-expansions",
            str(expansions_path),
            "--dense-query-variants",
            "zh,en,terms",
            "--bm25-query-variants",
            "en,terms",
            "--rerank-query-variants",
            "zh,en,terms",
            "--require-reviewed-expansions",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["retrieval_config"]["retrieval_method"] == "hybrid_rerank"
    assert report["retrieval_config"]["rerank_query_variants"] == ["zh", "en", "terms"]
    assert report["retrieval_config"]["rerank_fusion"] == "rrf"
    assert report["results"][0]["top_hits"][0]["parent_id"] == "both__parent"
    assert report["results"][0]["top_hits"][0]["reranker"] == "fake-reranker:rrf"
    assert json.loads(capsys.readouterr().out)["retrieval_config"]["rerank_fusion"] == "rrf"
```

- [ ] **Step 2: Run focused RED test**

Run:

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_multi_rerank_cli_red tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_multi_query_rrf -q
```

Expected: FAIL with unrecognized argument `--rerank-query-variants`.

- [ ] **Step 3: Add CLI flag and pass-through**

In `src/fireclaw_core/rag/rag_cli.py`, add this parser argument near `--rerank-query-variant`:

```python
    hybrid_rerank_eval.add_argument("--rerank-query-variants", default=None)
```

In `_cmd_eval_hybrid_rerank_index`, pass:

```python
        rerank_query_variants=_parse_csv_arg(args.rerank_query_variants) if args.rerank_query_variants else None,
```

Keep the existing single-query line:

```python
        rerank_query_variant=args.rerank_query_variant,
```

- [ ] **Step 4: Run CLI and wrapper tests**

Run:

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_multi_rerank_cli_green tests/test_rag_bm25_cli.py tests/test_rag_rerank_eval.py tests/test_rag_reranking.py -q
```

Expected: PASS.

- [ ] **Step 5: Review and commit if approved**

Run:

```powershell
git diff -- src/fireclaw_core/rag/rag_cli.py tests/test_rag_bm25_cli.py
git diff --check -- src/fireclaw_core/rag/rag_cli.py tests/test_rag_bm25_cli.py
```

If the user has explicitly asked for commits in this execution session:

```powershell
git add src/fireclaw_core/rag/rag_cli.py tests/test_rag_bm25_cli.py
git commit -m "Expose multi-query rerank RRF in CLI"
```

---

### Task 4: Documentation, Real Report, and Memory

**Files:**
- Modify: `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
- Modify: `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`
- Generate: `data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v2_rerank_bge_reranker_v2_m3_multi_rrf_report.json`

**Interfaces:**
- Consumes: CLI command `eval-hybrid-rerank-index --rerank-query-variants zh,en,terms`
- Produces: documented multi-query rerank RRF report and metric comparison against hybrid baseline, `rerank:en`, `rerank:terms`, and `rerank:zh`

- [ ] **Step 1: Document retrieval RRF versus rerank RRF**

Append a concise Chinese section to `docs/rag/dense-evaluation-walkthrough.zh-CN.md`:

```markdown
### Multi-Query Rerank RRF

`hybrid` 阶段的 RRF 和 `rerank` 阶段的 RRF 是两层不同的融合。

第一层仍然负责召回候选：

```text
dense: zh + en + terms
bm25: en + terms
-> RRF
-> top50 parent candidates
```

第二层只在这些候选内部重新排序：

```text
rerank: zh
rerank: en
rerank: terms
-> per-query cross-encoder ranks
-> RRF
-> final top10 parent candidates
```

这避免直接相加不同 query 下的 cross-encoder 原始分数，也保留中文意图、英文语义和专业术语三种信号。
```

- [ ] **Step 2: Run focused regression suite**

Run:

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_multi_rerank_final tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py tests/test_rag_hybrid_eval.py tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py -q
git diff --check
```

Expected:

```text
pytest: all tests pass, aside from known Windows sandbox basetemp PermissionError if it appears
git diff --check: exit code 0
```

- [ ] **Step 3: Run the real multi-query BGE reranker report**

Run:

```powershell
cmd /c "set PYTHONPATH=src&& .\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-hybrid-rerank-index --provider bge-m3 --model-path .cache/models/bge-m3 --dense-index-dir data/rag/fire_rescue/indexes/dense/bge-m3 --bm25-index-dir data/rag/fire_rescue/indexes/bm25/small_v1 --reranker-provider bge-reranker --reranker-model-path .cache/models/bge-reranker-v2-m3 --reranker-batch-size 8 --reranker-max-length 512 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v2.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v2.jsonl --dense-query-variants zh,en,terms --bm25-query-variants en,terms --rerank-query-variants zh,en,terms --small-top-k 50 --rerank-pool-size 50 --top-k 10 --require-reviewed-expansions --output data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v2_rerank_bge_reranker_v2_m3_multi_rrf_report.json"
```

Expected: report file is created. Compare these metrics in the memory record:

```text
hybrid_bge_m3_bm25_v2_expanded_parent_report.json
hybrid_bge_m3_bm25_v2_rerank_bge_reranker_v2_m3_report.json
hybrid_bge_m3_bm25_v2_rerank_bge_reranker_v2_m3_terms_report.json
hybrid_bge_m3_bm25_v2_rerank_bge_reranker_v2_m3_zh_report.json
hybrid_bge_m3_bm25_v2_rerank_bge_reranker_v2_m3_multi_rrf_report.json
```

- [ ] **Step 4: Update memory with exact results**

Append this structure to `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`:

```markdown
## 2026-07-08 Multi-Query Rerank RRF

### Goal

Evaluate whether `zh + en + terms` rerank RRF improves over single-query rerank.

### Commands

List the exact pytest, diff-check, and real report commands that were run. Include both the sandbox result and the established-style rerun result if the known Windows basetemp permission issue appears.

### Results

```text
hybrid baseline: Hit@1=0.366667 Hit@5=0.766667 Hit@10=0.966667 MRR@10=0.555040 Recall@10=0.966667
rerank en: Hit@1=0.333333 Hit@5=0.700000 Hit@10=0.833333 MRR@10=0.517778 Recall@10=0.833333
rerank terms: Hit@1=0.433333 Hit@5=0.800000 Hit@10=0.933333 MRR@10=0.585317 Recall@10=0.933333
rerank zh: Hit@1=0.433333 Hit@5=0.666667 Hit@10=0.866667 MRR@10=0.553783 Recall@10=0.866667
```

Then add one additional line for `rerank multi-query rrf` using the exact numeric values from `hybrid_bge_m3_bm25_v2_rerank_bge_reranker_v2_m3_multi_rrf_report.json`.

### Conclusion

State whether multi-query RRF improved `Hit@1`, `Hit@5`, `Hit@10`, `MRR@10`, and `Recall@10`, and whether it should replace single-query rerank as the recommended evaluation setting.

### Remaining Questions

- whether strict single gold parent labels still undercount genuinely relevant neighbors;
- whether child-level rerank would recover evidence-bearing small chunks better than parent-level rerank;
- whether hybrid-score protection is needed if Hit@10 still drops.
```

- [ ] **Step 5: Review and commit if approved**

Run:

```powershell
git diff -- docs/rag/dense-evaluation-walkthrough.zh-CN.md memory/2026-07-08/fireclaw-rag-reranker-evaluation.md
git status --short
```

If the user has explicitly asked for commits in this execution session:

```powershell
git add src/fireclaw_core/rag/reranking.py src/fireclaw_core/rag/rerank_eval.py src/fireclaw_core/rag/rag_cli.py tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py docs/rag/dense-evaluation-walkthrough.zh-CN.md memory/2026-07-08/fireclaw-rag-reranker-evaluation.md data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v2_rerank_bge_reranker_v2_m3_multi_rrf_report.json
git commit -m "Add multi-query rerank RRF evaluation"
```

---

## Self-Review Checklist

- Spec coverage:
  - Multi-query rerank RRF is implemented in Task 1.
  - Evaluation wrapper chooses single-query or multi-query mode in Task 2.
  - CLI exposes the new mode without breaking the existing default in Task 3.
  - Documentation, real report, and memory record are covered in Task 4.
- Placeholder scan:
  - No placeholder markers, incomplete sections, or unspecified edge handling remains.
- Type consistency:
  - `rerank_query_variants` is consistently `Sequence[str] | None`.
  - `select_rerank_queries` returns `dict[str, str]`.
  - `rerank_parent_hits_with_rrf` returns `list[DenseEvalRetrievedHit]`.
- Scope check:
  - Gold-label expansion, child-level rerank, and hybrid-score protection are explicitly out of scope for this plan and should be separate experiments.
