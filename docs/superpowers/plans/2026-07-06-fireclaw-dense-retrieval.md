# FireClaw Dense Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build FireClaw's first dense retrieval stage with a model-independent Numpy index, deterministic tests, CLI commands, and BGE-M3 provider wiring.

**Architecture:** Dense retrieval is split into a pure core module and an optional BGE-M3 provider module. The core reads `small_index_records.jsonl`, writes `manifest.json`, `vectors.npy`, and `records.jsonl`, validates provider compatibility at query time, and optionally expands small chunk hits into parent chunks. BGE-M3 is loaded lazily so normal unit tests do not require FlagEmbedding, torch, GPU, or downloaded model files.

**Tech Stack:** Python 3.11+, `numpy>=1.26`, `pytest`, optional `FlagEmbedding` in `.venv-bge-m3` for real BGE-M3 smoke tests.

## Global Constraints

- User-facing replies stay Chinese; code, paths, commands, class names, and errors stay English.
- Do not commit unless the user explicitly asks.
- Use only `indexable == true` rows from `small_index_records.jsonl`.
- Embed `clean_text`.
- Store dense indexes as `manifest.json`, `vectors.npy`, and `records.jsonl`.
- Query providers must match the manifest before search.
- Keep BGE-M3 out of normal unit tests.
- Add `numpy>=1.26` as a FireClaw dependency.
- Preserve existing RAG tests.
- Use TDD: write a failing test, run it red, implement minimal code, run it green.

---

## File Structure

- Create `src/fireclaw_core/rag/dense_retrieval.py`
  - Owns model-independent dense index build, load, query, compatibility validation, and parent expansion.

- Create `src/fireclaw_core/rag/bge_m3_provider.py`
  - Owns optional BGE-M3 embedding provider with lazy imports.

- Modify `src/fireclaw_core/rag/rag_cli.py`
  - Adds `build-dense-index` and `query-dense-index` commands.

- Modify `pyproject.toml`
  - Adds `numpy>=1.26` runtime dependency.

- Create `tests/test_rag_dense_retrieval.py`
  - Tests core behavior without real models.

- Create `tests/test_rag_dense_cli.py`
  - Tests CLI build/query flow with fake provider.

- Update `memory/2026-07-06/fireclaw-rag-retrieval-roadmap.md`
  - Records implemented files, commands, results, and remaining gaps.

---

### Task 1: Dense Index Build Core

**Files:**
- Create: `src/fireclaw_core/rag/dense_retrieval.py`
- Test: `tests/test_rag_dense_retrieval.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces:
  - `EmbeddingModelInfo`
  - `EmbeddingProvider`
  - `DenseIndexManifest`
  - `DenseIndexBuildReport`
  - `build_dense_index(records_path: Path, output_dir: Path, provider: EmbeddingProvider, *, batch_size: int = 32) -> DenseIndexBuildReport`
  - `load_jsonl(path: Path) -> list[dict[str, Any]]`
- Consumes:
  - JSONL rows containing `indexable`, `clean_text`, `chunk_id`, and `parent_id`.

- [ ] **Step 1: Write failing test for building an index from indexable records**

Add this to `tests/test_rag_dense_retrieval.py`:

```python
from __future__ import annotations

import json

import numpy as np

from fireclaw_core.rag.dense_retrieval import EmbeddingModelInfo
from fireclaw_core.rag.dense_retrieval import build_dense_index


class RecordingProvider:
    def __init__(self) -> None:
        self.model_info = EmbeddingModelInfo(
            provider="fake",
            model="fake-dense-v1",
            dimension=3,
            normalized=True,
        )
        self.seen_batches: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        self.seen_batches.append(list(texts))
        vectors = []
        for text in texts:
            if "rescue" in text:
                vectors.append([1.0, 0.0, 0.0])
            elif "smoke" in text:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return np.asarray(vectors, dtype=np.float32)


