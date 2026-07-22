from __future__ import annotations

import json

from fireclaw_core.rag.chunking import ChunkingConfig
from fireclaw_core.rag.chunking import PageRecord
from fireclaw_core.rag.chunking import chunk_document
from fireclaw_core.rag.chunking import recursive_split_text
from fireclaw_core.rag.corpus_chunking import chunk_corpus_pages


def test_recursive_split_prefers_headings_before_character_fallback():
    text = (
        "1. Fire Behavior\n\n"
        + "Smoke movement depends on heat, ventilation, and geometry. " * 20
        + "\n\n2. Thermal Imaging\n\n"
        + "Thermal cameras support search, but readings need context. " * 20
    )

    pieces = recursive_split_text(text, max_chars=500)

    assert len(pieces) > 2
    assert pieces[0].startswith("1. Fire Behavior")
    assert any(piece.startswith("2. Thermal Imaging") for piece in pieces)
    assert all(len(piece) <= 500 for piece in pieces)


def test_chunk_document_preserves_page_metadata_and_parent_linkage():
    pages = [
        PageRecord(
            doc_id="sample_fire_manual",
            source_file="raw/sample_fire_manual.pdf",
            page=1,
            text="1. Fireground Size-Up\n\n" + "Assess smoke, heat, access, and victims. " * 18,
            title="Sample Fire Manual",
            source_url="https://example.test/fire.pdf",
            publisher="Example Fire Academy",
            authority_level="test",
            allowed_use="unit_test",
            domain="fireground_operations",
            language="en",
        ),
        PageRecord(
            doc_id="sample_fire_manual",
            source_file="raw/sample_fire_manual.pdf",
            page=2,
            text="2. Thermal Imaging\n\n" + "Thermal images can mislead near glass and reflective surfaces. " * 18,
            title="Sample Fire Manual",
            source_url="https://example.test/fire.pdf",
            publisher="Example Fire Academy",
            authority_level="test",
            allowed_use="unit_test",
            domain="fireground_operations",
            language="en",
        ),
    ]
    config = ChunkingConfig(
        parent_target_chars=600,
        parent_max_chars=900,
        parent_min_chars=200,
        parent_max_pages=1,
        small_target_chars=280,
        small_max_chars=420,
        small_min_chars=120,
        small_overlap_chars=40,
    )

    parents, smalls = chunk_document(pages, config)

    assert len(parents) >= 2
    assert {parent.page_start for parent in parents} == {1, 2}
    assert all(parent.page_start == parent.page_end for parent in parents)
    assert all(small.parent_id in {parent.parent_id for parent in parents} for small in smalls)
    assert all(small.title == "Sample Fire Manual" for small in smalls)
    assert all(small.source_url == "https://example.test/fire.pdf" for small in smalls)


def test_chunk_corpus_pages_writes_parent_small_and_report(tmp_path):
    pages_path = tmp_path / "pages.jsonl"
    output_dir = tmp_path / "chunks"
    records = [
        {
            "doc_id": "sample_fire_manual",
            "source_file": "raw/sample_fire_manual.pdf",
            "page": 1,
            "text": "1. Fireground Size-Up\n\n" + "Assess changing smoke and heat conditions. " * 16,
            "title": "Sample Fire Manual",
            "source_url": "https://example.test/fire.pdf",
            "publisher": "Example Fire Academy",
            "authority_level": "test",
            "allowed_use": "unit_test",
            "domain": "fireground_operations",
            "language": "en",
        }
    ]
    pages_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )
    config = ChunkingConfig(
        parent_target_chars=500,
        parent_max_chars=800,
        parent_min_chars=200,
        small_target_chars=240,
        small_max_chars=360,
        small_min_chars=100,
        small_overlap_chars=30,
    )

    report = chunk_corpus_pages(pages_path=pages_path, output_dir=output_dir, config=config)

    assert report.status == "completed"
    assert report.document_count == 1
    assert report.parent_chunk_count >= 1
    assert report.small_chunk_count >= 1
    parents = [
        json.loads(line)
        for line in (output_dir / "parent_chunks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    smalls = [
        json.loads(line)
        for line in (output_dir / "small_chunks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert parents[0]["parent_id"].startswith("sample_fire_manual__parent_")
    assert smalls[0]["parent_id"] == parents[0]["parent_id"]
    assert (output_dir / "chunk_report.json").exists()

