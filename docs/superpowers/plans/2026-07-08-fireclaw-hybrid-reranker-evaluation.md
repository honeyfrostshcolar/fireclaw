# FireClaw Hybrid Reranker Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first reranker evaluation layer that reranks hybrid Dense+BM25 parent candidates with a pretrained cross-encoder and compares the result against the existing dense, BM25, and hybrid reports.

**Architecture:** Keep dense retrieval, BM25 retrieval, query expansion, parent aggregation, and RRF fusion unchanged. Add a focused reranker module that scores `(query, parent_text)` pairs, a rerank evaluation wrapper that reranks the hybrid parent pool, and a CLI command for reproducible reports. The first real model target is a local `FlagEmbedding.FlagReranker`-compatible BGE reranker; tests use a deterministic fake reranker and do not require a downloaded model.

**Tech Stack:** Python 3.11+, dataclasses, JSON/JSONL, existing FireClaw RAG eval dataclasses, existing `.venv-bge-m3` optional `FlagEmbedding`, pytest, local BGE-M3 dense index, local BM25 index, optional local BGE reranker model.

## Global Constraints

- User-facing replies should be in Chinese; code, commands, paths, class names, API names, data shapes, and error messages stay English.
- Do not train or fine-tune a reranker in this round.
- Do not change dense vectors, BM25 index scoring, query expansion rows, chunking, or strict `gold_parent_ids`.
- Do not silently download a reranker model. If `.cache/models/bge-reranker-v2-m3` is absent, stop before the real reranker run and ask the user about model download or local model path.
- The first official reranker eval uses hybrid parent candidates, `rerank_pool_size=50`, `final_top_k=10`, and `rerank_query_variant=en`.
- Preserve existing dense, BM25, and hybrid CLI behavior and report schema compatibility.
- Do not commit unless the user explicitly asks for a commit during execution.

---

## File Structure

- Create `src/fireclaw_core/rag/reranking.py`
  - Defines `RerankerModelInfo`, `RerankerProvider`, `FakeRerankerProvider`, `BGEFlagRerankerProvider`.
  - Loads parent texts from `parent_chunks.jsonl`.
  - Reranks `DenseEvalRetrievedHit` parent hits using full parent text.

- Create `tests/test_rag_reranking.py`
  - Unit tests for parent text loading, fake reranker scoring, metadata preservation, and query variant selection.

- Modify `src/fireclaw_core/rag/dense_eval.py`
  - Adds optional reranker metadata fields to `DenseEvalRetrievedHit`.
  - Keeps baseline serialization unchanged when fields are empty.

- Create `src/fireclaw_core/rag/rerank_eval.py`
  - Runs hybrid retrieval with a large parent pool.
  - Selects the rerank query text.
  - Reranks hybrid candidates.
  - Recomputes `Hit@1`, `Hit@5`, `Hit@10`, `MRR@10`, and `gold_recall@10`.

- Create `tests/test_rag_rerank_eval.py`
  - Unit tests for hybrid rerank evaluation using fake retrievers and fake reranker.

- Modify `src/fireclaw_core/rag/rag_cli.py`
  - Adds `eval-hybrid-rerank-index`.
  - Adds `_create_reranker_provider(...)`.
  - Writes rerank reports through the same output path pattern as existing eval commands.

- Modify `tests/test_rag_bm25_cli.py`
  - Adds CLI test for `eval-hybrid-rerank-index` with fake dense, fake BM25, and fake reranker.

- Modify `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
  - Adds a short reranker evaluation section.

- Modify or create `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`
  - Records plan, commands, model availability, metrics, known gaps, and next steps.

---

### Task 1: Reranker Metadata and Core Reranking Utilities

**Files:**
- Modify: `src/fireclaw_core/rag/dense_eval.py`
- Create: `src/fireclaw_core/rag/reranking.py`
- Create: `tests/test_rag_reranking.py`

**Interfaces:**
- Consumes:
  - `DenseEvalRetrievedHit` from `fireclaw_core.rag.dense_eval`.
  - Parent chunks JSONL rows with fields `parent_id` and `text`.
- Produces:
  - `RerankerModelInfo`
  - `RerankerProvider`
  - `FakeRerankerProvider`
  - `BGEFlagRerankerProvider`
  - `load_parent_texts(path: Path) -> dict[str, str]`
  - `select_rerank_query(query_variants: Mapping[str, str], fallback_query: str, variant: str = "en") -> str`
  - `rerank_parent_hits(query: str, hits: list[DenseEvalRetrievedHit], parent_texts: Mapping[str, str], reranker: RerankerProvider, *, top_k: int = 10, max_passage_chars: int = 6000) -> list[DenseEvalRetrievedHit]`

- [ ] **Step 1: Write failing reranking tests**

Create `tests/test_rag_reranking.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fireclaw_core.rag.dense_eval import DenseEvalRetrievedHit
from fireclaw_core.rag.reranking import FakeRerankerProvider
from fireclaw_core.rag.reranking import load_parent_texts
from fireclaw_core.rag.reranking import rerank_parent_hits
from fireclaw_core.rag.reranking import select_rerank_query


def _hit(rank: int, score: float, parent_id: str, chunk_id: str) -> DenseEvalRetrievedHit:
    return DenseEvalRetrievedHit(
        rank=rank,
        score=score,
        chunk_id=chunk_id,
        parent_id=parent_id,
        doc_id="doc",
        text_preview=f"preview {chunk_id}",
        fusion_score=score,
        variant_ranks={"hybrid": rank},
        variant_scores={"hybrid": score},
    )


