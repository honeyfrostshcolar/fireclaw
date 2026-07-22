# FireClaw Dense Retrieval Evaluation Design

Date: 2026-07-07

Status: Design approved in conversation; implementation not started.

## Goal

Add the first evaluation layer for FireClaw's dense-only RAG retrieval. The goal is to measure whether the existing BGE-M3 dense index can retrieve known, high-quality evidence chunks for Chinese task-style firefighting robot questions.

This first evaluation is intentionally small:

- 10 evidence-first evaluation cases;
- Chinese task-style queries;
- dense retrieval only;
- gold evidence identified by `parent_id`;
- metrics focused on gold evidence retrieval: `Hit@1`, `Hit@5`, `Hit@10`, `MRR@10`, and `gold_recall@10`.

## Current Context

The existing dense retrieval pipeline already provides:

- `data/rag/fire_rescue/index_inputs/small_index_records.jsonl`;
- `data/rag/fire_rescue/chunks/parent_chunks.jsonl`;
- `data/rag/fire_rescue/indexes/dense/bge-m3/manifest.json`;
- `data/rag/fire_rescue/indexes/dense/bge-m3/vectors.npy`;
- `data/rag/fire_rescue/indexes/dense/bge-m3/records.jsonl`;
- `DenseRetriever.query()`, which ranks chunks by dense vector dot product;
- optional parent expansion through `expand_hits_to_parents()`.

The dense ranking algorithm is already implemented. Query vectors and chunk vectors are compared by dot product. Because the BGE-M3 vectors are normalized, this is equivalent to cosine-similarity-style ranking.

## Non-Goals

This first evaluation does not add:

- BM25;
- hybrid fusion;
- reranking;
- answer generation;
- LLM-based judging;
- full manual annotation of all 5,774 chunks;
- publication-grade complete recall over the full corpus;
- `nDCG@K` or graded `0/1/2/3` relevance judgments.

Those are later stages. The first version only checks whether known gold parent evidence can be found by the current dense retriever.

## Evaluation Strategy

Use an evidence-first gold set.

The workflow is:

1. Select 10 high-quality `parent_id` evidence chunks from the existing corpus.
2. Write one Chinese task-style query for each evidence item.
3. Store each case with its gold `parent_id`.
4. Run the BGE-M3 dense retriever for each query.
5. Inspect the top ranked small chunk hits and their `parent_id`.
6. Compute whether the gold parent appears in the top K results.

This avoids requiring the user to read all 5,774 small chunks. It also avoids using point-score thresholds. Dense scores are used only for ranking. Evaluation is based on whether the expected gold parent is retrieved.

## Query Design

Queries should be Chinese and task-oriented, because FireClaw is intended for Chinese firefighting robot operations.

Good query style:

```text
烟雾很大的房间里，机器人应该用什么传感信息辅助寻找被困人员？
```

Avoid copying source wording directly from English evidence text:

```text
thermal imaging camera locate victims in smoke-filled environments
```

The first version should include diverse firefighting robot topics, such as:

- smoke-filled victim search;
- thermal imaging;
- robot mobility or response robot test methods;
- USAR or rubble search;
- building fire protection;
- smoke movement or simulation;
- gas or odor source localization;
- firefighter safety or rehabilitation;
- confined-space rescue;
- hazardous or degraded-visibility operation.

## Evaluation Case Format

Store the first gold set as JSONL:

```text
data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl
```

Each row should follow this schema:

```json
{
  "case_id": "dense_zh_001",
  "topic": "smoke_victim_search",
  "query": "烟雾很大的房间里，机器人应该用什么传感信息辅助寻找被困人员？",
  "gold_parent_ids": ["example_parent_id"],
  "gold_chunk_ids": [],
  "expected_evidence_summary": "The gold parent should discuss victim search, smoke, low visibility, or sensing support.",
  "source_doc_id": "example_doc_id",
  "notes": "Chinese task-style query; not copied from source wording."
}
```

