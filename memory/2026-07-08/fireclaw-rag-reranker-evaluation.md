# FireClaw RAG Hybrid Reranker Evaluation

**Date:** 2026-07-08
**Status:** Tasks 1-4 implemented and reviewed; real reranker model check/report pending.

## Task Goal

Add a first reranker evaluation layer after hybrid Dense+BM25 parent retrieval.

The intended first version is an evaluation-only ablation:

```text
hybrid parent candidates top 50
-> pretrained cross-encoder reranker scores query + parent_text
-> reranked top 10
-> compare Hit@1 / Hit@5 / Hit@10 / MRR@10 / gold_recall@10
```

## User Decisions

- Keep the current BM25/hybrid candidate preservation issue as a known limitation for now.
- Start reranking next.
- Use a pretrained reranker first; do not train or fine-tune on the current 30 cases.
- The user asked to write the implementation plan before code changes.

## Current Context

Existing v2 reports from the expanded 30-case eval:

```text
data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v2_expanded_parent_report.json
data/rag/fire_rescue/eval/runs/bm25_small_v2_expanded_parent_report.json
data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v2_expanded_parent_report.json
```

Existing v2 metrics:

```text
dense  cases=30 hit_at_1=0.266667 hit_at_5=0.8      hit_at_10=0.9      mrr_at_10=0.477037 gold_recall_at_10=0.9
bm25   cases=30 hit_at_1=0.366667 hit_at_5=0.766667 hit_at_10=0.966667 mrr_at_10=0.551005 gold_recall_at_10=0.966667
hybrid cases=30 hit_at_1=0.366667 hit_at_5=0.766667 hit_at_10=0.966667 mrr_at_10=0.55504  gold_recall_at_10=0.966667
```

Relevant current implementation:

```text
src/fireclaw_core/rag/hybrid_eval.py
src/fireclaw_core/rag/dense_eval.py
src/fireclaw_core/rag/dense_ranking.py
src/fireclaw_core/rag/rag_cli.py
src/fireclaw_core/rag/bge_m3_provider.py
```

Local environment notes:

```text
.cache/models/bge-m3 exists
.cache/models/bge-reranker-v2-m3 has not been confirmed as present
.venv-bge-m3 contains FlagEmbedding with FlagReranker
```

## Plan

Written to:

```text
docs/superpowers/plans/2026-07-08-fireclaw-hybrid-reranker-evaluation.md
```

Plan structure:

```text
Task 1: reranker metadata and core reranking utilities
Task 2: hybrid rerank evaluation wrapper
Task 3: CLI integration for eval-hybrid-rerank-index
Task 4: documentation and focused verification
Task 5: real v2 hybrid rerank report if local model exists
```

## Commands Already Run

Checked git status:

```powershell
git status -sb
```

Observed existing untracked v2 report and memory files. No retrieval code changes were made while writing the plan.

Checked local `FlagEmbedding` reranker support:

```powershell
.\.venv-bge-m3\Scripts\python.exe -c "import FlagEmbedding; print(FlagEmbedding.__file__); print([name for name in dir(FlagEmbedding) if 'Rerank' in name or 'Reranker' in name])"
```

Observed:

```text
['AbsReranker', 'FlagAutoReranker', 'FlagLLMReranker', 'FlagReranker', 'LayerWiseFlagLLMReranker', 'LightWeightFlagLLMReranker', 'RerankerModelClass']
```

Checked `FlagReranker` signature:

```powershell
.\.venv-bge-m3\Scripts\python.exe -c "from FlagEmbedding import FlagReranker; import inspect; print(inspect.signature(FlagReranker)); print(inspect.signature(FlagReranker.compute_score))"
```

Observed that `FlagReranker` supports `model_name_or_path`, `devices`, `batch_size`, `max_length`, `cache_dir`, and `compute_score(sentence_pairs, **kwargs)`.

Plan self-review:

```powershell
rg -n "TBD|TODO|implement later|fill in details|Similar to|appropriate error|Write tests for the above|Record .* here|\.\.\." docs/superpowers/plans/2026-07-08-fireclaw-hybrid-reranker-evaluation.md
git diff --check -- docs/superpowers/plans/2026-07-08-fireclaw-hybrid-reranker-evaluation.md
```

Result:

```text
git diff --check exited 0
remaining rg hits are function-signature shorthand such as `(...)`, not incomplete implementation placeholders
```

## Next Recommended Step

Ask the user to choose execution mode:

```text
1. Subagent-Driven
2. Inline Execution
```

Subagent-Driven is recommended because the plan has separable tasks with clean review checkpoints.

## 2026-07-08 02:31 Task 1 Execution Update

### Task Goal

Implement Task 1: reranker metadata and core reranking utilities for FireClaw RAG.

### Commands Already Executed

```powershell
Get-Content AGENTS.md
Get-Content .superpowers/sdd/task-1-brief.md
Get-ChildItem memory -Directory | Sort-Object Name -Descending | Select-Object -First 2 -ExpandProperty Name
git status --short
Get-Content src/fireclaw_core/rag/dense_eval.py
Get-Content tests/test_rag_dense_eval.py
rg -n "DenseEvalRetrievedHit|load_jsonl|query_variants|variant_ranks|variant_scores" src/fireclaw_core/rag tests
```

TDD verification commands:

