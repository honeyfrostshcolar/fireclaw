# FireClaw BM25 and Hybrid Retrieval

**Date:** 2026-07-07
**Status:** Tasks 1-4 implemented and reviewed; Task 5 real ablation/report generation in progress.

## Task Goal

Add a lightweight, explainable Okapi BM25 retrieval layer and a Dense+BM25 hybrid evaluation path for the FireClaw RAG corpus.

## Current Progress

- The user asked to move from Dense Retrieval Layer 1 to BM25 / hybrid retrieval.
- We discussed BM25 conceptually:
  - BM25 uses lexical token matching, not embeddings.
  - Build-time index covers effective tokens across all small chunks.
  - Query-time lookup only checks query tokens' posting lists.
  - BM25 is valuable for exact terms such as `SCBA`, `NFPA 1584`, `TDLAS`, `hazmat`, and `PPE`.
  - Chinese queries need reviewed English translation and professional terms for BM25 because the corpus is primarily English.
- The user approved planning a local first version of BM25, without adding a dependency.

## Design Spec

Saved:

```text
docs/superpowers/specs/2026-07-07-fireclaw-bm25-hybrid-retrieval-design.md
```

Spec decisions:

- Implement BM25 locally.
- Build BM25 over `small_index_records.jsonl`.
- Use existing small chunk -> parent aggregation flow.
- Use reviewed `en` and `terms` query variants for BM25.
- Use RRF for hybrid fusion.
- Preserve dense baseline and dense reviewed expanded-parent reports.
- Do not broaden gold labels, rebuild dense vectors, or change chunking.

## Implementation Plan

Saved:

```text
docs/superpowers/plans/2026-07-07-fireclaw-bm25-hybrid-retrieval.md
```

Plan tasks:

1. BM25 tokenizer and retriever:
   - `src/fireclaw_core/rag/bm25_retrieval.py`
   - `tests/test_rag_bm25_retrieval.py`
2. BM25 expanded evaluation:
   - `src/fireclaw_core/rag/dense_eval.py`
   - `tests/test_rag_bm25_eval.py`
3. Hybrid Dense+BM25 evaluation:
   - `src/fireclaw_core/rag/hybrid_eval.py`
   - `tests/test_rag_hybrid_eval.py`
4. CLI integration:
   - `src/fireclaw_core/rag/rag_cli.py`
   - `tests/test_rag_bm25_cli.py`
