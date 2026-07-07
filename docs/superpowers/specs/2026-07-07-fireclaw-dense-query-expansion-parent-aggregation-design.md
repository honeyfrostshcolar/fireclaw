# FireClaw Dense Query Expansion and Parent Aggregation Design

Date: 2026-07-07

## Goal

Improve the current dense-only RAG retrieval evaluation without changing the strict single-gold baseline or rebuilding the dense index.

This design adds two query-time improvements:

1. query expansion through Chinese query, LLM-translated English query, and reviewed professional terminology query;
2. parent-level deduplication and aggregation after small-chunk dense retrieval.

Chunk embedding text enhancement is explicitly out of scope for this round.

## Current Baseline to Preserve

The strict dense-only baseline remains:

```text
case_count = 10
Hit@1 = 0.2
Hit@5 = 0.4
Hit@10 = 0.6
MRR@10 = 0.323611
gold_recall@10 = 0.6
```

Baseline files:

```text
data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl
data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

The strict baseline must remain reproducible:

- do not broaden `gold_parent_ids` in this round;
- do not count neighboring relevant parents as correct in official metrics;
- use miss-case analysis only as qualitative explanation;
- keep the original dense evaluation mode available as the unchanged default.

## Scope

In scope:

- parent-level deduplication and aggregation over existing small-chunk hits;
- query expansion cache for LLM translation and reviewed/manual translation;
- candidate professional terminology glossary for firefighting robot topics and the previous miss cases;
- query fusion across query variants;
- evaluation report fields that make ablations comparable.

Out of scope:

- rebuilding dense vectors with enhanced document text;
- BM25 or hybrid keyword retrieval;
- reranking with a cross-encoder or LLM judge;
- changing chunking;
- changing the strict gold file;
- fully automatic corpus-wide glossary construction.

## Design Principles

The evaluation should separate three concerns:

```text
retrieval backend: existing BGE-M3 dense index
query variants: Chinese / English translation / terminology expansion
ranking view: small chunk ranking / parent aggregated ranking
```

This lets later experiments compare improvements cleanly:

```text
A. dense baseline
B. dense + parent aggregation
C. dense + query expansion
D. dense + query expansion + parent aggregation
```

## Query Expansion

### Query Expansion Cache

LLM translation is allowed, but official evaluation should not call the LLM every time. The first translation pass should be cached and then frozen for evaluation.

Default cache path:

```text
data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl
```

Each JSONL row represents one eval case:

```json
{
  "case_id": "dense_zh_008",
  "query_zh": "消防员连续作业多久、用完几瓶空呼后，必须进入rehab做补水和医疗评估？",
  "llm_query_en": "When should firefighters enter rehab for hydration and medical evaluation after continuous work or SCBA cylinder depletion?",
  "reviewed_query_en": "When must firefighters enter formal rehabilitation for hydration and medical evaluation after SCBA cylinder depletion or prolonged intense work?",
  "term_query": "SCBA self-contained breathing apparatus NFPA 1584 emergency incident rehabilitation formal rehab medical evaluation hydration work-to-rest ratio",
  "terms": [
    "SCBA",
    "self-contained breathing apparatus",
    "NFPA 1584",
    "emergency incident rehabilitation",
    "formal rehab",
    "medical evaluation",
    "hydration",
    "work-to-rest ratio"
  ],
  "status": "reviewed",
  "notes": "LLM translation reviewed for the firefighter rehab threshold case."
}
```

Field rules:

- `case_id`: must match an eval case.
- `query_zh`: copied from the eval case for review traceability.
- `llm_query_en`: initial LLM translation.
- `reviewed_query_en`: optional human/agent-reviewed translation. If non-empty, evaluation uses this instead of `llm_query_en`.
- `term_query`: whitespace-joined professional terms for a separate dense query.
- `terms`: structured term list used to build `term_query` and support review.
- `status`: `candidate` or `reviewed`.
- `notes`: optional review notes.

Evaluation-time English query selection:

```text
effective_query_en = reviewed_query_en if reviewed_query_en is non-empty
otherwise llm_query_en
```

Official evaluation should require reviewed rows when `--require-reviewed-expansions` is enabled.

### Professional Terminology Scope

The first glossary only covers firefighting robot topics and the current dense miss/error-analysis areas:

- remote gas detection;
- response robot standard test methods;
- firefighter rehabilitation;
- USAR hazardous materials entry safety;
- thermal imaging and victim search;
- thermal radiation path planning;
- void-space search and soft/vine robots;
- building fire service features.

The glossary should be small and auditable. It should prefer:

- abbreviations: `SCBA`, `PPE`, `TDLAS`;
- standard names: `NFPA 1584`, `DHS-NIST-ASTM`;
- document-native phrases: `go/no-go conditions`, `standard test methods`;
- Chinese user aliases mapped to English domain terms.

Avoid broad terms that add noise:

```text
fire
robot
safety
emergency
rescue
danger
```

### Candidate Glossary

Default candidate path:

```text
data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl
```

Example row:

```json
{
  "entry_id": "firefighter_rehab_scba",
  "category": "firefighter_rehab",
  "zh_aliases": ["空呼", "空气呼吸器"],
  "en_terms": ["SCBA", "self-contained breathing apparatus", "SCBA cylinder"],
  "source_parent_ids": ["usfa_emergency_incident_rehabilitation_fa_314__parent_00068"],
  "status": "candidate",
  "notes": "Extracted from the rehab threshold miss-case analysis."
}
```

The agent may generate and organize candidate entries, but official query expansion should only use entries reviewed by the user or explicitly marked `reviewed`.

## Query Variants

For each eval case, query expansion may produce up to three dense query variants:

```text
zh: original Chinese query from dense_gold_cases_zh_v1.jsonl
en: effective_query_en from query_expansions_zh_v1.jsonl
terms: term_query from query_expansions_zh_v1.jsonl
```

The retrieval mode should allow selecting variants:

```text
--query-variants zh
--query-variants zh,en
--query-variants zh,terms
--query-variants zh,en,terms
```

The default remains:

```text
--query-variants zh
```

This preserves the existing strict dense baseline.

## Fusion Across Query Variants

Each selected query variant is sent to the same dense retriever independently.

Raw dense scores should not be directly compared across variants. Fusion should use rank-based Reciprocal Rank Fusion.

Recommended formula:

```text
rrf_score(item) = sum(1 / (rrf_k + rank_in_variant))
```

Default:

```text
rrf_k = 60
```

Fusion item identity depends on ranking view:

- small-chunk view: `chunk_id`;
- parent view: `parent_id`.

Fused reports should preserve per-variant evidence:

```json
{
  "rank": 1,
  "score": 0.032266,
  "fusion_score": 0.032266,
  "parent_id": "parent_a",
  "chunk_id": "chunk_a_1",
  "variant_ranks": {
    "zh": 4,
    "en": 2,
    "terms": 1
  },
  "variant_scores": {
    "zh": 0.61,
    "en": 0.64,
    "terms": 0.66
  }
}
```

For compatibility with existing metric code, `score` in the final hit can be the `fusion_score` when fusion is enabled.

## Parent Aggregation

Parent aggregation is a query-time post-processing step. It does not require rebuilding the dense index.

Flow:

```text
dense query returns small_top_k small chunks
-> group hits by parent_id
-> compute parent_score
-> sort parents
-> emit one representative hit per parent
-> evaluate Hit@K / MRR@10 / gold_recall@10 against parent_id
```

Default aggregation:

```text
parent_score = max(child_chunk_score)
representative_chunk = child chunk with max score
```

Reason:

- easy to explain;
- robust when one small chunk is highly relevant;
- avoids duplicate chunks from the same parent consuming top-k slots.

The implementation should keep room for future aggregation methods:

```text
max
top2_average
rrf
```

Only `max` is required in this round.

Parent aggregation should be separately switchable:

```text
--ranking-view small
--ranking-view parent
```

Default:

```text
--ranking-view small
```

This preserves the strict dense baseline.

Recommended parent evaluation retrieval depth:

```text
small_top_k = 50
final top_k = 10
```

This avoids losing relevant parents before aggregation.

## Evaluation Reports

The existing report shape should remain compatible for the default baseline.

When query expansion or parent ranking is enabled, add metadata fields:

```json
{
  "case_count": 10,
  "hit_at_1": 0.2,
  "hit_at_5": 0.4,
  "hit_at_10": 0.6,
  "mrr_at_10": 0.323611,
  "gold_recall_at_10": 0.6,
  "retrieval_config": {
    "query_variants": ["zh", "en", "terms"],
    "fusion": "rrf",
    "rrf_k": 60,
    "ranking_view": "parent",
    "parent_aggregation": "max",
    "small_top_k": 50,
    "final_top_k": 10,
    "query_expansions_path": "data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl"
  },
  "results": []
}
```

Each case result should include:

- selected query variants;
- top hits after final ranking;
- variant ranks and scores when available;
- parent aggregation metadata when `ranking_view=parent`.

## CLI Shape

Existing command remains:

```powershell
python -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

