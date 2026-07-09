# FireClaw RAG Next Rerank Ablations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add expanded/graded relevance evaluation, small-chunk rerank-to-parent aggregation, hybrid+rerank rank fusion, and a report matrix comparing the current baseline/rerank variants against the new ablations.

**Architecture:** Keep the current retrieval stack as the source of candidates. Add optional evaluation-layer relevance judgments, optional small-chunk rerank mode, and optional rank-level joint fusion. Existing default CLI behavior must remain unchanged.

**Tech Stack:** Python dataclasses, JSONL/JSON reports, pytest, existing FireClaw RAG modules, local `FlagEmbedding.FlagReranker` through `BGEFlagRerankerProvider`.

## Global Constraints

- Do not modify dense embeddings, BM25 scoring, indexes, chunking, existing query expansion files, or `dense_gold_cases_zh_v2.jsonl`.
- Preserve existing `eval-hybrid-index` and `eval-hybrid-rerank-index` behavior unless new optional flags are provided.
- Graded relevance is evaluation-only; `grade >= 2` is relevant for Hit/Recall/MRR.
- Add `nDCG@10` only when graded relevance judgments are provided.
- Use rank-level RRF for joint hybrid+rerank fusion; do not use raw weighted score addition in this plan.
- Real reranker runs must use the existing local `.cache/models/bge-reranker-v2-m3`.
- Generated reports must be saved under `data/rag/fire_rescue/eval/runs/`.
- Commit only when the user explicitly asks during execution.

---

## File Structure

- Create `src/fireclaw_core/rag/relevance_eval.py`
  - Load JSONL relevance judgments.
  - Validate grades.
  - Convert judgments into expanded relevant parent ids.
  - Compute `nDCG@10`.
- Modify `src/fireclaw_core/rag/dense_eval.py`
  - Allow `evaluate_ranked_hits(cases, hits_by_case_id, top_k=10, relevance_judgments=None, relevance_threshold=2)`.
  - Add optional `ndcg_at_10` to case results and reports.
- Modify `src/fireclaw_core/rag/reranking.py`
  - Load small chunk texts.
  - Rerank small hits by full small chunk text.
  - Aggregate reranked small hits to parent.
  - Fuse hybrid parent ranking with rerank parent ranking by RRF.
- Modify `src/fireclaw_core/rag/hybrid_eval.py`
  - Expose hybrid small-chunk candidate generation as a reusable helper.
- Modify `src/fireclaw_core/rag/rerank_eval.py`
  - Add optional `rerank_level="parent"|"small"`.
  - Add optional `joint_fusion=None|"rrf"`.
  - Thread optional relevance judgments into final metrics.
- Modify `src/fireclaw_core/rag/rag_cli.py`
  - Add flags:
    - `--relevance-judgments`
    - `--relevance-threshold`
    - `--rerank-level parent|small`
    - `--small-chunks`
    - `--joint-fusion none|rrf`
- Tests:
  - `tests/test_rag_relevance_eval.py`
  - `tests/test_rag_dense_eval.py`
  - `tests/test_rag_reranking.py`
  - `tests/test_rag_rerank_eval.py`
  - `tests/test_rag_bm25_cli.py`
- Generate:
  - `data/rag/fire_rescue/eval/relevance_judgments_zh_v3_agent_reviewed.jsonl`
  - report files under `data/rag/fire_rescue/eval/runs/`