```powershell
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_reranking_red tests/test_rag_reranking.py -q
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_reranking_green tests/test_rag_reranking.py tests/test_rag_dense_eval.py -q
$env:PYTHONPATH = '.deps;src'; $bt = Join-Path $env:TEMP 'pytest_tmp_reranking_green_20260708'; python -m pytest --basetemp=$bt tests/test_rag_reranking.py tests/test_rag_dense_eval.py -q
$env:PYTHONPATH = '.deps;src'; python -c "import sys; import _pytest.pathlib as pathlib; pathlib.cleanup_dead_symlinks = lambda root: None; import pytest; sys.exit(pytest.main(['--basetemp=.tmp/pytest_tmp_reranking_green_nocleanup','tests/test_rag_reranking.py','tests/test_rag_dense_eval.py','-q']))"
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_reranking_green tests/test_rag_reranking.py tests/test_rag_dense_eval.py -q
git diff -- src/fireclaw_core/rag/dense_eval.py src/fireclaw_core/rag/reranking.py tests/test_rag_reranking.py
```

### Observed Test Results

- RED matched brief exactly:

```text
ModuleNotFoundError: No module named 'fireclaw_core.rag.reranking'
```

- Sandbox GREEN attempt failed at known Windows pytest cleanup:

```text
PermissionError: [WinError 5] Access is denied
```

- Alternate `basetemp` retry hit the same issue.
- Diagnostic no-cleanup run showed:

```text
17 passed, 5 errors
```

- All 5 errors were `tmp_path` fixture setup failures caused by basetemp directory access, not behavior assertions.
- Unsandboxed rerun of the same GREEN suite succeeded:

```text
22 passed in 0.24s
```

### Files Inspected

- `src/fireclaw_core/rag/dense_eval.py`
- `tests/test_rag_dense_eval.py`
- `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`
- `.superpowers/sdd/task-1-brief.md`

### Files Modified

- `src/fireclaw_core/rag/dense_eval.py`
- `src/fireclaw_core/rag/reranking.py`
- `tests/test_rag_reranking.py`
- `.superpowers/sdd/task-1-report.md`

### Current Hypothesis

Task 1 only needs a thin utility layer: optional rerank metadata on retrieved hits, deterministic fake reranking for tests, parent-text loading, query selection, and lazy local-path FlagEmbedding wrapper. No retrieval-stack behavior changes are required.

### Current Conclusion

Task 1 is complete. The implementation preserves existing dense/BM25/hybrid behavior and adds only the requested reranker metadata/utilities.

### Important Parameters / Paths

- `top_k` default for reranking: `10`
- `max_passage_chars` default: `6000`
- fake reranker provider id: `fake-reranker`
- BGE backend string: `FlagEmbedding.FlagReranker`
- report path: `.superpowers/sdd/task-1-report.md`

### Failed Attempts and Why Rejected

- Using workspace `--basetemp=.pytest_tmp_reranking_green`: blocked by Windows `PermissionError` during pytest cleanup.
- Using temp-dir `--basetemp=$env:TEMP\pytest_tmp_reranking_green_20260708`: same permission problem.
- Disabling only `cleanup_dead_symlinks`: still left `tmp_path` fixture setup failures because the environment could not list the basetemp directory reliably.

### User Preferences / Decisions

- follow TDD strictly
- do not commit
- do not modify dense vectors, BM25 scoring, query expansion rows, chunking, or gold labels
- do not silently download any model
- keep code/data keys in English
- report Windows pytest basetemp `PermissionError` clearly rather than hiding it

### Next Recommended Step

Proceed to Task 2: build the hybrid rerank evaluation wrapper on top of these utilities, reusing `select_rerank_query`, `load_parent_texts`, and `rerank_parent_hits`.

## 2026-07-08 03:02 Task 2 Execution Update

### Task Goal

Implement Task 2: hybrid rerank evaluation wrapper for FireClaw RAG.

### Commands Already Executed

```powershell
Get-Content AGENTS.md
Get-Content .superpowers/sdd/task-2-brief.md
Get-Content memory/2026-07-08/fireclaw-rag-reranker-evaluation.md
Get-Content memory/2026-07-08/fireclaw-rag-eval-v2-expansion.md
Get-Content memory/2026-07-07/fireclaw-bm25-hybrid-retrieval.md
Get-Content src/fireclaw_core/rag/hybrid_eval.py
Get-Content src/fireclaw_core/rag/dense_eval.py
Get-Content src/fireclaw_core/rag/reranking.py
Get-Content tests/test_rag_hybrid_eval.py
Get-Content tests/test_rag_reranking.py
rg -n "DenseEvalRetrievedHit\\(|rerank_score|base_rank|reranker" tests src/fireclaw_core/rag
```

TDD commands:

```powershell
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_rerank_eval_red tests/test_rag_rerank_eval.py -q
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_rerank_eval_green tests/test_rag_rerank_eval.py tests/test_rag_reranking.py tests/test_rag_hybrid_eval.py -q
```

### Observed Test Results

- RED matched the brief exactly:

```text
ModuleNotFoundError: No module named 'fireclaw_core.rag.rerank_eval'
```

- GREEN passed cleanly without the Windows `basetemp` permission problem:

```text
12 passed in 0.19s
```

### Files Inspected

- `src/fireclaw_core/rag/hybrid_eval.py`
- `src/fireclaw_core/rag/dense_eval.py`
- `src/fireclaw_core/rag/reranking.py`
- `tests/test_rag_hybrid_eval.py`
- `tests/test_rag_reranking.py`
- `.superpowers/sdd/task-2-brief.md`

### Files Modified

- `src/fireclaw_core/rag/rerank_eval.py`
- `tests/test_rag_rerank_eval.py`
- `.superpowers/sdd/task-2-report.md`

### Current Hypothesis

Task 2 only needs a thin wrapper around the already-approved hybrid parent evaluation and Task 1 reranking utilities. Reusing `evaluate_ranked_hits(...)` is enough to keep report schema and metrics aligned with `DenseEvalReport`.

### Current Conclusion

Task 2 is complete. The wrapper preserves candidate retrieval behavior, adds reranking only after hybrid parent candidate generation, and reuses the existing evaluation report format.