New optional flags:

```text
--query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl
--query-variants zh,en,terms
--ranking-view parent
--small-top-k 50
--parent-aggregation max
--fusion rrf
--rrf-k 60
--require-reviewed-expansions
```

Defaults should preserve current behavior:

```text
query_variants = zh
ranking_view = small
small_top_k = top_k
fusion = none for one variant, rrf for multiple variants
parent_aggregation = max
require_reviewed_expansions = false
```

## Error Handling

The implementation should fail clearly when:

- query expansion rows contain duplicate `case_id`;
- a requested query variant is missing for a case;
- `--require-reviewed-expansions` is set and any row is not `reviewed`;
- `--ranking-view parent` is used with `small_top_k < top_k`;
- an unsupported `query_variant`, `fusion`, or `parent_aggregation` is requested.

Warnings are acceptable when:

- query expansion file contains rows for cases not in the current eval set;
- glossary candidate file contains entries still marked `candidate`, because glossary review is outside the eval command unless explicitly wired in.

## Testing Strategy

Unit tests should use fake retrievers and deterministic hits.

Test areas:

1. load query expansion JSONL and prefer `reviewed_query_en` over `llm_query_en`;
2. reject duplicate expansion `case_id`;
3. build selected query variants for a case;
4. fuse variant hit lists with RRF without comparing raw scores;
5. aggregate small hits to parent hits with `max` score;
6. evaluate parent-ranked hits against strict `gold_parent_ids`;
7. CLI default behavior remains unchanged;
8. CLI expanded mode writes `retrieval_config`.

Real BGE-M3 verification should run after unit tests:

```powershell
$env:PYTHONPATH = 'src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

Then run optional ablation reports:

```powershell
$env:PYTHONPATH = 'src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl --query-variants zh,en,terms --ranking-view parent --small-top-k 50 --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_expanded_parent_report.json
```

## Research Interpretation

This round should be framed as dense-side query-time optimization, not hybrid retrieval.

Expected claims if metrics improve:

- parent aggregation reduces small-chunk duplicate occupancy;
- English translation and terminology expansion improve cross-lingual retrieval into English technical documents;
- strict gold metrics remain comparable because the gold file is unchanged.

Claims not supported by this round:

- BM25/hybrid superiority;
- reranker effectiveness;
- final RAG answer quality;
- corpus-wide glossary completeness.

## Open Implementation Notes

- No new external dependency is required for parent aggregation or cached expansion evaluation.
- LLM translation generation may be a separate manual/offline step if no API integration exists yet.
- The first `query_expansions_zh_v1.jsonl` can be produced with LLM-assisted translation and manually reviewed before official evaluation.
- The CLI should not require network access during evaluation if the expansion cache already exists.
