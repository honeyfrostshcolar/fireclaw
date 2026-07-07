# FireClaw BM25 and Hybrid Retrieval Design

## Goal

Add a lightweight, explainable BM25 retrieval layer and a hybrid Dense+BM25 evaluation path for the existing FireClaw RAG corpus, while preserving the current dense baseline and reviewed query expansion workflow.

## Scope

- Build a small-chunk BM25 inverted index from `data/rag/fire_rescue/index_inputs/small_index_records.jsonl`.
- Implement Okapi BM25 locally, without adding a new runtime dependency.
- Query BM25 with reviewed English and terminology query variants.
- Reuse the existing parent aggregation, RRF fusion, and evaluation metrics.
- Add BM25-only and hybrid evaluation reports for direct comparison with dense reports.

## Non-Goals

- Do not change dense embeddings or rebuild dense vectors.
- Do not change chunking.
- Do not broaden `gold_parent_ids`.
- Do not add Elasticsearch/Lucene.
- Do not add reranking or LLM judging in this round.
- Do not use unreviewed query expansions for official comparison runs.

## Architecture

The BM25 layer will mirror the existing dense retrieval boundary:

```text
small_index_records.jsonl
-> build-bm25-index
-> indexes/bm25/small_v1/
-> BM25Retriever.query(query, top_k)
-> list[DenseHit]
-> existing DenseEvalRetrievedHit conversion
-> existing parent aggregation and RRF
```

BM25 itself stores lexical statistics rather than vectors:

```text
token -> [(record_index, tf), ...]
doc_lengths[record_index]
avg_doc_len
doc_count
records metadata
tokenizer_config
k1, b
```

Hybrid evaluation will use rank-based fusion instead of score addition:

```text
dense zh/en/terms
-> per-method RRF

bm25 en/terms
-> per-method RRF

dense fused hits + bm25 fused hits
-> hybrid RRF
-> parent-level top-k
```

## Tokenization

The first version uses deterministic lexical tokenization:

```text
lowercase
regex extract ASCII letters and digits
split punctuation, slashes, and hyphens
remove stop words
remove one-character noise tokens unless explicitly allowed
keep domain abbreviations and numbers such as scba, ppe, nfpa, 1584, tdlas
```

This is intentionally simple and auditable. Chinese query text is not the main BM25 path because the corpus is primarily English. BM25 evaluation should use reviewed English and terminology query variants.

## Evaluation Matrix

Keep the dense reports as fixed comparison points:

```text
dense strict baseline
dense parent
dense reviewed expanded parent
```

Add:

```text
bm25 reviewed expanded parent
hybrid dense+bm25 reviewed expanded parent
```

All reports use the existing metrics:

```text
Hit@1
Hit@5
Hit@10
MRR@10
gold_recall@10
```

## Research Notes

BM25 mainly tests whether exact lexical anchoring improves recall for standards, abbreviations, and professional terms such as `SCBA`, `NFPA 1584`, `TDLAS`, `hazmat`, and `PPE`. Hybrid retrieval should be reported as an engineering retrieval improvement, not as a new research contribution by itself. The research value comes from careful ablation, miss-case analysis, and showing where semantic versus lexical retrieval fails under firefighting robotics queries.

## Approval State

The user approved planning after discussing:

- local BM25 implementation instead of a dependency;
- small-chunk index with parent aggregation;
- reviewed English and terminology query variants for BM25;
- RRF for hybrid fusion;
- BM25-only and hybrid ablations.

No commit should be created unless the user explicitly asks for one.