### Important Parameters / Paths

- `rerank_pool_size`: `10` in the focused regression
- `top_k`: `10`
- rerank query metadata key: `rerank:en`
- report path: `.superpowers/sdd/task-2-report.md`

### Failed Attempts and Why Rejected

- None beyond the expected RED failure for the missing module.

### User Preferences / Decisions

- follow TDD strictly
- do not commit
- keep `DenseEvalReport` compatibility
- do not change dense vectors, BM25 scoring, query expansion rows, chunking, or gold labels
- use the actual approved Task 1 API if the brief sample drifts slightly

### Next Recommended Step

Proceed to Task 3 CLI integration for hybrid rerank evaluation, reusing this wrapper and the existing Task 1 provider loading path.

## 2026-07-08 03:31 Task 3 Execution Update

### Task Goal

Implement Task 3: CLI integration for hybrid rerank evaluation in FireClaw RAG.

### Commands Already Executed

```powershell
Get-Content AGENTS.md
Get-Content .superpowers/sdd/task-3-brief.md
Get-Content memory/2026-07-08/fireclaw-rag-reranker-evaluation.md
Get-Content memory/2026-07-08/fireclaw-rag-eval-v2-expansion.md
Get-Content memory/2026-07-07/fireclaw-bm25-hybrid-retrieval.md
Get-Content src/fireclaw_core/rag/rag_cli.py
Get-Content tests/test_rag_bm25_cli.py
Get-Content src/fireclaw_core/rag/rerank_eval.py
Get-Content src/fireclaw_core/rag/reranking.py
git status --short
```

TDD and verification commands:

```powershell
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_rerank_cli_red tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker -q
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_rerank_cli_red tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker -q
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_rerank_cli_green tests/test_rag_bm25_cli.py tests/test_rag_rerank_eval.py tests/test_rag_reranking.py -q
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_rerank_cli_green tests/test_rag_bm25_cli.py tests/test_rag_rerank_eval.py tests/test_rag_reranking.py -q
git diff -- src/fireclaw_core/rag/rag_cli.py tests/test_rag_bm25_cli.py
```

### Observed Test Results

- First RED run hit the known Windows pytest cleanup problem:

```text
PermissionError: [WinError 5] Access is denied: '...\\.pytest_tmp_rerank_cli_red'
```

- Established-style rerun produced the expected RED failure:

```text
argparse.ArgumentError: argument command: invalid choice: 'eval-hybrid-rerank-index'
```

- First GREEN run hit the same Windows pytest cleanup problem:

```text
PermissionError: [WinError 5] Access is denied: '...\\.pytest_tmp_rerank_cli_green'
```

- Established-style rerun of the requested GREEN suite succeeded:

```text
15 passed in 0.67s
```

### Files Inspected

- `src/fireclaw_core/rag/rag_cli.py`
- `tests/test_rag_bm25_cli.py`
- `src/fireclaw_core/rag/rerank_eval.py`
- `src/fireclaw_core/rag/reranking.py`
- `.superpowers/sdd/task-3-brief.md`
- `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`
- `memory/2026-07-08/fireclaw-rag-eval-v2-expansion.md`
- `memory/2026-07-07/fireclaw-bm25-hybrid-retrieval.md`

### Files Modified

- `src/fireclaw_core/rag/rag_cli.py`
- `tests/test_rag_bm25_cli.py`
- `.superpowers/sdd/task-3-report.md`

### Current Hypothesis

Task 3 only needs thin CLI wiring: one new parser, one dispatch branch, one command helper, and one reranker-provider factory that stays lazy and local-path-only. Reusing the existing report serialization path should preserve JSON/stdout behavior automatically.

### Current Conclusion

Task 3 is complete. The new CLI command integrates the already-approved Task 1/2 APIs, preserves existing dense/BM25/hybrid CLI behavior, and uses `FakeRerankerProvider` for deterministic offline testing.

### Important Parameters / Paths

- command: `eval-hybrid-rerank-index`
- default reranker model path: `.cache/models/bge-reranker-v2-m3`
- default reranker batch size: `32`
- default reranker max length: `512`
- focused report path: `.superpowers/sdd/task-3-report.md`

### Failed Attempts and Why Rejected

- Sandboxed RED run with workspace `--basetemp=.pytest_tmp_rerank_cli_red`: blocked by the known Windows `PermissionError` during pytest cleanup, so it did not provide trustworthy RED evidence.
- Sandboxed GREEN run with workspace `--basetemp=.pytest_tmp_rerank_cli_green`: blocked by the same pytest cleanup issue, so it did not provide trustworthy GREEN evidence.

### User Preferences / Decisions

- follow TDD strictly
- do not commit
- do not load or download a real reranker in tests
- for the real BGE reranker factory, create only a lazy `BGEFlagRerankerProvider` with local path
- preserve existing dense, BM25, and hybrid CLI behavior
- preserve JSON output style and output file behavior used by the other eval commands

### Next Recommended Step

Proceed to Task 4 documentation/final verification or to a real local rerank ablation only if the user asks for it and the local reranker checkpoint is available.

## 2026-07-08 10:55 Task 3 Review-Fix Update

### Task Goal

Fix the Task 3 review findings for hybrid rerank CLI integration by strengthening the CLI regression test so it proves reranking actually changes the result ordering.

### Current Progress

- 当前进展：
  - review fix completed
- 已完成：
  - tightened `tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker`
  - verified default CLI rerank config serialization
  - confirmed no `rag_cli.py` production fix was needed
- 当前问题：
  - managed Windows sandbox still hits the known pytest `basetemp` cleanup `PermissionError`
- 下一步：
  - keep this stronger fixture as the regression guard for future CLI rerank changes