5. Documentation, real BM25 index build, and ablation reports:
   - `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
   - `data/rag/fire_rescue/indexes/bm25/small_v1/`
   - `data/rag/fire_rescue/eval/runs/bm25_small_v1_expanded_parent_report.json`
   - `data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v1_expanded_parent_report.json`

## Commands Already Run

Context inspection:

```powershell
Get-ChildItem -LiteralPath memory -Directory | Select-Object -ExpandProperty Name
git status --short
rg --files src/fireclaw_core/rag tests
rg -n "class .*Retriever|def evaluate|eval-dense-index|DenseEvalRetrievedHit|reciprocal_rank_fuse|aggregate_hits_by_parent|load_query_expansions" src/fireclaw_core/rag tests
```

Relevant files inspected:

```text
src/fireclaw_core/rag/dense_eval.py
src/fireclaw_core/rag/dense_retrieval.py
src/fireclaw_core/rag/rag_cli.py
src/fireclaw_core/rag/dense_ranking.py
tests/test_rag_dense_cli.py
tests/test_rag_dense_eval.py
data/rag/fire_rescue/index_inputs/small_index_records.jsonl
data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl
```

Plan self-review command:

```powershell
rg -n "TBD|TODO|implement later|fill in details|Similar to|appropriate error|Write tests for the above" docs\superpowers\plans\2026-07-07-fireclaw-bm25-hybrid-retrieval.md docs\superpowers\specs\2026-07-07-fireclaw-bm25-hybrid-retrieval-design.md
```

Result:

```text
exit code 1, no matches
```

## Current Git Notes

- The working tree already contains many uncommitted dense-retrieval changes from the previous task.
- No commit has been created.
- `git status --short` emits known Windows permission warnings for `.pytest_tmp*` directories.

## Next Recommended Step

Ask the user to choose execution mode for the saved plan:

```text
1. Subagent-Driven
2. Inline Execution
```

Given this task has separable modules and tests, `Subagent-Driven` is recommended if available. If not, use inline execution with task-by-task checkpoints.

## Task 1 BM25 Tokenizer and Retriever Implementation

**Timestamp:** 2026-07-07 +08:00
**Status:** Task 1 complete; changes left uncommitted per user instruction.

Task goal:

- Add the local BM25 tokenizer, index builder, and retriever over existing small index records.
- Keep dense retrieval, chunking, and strict gold labels unchanged.

Files added:

- `src/fireclaw_core/rag/bm25_retrieval.py`
- `tests/test_rag_bm25_retrieval.py`
- `.superpowers/sdd/bm25-task-1-report.md`

Implementation summary:

- Added `BM25TokenizerConfig`, `BM25IndexManifest`, and `BM25IndexBuildReport`.
- Added `tokenize_for_bm25(...)` using lowercase regex tokenization, stop-word filtering, and single-letter filtering.
- Added `build_bm25_index(...)`, writing `manifest.json`, `index.json`, and `records.jsonl`.
- Added `BM25Retriever.load(...)` and `BM25Retriever.query(...) -> list[DenseHit]`.
- Reused `DenseHit`, `load_jsonl`, and `write_jsonl` from `dense_retrieval.py`.
- Default BM25 parameters are `k1=1.5` and `b=0.75`.

TDD commands and results:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_tokenizer_red tests/test_rag_bm25_retrieval.py::test_tokenize_for_bm25_keeps_domain_abbreviations_and_numbers tests/test_rag_bm25_retrieval.py::test_tokenize_for_bm25_removes_common_stop_words_and_single_letter_noise -q
```

Result: expected `ModuleNotFoundError` for missing `fireclaw_core.rag.bm25_retrieval`.

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_tokenizer_green tests/test_rag_bm25_retrieval.py::test_tokenize_for_bm25_keeps_domain_abbreviations_and_numbers tests/test_rag_bm25_retrieval.py::test_tokenize_for_bm25_removes_common_stop_words_and_single_letter_noise -q
```

Result: `2 passed in 0.03s`.

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_index_red tests/test_rag_bm25_retrieval.py -q
```

Initial sandbox run hit the known Windows pytest basetemp cleanup `PermissionError`. Escalated rerun of the same command produced the expected RED result: `3 failed, 2 passed`, with `build_bm25_index` raising `NotImplementedError`.

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_retrieval_green tests/test_rag_bm25_retrieval.py -q
```

Initial sandbox run again hit the basetemp cleanup `PermissionError`. Escalated rerun of the same command produced: `5 passed in 0.30s`.

Additional guard:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_dense_guard tests/test_rag_bm25_retrieval.py tests/test_rag_dense_retrieval.py -q
```

Initial sandbox run hit the same basetemp permission issue. Escalated rerun produced: `11 passed in 0.41s`.

Final checks:

```powershell
python -m py_compile src\fireclaw_core\rag\bm25_retrieval.py
git diff --check
git status --short --branch
```

Results:

- `py_compile`: exit code 0, no output.
- `git diff --check`: exit code 0, no output.
- `git status --short --branch`: planned untracked BM25 source/test files plus known `.pytest_tmp*` permission warnings.

Current conclusion:

- Task 1 BM25 tokenizer and retriever are implemented and unit-tested.
- Dense retrieval behavior was not changed and passed the focused guard test.
- No commits were created.

Next recommended step:

- Continue with Task 2 BM25 expanded evaluation, reusing the same `DenseHit` output shape and existing dense evaluation/report machinery.

## Task 2 BM25 Expanded Evaluation Implementation

