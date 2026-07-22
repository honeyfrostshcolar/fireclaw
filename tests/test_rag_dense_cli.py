from __future__ import annotations

from io import BytesIO
from io import TextIOWrapper
import json
from pathlib import Path

import pytest

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


def test_cli_eval_dense_index_writes_report_with_fake_provider(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider, build_dense_index

    records_path = tmp_path / "records.jsonl"
    records_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "chunk_id": "chunk_rescue",
                        "parent_id": "parent_rescue",
                        "doc_id": "doc_rescue",
                        "clean_text": "rescue victim search",
                        "indexable": True,
                    }
                ),
                json.dumps(
                    {
                        "chunk_id": "chunk_smoke",
                        "parent_id": "parent_smoke",
                        "doc_id": "doc_smoke",
                        "clean_text": "smoke visibility",
                        "indexable": True,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    index_dir = tmp_path / "dense_index"
    build_dense_index(records_path, index_dir, FakeEmbeddingProvider(), batch_size=2)

    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "case_id": "dense_zh_001",
                "topic": "rescue",
                "query": "rescue victim",
                "gold_parent_ids": ["parent_rescue"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "report.json"

    exit_code = main(
        [
            "eval-dense-index",
            "--provider",
            "fake",
            "--index-dir",
            str(index_dir),
            "--cases",
            str(cases_path),
            "--top-k",
            "10",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["case_count"] == 1
    assert report["hit_at_1"] == 1.0
    printed = json.loads(capsys.readouterr().out)
    assert printed["hit_at_1"] == 1.0


def test_cli_eval_dense_index_with_expansion_and_parent_view_writes_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider, build_dense_index

    records_path = tmp_path / "records.jsonl"
    records_path.write_text(
        "\n".join(
            [
                json.dumps({"chunk_id": "wrong_1", "parent_id": "parent_wrong", "doc_id": "doc", "clean_text": "wrong rescue", "indexable": True}),
                json.dumps({"chunk_id": "wrong_2", "parent_id": "parent_wrong", "doc_id": "doc", "clean_text": "wrong victim", "indexable": True}),
                json.dumps({"chunk_id": "gold_1", "parent_id": "parent_gold", "doc_id": "doc", "clean_text": "rescue victim search", "indexable": True}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    index_dir = tmp_path / "dense_index"
    build_dense_index(records_path, index_dir, FakeEmbeddingProvider(), batch_size=2)

    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "case_id": "dense_zh_001",
                "topic": "rescue",
                "query": "rescue victim",
                "gold_parent_ids": ["parent_gold"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    expansions_path = tmp_path / "query_expansions.jsonl"
    expansions_path.write_text(
        json.dumps(
            {
                "case_id": "dense_zh_001",
                "query_zh": "rescue victim",
                "llm_query_en": "rescue victim search",
                "reviewed_query_en": "",
                "term_query": "victim search",
                "terms": ["victim search"],
                "status": "candidate",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "expanded_report.json"

    exit_code = main(
        [
            "eval-dense-index",
            "--provider",
            "fake",
            "--index-dir",
            str(index_dir),
            "--cases",
            str(cases_path),
            "--query-expansions",
            str(expansions_path),
            "--query-variants",
            "zh,en,terms",
            "--ranking-view",
            "parent",
            "--small-top-k",
            "10",
            "--top-k",
            "10",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["retrieval_config"]["query_variants"] == ["zh", "en", "terms"]
    assert report["retrieval_config"]["ranking_view"] == "parent"
    assert report["retrieval_config"]["small_top_k"] == 10
    assert "query_variants" in report["results"][0]
    printed = json.loads(capsys.readouterr().out)
    assert printed["retrieval_config"]["fusion"] == "rrf"


def test_write_json_output_replaces_unencodable_characters_for_gbk_stream():
    raw = BytesIO()
    stream = TextIOWrapper(raw, encoding="gbk", errors="strict", newline="")

    _write_json_output({"text": "private-use-\uf050"}, stream=stream)
    stream.flush()

    assert b"private-use-?" in raw.getvalue()
