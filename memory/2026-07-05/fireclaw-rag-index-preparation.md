# FireClaw RAG Index Preparation Layer

**Date:** 2026-07-05
**Status:** Implemented and generated real index input records.

## Task Goal

The user asked to implement the cleaning/index-input layer discussed after chunk quality audit.

The layer must not modify original chunks. It prepares embedding-ready records from `small_chunks.jsonl` while preserving `chunk_id`, `parent_id`, source metadata, and traceability.

## Files Added / Modified

Added:

- `src/fireclaw_core/rag/index_preparation.py`
- `tests/test_rag_index_preparation.py`

Updated:

- `src/fireclaw_core/rag/rag_cli.py`

Generated:

- `data/rag/fire_rescue/index_inputs/small_index_records.jsonl`
- `data/rag/fire_rescue/index_inputs/index_preparation_report.json`

## CLI

```powershell
.\.venv\Scripts\python.exe -m fireclaw_core.rag.rag_cli prepare-index
```

Defaults:

- input: `data/rag/fire_rescue/chunks/small_chunks.jsonl`
- output dir: `data/rag/fire_rescue/index_inputs`

## Schema

Each index record includes:

- `chunk_id`
- `parent_id`
- `doc_id`
- `source_file`
- `page_start`
- `page_end`
- `heading`
- `clean_text`
- `clean_char_count`
- `clean_word_count`
- `indexable`
- `retrieval_weight`
- `cleaning_flags`
- source metadata such as `title`, `source_url`, `publisher`, `authority_level`, `allowed_use`, `domain`, `language`
- source chunk counts: `source_chunk_char_count`, `source_chunk_word_count`

## Cleaning Rules

- Remove invisible control characters except newline and tab.
- Preserve normal mathematical symbols, Greek symbols, punctuation, and formula characters.
- Collapse excessive spaces and newlines.
- Mark but do not delete:
  - `too_short`
  - `toc_like`
  - `formula_or_table_heavy`
  - `control_chars_removed`
- Set `indexable=false` for clear noise:
  - `empty_clean_text`
  - `blank_page_like`
  - `cover_page_like`
  - `header_footer_like`

Important decision after manual inspection:

- `toc_like` is no longer a blocking flag because several body chunks mentioned chapters/appendices and were too risky to exclude. It now lowers `retrieval_weight` instead.

## Validation

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_rag_extraction.py tests\test_rag_chunking.py tests\test_rag_index_preparation.py
```

Result:

- `10 passed`

## Real Corpus Output Summary

Command:

```powershell
.\.venv\Scripts\python.exe -m fireclaw_core.rag.rag_cli prepare-index
```

Report:

- status: `completed`
- source chunks: `5790`
- index records: `5790`
- indexable: `5774`
- non-indexable: `16`
- control-char records: `383`
- too-short records: `132`
- blank-page-like: `1`
- toc-like: `19`
- cover-page-like: `10`
- header-footer-like: `7`
- formula-or-table-heavy: `1010`
- average clean chars: `1484.13`

## Current Conclusion

The index input layer is ready for embedding.

For the first vector index, use only records where:

- `indexable == true`

Use `clean_text` as the embedding text. Keep `retrieval_weight` available for later reranking or score adjustment.

## Next Recommended Step

Choose and implement the embedding/vector index backend. Recommended next stage:

- local embedding model first if offline deployment is important;
- build vector index from `small_index_records.jsonl` using `clean_text`;
- retrieval returns small chunks, then expands to `parent_chunks.jsonl` using `parent_id`.