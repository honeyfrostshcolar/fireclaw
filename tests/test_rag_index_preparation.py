from __future__ import annotations

import json

from fireclaw_core.rag.index_preparation import IndexPreparationConfig
from fireclaw_core.rag.index_preparation import clean_text_for_embedding
from fireclaw_core.rag.index_preparation import prepare_index_record
from fireclaw_core.rag.index_preparation import prepare_index_records


def _chunk(**overrides):
    data = {
        "chunk_id": "doc__parent_00001__small_001",
        "parent_id": "doc__parent_00001",
        "doc_id": "doc",
        "source_file": "raw/doc.pdf",
        "page_start": 3,
        "page_end": 3,
        "heading": "Fireground Safety",
        "text": "Firefighters should monitor smoke, heat, and structural conditions.",
        "char_count": 65,
        "word_count": 8,
        "title": "Fire Manual",
        "source_url": "https://example.test/doc.pdf",
        "publisher": "Example Fire Academy",
        "authority_level": "test",
        "allowed_use": "unit_test",
        "domain": "fireground_operations",
        "language": "en",
    }
    data.update(overrides)
    return data


def test_clean_text_removes_control_chars_but_keeps_formula_symbols():
    clean_text, removed = clean_text_for_embedding("Smoke\x10 flow ∇T ≤ 300\n\n\nHeat")

    assert removed == 1
    assert "\x10" not in clean_text
    assert "∇T ≤ 300" in clean_text
    assert clean_text == "Smoke flow ∇T ≤ 300\n\nHeat"


def test_prepare_index_record_marks_blank_page_as_non_indexable():
    record = prepare_index_record(
        _chunk(
            heading="- BLANK PAGE -",
            text="- BLANK PAGE -",
            char_count=14,
            word_count=2,
        )
    )

    assert record.indexable is False
    assert record.retrieval_weight == 0.0
    assert "blank_page_like" in record.cleaning_flags
    assert "too_short" in record.cleaning_flags


def test_prepare_index_record_keeps_short_content_but_downweights_it():
    record = prepare_index_record(
        _chunk(text="Check SCBA pressure before entry.", char_count=33, word_count=5),
        IndexPreparationConfig(min_clean_chars=80, min_clean_words=3),
    )

    assert record.indexable is True
    assert record.retrieval_weight == 0.5
    assert "too_short" in record.cleaning_flags


def test_prepare_index_records_writes_jsonl_and_report(tmp_path):
    input_path = tmp_path / "small_chunks.jsonl"
    output_dir = tmp_path / "index_inputs"
    rows = [
        _chunk(text="Fireground operations require continuous size-up." * 8),
        _chunk(
            chunk_id="doc__parent_00001__small_002",
            heading="- BLANK PAGE -",
            text="- BLANK PAGE -",
            char_count=14,
            word_count=2,
        ),
    ]
    input_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    report = prepare_index_records(small_chunks_path=input_path, output_dir=output_dir)

    assert report.status == "completed"
    assert report.source_chunk_count == 2
    assert report.index_record_count == 2
    assert report.indexable_count == 1
    records = [
        json.loads(line)
        for line in (output_dir / "small_index_records.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["chunk_id"] == "doc__parent_00001__small_001"
    assert records[0]["source_kind"] == "external_knowledge"
    assert records[0]["clean_text"]
    assert records[1]["indexable"] is False
    assert (output_dir / "index_preparation_report.json").exists()
