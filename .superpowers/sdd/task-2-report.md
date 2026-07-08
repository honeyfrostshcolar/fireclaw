# Task 2 Report - CLI Integration

## Summary

Implemented `eval-dense-index` in `src/fireclaw_core/rag/rag_cli.py` and added a CLI regression test in `tests/test_rag_dense_cli.py`.

## RED Evidence

### Command

```powershell
$env:PYTHONPATH = ".deps;src"; $bt = Join-Path $env:TEMP 'pytest_tmp_dense_eval_cli_red'; python -m pytest --basetemp=$bt tests/test_rag_dense_cli.py::test_cli_eval_dense_index_writes_report_with_fake_provider -q
```

### Result

Initial sandboxed run hit the known Windows pytest `basetemp` cleanup permission issue, so I reran with elevated permissions to capture the real failure:

```text
E           argparse.ArgumentError: argument command: invalid choice: 'eval-dense-index'
...
SystemExit: 2
```

This confirmed the test was failing for the expected reason: the CLI command did not exist yet.

## GREEN Evidence

### Command

```powershell
$env:PYTHONPATH = ".deps;src"; $bt = Join-Path $env:TEMP 'pytest_tmp_dense_eval_cli_green'; python -m pytest --basetemp=$bt tests/test_rag_dense_cli.py::test_cli_eval_dense_index_writes_report_with_fake_provider -q
```

### Result

```text
1 passed in 0.29s
```

## Full Verification

### Command

```powershell
$env:PYTHONPATH = ".deps;src"; $bt = Join-Path $env:TEMP 'pytest_tmp_dense_eval_cli_final'; python -m pytest --basetemp=$bt tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py -q
```

### Result

```text
13 passed in 0.29s
```

## File Changes

- `src/fireclaw_core/rag/rag_cli.py`
  - added `eval-dense-index` parser
  - added `_cmd_eval_dense_index`
  - wired dispatch and dense eval imports
- `tests/test_rag_dense_cli.py`
  - added CLI regression test for `eval-dense-index`
  - used `--top-k 10` to satisfy the dense eval contract
- `.superpowers/sdd/task-2-report.md`
  - this report

## Self-Check

- The new CLI test fails before the implementation and passes after it.
- The implementation is thin and delegates evaluation to `evaluate_dense_retriever` / `load_dense_eval_cases`.
- The test uses `--top-k 10`, matching the dense evaluation requirement.
- The Windows pytest `basetemp` permission issue was observed and worked around by rerunning with a temp-directory basetemp path and elevated permissions.

---

## 2026-07-08 Task 2 - Hybrid Rerank Evaluation Wrapper

### Summary

Implemented `evaluate_hybrid_retrievers_with_rerank(...)` in `src/fireclaw_core/rag/rerank_eval.py` and added a focused TDD regression in `tests/test_rag_rerank_eval.py`.

This wrapper preserves existing dense/BM25/hybrid candidate generation, increases the candidate pool via the existing hybrid parent evaluation path, reranks parent hits with the approved Task 1 API, and recomputes metrics through the existing `DenseEvalReport` machinery.

### RED Evidence

#### Command

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_eval_red tests/test_rag_rerank_eval.py -q
```

#### Result

```text
ModuleNotFoundError: No module named 'fireclaw_core.rag.rerank_eval'
```

This matched the brief's expected failure and confirmed the test was exercising a missing wrapper rather than an existing behavior.

### GREEN Evidence

#### Command

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_eval_green tests/test_rag_rerank_eval.py tests/test_rag_reranking.py tests/test_rag_hybrid_eval.py -q
```

#### Result

```text
12 passed in 0.19s
```

No Windows `basetemp` `PermissionError` occurred on this focused rerank suite, so no rerun was needed here.

### File Changes

- `src/fireclaw_core/rag/rerank_eval.py`
  - added `evaluate_hybrid_retrievers_with_rerank(...)`
  - delegates candidate generation to `evaluate_hybrid_retrievers_with_expansion(...)`
  - selects rerank query with `select_rerank_query(...)`
  - reranks parent hits with `rerank_parent_hits(...)`
  - recomputes metrics through `evaluate_ranked_hits(...)`
  - emits `DenseEvalReport`-compatible `retrieval_config`
- `tests/test_rag_rerank_eval.py`
  - added regression test proving reranking can promote the more relevant parent while preserving base-rank metadata and rerank query metadata

### Brief/API Adjustment

The brief's sample implementation imported `DenseEvalRetrievedHit` and used the approved Task 1 helper APIs directly. That remains compatible with the current working-tree Task 1 implementation, so no API redesign was needed.

### Self-Check

- The new wrapper test was written before the module existed and failed for the expected reason.
- Existing `tests/test_rag_reranking.py` and `tests/test_rag_hybrid_eval.py` remained green after the wrapper was added.
- No dense vectors, BM25 scoring, query expansion rows, chunking, or gold labels were changed.
- The wrapper is evaluation-only and does not load a real reranker model in tests.
