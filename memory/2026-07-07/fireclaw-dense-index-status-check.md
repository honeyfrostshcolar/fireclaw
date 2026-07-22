# FireClaw Dense Index Status Check

**Date:** 2026-07-07
**Timestamp:** 2026-07-07 +08:00
**Status:** Dense retrieval code and existing BGE-M3 dense index verified in this session.

## Task Goal

The user paused the BM25/keyword retrieval direction and asked what the current dense index is and whether it can run normally.

## Current Dense Index

Existing BGE-M3 dense index directory:

- `data/rag/fire_rescue/indexes/dense/bge-m3/`

Files present:

- `manifest.json`
- `records.jsonl`
- `vectors.npy`

Manifest summary:

- `index_type`: `dense_numpy`
- `record_count`: `5774`
- `vector_dimension`: `1024`
- `normalized`: `true`
- embedding provider: `bge-m3`
- embedding backend: `FlagEmbedding.BGEM3FlagModel`
- model path: `.cache/models/bge-m3`
- build device recorded in manifest: `cuda:0`

## Commands Executed

Checked dense index files:

```powershell
Get-ChildItem -LiteralPath data\rag\fire_rescue\indexes\dense\bge-m3 -File | Select-Object Name,Length,LastWriteTime
Get-Content -Raw -LiteralPath data\rag\fire_rescue\indexes\dense\bge-m3\manifest.json
```

Ran dense retrieval unit and CLI tests. First sandboxed run hit the known pytest temp directory permission issue, then the elevated rerun passed:

```powershell
$env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=pytest_tmp_dense_verify_20260707_elevated tests/test_rag_dense_retrieval.py tests/test_rag_dense_cli.py
```

Result:

- `8 passed in 0.54s`

Ran real BGE-M3 query against the existing dense index:

```powershell
$env:PYTHONPATH = 'src'; .\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli query-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --query "smoke-filled victim search" --top-k 1
```

Result:

- exit code: `0`
- top hit: `nist_smv_6_11_0_user_guide__parent_00012__small_001`
- score: approximately `0.538944`
- record source: `raw/nist_smv_6_11_0_user_guide.pdf`
- non-fatal warnings appeared from Transformers tokenizer/cache deprecation paths.

## Current Conclusion

The dense retrieval layer is operational:

- model-independent dense retrieval tests pass;
- the CLI can query the stored BGE-M3 index;
- the local BGE-M3 provider can load `.cache/models/bge-m3` from `.venv-bge-m3`;
- query embedding and exact Numpy dot-product search work.

Important caveat:

- Dense-only retrieval quality is not yet ideal. The verified smoke query returns a very short Smokeview front-matter chunk marked with `cleaning_flags=["too_short"]` and `retrieval_weight=0.5`. This confirms the pipeline runs, but also shows why later retrieval-quality work should add BM25/hybrid retrieval, reranking, and/or retrieval-weight filtering.

## Next Recommended Step

If the user only wants to understand the current dense index, explain:

1. build time path: `small_index_records.jsonl -> BGE-M3 embeddings -> vectors.npy + records.jsonl + manifest.json`;
2. query time path: operator/query text -> BGE-M3 query vector -> dot-product top-k over `vectors.npy` -> small chunk hits -> optional parent expansion;
3. current status: runnable, but dense-only quality is a baseline rather than final FireClaw RAG retrieval quality.

## Dense Evaluation Harness Update

**Timestamp:** 2026-07-07 +08:00

Implemented evidence-first dense retrieval evaluation for 10 Chinese task-style cases.

Files added or modified for the evaluation harness:

- `src/fireclaw_core/rag/dense_eval.py`
- `tests/test_rag_dense_eval.py`
- `src/fireclaw_core/rag/rag_cli.py`
- `tests/test_rag_dense_cli.py`
- `data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl`
- `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json`

Verification commands:

```powershell
$env:PYTHONPATH = ".deps;src"; $bt = Join-Path $env:TEMP 'pytest_tmp_dense_eval_unit_final_controller'; python -m pytest --basetemp=$bt tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

Result:

- `19 passed in 0.50s`

Real BGE-M3 evaluation command:

```powershell
$env:PYTHONPATH = "src"; .\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

Result:

- exit code: `0`
- non-fatal Transformers tokenizer/cache warnings appeared;
- report written to `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json`.

Observed metrics:

- `case_count`: `10`
- `hit_at_1`: `0.2`
- `hit_at_5`: `0.4`
- `hit_at_10`: `0.6`
- `mrr_at_10`: `0.323611`
- `gold_recall_at_10`: `0.6`

Per-case gold parent rank:

- `dense_zh_001` / `thermal_victim_search`: rank `2`
- `dense_zh_002` / `remote_gas_source_detection`: not found in top 10
- `dense_zh_003` / `smoke_obscured_flame_detection`: rank `1`
- `dense_zh_004` / `usar_void_space_robot`: rank `1`
- `dense_zh_005` / `usar_void_entry_risk`: rank `2`
- `dense_zh_006` / `response_robot_test_methods`: not found in top 10
- `dense_zh_007` / `building_fire_service_features`: rank `8`
- `dense_zh_008` / `firefighter_rehab_thresholds`: not found in top 10
- `dense_zh_009` / `usar_hazmat_entry_safety`: not found in top 10
- `dense_zh_010` / `thermal_radiation_path_planning`: rank `9`

