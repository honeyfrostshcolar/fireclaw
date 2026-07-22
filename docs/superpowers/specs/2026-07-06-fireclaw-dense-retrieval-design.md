# FireClaw Dense Retrieval Design

Date: 2026-07-06

Status: Design approved in conversation; implementation not started.

## Goal

Add the first dense retrieval stage for FireClaw RAG. This stage starts after the existing index-preparation pipeline and turns `small_index_records.jsonl` into a local dense vector index that can be queried by operator or evaluation questions.

The first implementation scope is:

- model-independent dense retrieval core;
- deterministic fake embedding provider for unit tests;
- BGE-M3 embedding provider for the local research/workstation backend;
- Numpy exact search index using `vectors.npy`, `records.jsonl`, and `manifest.json`;
- CLI commands for building and querying the dense index;
- parent chunk expansion through `parent_id`.

BM25, hybrid fusion, reranking, and MiniLM/ONNX are intentionally left for later stages.

## Current Context

The existing FireClaw RAG pipeline already provides:

- PDF page extraction;
- parent and small chunk generation;
- index-input preparation;
- `data/rag/fire_rescue/index_inputs/small_index_records.jsonl`;
- `data/rag/fire_rescue/chunks/parent_chunks.jsonl`.

Dense retrieval must use only records where `indexable == true`, embed `clean_text`, preserve metadata, return small chunk hits first, and optionally expand those hits into parent chunks.

The repository guide asks agents to inspect OpenClaw analogues first when available. In this workspace, `openclaw-main/` is not present, so this design follows the existing FireClaw RAG JSONL and CLI patterns rather than an OpenClaw source implementation.

## Non-Goals

This design does not add:

- FAISS, hnswlib, or a vector database;
- BM25 or sparse retrieval;
- hybrid dense plus BM25 fusion;
- reranking;
- answer generation;
- robot-side deployment packaging;
- MiniLM ONNX support.

Those are later steps in the retrieval roadmap.

## Proposed Approach

Use a small model-independent dense retrieval core and connect real embedding models through providers.

The recommended first index is an exact Numpy matrix search:

- document vectors are stored as a two-dimensional float32 matrix;
- each row corresponds to one indexed small chunk;
- query vectors are embedded with the same provider;
- if vectors are normalized, scores are dot products equivalent to cosine similarity;
- top-k results are selected by score and returned with source metadata.

This is sufficient for the current corpus size of about 5,774 indexable small chunks. It keeps the implementation transparent and easy to test before introducing approximate indexes.

Because the dense core stores and searches vectors with Numpy, the implementation should add `numpy>=1.26` as a FireClaw dependency unless the project later chooses a different vector backend.

## Module Structure

Add `src/fireclaw_core/rag/dense_retrieval.py` for model-independent code:

- `EmbeddingModelInfo`
  - provider name;
  - model name or local model path;
  - vector dimension;
  - normalization flag;
  - optional backend details such as device, pooling, max length, query instruction, or passage instruction.

- `EmbeddingProvider`
  - protocol-like interface with `model_info` and `embed_texts(texts: list[str])`.
  - returns a two-dimensional `numpy.ndarray`.

- `DenseIndexManifest`
  - index type;
  - embedding model info;
  - source record path;
  - text field;
  - record count;
  - vector dimension;
  - normalization flag;
  - creation timestamp.

- `DenseIndexBuildReport`
  - status;
  - input path;
  - output directory;
  - indexable record count;
  - skipped record count;
  - vector count;
  - vector dimension.

- `DenseHit`
  - rank;
  - score;
  - chunk metadata;
  - optional parent metadata after expansion.

- `build_dense_index()`
  - reads JSONL records;
  - filters `indexable == true`;
  - embeds `clean_text`;
  - validates vector shape;
  - writes `vectors.npy`, `records.jsonl`, and `manifest.json`.

- `DenseRetriever`
  - loads a dense index directory;
  - validates query provider compatibility against the manifest;
  - embeds a query;
  - computes exact dense similarity;
  - returns sorted top-k hits.

- `expand_hits_to_parents()`
  - reads `parent_chunks.jsonl`;
  - matches dense hits by `parent_id`;
  - attaches parent text and parent metadata for answer grounding.

Add `src/fireclaw_core/rag/bge_m3_provider.py` for the real BGE-M3 provider:

- lazily imports `FlagEmbedding` so ordinary tests do not require BGE-M3 dependencies;
- loads `.cache/models/bge-m3` by default when requested;
- supports explicit `--model-path`;
- uses dense output only;
- records provider metadata in `EmbeddingModelInfo`.

## Index Layout

The default BGE-M3 index directory is:

```text
data/rag/fire_rescue/indexes/dense/bge-m3/
  manifest.json
  vectors.npy
  records.jsonl
```

`vectors.npy` contains the float32 dense matrix.

`records.jsonl` contains the metadata rows corresponding one-to-one with vector rows. It should preserve at least:

- `chunk_id`;
- `parent_id`;
- `doc_id`;
- `source_file`;
- `page_start`;
- `page_end`;
- `heading`;
- `clean_text`;
- `title`;
- `source_url`;
- `publisher`;
- `authority_level`;
- `allowed_use`;
- `domain`;
- `language`;
- `retrieval_weight`;
- `cleaning_flags`.

`manifest.json` is the index identity and compatibility contract. It prevents mixing BGE-M3 document vectors with MiniLM query vectors, records experimental settings, and supports reproducibility.

## CLI

Extend `src/fireclaw_core/rag/rag_cli.py` with:

```powershell
python -m fireclaw_core.rag.rag_cli build-dense-index --provider fake
python -m fireclaw_core.rag.rag_cli query-dense-index --provider fake --query "victim search in smoke"
```

For the real BGE-M3 path:

```powershell
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli build-dense-index --provider bge-m3 --model-path .cache/models/bge-m3
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli query-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --query "smoke-filled victim search"
```

Useful CLI options:

- `--corpus-root`;
- `--records`;
- `--index-dir`;
- `--provider`;
- `--model-path`;
- `--top-k`;
- `--query`;
- `--parents`;
- `--parent-chunks`;
- `--batch-size`.

The CLI should print JSON reports and JSON search results so later evaluation scripts can consume the output.

## Provider Compatibility

Dense document vectors and query vectors must be generated by the same embedding space. A retriever must reject incompatible query providers when any of the following differ:

- provider name;
- model name or model path identity;
- vector dimension;
- normalization flag;
- query or passage instruction settings when present.

This matters because BGE-M3 and MiniLM cannot share an index even if a future model pair happens to use the same dimension.

## Error Handling

Build-time failures should be explicit:

- missing input JSONL path;
- no indexable records;
- provider returns a non-2D array;
- provider vector count does not match record count;
- provider dimension does not match declared model info;
- output directory cannot be written.

Query-time failures should be explicit:

- missing `manifest.json`;
- missing `vectors.npy`;
- missing `records.jsonl`;
- vector row count does not match metadata record count;
- query provider is incompatible with the manifest;
- query embedding shape is invalid;
- parent chunk file is missing when parent expansion is requested.

## Testing Strategy

Use TDD for the implementation.

Unit tests should avoid real model dependencies by using a deterministic fake embedding provider. The fake provider should be good enough to test ranking behavior without network, GPU, PyTorch, or FlagEmbedding.

Add `tests/test_rag_dense_retrieval.py` covering:

- index building skips non-indexable records;
- `clean_text` is the text sent to the provider;
- `manifest.json`, `vectors.npy`, and `records.jsonl` are written;
- manifest records provider identity, dimension, normalization, and record count;
- retriever returns top-k hits sorted by score;
- retriever rejects incompatible provider metadata;
- parent expansion attaches matching parent chunks by `parent_id`;
- CLI can build and query with the fake provider.

BGE-M3 should have a manual smoke command rather than a normal unit test, because it depends on local model files and the `.venv-bge-m3` runtime.

## Research Impact

This design creates the first ablation point for the retrieval roadmap:

- dense-only BGE-M3 retrieval.

Keeping the index manifest explicit makes later comparisons cleaner:

- BGE-M3 dense;
- MiniLM dense;
- BM25;
- dense plus BM25;
- dense plus BM25 plus reranker.

The parent expansion step also supports later analysis of whether retrieving small chunks and answering from parent chunks improves grounding without adding too much irrelevant context.

## Open Questions For Later

- Whether to apply `retrieval_weight` directly to dense similarity scores or reserve it for later fusion/reranking.
- Whether BGE-M3 should use query and passage instructions for this corpus.
- Whether to add FAISS once corpus size grows beyond exact Numpy search.
- Whether to add a dedicated evaluation fixture with labeled FireClaw RAG queries before or after BM25.
