# FireClaw BM25 and Hybrid Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lightweight Okapi BM25 retriever and Dense+BM25 hybrid evaluation path for the FireClaw RAG corpus.

**Architecture:** Build a small-chunk lexical inverted index from the existing index input records, expose `BM25Retriever.query()` with the same `DenseHit` shape as dense retrieval, then reuse existing query expansion, parent aggregation, RRF, and dense eval report machinery. Hybrid evaluation fuses per-method ranked parent lists with RRF rather than adding incompatible dense and BM25 scores.

**Tech Stack:** Python 3.12, dataclasses, JSON/JSONL index files, regex tokenization, existing `DenseHit` / `DenseEvalRetrievedHit`, pytest, local BGE-M3 verification.

## Global Constraints

- User-facing replies should be in Chinese; code, commands, paths, class names, API names, data shapes, and error messages stay English.
- Do not broaden `gold_parent_ids` in this round.
- Do not rebuild dense vectors or change chunk embedding text in this round.
- Do not change chunking.
- Do not add Elasticsearch, Lucene, rerankers, LLM judges, or new external dependencies.
- BM25 official eval should use reviewed query expansions with `--require-reviewed-expansions`.
- Preserve existing dense CLI behavior and dense reports.
- Do not commit unless the user explicitly asks for a commit.

---

## File Structure

- Create `src/fireclaw_core/rag/bm25_retrieval.py`
  - Deterministic tokenizer.
  - BM25 index manifest/report dataclasses.
  - `build_bm25_index(...)`.
  - `BM25Retriever.load(...)`.
  - `BM25Retriever.query(...) -> list[DenseHit]`.

- Create `tests/test_rag_bm25_retrieval.py`
  - Tokenization tests.
  - Index build validation.
  - Save/load roundtrip.
  - BM25 scoring/ranking behavior.

- Modify `src/fireclaw_core/rag/dense_eval.py`
  - Add a generic expanded retriever evaluator internally or a BM25 wrapper that reuses the existing expanded eval flow.
  - Preserve `evaluate_dense_retriever(...)` and `evaluate_dense_retriever_with_expansion(...)` public behavior.

- Create `src/fireclaw_core/rag/hybrid_eval.py`
  - Fuse dense and BM25 result lists through method-level RRF.
  - Preserve per-case query variant metadata.

- Create `tests/test_rag_hybrid_eval.py`
  - Unit tests for hybrid fusion and reviewed query expansion use.

- Modify `src/fireclaw_core/rag/rag_cli.py`
  - Add `build-bm25-index`.
  - Add `query-bm25-index`.
  - Add `eval-bm25-index`.
  - Add `eval-hybrid-index`.

- Create `tests/test_rag_bm25_cli.py`
  - CLI tests for build/query/eval BM25.
  - CLI test for hybrid config output.

- Modify `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
  - Add BM25 and hybrid evaluation section.

- Modify or create `memory/2026-07-07/fireclaw-bm25-hybrid-retrieval.md`
  - Record implementation, commands, metrics, and conclusions.

---

### Task 1: BM25 Tokenizer and Retriever

**Files:**
- Create: `src/fireclaw_core/rag/bm25_retrieval.py`
- Create: `tests/test_rag_bm25_retrieval.py`

**Interfaces:**
- Consumes:
  - JSONL records from `data/rag/fire_rescue/index_inputs/small_index_records.jsonl`.
  - Records with fields: `chunk_id`, `parent_id`, `doc_id`, `clean_text`, `indexable`, plus optional metadata.
- Produces:
  - `BM25TokenizerConfig`
  - `BM25IndexManifest`
  - `BM25IndexBuildReport`
  - `tokenize_for_bm25(text: str, config: BM25TokenizerConfig | None = None) -> list[str]`
  - `build_bm25_index(records_path: Path, output_dir: Path, *, text_field: str = "clean_text", k1: float = 1.5, b: float = 0.75, tokenizer_config: BM25TokenizerConfig | None = None) -> BM25IndexBuildReport`
  - `BM25Retriever.load(index_dir: Path) -> BM25Retriever`
  - `BM25Retriever.query(query: str, *, top_k: int = 5) -> list[DenseHit]`

- [ ] **Step 1: Write failing tokenizer tests**

Create `tests/test_rag_bm25_retrieval.py` with:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fireclaw_core.rag.bm25_retrieval import BM25Retriever
from fireclaw_core.rag.bm25_retrieval import build_bm25_index
from fireclaw_core.rag.bm25_retrieval import tokenize_for_bm25


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _record(chunk_id: str, text: str, *, indexable: bool = True) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "parent_id": f"{chunk_id}__parent",
        "doc_id": "doc",
        "source_file": "raw/doc.pdf",
        "page_start": 1,
        "page_end": 1,
        "heading": "Heading",
        "clean_text": text,
        "indexable": indexable,
    }


def test_tokenize_for_bm25_keeps_domain_abbreviations_and_numbers() -> None:
    tokens = tokenize_for_bm25("SCBA, NFPA-1584, TDLAS, PPE, and go/no-go checks.")

    assert tokens == ["scba", "nfpa", "1584", "tdlas", "ppe", "go", "no", "go", "checks"]


def test_tokenize_for_bm25_removes_common_stop_words_and_single_letter_noise() -> None:
    tokens = tokenize_for_bm25("The robot is in a fire and x y z marker.")

    assert tokens == ["robot", "fire", "marker"]
```

- [ ] **Step 2: Run tokenizer tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_tokenizer_red tests/test_rag_bm25_retrieval.py::test_tokenize_for_bm25_keeps_domain_abbreviations_and_numbers tests/test_rag_bm25_retrieval.py::test_tokenize_for_bm25_removes_common_stop_words_and_single_letter_noise -q
```

Expected:

```text
ModuleNotFoundError: No module named 'fireclaw_core.rag.bm25_retrieval'
```

- [ ] **Step 3: Implement tokenizer and dataclasses**

Create `src/fireclaw_core/rag/bm25_retrieval.py` with these starting definitions:

```python
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from typing import Any