Rules:

- `case_id` is stable and unique.
- `query` is the text sent to the dense retriever.
- `gold_parent_ids` is required and must contain at least one parent id.
- `gold_chunk_ids` is optional in v1.
- `expected_evidence_summary` explains why the selected parent is gold evidence.
- `notes` records any caveat, such as approximate translation from an English source.

## Evidence Selection

The first 10 evidence parents should be selected from parent chunks, not from small chunks alone.

Selection criteria:

- prefer authoritative or research-relevant sources;
- avoid `too_short`, table-of-contents, cover, and front-matter evidence;
- prefer parent chunks whose text can support a real FireClaw task or safety decision;
- cover multiple domains rather than ten near-duplicates;
- keep source traceability through `doc_id`, `source_file`, and page metadata.

The implementation may include a small helper command or script to export candidate parent snippets for review, but the evaluation runner must consume a manually reviewed JSONL gold set.

## Metrics

For each query, the dense retriever returns ranked hits. Each hit has a `record.parent_id`. A hit is gold-relevant when its `parent_id` is in `gold_parent_ids`.

`Hit@K`:

- `1` if any gold parent appears in the first K hits;
- `0` otherwise.

`MRR@10`:

- `1 / rank` for the first gold hit in the top 10;
- `0` if no gold parent appears in the top 10.

`gold_recall@10`:

- number of distinct gold parent ids retrieved in top 10 divided by total gold parent ids for the case.

Aggregated report fields:

- `case_count`;
- `hit_at_1`;
- `hit_at_5`;
- `hit_at_10`;
- `mrr_at_10`;
- `gold_recall_at_10`;
- per-case query, gold ids, retrieved ids, first gold rank, and top hit summary.

In v1, if each case has exactly one `gold_parent_id`, then `Hit@10` and `gold_recall@10` are numerically identical for that case. The separate names are still useful because later cases may have multiple acceptable gold parents.

## CLI Design

Add a dense evaluation command to the existing RAG CLI or a focused evaluation module exposed through the CLI.

Recommended CLI shape:

```powershell
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index `
  --provider bge-m3 `
  --model-path .cache/models/bge-m3 `
  --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl `
  --top-k 10 `
  --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

The command should output JSON and write the same report to `--output` when provided.

## Error Handling

The evaluator should fail clearly for:

- missing case file;
- invalid JSONL;
- missing `case_id`;
- missing or empty `query`;
- missing or empty `gold_parent_ids`;
- duplicate `case_id`;
- `top_k <= 0`;
- dense index/provider incompatibility;
- malformed retriever output.

The report should include per-case misses rather than crashing when a query simply fails to retrieve gold evidence.

## Testing Strategy

Use TDD for implementation.

Unit tests should use fake retrieval results or `FakeEmbeddingProvider`, not BGE-M3. Tests must verify:

- case loading and validation;
- duplicate case id rejection;
- `Hit@K` computation;
- `MRR@K` computation;
- `gold_recall@K` computation;
- per-case report serialization;
- CLI/report wiring with a fake provider or deterministic test index.

BGE-M3 evaluation should be a manual smoke command because it depends on local model files, GPU/runtime state, and the existing generated index.

## Research Interpretation

This v1 evaluation answers a narrow but useful question:

```text
Given known high-quality evidence parents, can BGE-M3 dense retrieval recover them from Chinese task-style operator queries?
```

It does not prove full RAG quality. If results are weak, possible causes include:

- query language mismatch between Chinese queries and mostly English source documents;
- evidence text too technical or too far from operator language;
- noisy chunks;
- no query/passage instruction tuning;
- dense-only retrieval not enough for precise firefighting terminology.

These findings are still valuable because they motivate later BM25, hybrid retrieval, reranking, Chinese corpus expansion, or better multilingual/robot-side embedding comparisons.