**Timestamp:** 2026-07-07 20:42:00 +08:00
**Status:** Task 2 complete; changes left uncommitted per user instruction.

Task goal:

- Reuse the existing expanded dense evaluation flow for BM25-style retrievers.
- Add retrieval-method metadata only.
- Preserve dense behavior, strict gold labels, dense vectors, chunking, parent aggregation, and RRF semantics.

Files changed:

- `src/fireclaw_core/rag/dense_eval.py`
- `tests/test_rag_bm25_eval.py`
- `.superpowers/sdd/bm25-task-2-report.md`
- `memory/2026-07-07/fireclaw-bm25-hybrid-retrieval.md`

Implementation summary:

- Added `retrieval_method: str = "dense"` to `evaluate_dense_retriever_with_expansion(...)`.
- Added `retrieval_config["retrieval_method"]`.
- Added `evaluate_bm25_retriever_with_expansion(...)` as a thin wrapper with defaults `query_variants=("en", "terms")`, `ranking_view="parent"`, and `retrieval_method="bm25"`.
- Did not modify Task 1 BM25 retrieval code or tests.

TDD commands and results:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_eval_red tests/test_rag_bm25_eval.py -q
```

Result: expected RED failure, `ImportError: cannot import name 'evaluate_bm25_retriever_with_expansion'`.

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_eval_green tests/test_rag_bm25_eval.py -q
```

Result: `1 passed in 0.19s`.

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_eval_dense_regression tests/test_rag_dense_eval.py tests/test_rag_bm25_eval.py -q
```

Initial sandbox run hit the known managed-Windows pytest basetemp `PermissionError`. Escalated rerun of the same command produced: `17 passed in 0.24s`.

Current conclusion:

- BM25 expanded evaluation wrapper is implemented and covered by a focused RED/GREEN test.
- Existing dense evaluation tests still pass with the added default `retrieval_method="dense"` metadata.
- No commits were created.

Next recommended step:

- Continue with Task 3 hybrid Dense+BM25 evaluation, using this wrapper plus the Task 1 BM25 retriever and existing RRF/parent aggregation primitives.

## Task 3 Hybrid Dense+BM25 Evaluation Implementation

**Timestamp:** 2026-07-07 20:54:10 +08:00
**Status:** Task 3 complete with one documented brief concern; changes left uncommitted per user instruction.

Task goal:

- Add parent-level hybrid dense+BM25 evaluation that builds dense and BM25 query variants separately, fuses each method's parent lists with RRF, then fuses method-level dense/BM25 ranked parent lists with RRF.
- Preserve strict `gold_parent_ids`, dense vectors, chunking, dense behavior, and Task 1/2 changes.

Files changed:

- `src/fireclaw_core/rag/hybrid_eval.py`
- `tests/test_rag_hybrid_eval.py`
- `.superpowers/sdd/bm25-task-3-report.md`
- `memory/2026-07-07/fireclaw-bm25-hybrid-retrieval.md`

Implementation summary:

- Added `evaluate_hybrid_retrievers_with_expansion(...) -> DenseEvalReport`.
- Supports `ranking_view="parent"` only and rejects other ranking views.
- Uses `build_query_variants(...)` separately for dense and BM25.
- Converts retriever outputs with `hits_from_dense_results(...)`, aggregates by parent with `aggregate_hits_by_parent(...)`, and uses `reciprocal_rank_fuse(...)` for dense variants, BM25 variants, and final method-level hybrid fusion.
- Adds `retrieval_config["retrieval_method"] == "hybrid"` and separate `dense_query_variants` / `bm25_query_variants` metadata.
- Adds per-case query variant metadata with prefixed keys such as `dense:zh` and `bm25:terms`.

TDD commands and results:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_hybrid_eval_red tests/test_rag_hybrid_eval.py -q
```

