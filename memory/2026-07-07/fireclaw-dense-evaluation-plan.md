# FireClaw Dense Evaluation Plan

**Date:** 2026-07-07
**Status:** Evidence-first dense retrieval evaluation spec and implementation plan written; implementation not started.

## Task Goal

The user decided to evaluate the current BGE-M3 dense retrieval layer before moving to BM25 or hybrid retrieval. After discussing candidate-pool evaluation versus evidence-first evaluation, the chosen first version is method 2: evidence-first gold set.

## Current Decision

Use method 2 as the main evaluation path:

- select 10 high-quality parent chunks as gold evidence;
- write one Chinese task-style query per evidence item;
- evaluate whether dense retrieval returns the gold `parent_id`;
- use `Hit@1`, `Hit@5`, `Hit@10`, `MRR@10`, and `gold_recall@10`;
- do not add BM25, hybrid retrieval, reranking, LLM answer generation, nDCG, or graded `0/1/2/3` relevance in v1.

## Files Added

- `docs/superpowers/specs/2026-07-07-fireclaw-dense-retrieval-evaluation-design.md`
- `docs/superpowers/plans/2026-07-07-fireclaw-dense-retrieval-evaluation.md`

## Plan Summary

The implementation plan has four tasks:

1. Add `src/fireclaw_core/rag/dense_eval.py` and `tests/test_rag_dense_eval.py`.
2. Add `eval-dense-index` to `src/fireclaw_core/rag/rag_cli.py` and CLI tests.
3. Create `data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl` with 10 evidence-first Chinese cases.
4. Run real BGE-M3 evaluation and write `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json`.

## Current Workspace State

As of this note, implementation code has not been changed yet. The plan/spec and this memory note are untracked. Existing `.pytest_tmp/` still causes a harmless `git status` permission warning in the sandbox.

## Next Recommended Step

Proceed to implementation using either:

- inline execution in this session; or
- subagent-driven task execution if multi-agent support is available and desired.

Because this is a compact, tightly coupled feature, inline execution is likely sufficient.