- Update:
  - `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
  - `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`

---

### Task 1: Graded Relevance Evaluation

**Files:**
- Create: `src/fireclaw_core/rag/relevance_eval.py`
- Modify: `src/fireclaw_core/rag/dense_eval.py`
- Test: `tests/test_rag_relevance_eval.py`
- Test: `tests/test_rag_dense_eval.py`

**Interfaces:**
- Produces:
  - `RelevanceJudgment(case_id: str, parent_id: str, grade: int, source: str = "", notes: str = "")`
  - `load_relevance_judgments(path: Path) -> dict[str, dict[str, RelevanceJudgment]]`
  - `relevant_parent_ids(judgments: Mapping[str, RelevanceJudgment], threshold: int = 2) -> list[str]`
  - `ndcg_at_k(ranked_parent_ids: Sequence[str], judgments: Mapping[str, RelevanceJudgment], *, k: int = 10) -> float`
  - `evaluate_ranked_hits(cases: list[DenseEvalCase], hits_by_case_id: dict[str, list[DenseEvalRetrievedHit]], *, top_k: int = 10, relevance_judgments: Mapping[str, Mapping[str, RelevanceJudgment]] | None = None, relevance_threshold: int = 2) -> DenseEvalReport`

- [ ] **Step 1: Add relevance loader and nDCG tests**

Create `tests/test_rag_relevance_eval.py` with tests for valid JSONL, duplicate `(case_id,parent_id)`, invalid grade, relevant threshold, and `nDCG@10`.

- [ ] **Step 2: Run RED**

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_relevance_eval_red tests/test_rag_relevance_eval.py -q
```

Expected: import failure for `fireclaw_core.rag.relevance_eval`.

- [ ] **Step 3: Implement relevance loader and nDCG**

Implement `src/fireclaw_core/rag/relevance_eval.py`. Use DCG gain `2**grade - 1` and log denominator `log2(rank + 1)`.

- [ ] **Step 4: Add dense eval integration tests**

Append tests to `tests/test_rag_dense_eval.py` proving:

- with judgments, `grade >= 2` expands relevant parent ids;
- `ndcg_at_10` is serialized in `DenseEvalReport.to_dict()`;
- default behavior without judgments is unchanged.

- [ ] **Step 5: Implement dense eval integration**

Modify `DenseEvalCaseResult` and `DenseEvalReport` with optional `ndcg_at_10`. Keep `to_dict()` backward-compatible by omitting `ndcg_at_10` when `None`.

- [ ] **Step 6: Run GREEN**

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_relevance_eval_green tests/test_rag_relevance_eval.py tests/test_rag_dense_eval.py -q
```

Expected: PASS.

---

### Task 2: Small-Chunk Rerank to Parent Aggregation

**Files:**
- Modify: `src/fireclaw_core/rag/hybrid_eval.py`
- Modify: `src/fireclaw_core/rag/reranking.py`
- Modify: `src/fireclaw_core/rag/rerank_eval.py`
- Test: `tests/test_rag_reranking.py`
- Test: `tests/test_rag_rerank_eval.py`

**Interfaces:**
- Produces:
  - `load_small_texts(path: Path) -> dict[str, str]`
  - `rerank_small_hits(query: str, hits: list[DenseEvalRetrievedHit], small_texts: Mapping[str, str], reranker: RerankerProvider, *, top_k: int, max_passage_chars: int = 2000) -> list[DenseEvalRetrievedHit]`
  - `rerank_small_hits_with_rrf(queries_by_variant: Mapping[str, str], hits: list[DenseEvalRetrievedHit], small_texts: Mapping[str, str], reranker: RerankerProvider, *, top_k: int, rrf_k: int = 60, max_passage_chars: int = 2000) -> list[DenseEvalRetrievedHit]`
  - `aggregate_reranked_small_hits_by_parent(hits: list[DenseEvalRetrievedHit], *, top_k: int) -> list[DenseEvalRetrievedHit]`
  - `evaluate_hybrid_retrievers_with_rerank(dense_retriever, bm25_retriever, reranker, cases, *, query_expansions=None, parent_texts, dense_query_variants=("zh","en","terms"), bm25_query_variants=("en","terms"), rerank_query_variant="en", rerank_query_variants=None, rerank_pool_size=50, top_k=10, small_top_k=50, parent_aggregation="max", rrf_k=60, max_passage_chars=6000, require_reviewed_expansions=False, query_expansions_path=None, parent_chunks_path=None, rerank_level="parent", small_texts=None) -> DenseEvalReport`

- [ ] **Step 1: Add small-text loader and rerank tests**

Add tests that:

- missing small chunk text raises `ValueError`;
- small-chunk rerank promotes the parent whose child chunk contains the evidence;
- aggregation chooses the best reranked child per parent and preserves `child_ranks`.

- [ ] **Step 2: Run RED**

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_small_rerank_red tests/test_rag_reranking.py::test_rerank_small_hits_uses_full_small_text tests/test_rag_reranking.py::test_aggregate_reranked_small_hits_by_parent -q
```

