# Query Expansion Final Review Fix Report

## What Changed

- Added `_require_min_top_k(top_k)` to `evaluate_dense_retriever_with_expansion` so expanded evaluation rejects `top_k < 10` before any retriever query is issued.
- Kept `small_top_k` independent from `top_k`; tests still use `small_top_k=2`/`4` with final `top_k=10`.
- Updated the existing expanded parent-dedup eval test from `top_k=2` to `top_k=10`.
- Added a regression test proving expanded eval rejects `top_k < 10` without querying the retriever.
- Changed RRF representative selection so each fused identity uses the hit with the best contributing rank; ties prefer the higher original score, then deterministic `chunk_id`.
- Added an RRF regression test where `zh` finds `parent_gold` at worse rank with one chunk, `en` finds it at rank 1 with a different chunk, and the fused hit uses the `en` chunk metadata while preserving `variant_ranks` and `variant_scores`.
- Added narrow `.gitignore` exceptions for:
  - `data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl`
  - `data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl`
  - `data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl`

## RED Evidence

- `python -m pytest --basetemp=.pytest_tmp_final_review_red_dense_eval_guard tests/test_rag_dense_eval.py::test_evaluate_dense_retriever_with_expansion_rejects_top_k_below_10_without_querying -q`
  - Exit code: `1`
  - Expected failure: `AssertionError: query should not be called`, showing expanded eval queried the retriever before rejecting `top_k=5`.
- `python -m pytest --basetemp=.pytest_tmp_final_review_red_dense_ranking tests/test_rag_dense_ranking.py -q`
  - Exit code: `1`
  - Expected failure: `assert 'chunk_gold_zh' == 'chunk_gold_en'`, showing RRF kept the first variant's representative metadata instead of the best contributing variant.

## GREEN Evidence

- `python -m pytest --basetemp=.pytest_tmp_final_review_fix_dense_eval tests/test_rag_dense_eval.py -q`
  - Sandboxed run hit known Windows pytest basetemp cleanup/setup `PermissionError: [WinError 5]`.
  - Non-sandbox rerun result: `15 passed in 0.24s`.
- `python -m pytest --basetemp=.pytest_tmp_final_review_fix_dense_ranking tests/test_rag_dense_ranking.py -q`
  - Result: `5 passed in 0.19s`.

## Verification Commands / Results

- `python -m pytest --basetemp=.pytest_tmp_final_review_fix_all tests/test_rag_query_expansion.py tests/test_rag_dense_ranking.py tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q`
  - Non-sandbox result: `35 passed in 0.59s`.
- `git diff --check`
  - Exit code: `0`.
  - Output only line-ending warnings for `.gitignore`, `src/fireclaw_core/rag/rag_cli.py`, and `tests/test_rag_dense_cli.py`.
- `git check-ignore data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl`
  - Exit code: `1`.
  - No output.
- `git status --short --untracked-files=all data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl`
  - Shows all three files as `??`, confirming they are no longer ignored.
- `git check-ignore -v data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl`
  - This Git version printed the matching `!` exception rules and returned exit code `0`.
  - Plain `git check-ignore` and `git status` confirm the files are not ignored; see concern below.

## Files Changed

- `.gitignore`
- `src/fireclaw_core/rag/dense_eval.py`
- `tests/test_rag_dense_eval.py`
- `src/fireclaw_core/rag/dense_ranking.py`
- `tests/test_rag_dense_ranking.py`
- `.superpowers/sdd/query-expansion-final-review-fix-report.md`

## Concerns

- The sandboxed dense eval pytest run still hits the known managed Windows `PermissionError: [WinError 5]` around pytest basetemp cleanup/setup. The same command passed outside the sandbox.
- The requested `git check-ignore -v ...` command does not produce empty output in this Git environment because `-v` reports matching negation patterns (`!exact/path.jsonl`) and exits `0`. Plain `git check-ignore` exits `1` with no output, and `git status --untracked-files=all` lists the three eval JSONL files as untracked, so the files are effectively unignored.
