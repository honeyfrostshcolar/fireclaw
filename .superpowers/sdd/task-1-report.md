# Task 1 Report - Reranker Metadata and Core Reranking Utilities

Date: 2026-07-08
Status: DONE
Commits created: none

## Scope

Owned files for this task:

- `src/fireclaw_core/rag/dense_eval.py`
- `src/fireclaw_core/rag/reranking.py`
- `tests/test_rag_reranking.py`

Also updated:

- `.superpowers/sdd/task-1-report.md`

No other production files were modified.

## Requirements Implemented

Implemented the Task 1 utility layer described in `.superpowers/sdd/task-1-brief.md`:

- added optional reranker metadata fields to `DenseEvalRetrievedHit`
- created `RerankerModelInfo`
- created `RerankerProvider` protocol
- created `FakeRerankerProvider`
- created `BGEFlagRerankerProvider`
- created `load_parent_texts(path: Path) -> dict[str, str]`
- created `select_rerank_query(query_variants, fallback_query, variant="en") -> str`
- created `rerank_parent_hits(...) -> list[DenseEvalRetrievedHit]`

Behavior intentionally preserved:

- no dense vector changes
- no BM25 scoring changes
- no hybrid fusion changes
- no query expansion row changes
- no chunking changes
- no gold label changes
- no silent model download

## TDD Record

### RED

Test file written first:

- `tests/test_rag_reranking.py`

RED command:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_reranking_red tests/test_rag_reranking.py -q
```

Observed failure:

```text
ModuleNotFoundError: No module named 'fireclaw_core.rag.reranking'
```

This matched the brief's expected RED condition.

### GREEN

Implemented minimal production code in:

- `src/fireclaw_core/rag/dense_eval.py`
- `src/fireclaw_core/rag/reranking.py`

Requested GREEN command from brief:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_reranking_green tests/test_rag_reranking.py tests/test_rag_dense_eval.py -q
```

Initial sandbox run hit the known Windows pytest cleanup problem:

```text
PermissionError: [WinError 5] Access is denied: '...\.pytest_tmp_reranking_green'
```

Retry with a different `basetemp` hit the same environment issue. A diagnostic no-cleanup run showed:

```text
17 passed, 5 errors
```

Those 5 errors were all `tmp_path` fixture setup failures caused by the same basetemp directory access problem, not assertion failures in reranking logic.

