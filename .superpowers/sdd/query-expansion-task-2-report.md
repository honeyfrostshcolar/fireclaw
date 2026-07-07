# Query Expansion Task 2 Report

## What I Implemented

- Created `src/fireclaw_core/rag/dense_ranking.py`.
- Added `aggregate_hits_by_parent(...)` with `method="max"`:
  - groups dense hits by `parent_id`;
  - keeps the best-scoring child hit per parent, breaking ties by earlier rank;
  - records `child_hit_count` and `child_ranks`;
  - re-sorts parent representatives by score and resets ranks from 1.
- Added `reciprocal_rank_fuse(...)`:
  - supports `identity="chunk"` and `identity="parent"`;
  - computes RRF scores from per-variant ranks;
  - stores `fusion_score`, `variant_ranks`, and `variant_scores`;
  - skips duplicate identities within the same variant list.
- Extended `DenseEvalRetrievedHit` in `src/fireclaw_core/rag/dense_eval.py` with optional metadata:
  - `fusion_score`
  - `variant_ranks`
  - `variant_scores`
  - `child_hit_count`
  - `child_ranks`
- Preserved baseline serialization compatibility by omitting empty optional metadata from `DenseEvalRetrievedHit.to_dict()`.
- Added focused tests in:
  - `tests/test_rag_dense_ranking.py`
  - `tests/test_rag_dense_eval.py`

## TDD Evidence

### RED

Command:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_ranking_red tests/test_rag_dense_ranking.py -q
```

Output:

```text
=================================== ERRORS ====================================
______________ ERROR collecting tests/test_rag_dense_ranking.py _______________
ImportError while importing test module 'C:\Users\L\Desktop\lpp\fireclaw-master\tests\test_rag_dense_ranking.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
E:\anaconda3\Lib\importlib\__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
tests\test_rag_dense_ranking.py:6: in <module>
    from fireclaw_core.rag.dense_ranking import aggregate_hits_by_parent
E   ModuleNotFoundError: No module named 'fireclaw_core.rag.dense_ranking'
=========================== short test summary info ===========================
ERROR tests/test_rag_dense_ranking.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.28s
```

Why expected:

- The test imported `fireclaw_core.rag.dense_ranking` before that production module existed.
- This matches the brief's expected RED failure mode.

### GREEN

Command:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_ranking_green tests/test_rag_dense_ranking.py tests/test_rag_dense_eval.py::test_dense_eval_retrieved_hit_omits_empty_optional_metadata -q
```

Output:

```text
.....                                                                    [100%]
5 passed in 0.25s
```

Additional verification:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_ranking_full_elevated tests/test_rag_dense_ranking.py tests/test_rag_dense_eval.py -q
```

Output:

```text
................                                                         [100%]
16 passed in 0.28s
```

Note:

- The additional full relevant test command needed to run outside the sandbox because sandboxed pytest runs that used `tmp_path` failed during pytest `basetemp` directory access/cleanup with `PermissionError: [WinError 5]`.
- The task-specified GREEN command did pass inside the normal sandbox.

## Files Changed

- Created `src/fireclaw_core/rag/dense_ranking.py`.
- Created `tests/test_rag_dense_ranking.py`.
- Modified `src/fireclaw_core/rag/dense_eval.py`.
- Modified `tests/test_rag_dense_eval.py`.
- Created this report: `.superpowers/sdd/query-expansion-task-2-report.md`.

## Self-Review Findings

- Scope stayed within the task brief:
  - no CLI changes;
  - no data JSONL changes;
  - no docs changes beyond this required report;
  - no memory writes;
  - no changes to `query_expansion.py` or its tests.
- `DenseEvalRetrievedHit.to_dict()` keeps default baseline payloads compatible by dropping only empty optional Task 2 metadata.
- `aggregate_hits_by_parent` and `reciprocal_rank_fuse` use `dataclasses.replace`, preserving existing hit metadata while adding ranking metadata.
- Validation guards match the brief's expected error messages for unsupported aggregation method and unsupported fusion identity.
- The brief's `git diff -- ...` checkpoint produced no diff output because the relevant files are untracked in this workspace; `git status --short -- ...` showed:

```text
?? src/fireclaw_core/rag/dense_eval.py
?? src/fireclaw_core/rag/dense_ranking.py
?? tests/test_rag_dense_eval.py
?? tests/test_rag_dense_ranking.py
```

## Concerns

- No functional implementation concerns found.
- Environment concern: full `tests/test_rag_dense_eval.py` verification with `tmp_path` hit sandbox-related Windows `PermissionError` on pytest `basetemp`; rerunning the same relevant test set outside the sandbox passed.
- Checkpoint concern: ordinary `git diff -- <paths>` does not display the changed content because these task files are currently untracked in the repository.