Expected: import/name failures.

- [ ] **Step 3: Expose hybrid small candidates**

In `hybrid_eval.py`, factor candidate generation so rerank eval can request hybrid small candidates before parent aggregation. Preserve existing `evaluate_hybrid_retrievers_with_expansion` behavior and public signature.

- [ ] **Step 4: Implement small rerank utilities**

Implement the functions in `reranking.py`. Use full `small_texts[chunk_id]`, not `text_preview`.

- [ ] **Step 5: Add wrapper-level regression**

Add `tests/test_rag_rerank_eval.py::test_hybrid_rerank_eval_supports_small_chunk_rerank_to_parent`.

- [ ] **Step 6: Implement `rerank_level` branching**

In `evaluate_hybrid_retrievers_with_rerank`, branch without changing current default behavior:

```text
rerank_level == "parent": current path
rerank_level == "small": small candidate path -> rerank small hits -> aggregate to parent
```

- [ ] **Step 7: Run GREEN**

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_small_rerank_green tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_hybrid_eval.py -q
```

Expected: PASS.

---

### Task 3: Hybrid + Rerank Rank-Level Joint Fusion

**Files:**
- Modify: `src/fireclaw_core/rag/reranking.py`
- Modify: `src/fireclaw_core/rag/rerank_eval.py`
- Test: `tests/test_rag_reranking.py`
- Test: `tests/test_rag_rerank_eval.py`

**Interfaces:**
- Produces:
  - `fuse_hybrid_and_rerank_hits(hybrid_hits: list[DenseEvalRetrievedHit], reranked_hits: list[DenseEvalRetrievedHit], *, top_k: int = 10, rrf_k: int = 60) -> list[DenseEvalRetrievedHit]`
  - `evaluate_hybrid_retrievers_with_rerank(dense_retriever, bm25_retriever, reranker, cases, *, query_expansions=None, parent_texts, dense_query_variants=("zh","en","terms"), bm25_query_variants=("en","terms"), rerank_query_variant="en", rerank_query_variants=None, rerank_pool_size=50, top_k=10, small_top_k=50, parent_aggregation="max", rrf_k=60, max_passage_chars=6000, require_reviewed_expansions=False, query_expansions_path=None, parent_chunks_path=None, rerank_level="parent", small_texts=None, joint_fusion=None) -> DenseEvalReport`

- [ ] **Step 1: Add joint fusion tests**

Test that a strong hybrid top candidate can remain in top10 after rerank demotion and that output records include `variant_ranks` for `hybrid` and `rerank`.

- [ ] **Step 2: Run RED**

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_joint_fusion_red tests/test_rag_reranking.py::test_fuse_hybrid_and_rerank_hits_uses_rank_level_rrf -q
```

Expected: missing function.

- [ ] **Step 3: Implement joint fusion utility**

Use RRF over two lists:

```text
hybrid rank contribution: 1 / (rrf_k + hybrid_rank)
rerank rank contribution: 1 / (rrf_k + rerank_rank)
```

Tie-break by hybrid rank, rerank rank, parent_id.

- [ ] **Step 4: Add wrapper regression**

Add a test proving `joint_fusion="rrf"` changes final order versus rerank-only and sets report config:

```text
"joint_fusion": "rrf"
"joint_rrf_k": 60
```

- [ ] **Step 5: Implement wrapper branching**

After the selected parent or small rerank path returns reranked parent hits, optionally call `fuse_hybrid_and_rerank_hits(hybrid_hits, reranked_hits, top_k=top_k, rrf_k=rrf_k)`.