- 需要运行的命令：
  - `$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=.pytest_tmp_rerank_cli_fix_green tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker tests/test_rag_rerank_eval.py tests/test_rag_reranking.py -q`

### Commands Already Executed

```powershell
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_rerank_cli_fix_green tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker -q
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_rerank_cli_fix_green tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker -q
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_rerank_cli_fix_green tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker tests/test_rag_rerank_eval.py tests/test_rag_reranking.py -q
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_rerank_cli_fix_green tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker tests/test_rag_rerank_eval.py tests/test_rag_reranking.py -q
```

### Observed Results

- Tightened-assertion RED was meaningful after the established-style rerun:

```text
assert 1 == 2
```

- This proved the old fixture still had `gold__parent` at base rank 1, so the previous test did not demonstrate reranking changed the ranking.
- Sandbox GREEN runs hit the known Windows cleanup error:

```text
PermissionError: [WinError 5] Access is denied: '.pytest_tmp_rerank_cli_fix_green'
```

- Established-style reruns succeeded:

```text
1 passed in 0.29s
12 passed in 0.36s
```

### Files Modified

- `tests/test_rag_bm25_cli.py`
- `.superpowers/sdd/task-3-report.md`
- `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`

### Current Hypothesis

The review issue was entirely in the CLI test fixture. The existing CLI wiring and rerank evaluation code already behaved correctly once the fixture made hybrid base retrieval prefer the generic parent and let reranking use the English reviewed query to promote the gold parent.

### Current Conclusion

No production code change is needed for this review fix. The strengthened test now proves:

- base hybrid ordering can put the non-gold parent first
- reranking promotes the gold parent to rank 1
- CLI defaults serialize as expected for rerank query variant, rerank pool size, and final top-k

### Important Parameters / Paths

- dense query variants in the stronger fixture: `zh,en`
- BM25 query variants in the stronger fixture: `terms`
- default rerank query variant: `en`
- default rerank pool size: `50`
- default final top-k: `10`

### Failed Attempts and Why Rejected

- Keeping the original fixture with only stronger assertions failed because `gold__parent` already had `base_rank == 1`; this was rejected as too weak to prove reranking changed anything.

### User Preferences / Decisions

- prefer modifying only `tests/test_rag_bm25_cli.py`
- modify `src/fireclaw_core/rag/rag_cli.py` only if the improved test reveals a real CLI bug
- append the fix report to `.superpowers/sdd/task-3-report.md`
- do not commit

## 2026-07-08 11:18 Task 4 Documentation and Verification Update

### Task Goal

Complete Task 4 by appending the reranker walkthrough section, running the focused reranker verification suite, recording the exact results, and checking `git diff --check`.

### Current Progress

- 当前进展：
  - Task 4 docs append and focused verification completed
- 已完成：
  - appended the required `Optional Evaluation: Hybrid Reranking` section to `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
  - ran the requested focused pytest suite
  - ran `git diff --check`
  - wrote the Task 4 handoff report
- 当前问题：
  - managed Windows sandbox still hits the known pytest `basetemp` `PermissionError`
- 下一步：
  - optional real reranker ablation if a local reranker checkpoint is available
- 需要运行的命令：
  - `$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=.pytest_tmp_rerank_all tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py tests/test_rag_hybrid_eval.py tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py -q`
  - `git diff --check`

### Commands

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_all tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py tests/test_rag_hybrid_eval.py tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py -q
git diff --check
```

### Results

- 2026-07-08 11:18 sandbox pytest result: `PermissionError: [WinError 5] Access is denied: 'C:\\Users\\L\\Desktop\\lpp\\fireclaw-master\\.pytest_tmp_rerank_all'`
- 2026-07-08 11:21 established-style rerun result: `42 passed in 0.67s`
- 2026-07-08 11:22 first `git diff --check` result: non-zero due to `new blank line at EOF` findings in `.superpowers/sdd/task-1-brief.md`, `.superpowers/sdd/task-2-brief.md`, `.superpowers/sdd/task-3-brief.md`, and `.superpowers/sdd/task-4-brief.md`
- 2026-07-08 11:24 removed the trailing EOF blank lines from those four brief files
- 2026-07-08 11:25 full `git diff --check` result: `exit code 0` with only CRLF conversion warnings

### Files Modified

- `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
- `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`
- `.superpowers/sdd/task-4-report.md`

### Current Conclusion

Task 4 is complete. Documentation now explains where reranking sits in the hybrid retrieval pipeline, and the focused reranker-related regression suite passes after the established rerun style for the known Windows sandbox issue. The brief-file EOF whitespace findings from the first diff check were corrected, and the current full `git diff --check` exits 0.

### Next Step

Run a real BGE reranker report if a local reranker model exists, or ask the user whether to download/provide one.

## 2026-07-08 11:30 Task 5 Real Model Availability

### Task Goal

Check whether the local BGE reranker model is available before running a real v2 hybrid rerank report.

### Commands

```powershell
Test-Path -LiteralPath '.cache/models/bge-reranker-v2-m3'
Get-ChildItem -LiteralPath '.cache/models' -Directory | Select-Object -ExpandProperty Name
```

### Results

```text
Test-Path .cache/models/bge-reranker-v2-m3 = False
available local model directories:
bge-m3
```

### Real Model Availability

`.cache/models/bge-reranker-v2-m3` is absent. The implementation is ready for fake-provider tests, but the real BGE reranker report is blocked until the user approves a download or provides a local model path.

### Current Conclusion

Task 5 stops before the real reranker run, as required by the plan. No model was downloaded and no real v2 `hybrid_rerank` report was generated.

## 2026-07-08 11:42 Final Review Fix

### Review Finding

Final whole-branch review found no Critical issues and one Important test gap:

- add a regression test proving `BGEFlagRerankerProvider` rejects a missing local model path before attempting backend imports, so future changes cannot silently turn a missing local path into a download path.

It also noted one Minor documentation issue:

- the newly appended reranker section in `docs/rag/dense-evaluation-walkthrough.zh-CN.md` was English inside a `zh-CN` document.

### Files Modified

- `tests/test_rag_reranking.py`
- `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
- `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`

