# FireClaw RAG Evaluation v2 Expansion

**Date:** 2026-07-08
**Status:** v2 local evaluation dataset generated and dense/BM25/hybrid reports produced.

## Task Goal

Expand the retrieval evaluation set from 10 cases to 30 cases before adding a reranker, so dense/BM25/hybrid comparisons are less dependent on a tiny smoke-test set.

## User Constraint

The user explicitly approved expanding to 30 cases and running the evaluation directly. The user also explicitly asked not to make code or algorithm changes without discussion first.

This run therefore changed only local/generated evaluation data and reports. No retrieval code, ranking code, fusion code, or CLI code was modified.

## Files Generated

Generated local v2 eval files:

```text
data/rag/fire_rescue/eval/dense_gold_cases_zh_v2.jsonl
data/rag/fire_rescue/eval/query_expansions_zh_v2.jsonl
```

Generated v2 report files:

```text
data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v2_expanded_parent_report.json
data/rag/fire_rescue/eval/runs/bm25_small_v2_expanded_parent_report.json
data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v2_expanded_parent_report.json
```

Temporary helper:

```text
.tmp/generate_eval_v2.py
```

Note: `.gitignore` currently ignores `*.jsonl` except the v1 eval files, so the v2 JSONL files are local ignored artifacts unless `.gitignore` is updated later after discussion.

## Added Case Coverage

The v2 set keeps the 10 v1 cases and adds cases `dense_zh_011` through `dense_zh_030`, covering:

```text
firefighter_ar_thermal_depth_interface
thermal_ir_fusion_human_detection
fire360_degraded_video_understanding
fire360_degraded_object_retrieval
thermal_radiation_field_mapping
fire_aware_navigation_safety_margin
industrial_emergency_robotic_intervention
industrial_robot_communication_limits
kg_llm_emergency_decision_limits
kg_guided_prompt_chain_reasoning
chemical_leak_personal_protection
usar_robot_deployment_categories
usar_robot_field_maintenance
usar_medical_risk_information_gathering
usar_coordination_structure
usar_team_classification_staffing
fire_apparatus_access_planning
standpipe_fire_hose_connection_risk
firefighter_rehydration_strategy
smokeview_load_smoke_slice_outputs
```

All added cases use single strict `gold_parent_ids` for this round.

## Validation Commands

Generated v2 files:

```powershell
python .tmp/generate_eval_v2.py
```

Output:

```text
wrote dense_gold_cases_zh_v2.jsonl rows=30
wrote query_expansions_zh_v2.jsonl rows=30
```

Loaded cases with the existing eval loader:

```powershell
$env:PYTHONPATH = "src"
python -c "from pathlib import Path; from fireclaw_core.rag.dense_eval import load_dense_eval_cases; cases=load_dense_eval_cases(Path('data/rag/fire_rescue/eval/dense_gold_cases_zh_v2.jsonl')); print(len(cases)); print(cases[0].case_id, cases[-1].case_id, len(cases[-1].gold_chunk_ids))"
```

Output:

```text
30
dense_zh_001 dense_zh_030 2
```

Loaded query expansions with the existing expansion loader:

```powershell
$env:PYTHONPATH = "src"
python -c "from pathlib import Path; from fireclaw_core.rag.query_expansion import load_query_expansions; ex=load_query_expansions(Path('data/rag/fire_rescue/eval/query_expansions_zh_v2.jsonl')); print(len(ex)); print(ex['dense_zh_030'].status, bool(ex['dense_zh_030'].reviewed_query_en), bool(ex['dense_zh_030'].term_query))"
```

Output:

```text
30
reviewed True True
```

## Evaluation Commands

BM25:

```powershell
$env:PYTHONPATH = "src"
python -m fireclaw_core.rag.rag_cli eval-bm25-index --index-dir data/rag/fire_rescue/indexes/bm25/small_v1 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v2.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v2.jsonl --query-variants en,terms --ranking-view parent --small-top-k 50 --top-k 10 --require-reviewed-expansions --output data/rag/fire_rescue/eval/runs/bm25_small_v2_expanded_parent_report.json
```

