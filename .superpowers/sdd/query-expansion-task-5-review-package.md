# Review package: Query Expansion Task 5

## Scoped status
```text
?? data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_expanded_parent_report.json
?? data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_parent_report.json
?? data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
?? docs/rag/dense-evaluation-walkthrough.zh-CN.md
?? memory/2026-07-07/fireclaw-dense-miss-case-analysis.md
```

## Task report
```markdown
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

Final checks:

```powershell
git status --short
git diff --check
```

Results:

- `git status --short` exit code: `0`
- `git status --short` still warns that several existing `.pytest_tmp*` directories cannot be opened due to permission denial.
- `git status --short` shows pre-existing Tasks 1-4 source/test changes plus untracked docs/memory/data/report paths; no commit was created.
- `git diff --check` exit code: `0`
- `git diff --check` emitted only LF-to-CRLF warnings for pre-existing tracked files:
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

- Focused pytest is blocked by managed Windows Python temp directory permissions. It could not be fully verified here.
- `git check-ignore -v` shows `.gitignore:20:*.jsonl` ignores these eval source artifacts:
  - `data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl`
  - `data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl`
  - `data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl`
- `query_expansions_zh_v1.jsonl` and `glossary_candidates_zh_v1.jsonl` remain `candidate` until user review.
```

## Walkthrough tail
```markdown
score > 0.6 才算相关
```

原因是 dense 分数不是绝对语义概率。不同 query、不同主题、不同 chunk 长度下，分数分布会变。第一版更稳的是只看排序：

```text
gold evidence 有没有被排进前 K？
```

这就是 `Hit@K`、`MRR@10` 这类指标适合第一层 retrieval evaluation 的原因。

## 当前结果怎么理解

这次结果说明：

```text
当前 BGE-M3 dense index 能跑通；
对一部分中文消防任务 query，能找回英文语料中的 gold parent；
但 dense-only 效果不稳定，尤其对精确术语和标准条文类 query 较弱。
```

具体表现：

```text
Hit@10 = 0.6
```

意味着：

```text
10 条题里，6 条能在前 10 个检索结果中找回标准证据。
```

```text
Hit@1 = 0.2
```

意味着：

```text
10 条题里，只有 2 条把标准证据排在第一。
```

所以这不是最终 RAG 质量，只是 dense retrieval 第一层基线。

## 下一步最该看什么

下一步建议分析这 4 个 miss cases：

```text
dense_zh_002 remote_gas_source_detection
dense_zh_006 response_robot_test_methods
dense_zh_008 firefighter_rehab_thresholds
dense_zh_009 usar_hazmat_entry_safety
```

每个 miss case 应该看：

```text
1. top 10 到底返回了什么？
2. 返回结果是不是其实相关，只是没有命中预设 gold parent？
3. query 是否需要英文术语扩展？
4. gold parent 是否选得太窄？
5. dense-only 是否对这类精确术语确实弱？
```

如果很多 miss 是术语问题，那么 BM25 / hybrid 的价值就会很明显。

## 可选优化评估：Query Expansion 与 Parent Aggregation

当前 strict dense baseline 不变，默认仍然只用中文 query 和 small chunk 排名。

新增优化只在显式打开参数时生效：

```text
--query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl
--query-variants zh,en,terms
--ranking-view parent
--small-top-k 50
```

含义是：

```text
中文原 query
+ 缓存的英文翻译 query
+ 候选专业术语 query
-> 分别 dense 检索
-> 用 RRF 融合
-> 按 parent_id 聚合排序
-> 仍然用原始 strict gold_parent_ids 计算 Hit@K / MRR@10 / gold_recall@10
```

注意：

```text
query_expansions_zh_v1.jsonl 和 glossary_candidates_zh_v1.jsonl 初始状态是 candidate。
candidate 表示这些翻译和术语由 LLM/agent 辅助生成，还需要人工审核。
正式论文实验应记录是否使用 candidate，或者先改成 reviewed 后再作为正式结果。
```
```

## Memory tail
```markdown
- The user chose LLM translation for query expansion.
- The user approved a cache/freeze flow:
  - first run can use LLM translation;
  - translations are saved to `query_expansions_zh_v1.jsonl`;
  - official eval uses the cached file rather than calling the LLM each time.
