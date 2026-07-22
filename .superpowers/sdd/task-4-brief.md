### Task 4: Documentation and Focused Verification

**Files:**
- Modify: `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
- Modify or create: `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`

**Interfaces:**
- Consumes:
  - Completed Tasks 1-3.
  - Existing v2 reports.
- Produces:
  - Documentation explaining rerank position in the retrieval pipeline.
  - Memory record with implementation status and test commands.

- [ ] **Step 1: Add reranker walkthrough section**

Append this section to `docs/rag/dense-evaluation-walkthrough.zh-CN.md`:

```markdown
## Optional Evaluation: Hybrid Reranking

Reranking is a second-stage ranking step. It does not replace dense retrieval,
BM25, query expansion, parent aggregation, or RRF. It first asks hybrid retrieval
to produce a larger parent candidate pool, then scores each `(query, parent_text)`
pair with a reranker model and sorts candidates by the reranker score.

Default first-pass evaluation settings:

```text
hybrid parent candidates: top 50
rerank query variant: reviewed English query
reranker input text: full parent text from parent_chunks.jsonl
final report cutoff: top 10
```

The first reranker experiment should be interpreted as a ranking-quality
ablation. It can improve `Hit@1`, `Hit@5`, and `MRR@10` when the correct parent
is already inside the candidate pool. It cannot recover a gold parent that
hybrid retrieval did not retrieve into the rerank pool.
```

- [ ] **Step 2: Run focused unit tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_all tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py tests/test_rag_hybrid_eval.py tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py -q
```

Expected:

```text
all selected tests pass
```

If managed Windows sandbox cleanup raises `PermissionError` for the pytest basetemp directory, rerun the same command with escalation and record both outcomes in memory.

- [ ] **Step 3: Update memory after focused verification**

Create or append `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`:

```markdown
# FireClaw RAG Hybrid Reranker Evaluation

**Date:** 2026-07-08
**Status:** Reranker evaluation implementation in progress.

## Goal

Add a pretrained cross-encoder reranker evaluation layer after hybrid Dense+BM25 parent retrieval.

## User Decisions

- Use a pretrained reranker first; do not train or fine-tune.
- Keep the existing BM25 preservation concern as a known limitation for now.
- Do not change retrieval algorithms without discussion.

## Implementation Notes

- Reranker is inserted after hybrid parent RRF.
- Candidate pool size is 50 parents by default.
- Final metric cutoff remains top 10.
- Rerank query variant defaults to reviewed English.
- Parent text comes from `data/rag/fire_rescue/chunks/parent_chunks.jsonl`.

## Commands

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_all tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py tests/test_rag_hybrid_eval.py tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py -q
```

## Results

After execution, write one timestamped bullet containing the exact pytest result line, for example `7 passed in 0.42s`, or the exact sandbox `PermissionError` followed by the escalated rerun result.

## Next Step

Run a real BGE reranker report if a local reranker model exists, or ask the user whether to download/provide one.
```

- [ ] **Step 4: Run diff check**

Run:

```powershell
git diff --check
```

Expected:

```text
exit code 0
```

---