def test_load_parent_texts_reads_parent_chunk_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "parent_chunks.jsonl"
    path.write_text(
        json.dumps({"parent_id": "parent_a", "text": "alpha rescue text"}, ensure_ascii=False) + "\n"
        + json.dumps({"parent_id": "parent_b", "text": "bravo SCBA text"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    texts = load_parent_texts(path)

    assert texts == {"parent_a": "alpha rescue text", "parent_b": "bravo SCBA text"}


def test_load_parent_texts_rejects_duplicate_parent_id(tmp_path: Path) -> None:
    path = tmp_path / "parent_chunks.jsonl"
    path.write_text(
        json.dumps({"parent_id": "dup", "text": "first"}, ensure_ascii=False) + "\n"
        + json.dumps({"parent_id": "dup", "text": "second"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate parent_id in parent chunks: dup"):
        load_parent_texts(path)


def test_select_rerank_query_prefers_requested_english_variant() -> None:
    query = select_rerank_query(
        {
            "dense:zh": "中文问题",
            "dense:en": "When should firefighters enter SCBA rehabilitation?",
            "bm25:terms": "SCBA rehabilitation NFPA 1584",
        },
        fallback_query="中文问题",
        variant="en",
    )

    assert query == "When should firefighters enter SCBA rehabilitation?"


def test_select_rerank_query_falls_back_to_original_query() -> None:
    query = select_rerank_query({}, fallback_query="中文问题", variant="en")

    assert query == "中文问题"


def test_rerank_parent_hits_uses_parent_text_and_preserves_base_metadata() -> None:
    parent_texts = {
        "parent_generic": "generic firefighter health information",
        "parent_gold": "SCBA rehabilitation medical evaluation NFPA 1584",
    }
    hits = [
        _hit(1, 0.90, "parent_generic", "chunk_generic"),
        _hit(2, 0.70, "parent_gold", "chunk_gold"),
    ]
    reranker = FakeRerankerProvider()

    reranked = rerank_parent_hits(
        "When should firefighters enter SCBA rehabilitation?",
        hits,
        parent_texts,
        reranker,
        top_k=2,
    )

    assert [hit.parent_id for hit in reranked] == ["parent_gold", "parent_generic"]
    assert reranked[0].rank == 1
    assert reranked[0].rerank_score > reranked[1].rerank_score
    assert reranked[0].base_rank == 2
    assert reranked[0].base_score == 0.70
    assert reranked[0].base_fusion_score == 0.70
    assert reranked[0].reranker == "fake-reranker"
    assert reranked[0].score == reranked[0].rerank_score
    assert reranked[0].variant_ranks == {"hybrid": 2}


def test_rerank_parent_hits_rejects_missing_parent_text() -> None:
    reranker = FakeRerankerProvider()

    with pytest.raises(ValueError, match="missing parent text for parent_id: parent_missing"):
        rerank_parent_hits(
            "SCBA rehabilitation",
            [_hit(1, 0.5, "parent_missing", "chunk")],
            {},
            reranker,
            top_k=1,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_reranking_red tests/test_rag_reranking.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'fireclaw_core.rag.reranking'
```

- [ ] **Step 3: Add optional reranker metadata to `DenseEvalRetrievedHit`**

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
    rerank_score: float | None = None
    base_rank: int | None = None
    base_score: float | None = None
    base_fusion_score: float | None = None
    reranker: str = ""

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
        if self.rerank_score is None:
            data.pop("rerank_score")
        if self.base_rank is None:
            data.pop("base_rank")
        if self.base_score is None:
            data.pop("base_score")
        if self.base_fusion_score is None:
            data.pop("base_fusion_score")
        if not self.reranker:
            data.pop("reranker")
        return data
```

- [ ] **Step 4: Implement core reranking module**

Create `src/fireclaw_core/rag/reranking.py`:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
import os
from pathlib import Path
import re
from typing import Protocol

from fireclaw_core.rag.dense_eval import DenseEvalRetrievedHit
from fireclaw_core.rag.dense_retrieval import load_jsonl


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class RerankerModelInfo:
    provider: str
    model: str
    backend: str
    device: str | None = None
    batch_size: int = 32
    max_length: int = 512

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class RerankerProvider(Protocol):
    model_info: RerankerModelInfo

    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        raise NotImplementedError


class FakeRerankerProvider:
    def __init__(self) -> None:
        self.model_info = RerankerModelInfo(
            provider="fake-reranker",
            model="fake-token-overlap-v1",
            backend="deterministic-token-overlap",
            batch_size=32,
            max_length=512,
        )

    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        scores: list[float] = []
        for query, passage in pairs:
            query_tokens = _token_set(query)
            passage_tokens = _token_set(passage)
            if not query_tokens:
                scores.append(0.0)
                continue
            overlap = query_tokens & passage_tokens
            scores.append(len(overlap) / len(query_tokens))
        return scores


class BGEFlagRerankerProvider:
    def __init__(
        self,
        model_path: Path,
        *,
        device: str | None = None,
        batch_size: int = 32,
        max_length: int = 512,
        use_fp16: bool | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.device = device
        self.batch_size = batch_size
        self.max_length = max_length
        self.use_fp16 = use_fp16
        self.model_info = RerankerModelInfo(
            provider="bge-reranker",
            model=str(self.model_path),
            backend="FlagEmbedding.FlagReranker",
            device=device or "auto",
            batch_size=batch_size,
            max_length=max_length,
        )
        self._model: object | None = None

    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        model = self._load_model()
        raw_scores = model.compute_score(list(pairs), batch_size=self.batch_size, max_length=self.max_length)
        if isinstance(raw_scores, float):
            return [float(raw_scores)]
        return [float(score) for score in raw_scores]

    def _load_model(self):
        if self._model is None:
            if not self.model_path.exists():
                raise FileNotFoundError(f"BGE reranker model path not found: {self.model_path}")
            cache_dir = _huggingface_cache_dir_for(self.model_path)
            _set_huggingface_cache_env(cache_dir)
            try:
                import torch
                from FlagEmbedding import FlagReranker
            except ImportError as exc:
                raise ImportError(
                    "FlagEmbedding and torch are required for --reranker-provider bge-reranker. "
                    "Use .\\.venv-bge-m3\\Scripts\\python.exe."
                ) from exc

            if self.device is not None:
                devices: str | list[str] = [self.device]
            else:
                devices = ["cuda:0"] if torch.cuda.is_available() else ["cpu"]
            use_fp16 = self.use_fp16
            if use_fp16 is None:
                use_fp16 = bool(devices and str(devices[0]).startswith("cuda"))
            self.model_info = replace(self.model_info, device=",".join(str(item) for item in devices))
            self._model = FlagReranker(
                str(self.model_path),
                use_fp16=use_fp16,
                devices=devices,
                batch_size=self.batch_size,
                max_length=self.max_length,
                cache_dir=str(cache_dir),
            )
        return self._model


def load_parent_texts(path: Path) -> dict[str, str]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Parent chunks JSONL not found: {path}")
    texts: dict[str, str] = {}
    for row in load_jsonl(path):
        parent_id = str(row.get("parent_id") or "").strip()
        if not parent_id:
            raise ValueError(f"missing parent_id in parent chunks: {path}")
        if parent_id in texts:
            raise ValueError(f"duplicate parent_id in parent chunks: {parent_id}")
        texts[parent_id] = str(row.get("text") or "")
    return texts


def select_rerank_query(
    query_variants: Mapping[str, str],
    *,
    fallback_query: str,
    variant: str = "en",
) -> str:
    if variant == "zh":
        return fallback_query
    preferred_keys = [f"dense:{variant}", f"bm25:{variant}", variant]
    for key in preferred_keys:
        text = str(query_variants.get(key) or "").strip()
        if text:
            return text
    return fallback_query


def rerank_parent_hits(
    query: str,
    hits: list[DenseEvalRetrievedHit],
    parent_texts: Mapping[str, str],
    reranker: RerankerProvider,
    *,
    top_k: int = 10,
    max_passage_chars: int = 6000,
) -> list[DenseEvalRetrievedHit]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if max_passage_chars <= 0:
        raise ValueError("max_passage_chars must be positive")

    pairs: list[tuple[str, str]] = []
    for hit in hits:
        parent_text = parent_texts.get(hit.parent_id)
        if parent_text is None:
            raise ValueError(f"missing parent text for parent_id: {hit.parent_id}")
        pairs.append((query, parent_text[:max_passage_chars]))

    scores = reranker.score_pairs(pairs)
    if len(scores) != len(hits):
        raise ValueError(f"reranker returned {len(scores)} scores for {len(hits)} hits")

    scored = list(zip(hits, scores, strict=True))
    scored.sort(key=lambda item: (-float(item[1]), item[0].rank, item[0].parent_id))
    reranked: list[DenseEvalRetrievedHit] = []
    for new_rank, (hit, score) in enumerate(scored[:top_k], start=1):
        reranked.append(
            replace(
                hit,
                rank=new_rank,
                score=float(score),
                rerank_score=float(score),
                base_rank=hit.rank,
                base_score=hit.score,
                base_fusion_score=hit.fusion_score,
                reranker=reranker.model_info.provider,
            )
        )
    return reranked


def _token_set(text: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)}


def _huggingface_cache_dir_for(model_path: Path) -> Path:
    model_path = Path(model_path)
    if len(model_path.parents) >= 2:
        return model_path.parents[1] / "huggingface"
    return Path(".cache") / "huggingface"


def _set_huggingface_cache_env(cache_dir: Path) -> None:
    cache_dir = Path(cache_dir)
    transformers_dir = cache_dir / "transformers"
    hub_dir = cache_dir / "hub"
    transformers_dir.mkdir(parents=True, exist_ok=True)
    hub_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache_dir))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(transformers_dir))
    os.environ.setdefault("HF_HUB_CACHE", str(hub_dir))
```

- [ ] **Step 5: Run reranking tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_reranking_green tests/test_rag_reranking.py tests/test_rag_dense_eval.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 6: Checkpoint without committing**

Run:

```powershell
git diff -- src/fireclaw_core/rag/dense_eval.py src/fireclaw_core/rag/reranking.py tests/test_rag_reranking.py
```

Expected:

```text
diff output shows only reranker metadata, reranking utilities, and focused tests
```

---

### Task 2: Hybrid Rerank Evaluation Wrapper

**Files:**
- Create: `src/fireclaw_core/rag/rerank_eval.py`
- Create: `tests/test_rag_rerank_eval.py`

**Interfaces:**
- Consumes:
  - `evaluate_hybrid_retrievers_with_expansion(...)`
  - `evaluate_ranked_hits(...)`
  - `select_rerank_query(...)`
  - `rerank_parent_hits(...)`
- Produces:
  - `evaluate_hybrid_retrievers_with_rerank(...) -> DenseEvalReport`

- [ ] **Step 1: Write failing hybrid rerank eval test**

Create `tests/test_rag_rerank_eval.py`:

```python
from __future__ import annotations

from fireclaw_core.rag.dense_eval import DenseEvalCase
from fireclaw_core.rag.dense_retrieval import DenseHit
from fireclaw_core.rag.query_expansion import QueryExpansion
from fireclaw_core.rag.reranking import FakeRerankerProvider


def test_hybrid_rerank_eval_promotes_more_relevant_parent() -> None:
    from fireclaw_core.rag.rerank_eval import evaluate_hybrid_retrievers_with_rerank

    class FakeDenseRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(
                    rank=1,
                    score=0.9,
                    record={"chunk_id": "dense_generic", "parent_id": "parent_generic", "doc_id": "doc"},
                ),
                DenseHit(
                    rank=2,
                    score=0.8,
                    record={"chunk_id": "dense_gold", "parent_id": "parent_gold", "doc_id": "doc"},
                ),
            ]

    class FakeBM25Retriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(
                    rank=1,
                    score=3.0,
                    record={"chunk_id": "bm25_generic", "parent_id": "parent_generic", "doc_id": "doc"},
                ),
                DenseHit(
                    rank=2,
                    score=2.0,
                    record={"chunk_id": "bm25_gold", "parent_id": "parent_gold", "doc_id": "doc"},
                ),
            ]

    case = DenseEvalCase(
        case_id="case",
        topic="rehab",
        query="中文问题",
        gold_parent_ids=["parent_gold"],
    )
    expansions = {
        "case": QueryExpansion(
            case_id="case",
            query_zh="中文问题",
            reviewed_query_en="When should firefighters enter SCBA rehabilitation?",
            term_query="SCBA rehabilitation NFPA 1584",
            terms=["SCBA", "rehabilitation", "NFPA 1584"],
            status="reviewed",
        )
    }
    parent_texts = {
        "parent_generic": "generic firefighter health information",
        "parent_gold": "SCBA rehabilitation medical evaluation NFPA 1584",
    }

    report = evaluate_hybrid_retrievers_with_rerank(
        FakeDenseRetriever(),
        FakeBM25Retriever(),
        FakeRerankerProvider(),
        [case],
        query_expansions=expansions,
        parent_texts=parent_texts,
        dense_query_variants=["zh", "en"],
        bm25_query_variants=["en", "terms"],
        rerank_pool_size=10,
        top_k=10,
        require_reviewed_expansions=True,
    )

    assert report.hit_at_1 == 1.0
    assert report.results[0].top_hits[0].parent_id == "parent_gold"
    assert report.results[0].top_hits[0].base_rank == 2
    assert report.results[0].top_hits[0].reranker == "fake-reranker"
    assert report.results[0].query_variants["rerank:en"] == "When should firefighters enter SCBA rehabilitation?"
    assert report.retrieval_config is not None
    assert report.retrieval_config["retrieval_method"] == "hybrid_rerank"
    assert report.retrieval_config["candidate_retrieval_method"] == "hybrid"
    assert report.retrieval_config["rerank_pool_size"] == 10
    assert report.retrieval_config["final_top_k"] == 10
    assert report.retrieval_config["reranker"]["provider"] == "fake-reranker"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_eval_red tests/test_rag_rerank_eval.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'fireclaw_core.rag.rerank_eval'
```

- [ ] **Step 3: Implement hybrid rerank eval**

Create `src/fireclaw_core/rag/rerank_eval.py`:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from fireclaw_core.rag.dense_eval import DenseEvalCase
from fireclaw_core.rag.dense_eval import DenseEvalReport
from fireclaw_core.rag.dense_eval import DenseEvalRetrievedHit
from fireclaw_core.rag.dense_eval import evaluate_ranked_hits
from fireclaw_core.rag.hybrid_eval import evaluate_hybrid_retrievers_with_expansion
from fireclaw_core.rag.query_expansion import QueryExpansion
from fireclaw_core.rag.reranking import RerankerProvider
from fireclaw_core.rag.reranking import rerank_parent_hits
from fireclaw_core.rag.reranking import select_rerank_query


def evaluate_hybrid_retrievers_with_rerank(
    dense_retriever: Any,
    bm25_retriever: Any,
    reranker: RerankerProvider,
    cases: list[DenseEvalCase],
    *,
    query_expansions: Mapping[str, QueryExpansion] | None = None,
    parent_texts: Mapping[str, str],
    dense_query_variants: Sequence[str] = ("zh", "en", "terms"),
    bm25_query_variants: Sequence[str] = ("en", "terms"),
    rerank_query_variant: str = "en",
    rerank_pool_size: int = 50,
    top_k: int = 10,
    small_top_k: int = 50,
    parent_aggregation: str = "max",
    rrf_k: int = 60,
    max_passage_chars: int = 6000,
    require_reviewed_expansions: bool = False,
    query_expansions_path: str | None = None,
    parent_chunks_path: str | None = None,
) -> DenseEvalReport:
    if rerank_pool_size < top_k:
        raise ValueError("rerank_pool_size must be greater than or equal to top_k")
    if top_k < 10:
        raise ValueError("top_k must be at least 10")

    hybrid_report = evaluate_hybrid_retrievers_with_expansion(
        dense_retriever,
        bm25_retriever,
        cases,
        query_expansions=query_expansions,
        dense_query_variants=dense_query_variants,
        bm25_query_variants=bm25_query_variants,
        ranking_view="parent",
        top_k=rerank_pool_size,
        small_top_k=small_top_k,
        parent_aggregation=parent_aggregation,
        rrf_k=rrf_k,
        require_reviewed_expansions=require_reviewed_expansions,
        query_expansions_path=query_expansions_path,
    )

    case_by_id = {case.case_id: case for case in cases}
    reranked_by_case_id: dict[str, list[DenseEvalRetrievedHit]] = {}
    query_variants_by_case_id: dict[str, dict[str, str]] = {}
    for result in hybrid_report.results:
        case = case_by_id[result.case_id]
        rerank_query = select_rerank_query(
            result.query_variants,
            fallback_query=case.query,
            variant=rerank_query_variant,
        )
        reranked_by_case_id[result.case_id] = rerank_parent_hits(
            rerank_query,
            result.top_hits,
            parent_texts,
            reranker,
            top_k=top_k,
            max_passage_chars=max_passage_chars,
        )
        query_variants_by_case_id[result.case_id] = {
            **result.query_variants,
            f"rerank:{rerank_query_variant}": rerank_query,
        }

    report = evaluate_ranked_hits(cases, reranked_by_case_id, top_k=max(top_k, 10))
    enriched_results = [
        replace(result, query_variants=query_variants_by_case_id.get(result.case_id, {}))
        for result in report.results
    ]
    return replace(
        report,
        results=enriched_results,
        retrieval_config={
            "retrieval_method": "hybrid_rerank",
            "candidate_retrieval_method": "hybrid",
            "dense_query_variants": list(dense_query_variants),
            "bm25_query_variants": list(bm25_query_variants),
            "rerank_query_variant": rerank_query_variant,
            "fusion": "rrf",
            "rrf_k": rrf_k,
            "ranking_view": "parent",
            "parent_aggregation": parent_aggregation,
            "small_top_k": small_top_k,
            "rerank_pool_size": rerank_pool_size,
            "final_top_k": top_k,
            "max_passage_chars": max_passage_chars,
            "query_expansions_path": query_expansions_path,
            "parent_chunks_path": parent_chunks_path,
            "require_reviewed_expansions": require_reviewed_expansions,
            "reranker": reranker.model_info.to_dict(),
        },
    )
```

- [ ] **Step 4: Run rerank eval tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_eval_green tests/test_rag_rerank_eval.py tests/test_rag_hybrid_eval.py tests/test_rag_reranking.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Checkpoint without committing**

Run:

```powershell
git diff -- src/fireclaw_core/rag/rerank_eval.py tests/test_rag_rerank_eval.py
```

Expected:

```text
diff output shows the hybrid rerank evaluation wrapper and tests
```

---

### Task 3: CLI Integration for Hybrid Rerank Evaluation

**Files:**
- Modify: `src/fireclaw_core/rag/rag_cli.py`
- Modify: `tests/test_rag_bm25_cli.py`

**Interfaces:**
- Consumes:
  - `evaluate_hybrid_retrievers_with_rerank(...)`
  - `load_parent_texts(...)`
  - `FakeRerankerProvider`
  - `BGEFlagRerankerProvider`
- Produces:
  - CLI command `eval-hybrid-rerank-index`

- [ ] **Step 1: Write failing CLI test**

Append to `tests/test_rag_bm25_cli.py`:

```python
def test_cli_eval_hybrid_rerank_index_with_fake_reranker(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider
    from fireclaw_core.rag.dense_retrieval import build_dense_index

    records_path = tmp_path / "records.jsonl"
    _write_jsonl(
        records_path,
        [
            _record("generic", "generic firefighter health rescue"),
            _record("gold", "SCBA rehabilitation medical evaluation NFPA 1584 rescue"),
        ],
    )
    dense_index_dir = tmp_path / "dense_index"
    build_dense_index(records_path, dense_index_dir, FakeEmbeddingProvider(), batch_size=2)

    bm25_index_dir = tmp_path / "bm25_index"
    assert main(["build-bm25-index", "--records", str(records_path), "--index-dir", str(bm25_index_dir)]) == 0
    capsys.readouterr()

    parent_chunks_path = tmp_path / "parent_chunks.jsonl"
    _write_jsonl(
        parent_chunks_path,
        [
            {"parent_id": "generic__parent", "text": "generic firefighter health information"},
            {"parent_id": "gold__parent", "text": "SCBA rehabilitation medical evaluation NFPA 1584"},
        ],
    )

    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "case_id": "case",
                "topic": "rehab",
                "query": "中文问题",
                "gold_parent_ids": ["gold__parent"],
            }
        ],
    )
    expansions_path = tmp_path / "expansions.jsonl"
    _write_jsonl(
        expansions_path,
        [
            {
                "case_id": "case",
                "query_zh": "中文问题",
                "reviewed_query_en": "When should firefighters enter SCBA rehabilitation?",
                "term_query": "SCBA rehabilitation medical evaluation NFPA 1584",
                "terms": ["SCBA", "rehabilitation", "NFPA 1584"],
                "status": "reviewed",
            }
        ],
    )
    output_path = tmp_path / "hybrid_rerank_report.json"

    exit_code = main(
        [
            "eval-hybrid-rerank-index",
            "--provider",
            "fake",
            "--dense-index-dir",
            str(dense_index_dir),
            "--bm25-index-dir",
            str(bm25_index_dir),
            "--reranker-provider",
            "fake",
            "--parent-chunks",
            str(parent_chunks_path),
            "--cases",
            str(cases_path),
            "--query-expansions",
            str(expansions_path),
            "--dense-query-variants",
            "zh,en",
            "--bm25-query-variants",
            "en,terms",
            "--rerank-query-variant",
            "en",
            "--rerank-pool-size",
            "10",
            "--top-k",
            "10",
            "--require-reviewed-expansions",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["retrieval_config"]["retrieval_method"] == "hybrid_rerank"
    assert report["retrieval_config"]["reranker"]["provider"] == "fake-reranker"
    assert report["retrieval_config"]["rerank_query_variant"] == "en"
    assert report["retrieval_config"]["rerank_pool_size"] == 10
    assert report["hit_at_1"] == 1.0
    assert report["results"][0]["top_hits"][0]["parent_id"] == "gold__parent"
    assert json.loads(capsys.readouterr().out)["retrieval_config"]["retrieval_method"] == "hybrid_rerank"
```

- [ ] **Step 2: Run CLI test to verify it fails**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_cli_red tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker -q
```

Expected:

```text
argparse error because command eval-hybrid-rerank-index does not exist
```

- [ ] **Step 3: Add CLI imports**

Modify `src/fireclaw_core/rag/rag_cli.py` imports:

```python
from fireclaw_core.rag.rerank_eval import evaluate_hybrid_retrievers_with_rerank
from fireclaw_core.rag.reranking import BGEFlagRerankerProvider
from fireclaw_core.rag.reranking import FakeRerankerProvider
from fireclaw_core.rag.reranking import load_parent_texts
```

- [ ] **Step 4: Add parser for `eval-hybrid-rerank-index`**

Inside `main(...)`, after the existing `hybrid_eval` parser block, add:

```python
hybrid_rerank_eval = subparsers.add_parser(
    "eval-hybrid-rerank-index",
    help="Evaluate hybrid dense+BM25 retrieval with parent-level reranking.",
)
hybrid_rerank_eval.add_argument("--corpus-root", default="data/rag/fire_rescue")
hybrid_rerank_eval.add_argument("--dense-index-dir", default=None)
hybrid_rerank_eval.add_argument("--bm25-index-dir", default=None)
hybrid_rerank_eval.add_argument("--provider", choices=["fake", "bge-m3"], default="fake")
hybrid_rerank_eval.add_argument("--model-path", default=None)
hybrid_rerank_eval.add_argument("--reranker-provider", choices=["fake", "bge-reranker"], default="fake")
hybrid_rerank_eval.add_argument("--reranker-model-path", default=None)
hybrid_rerank_eval.add_argument("--reranker-device", default=None)
hybrid_rerank_eval.add_argument("--reranker-batch-size", type=int, default=32)
hybrid_rerank_eval.add_argument("--reranker-max-length", type=int, default=512)
hybrid_rerank_eval.add_argument("--parent-chunks", default=None)
hybrid_rerank_eval.add_argument("--cases", default=None)
hybrid_rerank_eval.add_argument("--query-expansions", required=True)
hybrid_rerank_eval.add_argument("--dense-query-variants", default="zh,en,terms")
hybrid_rerank_eval.add_argument("--bm25-query-variants", default="en,terms")
hybrid_rerank_eval.add_argument("--rerank-query-variant", choices=["zh", "en", "terms"], default="en")
hybrid_rerank_eval.add_argument("--small-top-k", type=int, default=50)
hybrid_rerank_eval.add_argument("--rerank-pool-size", type=int, default=50)
hybrid_rerank_eval.add_argument("--top-k", type=int, default=10)
hybrid_rerank_eval.add_argument("--rrf-k", type=int, default=60)
hybrid_rerank_eval.add_argument("--max-passage-chars", type=int, default=6000)
hybrid_rerank_eval.add_argument("--output", default=None)
hybrid_rerank_eval.add_argument("--require-reviewed-expansions", action="store_true")
```

Add command dispatch:

```python
if args.command == "eval-hybrid-rerank-index":
    return _cmd_eval_hybrid_rerank_index(args)
```

- [ ] **Step 5: Add reranker provider and command helpers**

Add to `src/fireclaw_core/rag/rag_cli.py`:

```python
def _cmd_eval_hybrid_rerank_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    dense_index_dir = (
        Path(args.dense_index_dir)
        if args.dense_index_dir
        else corpus_root / "indexes" / "dense" / _provider_index_name(args.provider)
    )
    bm25_index_dir = _bm25_index_dir(corpus_root, args.bm25_index_dir)
    cases_path = Path(args.cases) if args.cases else corpus_root / "eval" / "dense_gold_cases_zh_v1.jsonl"
    output_path = Path(args.output) if args.output else None
    expansions_path = Path(args.query_expansions)
    parent_chunks_path = Path(args.parent_chunks) if args.parent_chunks else corpus_root / "chunks" / "parent_chunks.jsonl"

    provider = _create_embedding_provider(args.provider, model_path=args.model_path)
    dense_retriever = DenseRetriever.load(dense_index_dir, provider)
    bm25_retriever = BM25Retriever.load(bm25_index_dir)
    reranker = _create_reranker_provider(
        args.reranker_provider,
        model_path=args.reranker_model_path,
        device=args.reranker_device,
        batch_size=args.reranker_batch_size,
        max_length=args.reranker_max_length,
    )
    cases = load_dense_eval_cases(cases_path)
    expansions = load_query_expansions(expansions_path)
    parent_texts = load_parent_texts(parent_chunks_path)
    report = evaluate_hybrid_retrievers_with_rerank(
        dense_retriever,
        bm25_retriever,
        reranker,
        cases,
        query_expansions=expansions,
        parent_texts=parent_texts,
        dense_query_variants=_parse_csv_arg(args.dense_query_variants),
        bm25_query_variants=_parse_csv_arg(args.bm25_query_variants),
        rerank_query_variant=args.rerank_query_variant,
        rerank_pool_size=args.rerank_pool_size,
        top_k=args.top_k,
        small_top_k=args.small_top_k,
        rrf_k=args.rrf_k,
        max_passage_chars=args.max_passage_chars,
        require_reviewed_expansions=args.require_reviewed_expansions,
        query_expansions_path=str(expansions_path),
        parent_chunks_path=str(parent_chunks_path),
    )
    payload = report.to_dict()
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_json_output(payload)
    return 0


def _create_reranker_provider(
    provider_name: str,
    *,
    model_path: str | None = None,
    device: str | None = None,
    batch_size: int = 32,
    max_length: int = 512,
):
    if provider_name == "fake":
        return FakeRerankerProvider()
    if provider_name == "bge-reranker":
        path = Path(model_path) if model_path else Path(".cache/models/bge-reranker-v2-m3")
        return BGEFlagRerankerProvider(
            path,
            device=device,
            batch_size=batch_size,
            max_length=max_length,
        )
    raise ValueError(f"Unsupported reranker provider: {provider_name}")
```

- [ ] **Step 6: Run CLI tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_cli_green tests/test_rag_bm25_cli.py tests/test_rag_rerank_eval.py tests/test_rag_reranking.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 7: Checkpoint without committing**

Run:

```powershell
git diff -- src/fireclaw_core/rag/rag_cli.py tests/test_rag_bm25_cli.py
```

Expected:

```text
diff output shows only the new hybrid rerank CLI command, provider factory, and CLI test
```

---

### Task 4: Documentation and Focused Verification

**Files:**
- Modify: `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
- Modify or create: `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`

**Interfaces:**
- Consumes:
  - Completed Tasks 1-3.
  - Existing v2 reports.
- Produces:
  - Documentation explaining rerank position in the retrieval pipeline.
  - Memory record with implementation status and test commands.

- [ ] **Step 1: Add reranker walkthrough section**

Append this section to `docs/rag/dense-evaluation-walkthrough.zh-CN.md`:

```markdown
## Optional Evaluation: Hybrid Reranking

Reranking is a second-stage ranking step. It does not replace dense retrieval,
BM25, query expansion, parent aggregation, or RRF. It first asks hybrid retrieval
to produce a larger parent candidate pool, then scores each `(query, parent_text)`
pair with a reranker model and sorts candidates by the reranker score.

Default first-pass evaluation settings:

```text
hybrid parent candidates: top 50
rerank query variant: reviewed English query
reranker input text: full parent text from parent_chunks.jsonl
final report cutoff: top 10
```

The first reranker experiment should be interpreted as a ranking-quality
ablation. It can improve `Hit@1`, `Hit@5`, and `MRR@10` when the correct parent
is already inside the candidate pool. It cannot recover a gold parent that
hybrid retrieval did not retrieve into the rerank pool.
```

- [ ] **Step 2: Run focused unit tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_all tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py tests/test_rag_hybrid_eval.py tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py -q
```

Expected:

```text
all selected tests pass
```

If managed Windows sandbox cleanup raises `PermissionError` for the pytest basetemp directory, rerun the same command with escalation and record both outcomes in memory.

- [ ] **Step 3: Update memory after focused verification**

Create or append `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`:

```markdown
# FireClaw RAG Hybrid Reranker Evaluation

**Date:** 2026-07-08
**Status:** Reranker evaluation implementation in progress.

## Goal

Add a pretrained cross-encoder reranker evaluation layer after hybrid Dense+BM25 parent retrieval.

## User Decisions

- Use a pretrained reranker first; do not train or fine-tune.
- Keep the existing BM25 preservation concern as a known limitation for now.
- Do not change retrieval algorithms without discussion.

## Implementation Notes

- Reranker is inserted after hybrid parent RRF.
- Candidate pool size is 50 parents by default.
- Final metric cutoff remains top 10.
- Rerank query variant defaults to reviewed English.
- Parent text comes from `data/rag/fire_rescue/chunks/parent_chunks.jsonl`.

## Commands

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_all tests/test_rag_reranking.py tests/test_rag_rerank_eval.py tests/test_rag_bm25_cli.py tests/test_rag_hybrid_eval.py tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py -q
```

## Results

After execution, write one timestamped bullet containing the exact pytest result line, for example `7 passed in 0.42s`, or the exact sandbox `PermissionError` followed by the escalated rerun result.

## Next Step

Run a real BGE reranker report if a local reranker model exists, or ask the user whether to download/provide one.
```

- [ ] **Step 4: Run diff check**

Run:

```powershell
git diff --check
```

Expected:

```text
exit code 0
```

---

### Task 5: Real v2 Hybrid Rerank Report

**Files:**
- Generate if local model exists:
  - `data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_rerank_bge_v2_expanded_parent_report.json`
- Modify:
  - `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`

**Interfaces:**
- Consumes:
  - `data/rag/fire_rescue/eval/dense_gold_cases_zh_v2.jsonl`
  - `data/rag/fire_rescue/eval/query_expansions_zh_v2.jsonl`
  - `data/rag/fire_rescue/indexes/dense/bge-m3`
  - `data/rag/fire_rescue/indexes/bm25/small_v1`
  - `data/rag/fire_rescue/chunks/parent_chunks.jsonl`
  - Local reranker model directory, default `.cache/models/bge-reranker-v2-m3`
- Produces:
  - v2 hybrid rerank report and metric comparison.

- [ ] **Step 1: Check local reranker model availability**

Run:

```powershell
Test-Path -LiteralPath '.cache/models/bge-reranker-v2-m3'
```

Expected if model is already available:

```text
True
```

Expected if model is absent:

```text
False
```

If the output is `False`, stop this task before running the real report. Update memory with:

```markdown
## Real Model Availability

`.cache/models/bge-reranker-v2-m3` is absent. The implementation is ready for fake-provider tests, but the real BGE reranker report is blocked until the user approves a download or provides a local model path.
```

- [ ] **Step 2: Run real v2 hybrid rerank eval when model exists**

Run only if Step 1 prints `True`:

```powershell
$env:PYTHONPATH = "src"
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-hybrid-rerank-index --provider bge-m3 --model-path .cache/models/bge-m3 --dense-index-dir data/rag/fire_rescue/indexes/dense/bge-m3 --bm25-index-dir data/rag/fire_rescue/indexes/bm25/small_v1 --reranker-provider bge-reranker --reranker-model-path .cache/models/bge-reranker-v2-m3 --parent-chunks data/rag/fire_rescue/chunks/parent_chunks.jsonl --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v2.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v2.jsonl --dense-query-variants zh,en,terms --bm25-query-variants en,terms --rerank-query-variant en --small-top-k 50 --rerank-pool-size 50 --top-k 10 --require-reviewed-expansions --output data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_rerank_bge_v2_expanded_parent_report.json
```

Expected:

```text
exit code 0
retrieval_config.retrieval_method = "hybrid_rerank"
retrieval_config.reranker.provider = "bge-reranker"
case_count = 30
```

- [ ] **Step 3: Compare v2 metrics**

Run after Step 2 succeeds:

```powershell
$env:PYTHONPATH = "src"
python -c "import json; from pathlib import Path; paths=['data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v2_expanded_parent_report.json','data/rag/fire_rescue/eval/runs/bm25_small_v2_expanded_parent_report.json','data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v2_expanded_parent_report.json','data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_rerank_bge_v2_expanded_parent_report.json']; labels=['dense','bm25','hybrid','hybrid_rerank']; fields=['case_count','hit_at_1','hit_at_5','hit_at_10','mrr_at_10','gold_recall_at_10']; [print(label, ' '.join(f'{field}={json.loads(Path(path).read_text(encoding=\"utf-8\"))[field]}' for field in fields)) for label, path in zip(labels, paths)]"
```

Expected:

```text
four lines are printed, one each for dense, bm25, hybrid, and hybrid_rerank
each line includes case_count, hit_at_1, hit_at_5, hit_at_10, mrr_at_10, and gold_recall_at_10
```

- [ ] **Step 4: Update memory with real metrics**

Append to `memory/2026-07-08/fireclaw-rag-reranker-evaluation.md`:

```markdown
## Real v2 Reranker Report

Command:

```powershell
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-hybrid-rerank-index --provider bge-m3 --model-path .cache/models/bge-m3 --dense-index-dir data/rag/fire_rescue/indexes/dense/bge-m3 --bm25-index-dir data/rag/fire_rescue/indexes/bm25/small_v1 --reranker-provider bge-reranker --reranker-model-path .cache/models/bge-reranker-v2-m3 --parent-chunks data/rag/fire_rescue/chunks/parent_chunks.jsonl --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v2.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v2.jsonl --dense-query-variants zh,en,terms --bm25-query-variants en,terms --rerank-query-variant en --small-top-k 50 --rerank-pool-size 50 --top-k 10 --require-reviewed-expansions --output data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_rerank_bge_v2_expanded_parent_report.json
```

Metrics:

Paste the four exact metric lines printed by the comparison command.

Conclusion:

Record whether reranking improved `Hit@1`, `Hit@5`, and `MRR@10`, and whether any gold parent dropped out after reranking.
```

- [ ] **Step 5: Final status check**

Run:

```powershell
git status -sb
git diff --check
```

Expected:

```text
git status shows planned reranker files plus existing untracked v2 reports and memory records
git diff --check exits 0
```

## Self-Review Checklist

- Spec coverage:
  - Pretrained reranker only; no training or fine-tuning.
  - Hybrid parent candidate pool reranked after RRF.
  - Parent text loaded from `parent_chunks.jsonl`.
  - Default rerank query is reviewed English.
  - Existing dense, BM25, and hybrid eval paths remain available.
  - Real model run stops before download if model path is absent.
- Placeholder scan:
  - No "TBD", "TODO", "implement later", or unspecified file paths.
  - Every code step includes concrete code.
  - Every verification step includes concrete command and expected result.
- Type consistency:
  - `RerankerProvider.score_pairs(...)` is used by `rerank_parent_hits(...)`.
  - `evaluate_hybrid_retrievers_with_rerank(...)` returns `DenseEvalReport`.
  - CLI command writes the same report JSON shape as other eval commands.
- Research validity:
  - Reranker is evaluated as a ranking ablation, not a recall improvement.
  - `Hit@1`, `Hit@5`, and `MRR@10` are the primary success metrics.
  - `Hit@10` is still recorded to detect candidate loss or harmful reranking.
  - The 30-case v2 set remains an engineering ablation, not a publication-level benchmark.