Dense:

```powershell
$env:PYTHONPATH = "src"
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --index-dir data/rag/fire_rescue/indexes/dense/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v2.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v2.jsonl --query-variants zh,en,terms --ranking-view parent --small-top-k 50 --top-k 10 --require-reviewed-expansions --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v2_expanded_parent_report.json
```

Hybrid:

```powershell
$env:PYTHONPATH = "src"
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-hybrid-index --provider bge-m3 --model-path .cache/models/bge-m3 --dense-index-dir data/rag/fire_rescue/indexes/dense/bge-m3 --bm25-index-dir data/rag/fire_rescue/indexes/bm25/small_v1 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v2.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v2.jsonl --dense-query-variants zh,en,terms --bm25-query-variants en,terms --small-top-k 50 --top-k 10 --require-reviewed-expansions --output data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v2_expanded_parent_report.json
```

## Metrics

```text
dense  cases=30 hit_at_1=0.266667 hit_at_5=0.8      hit_at_10=0.9      mrr_at_10=0.477037 gold_recall_at_10=0.9
bm25   cases=30 hit_at_1=0.366667 hit_at_5=0.766667 hit_at_10=0.966667 mrr_at_10=0.551005 gold_recall_at_10=0.966667
hybrid cases=30 hit_at_1=0.366667 hit_at_5=0.766667 hit_at_10=0.966667 mrr_at_10=0.55504  gold_recall_at_10=0.966667
```

Compared with v1, the larger and more varied set lowers the scores, especially dense-only. BM25 and hybrid remain stronger than dense-only on this expanded local set.

## New Case Rank Table

```text
case_id      topic                                  dense bm25 hybrid
dense_zh_011 firefighter_ar_thermal_depth_interface 5     3    3
dense_zh_012 thermal_ir_fusion_human_detection      10    3    6
dense_zh_013 fire360_degraded_video_understanding   None  6    8
dense_zh_014 fire360_degraded_object_retrieval      2     3    3
dense_zh_015 thermal_radiation_field_mapping        5     3    4
dense_zh_016 fire_aware_navigation_safety_margin    1     1    1
dense_zh_017 industrial_emergency_robotic_intervention 2  7    3
dense_zh_018 industrial_robot_communication_limits  2     2    2
dense_zh_019 kg_llm_emergency_decision_limits       4     7    6
dense_zh_020 kg_guided_prompt_chain_reasoning       4     2    2
dense_zh_021 chemical_leak_personal_protection      1     1    1
dense_zh_022 usar_robot_deployment_categories       2     None 6
dense_zh_023 usar_robot_field_maintenance           None  1    2
dense_zh_024 usar_medical_risk_information_gathering 1    1    1
dense_zh_025 usar_coordination_structure            1     1    1
dense_zh_026 usar_team_classification_staffing      1     1    1
dense_zh_027 fire_apparatus_access_planning         2     3    1
dense_zh_028 standpipe_fire_hose_connection_risk    5     6    5
dense_zh_029 firefighter_rehydration_strategy       1     1    1
dense_zh_030 smokeview_load_smoke_slice_outputs     None  5    None
```

## Misses and Discussion Points

Dense misses:

```text
dense_zh_013
dense_zh_023
dense_zh_030
```

BM25 miss:

```text
dense_zh_022
```

Hybrid miss:

```text
dense_zh_030
```

Important discussion point:

- `dense_zh_030` is retrieved by BM25 at rank 5, but hybrid final top 10 drops the gold parent. This is not automatically a code bug. It reflects current method-level RRF behavior: a BM25-only hit can be pushed below top 10 when dense contributes several related but non-gold Smokeview/FDS parents. Do not change fusion logic without discussing with the user.

Next recommended step:

1. Inspect `dense_zh_030` and decide whether the gold parent is too narrow, or whether hybrid should preserve each method's top hits before final fusion.
2. Inspect `dense_zh_013`, `dense_zh_022`, and `dense_zh_023` for query/gold quality.
3. After discussion, decide whether to update only evaluation labels, add per-method candidate preservation, or move on to reranking.