def _write_jsonl(path, rows):
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _record(chunk_id: str, text: str, *, indexable: bool = True) -> dict:
    return {
        "chunk_id": chunk_id,
        "parent_id": f"{chunk_id}__parent",
        "doc_id": "doc",
        "source_file": "raw/doc.pdf",
        "page_start": 1,
        "page_end": 1,
        "heading": "Heading",
        "clean_text": text,
        "clean_char_count": len(text),
        "clean_word_count": len(text.split()),
        "indexable": indexable,
        "retrieval_weight": 1.0 if indexable else 0.0,
        "cleaning_flags": [],
        "title": "Manual",
        "source_url": "https://example.test/doc.pdf",
        "publisher": "Example",
        "authority_level": "test",
        "allowed_use": "unit_test",
        "domain": "fireground",
        "language": "en",
    }


def test_build_dense_index_writes_manifest_vectors_and_records(tmp_path):
    records_path = tmp_path / "small_index_records.jsonl"
    index_dir = tmp_path / "dense_index"
    _write_jsonl(
        records_path,
        [
            _record("chunk-rescue", "rescue victim search"),
            _record("chunk-skip", "cover page", indexable=False),
            _record("chunk-smoke", "smoke visibility"),
        ],
    )
    provider = RecordingProvider()

    report = build_dense_index(records_path, index_dir, provider, batch_size=1)

    assert report.status == "completed"
    assert report.indexable_record_count == 2
    assert report.skipped_record_count == 1
    assert provider.seen_batches == [["rescue victim search"], ["smoke visibility"]]

    manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["index_type"] == "dense_numpy"
    assert manifest["embedding"]["provider"] == "fake"
    assert manifest["embedding"]["model"] == "fake-dense-v1"
    assert manifest["embedding"]["dimension"] == 3
    assert manifest["record_count"] == 2
    assert manifest["text_field"] == "clean_text"

    vectors = np.load(index_dir / "vectors.npy")
    assert vectors.shape == (2, 3)
    stored_records = [
        json.loads(line)
        for line in (index_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["chunk_id"] for record in stored_records] == ["chunk-rescue", "chunk-smoke"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense tests/test_rag_dense_retrieval.py::test_build_dense_index_writes_manifest_vectors_and_records -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'fireclaw_core.rag.dense_retrieval'`.

- [ ] **Step 3: Add Numpy dependency**

Modify `pyproject.toml` dependencies to include:

```toml
dependencies = [
  "httpx>=0.27",
  "numpy>=1.26",
]
```

- [ ] **Step 4: Write minimal implementation**

Create `src/fireclaw_core/rag/dense_retrieval.py` with these core definitions and build logic:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Protocol

import numpy as np


@dataclass(frozen=True)
class EmbeddingModelInfo:
    provider: str
    model: str
    dimension: int
    normalized: bool
    backend: str | None = None
    device: str | None = None
    pooling: str | None = None
    max_length: int | None = None
    query_instruction: str | None = None
    passage_instruction: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EmbeddingProvider(Protocol):
    model_info: EmbeddingModelInfo

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        ...


@dataclass(frozen=True)
class DenseIndexManifest:
    index_type: str
    created_at: str
    source_records: str
    text_field: str
    record_count: int
    vector_dimension: int
    normalized: bool
    embedding: EmbeddingModelInfo

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["embedding"] = self.embedding.to_dict()
        return data


@dataclass(frozen=True)
class DenseIndexBuildReport:
    status: str
    input_records: str
    output_dir: str
    indexable_record_count: int
    skipped_record_count: int
    vector_count: int
    vector_dimension: int
    files: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_dense_index(
    records_path: Path,
    output_dir: Path,
    provider: EmbeddingProvider,
    *,
    batch_size: int = 32,
) -> DenseIndexBuildReport:
    records_path = Path(records_path)
    output_dir = Path(output_dir)
    if not records_path.exists():
        raise FileNotFoundError(f"Index records JSONL not found: {records_path}")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    all_records = load_jsonl(records_path)
    indexable_records = [record for record in all_records if record.get("indexable") is True]
    if not indexable_records:
        raise ValueError(f"No indexable records found in {records_path}")

    texts = [str(record.get("clean_text") or "") for record in indexable_records]
    vectors = _embed_in_batches(provider, texts, batch_size=batch_size)
    vectors = _validate_vectors(vectors, expected_count=len(indexable_records), provider=provider)

    output_dir.mkdir(parents=True, exist_ok=True)
    vectors_path = output_dir / "vectors.npy"
    records_output_path = output_dir / "records.jsonl"
    manifest_path = output_dir / "manifest.json"

    np.save(vectors_path, vectors.astype(np.float32, copy=False))
    write_jsonl(records_output_path, indexable_records)

    manifest = DenseIndexManifest(
        index_type="dense_numpy",
        created_at=datetime.now(timezone.utc).isoformat(),
        source_records=str(records_path),
        text_field="clean_text",
        record_count=len(indexable_records),
        vector_dimension=int(vectors.shape[1]),
        normalized=provider.model_info.normalized,
        embedding=provider.model_info,
    )
    manifest_path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    return DenseIndexBuildReport(
        status="completed",
        input_records=str(records_path),
        output_dir=str(output_dir),
        indexable_record_count=len(indexable_records),
        skipped_record_count=len(all_records) - len(indexable_records),
        vector_count=int(vectors.shape[0]),
        vector_dimension=int(vectors.shape[1]),
        files={
            "manifest": str(manifest_path),
            "vectors": str(vectors_path),
            "records": str(records_output_path),
        },
    )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL in {path}:{line_no}: {exc}") from exc
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _embed_in_batches(provider: EmbeddingProvider, texts: list[str], *, batch_size: int) -> np.ndarray:
    batches = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        batches.append(provider.embed_texts(batch))
    return np.vstack(batches)


def _validate_vectors(vectors: np.ndarray, *, expected_count: int, provider: EmbeddingProvider) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim != 2:
        raise ValueError(f"Embedding provider returned {vectors.ndim}D vectors; expected 2D")
    if vectors.shape[0] != expected_count:
        raise ValueError(f"Embedding row count {vectors.shape[0]} does not match record count {expected_count}")
    if vectors.shape[1] != provider.model_info.dimension:
        raise ValueError(
            f"Embedding dimension {vectors.shape[1]} does not match provider dimension {provider.model_info.dimension}"
        )
    return vectors
```

- [ ] **Step 5: Run test to verify it passes**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense tests/test_rag_dense_retrieval.py::test_build_dense_index_writes_manifest_vectors_and_records -v
```

Expected: PASS.

---

### Task 2: Dense Retriever Query and Compatibility Validation

**Files:**
- Modify: `src/fireclaw_core/rag/dense_retrieval.py`
- Test: `tests/test_rag_dense_retrieval.py`

**Interfaces:**
- Consumes:
  - `EmbeddingModelInfo`
  - `build_dense_index()`
- Produces:
  - `DenseHit`
  - `DenseRetriever.load(index_dir: Path, provider: EmbeddingProvider) -> DenseRetriever`
  - `DenseRetriever.query(query: str, *, top_k: int = 5) -> list[DenseHit]`

- [ ] **Step 1: Write failing tests for ranked query and provider mismatch**

Append to `tests/test_rag_dense_retrieval.py`:

```python
from fireclaw_core.rag.dense_retrieval import DenseRetriever


class QueryProvider(RecordingProvider):
    def embed_texts(self, texts: list[str]) -> np.ndarray:
        self.seen_batches.append(list(texts))
        vectors = []
        for text in texts:
            if "rescue" in text:
                vectors.append([1.0, 0.0, 0.0])
            elif "smoke" in text:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return np.asarray(vectors, dtype=np.float32)


def test_dense_retriever_returns_sorted_top_k_hits(tmp_path):
    records_path = tmp_path / "small_index_records.jsonl"
    index_dir = tmp_path / "dense_index"
    _write_jsonl(
        records_path,
        [
            _record("chunk-rescue", "rescue victim search"),
            _record("chunk-smoke", "smoke visibility"),
            _record("chunk-thermal", "thermal camera"),
        ],
    )
    build_dense_index(records_path, index_dir, RecordingProvider())

    retriever = DenseRetriever.load(index_dir, QueryProvider())
    hits = retriever.query("rescue question", top_k=2)

    assert [hit.record["chunk_id"] for hit in hits] == ["chunk-rescue", "chunk-thermal"]
    assert hits[0].rank == 1
    assert hits[0].score > hits[1].score
    assert hits[0].record["clean_text"] == "rescue victim search"


def test_dense_retriever_rejects_incompatible_provider(tmp_path):
    records_path = tmp_path / "small_index_records.jsonl"
    index_dir = tmp_path / "dense_index"
    _write_jsonl(records_path, [_record("chunk-rescue", "rescue victim search")])
    build_dense_index(records_path, index_dir, RecordingProvider())

    provider = QueryProvider()
    provider.model_info = EmbeddingModelInfo(
        provider="fake",
        model="other-model",
        dimension=3,
        normalized=True,
    )

    try:
        DenseRetriever.load(index_dir, provider)
    except ValueError as exc:
        assert "incompatible embedding provider" in str(exc)
    else:
        raise AssertionError("DenseRetriever.load should reject incompatible providers")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense tests/test_rag_dense_retrieval.py::test_dense_retriever_returns_sorted_top_k_hits tests/test_rag_dense_retrieval.py::test_dense_retriever_rejects_incompatible_provider -v
```

Expected: FAIL with `ImportError` or `AttributeError` because `DenseRetriever` is not implemented.

- [ ] **Step 3: Implement retriever and compatibility validation**

Add to `src/fireclaw_core/rag/dense_retrieval.py`:

```python
@dataclass(frozen=True)
class DenseHit:
    rank: int
    score: float
    record: dict[str, Any]
    parent: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "rank": self.rank,
            "score": self.score,
            "record": self.record,
        }
        if self.parent is not None:
            data["parent"] = self.parent
        return data


class DenseRetriever:
    def __init__(
        self,
        *,
        index_dir: Path,
        manifest: DenseIndexManifest,
        vectors: np.ndarray,
        records: list[dict[str, Any]],
        provider: EmbeddingProvider,
    ) -> None:
        self.index_dir = index_dir
        self.manifest = manifest
        self.vectors = vectors
        self.records = records
        self.provider = provider

    @classmethod
    def load(cls, index_dir: Path, provider: EmbeddingProvider) -> "DenseRetriever":
        index_dir = Path(index_dir)
        manifest = read_manifest(index_dir / "manifest.json")
        _validate_provider_compatible(manifest.embedding, provider.model_info)
        vectors = np.load(index_dir / "vectors.npy")
        vectors = np.asarray(vectors, dtype=np.float32)
        records = load_jsonl(index_dir / "records.jsonl")
        if vectors.ndim != 2:
            raise ValueError(f"Index vectors must be 2D, got {vectors.ndim}D")
        if vectors.shape[0] != len(records):
            raise ValueError(f"Vector row count {vectors.shape[0]} does not match records count {len(records)}")
        if vectors.shape[1] != manifest.vector_dimension:
            raise ValueError(
                f"Vector dimension {vectors.shape[1]} does not match manifest dimension {manifest.vector_dimension}"
            )
        return cls(index_dir=index_dir, manifest=manifest, vectors=vectors, records=records, provider=provider)

    def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        query_vectors = self.provider.embed_texts([query])
        query_vectors = _validate_vectors(query_vectors, expected_count=1, provider=self.provider)
        scores = query_vectors[0] @ self.vectors.T
        limit = min(top_k, len(self.records))
        ranked_indexes = np.argsort(scores)[::-1][:limit]
        return [
            DenseHit(rank=rank, score=float(scores[index]), record=self.records[int(index)])
            for rank, index in enumerate(ranked_indexes, start=1)
        ]


def read_manifest(path: Path) -> DenseIndexManifest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    embedding = EmbeddingModelInfo(**data["embedding"])
    return DenseIndexManifest(
        index_type=data["index_type"],
        created_at=data["created_at"],
        source_records=data["source_records"],
        text_field=data["text_field"],
        record_count=int(data["record_count"]),
        vector_dimension=int(data["vector_dimension"]),
        normalized=bool(data["normalized"]),
        embedding=embedding,
    )


def _validate_provider_compatible(expected: EmbeddingModelInfo, actual: EmbeddingModelInfo) -> None:
    fields = (
        "provider",
        "model",
        "dimension",
        "normalized",
        "query_instruction",
        "passage_instruction",
    )
    mismatches = [field for field in fields if getattr(expected, field) != getattr(actual, field)]
    if mismatches:
        raise ValueError(f"incompatible embedding provider for index manifest: {', '.join(mismatches)}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense tests/test_rag_dense_retrieval.py::test_dense_retriever_returns_sorted_top_k_hits tests/test_rag_dense_retrieval.py::test_dense_retriever_rejects_incompatible_provider -v
```

Expected: PASS.

---

### Task 3: Parent Chunk Expansion

**Files:**
- Modify: `src/fireclaw_core/rag/dense_retrieval.py`
- Test: `tests/test_rag_dense_retrieval.py`

**Interfaces:**
- Consumes:
  - `DenseHit`
  - parent chunk JSONL with `parent_id`
- Produces:
  - `expand_hits_to_parents(hits: list[DenseHit], parent_chunks_path: Path) -> list[DenseHit]`

- [ ] **Step 1: Write failing test for parent expansion**

Append to `tests/test_rag_dense_retrieval.py`:

```python
from fireclaw_core.rag.dense_retrieval import expand_hits_to_parents


def test_expand_hits_to_parents_attaches_parent_metadata(tmp_path):
    records_path = tmp_path / "small_index_records.jsonl"
    index_dir = tmp_path / "dense_index"
    parent_path = tmp_path / "parent_chunks.jsonl"
    _write_jsonl(records_path, [_record("chunk-rescue", "rescue victim search")])
    _write_jsonl(
        parent_path,
        [
            {
                "parent_id": "chunk-rescue__parent",
                "doc_id": "doc",
                "text": "Long parent context about rescue victim search.",
                "page_start": 1,
                "page_end": 2,
                "title": "Manual",
            }
        ],
    )
    build_dense_index(records_path, index_dir, RecordingProvider())
    hits = DenseRetriever.load(index_dir, QueryProvider()).query("rescue", top_k=1)

    expanded = expand_hits_to_parents(hits, parent_path)

    assert expanded[0].parent is not None
    assert expanded[0].parent["parent_id"] == "chunk-rescue__parent"
    assert "Long parent context" in expanded[0].parent["text"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense tests/test_rag_dense_retrieval.py::test_expand_hits_to_parents_attaches_parent_metadata -v
```

Expected: FAIL because `expand_hits_to_parents` is not implemented.

- [ ] **Step 3: Implement parent expansion**

Add to `src/fireclaw_core/rag/dense_retrieval.py`:

```python
def expand_hits_to_parents(hits: list[DenseHit], parent_chunks_path: Path) -> list[DenseHit]:
    parent_chunks_path = Path(parent_chunks_path)
    if not parent_chunks_path.exists():
        raise FileNotFoundError(f"Parent chunks JSONL not found: {parent_chunks_path}")
    parents = {row["parent_id"]: row for row in load_jsonl(parent_chunks_path)}
    expanded: list[DenseHit] = []
    for hit in hits:
        parent = parents.get(hit.record.get("parent_id"))
        expanded.append(DenseHit(rank=hit.rank, score=hit.score, record=hit.record, parent=parent))
    return expanded
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense tests/test_rag_dense_retrieval.py::test_expand_hits_to_parents_attaches_parent_metadata -v
```

Expected: PASS.

---

### Task 4: CLI Fake Provider Build and Query

**Files:**
- Modify: `src/fireclaw_core/rag/rag_cli.py`
- Modify: `src/fireclaw_core/rag/dense_retrieval.py`
- Test: `tests/test_rag_dense_cli.py`

**Interfaces:**
- Consumes:
  - `build_dense_index()`
  - `DenseRetriever`
  - `expand_hits_to_parents()`
- Produces:
  - CLI subcommand `build-dense-index`
  - CLI subcommand `query-dense-index`
  - `create_embedding_provider(provider_name: str, model_path: str | None = None) -> EmbeddingProvider`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_rag_dense_cli.py`:

```python
from __future__ import annotations

import json

from fireclaw_core.rag.rag_cli import main


def _write_jsonl(path, rows):
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _record(chunk_id: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "parent_id": f"{chunk_id}__parent",
        "doc_id": "doc",
        "source_file": "raw/doc.pdf",
        "page_start": 1,
        "page_end": 1,
        "heading": "Heading",
        "clean_text": text,
        "clean_char_count": len(text),
        "clean_word_count": len(text.split()),
        "indexable": True,
        "retrieval_weight": 1.0,
        "cleaning_flags": [],
        "title": "Manual",
        "source_url": "https://example.test/doc.pdf",
        "publisher": "Example",
        "authority_level": "test",
        "allowed_use": "unit_test",
        "domain": "fireground",
        "language": "en",
    }


def test_cli_build_and_query_dense_index_with_fake_provider(tmp_path, capsys):
    records_path = tmp_path / "small_index_records.jsonl"
    index_dir = tmp_path / "dense_index"
    _write_jsonl(
        records_path,
        [
            _record("chunk-rescue", "rescue victim search"),
            _record("chunk-smoke", "smoke visibility"),
        ],
    )

    build_code = main(
        [
            "build-dense-index",
            "--provider",
            "fake",
            "--records",
            str(records_path),
            "--index-dir",
            str(index_dir),
        ]
    )
    assert build_code == 0
    build_output = json.loads(capsys.readouterr().out)
    assert build_output["status"] == "completed"
    assert build_output["indexable_record_count"] == 2

    query_code = main(
        [
            "query-dense-index",
            "--provider",
            "fake",
            "--index-dir",
            str(index_dir),
            "--query",
            "rescue",
            "--top-k",
            "1",
        ]
    )
    assert query_code == 0
    query_output = json.loads(capsys.readouterr().out)
    assert query_output["query"] == "rescue"
    assert query_output["hits"][0]["record"]["chunk_id"] == "chunk-rescue"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense tests/test_rag_dense_cli.py::test_cli_build_and_query_dense_index_with_fake_provider -v
```

Expected: FAIL because CLI commands are not defined.

- [ ] **Step 3: Add deterministic fake provider to dense core**

Add to `src/fireclaw_core/rag/dense_retrieval.py`:

```python
class FakeEmbeddingProvider:
    def __init__(self, *, dimension: int = 8) -> None:
        self.model_info = EmbeddingModelInfo(
            provider="fake",
            model="fake-dense-v1",
            dimension=dimension,
            normalized=True,
            backend="deterministic-keyword-buckets",
        )

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), self.model_info.dimension), dtype=np.float32)
        buckets = {
            "rescue": 0,
            "victim": 0,
            "search": 0,
            "smoke": 1,
            "visibility": 1,
            "thermal": 2,
            "camera": 2,
        }
        for row, text in enumerate(texts):
            tokens = [token.strip().lower() for token in text.split() if token.strip()]
            for token in tokens:
                column = buckets.get(token, sum(ord(char) for char in token) % self.model_info.dimension)
                vectors[row, column] += 1.0
            norm = float(np.linalg.norm(vectors[row]))
            if norm > 0:
                vectors[row] /= norm
        return vectors
```

- [ ] **Step 4: Add CLI commands**

Modify `src/fireclaw_core/rag/rag_cli.py`:

```python
from fireclaw_core.rag.dense_retrieval import DenseRetriever
from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider
from fireclaw_core.rag.dense_retrieval import build_dense_index
from fireclaw_core.rag.dense_retrieval import expand_hits_to_parents
```

Add parsers inside `main()`:

```python
    dense_build = subparsers.add_parser("build-dense-index", help="Build a dense vector index from prepared records.")
    dense_build.add_argument("--corpus-root", default="data/rag/fire_rescue")
    dense_build.add_argument("--records", default=None)
    dense_build.add_argument("--index-dir", default=None)
    dense_build.add_argument("--provider", choices=["fake", "bge-m3"], default="fake")
    dense_build.add_argument("--model-path", default=None)
    dense_build.add_argument("--batch-size", type=int, default=32)

    dense_query = subparsers.add_parser("query-dense-index", help="Query a dense vector index.")
    dense_query.add_argument("--corpus-root", default="data/rag/fire_rescue")
    dense_query.add_argument("--index-dir", default=None)
    dense_query.add_argument("--provider", choices=["fake", "bge-m3"], default="fake")
    dense_query.add_argument("--model-path", default=None)
    dense_query.add_argument("--query", required=True)
    dense_query.add_argument("--top-k", type=int, default=5)
    dense_query.add_argument("--parents", action="store_true")
    dense_query.add_argument("--parent-chunks", default=None)
```

Add command dispatch:

```python
    if args.command == "build-dense-index":
        return _cmd_build_dense_index(args)
    if args.command == "query-dense-index":
        return _cmd_query_dense_index(args)
```

Add helpers:

```python
def _cmd_build_dense_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    records_path = Path(args.records) if args.records else corpus_root / "index_inputs" / "small_index_records.jsonl"
    index_dir = Path(args.index_dir) if args.index_dir else corpus_root / "indexes" / "dense" / _provider_index_name(args.provider)
    provider = _create_embedding_provider(args.provider, model_path=args.model_path)
    report = build_dense_index(records_path, index_dir, provider, batch_size=args.batch_size)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _cmd_query_dense_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    index_dir = Path(args.index_dir) if args.index_dir else corpus_root / "indexes" / "dense" / _provider_index_name(args.provider)
    provider = _create_embedding_provider(args.provider, model_path=args.model_path)
    retriever = DenseRetriever.load(index_dir, provider)
    hits = retriever.query(args.query, top_k=args.top_k)
    if args.parents:
        parent_chunks_path = Path(args.parent_chunks) if args.parent_chunks else corpus_root / "chunks" / "parent_chunks.jsonl"
        hits = expand_hits_to_parents(hits, parent_chunks_path)
    print(json.dumps({"query": args.query, "hits": [hit.to_dict() for hit in hits]}, ensure_ascii=False, indent=2))
    return 0


def _create_embedding_provider(provider_name: str, *, model_path: str | None = None):
    if provider_name == "fake":
        return FakeEmbeddingProvider()
    if provider_name == "bge-m3":
        from fireclaw_core.rag.bge_m3_provider import BGEM3EmbeddingProvider

        return BGEM3EmbeddingProvider(model_path=Path(model_path) if model_path else Path(".cache/models/bge-m3"))
    raise ValueError(f"Unsupported dense embedding provider: {provider_name}")


def _provider_index_name(provider_name: str) -> str:
    if provider_name == "bge-m3":
        return "bge-m3"
    return provider_name
```

- [ ] **Step 5: Run CLI test to verify it passes**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense tests/test_rag_dense_cli.py::test_cli_build_and_query_dense_index_with_fake_provider -v
```

Expected: PASS.

---

### Task 5: BGE-M3 Provider

**Files:**
- Create: `src/fireclaw_core/rag/bge_m3_provider.py`
- Test: `tests/test_rag_dense_retrieval.py`

**Interfaces:**
- Consumes:
  - `EmbeddingModelInfo`
- Produces:
  - `BGEM3EmbeddingProvider(model_path: Path, *, device: str | None = None, batch_size: int = 32)`

- [ ] **Step 1: Write failing test that BGE provider can be imported without FlagEmbedding installed**

Append to `tests/test_rag_dense_retrieval.py`:

```python
def test_bge_m3_provider_module_import_is_lightweight():
    from fireclaw_core.rag.bge_m3_provider import BGEM3EmbeddingProvider

    assert BGEM3EmbeddingProvider.__name__ == "BGEM3EmbeddingProvider"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense tests/test_rag_dense_retrieval.py::test_bge_m3_provider_module_import_is_lightweight -v
```

Expected: FAIL because `bge_m3_provider.py` does not exist.

- [ ] **Step 3: Implement lazy BGE-M3 provider**

Create `src/fireclaw_core/rag/bge_m3_provider.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from fireclaw_core.rag.dense_retrieval import EmbeddingModelInfo


class BGEM3EmbeddingProvider:
    def __init__(
        self,
        model_path: Path,
        *,
        device: str | None = None,
        batch_size: int = 32,
    ) -> None:
        self.model_path = Path(model_path)
        self.device = device
        self.batch_size = batch_size
        self.model_info = EmbeddingModelInfo(
            provider="bge-m3",
            model=str(self.model_path),
            dimension=1024,
            normalized=True,
            backend="FlagEmbedding.BGEM3FlagModel",
            device=device,
            pooling="bge-m3-dense",
        )
        self._model: Any | None = None

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        model = self._load_model()
        result = model.encode(texts, batch_size=self.batch_size, return_dense=True, return_sparse=False, return_colbert_vecs=False)
        dense_vectors = result["dense_vecs"]
        return np.asarray(dense_vectors, dtype=np.float32)

    def _load_model(self) -> Any:
        if self._model is None:
            if not self.model_path.exists():
                raise FileNotFoundError(f"BGE-M3 model path not found: {self.model_path}")
            try:
                from FlagEmbedding import BGEM3FlagModel
            except ImportError as exc:
                raise ImportError(
                    "FlagEmbedding is required for --provider bge-m3. Use .\\.venv-bge-m3\\Scripts\\python.exe."
                ) from exc
            kwargs: dict[str, Any] = {"use_fp16": False}
            if self.device is not None:
                kwargs["devices"] = self.device
            self._model = BGEM3FlagModel(str(self.model_path), **kwargs)
        return self._model
```

- [ ] **Step 4: Run lightweight provider import test**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense tests/test_rag_dense_retrieval.py::test_bge_m3_provider_module_import_is_lightweight -v
```

Expected: PASS.

- [ ] **Step 5: Run optional real BGE-M3 smoke after all unit tests pass**

Run only after the normal test suite is green:

```powershell
$env:PYTHONPATH = "src"; .\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli build-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --batch-size 16
```

Expected: command prints JSON with `"status": "completed"` and creates `data/rag/fire_rescue/indexes/dense/bge-m3/`.

Then run:

```powershell
$env:PYTHONPATH = "src"; .\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli query-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --query "smoke-filled victim search" --top-k 3 --parents
```

Expected: command prints JSON with three ranked hits.

---

### Task 6: Regression Verification and Memory Update

**Files:**
- Modify: `memory/2026-07-06/fireclaw-rag-retrieval-roadmap.md`

**Interfaces:**
- Consumes:
  - all previous tasks.
- Produces:
  - validation record for later sessions.

- [ ] **Step 1: Run dense retrieval tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense tests/test_rag_dense_retrieval.py tests/test_rag_dense_cli.py
```

Expected: all dense retrieval tests pass.

- [ ] **Step 2: Run existing RAG tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_rag_after_dense tests/test_rag_extraction.py tests/test_rag_chunking.py tests/test_rag_index_preparation.py
```

Expected: existing RAG tests still pass.

- [ ] **Step 3: Run optional BGE-M3 smoke commands**

Run the two `.venv-bge-m3` commands from Task 5 Step 5 if the local model and environment are still present.

Expected: build and query commands complete without importing BGE-M3 in the normal Python environment.

- [ ] **Step 4: Update memory**

Append a timestamped implementation note to `memory/2026-07-06/fireclaw-rag-retrieval-roadmap.md` with:

```markdown
## Dense Retrieval Implementation Update

**Timestamp:** <current Asia/Shanghai time>

- Files added:
  - `src/fireclaw_core/rag/dense_retrieval.py`
  - `src/fireclaw_core/rag/bge_m3_provider.py`
  - `tests/test_rag_dense_retrieval.py`
  - `tests/test_rag_dense_cli.py`
- Files modified:
  - `src/fireclaw_core/rag/rag_cli.py`
  - `pyproject.toml`
- Validation:
  - `<dense test command>` -> `<result>`
  - `<existing RAG test command>` -> `<result>`
  - `<BGE-M3 smoke command>` -> `<result or skipped reason>`
- Remaining gaps:
  - BM25, hybrid retrieval, reranking, and MiniLM ONNX remain later roadmap items.
```

- [ ] **Step 5: Inspect git diff**

Run:

```powershell
git status --short --branch
git diff --stat
```

Expected: only dense retrieval, tests, CLI, pyproject, spec/plan, and memory files are changed.