Result: expected RED failure, `ModuleNotFoundError: No module named 'fireclaw_core.rag.hybrid_eval'`.

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_hybrid_eval_green tests/test_rag_hybrid_eval.py tests/test_rag_dense_ranking.py tests/test_rag_bm25_eval.py -q
```

Result: `7 passed in 0.20s`.

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_hybrid_eval_dense_regression tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_bm25_eval.py tests/test_rag_hybrid_eval.py -q
```

Initial sandbox run hit the known managed-Windows pytest basetemp `PermissionError`. Escalated rerun of the same command produced: `23 passed in 0.22s`.

Concern:

- The Task 3 brief sample assertion expected `variant_ranks == {"bm25": 1, "dense": 1}`. With the fake dense retriever in the same sample and required method-level RRF semantics, the gold parent is rank 2 in the dense method list. The implemented test expects `{"bm25": 1, "dense": 2}` to preserve existing `reciprocal_rank_fuse(...)` metadata semantics.

Current conclusion:

- Task 3 hybrid evaluation is implemented and covered by RED/GREEN TDD.
- Focused hybrid, BM25 eval, dense ranking, and dense eval regressions pass.
- No commits were created.

Next recommended step:

- Continue with Task 4 CLI integration for BM25/hybrid evaluation if requested by the SDD plan.

## Task 4 CLI Integration Implementation

**Timestamp:** 2026-07-07 21:11:00 +08:00
**Status:** Task 4 complete; changes left uncommitted per user instruction.

Task goal:

- Add CLI commands for BM25 build/query/eval and hybrid dense+BM25 eval.
- Preserve existing dense CLI behavior and dense reports.
- Do not broaden `gold_parent_ids`, rebuild dense vectors, change chunking, or add dependencies.

Files changed:

- `src/fireclaw_core/rag/rag_cli.py`
- `tests/test_rag_bm25_cli.py`
- `.superpowers/sdd/bm25-task-4-report.md`
- `memory/2026-07-07/fireclaw-bm25-hybrid-retrieval.md`

Implementation summary:

- Added `build-bm25-index`, `query-bm25-index`, `eval-bm25-index`, and `eval-hybrid-index`.
- Reused `_write_json_output`, `_parse_csv_arg`, `_create_embedding_provider`, and `_provider_index_name`.
- BM25 CLI defaults to `data/rag/fire_rescue/indexes/bm25/small_v1`.
- BM25 eval passes `require_reviewed_expansions` and parsed query variants to `evaluate_bm25_retriever_with_expansion`.
- Hybrid eval passes `require_reviewed_expansions`, separate dense/BM25 query variant lists, and uses the existing dense provider/index conventions.

TDD and verification:

- RED build/query CLI test: expected argparse `invalid choice: 'build-bm25-index'`.
- GREEN build/query CLI test: `1 passed in 0.26s`.
- RED eval/hybrid CLI tests: expected argparse `invalid choice` for `eval-bm25-index` and `eval-hybrid-index`.
- GREEN CLI regression command: `python -m pytest --basetemp=.pytest_tmp_bm25_cli_green tests/test_rag_bm25_cli.py tests/test_rag_dense_cli.py -q`
- Result: `7 passed in 0.70s`.
- `git diff --check`: exit code 0, only existing CRLF warnings.

Operational note:

- Managed Windows sandbox pytest runs hit the known basetemp cleanup `PermissionError`; escalated reruns were used for meaningful RED/GREEN results.
- No commits were created.

## Task 5 Documentation, Real BM25 Index, and Ablation Reports

**Timestamp:** 2026-07-07 21:25:00 +08:00
**Status:** Task 5 completed; final focused tests and whole-branch review passed.

Task goal:

- Document the BM25/hybrid evaluation flow.
- Run focused verification for BM25/hybrid code.
- Build the real small-chunk BM25 index.
- Generate BM25-only and Dense+BM25 hybrid ablation reports with reviewed query expansions.
- Compare results against existing dense reports without changing strict `gold_parent_ids`.

Files modified or generated:

- `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
- `memory/2026-07-07/fireclaw-bm25-hybrid-retrieval.md`
- `data/rag/fire_rescue/indexes/bm25/small_v1/manifest.json`
- `data/rag/fire_rescue/indexes/bm25/small_v1/index.json`
- `data/rag/fire_rescue/indexes/bm25/small_v1/records.jsonl`
- `data/rag/fire_rescue/eval/runs/bm25_small_v1_expanded_parent_report.json`
- `data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v1_expanded_parent_report.json`

BM25 implementation decisions:

- Local Okapi BM25 implementation, no Elasticsearch/Lucene/new dependency.
- Tokenizer lowercases English text, splits `[A-Za-z0-9]+`, removes configured English stop words, and drops single-letter noise.
- Hyphen/slash domain expressions are split into useful lexical pieces, for example `NFPA-1584` -> `nfpa`, `1584`, and `go/no-go` -> `go`, `no`, `go`.
- Defaults: `k1=1.5`, `b=0.75`.
- Index is built over existing small chunk records; dense vectors and chunking are unchanged.

Focused unit test command:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_hybrid_all tests/test_rag_bm25_retrieval.py tests/test_rag_bm25_eval.py tests/test_rag_hybrid_eval.py tests/test_rag_bm25_cli.py tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py tests/test_rag_dense_cli.py -q
```

Sandbox result:

```text
PermissionError: [WinError 5] Access is denied: '.pytest_tmp_bm25_hybrid_all'
```

Escalated rerun of the same focused suite:

```text
40 passed in 0.63s
```

Real BM25 index build command:

```powershell
$env:PYTHONPATH = "src"
python -m fireclaw_core.rag.rag_cli build-bm25-index --records data/rag/fire_rescue/index_inputs/small_index_records.jsonl --index-dir data/rag/fire_rescue/indexes/bm25/small_v1
```

Build result:

```text
status = completed
indexable_record_count = 5774
skipped_record_count = 16
token_count = 29069
avg_doc_len = 169.48423969518532
files:
  manifest = data/rag/fire_rescue/indexes/bm25/small_v1/manifest.json
  index = data/rag/fire_rescue/indexes/bm25/small_v1/index.json
  records = data/rag/fire_rescue/indexes/bm25/small_v1/records.jsonl
```

BM25-only eval command:

```powershell
$env:PYTHONPATH = "src"
python -m fireclaw_core.rag.rag_cli eval-bm25-index --index-dir data/rag/fire_rescue/indexes/bm25/small_v1 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl --query-variants en,terms --ranking-view parent --small-top-k 50 --top-k 10 --require-reviewed-expansions --output data/rag/fire_rescue/eval/runs/bm25_small_v1_expanded_parent_report.json
```

BM25-only metrics:

```text
case_count = 10
hit_at_1 = 0.4
hit_at_5 = 0.8
hit_at_10 = 1.0
mrr_at_10 = 0.604444
gold_recall_at_10 = 1.0
retrieval_method = bm25
query_variants = en,terms
require_reviewed_expansions = true
```

BM25 first gold ranks:

```text
dense_zh_001: 1
dense_zh_002: 2
dense_zh_003: 2
dense_zh_004: 1
dense_zh_005: 3
dense_zh_006: 1
dense_zh_007: 1
dense_zh_008: 10
dense_zh_009: 2
dense_zh_010: 9
```

Hybrid eval command:

```powershell
$env:PYTHONPATH = "src"
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-hybrid-index --provider bge-m3 --model-path .cache/models/bge-m3 --dense-index-dir data/rag/fire_rescue/indexes/dense/bge-m3 --bm25-index-dir data/rag/fire_rescue/indexes/bm25/small_v1 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl --dense-query-variants zh,en,terms --bm25-query-variants en,terms --small-top-k 50 --top-k 10 --require-reviewed-expansions --output data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v1_expanded_parent_report.json
```

Hybrid result:

```text
case_count = 10
hit_at_1 = 0.4
hit_at_5 = 0.8
hit_at_10 = 1.0
mrr_at_10 = 0.607619
gold_recall_at_10 = 1.0
retrieval_method = hybrid
dense_query_variants = zh,en,terms
bm25_query_variants = en,terms
require_reviewed_expansions = true
```

