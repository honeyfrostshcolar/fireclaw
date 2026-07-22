# FireClaw Dense Miss Case Analysis

**Date:** 2026-07-07
**Timestamp:** 2026-07-07 +08:00
**Status:** Four dense retrieval miss cases analyzed with top-10, temporary top-50, gold parent text, and parent-deduplicated ranking.

## Task Goal

The user asked to analyze why these dense evaluation cases missed `Hit@10`:

- `dense_zh_002` / `remote_gas_source_detection`
- `dense_zh_006` / `response_robot_test_methods`
- `dense_zh_008` / `firefighter_rehab_thresholds`
- `dense_zh_009` / `usar_hazmat_entry_safety`

The analysis questions were:

1. What did top 10 return?
2. Are returned results actually relevant but outside the preset gold parent?
3. Does the query need English terminology expansion?
4. Was the gold parent too narrow?
5. Is dense-only weak for precise terminology?

## Commands Executed

Read current formal report and gold cases with explicit UTF-8 encoding:

```powershell
$casePath='data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl'
$reportPath='data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json'
$cases=Get-Content -Encoding UTF8 $casePath | ForEach-Object { $_ | ConvertFrom-Json }
$report=Get-Content -Encoding UTF8 -Raw $reportPath | ConvertFrom-Json
```

Important note:

- A first PowerShell parse without `-Encoding UTF8` produced mojibake and JSON parse errors.
- The files themselves are valid UTF-8; Windows PowerShell must read them explicitly as UTF-8.

Ran a temporary top-50 report to inspect true gold ranks beyond top 10:

```powershell
$env:PYTHONPATH='src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 50 --output .tmp/dense_bge_m3_zh_v1_top50_report.json
```

Result:

- exit code: `0`
- temporary report path: `.tmp/dense_bge_m3_zh_v1_top50_report.json`
- console JSON showed mojibake because the Windows console path is not UTF-8 clean, but the output file parsed correctly with `-Encoding UTF8`.

Inspected gold parent chunks and selected neighboring/top-hit parent chunks from:

```text
data/rag/fire_rescue/chunks/parent_chunks.jsonl
```

## True Gold Ranks in Temporary Top-50

The official report uses top 10 small chunks. The temporary top-50 showed all four gold parents are near misses rather than random failures:

| case_id | gold small-chunk ranks within top 50 | unique parent rank |
|---|---:|---:|
| `dense_zh_002` | 13, 18, 22 | 11 |
| `dense_zh_006` | 12, 15, 17, 23, 48 | 9 |
| `dense_zh_008` | 15 | 10 |
| `dense_zh_009` | 13, 14, 43 | 12 |

Key implication:

- Current evaluation ranks small chunks directly, so duplicate small chunks from the same parent can consume top-10 slots.
- Parent-level deduplication would turn `dense_zh_006` and `dense_zh_008` into parent-level top-10 hits.
- `dense_zh_002` and `dense_zh_009` remain just outside parent-level top 10.

## Case Conclusions

### dense_zh_002 / remote_gas_source_detection

Query:

```text
已知厂房平面图时，机器人如何规划远程气体扫描，尽快找出可燃气体泄漏源？
```

Gold:

```text
arxiv_1801_06819_next_best_smell_mobile_robot_gas_detection__parent_00002
```

Top-10 behavior:

- rank 1, 3, and 4 are the same paper's `parent_00001`, which contains the paper title, abstract, and high-level description of planning a path for a remote gas sensor to minimize time to detect all gas sources.
- rank 9 and 10 are also same-paper evidence about real-world remote gas detection experiments and experimental results.
- gold `parent_00002` appears at small ranks 13, 18, and 22; parent-level unique rank 11.

Root cause:

- Not a total retrieval failure. The dense retriever found the right paper and task.
- The natural-language query matches the abstract in `parent_00001` more strongly than the formal problem-definition section in `parent_00002`.
- The gold parent is somewhat narrow; `parent_00001` is also relevant for this query.
- English terminology expansion would likely help: `remote gas detection`, `methane leak`, `TDLAS`, `Next-Best-Smell`, `coverage planning`, `candidate locations`, `information gain`, `sensing time`.
- BM25/hybrid can help only if the Chinese query is translated/expanded with these English terms.