### Verification

Focused pytest command:

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_final_reranker_review_fix tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py -q
```

Sandbox result:

```text
PermissionError: [WinError 5] Access is denied: '.pytest_tmp_final_reranker_review_fix'
```

Established-style rerun result:

```text
16 passed in 0.52s
```

Diff check:

```powershell
git diff --check
```

Result:

```text
exit code 0
```

### Current Conclusion

The final review Important finding is fixed. The BGE reranker wrapper now has a regression test that guards the local-model-only, no-silent-download boundary.

## 2026-07-08 11:50 Final Verification

### Commands

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_final_reranker_all tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py tests/test_rag_hybrid_eval.py tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py -q
git diff --check
Test-Path -LiteralPath '.cache/models/bge-reranker-v2-m3'
```

### Results

```text
sandbox pytest result: PermissionError: [WinError 5] Access is denied: '.pytest_tmp_final_reranker_all'
established-style pytest rerun: 43 passed in 0.68s
git diff --check: exit code 0
Test-Path .cache/models/bge-reranker-v2-m3: False
```

### Final Conclusion

The evaluation-only hybrid reranker implementation is complete and reviewed. The fake-provider regression suite passes, whitespace checks pass, and the real BGE reranker report remains intentionally blocked by the absent local reranker checkpoint.

## 2026-07-08 Real BGE Reranker Download and v2 Report

### Task Goal

Download a local pretrained reranker model and run the real hybrid rerank v2 evaluation that was previously blocked by the missing checkpoint.

### Model Choice

Recommended and downloaded:

```text
BAAI/bge-reranker-v2-m3
local path: .cache/models/bge-reranker-v2-m3
```

Reason:

- current FireClaw CLI default already points to `.cache/models/bge-reranker-v2-m3`
- `BGEFlagRerankerProvider` uses `FlagEmbedding.FlagReranker`, which supports this model
- `v2-m3` is multilingual and fits the current Chinese query + English corpus setting better than English-only reranker defaults

Other reasonable options discussed:

```text
BAAI/bge-reranker-base
BAAI/bge-reranker-large
BAAI/bge-reranker-v2-gemma
BAAI/bge-reranker-v2-minicpm-layerwise
```

The LLM/layerwise reranker options may be stronger but are heavier and were not chosen for the first local evaluation.

### Commands and Results

Initial official Hugging Face Hub API download attempt:

```powershell
.\.venv-bge-m3\Scripts\python.exe -c "from huggingface_hub import snapshot_download; p=snapshot_download(repo_id='BAAI/bge-reranker-v2-m3', local_dir='.cache/models/bge-reranker-v2-m3', local_dir_use_symlinks=False); print(p)"
```

Result:

```text
failed with requests.exceptions.SSLError / SSLEOFError when connecting to huggingface.co
```

Mirror attempt:

```powershell
$env:HF_ENDPOINT='https://hf-mirror.com'
```

Result:

```text
blocked by safety review because hf-mirror.com is not an official trusted source and the user had not explicitly accepted that supply-chain risk
```

Successful official Git/LFS download:

```powershell
git clone https://huggingface.co/BAAI/bge-reranker-v2-m3 .cache/models/bge-reranker-v2-m3
```

Result:

```text
Filtering content: 100% (3/3), 2.13 GiB, done.
```

Local file verification:

```text
.cache/models/bge-reranker-v2-m3/config.json = present
.cache/models/bge-reranker-v2-m3/model.safetensors = present
model.safetensors size = 2271071852 bytes
```

Minimal provider scoring check:

```powershell
cmd /c "set PYTHONPATH=src&& .\.venv-bge-m3\Scripts\python.exe -c ""from pathlib import Path; from fireclaw_core.rag.reranking import BGEFlagRerankerProvider; r=BGEFlagRerankerProvider(Path('.cache/models/bge-reranker-v2-m3'), batch_size=1, max_length=128); scores=r.score_pairs([('firefighter rehabilitation threshold', 'NFPA 1584 rehabilitation recommends monitoring firefighter heat stress and vital signs.'), ('firefighter rehabilitation threshold', 'Smokeview can display simulation slices and rendered smoke output.')]); print(r.model_info.to_dict()); print([round(s, 4) for s in scores])"""
```

Result:

```text
device = cuda:0
scores = [-0.5283, -11.0391]
```

The relevant firefighter-rehab passage scored much higher than the unrelated Smokeview passage, confirming the model loads and the project provider can execute real scores.

Real v2 hybrid rerank command:

```powershell
cmd /c "set PYTHONPATH=src&& .\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-hybrid-rerank-index --provider bge-m3 --model-path .cache/models/bge-m3 --dense-index-dir data/rag/fire_rescue/indexes/dense/bge-m3 --bm25-index-dir data/rag/fire_rescue/indexes/bm25/small_v1 --reranker-provider bge-reranker --reranker-model-path .cache/models/bge-reranker-v2-m3 --reranker-batch-size 8 --reranker-max-length 512 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v2.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v2.jsonl --dense-query-variants zh,en,terms --bm25-query-variants en,terms --rerank-query-variant en --small-top-k 50 --rerank-pool-size 50 --top-k 10 --require-reviewed-expansions --output data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v2_rerank_bge_reranker_v2_m3_report.json"
```

Generated report:

```text
data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v2_rerank_bge_reranker_v2_m3_report.json
```

