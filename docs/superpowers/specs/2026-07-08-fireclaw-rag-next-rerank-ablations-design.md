# FireClaw RAG Next Rerank Ablations Design

## Goal

Add the next RAG evaluation ablations without changing existing indexes, chunking, embeddings, query expansions, or strict v2 labels. The work should make three questions measurable:

1. Are strict single gold parents undercounting relevant results?
2. Does reranking evidence-bearing small chunks work better than reranking long parent chunks?
3. Can rank-level fusion preserve hybrid recall while still benefiting from reranker ordering?

## Current Baseline

The current 30-case v2 reports show:

```text
hybrid baseline: Hit@1=0.366667 Hit@5=0.766667 Hit@10=0.966667 MRR@10=0.555040 Recall@10=0.966667
rerank en: Hit@1=0.333333 Hit@5=0.700000 Hit@10=0.833333 MRR@10=0.517778 Recall@10=0.833333
rerank terms: Hit@1=0.433333 Hit@5=0.800000 Hit@10=0.933333 MRR@10=0.585317 Recall@10=0.933333
rerank zh: Hit@1=0.433333 Hit@5=0.666667 Hit@10=0.866667 MRR@10=0.553783 Recall@10=0.866667
rerank multi-query rrf: Hit@1=0.433333 Hit@5=0.766667 Hit@10=0.833333 MRR@10=0.583981 Recall@10=0.833333
```

The strongest current rerank ablation is `rerank:terms`, but the plain hybrid baseline still has the best top-10 coverage.

## Design

### 1. Expanded / Graded Relevance

Create an optional relevance-judgment layer instead of modifying `dense_gold_cases_zh_v2.jsonl`.

The v3 judgment file will be JSONL with one row per judged parent:

```json
{"case_id":"dense_zh_001","parent_id":"arxiv_1910_03617_thermal_image_target_detection_firefighting__parent_00002","grade":3,"source":"strict_v2_gold","notes":"Original strict gold parent."}
```

Grades:

```text
3 = direct answer / original strict gold quality
2 = relevant supporting parent, acceptable for RAG evidence
1 = related but not enough to answer
0 = judged not relevant
```

Binary Hit/Recall/MRR metrics will treat `grade >= 2` as relevant. Reports should also include `nDCG@10` when graded judgments are provided.

This is an evaluation-layer change only. It should not overwrite v2 strict cases.

### 2. Small-Chunk Rerank Then Parent Aggregation

Add an optional rerank mode:

```text
dense zh/en/terms small hits
BM25 en/terms small hits
-> hybrid RRF over small chunks
-> rerank small chunks with selected rerank query or multi-query RRF
-> aggregate reranked chunks to parent
-> top10 parents
```

This differs from the current parent-level rerank:

```text
small hits
-> aggregate to parent
-> rerank long parent text
```

The new mode should load `small_chunks.jsonl` so the reranker scores full small-chunk text, not only `text_preview`.

### 3. Hybrid + Rerank Joint Rank Fusion

Do not add raw score weighting in the first version. Hybrid RRF scores and cross-encoder scores live on different scales.

Instead, add rank-level fusion:

```text
hybrid parent ranking
rerank parent ranking
-> RRF over the two rankings
-> final top10 parents
```

For small-chunk rerank, use the parent ranking produced after chunk rerank aggregation as the rerank ranking. This tests whether hybrid recall can be protected while still improving top ranks.

### 4. Evaluation Matrix

Run and compare these systems under both strict v2 labels and optional v3 graded relevance:

```text
hybrid baseline
rerank en
rerank terms
rerank zh
rerank multi-query rrf
small-chunk rerank en
small-chunk rerank terms
small-chunk rerank zh
small-chunk rerank multi-query rrf
joint hybrid+rerank terms
joint hybrid+multi-query rrf
joint hybrid+small-chunk terms
joint hybrid+small-chunk multi-query rrf
```

The final recommendation should distinguish:

- best recall-preserving setting;
- best top-rank setting;
- best balanced experimental setting;
- whether metric gains come from better retrieval or from broader relevance labels.

## Non-Goals

- Do not rebuild dense indexes.
- Do not rebuild BM25 indexes.
- Do not change chunking.
- Do not change existing query expansion files.
- Do not replace `dense_gold_cases_zh_v2.jsonl`.
- Do not claim agent-generated relevance labels are human gold labels.

## Research Caveat

Expanded/graded labels are an evaluation audit layer. They can reveal that strict single gold labels undercount relevant evidence, but they do not prove the retriever is stronger. Retrieval-quality claims should prioritize strict v2 results unless the v3 relevance file is manually reviewed later.