from fireclaw_core.rag.dense_retrieval import DenseHit
from fireclaw_core.rag.dense_retrieval import load_jsonl
from fireclaw_core.rag.dense_retrieval import write_jsonl


DEFAULT_BM25_STOP_WORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "in", "into", "is", "it", "of", "on", "or", "that", "the",
        "their", "this", "to", "was", "were", "with",
    }
)
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class BM25TokenizerConfig:
    lowercase: bool = True
    min_token_length: int = 2
    stop_words: tuple[str, ...] = field(default_factory=lambda: tuple(sorted(DEFAULT_BM25_STOP_WORDS)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BM25IndexManifest:
    index_type: str
    created_at: str
    source_records: str
    text_field: str
    record_count: int
    token_count: int
    avg_doc_len: float
    k1: float
    b: float
    tokenizer: BM25TokenizerConfig

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tokenizer"] = self.tokenizer.to_dict()
        return data


@dataclass(frozen=True)
class BM25IndexBuildReport:
    status: str
    input_records: str
    output_dir: str
    indexable_record_count: int
    skipped_record_count: int
    token_count: int
    avg_doc_len: float
    files: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def tokenize_for_bm25(text: str, config: BM25TokenizerConfig | None = None) -> list[str]:
    cfg = config or BM25TokenizerConfig()
    source = text.lower() if cfg.lowercase else text
    stop_words = set(cfg.stop_words)
    tokens: list[str] = []
    for match in TOKEN_PATTERN.finditer(source):
        token = match.group(0)
        if len(token) < cfg.min_token_length:
            continue
        if token in stop_words:
            continue
        tokens.append(token)
    return tokens
```

- [ ] **Step 4: Run tokenizer tests to verify they pass**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_tokenizer_green tests/test_rag_bm25_retrieval.py::test_tokenize_for_bm25_keeps_domain_abbreviations_and_numbers tests/test_rag_bm25_retrieval.py::test_tokenize_for_bm25_removes_common_stop_words_and_single_letter_noise -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Add failing index build and query tests**

Append to `tests/test_rag_bm25_retrieval.py`:

```python
def test_build_bm25_index_writes_manifest_and_index_files(tmp_path: Path) -> None:
    records_path = tmp_path / "records.jsonl"
    index_dir = tmp_path / "bm25_index"
    _write_jsonl(
        records_path,
        [
            _record("chunk_scba", "SCBA rehabilitation medical evaluation"),
            _record("chunk_thermal", "thermal imaging smoke victim detection"),
            _record("chunk_skip", "not indexed", indexable=False),
        ],
    )

    report = build_bm25_index(records_path, index_dir)

    assert report.status == "completed"
    assert report.indexable_record_count == 2
    assert report.skipped_record_count == 1
    assert report.token_count >= 7
    assert (index_dir / "manifest.json").exists()
    assert (index_dir / "index.json").exists()
    assert (index_dir / "records.jsonl").exists()


def test_bm25_retriever_ranks_exact_term_match_first(tmp_path: Path) -> None:
    records_path = tmp_path / "records.jsonl"
    index_dir = tmp_path / "bm25_index"
    _write_jsonl(
        records_path,
        [
            _record("chunk_scba", "SCBA cylinder formal rehabilitation medical evaluation hydration"),
            _record("chunk_thermal", "thermal imaging smoke victim detection"),
            _record("chunk_generic", "fire robot system method response"),
        ],
    )
    build_bm25_index(records_path, index_dir)
    retriever = BM25Retriever.load(index_dir)

    hits = retriever.query("SCBA rehabilitation medical evaluation", top_k=2)

    assert [hit.record["chunk_id"] for hit in hits] == ["chunk_scba"]
    assert hits[0].rank == 1
    assert hits[0].score > 0.0
    assert hits[0].record["parent_id"] == "chunk_scba__parent"


def test_bm25_retriever_rejects_non_positive_top_k(tmp_path: Path) -> None:
    records_path = tmp_path / "records.jsonl"
    index_dir = tmp_path / "bm25_index"
    _write_jsonl(records_path, [_record("chunk", "SCBA rehabilitation")])
    build_bm25_index(records_path, index_dir)
    retriever = BM25Retriever.load(index_dir)

    with pytest.raises(ValueError, match="top_k must be positive"):
        retriever.query("SCBA", top_k=0)
```

- [ ] **Step 6: Run index/query tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_index_red tests/test_rag_bm25_retrieval.py -q
```

Expected:

```text
AttributeError or ImportError for missing build_bm25_index / BM25Retriever
```

- [ ] **Step 7: Implement BM25 index build, load, and query**

Extend `src/fireclaw_core/rag/bm25_retrieval.py`:

```python
class BM25Retriever:
    def __init__(
        self,
        *,
        index_dir: Path,
        manifest: BM25IndexManifest,
        postings: dict[str, list[tuple[int, int]]],
        doc_lengths: list[int],
        records: list[dict[str, Any]],
    ) -> None:
        self.index_dir = index_dir
        self.manifest = manifest
        self.postings = postings
        self.doc_lengths = doc_lengths
        self.records = records

    @classmethod
    def load(cls, index_dir: Path) -> BM25Retriever:
        index_dir = Path(index_dir)
        manifest = read_bm25_manifest(index_dir / "manifest.json")
        index_data = json.loads((index_dir / "index.json").read_text(encoding="utf-8"))
        postings = {
            token: [(int(record_index), int(tf)) for record_index, tf in posting_list]
            for token, posting_list in index_data["postings"].items()
        }
        doc_lengths = [int(length) for length in index_data["doc_lengths"]]
        records = load_jsonl(index_dir / "records.jsonl")
        if len(records) != manifest.record_count:
            raise ValueError(f"Record count {len(records)} does not match manifest count {manifest.record_count}")
        if len(doc_lengths) != manifest.record_count:
            raise ValueError(f"Doc length count {len(doc_lengths)} does not match manifest count {manifest.record_count}")
        return cls(index_dir=index_dir, manifest=manifest, postings=postings, doc_lengths=doc_lengths, records=records)

    def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        query_tokens = list(dict.fromkeys(tokenize_for_bm25(query, self.manifest.tokenizer)))
        scores: dict[int, float] = defaultdict(float)
        for token in query_tokens:
            posting_list = self.postings.get(token)
            if not posting_list:
                continue
            df = len(posting_list)
            idf = math.log(1.0 + ((self.manifest.record_count - df + 0.5) / (df + 0.5)))
            for record_index, tf in posting_list:
                doc_len = self.doc_lengths[record_index]
                denominator = tf + self.manifest.k1 * (
                    1.0 - self.manifest.b + self.manifest.b * (doc_len / self.manifest.avg_doc_len)
                )
                scores[record_index] += idf * ((tf * (self.manifest.k1 + 1.0)) / denominator)

        ranked = sorted(scores.items(), key=lambda item: (-item[1], self.records[item[0]].get("chunk_id", "")))[:top_k]
        return [
            DenseHit(rank=rank, score=float(score), record=self.records[record_index])
            for rank, (record_index, score) in enumerate(ranked, start=1)
        ]


def build_bm25_index(
    records_path: Path,
    output_dir: Path,
    *,
    text_field: str = "clean_text",
    k1: float = 1.5,
    b: float = 0.75,
    tokenizer_config: BM25TokenizerConfig | None = None,
) -> BM25IndexBuildReport:
    records_path = Path(records_path)
    output_dir = Path(output_dir)
    if not records_path.exists():
        raise FileNotFoundError(f"Index records JSONL not found: {records_path}")
    if k1 <= 0:
        raise ValueError("k1 must be positive")
    if not 0 <= b <= 1:
        raise ValueError("b must be between 0 and 1")

    cfg = tokenizer_config or BM25TokenizerConfig()
    all_records = load_jsonl(records_path)
    indexable_records = [record for record in all_records if record.get("indexable") is True]
    if not indexable_records:
        raise ValueError(f"No indexable records found in {records_path}")

    postings_by_token: dict[str, dict[int, int]] = defaultdict(dict)
    doc_lengths: list[int] = []
    for record_index, record in enumerate(indexable_records):
        tokens = tokenize_for_bm25(str(record.get(text_field) or ""), cfg)
        counts = Counter(tokens)
        doc_lengths.append(len(tokens))
        for token, tf in counts.items():
            postings_by_token[token][record_index] = int(tf)

    avg_doc_len = sum(doc_lengths) / len(doc_lengths)
    postings = {
        token: [[record_index, tf] for record_index, tf in sorted(posting.items())]
        for token, posting in sorted(postings_by_token.items())
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = BM25IndexManifest(
        index_type="bm25_inverted_index",
        created_at=datetime.now(timezone.utc).isoformat(),
        source_records=str(records_path),
        text_field=text_field,
        record_count=len(indexable_records),
        token_count=len(postings),
        avg_doc_len=float(avg_doc_len),
        k1=float(k1),
        b=float(b),
        tokenizer=cfg,
    )
    manifest_path = output_dir / "manifest.json"
    index_path = output_dir / "index.json"
    records_output_path = output_dir / "records.jsonl"
    manifest_path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    index_path.write_text(json.dumps({"postings": postings, "doc_lengths": doc_lengths}, ensure_ascii=False), encoding="utf-8")
    write_jsonl(records_output_path, indexable_records)

    return BM25IndexBuildReport(
        status="completed",
        input_records=str(records_path),
        output_dir=str(output_dir),
        indexable_record_count=len(indexable_records),
        skipped_record_count=len(all_records) - len(indexable_records),
        token_count=len(postings),
        avg_doc_len=float(avg_doc_len),
        files={"manifest": str(manifest_path), "index": str(index_path), "records": str(records_output_path)},
    )


def read_bm25_manifest(path: Path) -> BM25IndexManifest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tokenizer = BM25TokenizerConfig(**data["tokenizer"])
    return BM25IndexManifest(
        index_type=data["index_type"],
        created_at=data["created_at"],
        source_records=data["source_records"],
        text_field=data["text_field"],
        record_count=int(data["record_count"]),
        token_count=int(data["token_count"]),
        avg_doc_len=float(data["avg_doc_len"]),
        k1=float(data["k1"]),
        b=float(data["b"]),
        tokenizer=tokenizer,
    )
```

- [ ] **Step 8: Run BM25 retrieval tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_retrieval_green tests/test_rag_bm25_retrieval.py -q
```

Expected:

```text
5 passed
```

---

### Task 2: BM25 Expanded Evaluation

**Files:**
- Modify: `src/fireclaw_core/rag/dense_eval.py`
- Create or modify: `tests/test_rag_bm25_eval.py`

**Interfaces:**
- Consumes:
  - Any retriever whose `query(query: str, *, top_k: int) -> list[DenseHit]`.
  - Existing `QueryExpansion`.
- Produces:
  - `evaluate_bm25_retriever_with_expansion(...) -> DenseEvalReport`
  - Report `retrieval_config.retrieval_method = "bm25"`

- [ ] **Step 1: Write failing BM25 eval tests**

Create `tests/test_rag_bm25_eval.py`:

```python
from __future__ import annotations

from fireclaw_core.rag.dense_eval import DenseEvalCase
from fireclaw_core.rag.dense_retrieval import DenseHit
from fireclaw_core.rag.query_expansion import QueryExpansion


def test_evaluate_bm25_retriever_with_expansion_uses_reviewed_en_and_terms() -> None:
    from fireclaw_core.rag.dense_eval import evaluate_bm25_retriever_with_expansion

    seen_queries: list[str] = []

    class FakeBM25Retriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            seen_queries.append(query)
            if "SCBA" in query or "rehabilitation" in query:
                return [
                    DenseHit(
                        rank=1,
                        score=3.0,
                        record={"chunk_id": "chunk_gold", "parent_id": "parent_gold", "doc_id": "doc"},
                    )
                ]
            return []

    case = DenseEvalCase(
        case_id="dense_zh_008",
        topic="rehab",
        query="消防员什么时候进入rehab？",
        gold_parent_ids=["parent_gold"],
    )
    expansions = {
        "dense_zh_008": QueryExpansion(
            case_id="dense_zh_008",
            query_zh=case.query,
            llm_query_en="When should firefighters enter rehab?",
            reviewed_query_en="When must firefighters enter rehabilitation after SCBA use?",
            term_query="SCBA rehabilitation medical evaluation hydration",
            terms=["SCBA", "rehabilitation", "medical evaluation", "hydration"],
            status="reviewed",
        )
    }

    report = evaluate_bm25_retriever_with_expansion(
        FakeBM25Retriever(),
        [case],
        query_expansions=expansions,
        query_variants=["en", "terms"],
        ranking_view="parent",
        small_top_k=10,
        top_k=10,
        require_reviewed_expansions=True,
        query_expansions_path="query_expansions.jsonl",
    )

    assert seen_queries == [
        "When must firefighters enter rehabilitation after SCBA use?",
        "SCBA rehabilitation medical evaluation hydration",
    ]
    assert report.hit_at_1 == 1.0
    assert report.retrieval_config is not None
    assert report.retrieval_config["retrieval_method"] == "bm25"
    assert report.retrieval_config["query_variants"] == ["en", "terms"]
    assert report.retrieval_config["require_reviewed_expansions"] is True
```

- [ ] **Step 2: Run BM25 eval test to verify it fails**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_eval_red tests/test_rag_bm25_eval.py -q
```

Expected:

```text
ImportError: cannot import name 'evaluate_bm25_retriever_with_expansion'
```

- [ ] **Step 3: Refactor expanded eval to support retrieval method metadata**

Modify `src/fireclaw_core/rag/dense_eval.py` by adding optional `retrieval_method` to `evaluate_dense_retriever_with_expansion`:

```python
def evaluate_dense_retriever_with_expansion(
    retriever: Any,
    cases: list[DenseEvalCase],
    *,
    query_expansions: Mapping[str, QueryExpansion] | None = None,
    query_variants: Sequence[str] = ("zh",),
    ranking_view: str = "small",
    top_k: int = 10,
    small_top_k: int | None = None,
    parent_aggregation: str = "max",
    fusion: str | None = None,
    rrf_k: int = 60,
    require_reviewed_expansions: bool = False,
    query_expansions_path: str | None = None,
    retrieval_method: str = "dense",
) -> DenseEvalReport:
```

Then add `"retrieval_method": retrieval_method` to the returned `retrieval_config`.

Add wrapper:

```python
def evaluate_bm25_retriever_with_expansion(
    retriever: Any,
    cases: list[DenseEvalCase],
    *,
    query_expansions: Mapping[str, QueryExpansion] | None = None,
    query_variants: Sequence[str] = ("en", "terms"),
    ranking_view: str = "parent",
    top_k: int = 10,
    small_top_k: int | None = None,
    parent_aggregation: str = "max",
    fusion: str | None = None,
    rrf_k: int = 60,
    require_reviewed_expansions: bool = False,
    query_expansions_path: str | None = None,
) -> DenseEvalReport:
    return evaluate_dense_retriever_with_expansion(
        retriever,
        cases,
        query_expansions=query_expansions,
        query_variants=query_variants,
        ranking_view=ranking_view,
        top_k=top_k,
        small_top_k=small_top_k,
        parent_aggregation=parent_aggregation,
        fusion=fusion,
        rrf_k=rrf_k,
        require_reviewed_expansions=require_reviewed_expansions,
        query_expansions_path=query_expansions_path,
        retrieval_method="bm25",
    )
```

- [ ] **Step 4: Preserve dense tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_eval_dense_regression tests/test_rag_dense_eval.py tests/test_rag_bm25_eval.py -q
```

Expected:

```text
all selected tests pass
```

---

### Task 3: Hybrid Dense+BM25 Evaluation

**Files:**
- Create: `src/fireclaw_core/rag/hybrid_eval.py`
- Create: `tests/test_rag_hybrid_eval.py`

**Interfaces:**
- Consumes:
  - `dense_retriever.query(...) -> list[DenseHit]`
  - `bm25_retriever.query(...) -> list[DenseHit]`
  - `QueryExpansion`
  - `aggregate_hits_by_parent`
  - `reciprocal_rank_fuse`
- Produces:
  - `evaluate_hybrid_retrievers_with_expansion(...) -> DenseEvalReport`

- [ ] **Step 1: Write failing hybrid eval test**

Create `tests/test_rag_hybrid_eval.py`:

```python
from __future__ import annotations

from fireclaw_core.rag.dense_eval import DenseEvalCase
from fireclaw_core.rag.dense_retrieval import DenseHit
from fireclaw_core.rag.query_expansion import QueryExpansion


def test_hybrid_eval_fuses_dense_and_bm25_parent_lists() -> None:
    from fireclaw_core.rag.hybrid_eval import evaluate_hybrid_retrievers_with_expansion

    class FakeDenseRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(rank=1, score=0.9, record={"chunk_id": "dense_wrong", "parent_id": "parent_wrong", "doc_id": "doc"}),
                DenseHit(rank=2, score=0.8, record={"chunk_id": "dense_gold", "parent_id": "parent_gold", "doc_id": "doc"}),
            ]

    class FakeBM25Retriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            return [
                DenseHit(rank=1, score=4.0, record={"chunk_id": "bm25_gold", "parent_id": "parent_gold", "doc_id": "doc"}),
                DenseHit(rank=2, score=2.0, record={"chunk_id": "bm25_other", "parent_id": "parent_other", "doc_id": "doc"}),
            ]

    case = DenseEvalCase(
        case_id="case_hybrid",
        topic="rehab",
        query="中文",
        gold_parent_ids=["parent_gold"],
    )
    expansions = {
        "case_hybrid": QueryExpansion(
            case_id="case_hybrid",
            query_zh="中文",
            reviewed_query_en="English SCBA query",
            term_query="SCBA rehabilitation",
            terms=["SCBA", "rehabilitation"],
            status="reviewed",
        )
    }

    report = evaluate_hybrid_retrievers_with_expansion(
        FakeDenseRetriever(),
        FakeBM25Retriever(),
        [case],
        query_expansions=expansions,
        dense_query_variants=["zh", "en"],
        bm25_query_variants=["en", "terms"],
        ranking_view="parent",
        small_top_k=10,
        top_k=10,
        require_reviewed_expansions=True,
    )

    assert report.hit_at_1 == 1.0
    top_hit = report.results[0].top_hits[0]
    assert top_hit.parent_id == "parent_gold"
    assert top_hit.variant_ranks == {"bm25": 1, "dense": 1}
    assert report.retrieval_config is not None
    assert report.retrieval_config["retrieval_method"] == "hybrid"
    assert report.retrieval_config["dense_query_variants"] == ["zh", "en"]
    assert report.retrieval_config["bm25_query_variants"] == ["en", "terms"]
```

- [ ] **Step 2: Run hybrid test to verify it fails**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_hybrid_eval_red tests/test_rag_hybrid_eval.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'fireclaw_core.rag.hybrid_eval'
```

- [ ] **Step 3: Implement hybrid eval**

Create `src/fireclaw_core/rag/hybrid_eval.py`:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from fireclaw_core.rag.dense_eval import DenseEvalCase
from fireclaw_core.rag.dense_eval import DenseEvalReport
from fireclaw_core.rag.dense_eval import evaluate_ranked_hits
from fireclaw_core.rag.dense_eval import hits_from_dense_results
from fireclaw_core.rag.dense_ranking import aggregate_hits_by_parent
from fireclaw_core.rag.dense_ranking import reciprocal_rank_fuse
from fireclaw_core.rag.query_expansion import QueryExpansion
from fireclaw_core.rag.query_expansion import build_query_variants


def evaluate_hybrid_retrievers_with_expansion(
    dense_retriever: Any,
    bm25_retriever: Any,
    cases: list[DenseEvalCase],
    *,
    query_expansions: Mapping[str, QueryExpansion] | None = None,
    dense_query_variants: Sequence[str] = ("zh", "en", "terms"),
    bm25_query_variants: Sequence[str] = ("en", "terms"),
    ranking_view: str = "parent",
    top_k: int = 10,
    small_top_k: int = 50,
    parent_aggregation: str = "max",
    rrf_k: int = 60,
    require_reviewed_expansions: bool = False,
    query_expansions_path: str | None = None,
) -> DenseEvalReport:
    if ranking_view != "parent":
        raise ValueError("hybrid evaluation currently supports ranking_view='parent' only")
    if top_k < 10:
        raise ValueError("top_k must be at least 10")
    if small_top_k <= 0:
        raise ValueError("small_top_k must be positive")
    expansions = query_expansions or {}
    hits_by_case_id = {}
    query_variants_by_case_id: dict[str, dict[str, str]] = {}

    for case in cases:
        dense_variants = build_query_variants(case, expansions, dense_query_variants, require_reviewed=require_reviewed_expansions)
        bm25_variants = build_query_variants(case, expansions, bm25_query_variants, require_reviewed=require_reviewed_expansions)
        query_variants_by_case_id[case.case_id] = {
            **{f"dense:{variant.name}": variant.text for variant in dense_variants},
            **{f"bm25:{variant.name}": variant.text for variant in bm25_variants},
        }

        dense_hits_by_variant = {}
        for variant in dense_variants:
            hits = hits_from_dense_results(dense_retriever.query(variant.text, top_k=small_top_k))
            dense_hits_by_variant[variant.name] = aggregate_hits_by_parent(hits, top_k=small_top_k, method=parent_aggregation)
        dense_fused = reciprocal_rank_fuse(dense_hits_by_variant, identity="parent", top_k=small_top_k, rrf_k=rrf_k)

        bm25_hits_by_variant = {}
        for variant in bm25_variants:
            hits = hits_from_dense_results(bm25_retriever.query(variant.text, top_k=small_top_k))
            bm25_hits_by_variant[variant.name] = aggregate_hits_by_parent(hits, top_k=small_top_k, method=parent_aggregation)
        bm25_fused = reciprocal_rank_fuse(bm25_hits_by_variant, identity="parent", top_k=small_top_k, rrf_k=rrf_k)

        hits_by_case_id[case.case_id] = reciprocal_rank_fuse(
            {"dense": dense_fused, "bm25": bm25_fused},
            identity="parent",
            top_k=top_k,
            rrf_k=rrf_k,
        )

    report = evaluate_ranked_hits(cases, hits_by_case_id, top_k=max(top_k, 10))
    enriched_results = [
        result.__class__(**{**result.__dict__, "query_variants": query_variants_by_case_id.get(result.case_id, {})})
        for result in report.results
    ]
    return DenseEvalReport(
        case_count=report.case_count,
        hit_at_1=report.hit_at_1,
        hit_at_5=report.hit_at_5,
        hit_at_10=report.hit_at_10,
        mrr_at_10=report.mrr_at_10,
        gold_recall_at_10=report.gold_recall_at_10,
        results=enriched_results,
        retrieval_config={
            "retrieval_method": "hybrid",
            "dense_query_variants": list(dense_query_variants),
            "bm25_query_variants": list(bm25_query_variants),
            "fusion": "rrf",
            "rrf_k": rrf_k,
            "ranking_view": ranking_view,
            "parent_aggregation": parent_aggregation,
            "small_top_k": small_top_k,
            "final_top_k": top_k,
            "query_expansions_path": query_expansions_path,
            "require_reviewed_expansions": require_reviewed_expansions,
        },
    )
```

- [ ] **Step 4: Run hybrid eval tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_hybrid_eval_green tests/test_rag_hybrid_eval.py tests/test_rag_dense_ranking.py tests/test_rag_bm25_eval.py -q
```

Expected:

```text
all selected tests pass
```

---

### Task 4: CLI Integration

**Files:**
- Modify: `src/fireclaw_core/rag/rag_cli.py`
- Create: `tests/test_rag_bm25_cli.py`

**Interfaces:**
- Consumes:
  - `build_bm25_index`
  - `BM25Retriever`
  - `evaluate_bm25_retriever_with_expansion`
  - `evaluate_hybrid_retrievers_with_expansion`
- Produces CLI commands:
  - `build-bm25-index`
  - `query-bm25-index`
  - `eval-bm25-index`
  - `eval-hybrid-index`

- [ ] **Step 1: Write failing CLI tests for build/query/eval BM25**

Create `tests/test_rag_bm25_cli.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fireclaw_core.rag.rag_cli import main


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _record(chunk_id: str, text: str) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "parent_id": f"{chunk_id}__parent",
        "doc_id": "doc",
        "source_file": "raw/doc.pdf",
        "page_start": 1,
        "page_end": 1,
        "heading": "Heading",
        "clean_text": text,
        "indexable": True,
    }


def test_cli_build_and_query_bm25_index(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    records_path = tmp_path / "records.jsonl"
    index_dir = tmp_path / "bm25_index"
    _write_jsonl(
        records_path,
        [
            _record("chunk_scba", "SCBA rehabilitation medical evaluation"),
            _record("chunk_thermal", "thermal imaging victim detection"),
        ],
    )

    build_code = main(["build-bm25-index", "--records", str(records_path), "--index-dir", str(index_dir)])
    assert build_code == 0
    build_output = json.loads(capsys.readouterr().out)
    assert build_output["status"] == "completed"
    assert build_output["indexable_record_count"] == 2

    query_code = main(["query-bm25-index", "--index-dir", str(index_dir), "--query", "SCBA rehabilitation", "--top-k", "1"])
    assert query_code == 0
    query_output = json.loads(capsys.readouterr().out)
    assert query_output["query"] == "SCBA rehabilitation"
    assert query_output["hits"][0]["record"]["chunk_id"] == "chunk_scba"
```

- [ ] **Step 2: Run CLI BM25 test to verify it fails**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_cli_red tests/test_rag_bm25_cli.py::test_cli_build_and_query_bm25_index -q
```

Expected:

```text
SystemExit or argparse error because command build-bm25-index does not exist
```

- [ ] **Step 3: Add BM25 CLI commands**

Modify imports in `src/fireclaw_core/rag/rag_cli.py`:

```python
from fireclaw_core.rag.bm25_retrieval import BM25Retriever
from fireclaw_core.rag.bm25_retrieval import build_bm25_index
from fireclaw_core.rag.dense_eval import evaluate_bm25_retriever_with_expansion
from fireclaw_core.rag.hybrid_eval import evaluate_hybrid_retrievers_with_expansion
```

Add parsers:

```python
bm25_build = subparsers.add_parser("build-bm25-index", help="Build a BM25 lexical index from prepared records.")
bm25_build.add_argument("--corpus-root", default="data/rag/fire_rescue")
bm25_build.add_argument("--records", default=None)
bm25_build.add_argument("--index-dir", default=None)
bm25_build.add_argument("--k1", type=float, default=1.5)
bm25_build.add_argument("--b", type=float, default=0.75)

bm25_query = subparsers.add_parser("query-bm25-index", help="Query a BM25 lexical index.")
bm25_query.add_argument("--corpus-root", default="data/rag/fire_rescue")
bm25_query.add_argument("--index-dir", default=None)
bm25_query.add_argument("--query", required=True)
bm25_query.add_argument("--top-k", type=int, default=5)

bm25_eval = subparsers.add_parser("eval-bm25-index", help="Evaluate BM25 retrieval against gold cases.")
bm25_eval.add_argument("--corpus-root", default="data/rag/fire_rescue")
bm25_eval.add_argument("--index-dir", default=None)
bm25_eval.add_argument("--cases", default=None)
bm25_eval.add_argument("--top-k", type=int, default=10)
bm25_eval.add_argument("--small-top-k", type=int, default=50)
bm25_eval.add_argument("--output", default=None)
bm25_eval.add_argument("--query-expansions", required=True)
bm25_eval.add_argument("--query-variants", default="en,terms")
bm25_eval.add_argument("--ranking-view", choices=["small", "parent"], default="parent")
bm25_eval.add_argument("--parent-aggregation", choices=["max"], default="max")
bm25_eval.add_argument("--fusion", choices=["none", "rrf"], default=None)
bm25_eval.add_argument("--rrf-k", type=int, default=60)
bm25_eval.add_argument("--require-reviewed-expansions", action="store_true")

hybrid_eval = subparsers.add_parser("eval-hybrid-index", help="Evaluate hybrid dense+BM25 retrieval against gold cases.")
hybrid_eval.add_argument("--corpus-root", default="data/rag/fire_rescue")
hybrid_eval.add_argument("--dense-index-dir", default=None)
hybrid_eval.add_argument("--bm25-index-dir", default=None)
hybrid_eval.add_argument("--provider", choices=["fake", "bge-m3"], default="fake")
hybrid_eval.add_argument("--model-path", default=None)
hybrid_eval.add_argument("--cases", default=None)
hybrid_eval.add_argument("--query-expansions", required=True)
hybrid_eval.add_argument("--dense-query-variants", default="zh,en,terms")
hybrid_eval.add_argument("--bm25-query-variants", default="en,terms")
hybrid_eval.add_argument("--small-top-k", type=int, default=50)
hybrid_eval.add_argument("--top-k", type=int, default=10)
hybrid_eval.add_argument("--rrf-k", type=int, default=60)
hybrid_eval.add_argument("--output", default=None)
hybrid_eval.add_argument("--require-reviewed-expansions", action="store_true")
```

Add command dispatch:

```python
if args.command == "build-bm25-index":
    return _cmd_build_bm25_index(args)
if args.command == "query-bm25-index":
    return _cmd_query_bm25_index(args)
if args.command == "eval-bm25-index":
    return _cmd_eval_bm25_index(args)
if args.command == "eval-hybrid-index":
    return _cmd_eval_hybrid_index(args)
```

Add helpers:

```python
def _bm25_index_dir(corpus_root: Path, value: str | None) -> Path:
    return Path(value) if value else corpus_root / "indexes" / "bm25" / "small_v1"


def _cmd_build_bm25_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    records_path = Path(args.records) if args.records else corpus_root / "index_inputs" / "small_index_records.jsonl"
    index_dir = _bm25_index_dir(corpus_root, args.index_dir)
    report = build_bm25_index(records_path, index_dir, k1=args.k1, b=args.b)
    _write_json_output(report.to_dict())
    return 0


def _cmd_query_bm25_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    index_dir = _bm25_index_dir(corpus_root, args.index_dir)
    retriever = BM25Retriever.load(index_dir)
    hits = retriever.query(args.query, top_k=args.top_k)
    _write_json_output({"query": args.query, "hits": [hit.to_dict() for hit in hits]})
    return 0


def _cmd_eval_bm25_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    index_dir = _bm25_index_dir(corpus_root, args.index_dir)
    cases_path = Path(args.cases) if args.cases else corpus_root / "eval" / "dense_gold_cases_zh_v1.jsonl"
    output_path = Path(args.output) if args.output else None
    expansions_path = Path(args.query_expansions)
    retriever = BM25Retriever.load(index_dir)
    cases = load_dense_eval_cases(cases_path)
    expansions = load_query_expansions(expansions_path)
    report = evaluate_bm25_retriever_with_expansion(
        retriever,
        cases,
        query_expansions=expansions,
        query_variants=_parse_csv_arg(args.query_variants),
        ranking_view=args.ranking_view,
        top_k=args.top_k,
        small_top_k=args.small_top_k,
        parent_aggregation=args.parent_aggregation,
        fusion=args.fusion,
        rrf_k=args.rrf_k,
        require_reviewed_expansions=args.require_reviewed_expansions,
        query_expansions_path=str(expansions_path),
    )
    payload = report.to_dict()
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_json_output(payload)
    return 0


def _cmd_eval_hybrid_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    dense_index_dir = Path(args.dense_index_dir) if args.dense_index_dir else corpus_root / "indexes" / "dense" / _provider_index_name(args.provider)
    bm25_index_dir = _bm25_index_dir(corpus_root, args.bm25_index_dir)
    cases_path = Path(args.cases) if args.cases else corpus_root / "eval" / "dense_gold_cases_zh_v1.jsonl"
    output_path = Path(args.output) if args.output else None
    expansions_path = Path(args.query_expansions)
    provider = _create_embedding_provider(args.provider, model_path=args.model_path)
    dense_retriever = DenseRetriever.load(dense_index_dir, provider)
    bm25_retriever = BM25Retriever.load(bm25_index_dir)
    cases = load_dense_eval_cases(cases_path)
    expansions = load_query_expansions(expansions_path)
    report = evaluate_hybrid_retrievers_with_expansion(
        dense_retriever,
        bm25_retriever,
        cases,
        query_expansions=expansions,
        dense_query_variants=_parse_csv_arg(args.dense_query_variants),
        bm25_query_variants=_parse_csv_arg(args.bm25_query_variants),
        small_top_k=args.small_top_k,
        top_k=args.top_k,
        rrf_k=args.rrf_k,
        require_reviewed_expansions=args.require_reviewed_expansions,
        query_expansions_path=str(expansions_path),
    )
    payload = report.to_dict()
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_json_output(payload)
    return 0
```

Add eval helpers using the same output write pattern as `_cmd_eval_dense_index`.

- [ ] **Step 4: Add eval and hybrid CLI tests**

Append tests to `tests/test_rag_bm25_cli.py`:

```python
def test_cli_eval_bm25_index_with_reviewed_expansion(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    records_path = tmp_path / "records.jsonl"
    index_dir = tmp_path / "bm25_index"
    _write_jsonl(records_path, [_record("chunk_gold", "SCBA rehabilitation medical evaluation")])
    assert main(["build-bm25-index", "--records", str(records_path), "--index-dir", str(index_dir)]) == 0
    capsys.readouterr()

    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(cases_path, [{"case_id": "case", "topic": "rehab", "query": "中文", "gold_parent_ids": ["chunk_gold__parent"]}])
    expansions_path = tmp_path / "expansions.jsonl"
    _write_jsonl(
        expansions_path,
        [
            {
                "case_id": "case",
                "query_zh": "中文",
                "reviewed_query_en": "When should firefighters enter SCBA rehabilitation?",
                "term_query": "SCBA rehabilitation medical evaluation",
                "terms": ["SCBA", "rehabilitation", "medical evaluation"],
                "status": "reviewed",
            }
        ],
    )
    output_path = tmp_path / "bm25_report.json"

    exit_code = main(
        [
            "eval-bm25-index",
            "--index-dir", str(index_dir),
            "--cases", str(cases_path),
            "--query-expansions", str(expansions_path),
            "--query-variants", "en,terms",
            "--require-reviewed-expansions",
            "--output", str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["hit_at_1"] == 1.0
    assert report["retrieval_config"]["retrieval_method"] == "bm25"
    assert report["retrieval_config"]["query_variants"] == ["en", "terms"]
    assert json.loads(capsys.readouterr().out)["hit_at_1"] == 1.0
```

Append a hybrid CLI test to `tests/test_rag_bm25_cli.py`:

```python
def test_cli_eval_hybrid_index_with_fake_dense_and_bm25(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider
    from fireclaw_core.rag.dense_retrieval import build_dense_index

    records_path = tmp_path / "records.jsonl"
    _write_jsonl(
        records_path,
        [
            _record("wrong_dense", "rescue generic wrong"),
            _record("gold_bm25", "SCBA rehabilitation medical evaluation rescue"),
        ],
    )
    dense_index_dir = tmp_path / "dense_index"
    build_dense_index(records_path, dense_index_dir, FakeEmbeddingProvider(), batch_size=2)

    bm25_index_dir = tmp_path / "bm25_index"
    assert main(["build-bm25-index", "--records", str(records_path), "--index-dir", str(bm25_index_dir)]) == 0
    capsys.readouterr()

    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [{"case_id": "case", "topic": "rehab", "query": "rescue", "gold_parent_ids": ["gold_bm25__parent"]}],
    )
    expansions_path = tmp_path / "expansions.jsonl"
    _write_jsonl(
        expansions_path,
        [
            {
                "case_id": "case",
                "query_zh": "rescue",
                "reviewed_query_en": "SCBA rehabilitation rescue",
                "term_query": "SCBA rehabilitation medical evaluation",
                "terms": ["SCBA", "rehabilitation", "medical evaluation"],
                "status": "reviewed",
            }
        ],
    )
    output_path = tmp_path / "hybrid_report.json"

    exit_code = main(
        [
            "eval-hybrid-index",
            "--provider", "fake",
            "--dense-index-dir", str(dense_index_dir),
            "--bm25-index-dir", str(bm25_index_dir),
            "--cases", str(cases_path),
            "--query-expansions", str(expansions_path),
            "--dense-query-variants", "zh,en",
            "--bm25-query-variants", "en,terms",
            "--small-top-k", "10",
            "--top-k", "10",
            "--require-reviewed-expansions",
            "--output", str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["retrieval_config"]["retrieval_method"] == "hybrid"
    assert report["retrieval_config"]["dense_query_variants"] == ["zh", "en"]
    assert report["retrieval_config"]["bm25_query_variants"] == ["en", "terms"]
    assert "query_variants" in report["results"][0]
    assert json.loads(capsys.readouterr().out)["retrieval_config"]["retrieval_method"] == "hybrid"
```

- [ ] **Step 5: Run CLI tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_cli_green tests/test_rag_bm25_cli.py tests/test_rag_dense_cli.py -q
```

Expected:

```text
all selected tests pass
```

---

### Task 5: Documentation, Real Index Build, and Ablation Reports

**Files:**
- Modify: `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
- Create or modify: `memory/2026-07-07/fireclaw-bm25-hybrid-retrieval.md`
- Generate: `data/rag/fire_rescue/indexes/bm25/small_v1/`
- Generate:
  - `data/rag/fire_rescue/eval/runs/bm25_small_v1_expanded_parent_report.json`
  - `data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v1_expanded_parent_report.json`

**Interfaces:**
- Consumes:
  - reviewed query expansion file
  - existing dense BGE-M3 index
  - new BM25 index
- Produces:
  - documented ablation commands and metrics

- [ ] **Step 1: Update walkthrough**

Append a section to `docs/rag/dense-evaluation-walkthrough.zh-CN.md` explaining:

```text
BM25 是关键词检索，不使用 embedding。
第一版 BM25 index 建在 small chunk 上。
BM25 主要使用 reviewed English query 和 reviewed terms query。
Hybrid 使用 RRF 融合 dense parent list 和 BM25 parent list。
BM25 score 不能直接和 dense score 相加。
```

- [ ] **Step 2: Run focused unit tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_bm25_hybrid_all tests/test_rag_bm25_retrieval.py tests/test_rag_bm25_eval.py tests/test_rag_hybrid_eval.py tests/test_rag_bm25_cli.py tests/test_rag_dense_eval.py tests/test_rag_dense_ranking.py tests/test_rag_query_expansion.py tests/test_rag_dense_cli.py -q
```

Expected:

```text
all selected tests pass
```

If the managed Windows sandbox raises `PermissionError` for pytest basetemp, rerun the same command with escalation and record the reason in memory.

- [ ] **Step 3: Build real BM25 index**

Run:

```powershell
$env:PYTHONPATH = "src"
python -m fireclaw_core.rag.rag_cli build-bm25-index --records data/rag/fire_rescue/index_inputs/small_index_records.jsonl --index-dir data/rag/fire_rescue/indexes/bm25/small_v1
```

Expected:

```text
exit code 0
status = completed
indexable_record_count matches the dense small record count
token_count > 0
```

- [ ] **Step 4: Run BM25 expanded parent eval**

Run:

```powershell
$env:PYTHONPATH = "src"
python -m fireclaw_core.rag.rag_cli eval-bm25-index --index-dir data/rag/fire_rescue/indexes/bm25/small_v1 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl --query-variants en,terms --ranking-view parent --small-top-k 50 --top-k 10 --require-reviewed-expansions --output data/rag/fire_rescue/eval/runs/bm25_small_v1_expanded_parent_report.json
```

Expected:

```text
exit code 0
retrieval_config.retrieval_method = "bm25"
retrieval_config.query_variants = ["en", "terms"]
metrics recorded in memory
```

- [ ] **Step 5: Run hybrid expanded parent eval**

Run:

```powershell
$env:PYTHONPATH = "src"
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-hybrid-index --provider bge-m3 --model-path .cache/models/bge-m3 --dense-index-dir data/rag/fire_rescue/indexes/dense/bge-m3 --bm25-index-dir data/rag/fire_rescue/indexes/bm25/small_v1 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl --dense-query-variants zh,en,terms --bm25-query-variants en,terms --small-top-k 50 --top-k 10 --require-reviewed-expansions --output data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v1_expanded_parent_report.json
```

Expected:

```text
exit code 0
retrieval_config.retrieval_method = "hybrid"
retrieval_config.dense_query_variants = ["zh", "en", "terms"]
retrieval_config.bm25_query_variants = ["en", "terms"]
metrics recorded in memory
```

- [ ] **Step 6: Update memory**

Create or update `memory/2026-07-07/fireclaw-bm25-hybrid-retrieval.md` with:

```text
task goal
files modified
BM25 tokenizer decisions
BM25 formula parameters k1=1.5, b=0.75
unit test commands and results
BM25 index build command and report path
BM25-only report metrics
hybrid report metrics
comparison against dense strict / dense parent / dense reviewed expanded parent
known limitations
next recommended step
```

- [ ] **Step 7: Final checks**

Run:

```powershell
git diff --check
git status --short
```

Expected:

```text
git diff --check exits 0
git status shows planned files only, aside from known pytest temp directory permission warnings
```

## Self-Review Checklist

- Spec coverage:
  - BM25 local implementation covered.
  - Small-chunk index covered.
  - Reviewed English/terms query variants covered.
  - Parent aggregation and RRF reuse covered.
  - BM25-only and hybrid ablations covered.
  - Dense default behavior preserved.
- Type consistency:
  - BM25 uses `DenseHit` output to reuse `hits_from_dense_results`.
  - Eval reports remain `DenseEvalReport`.
  - Hybrid report uses `retrieval_config.retrieval_method = "hybrid"`.
- Research validity:
  - BM25 and hybrid are ablations, not new gold-label changes.
  - No gold parent broadening.
  - No dense vector rebuild.
  - No unreviewed query expansion in formal commands.
- Repository policy:
  - Plan uses checkpoints rather than commits because commits require explicit user approval.
