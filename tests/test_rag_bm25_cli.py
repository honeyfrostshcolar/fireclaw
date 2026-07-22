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


def test_cli_eval_hybrid_index_with_relevance_judgments_adds_ndcg(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider
    from fireclaw_core.rag.dense_retrieval import build_dense_index

    records_path = tmp_path / "records.jsonl"
    _write_jsonl(
        records_path,
        [
            _record("wrong_dense", "generic rescue"),
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
    judgments_path = tmp_path / "judgments.jsonl"
    _write_jsonl(
        judgments_path,
        [
            {
                "case_id": "case",
                "parent_id": "gold_bm25__parent",
                "grade": 3,
                "source": "agent-reviewed evaluation label; not human gold",
            }
        ],
    )
    output_path = tmp_path / "hybrid_graded_report.json"

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
            "--relevance-judgments",
            str(judgments_path),
            "--relevance-threshold",
            "2",
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
    assert report["ndcg_at_10"] == 1.0
    assert report["results"][0]["ndcg_at_10"] == 1.0
    assert report["retrieval_config"]["relevance_judgments_path"] == str(judgments_path)
    assert report["retrieval_config"]["relevance_threshold"] == 2
    assert json.loads(capsys.readouterr().out)["ndcg_at_10"] == 1.0


def test_cli_eval_hybrid_rerank_index_with_fake_reranker(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider
    from fireclaw_core.rag.dense_retrieval import build_dense_index

    records_path = tmp_path / "records.jsonl"
    _write_jsonl(
        records_path,
        [
            _record("generic", "generic firefighter health rescue"),
            _record("gold", "SCBA rehabilitation medical evaluation NFPA 1584 rescue"),
        ],
    )
    dense_index_dir = tmp_path / "dense_index"
    build_dense_index(records_path, dense_index_dir, FakeEmbeddingProvider(), batch_size=2)

    bm25_index_dir = tmp_path / "bm25_index"
    assert main(["build-bm25-index", "--records", str(records_path), "--index-dir", str(bm25_index_dir)]) == 0
    capsys.readouterr()

    parent_chunks_path = tmp_path / "parent_chunks.jsonl"
    _write_jsonl(
        parent_chunks_path,
        [
            {"parent_id": "generic__parent", "text": "generic firefighter health information"},
            {"parent_id": "gold__parent", "text": "SCBA rehabilitation medical evaluation NFPA 1584"},
        ],
    )

    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "case_id": "case",
                "topic": "rehab",
                "query": "generic rescue",
                "gold_parent_ids": ["gold__parent"],
            }
        ],
    )
    expansions_path = tmp_path / "expansions.jsonl"
    _write_jsonl(
        expansions_path,
        [
            {
                "case_id": "case",
                "query_zh": "generic rescue",
                "reviewed_query_en": "When should firefighters enter SCBA rehabilitation?",
                "term_query": "generic firefighter health rescue",
                "terms": ["generic", "firefighter", "health", "rescue"],
                "status": "reviewed",
            }
        ],
    )
    output_path = tmp_path / "hybrid_rerank_report.json"

    exit_code = main(
        [
            "eval-hybrid-rerank-index",
            "--provider",
            "fake",
            "--dense-index-dir",
            str(dense_index_dir),
            "--bm25-index-dir",
            str(bm25_index_dir),
            "--reranker-provider",
            "fake",
            "--parent-chunks",
            str(parent_chunks_path),
            "--cases",
            str(cases_path),
            "--query-expansions",
            str(expansions_path),
            "--dense-query-variants",
            "zh,en",
            "--bm25-query-variants",
            "terms",
            "--require-reviewed-expansions",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["retrieval_config"]["retrieval_method"] == "hybrid_rerank"
    assert report["retrieval_config"]["reranker"]["provider"] == "fake-reranker"
    assert report["retrieval_config"]["rerank_query_variant"] == "en"
    assert report["retrieval_config"]["rerank_pool_size"] == 50
    assert report["retrieval_config"]["final_top_k"] == 10
    assert report["hit_at_1"] == 1.0
    assert report["results"][0]["top_hits"][0]["parent_id"] == "gold__parent"
    assert report["results"][0]["top_hits"][0]["base_rank"] == 2
    assert report["results"][0]["top_hits"][1]["parent_id"] == "generic__parent"
    assert report["results"][0]["top_hits"][1]["base_rank"] == 1
    assert json.loads(capsys.readouterr().out)["retrieval_config"]["retrieval_method"] == "hybrid_rerank"


def test_cli_eval_hybrid_rerank_index_with_multi_query_rrf(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider
    from fireclaw_core.rag.dense_retrieval import build_dense_index

    records_path = tmp_path / "records.jsonl"
    _write_jsonl(
        records_path,
        [
            _record("en", "ventilation explanation rescue"),
            _record("terms", "LOAD3DSMOKE HRRPUV rescue"),
            _record("both", "ventilation LOAD3DSMOKE rescue"),
        ],
    )
    dense_index_dir = tmp_path / "dense_index"
    build_dense_index(records_path, dense_index_dir, FakeEmbeddingProvider(), batch_size=2)

    bm25_index_dir = tmp_path / "bm25_index"
    assert main(["build-bm25-index", "--records", str(records_path), "--index-dir", str(bm25_index_dir)]) == 0
    capsys.readouterr()

    parent_chunks_path = tmp_path / "parent_chunks.jsonl"
    _write_jsonl(
        parent_chunks_path,
        [
            {"parent_id": "en__parent", "text": "ventilation natural language explanation"},
            {"parent_id": "terms__parent", "text": "LOAD3DSMOKE HRRPUV smokeview command"},
            {"parent_id": "both__parent", "text": "ventilation LOAD3DSMOKE smokeview"},
        ],
    )
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [{"case_id": "case", "topic": "smokeview", "query": "rescue", "gold_parent_ids": ["both__parent"]}],
    )
    expansions_path = tmp_path / "expansions.jsonl"
    _write_jsonl(
        expansions_path,
        [
            {
                "case_id": "case",
                "query_zh": "ventilation LOAD3DSMOKE",
                "reviewed_query_en": "ventilation explanation",
                "term_query": "LOAD3DSMOKE HRRPUV",
                "terms": ["LOAD3DSMOKE", "HRRPUV"],
                "status": "reviewed",
            }
        ],
    )
    output_path = tmp_path / "multi_rerank_report.json"

    exit_code = main(
        [
            "eval-hybrid-rerank-index",
            "--provider",
            "fake",
            "--dense-index-dir",
            str(dense_index_dir),
            "--bm25-index-dir",
            str(bm25_index_dir),
            "--reranker-provider",
            "fake",
            "--parent-chunks",
            str(parent_chunks_path),
            "--cases",
            str(cases_path),
            "--query-expansions",
            str(expansions_path),
            "--dense-query-variants",
            "zh,en,terms",
            "--bm25-query-variants",
            "en,terms",
            "--rerank-query-variants",
            "zh,en,terms",
            "--require-reviewed-expansions",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["retrieval_config"]["retrieval_method"] == "hybrid_rerank"
    assert report["retrieval_config"]["rerank_query_variants"] == ["zh", "en", "terms"]
    assert report["retrieval_config"]["rerank_fusion"] == "rrf"
    assert report["results"][0]["top_hits"][0]["parent_id"] == "both__parent"
    assert report["results"][0]["top_hits"][0]["reranker"] == "fake-reranker:rrf"
    assert json.loads(capsys.readouterr().out)["retrieval_config"]["rerank_fusion"] == "rrf"


def test_cli_eval_hybrid_rerank_index_with_relevance_judgments_adds_ndcg(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider
    from fireclaw_core.rag.dense_retrieval import build_dense_index

    records_path = tmp_path / "records.jsonl"
    _write_jsonl(
        records_path,
        [
            _record("generic", "generic firefighter health rescue"),
            _record("gold", "SCBA rehabilitation medical evaluation NFPA 1584 rescue"),
        ],
    )
    dense_index_dir = tmp_path / "dense_index"
    build_dense_index(records_path, dense_index_dir, FakeEmbeddingProvider(), batch_size=2)

    bm25_index_dir = tmp_path / "bm25_index"
    assert main(["build-bm25-index", "--records", str(records_path), "--index-dir", str(bm25_index_dir)]) == 0
    capsys.readouterr()

    parent_chunks_path = tmp_path / "parent_chunks.jsonl"
    _write_jsonl(
        parent_chunks_path,
        [
            {"parent_id": "generic__parent", "text": "generic firefighter health information"},
            {"parent_id": "gold__parent", "text": "SCBA rehabilitation medical evaluation NFPA 1584"},
        ],
    )
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [{"case_id": "case", "topic": "rehab", "query": "generic rescue", "gold_parent_ids": ["gold__parent"]}],
    )
    expansions_path = tmp_path / "expansions.jsonl"
    _write_jsonl(
        expansions_path,
        [
            {
                "case_id": "case",
                "query_zh": "generic rescue",
                "reviewed_query_en": "When should firefighters enter SCBA rehabilitation?",
                "term_query": "generic firefighter health rescue",
                "terms": ["generic", "firefighter", "health", "rescue"],
                "status": "reviewed",
            }
        ],
    )
    judgments_path = tmp_path / "judgments.jsonl"
    _write_jsonl(
        judgments_path,
        [
            {
                "case_id": "case",
                "parent_id": "gold__parent",
                "grade": 3,
                "source": "agent-reviewed evaluation label; not human gold",
                "notes": "strict v2 gold parent",
            },
            {
                "case_id": "case",
                "parent_id": "generic__parent",
                "grade": 1,
                "source": "agent-reviewed evaluation label; not human gold",
                "notes": "background only",
            },
        ],
    )
    output_path = tmp_path / "hybrid_rerank_graded_report.json"

    exit_code = main(
        [
            "eval-hybrid-rerank-index",
            "--provider",
            "fake",
            "--dense-index-dir",
            str(dense_index_dir),
            "--bm25-index-dir",
            str(bm25_index_dir),
            "--reranker-provider",
            "fake",
            "--parent-chunks",
            str(parent_chunks_path),
            "--cases",
            str(cases_path),
            "--query-expansions",
            str(expansions_path),
            "--dense-query-variants",
            "zh,en",
            "--bm25-query-variants",
            "terms",
            "--relevance-judgments",
            str(judgments_path),
            "--relevance-threshold",
            "2",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ndcg_at_10"] == 1.0
    assert report["results"][0]["ndcg_at_10"] == 1.0
    assert report["retrieval_config"]["relevance_judgments_path"] == str(judgments_path)
    assert report["retrieval_config"]["relevance_threshold"] == 2
    assert json.loads(capsys.readouterr().out)["ndcg_at_10"] == 1.0


def test_cli_eval_hybrid_rerank_index_with_small_rerank_level(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider
    from fireclaw_core.rag.dense_retrieval import build_dense_index

    records_path = tmp_path / "records.jsonl"
    _write_jsonl(
        records_path,
        [
            _record("generic", "thermal victim evidence preview"),
            _record("gold_evidence", "generic preview"),
            _record("gold_context", "background context"),
        ],
    )
    dense_index_dir = tmp_path / "dense_index"
    build_dense_index(records_path, dense_index_dir, FakeEmbeddingProvider(), batch_size=2)

    bm25_index_dir = tmp_path / "bm25_index"
    assert main(["build-bm25-index", "--records", str(records_path), "--index-dir", str(bm25_index_dir)]) == 0
    capsys.readouterr()

    parent_chunks_path = tmp_path / "parent_chunks.jsonl"
    _write_jsonl(
        parent_chunks_path,
        [
            {"parent_id": "generic__parent", "text": "thermal victim evidence in parent text"},
            {"parent_id": "gold_evidence__parent", "text": "generic parent text"},
            {"parent_id": "gold_context__parent", "text": "background parent text"},
        ],
    )
    small_chunks_path = tmp_path / "small_chunks.jsonl"
    _write_jsonl(
        small_chunks_path,
        [
            {"chunk_id": "generic", "parent_id": "generic__parent", "clean_text": "generic operations"},
            {"chunk_id": "gold_evidence", "parent_id": "gold_evidence__parent", "clean_text": "thermal victim evidence"},
            {"chunk_id": "gold_context", "parent_id": "gold_context__parent", "clean_text": "background context"},
        ],
    )
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [{"case_id": "case", "topic": "thermal", "query": "thermal victim evidence", "gold_parent_ids": ["gold_evidence__parent"]}],
    )
    expansions_path = tmp_path / "expansions.jsonl"
    _write_jsonl(
        expansions_path,
        [
            {
                "case_id": "case",
                "query_zh": "thermal victim evidence",
                "reviewed_query_en": "thermal victim evidence",
                "term_query": "thermal victim evidence",
                "terms": ["thermal", "victim", "evidence"],
                "status": "reviewed",
            }
        ],
    )
    output_path = tmp_path / "small_rerank_report.json"

    exit_code = main(
        [
            "eval-hybrid-rerank-index",
            "--provider",
            "fake",
            "--dense-index-dir",
            str(dense_index_dir),
            "--bm25-index-dir",
            str(bm25_index_dir),
            "--reranker-provider",
            "fake",
            "--parent-chunks",
            str(parent_chunks_path),
            "--cases",
            str(cases_path),
            "--query-expansions",
            str(expansions_path),
            "--dense-query-variants",
            "zh",
            "--bm25-query-variants",
            "en",
            "--rerank-level",
            "small",
            "--small-chunks",
            str(small_chunks_path),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["retrieval_config"]["rerank_level"] == "small"
    assert report["retrieval_config"]["small_chunks_path"] == str(small_chunks_path)
    assert report["results"][0]["top_hits"][0]["parent_id"] == "gold_evidence__parent"
    assert report["results"][0]["top_hits"][0]["chunk_id"] == "gold_evidence"
    assert json.loads(capsys.readouterr().out)["retrieval_config"]["rerank_level"] == "small"


def test_cli_eval_hybrid_rerank_index_with_joint_fusion_rrf(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider
    from fireclaw_core.rag.dense_retrieval import build_dense_index

    records_path = tmp_path / "records.jsonl"
    _write_jsonl(
        records_path,
        [
            _record("hybrid", "alpha rescue"),
            _record("middle", "alpha beta rescue"),
            _record("rerank", "alpha beta gamma rescue"),
        ],
    )
    dense_index_dir = tmp_path / "dense_index"
    build_dense_index(records_path, dense_index_dir, FakeEmbeddingProvider(), batch_size=2)

    bm25_index_dir = tmp_path / "bm25_index"
    assert main(["build-bm25-index", "--records", str(records_path), "--index-dir", str(bm25_index_dir)]) == 0
    capsys.readouterr()

    parent_chunks_path = tmp_path / "parent_chunks.jsonl"
    _write_jsonl(
        parent_chunks_path,
        [
            {"parent_id": "hybrid__parent", "text": "alpha"},
            {"parent_id": "middle__parent", "text": "alpha beta"},
            {"parent_id": "rerank__parent", "text": "alpha beta gamma"},
        ],
    )
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [{"case_id": "case", "topic": "joint", "query": "alpha rescue", "gold_parent_ids": ["hybrid__parent"]}],
    )
    expansions_path = tmp_path / "expansions.jsonl"
    _write_jsonl(
        expansions_path,
        [
            {
                "case_id": "case",
                "query_zh": "alpha rescue",
                "reviewed_query_en": "alpha beta gamma",
                "term_query": "alpha rescue",
                "terms": ["alpha", "rescue"],
                "status": "reviewed",
            }
        ],
    )
    output_path = tmp_path / "joint_rerank_report.json"

    exit_code = main(
        [
            "eval-hybrid-rerank-index",
            "--provider",
            "fake",
            "--dense-index-dir",
            str(dense_index_dir),
            "--bm25-index-dir",
            str(bm25_index_dir),
            "--reranker-provider",
            "fake",
            "--parent-chunks",
            str(parent_chunks_path),
            "--cases",
            str(cases_path),
            "--query-expansions",
            str(expansions_path),
            "--dense-query-variants",
            "zh",
            "--bm25-query-variants",
            "terms",
            "--joint-fusion",
            "rrf",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["retrieval_config"]["joint_fusion"] == "rrf"
    assert report["retrieval_config"]["joint_rrf_k"] == 60
    assert "hybrid" in report["results"][0]["top_hits"][0]["variant_ranks"]
    assert "rerank" in report["results"][0]["top_hits"][0]["variant_ranks"]
    assert json.loads(capsys.readouterr().out)["retrieval_config"]["joint_fusion"] == "rrf"
