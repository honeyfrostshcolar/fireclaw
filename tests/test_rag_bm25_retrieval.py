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
