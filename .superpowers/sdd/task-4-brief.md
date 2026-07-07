### Task 4: Real BGE-M3 Evaluation Smoke Run

**Files:**
- Generate: `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json`
- Modify: `memory/2026-07-07/fireclaw-dense-index-status-check.md`

**Interfaces:**
- Consumes:
  - `eval-dense-index` CLI command
  - `data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl`
  - existing BGE-M3 index at `data/rag/fire_rescue/indexes/dense/bge-m3`
  - local model at `.cache/models/bge-m3`
- Produces:
  - real evaluation report JSON
  - memory update with commands and observed metrics.

- [ ] **Step 1: Run all unit tests before manual smoke**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_unit_final tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

Expected:

```text
All tests pass
```

If pytest hits the known sandbox temp directory permission issue, rerun the same command with approved elevated execution and a new `--basetemp`.

- [ ] **Step 2: Run real BGE-M3 dense evaluation**

Run:

```powershell
$env:PYTHONPATH = "src"; .\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

Expected:

```text
JSON output containing case_count, hit_at_1, hit_at_5, hit_at_10, mrr_at_10, gold_recall_at_10, and per-case results.
```

Non-fatal Transformers tokenizer/cache warnings are acceptable if the command exits with code `0`.

- [ ] **Step 3: Inspect the generated report**

Run:

```powershell
Get-Content -Raw -Encoding UTF8 -LiteralPath data\rag\fire_rescue\eval\runs\dense_bge_m3_zh_v1_report.json
```

Expected:

```text
Valid JSON with 10 results.
```

Read the metrics carefully before making any quality claim. A low score is a valid result and should be reported plainly.

- [ ] **Step 4: Update memory**

Append to `memory/2026-07-07/fireclaw-dense-index-status-check.md`:

```markdown
## Dense Evaluation Harness Update

Implemented evidence-first dense retrieval evaluation for 10 Chinese task-style cases.

Commands:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_unit_final tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
$env:PYTHONPATH = "src"; .\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

Observed metrics:

- Copy the exact `case_count`, `hit_at_1`, `hit_at_5`, `hit_at_10`, `mrr_at_10`, and `gold_recall_at_10` values from `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json`.

Current conclusion:

- State whether the BGE-M3 dense index retrieved the selected gold parents well, poorly, or unevenly. Base the statement only on the observed metrics and per-case misses.

Next recommended step:

- State the next concrete retrieval-quality step based on the report, such as revising gold queries, adding Chinese sources, adding BM25, or introducing reranking.
```

- [ ] **Step 5: Final verification**

Run:

```powershell
git status --short --branch
```

Expected:

```text
Modified and untracked files only from the dense evaluation work, plus existing memory/spec files.
```

Do not claim the evaluation is complete until the unit tests and real smoke command have both been run and inspected.