Current conclusion:

- The evaluation harness works and the BGE-M3 dense index can retrieve selected gold evidence for several Chinese task-style queries.
- Dense-only retrieval quality is uneven:
  - strong for thermal victim search, smoke-obscured flame detection, USAR void-space robotics, USAR void-entry risk, and thermal-radiation path planning;
  - weaker for remote gas source detection, response robot test methods, firefighter rehab thresholds, and USAR hazmat entry safety.
- `hit_at_10 = 0.6` and `mrr_at_10 = 0.323611` are useful baseline numbers, not final RAG performance.
- The Chinese query to mostly English corpus mismatch is likely one important reason for missed gold parents.

Next recommended step:

- Inspect the four miss cases and their top-10 hits before changing algorithms. This will distinguish query wording mismatch, gold-parent narrowness, chunk quality, and true dense-retrieval weakness.
- After that, the most natural retrieval-quality step is BM25 and dense+BM25 hybrid retrieval, because several miss cases involve precise terms such as `rehab`, `hazmat`, and response-robot testing standards.

## Resume Verification Update

**Timestamp:** 2026-07-07 12:19:06 +08:00

Fresh verification after context resume:

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_eval_unit_resume_20260707_elevated tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

Result:

- `19 passed in 0.45s`

The same pytest command first hit the known sandbox Windows temp cleanup permission issue, then the elevated rerun succeeded.

Fresh real BGE-M3 eval command:

```powershell
$env:PYTHONPATH = 'src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

Result:

- exit code: `0`
- report rewritten at `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json`
- metrics re-parsed from the JSON report:
  - `case_count`: `10`
  - `hit_at_1`: `0.2`
  - `hit_at_5`: `0.4`
  - `hit_at_10`: `0.6`
  - `mrr_at_10`: `0.323611`
  - `gold_recall_at_10`: `0.6`

Current conclusion remains unchanged: the dense evaluation harness is runnable and repeatable, while dense-only retrieval quality is uneven on the first 10 Chinese task-style cases.

## Dense Evaluation Review Fix

**Timestamp:** 2026-07-07 12:19 +08:00 onward

Final whole-branch review found an important metric edge case:

- `evaluate_ranked_hits(..., top_k > 10)` preserved `top_k` hits for display, but `gold_recall_at_10` and `first_gold_rank` were also looking across all retained hits.
- This could produce inconsistent reports, for example a gold parent at rank 12 would make `hit_at_10 = 0.0` but `gold_recall_at_10 = 1.0`.

Fix applied:

- Added a regression test where the gold parent appears at rank 12 while `top_k=12`.
- Updated `src/fireclaw_core/rag/dense_eval.py` so `top_hits` can preserve `top_k` results, while `Hit@1`, `Hit@5`, `Hit@10`, `MRR@10`, `first_gold_rank`, `retrieved_gold_parent_ids`, and `gold_recall_at_10` are computed only from the first 10 hits.

Fresh verification after the fix:

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_eval_review_fix_full_20260707 tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

Result:

- `20 passed in 0.50s`

Real BGE-M3 eval was rerun after the code fix:

```powershell
$env:PYTHONPATH = 'src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

Result:

- exit code: `0`
- metrics unchanged for the default `top_k=10` report:
  - `case_count`: `10`
  - `hit_at_1`: `0.2`
  - `hit_at_5`: `0.4`
  - `hit_at_10`: `0.6`
  - `mrr_at_10`: `0.323611`
  - `gold_recall_at_10`: `0.6`

Final `git status --short --branch` checkpoint:

```text
warning: could not open directory '.pytest_tmp/': Permission denied
## rag-dev...origin/rag-dev
 M src/fireclaw_core/rag/rag_cli.py
 M tests/test_rag_dense_cli.py
?? .superpowers/
?? data/rag/fire_rescue/eval/
?? docs/superpowers/plans/2026-07-07-fireclaw-dense-retrieval-evaluation.md
?? docs/superpowers/specs/2026-07-07-fireclaw-dense-retrieval-evaluation-design.md
?? memory/2026-07-07/
?? src/fireclaw_core/rag/dense_eval.py
?? tests/test_rag_dense_eval.py
```

## Dense Evaluation Walkthrough Documentation

**Timestamp:** 2026-07-07 14:47:39 +08:00

The user asked for a readable explanation/log of the dense retrieval evaluation process, especially how the 10 gold chunks, 10 Chinese queries, top-k retrieval, and metrics connect.

Added:

- `docs/rag/dense-evaluation-walkthrough.zh-CN.md`

The document explains:

- where the gold cases and real report live;
- how evidence-first gold cases are selected from parent chunks;
- how one Chinese task-style query is written for each selected parent;
- why the `gold_parent_id` is treated as the standard answer;
- how BGE-M3 dense retrieval ranks small chunks by vector dot product;
- how retrieved small chunks are matched back to gold evidence by `parent_id`;
- how `Hit@1`, `Hit@5`, `Hit@10`, `MRR@10`, and `gold_recall@10` are computed;
- the actual 10-case result table and hand calculation examples for `Hit@10`, `Hit@1`, and `MRR@10`;
- why score thresholds are not used in v1;
- why the four missed cases should be inspected before changing retrieval algorithms.

No retrieval code was changed in this documentation update.