Metrics:

```text
hybrid baseline v2:
case_count=30 hit_at_1=0.366667 hit_at_5=0.766667 hit_at_10=0.966667 mrr_at_10=0.55504 gold_recall_at_10=0.966667

hybrid rerank bge-reranker-v2-m3 v2:
case_count=30 hit_at_1=0.333333 hit_at_5=0.7 hit_at_10=0.833333 mrr_at_10=0.517778 gold_recall_at_10=0.833333
```

Case-rank changes:

```text
improved:
dense_zh_005 3->1
dense_zh_013 8->2
dense_zh_015 4->3
dense_zh_017 3->2
dense_zh_019 6->2
dense_zh_020 2->1
dense_zh_028 5->1
dense_zh_030 None->10

worse:
dense_zh_006 1->None
dense_zh_007 1->2
dense_zh_008 7->None
dense_zh_011 3->6
dense_zh_014 3->None
dense_zh_018 2->None
dense_zh_021 1->2
dense_zh_022 6->None
dense_zh_023 2->3
dense_zh_029 1->3
```

### Current Conclusion

The model is downloaded and operational. However, the zero-shot `bge-reranker-v2-m3` rerank setting is not an immediate improvement on the current 30-case v2 evaluation. It improves several hard cases, including `dense_zh_030`, but hurts more already-good cases and lowers aggregate Hit@5, Hit@10, MRR@10, and gold recall.

This suggests the next analysis should focus on why the reranker demotes known gold parents:

- possible query phrasing mismatch in `rerank:en`
- long parent passages truncated to 512 tokens may hide the evidence span
- strict single `gold_parent_id` labels may penalize semantically valid neighboring parent chunks
- cross-encoder is scoring parent text directly, not the best child evidence span
- zero-shot reranker may need child-level rerank before parent aggregation, or a keep-gold-method-candidates/fusion guard in the evaluation design

Do not claim reranking improves the system based on this first real run. Treat it as a useful negative ablation and inspect the worsened cases before changing code.

## 2026-07-08 Reranker Drop Investigation

### Task Goal

Investigate why the real `bge-reranker-v2-m3` hybrid rerank report underperformed the hybrid baseline.

### Current Progress

- 当前进展：
  - confirmed this is not a model availability problem
  - confirmed this is not simply a CLI/reporting issue
  - isolated the strongest factor so far: rerank query choice has a large effect
- 已完成：
  - inspected `hybrid_eval.py`, `rerank_eval.py`, `reranking.py`, and `dense_eval.py`
  - reconstructed top-50 hybrid candidates and full rerank positions for representative improved/worsened cases
  - ran two extra full v2 ablations with `--rerank-query-variant terms` and `--rerank-query-variant zh`
- 当前问题：
  - parent-level zero-shot rerank still loses some gold parents at top10, especially `dense_zh_006` and `dense_zh_018`
- 下一步：
  - discuss whether to analyze gold-label breadth / multi-gold parent labels before changing code
  - likely next design direction is child-level rerank or multi-query rerank fusion, not blindly keeping `rerank:en`
- 需要运行的命令：
  - none required before discussion; generated report files are already available

### Evidence: Query Variant Ablation

Baseline and real rerank reports:

```text
hybrid_bge_m3_bm25_v2_expanded_parent_report.json
hybrid_bge_m3_bm25_v2_rerank_bge_reranker_v2_m3_report.json
hybrid_bge_m3_bm25_v2_rerank_bge_reranker_v2_m3_terms_report.json
hybrid_bge_m3_bm25_v2_rerank_bge_reranker_v2_m3_zh_report.json
```

Metrics:

```text
hybrid baseline:
Hit@1=0.366667 Hit@5=0.766667 Hit@10=0.966667 MRR@10=0.55504  Recall@10=0.966667

rerank query=en:
Hit@1=0.333333 Hit@5=0.7      Hit@10=0.833333 MRR@10=0.517778 Recall@10=0.833333

rerank query=terms:
Hit@1=0.433333 Hit@5=0.8      Hit@10=0.933333 MRR@10=0.585317 Recall@10=0.933333

rerank query=zh:
Hit@1=0.433333 Hit@5=0.666667 Hit@10=0.866667 MRR@10=0.553783 Recall@10=0.866667
```

Interpretation:

- `rerank:en` is clearly the worst of the tested rerank query variants.
- `rerank:terms` improves ranking quality at the top (`Hit@1`, `Hit@5`, `MRR@10`) over the hybrid baseline.
- `rerank:terms` still loses one top10 hit relative to baseline, so it is not a clean all-metric improvement.
- `rerank:zh` improves `Hit@1` but hurts `Hit@5`/`Hit@10`, so multilingual reranking alone is not enough.

### Evidence: Full Top-50 Diagnostic

Representative case diagnostic with `rerank:en`:

```text
case_id      topic                               hybrid_rank rerank_rank gold_score rank10_score gap_to_rank10 top1_parent
dense_zh_005 usar_void_entry_risk               3           1           2.2559     -4.4141      +6.6699       gold
dense_zh_006 response_robot_test_methods         1           19          -3.0156    -2.1797      -0.8359       nist_response_robot_test_methods_guide__parent_00016
dense_zh_008 firefighter_rehab_thresholds        7           41          -4.1797    -1.0234      -3.1563       usfa_emergency_incident_rehabilitation_fa_314__parent_00068
dense_zh_013 fire360_degraded_video_understanding 8          2           3.7012     -1.1260      +4.8271       arxiv_2506_02167_fire360_firefighting_perception_memory__parent_00001
dense_zh_014 fire360_degraded_object_retrieval   3           16          -4.3984    -4.1289      -0.2695       arxiv_2506_02167_fire360_firefighting_perception_memory__parent_00001
dense_zh_018 industrial_robot_communication_limits 2         35          -5.9805    -3.7539      -2.2266       arxiv_2606_23246_robotic_intervention_industrial_emergency__parent_00006
dense_zh_022 usar_robot_deployment_categories    6           18          -0.8149    -0.1714      -0.6436       nist_usar_robot_performance_requirements__parent_00002
dense_zh_028 standpipe_fire_hose_connection_risk 5           1           1.6748     -1.7217      +3.3965       gold
dense_zh_030 smokeview_load_smoke_slice_outputs  13          10          1.2275      1.2275       0.0          nist_smv_6_11_0_user_guide__parent_00097
```

