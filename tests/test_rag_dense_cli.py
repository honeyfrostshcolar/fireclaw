from __future__ import annotations

from io import BytesIO
from io import TextIOWrapper
import json

from fireclaw_core.rag.rag_cli import _write_json_output
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


def test_write_json_output_replaces_unencodable_characters_for_gbk_stream():
    raw = BytesIO()
    stream = TextIOWrapper(raw, encoding="gbk", errors="strict", newline="")

    _write_json_output({"text": "private-use-\uf050"}, stream=stream)
    stream.flush()

    assert b"private-use-?" in raw.getvalue()