### dense_zh_006 / response_robot_test_methods

Query:

```text
消防救援机器人在采购前应该从哪些标准化能力维度做测试？
```

Gold:

```text
nist_response_robot_test_methods_guide__parent_00003
```

Top-10 behavior:

- Top hits are mostly the same NIST response robot standards guide or the NIST USAR robot performance requirements document.
- rank 1 explains standard test methods and capability objectives/lower thresholds.
- rank 3 is `Guiding Robot Purchases / Selecting a Suite of Representative Test Methods`, which is arguably more directly relevant to the purchasing query than the selected gold overview parent.
- gold appears at small ranks 12, 15, 17, 23, and 48; parent-level unique rank 9.

Root cause:

- This is mostly a false negative caused by small-chunk ranking and single-parent gold strictness.
- Parent-deduplicated ranking would count the gold parent within top 10.
- The returned results are relevant and in some places better targeted than the chosen gold parent.
- The gold parent is broad project overview evidence; top-ranked parent `00016` is a stronger purchasing-specific evidence item.
- English expansion terms that may help: `standard test methods`, `response robots`, `robot purchases`, `representative test methods`, `performance objectives`, `lower capability thresholds`.

### dense_zh_008 / firefighter_rehab_thresholds

Query:

```text
消防员连续作业多久、用完几瓶空呼后，必须进入rehab做补水和医疗评估？
```

Gold:

```text
usfa_emergency_incident_rehabilitation_fa_314__parent_00102
```

Top-10 behavior:

- Top 10 are all from the correct USFA rehab document.
- rank 2 / rank 4 parent `00068` directly answers the threshold question:
  - self-rehab after one 30-minute SCBA cylinder or 20 minutes intense work without SCBA;
  - formal rehab with medical evaluation after two 30-minute SCBA cylinders, one 45/60-minute SCBA cylinder, or similar criteria.
- gold `parent_00102` appears at small rank 15; parent-level unique rank 10.
- gold `parent_00102` is about dispatching rehab resources and IC decisions, not the specific SCBA/work-duration threshold.

Root cause:

- Strong false negative from an overly narrow or arguably wrong gold parent.
- Dense retrieval actually returned better evidence than the preset gold.
- This case should add `usfa_emergency_incident_rehabilitation_fa_314__parent_00068` as a gold parent, and possibly keep `parent_00102` only for a different query about dispatching rehab resources.
- English expansion can still help with exact terms: `SCBA`, `NFPA 1584`, `work-to-rest ratio`, `formal rehab`, `medical evaluation`, `hydration`.

### dense_zh_009 / usar_hazmat_entry_safety

Query:

```text
在疑似有毒泄漏或危险品污染的坍塌现场，USAR队进入前必须检查哪些风险？
```

Gold:

```text
insarag_guidelines_2020_volume_iii_operational_field_guide__parent_00021
```

Top-10 behavior:

- rank 2 is INSARAG Volume II Manual B `Hazardous Materials Operations`.
- rank 3 is Volume III `parent_00022`, the immediate neighboring parent after the chosen gold parent.
- `parent_00022` directly covers Go/No-go conditions, PPE limitations, risk-benefit analysis, resource status, security/safety, and detection/monitoring.
- gold `parent_00021` appears at small ranks 13, 14, and 43; parent-level unique rank 12.

Root cause:

- The relevant evidence straddles a parent boundary: `parent_00021` starts the hazardous materials section, while `parent_00022` contains the more specific checklist-like items.
- The returned results are substantially relevant, especially `parent_00022`.
- The gold parent is too narrow; `parent_00022` should be included as a gold parent for this query.
- English expansion is useful: `hazmat`, `contaminated site`, `dangerous goods`, `PPE`, `go/no-go`, `risk-benefit analysis`, `detection and monitoring`, `decontamination`.
- BM25/hybrid should help precise standards/procedure terms here.

## Overall Conclusion

The four misses are not evidence that BGE-M3 dense retrieval cannot handle Chinese firefighting queries. They are mostly near misses and evaluation-design issues:

1. Single gold parent is too strict for long procedural documents.
2. Several top results are relevant or even better than the preset gold parent.
3. Current ranking is small-chunk level, so duplicate chunks from the same parent consume top-k slots.
4. Some evidence crosses parent boundaries.
5. Chinese natural-language queries need English terminology expansion for exact standard/procedure terms.
6. Dense-only ranking is weak at exact term anchoring compared with a future translated BM25/hybrid layer.

## Recommended Next Step

Before using these 10 cases as a stable baseline:

1. Revise the gold file to allow multiple `gold_parent_ids` for cases where top results are genuinely relevant:
   - `dense_zh_002`: consider adding `arxiv_1801_06819_next_best_smell_mobile_robot_gas_detection__parent_00001`.
   - `dense_zh_006`: consider adding `nist_response_robot_test_methods_guide__parent_00016` and possibly NIST USAR requirements parents.
   - `dense_zh_008`: add `usfa_emergency_incident_rehabilitation_fa_314__parent_00068`; consider replacing `parent_00102` for this specific threshold query.
   - `dense_zh_009`: add `insarag_guidelines_2020_volume_iii_operational_field_guide__parent_00022` and possibly Volume II Manual B hazardous materials parent.
2. Add or separately report parent-deduplicated retrieval metrics.
3. Add query expansion fields later, such as `query_en_terms` or `expanded_query`, before BM25/hybrid evaluation.
4. Then rerun dense-only metrics to get a fairer baseline before implementing BM25/hybrid.

## User Decision: Keep Strict Gold for Now

**Timestamp:** 2026-07-07 +08:00

The user decided not to broaden `gold_parent_ids` yet. The current strict single-gold evaluation should be preserved as a conservative baseline before later retrieval improvements.

Saved baseline for future comparison:

- `case_count`: `10`
- `hit_at_1`: `0.2`
- `hit_at_5`: `0.4`
- `hit_at_10`: `0.6`
- `mrr_at_10`: `0.323611`
- `gold_recall_at_10`: `0.6`
- official report: `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json`
- gold cases: `data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl`

Interpretation to preserve:

- Treat these metrics as strict dense-only lower-bound metrics.
- Do not count semantically related neighboring parents as correct in the official metric yet.
- Keep the miss-case analysis as qualitative error analysis only, not as a score correction.

Optimization directions the user wants to discuss before coding:

1. Query expansion through original Chinese query + English translated query + professional terminology expansion query, each retrieved separately, then merged/reranked.
2. Chunk embedding text enhancement, likely by adding title/heading/context into embedding text; open question: whether this requires rebuilding the whole dense index.
3. Parent deduplication or parent-level aggregation: small chunk retrieval -> `parent_id` dedup -> parent-level ranking -> take top-N parents for the LLM.

The user explicitly said to discuss first and not write code or implementation plans yet.

## Retrieval Optimization Discussion Direction

**Timestamp:** 2026-07-07 +08:00

The user summarized the next likely dense-retrieval optimization scope:

1. Query expansion:
   - original Chinese query;
   - English translated query;
   - professional terminology expansion query;
   - run retrieval separately for these query variants;
   - merge/rank candidates afterward.
2. Terminology expansion should initially cover only firefighting-robot topics and the previous miss cases, not the whole corpus.
3. Candidate terminology can be generated/organized by the agent, but the user should review the final glossary before it is used in official evaluation.
4. Parent deduplication / parent aggregation:
   - small chunk retrieval;
   - group or deduplicate by `parent_id`;
   - rank at parent level;
   - take top-N parents for LLM context.

Open discussion point:

- whether to add any other dense-side optimization before moving to BM25/hybrid.

Current recommendation to discuss:

- Add a small ablation matrix so each improvement can be compared against the strict dense-only baseline independently.
- Consider query-time parent aggregation first because it does not require rebuilding the dense index.
- Keep chunk embedding text enhancement as a separate later index version, because changing embedding text requires rebuilding vectors.

## Superpowers Design Spec Update

**Timestamp:** 2026-07-07 +08:00

The user interrupted an attempted direct implementation and reminded the agent to follow the Superpowers process.

Process correction:

- `brainstorming` was re-entered.
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

- Task 5 documentation and verification handoff is complete.
- The earlier local focused pytest attempt was blocked by managed Windows pytest temp directory permissions, but the controller later ran the full focused suite successfully.
- Controller final focused pytest command:

```powershell
python -m pytest --basetemp=.pytest_tmp_dense_expansion_all_controller tests/test_rag_query_expansion.py tests/test_rag_dense_ranking.py tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

- Controller result: `33 passed in 0.66s`.
- `.superpowers/sdd/query-expansion-task-5-report.md` has been written.
- `git diff --check` exit code `0`; output only LF-to-CRLF warnings for `src/fireclaw_core/rag/rag_cli.py` and `tests/test_rag_dense_cli.py`.
- `git status --short --ignored` exit code `0` but emitted many permission warnings for pre-existing `.deps`, `.pytest_tmp*`, `.tmp`, and pytest temp directories.

下一步：

- No remaining Task 5 final-check/report step. If continuing the dense retrieval work, first review the candidate JSONL caveat above and decide whether the ignored eval artifacts should be versioned or remain local.

需要运行的命令：

```powershell
No command required for Task 5 handoff completion.
```

## Final Resume Verification and Minor Cleanup

**Timestamp:** 2026-07-07 17:43:00 +08:00

当前进展：

- Resumed after the previous run appeared stuck.
- Re-ran final whole-branch review for the dense query expansion + parent aggregation work.
- Final reviewer found no Critical or Important issues.
- Reviewer found one Minor issue: duplicate `query_variants` such as `zh,zh,en` could be silently collapsed because variant hits were keyed by variant name.

已完成：

- Added a regression test:

```text
tests/test_rag_dense_eval.py::test_evaluate_dense_retriever_with_expansion_rejects_duplicate_query_variants_without_querying
```

- Added minimal validation in `src/fireclaw_core/rag/dense_eval.py`:

```text
evaluate_dense_retriever_with_expansion(...) now rejects duplicate query variant names before any retriever query is issued.
```

- Closed the final reviewer subagent after receiving the report.

Verification commands and results:

```powershell
python -m pytest --basetemp=.pytest_tmp_duplicate_variant_red tests/test_rag_dense_eval.py::test_evaluate_dense_retriever_with_expansion_rejects_duplicate_query_variants_without_querying -q
```

Result:

```text
1 failed in 0.30s
Expected RED failure: the code reached retriever.query and raised "query should not be called".
```

```powershell
python -m pytest --basetemp=.pytest_tmp_duplicate_variant_green tests/test_rag_dense_eval.py::test_evaluate_dense_retriever_with_expansion_rejects_duplicate_query_variants_without_querying -q
```

Result:

```text
1 passed in 0.19s
```

```powershell
python -m pytest --basetemp=.pytest_tmp_dense_eval_after_duplicate_variant tests/test_rag_dense_eval.py -q
```

Sandbox result:

```text
PermissionError: [WinError 5] Access is denied: '.pytest_tmp_dense_eval_after_duplicate_variant'
```

Escalated rerun result:

```text
16 passed in 0.20s
```

Final focused suite:

```powershell
python -m pytest --basetemp=.pytest_tmp_final_resume_verify_after_duplicate_variant tests/test_rag_query_expansion.py tests/test_rag_dense_ranking.py tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

Result:

```text
36 passed in 0.54s
```

Fresh BGE-M3 strict baseline command:

```powershell
$env:PYTHONPATH='src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

Result:

```text
case_count=10
hit_at_1=0.2
hit_at_5=0.4
hit_at_10=0.6
mrr_at_10=0.323611
gold_recall_at_10=0.6
```

Fresh BGE-M3 parent-only command:

```powershell
$env:PYTHONPATH='src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --ranking-view parent --small-top-k 50 --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_parent_report.json
```

Result:

```text
case_count=10
hit_at_1=0.2
hit_at_5=0.4
hit_at_10=0.8
mrr_at_10=0.350278
gold_recall_at_10=0.8
retrieval_config.ranking_view=parent
retrieval_config.fusion=none
```

Fresh BGE-M3 expanded-parent command:

```powershell
$env:PYTHONPATH='src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl --query-variants zh,en,terms --ranking-view parent --small-top-k 50 --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_expanded_parent_report.json
```

Result:

```text
case_count=10
hit_at_1=0.2
hit_at_5=0.8
hit_at_10=1.0
mrr_at_10=0.444444
gold_recall_at_10=1.0
retrieval_config.ranking_view=parent
retrieval_config.fusion=rrf
retrieval_config.query_variants=zh,en,terms
```

Final checks:

```powershell
git diff --check
```

Result:

```text
exit code 0
Only LF-to-CRLF warnings for .gitignore, src/fireclaw_core/rag/rag_cli.py, and tests/test_rag_dense_cli.py.
```

```powershell
git check-ignore data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl
```

Result:

```text
exit code 1
No output, meaning these three eval JSONL inputs are not ignored.
```

当前问题：

- No remaining Critical or Important review issues.
- One previous Minor review issue was fixed and covered by RED/GREEN regression test.
- `query_expansions_zh_v1.jsonl` and `glossary_candidates_zh_v1.jsonl` still intentionally use `status: "candidate"` until user review.
- `git status` still emits known Windows permission warnings for `.pytest_tmp*` directories; this is environment noise from pytest temp directories, not a code issue.

下一步：

- If the user wants to continue retrieval quality work, the next research step is to review the candidate glossary/translation rows before treating expanded-parent metrics as official paper evidence.
- No commit has been created, per repository instruction and user preference.

## Agent Review of Query Expansions and Glossary

**Timestamp:** 2026-07-07 18:30:00 +08:00

当前进展：

- The user asked the agent to perform the query expansion/glossary review without requiring user-side manual review.
- `data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl` has been reviewed and updated in place.
- `data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl` has been reviewed and updated in place.

审查规则：

- Keep the original strict gold file unchanged.
- Keep translations faithful to the original Chinese query.
- Keep domain-level terminology that a query expansion module could plausibly add.
- Remove overly source-specific or method-specific terms that look like answer-document leakage.
- Mark rows as `status: "reviewed"` only after the above check.
- Fill `reviewed_query_en` from the reviewed English translation so `--require-reviewed-expansions` can be used.

Removed terms:

```text
dense_zh_002: Next-Best-Smell
dense_zh_003: FlameFinder, deep metric learning
dense_zh_004: SPROUT
remote_gas_detection_core glossary entry: Next-Best-Smell
```

Data integrity checks:

```text
query_expansions_zh_v1.jsonl rows = 10
query expansion statuses = ["reviewed"]
all reviewed_query_en fields present = true
removed high-risk terms present = false

glossary_candidates_zh_v1.jsonl rows = 6
glossary statuses = ["reviewed"]
removed high-risk terms present = false
```

Focused pytest verification:

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=.pytest_tmp_reviewed_expansion_verify_escalated tests/test_rag_query_expansion.py tests/test_rag_dense_ranking.py tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

Result:

```text
36 passed in 0.54s
```

Reviewed expanded-parent BGE-M3 command:

```powershell
$env:PYTHONPATH = 'src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl --query-variants zh,en,terms --ranking-view parent --small-top-k 50 --top-k 10 --require-reviewed-expansions --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_expanded_parent_report.json
```

Result:

```text
case_count=10
hit_at_1=0.2
hit_at_5=0.8
hit_at_10=1.0
mrr_at_10=0.461111
gold_recall_at_10=1.0
retrieval_config.require_reviewed_expansions=true
retrieval_config.ranking_view=parent
retrieval_config.fusion=rrf
retrieval_config.query_variants=zh,en,terms
```

Per-case first gold rank after reviewed expansion:

```text
dense_zh_001: 3
dense_zh_002: 2
dense_zh_003: 1
dense_zh_004: 1
dense_zh_005: 2
dense_zh_006: 2
dense_zh_007: 4
dense_zh_008: 6
dense_zh_009: 4
dense_zh_010: 9
```

Current conclusion:

- The reviewed version keeps the same `Hit@10 = 1.0` and `gold_recall_at_10 = 1.0`.
- Compared with the previous candidate expanded-parent run, `MRR@10` increased from `0.444444` to `0.461111`.
- The current expanded-parent report can now be interpreted as an agent-reviewed query expansion result, not as an unreviewed candidate result.
- For paper-level claims, still disclose that the review was performed by the agent and that no human domain expert has independently certified the terms yet.

下一步：

- With Dense Retrieval Layer 1 now reviewed and logged, the next retrieval-method step is BM25 / hybrid retrieval design and evaluation.
- Keep strict dense baseline and reviewed expanded-parent reports as comparison points.
