# FireClaw RAG Chunk Quality Audit

**Date:** 2026-07-05
**Status:** Completed.

## Task Goal

The user asked to inspect chunk quality before moving on to embedding/indexing.

## Audit Outputs

Generated:

- `data/rag/fire_rescue/chunks/chunk_quality_audit.json`
- `data/rag/fire_rescue/chunks/chunk_quality_audit.md`

## Corpus Counts

- Documents: `28`
- Parent chunks: `2095`
- Small chunks: `5790`

## Hard Integrity Checks

All hard structural checks passed:

- Parent JSONL parse errors: `0`
- Small JSONL parse errors: `0`
- Duplicate parent ids: `0`
- Duplicate small ids: `0`
- Missing parent refs: `0`
- Small page outside parent page range: `0`

## Length Checks

- Parent chars: min `24`, max `7720`, avg `3761.7`
- Small chars: min `24`, max `2600`, avg `1485.05`
- Short parents under `800` chars: `83`
- Short small chunks under `300` chars: `122`
- Parent chunks over `9000` chars: `0`
- Small chunks over `2600` chars: `0`

## Text Quality Findings

- Replacement-character small chunks: `0`
- Small chunks with control characters: `383`

The control-character cases are concentrated mostly in NIST FDS/Smokeview technical documents and formula-heavy papers. Examples include FDS mathematical formulas, tables, and PDF-extracted equation artifacts. This does not break source traceability, but it can pollute embedding quality.

## Current Verdict

The chunk layer is structurally usable.

Before embedding, add a lightweight cleaning/index-input layer:

- remove control characters except newline and tab;
- optionally filter or down-rank cover pages, blank-page fragments, table-of-contents fragments, and very short chunks;
- preserve original `parent_chunks.jsonl` and `small_chunks.jsonl` unchanged for citation/audit;
- use cleaned text only for embedding/search input.

## Next Recommended Step

Implement an index-preparation stage, not full vector indexing yet:

- read `small_chunks.jsonl`;
- produce cleaned/indexable records, for example `index_inputs/small_index_records.jsonl`;
- keep `chunk_id` and `parent_id` unchanged;
- add `clean_text`, `cleaning_flags`, and maybe `indexable` fields;
- then build embeddings from `clean_text`.