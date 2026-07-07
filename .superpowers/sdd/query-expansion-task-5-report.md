# Task 5 Report: Documentation and Verification

## What Implemented

- Appended the required walkthrough section to `docs/rag/dense-evaluation-walkthrough.zh-CN.md`:
  - `可选优化评估：Query Expansion 与 Parent Aggregation`
  - Covers explicit CLI flags, RRF fusion, parent aggregation, strict gold metric reuse, and candidate caveat.
- Appended a timestamped verification record to `memory/2026-07-07/fireclaw-dense-miss-case-analysis.md`.
- Ran BGE-M3 strict baseline, parent-only ablation, and expanded parent ablation.
- Did not modify `src/` or `tests/`.
- Did not create commits.

## Verification Commands and Results

Focused pytest command from brief:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_expansion_all tests/test_rag_query_expansion.py tests/test_rag_dense_ranking.py tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

Result:

- Exit code: `1`
- Blocked by known Windows basetemp `PermissionError`:

```text
PermissionError: [WinError 5] Access is denied: 'C:\Users\L\AppData\Local\Temp\pytest_tmp_dense_expansion_all'
```

Workspace basetemp rerun:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.tmp\pytest_tmp_dense_expansion_all_task5 tests/test_rag_query_expansion.py tests/test_rag_dense_ranking.py tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

Result:

- Exit code: `1`
- Same `PermissionError` on `.tmp\pytest_tmp_dense_expansion_all_task5`.

Diagnostic pytest launcher with `_pytest.pathlib.cleanup_dead_symlinks` disabled for the current process only:

```powershell
$env:PYTHONPATH = ".deps;src"
python -c "import sys; import _pytest.pathlib as pathlib; pathlib.cleanup_dead_symlinks = lambda root: None; import pytest; sys.exit(pytest.main(['--basetemp=.tmp/pytest_tmp_dense_expansion_all_task5_nocleanup','tests/test_rag_query_expansion.py','tests/test_rag_dense_ranking.py','tests/test_rag_dense_eval.py','tests/test_rag_dense_cli.py','tests/test_rag_dense_retrieval.py','-q']))"
```

Result:

```text
20 passed, 13 errors in 0.53s
```

All 13 errors were `tmp_path` fixture/setup errors caused by `pathlib.Path.iterdir()` hitting `PermissionError` on the pytest basetemp directory. A direct Python probe with `tempfile.mkdtemp()` followed by `os.listdir()` also hit the same PermissionError in this environment.

Controller final focused pytest verification:

```powershell
python -m pytest --basetemp=.pytest_tmp_dense_expansion_all_controller tests/test_rag_query_expansion.py tests/test_rag_dense_ranking.py tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

Result:

```text
33 passed in 0.66s
```

Final checks:

```powershell
git status --short --ignored
git diff --check
```

Results:

- `git status --short --ignored` exit code: `0`
- `git status --short --ignored` emitted many permission warnings for pre-existing `.deps`, `.pytest_tmp*`, `.tmp`, and pytest temp directories.
- `git status --short --ignored` shows pre-existing Tasks 1-4 source/test changes plus untracked docs/memory/data/report paths; no commit was created.
- `git diff --check` exit code: `0`
- `git diff --check` output only LF-to-CRLF warnings for:
  - `src/fireclaw_core/rag/rag_cli.py`
  - `tests/test_rag_dense_cli.py`

## BGE-M3 Report Paths and Metrics

Strict BGE-M3 baseline:

```powershell
$env:PYTHONPATH = 'src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

- Exit code: `0`
- Report path: `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json`
- `case_count = 10`
- `hit_at_1 = 0.2`
- `hit_at_5 = 0.4`
- `hit_at_10 = 0.6`
- `mrr_at_10 = 0.323611`
- `gold_recall_at_10 = 0.6`

Parent-only ablation:

```powershell
$env:PYTHONPATH = 'src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --ranking-view parent --small-top-k 50 --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_parent_report.json
```

- Exit code: `0`
- Report path: `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_parent_report.json`
- `retrieval_config.ranking_view = "parent"`
- `case_count = 10`
- `hit_at_1 = 0.2`
- `hit_at_5 = 0.4`
- `hit_at_10 = 0.8`
- `mrr_at_10 = 0.350278`
- `gold_recall_at_10 = 0.8`

Expanded parent ablation:

```powershell
$env:PYTHONPATH = 'src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl --query-variants zh,en,terms --ranking-view parent --small-top-k 50 --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_expanded_parent_report.json
```

- Exit code: `0`
- Report path: `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_expanded_parent_report.json`
- `retrieval_config.query_variants = ["zh", "en", "terms"]`
- `retrieval_config.fusion = "rrf"`
- `case_count = 10`
- `hit_at_1 = 0.2`
- `hit_at_5 = 0.8`
- `hit_at_10 = 1.0`
- `mrr_at_10 = 0.444444`
- `gold_recall_at_10 = 1.0`

## Files Changed

- `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
- `memory/2026-07-07/fireclaw-dense-miss-case-analysis.md`
- `.superpowers/sdd/query-expansion-task-5-report.md`
- Generated/updated eval report outputs:
  - `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json`
  - `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_parent_report.json`
  - `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_expanded_parent_report.json`

## Self-Review Findings

- Strict baseline default CLI path was preserved and reproduced expected metrics.
- Query expansion cache, candidate/reviewed caveat, professional terminology candidate files, parent aggregation, and RRF fusion are documented.
- Chunk embedding text enhancement remains excluded from this task.
- Type/interface names in the walkthrough section do not introduce inconsistent names.
- No commit was created, per repository/user constraint.

## Concerns or Blockers

- Earlier local focused pytest attempts were blocked by managed Windows Python temp directory permissions, but the controller later completed the full focused pytest suite successfully: `33 passed in 0.66s`.
- `git check-ignore -v` shows `.gitignore:20:*.jsonl` ignores these eval source artifacts:
  - `data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl`
  - `data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl`
  - `data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl`
- `query_expansions_zh_v1.jsonl` and `glossary_candidates_zh_v1.jsonl` remain `candidate` until user review.

## Review Finding Fix Evidence

- Updated `memory/2026-07-07/fireclaw-dense-miss-case-analysis.md` so Task 5 no longer presents final checks/report writing as the next step.
- Recorded controller final focused pytest command and result exactly:

```powershell
python -m pytest --basetemp=.pytest_tmp_dense_expansion_all_controller tests/test_rag_query_expansion.py tests/test_rag_dense_ranking.py tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

```text
33 passed in 0.66s
```

- Preserved existing BGE-M3 report metrics unchanged.
- Preserved candidate caveat: `query_expansions_zh_v1.jsonl` and `glossary_candidates_zh_v1.jsonl` are still `candidate`, and JSONL eval artifacts are ignored by `.gitignore:20 (*.jsonl)`.
