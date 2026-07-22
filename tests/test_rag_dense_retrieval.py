from __future__ import annotations

import json

import numpy as np

from fireclaw_core.rag.dense_retrieval import EmbeddingModelInfo
from fireclaw_core.rag.dense_retrieval import build_dense_index
from fireclaw_core.rag.dense_retrieval import DenseRetriever
from fireclaw_core.rag.dense_retrieval import expand_hits_to_parents


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


def test_bge_m3_provider_module_import_is_lightweight():
    from fireclaw_core.rag.bge_m3_provider import BGEM3EmbeddingProvider

    assert BGEM3EmbeddingProvider.__name__ == "BGEM3EmbeddingProvider"


def test_bge_m3_provider_sets_project_local_huggingface_cache(monkeypatch, tmp_path):
    from fireclaw_core.rag.bge_m3_provider import _set_huggingface_cache_env

    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("TRANSFORMERS_CACHE", raising=False)
    cache_dir = tmp_path / ".cache" / "huggingface"

    _set_huggingface_cache_env(cache_dir)

    assert cache_dir.exists()
    assert cache_dir.joinpath("transformers").exists()
    assert cache_dir.joinpath("hub").exists()
    assert __import__("os").environ["HF_HOME"] == str(cache_dir)
    assert __import__("os").environ["TRANSFORMERS_CACHE"] == str(cache_dir / "transformers")
