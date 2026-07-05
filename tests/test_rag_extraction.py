from __future__ import annotations

import json

from fireclaw_core.rag.corpus_extraction import extract_corpus_pages
from fireclaw_core.rag.extraction import clean_page_text
from fireclaw_core.rag.extraction import text_quality


class FakePageExtractor:
    method_name = "fake"

    def extract_pages(self, pdf_path):
        return [
            clean_page_text("Fire simu-\nlation text.\n\n\nSecond line."),
            "",
        ]


def test_clean_page_text_repairs_hyphenated_line_breaks():
    assert clean_page_text("Fire simu-\nlation\n\n\ntext") == "Fire simulation\n\ntext"


def test_text_quality_marks_empty_short_and_ok_text():
    assert text_quality("") == "empty"
    assert text_quality("short") == "short"
    assert text_quality("fire " * 40) == "ok"


def test_extract_corpus_pages_writes_page_jsonl_and_report(tmp_path):
    corpus_root = tmp_path / "fire_rescue"
    raw_dir = corpus_root / "raw"
    manifest_dir = corpus_root / "manifests"
    output_dir = corpus_root / "extracted"
    raw_dir.mkdir(parents=True)
    manifest_dir.mkdir(parents=True)
    pdf_path = raw_dir / "sample_fire_robot.pdf"
    pdf_path.write_bytes(b"%PDF fake")
    manifest_path = manifest_dir / "sources.jsonl"
    manifest_path.write_text(
        json.dumps(
            {
                "file": "raw/sample_fire_robot.pdf",
                "title": "Sample Fire Robot Manual",
                "source_url": "https://example.test/manual.pdf",
                "publisher": "Example Fire Lab",
                "authority_level": "test",
                "allowed_use": "unit_test",
                "domain": "fire_robotics",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = extract_corpus_pages(
        corpus_root=corpus_root,
        raw_dir=raw_dir,
        output_dir=output_dir,
        manifest_paths=[manifest_path],
        extractor=FakePageExtractor(),
    )

    assert report.status == "completed"
    assert report.document_count == 1
    assert report.page_count == 2
    pages = [
        json.loads(line)
        for line in (output_dir / "pages.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert pages[0]["doc_id"] == "sample_fire_robot"
    assert pages[0]["source_file"] == "raw/sample_fire_robot.pdf"
    assert pages[0]["page"] == 1
    assert pages[0]["text"] == "Fire simulation text.\n\nSecond line."
    assert pages[0]["title"] == "Sample Fire Robot Manual"
    assert pages[0]["source_url"] == "https://example.test/manual.pdf"
    assert pages[1]["text_quality"] == "empty"
    assert (output_dir / "extraction_report.json").exists()