Important observation:

- The dropped gold parents are usually present in the hybrid top50.
- The reranker is deliberately assigning them low scores; this is not an evaluation serialization bug.
- Gold evidence is not simply beyond the loaded parent text: the gold offsets begin at word 0 for the inspected cases, so the main issue is not just `max_passage_chars`.

### Case-Level Findings

`dense_zh_006 response_robot_test_methods`:

- hybrid placed gold parent rank 1
- `rerank:en` moved gold to rank 19
- `rerank:terms` still missed top10
- `rerank:zh` recovered it at rank 7
- top reranked parents are other NIST response robot test-method pages that appear semantically close and may contain more direct standardized test-method content
- likely issue: strict single gold parent is too narrow, and parent-level rerank prefers a neighboring section with stronger lexical/evidence match

`dense_zh_018 industrial_robot_communication_limits`:

- hybrid placed gold parent rank 2
- all tested rerank variants missed top10
- top reranked parent for `rerank:en` is `arxiv_2606_23246_robotic_intervention_industrial_emergency__parent_00006`
- that top parent text directly mentions that brick walls reinforced with steel severely attenuated signal and reduced communication reliability
- likely issue: the current gold parent may be too narrow; parent 00006 is arguably highly relevant for the query, maybe more direct than the selected gold

`dense_zh_008 firefighter_rehab_thresholds`:

- hybrid rank 7
- `rerank:en` missed top10, but `rerank:terms` recovered it at rank 10
- top reranked parents are closely related rehab/NFPA/SCBA parent chunks
- likely issue: terms are essential for reranking this case; natural-language `en` query is too broad

`dense_zh_022 usar_robot_deployment_categories`:

- hybrid rank 6
- `rerank:en` missed top10
- `rerank:terms` promoted gold to rank 1
- likely issue: exact deployment/category terminology is important

### Current Conclusion

Root cause is not "reranker model unusable." It is a combination of:

1. `rerank:en` natural-language query is unstable for technical/firefighting cases.
2. Parent-level rerank lets a cross-encoder prefer semantically nearby parent chunks over the single strict gold parent.
3. Some current gold labels are likely too narrow for parent-level RAG evaluation, especially `dense_zh_018`.
4. Cross-encoder reranking over whole parent text is not the same as reranking the best evidence-bearing small chunk.

Best current ablation is `rerank:terms`, not `rerank:en`. It improves top-rank quality but still hurts `Hit@10` compared with hybrid baseline.

Do not change production code until discussed. The likely next design options are:

- use `terms` as rerank query variant by default for this dataset
- rerank small chunks first, then aggregate to parent
- rerank with multiple query variants and fuse reranker scores
- allow carefully reviewed multiple `gold_parent_ids` for cases where neighboring parent chunks are genuinely relevant

## 2026-07-08 Multi-Query Rerank RRF Planning

### Task Goal

Plan the next reranker optimization after discussing why the current `hybrid_rerank` run still used only one rerank query variant at the second stage.

### Current Progress

- 当前进展：
  - clarified that current hybrid retrieval already uses RRF over dense `zh,en,terms` and BM25 `en,terms`
  - clarified that current rerank stage is single-query unless separately rerun as `rerank:en`, `rerank:terms`, or `rerank:zh`
  - agreed that `terms default rerank` and `multi-query rerank fusion` target the same issue
- 已完成：
  - selected multi-query rerank RRF as the next implementation direction
  - decided RRF is preferred over raw weighted score addition for this first multi-query rerank experiment because cross-encoder raw scores can have different scales across query variants
  - wrote the implementation plan
- 当前问题：
  - no code has been changed for multi-query rerank RRF yet
  - existing uncommitted real rerank report artifacts remain in the working tree
- 下一步：
  - execute the plan task-by-task using Subagent-Driven or Inline Execution after user choice
- 需要运行的命令：
  - none before the user chooses execution mode

### Plan File

```text
docs/superpowers/plans/2026-07-08-fireclaw-multi-query-rerank-rrf.md
```

### Plan Scope

The plan implements only the first optimization layer:

```text
hybrid top50 parent candidates
-> rerank with zh query
-> rerank with en query
-> rerank with terms query
-> RRF over rerank ranks
-> final top10 parents
```

The plan explicitly leaves these as later separate experiments:

- expanded `gold_parent_ids` or graded relevance labels
- small-chunk rerank followed by parent aggregation
- hybrid-score plus rerank-score joint sorting or high-confidence candidate protection

### Self-Review Results

Commands:

```powershell
rg -n "TBD|TODO|implement later|fill in details|Similar to|appropriate error|Write tests for the above|Record .* here|\.\.\." docs\superpowers\plans\2026-07-08-fireclaw-multi-query-rerank-rrf.md
git diff --check -- docs\superpowers\plans\2026-07-08-fireclaw-multi-query-rerank-rrf.md
```

Results:

```text
placeholder scan: no matches
git diff --check: exit code 0
```

## 2026-07-08 Multi-Query Rerank RRF

### Goal

Evaluate whether `zh + en + terms` rerank RRF improves over single-query rerank.

### Commands

