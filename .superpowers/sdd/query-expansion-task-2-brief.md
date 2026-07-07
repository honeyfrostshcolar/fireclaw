### Task 2: Dense Ranking Utilities for Parent Aggregation and RRF

**Files:**
- Create: `src/fireclaw_core/rag/dense_ranking.py`
- Create: `tests/test_rag_dense_ranking.py`
- Modify: `src/fireclaw_core/rag/dense_eval.py`
- Modify: `tests/test_rag_dense_eval.py`

**Interfaces:**
- Consumes:
  - `DenseEvalRetrievedHit` from `fireclaw_core.rag.dense_eval`.
- Produces:
  - `aggregate_hits_by_parent(hits: list[DenseEvalRetrievedHit], top_k: int, method: str = "max") -> list[DenseEvalRetrievedHit]`
  - `reciprocal_rank_fuse(hit_lists_by_variant: Mapping[str, list[DenseEvalRetrievedHit]], identity: str, top_k: int, rrf_k: int = 60) -> list[DenseEvalRetrievedHit]`

- [ ] **Step 1: Write failing tests for parent aggregation and RRF**

Create:

```python
# tests/test_rag_dense_ranking.py
from __future__ import annotations

import pytest

from fireclaw_core.rag.dense_eval import DenseEvalRetrievedHit
from fireclaw_core.rag.dense_ranking import aggregate_hits_by_parent
from fireclaw_core.rag.dense_ranking import reciprocal_rank_fuse


def _hit(rank: int, score: float, chunk_id: str, parent_id: str) -> DenseEvalRetrievedHit:
    return DenseEvalRetrievedHit(
        rank=rank,
        score=score,
        chunk_id=chunk_id,
        parent_id=parent_id,
        doc_id=f"doc_{parent_id}",
        text_preview=f"text {chunk_id}",
    )


def test_aggregate_hits_by_parent_keeps_best_child_and_resets_rank() -> None:
    hits = [
        _hit(1, 0.90, "chunk_a_1", "parent_a"),
        _hit(2, 0.88, "chunk_a_2", "parent_a"),
        _hit(3, 0.87, "chunk_b_1", "parent_b"),
        _hit(4, 0.95, "chunk_c_1", "parent_c"),
    ]

    aggregated = aggregate_hits_by_parent(hits, top_k=3, method="max")

    assert [(hit.rank, hit.parent_id, hit.chunk_id, hit.score) for hit in aggregated] == [
        (1, "parent_c", "chunk_c_1", 0.95),
        (2, "parent_a", "chunk_a_1", 0.90),
        (3, "parent_b", "chunk_b_1", 0.87),
    ]
    assert aggregated[1].child_hit_count == 2
    assert aggregated[1].child_ranks == [1, 2]


def test_aggregate_hits_by_parent_rejects_unknown_method() -> None:
    with pytest.raises(ValueError, match="Unsupported parent aggregation method: average"):
        aggregate_hits_by_parent([], top_k=10, method="average")


def test_reciprocal_rank_fuse_combines_variant_ranks_by_parent() -> None:
    zh_hits = [
        _hit(1, 0.50, "chunk_a_zh", "parent_a"),
        _hit(2, 0.49, "chunk_b_zh", "parent_b"),
    ]
    en_hits = [
        _hit(1, 0.60, "chunk_b_en", "parent_b"),
        _hit(2, 0.58, "chunk_c_en", "parent_c"),
    ]

    fused = reciprocal_rank_fuse({"zh": zh_hits, "en": en_hits}, identity="parent", top_k=3, rrf_k=60)

    assert [hit.parent_id for hit in fused] == ["parent_b", "parent_a", "parent_c"]
    assert fused[0].rank == 1
    assert fused[0].variant_ranks == {"zh": 2, "en": 1}
    assert fused[0].variant_scores == {"zh": 0.49, "en": 0.60}
    assert fused[0].fusion_score == pytest.approx((1 / 62) + (1 / 61))


def test_reciprocal_rank_fuse_rejects_unknown_identity() -> None:
    with pytest.raises(ValueError, match="Unsupported fusion identity: doc"):
        reciprocal_rank_fuse({}, identity="doc", top_k=10)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_ranking_red tests/test_rag_dense_ranking.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'fireclaw_core.rag.dense_ranking'
```

- [ ] **Step 3: Extend `DenseEvalRetrievedHit` with optional metadata**

Modify `src/fireclaw_core/rag/dense_eval.py`:

```python
@dataclass(frozen=True)
class DenseEvalRetrievedHit:
    rank: int
    score: float
    chunk_id: str
    parent_id: str
    doc_id: str
    source_file: str = ""
    page_start: int | None = None
    page_end: int | None = None
    heading: str = ""
    text_preview: str = ""
    fusion_score: float | None = None
    variant_ranks: dict[str, int] = field(default_factory=dict)
    variant_scores: dict[str, float] = field(default_factory=dict)
    child_hit_count: int | None = None
    child_ranks: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.fusion_score is None:
            data.pop("fusion_score")
        if not self.variant_ranks:
            data.pop("variant_ranks")
        if not self.variant_scores:
            data.pop("variant_scores")
        if self.child_hit_count is None:
            data.pop("child_hit_count")
        if not self.child_ranks:
            data.pop("child_ranks")
        return data
```

Add this serialization compatibility test to `tests/test_rag_dense_eval.py`:

```python
def test_dense_eval_retrieved_hit_omits_empty_optional_metadata() -> None:
    hit = DenseEvalRetrievedHit(rank=1, score=0.5, chunk_id="chunk", parent_id="parent", doc_id="doc")

    payload = hit.to_dict()

    assert "fusion_score" not in payload
    assert "variant_ranks" not in payload
    assert "variant_scores" not in payload
    assert "child_hit_count" not in payload
    assert "child_ranks" not in payload
```

- [ ] **Step 4: Implement ranking utilities**

Create:

```python
# src/fireclaw_core/rag/dense_ranking.py
from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from fireclaw_core.rag.dense_eval import DenseEvalRetrievedHit


def aggregate_hits_by_parent(
    hits: list[DenseEvalRetrievedHit],
    *,
    top_k: int,
    method: str = "max",
) -> list[DenseEvalRetrievedHit]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if method != "max":
        raise ValueError(f"Unsupported parent aggregation method: {method}")

    grouped: dict[str, list[DenseEvalRetrievedHit]] = {}
    for hit in sorted(hits, key=lambda item: item.rank):
        grouped.setdefault(hit.parent_id, []).append(hit)

    representatives: list[DenseEvalRetrievedHit] = []
    for parent_hits in grouped.values():
        best = max(parent_hits, key=lambda item: (item.score, -item.rank))
        representatives.append(
            replace(
                best,
                child_hit_count=len(parent_hits),
                child_ranks=[hit.rank for hit in parent_hits],
            )
        )

    representatives.sort(key=lambda item: (-item.score, item.rank, item.parent_id))
    return [replace(hit, rank=rank) for rank, hit in enumerate(representatives[:top_k], start=1)]


def reciprocal_rank_fuse(
    hit_lists_by_variant: Mapping[str, list[DenseEvalRetrievedHit]],
    *,
    identity: str,
    top_k: int,
    rrf_k: int = 60,
) -> list[DenseEvalRetrievedHit]:
    if identity not in {"chunk", "parent"}:
        raise ValueError(f"Unsupported fusion identity: {identity}")
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")

    items: dict[str, DenseEvalRetrievedHit] = {}
    fusion_scores: dict[str, float] = {}
    variant_ranks: dict[str, dict[str, int]] = {}
    variant_scores: dict[str, dict[str, float]] = {}

    for variant_name, hits in hit_lists_by_variant.items():
        seen_in_variant: set[str] = set()
        for hit in sorted(hits, key=lambda item: item.rank):
            key = hit.chunk_id if identity == "chunk" else hit.parent_id
            if key in seen_in_variant:
                continue
            seen_in_variant.add(key)
            items.setdefault(key, hit)
            fusion_scores[key] = fusion_scores.get(key, 0.0) + (1.0 / (rrf_k + hit.rank))
            variant_ranks.setdefault(key, {})[variant_name] = hit.rank
            variant_scores.setdefault(key, {})[variant_name] = hit.score

    ranked_keys = sorted(
        items,
        key=lambda key: (-fusion_scores[key], min(variant_ranks[key].values()), key),
    )

    fused: list[DenseEvalRetrievedHit] = []
    for rank, key in enumerate(ranked_keys[:top_k], start=1):
        hit = items[key]
        fused.append(
            replace(
                hit,
                rank=rank,
                score=fusion_scores[key],
                fusion_score=fusion_scores[key],
                variant_ranks=dict(sorted(variant_ranks[key].items())),
                variant_scores=dict(sorted(variant_scores[key].items())),
            )
        )
    return fused
```

- [ ] **Step 5: Run ranking and serialization tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_ranking_green tests/test_rag_dense_ranking.py tests/test_rag_dense_eval.py::test_dense_eval_retrieved_hit_omits_empty_optional_metadata -q
```

Expected:

```text
5 passed
```

- [ ] **Step 6: Checkpoint without committing**

Run:

```powershell
git diff -- src/fireclaw_core/rag/dense_ranking.py src/fireclaw_core/rag/dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_dense_eval.py
```

Expected:

```text
diff output shows ranking utilities, optional metadata serialization, and tests only
```