- [ ] **Step 6: Run GREEN**

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_joint_fusion_green tests/test_rag_reranking.py tests/test_rag_rerank_eval.py -q
```

Expected: PASS.

---

### Task 4: CLI, Agent-Reviewed v3 Relevance File, and Report Matrix

**Files:**
- Modify: `src/fireclaw_core/rag/rag_cli.py`
- Test: `tests/test_rag_bm25_cli.py`
- Generate: `data/rag/fire_rescue/eval/relevance_judgments_zh_v3_agent_reviewed.jsonl`
- Generate reports under `data/rag/fire_rescue/eval/runs/`

**Interfaces:**
- Adds CLI flags:
  - `--relevance-judgments`
  - `--relevance-threshold`
  - `--rerank-level`
  - `--small-chunks`
  - `--joint-fusion`

- [ ] **Step 1: Add CLI tests**

Extend CLI tests for:

- `--relevance-judgments` adds `ndcg_at_10`;
- `--rerank-level small --small-chunks <path>` works;
- `--joint-fusion rrf` reaches report config.

- [ ] **Step 2: Run RED**

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_next_ablation_cli_red tests/test_rag_bm25_cli.py -q
```

Expected: missing flags.

- [ ] **Step 3: Implement CLI wiring**

Load relevance judgments only when flag is provided. Load `small_chunks.jsonl` only for `rerank_level="small"`.

- [ ] **Step 4: Generate conservative v3 relevance file**

Create `data/rag/fire_rescue/eval/relevance_judgments_zh_v3_agent_reviewed.jsonl` with:

- every strict v2 gold parent as `grade=3`;
- only clearly relevant alternate parent candidates as `grade=2`;
- notes marking the file as agent-reviewed and not human gold.

- [ ] **Step 5: Run real report matrix**

Run strict v2 reports and v3 graded reports for:

```text
hybrid baseline
rerank en
rerank terms
rerank zh
rerank multi-query rrf
small-chunk rerank terms
small-chunk rerank multi-query rrf
joint hybrid+rerank terms
joint hybrid+multi-query rrf
joint hybrid+small-chunk terms
joint hybrid+small-chunk multi-query rrf
```

Use output names with suffixes:

```text
hybrid_bge_m3_bm25_v3_small_rerank_terms_strict_v2_report.json
hybrid_bge_m3_bm25_v3_small_rerank_terms_graded_v3_report.json
```

- [ ] **Step 6: Run GREEN**

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_next_ablation_cli_green tests/test_rag_bm25_cli.py tests/test_rag_relevance_eval.py tests/test_rag_rerank_eval.py tests/test_rag_reranking.py -q
```

Expected: PASS.

---

### Task 5: Documentation, Memory, Final Review

**Files:**
- Modify: `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
- Modify: `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`

- [ ] **Step 1: Document the new ablation layers**

Add a Chinese section explaining:

- strict v2 vs agent-reviewed graded v3;
- parent rerank vs small-chunk rerank;
- rerank-only vs joint hybrid+rerank rank fusion.

- [ ] **Step 2: Add a metrics comparison table to memory**

Record exact commands and metrics for all generated reports.

- [ ] **Step 3: Run focused suite**

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_next_ablation_final tests/test_rag_relevance_eval.py tests/test_rag_dense_eval.py tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py tests/test_rag_hybrid_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py -q
git diff --check
```

If the known Windows pytest `basetemp` `PermissionError` appears, record it and rerun with the established external-permission style.

- [ ] **Step 4: Final review**

Use a final reviewer to check:

- old default behavior is preserved;
- v3 labels are not described as human gold;
- small-chunk rerank uses full chunk text;
- joint fusion uses rank-level RRF, not raw scores;
- metric conclusions are not overclaimed.

- [ ] **Step 5: Commit if requested**

Commit only if the user asks after results are reviewed.

---

## Self-Review Checklist

- The plan keeps strict v2 labels unchanged.
- The plan treats v3 graded relevance as an optional evaluation view.
- The plan separates parent rerank, small-chunk rerank, and joint fusion.
- The plan avoids raw hybrid-score + reranker-score addition.
- The plan preserves current CLI defaults.
- The plan includes real report generation and final metric comparison.
