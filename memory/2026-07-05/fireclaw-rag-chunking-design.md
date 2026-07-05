# FireClaw RAG Chunking Design

**Date:** 2026-07-05
**Status:** Design agreed in discussion; implementation not started in this note.

## Task Goal

The user asked how to chunk the existing `pages.jsonl` corpus for a professional firefighting-robot RAG system. The goal is to design a chunking strategy before implementing the `chunks/` stage.

## Current Context

Existing source layer:

- `data/rag/fire_rescue/extracted/pages.jsonl`
- `data/rag/fire_rescue/extracted/extraction_report.json`

The extraction layer is page-level text only. It preserves PDF page numbers and document metadata. It does not yet include structured tables, figures, or semantic chunks.

## Key Design Decision

Use a hybrid chunking strategy:

- page-aware;
- hierarchical / recursive;
- heading-assisted;
- paragraph and sentence fallback;
- small chunks for embedding / vector search;
- parent chunks for final LLM answer context.

This is preferred over pure fixed-length chunking because firefighting knowledge often contains conditions, exceptions, warnings, procedures, and sensor limitations. Hard fixed-length splitting may separate a warning from the action it constrains.

This is also preferred over full semantic splitting for the first version because FireClaw is safety-critical. The first implementation should be deterministic, auditable, reproducible, and easy to trace back to source pages.

## Parent / Small Chunk Meaning

`parent_chunks.jsonl` should contain larger, relatively complete knowledge units.

`small_chunks.jsonl` should contain shorter retrieval units derived from parent chunks.

Important clarification:

- small chunk does not mean "title only";
- parent chunk does not mean "content only";
- do not summarize first and then chunk the summary;
- preserve original source text as the authoritative evidence layer.

Recommended flow:

```text
pages.jsonl
  -> parent_chunks.jsonl
  -> small_chunks.jsonl
  -> embeddings built from small_chunks.jsonl
  -> retrieval result maps small chunk back to parent chunk
  -> final answer uses parent chunk text and source metadata
```

Reasoning:

- small chunks improve retrieval precision;
- parent chunks preserve enough context for safe answers;
- `parent_id` links the two layers.

## Recursive Chunking Meaning

Recursive chunking means:

1. Try to split by high-level human-readable structure.
2. If a piece is still too long, split it by a lower-level structure.
3. Continue until the piece is within the target size.
4. Only use fixed character splitting as the final fallback.

Suggested rule priority:

For English documents:

1. chapter / section headings, e.g. `CHAPTER 1`, `Section 2.1`, `3.4 Fire Behavior`;
2. paragraph boundaries / blank lines;
3. sentence punctuation;
4. fixed character fallback.

For Chinese documents added later:

1. `第一章`, `一、`, `二、`;
2. `（一）`, `（二）`;
3. numbered headings such as `1.`, `2.`;
4. paragraph boundaries;
5. Chinese sentence punctuation such as `。`, `；`, `：`;
6. fixed character fallback.

## Recommended Sizes

Parent chunks:

- English: about `800-1500` words;
- Chinese: about `1500-3000` characters;
- normally span at most `2` pages;
- allow up to `3` pages only for continuous procedures, tables, or tightly connected sections.

Small chunks:

- English: about `200-450` words;
- Chinese: about `400-900` characters;
- overlap about `15%-25%`;
- each small chunk must include `parent_id`.

## Metadata Requirements

Each parent chunk should include:

- `parent_id`
- `doc_id`
- `source_file`
- `page_start`
- `page_end`
- `heading`
- `text`
- `char_count`
- `word_count`
- `title`
- `source_url`
- `publisher`
- `authority_level`
- `allowed_use`
- `domain`
- `language`
- `split_method`

Each small chunk should include:

- `chunk_id`
- `parent_id`
- `doc_id`
- `source_file`
- `page_start`
- `page_end`
- `heading`
- `text`
- `char_count`
- `word_count`
- `title`
- `source_url`
- `publisher`
- `authority_level`
- `allowed_use`
- `domain`
- `language`
- `split_method`

## Safety-Critical RAG Notes

FireClaw should not let retrieved text directly command robot hardware. RAG should support:

- operator-facing answers;
- planning evidence;
- safety gate checks;
- retrieval-augmented warnings;
- citations and audit logs.

RAG evidence should remain traceable to source document and page numbers.

## Proposed Next Step

Write a chunking implementation that:

1. reads `pages.jsonl`;
2. groups rows by `doc_id`;
3. builds parent chunks with page-aware recursive splitting;
4. builds small chunks from each parent chunk;
5. writes:
   - `data/rag/fire_rescue/chunks/parent_chunks.jsonl`
   - `data/rag/fire_rescue/chunks/small_chunks.jsonl`
   - `data/rag/fire_rescue/chunks/chunk_report.json`
6. adds focused tests for recursive splitting, page metadata preservation, and parent-small linkage.

