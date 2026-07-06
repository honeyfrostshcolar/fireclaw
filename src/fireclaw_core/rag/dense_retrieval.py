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


@dataclass(frozen=True)
class DenseHit:
    rank: int
    score: float
    record: dict[str, Any]
    parent: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
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
    def load(cls, index_dir: Path, provider: EmbeddingProvider) -> DenseRetriever:
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
                bucket = buckets.get(token, sum(ord(char) for char in token))
                column = bucket % self.model_info.dimension
                vectors[row, column] += 1.0
            norm = float(np.linalg.norm(vectors[row]))
            if norm > 0:
                vectors[row] /= norm
        return vectors


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