Focused regression suite in the managed sandbox:

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_multi_rerank_final tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py tests/test_rag_hybrid_eval.py tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py -q
```

Sandbox result:

```text
PermissionError: [WinError 5] Access is denied: 'C:\Users\L\Desktop\lpp\fireclaw-master\.pytest_tmp_multi_rerank_final'
```

Established-style rerun:

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_multi_rerank_final tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py tests/test_rag_hybrid_eval.py tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py -q
```

Established-style rerun result:

```text
47 passed in 0.81s
```

Whitespace/diff check:

```powershell
git diff --check
```

Result:

```text
exit code 0
```

Real multi-query BGE reranker report:

```powershell
cmd /c "set PYTHONPATH=src&& .\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-hybrid-rerank-index --provider bge-m3 --model-path .cache/models/bge-m3 --dense-index-dir data/rag/fire_rescue/indexes/dense/bge-m3 --bm25-index-dir data/rag/fire_rescue/indexes/bm25/small_v1 --reranker-provider bge-reranker --reranker-model-path .cache/models/bge-reranker-v2-m3 --reranker-batch-size 8 --reranker-max-length 512 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v2.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v2.jsonl --dense-query-variants zh,en,terms --bm25-query-variants en,terms --rerank-query-variants zh,en,terms --small-top-k 50 --rerank-pool-size 50 --top-k 10 --require-reviewed-expansions --output data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v2_rerank_bge_reranker_v2_m3_multi_rrf_report.json"
```

Real report result:

```text
generated: data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v2_rerank_bge_reranker_v2_m3_multi_rrf_report.json
retrieval_method=hybrid_rerank
rerank_fusion=rrf
rerank_query_variants=zh,en,terms
reranker backend=FlagEmbedding.FlagReranker
device=cuda:0
```

### Results

```text
hybrid baseline: Hit@1=0.366667 Hit@5=0.766667 Hit@10=0.966667 MRR@10=0.555040 Recall@10=0.966667
rerank en: Hit@1=0.333333 Hit@5=0.700000 Hit@10=0.833333 MRR@10=0.517778 Recall@10=0.833333
rerank terms: Hit@1=0.433333 Hit@5=0.800000 Hit@10=0.933333 MRR@10=0.585317 Recall@10=0.933333
rerank zh: Hit@1=0.433333 Hit@5=0.666667 Hit@10=0.866667 MRR@10=0.553783 Recall@10=0.866667
rerank multi-query rrf: Hit@1=0.433333 Hit@5=0.766667 Hit@10=0.833333 MRR@10=0.583981 Recall@10=0.833333
```

### Conclusion

Multi-query rerank RRF improved over `rerank:en` on all five metrics, but it did not beat `rerank:terms` on this 30-case dataset. `rerank:terms` remains the strongest rerank ablation overall here, with better `Hit@5`, `Hit@10`, `MRR@10`, and `Recall@10`, while `multi-query rrf` only matches its `Hit@1`. So `multi-query rrf` is a better alternative than `rerank:en`, but it is not the current best or recommended rerank setting for this benchmark.

### Remaining Questions

- whether strict single gold parent labels still undercount genuinely relevant neighbors;
- whether child-level rerank would recover evidence-bearing small chunks better than parent-level rerank;
- whether hybrid-score protection is needed if Hit@10 still drops.

## 2026-07-08 Multi-Rerank Final Review Fix

### Task Goal

Fix the final review finding that multi-query rerank CSV variants accepted unsupported or duplicate values without validation.

### Commands

```powershell
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_multi_rerank_variant_validation_red tests/test_rag_reranking.py::test_select_rerank_queries_rejects_duplicate_variants tests/test_rag_reranking.py::test_select_rerank_queries_rejects_unsupported_variants -q
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_multi_rerank_variant_validation_green tests/test_rag_reranking.py -q
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_multi_rerank_variant_validation_suite tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py -q
git diff --check -- src/fireclaw_core/rag/reranking.py tests/test_rag_reranking.py
```

### Results

- RED failed as intended with two `Failed: DID NOT RAISE <class 'ValueError'>` assertions.
- GREEN passed: `15 passed in 0.21s`.
- Managed-sandbox regression run hit the known Windows cleanup issue:

```text
PermissionError: [WinError 5] Access is denied: '.pytest_tmp_multi_rerank_variant_validation_suite'
```

- Established-style unsandboxed rerun of the same regression suite passed: `22 passed in 0.68s`.
- `git diff --check` exited 0 with only existing LF/CRLF warnings.

### Files Modified

- `src/fireclaw_core/rag/reranking.py`
- `tests/test_rag_reranking.py`
- `.superpowers/sdd/multi-rerank-final-review-fix-report.md`

### Conclusion

`select_rerank_queries(...)` now rejects empty, duplicate, and unsupported multi-rerank variants while preserving `select_rerank_query(...)` fallback behavior for the single-query path.

## 2026-07-08 Multi-Rerank Final Verification

### Commands

```powershell
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_multi_rerank_final_verify_after_fix tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py tests/test_rag_hybrid_eval.py tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py -q
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=.pytest_tmp_multi_rerank_final_verify_after_fix_escalated tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py tests/test_rag_hybrid_eval.py tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py -q
git diff --check
```

### Results

- Managed-sandbox pytest run hit the known Windows cleanup problem:

```text
PermissionError: [WinError 5] Access is denied: '.pytest_tmp_multi_rerank_final_verify_after_fix'
```

- Escalated rerun of the same focused suite passed:

```text
49 passed in 0.65s
```

- `git diff --check` exited 0 with only LF/CRLF warnings.

### Current Conclusion

The multi-query rerank RRF implementation and final variant-validation fix are verified. Final review and fix review found no remaining Critical or Important issues.
