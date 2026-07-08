# Task 3 Report - CLI Integration for Hybrid Rerank Evaluation

Date: 2026-07-08
Status: DONE
Commits created: none

## Scope

Owned files for this task:

- `src/fireclaw_core/rag/rag_cli.py`
- `tests/test_rag_bm25_cli.py`

Also updated:

- `.superpowers/sdd/task-3-report.md`

No other production files were modified for Task 3.

## Requirements Implemented

Implemented the CLI integration described in `.superpowers/sdd/task-3-brief.md`:

- added `eval-hybrid-rerank-index`
- added lazy reranker provider factory wiring for:
  - `FakeRerankerProvider`
  - `BGEFlagRerankerProvider`
- loaded parent chunk texts with `load_parent_texts(...)`
- delegated evaluation to `evaluate_hybrid_retrievers_with_rerank(...)`
- preserved the existing JSON stdout style and optional `--output` file behavior used by the other eval commands
- added a fake CLI regression test that does not load or download a real reranker

Behavior intentionally preserved:

- no dense retrieval CLI behavior changes
- no BM25 CLI behavior changes
- no existing hybrid CLI behavior changes
- no real BGE reranker model existence check at CLI construction time
- no silent model download

## TDD Record

### RED

Test written first:

- `tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker`

Requested RED command:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_cli_red tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker -q
```

Initial sandbox run hit the known Windows pytest cleanup issue:

```text
PermissionError: [WinError 5] Access is denied: '...\.pytest_tmp_rerank_cli_red'
```

Rerunning in the established style produced the meaningful RED failure:

```text
argparse.ArgumentError: argument command: invalid choice: 'eval-hybrid-rerank-index'
```

This matched the brief's expected failure and confirmed the test was exercising a missing CLI command.

### GREEN

Implemented the minimal CLI changes in:

- `src/fireclaw_core/rag/rag_cli.py`

Requested GREEN command:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_cli_green tests/test_rag_bm25_cli.py tests/test_rag_rerank_eval.py tests/test_rag_reranking.py -q
```

Initial sandbox run again hit the same Windows pytest cleanup issue:

```text
PermissionError: [WinError 5] Access is denied: '...\.pytest_tmp_rerank_cli_green'
```

Rerunning in the established style verified GREEN:

```text
15 passed in 0.67s
```

## File-Level Change Summary

### `src/fireclaw_core/rag/rag_cli.py`

Added:

- imports for `evaluate_hybrid_retrievers_with_rerank`, `FakeRerankerProvider`, `BGEFlagRerankerProvider`, and `load_parent_texts`
- parser block for `eval-hybrid-rerank-index`
- dispatch to `_cmd_eval_hybrid_rerank_index(args)`
- `_cmd_eval_hybrid_rerank_index(...)`
- `_create_reranker_provider(...)`

Implementation details:

- defaults mirror the existing corpus/index conventions used by the other eval commands
- `bge-reranker` creates `BGEFlagRerankerProvider(Path(".cache/models/bge-reranker-v2-m3"))` when no explicit model path is provided
- the provider factory is lazy: it does not validate or download the model at CLI parse time
- output JSON is written through the same `payload = report.to_dict()` plus `_write_json_output(payload)` pattern used elsewhere in the file

### `tests/test_rag_bm25_cli.py`

Added one focused regression test that:

- builds fake dense and BM25 indexes locally
- uses `FakeRerankerProvider` through CLI arguments
- supplies explicit parent chunk texts
- verifies:
  - exit code `0`
  - output file creation
  - `retrieval_method == "hybrid_rerank"`
  - reranker provider metadata is `fake-reranker`
  - rerank config fields are serialized
  - the gold parent is ranked first after reranking
  - stdout JSON matches the written report style

## Brief/API Adjustment

No command-name or option adjustment was required. The implemented CLI uses the brief's command and option names as written.

## Verification Commands Run

```powershell
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_rerank_cli_red tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker -q
```

```powershell
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_rerank_cli_red tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker -q
```

```powershell
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_rerank_cli_green tests/test_rag_bm25_cli.py tests/test_rag_rerank_eval.py tests/test_rag_reranking.py -q
```

```powershell
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_rerank_cli_green tests/test_rag_bm25_cli.py tests/test_rag_rerank_eval.py tests/test_rag_reranking.py -q
```

```powershell
git diff -- src/fireclaw_core/rag/rag_cli.py tests/test_rag_bm25_cli.py
```

## Diff Check

Focused diff inspection confirmed the owned changes are limited to:

- the new hybrid rerank CLI parser and dispatch
- the reranker provider factory helper
- the hybrid rerank CLI command helper
- the new fake CLI regression test

No commits were created.

## Concerns

- The known Windows pytest `basetemp` `PermissionError` is still present in this environment and required rerunning the requested RED/GREEN commands in the established style to get trustworthy results.
- `BGEFlagRerankerProvider` remains intentionally lazy and local-path-only here; this task wires the CLI but does not verify that a real reranker checkpoint exists.

## Conclusion

Task 3 is implemented and left uncommitted in the working tree, with RED observed first and GREEN verified on the requested focused suite after handling the known Windows pytest environment issue explicitly.

## 2026-07-08 Review Fix Update

Status: DONE

Review findings addressed without production code changes.

### What changed

- Tightened `tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker`.
- Reworked the fixture so the hybrid candidate stage prefers `generic__parent` first, while the fake reranker using the default English rerank query promotes `gold__parent`.
- Removed explicit CLI flags for `--rerank-query-variant`, `--rerank-pool-size`, and `--top-k` in this test so the test now covers default serialization for:
  - `rerank_query_variant == "en"`
  - `rerank_pool_size == 50`
  - `final_top_k == 10`

### TDD record for the review fix

RED:

- First tightened only the assertions and omitted the explicit rerank/default flags.
- Focused pytest rerun produced the meaningful failure:

```text
assert 1 == 2
```

- The failure showed the existing fixture still had `gold__parent` at base rank 1, so the old test setup did not prove reranking changed the result.

GREEN:

- Adjusted only the CLI test fixture:
  - case query changed to `generic rescue`
  - `reviewed_query_en` kept the SCBA rehab intent for reranking
  - `term_query` changed to `generic firefighter health rescue`
  - BM25 query variants narrowed to `terms`
- This makes the hybrid base order prefer `generic__parent`, while reranking with the default English query promotes `gold__parent`.

### Verification

Requested focused suite:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_cli_fix_green tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker tests/test_rag_rerank_eval.py tests/test_rag_reranking.py -q
```

Observed results:

- sandbox run hit the known Windows `basetemp` cleanup `PermissionError`
- established-style rerun succeeded:

```text
12 passed in 0.36s
```

### Final assertion coverage added

- reranked top hit is `gold__parent`
- reranked top hit `base_rank == 2`
- second hit is `generic__parent`
- second hit `base_rank == 1`
- report default config serializes:
  - `rerank_query_variant == "en"`
  - `rerank_pool_size == 50`
  - `final_top_k == 10`
