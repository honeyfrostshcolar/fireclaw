# Query Expansion Task 3 Report

## What I Implemented

- Added `evaluate_dense_retriever_with_expansion(...)` in `src/fireclaw_core/rag/dense_eval.py`.
- The expanded evaluator:
  - builds cached query variants through `build_query_variants`;
  - calls the existing retriever once per selected variant;
  - optionally aggregates small hits to parent hits through `aggregate_hits_by_parent`;
  - uses `reciprocal_rank_fuse` when multiple query variants are selected;
  - enriches case results with `query_variants`;
  - returns a `DenseEvalReport` with `retrieval_config`.
- Added optional serialization fields:
  - `DenseEvalCaseResult.query_variants`, omitted from `to_dict()` when empty;
  - `DenseEvalReport.retrieval_config`, omitted from `to_dict()` when `None`.
- Preserved default strict dense baseline behavior:
  - `evaluate_dense_retriever(...)` still requires `top_k >= 10`;
  - baseline report serialization still omits expanded-only metadata when unused.

## TDD Evidence

### RED

Command:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_eval_expanded_red tests/test_rag_dense_eval.py::test_evaluate_dense_retriever_with_parent_ranking_finds_gold_after_dedup tests/test_rag_dense_eval.py::test_evaluate_dense_retriever_with_query_expansion_uses_rrf -q
```

Output:

```text
FF                                                                       [100%]
================================== FAILURES ===================================
__ test_evaluate_dense_retriever_with_parent_ranking_finds_gold_after_dedup ___

    def test_evaluate_dense_retriever_with_parent_ranking_finds_gold_after_dedup() -> None:
>       from fireclaw_core.rag.dense_eval import evaluate_dense_retriever_with_expansion
E       ImportError: cannot import name 'evaluate_dense_retriever_with_expansion' from 'fireclaw_core.rag.dense_eval' (C:\Users\L\Desktop\lpp\fireclaw-master\src\fireclaw_core\rag\dense_eval.py)

tests\test_rag_dense_eval.py:274: ImportError
_________ test_evaluate_dense_retriever_with_query_expansion_uses_rrf _________

    def test_evaluate_dense_retriever_with_query_expansion_uses_rrf() -> None:
>       from fireclaw_core.rag.dense_eval import evaluate_dense_retriever_with_expansion
E       ImportError: cannot import name 'evaluate_dense_retriever_with_expansion' from 'fireclaw_core.rag.dense_eval' (C:\Users\L\Desktop\lpp\fireclaw-master\src\fireclaw_core\rag\dense_eval.py)

tests\test_rag_dense_eval.py:306: ImportError
=========================== short test summary info ===========================
FAILED tests/test_rag_dense_eval.py::test_evaluate_dense_retriever_with_parent_ranking_finds_gold_after_dedup
FAILED tests/test_rag_dense_eval.py::test_evaluate_dense_retriever_with_query_expansion_uses_rrf
2 failed in 0.28s
```

Why expected:

- The tests referenced the new Task 3 API before it existed, so `ImportError` was the expected RED failure.

### GREEN

First sandboxed GREEN attempt hit the known Windows pytest temp cleanup permission issue and obscured full details. I reran with elevated pytest permission, matching the repository's existing memory note for this environment.

Command:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_eval_expanded_green tests/test_rag_dense_eval.py -q
```

Output:

```text
..............                                                           [100%]
14 passed in 0.25s
```

## Files Changed

- `src/fireclaw_core/rag/dense_eval.py`
- `tests/test_rag_dense_eval.py`
- `.superpowers/sdd/query-expansion-task-3-report.md`

Scoped status check:

```text
?? src/fireclaw_core/rag/dense_eval.py
?? tests/test_rag_dense_eval.py
```

The brief's checkpoint command:

```powershell
git diff -- src/fireclaw_core/rag/dense_eval.py tests/test_rag_dense_eval.py
```

produced no diff output because these two files are currently untracked in this workspace, so normal `git diff -- <path>` does not display their contents.

## Self-Review Findings

- `dense_ranking` imports stay local inside `evaluate_dense_retriever_with_expansion` to avoid circular import issues.
- Default baseline serialization remains strict: empty `query_variants` and `None` `retrieval_config` are omitted.
- The expanded evaluator uses cached `QueryExpansion` mappings only. It performs no network or LLM calls.
- Parent aggregation and RRF are delegated to Task 2 functions rather than reimplemented.
- No CLI, data JSONL, docs, memory, `query_expansion.py`, `dense_ranking.py`, or unrelated code was modified.
- No commits were created.

## Concerns

- The brief's pasted Step 4 implementation contained `small_top_k must be greater than or equal to top_k`, but the Step 1 RRF test explicitly calls `small_top_k=2` and `top_k=10`. I implemented the behavior required by the tests and retrieval semantics: `small_top_k` is the per-variant candidate count and may be smaller than the final requested `top_k`; it only must be positive.
- The first non-elevated full pytest run failed during pytest temp directory cleanup with `PermissionError`, matching the known Windows sandbox issue in recent memory records. The elevated rerun passed.
