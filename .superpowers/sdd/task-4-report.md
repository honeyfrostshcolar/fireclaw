Status: DONE

Task: Real BGE-M3 Evaluation Smoke Run

Files generated or updated:

- `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json`
- `memory/2026-07-07/fireclaw-dense-index-status-check.md`
- `.superpowers/sdd/progress.md`

Verification:

```powershell
$env:PYTHONPATH = '.deps;src'
python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_eval_unit_resume_20260707_elevated tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

Result:

- Initial Task 4 verification before review fix: `19 passed in 0.45s`
- After review fix for `top_k > 10` metric semantics: `20 passed in 0.50s`

Review fix:

- Added regression coverage for `top_k > 10` where the gold parent appears after rank 10.
- Changed `evaluate_ranked_hits()` so `top_hits` can still contain `top_k` results for inspection, while `Hit@10`, `MRR@10`, and `gold_recall@10` only use the first 10 hits.

Real BGE-M3 evaluation:

```powershell
$env:PYTHONPATH = 'src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

Result:

- exit code: `0`
- non-fatal Transformers tokenizer/cache warnings appeared
- report written to `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json`

Observed metrics:

- `case_count`: `10`
- `hit_at_1`: `0.2`
- `hit_at_5`: `0.4`
- `hit_at_10`: `0.6`
- `mrr_at_10`: `0.323611`
- `gold_recall_at_10`: `0.6`

Per-case gold rank:

- `dense_zh_001`: rank `2`
- `dense_zh_002`: not found in top 10
- `dense_zh_003`: rank `1`
- `dense_zh_004`: rank `1`
- `dense_zh_005`: rank `2`
- `dense_zh_006`: not found in top 10
- `dense_zh_007`: rank `8`
- `dense_zh_008`: not found in top 10
- `dense_zh_009`: not found in top 10
- `dense_zh_010`: rank `9`

Self-review:

- The report has 10 results and all required metrics.
- Dense-only quality is uneven; the result should be treated as a baseline, not final RAG quality.
- The next engineering step should inspect the four miss cases before changing retrieval algorithms.

Final checkpoint:

```powershell
git status --short --branch
```

Result:

- branch: `rag-dev...origin/rag-dev`
- tracked modifications: `src/fireclaw_core/rag/rag_cli.py`, `tests/test_rag_dense_cli.py`
- untracked dense-evaluation work: `.superpowers/`, `data/rag/fire_rescue/eval/`, `docs/superpowers/...2026-07-07...`, `memory/2026-07-07/`, `src/fireclaw_core/rag/dense_eval.py`, `tests/test_rag_dense_eval.py`
- sandbox warning remained: `could not open directory '.pytest_tmp/': Permission denied`