- The user also requested keeping a manually reviewed/translated field, because the agent may need to provide improved translations.

Design spec written:

```text
docs/superpowers/specs/2026-07-07-fireclaw-dense-query-expansion-parent-aggregation-design.md
```

Spec scope:

- query expansion with original Chinese query, LLM English translation, manually reviewed English translation, and professional terminology query;
- candidate glossary for firefighting robot topics and the previous miss cases;
- parent-level deduplication/aggregation using existing small-chunk hits;
- RRF fusion for multiple query variants;
- strict baseline remains unchanged;
- chunk embedding text enhancement is explicitly out of scope.

Important repository-rule note:

- The Superpowers brainstorming skill asks for committing the design doc, but this repository guide says to commit only when explicitly asked.
- Therefore the design doc was saved but not committed.

Current state:

- Waiting for user review of the design spec before invoking `writing-plans`.

## Superpowers Implementation Plan Update

**Timestamp:** 2026-07-07 +08:00

The user approved the design direction by saying "开始吧".

Implementation plan written:

```text
docs/superpowers/plans/2026-07-07-fireclaw-dense-query-expansion-parent-aggregation.md
```

Plan summary:

1. Add `query_expansion.py` and tests for cached `zh/en/terms` variants.
2. Add `dense_ranking.py` and tests for parent aggregation and RRF fusion.
3. Extend `dense_eval.py` with optional expanded evaluation while preserving the default strict baseline.
4. Add CLI flags and candidate expansion/glossary JSONL files.
5. Update docs and run unit plus BGE-M3 ablation verification.

Self-review notes:

- Placeholder scan was clean after removing self-review wording that triggered false positives.
- Plan avoids a circular import by keeping `dense_ranking` imports local inside expanded evaluation.
- Plan uses checkpoint/diff steps rather than commit steps because the repository guide says to commit only when explicitly asked.

Current state:

- Waiting for the user to choose execution mode:
  - Subagent-Driven; or
  - Inline Execution.

## Task 5 Documentation and Verification

**Timestamp:** 2026-07-07 17:17:31 +08:00

当前进展：

- Task 5 的文档说明和验证记录已执行到最终检查前。
- 本次任务不修改 `src/` 或 `tests/`，不创建 commit。

已完成：

- Updated `docs/rag/dense-evaluation-walkthrough.zh-CN.md` by appending the brief-specified section `可选优化评估：Query Expansion 与 Parent Aggregation`.
- Generated/updated BGE-M3 evaluation reports under `data/rag/fire_rescue/eval/runs/`.
- Confirmed `.venv-bge-m3\Scripts\python.exe` and `.cache/models/bge-m3` were available.

Focused pytest command:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_expansion_all tests/test_rag_query_expansion.py tests/test_rag_dense_ranking.py tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

Result:

- Exit code: `1`
- Hit known Windows basetemp PermissionError:

```text
PermissionError: [WinError 5] Access is denied: 'C:\Users\L\AppData\Local\Temp\pytest_tmp_dense_expansion_all'
```

Rerun attempt with workspace basetemp:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.tmp\pytest_tmp_dense_expansion_all_task5 tests/test_rag_query_expansion.py tests/test_rag_dense_ranking.py tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

Result:

- Exit code: `1`
- Same PermissionError on `.tmp\pytest_tmp_dense_expansion_all_task5`.

Additional diagnostic run using a one-process pytest launcher that disabled only `_pytest.pathlib.cleanup_dead_symlinks`:

```powershell
$env:PYTHONPATH = ".deps;src"
python -c "import sys; import _pytest.pathlib as pathlib; pathlib.cleanup_dead_symlinks = lambda root: None; import pytest; sys.exit(pytest.main(['--basetemp=.tmp/pytest_tmp_dense_expansion_all_task5_nocleanup','tests/test_rag_query_expansion.py','tests/test_rag_dense_ranking.py','tests/test_rag_dense_eval.py','tests/test_rag_dense_cli.py','tests/test_rag_dense_retrieval.py','-q']))"
```

Result line:

```text
20 passed, 13 errors in 0.53s
```

All 13 errors occurred at `tmp_path` fixture/setup while `pathlib.Path.iterdir()` tried to list the pytest basetemp directory and hit:

```text
PermissionError: [WinError 5] Access is denied
```

Operational diagnosis:

- Python-created temp directories in this managed Windows environment can become non-listable by the Python process immediately after creation.
- A direct probe with `tempfile.mkdtemp()` followed by `os.listdir()` failed with the same `PermissionError`.
- Therefore the focused pytest suite could not be fully verified in this environment even after the known basetemp workaround attempt.

Strict BGE-M3 baseline command:

```powershell
$env:PYTHONPATH = 'src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

Result:

- Exit code: `0`
- Report path: `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json`
- Metrics:
  - `case_count = 10`
  - `hit_at_1 = 0.2`
  - `hit_at_5 = 0.4`
  - `hit_at_10 = 0.6`
  - `mrr_at_10 = 0.323611`
  - `gold_recall_at_10 = 0.6`

Parent-only ablation command:

```powershell
$env:PYTHONPATH = 'src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --ranking-view parent --small-top-k 50 --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_parent_report.json
```

Result:

- Exit code: `0`
- Report path: `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_parent_report.json`
- `retrieval_config.ranking_view = "parent"`
- Metrics:
  - `case_count = 10`
  - `hit_at_1 = 0.2`
  - `hit_at_5 = 0.4`
  - `hit_at_10 = 0.8`
  - `mrr_at_10 = 0.350278`
  - `gold_recall_at_10 = 0.8`

Expanded parent ablation command:

```powershell
$env:PYTHONPATH = 'src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl --query-variants zh,en,terms --ranking-view parent --small-top-k 50 --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_expanded_parent_report.json
```

Result:

- Exit code: `0`
- Report path: `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_expanded_parent_report.json`
- `retrieval_config.query_variants = ["zh", "en", "terms"]`
- `retrieval_config.fusion = "rrf"`
- Metrics:
  - `case_count = 10`
  - `hit_at_1 = 0.2`
  - `hit_at_5 = 0.8`
  - `hit_at_10 = 1.0`
  - `mrr_at_10 = 0.444444`
  - `gold_recall_at_10 = 1.0`

Candidate caveat:

- `data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl` remains `candidate`.
- `data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl` remains `candidate`.
- Candidate means the translations and terms were LLM/agent-assisted and still need user review before they should be treated as official paper results.

Operational concerns:

- `git check-ignore -v` showed `.gitignore:20:*.jsonl` ignores:
  - `data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl`
  - `data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl`
  - `data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl`
- If these JSONL eval artifacts are intended to be versioned, `.gitignore` will need a later explicit exception or force-add policy. Task 5 did not change `.gitignore`.

当前问题：

- Focused pytest could not fully pass in this managed Windows environment because pytest temp directories are not listable by Python.

下一步：

- Run final `git status --short` and `git diff --check`.
- Write `.superpowers/sdd/query-expansion-task-5-report.md`.

需要运行的命令：

```powershell
git status --short
git diff --check
```
```

## Metrics summary from reports
```text
PATH=data\rag\fire_rescue\eval\runs\dense_bge_m3_zh_v1_report.json
case_count=10 hit_at_1=0.2 hit_at_5=0.4 hit_at_10=0.6 mrr_at_10=0.323611 gold_recall_at_10=0.6

PATH=data\rag\fire_rescue\eval\runs\dense_bge_m3_zh_v1_parent_report.json
case_count=10 hit_at_1=0.2 hit_at_5=0.4 hit_at_10=0.8 mrr_at_10=0.350278 gold_recall_at_10=0.8
retrieval_config={"query_variants":["zh"],"fusion":"none","rrf_k":60,"ranking_view":"parent","parent_aggregation":"max","small_top_k":50,"final_top_k":10,"query_expansions_path":null,"require_reviewed_expansions":false}

PATH=data\rag\fire_rescue\eval\runs\dense_bge_m3_zh_v1_expanded_parent_report.json
case_count=10 hit_at_1=0.2 hit_at_5=0.8 hit_at_10=1.0 mrr_at_10=0.444444 gold_recall_at_10=1.0
retrieval_config={"query_variants":["zh","en","terms"],"fusion":"rrf","rrf_k":60,"ranking_view":"parent","parent_aggregation":"max","small_top_k":50,"final_top_k":10,"query_expansions_path":"data\\rag\\fire_rescue\\eval\\query_expansions_zh_v1.jsonl","require_reviewed_expansions":false}

```
