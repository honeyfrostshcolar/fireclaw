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


def test_cli_build_and_query_bm25_index(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
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


def test_cli_eval_bm25_index_with_reviewed_expansion(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    records_path = tmp_path / "records.jsonl"
    index_dir = tmp_path / "bm25_index"
    _write_jsonl(records_path, [_record("chunk_gold", "SCBA rehabilitation medical evaluation")])
    assert main(["build-bm25-index", "--records", str(records_path), "--index-dir", str(index_dir)]) == 0
    capsys.readouterr()

    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [{"case_id": "case", "topic": "rehab", "query": "zh query", "gold_parent_ids": ["chunk_gold__parent"]}],
    )
    expansions_path = tmp_path / "expansions.jsonl"
    _write_jsonl(
        expansions_path,
        [
            {
                "case_id": "case",
                "query_zh": "zh query",
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
            "--index-dir",
            str(index_dir),
            "--cases",
            str(cases_path),
            "--query-expansions",
            str(expansions_path),
            "--query-variants",
            "en,terms",
            "--require-reviewed-expansions",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["hit_at_1"] == 1.0
    assert report["retrieval_config"]["retrieval_method"] == "bm25"
    assert report["retrieval_config"]["query_variants"] == ["en", "terms"]
    assert report["retrieval_config"]["require_reviewed_expansions"] is True
    assert json.loads(capsys.readouterr().out)["hit_at_1"] == 1.0


def test_cli_eval_hybrid_index_with_fake_dense_and_bm25(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
            "--provider",
            "fake",
            "--dense-index-dir",
            str(dense_index_dir),
            "--bm25-index-dir",
            str(bm25_index_dir),
            "--cases",
            str(cases_path),
            "--query-expansions",
            str(expansions_path),
            "--dense-query-variants",
            "zh,en",
            "--bm25-query-variants",
            "en,terms",
            "--small-top-k",
            "10",
            "--top-k",
            "10",
            "--require-reviewed-expansions",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["retrieval_config"]["retrieval_method"] == "hybrid"
    assert report["retrieval_config"]["dense_query_variants"] == ["zh", "en"]
    assert report["retrieval_config"]["bm25_query_variants"] == ["en", "terms"]
    assert report["retrieval_config"]["require_reviewed_expansions"] is True
    assert "query_variants" in report["results"][0]
    assert json.loads(capsys.readouterr().out)["retrieval_config"]["retrieval_method"] == "hybrid"
