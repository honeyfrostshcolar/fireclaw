# Query Expansion Task 4 Report

## What I Implemented

- Added optional `eval-dense-index` CLI flags in `src/fireclaw_core/rag/rag_cli.py`:
  - `--query-expansions`
  - `--query-variants`
  - `--ranking-view`
  - `--small-top-k`
  - `--parent-aggregation`
  - `--fusion`
  - `--rrf-k`
  - `--require-reviewed-expansions`
- Added `_parse_csv_arg` for comma-separated CLI values.
- Preserved default `eval-dense-index` behavior when no new optional flags are passed by continuing to call `evaluate_dense_retriever`.
- Added expanded evaluation dispatch only when expansion/parent/fusion/review flags are used.
- Loaded cached query expansion JSONL via `load_query_expansions`; no network or LLM call path was added.
- Added the requested CLI regression test in `tests/test_rag_dense_cli.py`.
- Created candidate artifact files:
  - `data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl`
  - `data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl`

## TDD Evidence

### RED Command

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_cli_expanded_red tests/test_rag_dense_cli.py::test_cli_eval_dense_index_with_expansion_and_parent_view_writes_config -q
```

The first sandboxed run hit the known Windows pytest temp cleanup permission issue:

```text
PermissionError: [WinError 5] Access denied: 'C:\\Users\\L\\AppData\\Local\\Temp\\pytest_tmp_dense_cli_expanded_red'
```

I reran the same RED command outside the sandbox after approval. Expected failure:

```text
FAILED tests/test_rag_dense_cli.py::test_cli_eval_dense_index_with_expansion_and_parent_view_writes_config
E       SystemExit: 2
Captured stderr:
__main__.py: error: unrecognized arguments: --query-expansions ... --query-variants zh,en,terms --ranking-view parent --small-top-k 10
1 failed in 0.46s
```

This is the expected RED because the CLI did not yet recognize the new flags.

### GREEN Command

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_cli_expanded_green tests/test_rag_dense_cli.py -q
```

The first sandboxed GREEN run again hit the same pytest temp cleanup permission issue. I reran the same command outside the sandbox after approval.

GREEN output:

```text
....                                                                     [100%]
4 passed in 0.52s
```

## Files Changed

- Modified: `src/fireclaw_core/rag/rag_cli.py`
- Modified: `tests/test_rag_dense_cli.py`
- Created: `data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl`
- Created: `data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl`
- Created: `.superpowers/sdd/query-expansion-task-4-report.md`

## Self-Review Findings

- Default `eval-dense-index` still uses the original `evaluate_dense_retriever` path when no new optional flags are passed.
- Expanded evaluation uses `evaluate_dense_retriever_with_expansion` only when the new optional behavior is requested.
- `--query-variants` parses to `["zh", "en", "terms"]` for the test case.
- Multi-variant expanded evaluation reports `fusion` as `rrf`, matching the existing `dense_eval.py` behavior.
- Candidate query expansion artifact has 10 JSONL rows and all rows have `status: "candidate"`.
- Candidate glossary artifact has 6 JSONL rows and all rows have `status: "candidate"`.
- No commits were created.

## Concerns

- Pytest commands using `$env:TEMP\...` hit a Windows temp directory permission issue inside the sandbox, so RED/GREEN evidence required approved reruns outside the sandbox.
- The two JSONL artifact files exist at the requested paths, but the current repository ignore rules mark them as ignored (`!!`), so ordinary `git diff` does not show their content. I separately validated their row counts and statuses.