To obtain a meaningful verification result, I reran the same GREEN suite unsandboxed:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_reranking_green tests/test_rag_reranking.py tests/test_rag_dense_eval.py -q
```

Verified result:

```text
22 passed in 0.24s
```

## File-Level Change Summary

### `src/fireclaw_core/rag/dense_eval.py`

Added optional reranking metadata fields to `DenseEvalRetrievedHit`:

- `rerank_score`
- `base_rank`
- `base_score`
- `base_fusion_score`
- `reranker`

Updated `to_dict()` so empty/`None` reranker metadata is omitted, matching existing serialization style for optional hit metadata.

### `src/fireclaw_core/rag/reranking.py`

Added the new reranking utility module with:

- deterministic fake reranker for tests and offline utility work
- lazy-loading `FlagEmbedding.FlagReranker` wrapper that requires an existing local model path
- parent-chunk text loading with duplicate `parent_id` protection
- rerank-query selection that prefers English variant keys when available
- parent-hit reranking that:
  - uses parent text, not chunk preview
  - preserves original retrieval metadata
  - records rerank/base fields on returned hits
  - sorts by rerank score descending, then original rank, then parent id

### `tests/test_rag_reranking.py`

Added focused tests for:

- parent chunk JSONL loading
- duplicate `parent_id` rejection
- rerank query selection
- metadata-preserving parent reranking
- missing parent text rejection

## Verification Commands Run

```powershell
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_reranking_red tests/test_rag_reranking.py -q
```

```powershell
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_reranking_green tests/test_rag_reranking.py tests/test_rag_dense_eval.py -q
```

```powershell
$env:PYTHONPATH = '.deps;src'; $bt = Join-Path $env:TEMP 'pytest_tmp_reranking_green_20260708'; python -m pytest --basetemp=$bt tests/test_rag_reranking.py tests/test_rag_dense_eval.py -q
```

```powershell
$env:PYTHONPATH = '.deps;src'; python -c "import sys; import _pytest.pathlib as pathlib; pathlib.cleanup_dead_symlinks = lambda root: None; import pytest; sys.exit(pytest.main(['--basetemp=.tmp/pytest_tmp_reranking_green_nocleanup','tests/test_rag_reranking.py','tests/test_rag_dense_eval.py','-q']))"
```

```powershell
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_reranking_green tests/test_rag_reranking.py tests/test_rag_dense_eval.py -q
```

```powershell
git diff -- src/fireclaw_core/rag/dense_eval.py src/fireclaw_core/rag/reranking.py tests/test_rag_reranking.py
```

## Diff Check

Focused diff inspection confirmed the owned changes are limited to:

- reranker metadata in `DenseEvalRetrievedHit`
- new reranking utility module
- new focused reranking tests

No commits were created.

## Concerns

- The known Windows pytest `basetemp` `PermissionError` is still present in this environment and required an unsandboxed rerun for trustworthy GREEN verification.
- `BGEFlagRerankerProvider` is intentionally lazy and local-path-only; Task 1 does not verify a real reranker model is present, and does not download one.

## Conclusion

Task 1 is implemented and left uncommitted in the working tree, with RED observed first and GREEN verified on the requested suite after handling the known Windows pytest environment issue explicitly.

---

## 2026-07-08 Review Fix Update

Status: DONE
Commits created: none

### Review Findings Addressed

1. `select_rerank_query()` no longer special-cases `variant == "zh"` to return `fallback_query` early.
   It now uses the same preferred-key order as other variants:
   - `dense:{variant}`
   - `bm25:{variant}`
   - `{variant}`
   - then `fallback_query`

2. `load_parent_texts()` no longer silently coerces invalid text rows to `""`.
   It now raises:
   - `ValueError("missing text in parent chunks for parent_id: ...")` when the `text` key is absent
   - `ValueError("empty text in parent chunks for parent_id: ...")` when the text is blank after stripping

3. Added a regression test confirming a normal non-reranked `DenseEvalRetrievedHit.to_dict()` still omits unset rerank metadata keys.

### TDD Record

RED test additions were written first in `tests/test_rag_reranking.py`:

- zh variant preference when `dense:zh` / `bm25:zh` / `zh` are present
- missing `text` rejection in `load_parent_texts()`
- empty `text` rejection in `load_parent_texts()`
- unset rerank metadata omission in `DenseEvalRetrievedHit.to_dict()`

Observed RED after adding tests:

- `test_select_rerank_query_prefers_requested_zh_variant_when_present`
- `test_load_parent_texts_rejects_missing_text`
- `test_load_parent_texts_rejects_empty_text`

These failures matched the review findings.

### Verification Commands Run

Requested command:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_reranking_fix_green tests/test_rag_reranking.py tests/test_rag_dense_eval.py -q
```

Observed sandbox issue again:

```text
PermissionError: [WinError 5] Access is denied: '.pytest_tmp_reranking_fix_green'
```

Diagnostic no-cleanup rerun:

```powershell
$env:PYTHONPATH = ".deps;src"
python -c "import sys; import _pytest.pathlib as pathlib; pathlib.cleanup_dead_symlinks = lambda root: None; import pytest; sys.exit(pytest.main(['--basetemp=.tmp/pytest_tmp_reranking_fix_green_nocleanup','tests/test_rag_reranking.py','tests/test_rag_dense_eval.py','-q']))"
```

Observed result:

```text
23 passed, 3 errors
```

Those 3 errors were the known `tmp_path` setup failures from `tests/test_rag_dense_eval.py`, not reranking assertion failures.

Unsandboxed rerun of the exact requested suite:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_reranking_fix_green tests/test_rag_reranking.py tests/test_rag_dense_eval.py -q
```

Verified GREEN result:

```text
26 passed in 0.23s
```

### Files Modified For This Review Fix

- `src/fireclaw_core/rag/reranking.py`
- `tests/test_rag_reranking.py`
- `.superpowers/sdd/task-1-report.md`

No commit was created.