Hybrid first gold ranks:

```text
dense_zh_001: 1
dense_zh_002: 2
dense_zh_003: 2
dense_zh_004: 1
dense_zh_005: 3
dense_zh_006: 1
dense_zh_007: 1
dense_zh_008: 7
dense_zh_009: 2
dense_zh_010: 10
```

Comparison against current strict dense reports:

| setup | Hit@1 | Hit@5 | Hit@10 | MRR@10 | Gold Recall@10 |
|---|---:|---:|---:|---:|---:|
| dense strict zh small | 0.2 | 0.4 | 0.6 | 0.323611 | 0.6 |
| dense parent zh | 0.2 | 0.4 | 0.8 | 0.350278 | 0.8 |
| dense reviewed expanded parent | 0.2 | 0.8 | 1.0 | 0.461111 | 1.0 |
| BM25 reviewed en+terms parent | 0.4 | 0.8 | 1.0 | 0.604444 | 1.0 |
| hybrid reviewed dense+BM25 parent | 0.4 | 0.8 | 1.0 | 0.607619 | 1.0 |

Current conclusion:

- BM25-only strongly improves rank quality on this 10-case strict gold set compared with dense reviewed expanded parent: same `Hit@10 = 1.0`, but `Hit@1` improves from `0.2` to `0.4`, and `MRR@10` improves from `0.461111` to `0.604444`.
- Hybrid is slightly above BM25-only in `MRR@10` (`0.607619` vs `0.604444`), but the gain is small in this tiny evaluation set.
- BM25/hybrid value is visible mainly for precise terminology and standards-like queries, but this 10-case set is too small for publication-level claims.

Known limitations:

- Gold labels remain intentionally strict and single-parent in this round.
- Query expansion is agent-reviewed, not independently certified by a human domain expert.
- BM25 uses simple English regex tokenization; no stemming, phrase index, or Chinese tokenization is included.
- Hybrid uses RRF rank fusion, not a learned fusion/reranking model.
- Metrics are based on 10 cases only, so they are useful for engineering ablation, not statistically strong research claims.

Next recommended step:

- Inspect the remaining lower-ranked cases (`dense_zh_008`, `dense_zh_010`) in BM25/hybrid reports.
- Then decide whether to add BM25 phrase/stemming improvements, a larger eval set, or a reranker as the next retrieval-layer experiment.

## Final Review and Verification

**Timestamp:** 2026-07-07 21:35:00 +08:00
**Status:** Complete.

Final reviewer result:

```text
Critical findings: none
Important findings: none
Assessment: Ready to merge
```

Minor non-blocking findings:

- `token_count` in the BM25 manifest currently means vocabulary size (`len(postings)`), not total token occurrences. This does not affect ranking, but a later cleanup could rename it to `vocab_size` or add `total_token_occurrences`.
- `data/rag/fire_rescue/indexes/` is ignored by `.gitignore`, so the generated BM25 index files are local rebuildable artifacts unless force-added or copied to a tracked artifact location.

Fresh final verification:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_hybrid_final_verify tests/test_rag_bm25_retrieval.py tests/test_rag_bm25_eval.py tests/test_rag_hybrid_eval.py tests/test_rag_bm25_cli.py tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py tests/test_rag_dense_cli.py -q
```

Result:

```text
40 passed in 0.69s
```

Final `git diff --check`:

```text
exit code 0
Only LF-to-CRLF warnings for tracked text files.
```

Final report metrics confirmed from JSON files:

```text
BM25:  hit_at_1=0.4, hit_at_5=0.8, hit_at_10=1.0, mrr_at_10=0.604444, gold_recall_at_10=1.0
Hybrid: hit_at_1=0.4, hit_at_5=0.8, hit_at_10=1.0, mrr_at_10=0.607619, gold_recall_at_10=1.0
```
