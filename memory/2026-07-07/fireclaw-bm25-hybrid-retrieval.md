# FireClaw BM25 and Hybrid Retrieval

**Date:** 2026-07-07
**Status:** Planning complete; implementation not started.

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
