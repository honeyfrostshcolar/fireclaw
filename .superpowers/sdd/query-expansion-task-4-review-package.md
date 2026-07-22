# Review package: Query Expansion Task 4

## Scoped status
```text
 M src/fireclaw_core/rag/rag_cli.py
 M tests/test_rag_dense_cli.py
!! data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl
!! data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl
```

## Ignore check
```text
.gitignore:20:*.jsonl	data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl
.gitignore:20:*.jsonl	data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl
```

## File: src\fireclaw_core\rag\rag_cli.py
```python
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any
from typing import TextIO

from fireclaw_core.rag.chunking import ChunkingConfig
from fireclaw_core.rag.corpus_chunking import chunk_corpus_pages
from fireclaw_core.rag.corpus_extraction import extract_corpus_pages
from fireclaw_core.rag.extraction import PdfTextExtractionError
from fireclaw_core.rag.dense_eval import evaluate_dense_retriever
from fireclaw_core.rag.dense_eval import evaluate_dense_retriever_with_expansion
from fireclaw_core.rag.dense_eval import load_dense_eval_cases
from fireclaw_core.rag.dense_retrieval import DenseRetriever
from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider
from fireclaw_core.rag.dense_retrieval import build_dense_index
from fireclaw_core.rag.dense_retrieval import expand_hits_to_parents
from fireclaw_core.rag.index_preparation import IndexPreparationConfig
from fireclaw_core.rag.index_preparation import prepare_index_records
from fireclaw_core.rag.query_expansion import load_query_expansions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FireClaw RAG corpus utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract-pages", help="Extract raw PDFs into page-level JSONL.")
    extract.add_argument("--corpus-root", default="data/rag/fire_rescue")
    extract.add_argument("--raw-dir", default=None, help="PDF directory. Defaults to <corpus-root>/raw.")
    extract.add_argument("--output-dir", default=None, help="Output directory. Defaults to <corpus-root>/extracted.")
    extract.add_argument(
        "--manifest",
        action="append",
        default=[],
        help="Manifest JSONL path. Can be passed multiple times.",
    )
    extract.add_argument("--limit", type=int, default=None, help="Only extract the first N PDFs.")

    chunk = subparsers.add_parser("chunk-pages", help="Chunk page-level JSONL into parent and small chunks.")
    chunk.add_argument("--corpus-root", default="data/rag/fire_rescue")
    chunk.add_argument("--pages", default=None, help="Page JSONL path. Defaults to <corpus-root>/extracted/pages.jsonl.")
    chunk.add_argument("--output-dir", default=None, help="Output directory. Defaults to <corpus-root>/chunks.")
    chunk.add_argument("--limit-docs", type=int, default=None, help="Only chunk the first N documents.")
    chunk.add_argument("--parent-target-chars", type=int, default=7000)
    chunk.add_argument("--parent-max-chars", type=int, default=9000)
    chunk.add_argument("--parent-min-chars", type=int, default=800)
    chunk.add_argument("--parent-max-pages", type=int, default=2)
    chunk.add_argument("--small-target-chars", type=int, default=1800)
    chunk.add_argument("--small-max-chars", type=int, default=2600)
    chunk.add_argument("--small-min-chars", type=int, default=300)
    chunk.add_argument("--small-overlap-chars", type=int, default=300)

    prepare = subparsers.add_parser("prepare-index", help="Clean small chunks into embedding-ready index records.")
    prepare.add_argument("--corpus-root", default="data/rag/fire_rescue")
    prepare.add_argument("--small-chunks", default=None, help="Small chunk JSONL path. Defaults to <corpus-root>/chunks/small_chunks.jsonl.")
    prepare.add_argument("--output-dir", default=None, help="Output directory. Defaults to <corpus-root>/index_inputs.")
    prepare.add_argument("--min-clean-chars", type=int, default=300)
    prepare.add_argument("--min-clean-words", type=int, default=20)
    prepare.add_argument("--header-footer-max-chars", type=int, default=220)

    dense_build = subparsers.add_parser("build-dense-index", help="Build a dense vector index from prepared records.")
    dense_build.add_argument("--corpus-root", default="data/rag/fire_rescue")
    dense_build.add_argument("--records", default=None)
    dense_build.add_argument("--index-dir", default=None)
    dense_build.add_argument("--provider", choices=["fake", "bge-m3"], default="fake")
    dense_build.add_argument("--model-path", default=None)
    dense_build.add_argument("--batch-size", type=int, default=32)

    dense_query = subparsers.add_parser("query-dense-index", help="Query a dense vector index.")
    dense_query.add_argument("--corpus-root", default="data/rag/fire_rescue")
    dense_query.add_argument("--index-dir", default=None)
    dense_query.add_argument("--provider", choices=["fake", "bge-m3"], default="fake")
    dense_query.add_argument("--model-path", default=None)
    dense_query.add_argument("--query", required=True)
    dense_query.add_argument("--top-k", type=int, default=5)
    dense_query.add_argument("--parents", action="store_true")
    dense_query.add_argument("--parent-chunks", default=None)

    dense_eval = subparsers.add_parser("eval-dense-index", help="Evaluate dense retrieval against gold cases.")
    dense_eval.add_argument("--corpus-root", default="data/rag/fire_rescue")
    dense_eval.add_argument("--index-dir", default=None)
    dense_eval.add_argument("--provider", choices=["fake", "bge-m3"], default="fake")
    dense_eval.add_argument("--model-path", default=None)
    dense_eval.add_argument("--cases", default=None)
    dense_eval.add_argument("--top-k", type=int, default=10)
    dense_eval.add_argument("--output", default=None)
    dense_eval.add_argument("--query-expansions", default=None)
    dense_eval.add_argument("--query-variants", default="zh")
    dense_eval.add_argument("--ranking-view", choices=["small", "parent"], default="small")
    dense_eval.add_argument("--small-top-k", type=int, default=None)
    dense_eval.add_argument("--parent-aggregation", choices=["max"], default="max")
    dense_eval.add_argument("--fusion", choices=["none", "rrf"], default=None)
    dense_eval.add_argument("--rrf-k", type=int, default=60)
    dense_eval.add_argument("--require-reviewed-expansions", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "extract-pages":
        return _cmd_extract_pages(args)
    if args.command == "chunk-pages":
        return _cmd_chunk_pages(args)
    if args.command == "prepare-index":
        return _cmd_prepare_index(args)
    if args.command == "build-dense-index":
        return _cmd_build_dense_index(args)
    if args.command == "query-dense-index":
        return _cmd_query_dense_index(args)
    if args.command == "eval-dense-index":
        return _cmd_eval_dense_index(args)
    return 1


def _cmd_extract_pages(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    raw_dir = Path(args.raw_dir) if args.raw_dir else corpus_root / "raw"
    output_dir = Path(args.output_dir) if args.output_dir else corpus_root / "extracted"
    manifest_paths = [Path(path) for path in args.manifest]
    if not manifest_paths:
        manifest_dir = corpus_root / "manifests"
        manifest_paths = sorted(manifest_dir.glob("*.jsonl"))

    if not raw_dir.exists():
        print(f"Error: raw PDF directory not found: {raw_dir}", file=sys.stderr)
        return 1

    try:
        report = extract_corpus_pages(
            corpus_root=corpus_root,
            raw_dir=raw_dir,
            output_dir=output_dir,
            manifest_paths=manifest_paths,
            limit=args.limit,
        )
    except PdfTextExtractionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    _write_json_output(report.to_dict())
    return 0 if report.failed == 0 else 2


def _cmd_chunk_pages(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    pages_path = Path(args.pages) if args.pages else corpus_root / "extracted" / "pages.jsonl"
    output_dir = Path(args.output_dir) if args.output_dir else corpus_root / "chunks"

    if not pages_path.exists():
        print(f"Error: page JSONL not found: {pages_path}", file=sys.stderr)
        return 1

    config = ChunkingConfig(
        parent_target_chars=args.parent_target_chars,
        parent_max_chars=args.parent_max_chars,
        parent_min_chars=args.parent_min_chars,
        parent_max_pages=args.parent_max_pages,
        small_target_chars=args.small_target_chars,
        small_max_chars=args.small_max_chars,
        small_min_chars=args.small_min_chars,
        small_overlap_chars=args.small_overlap_chars,
    )
    report = chunk_corpus_pages(
        pages_path=pages_path,
        output_dir=output_dir,
        config=config,
        limit_docs=args.limit_docs,
    )
    _write_json_output(report.to_dict())
    return 0 if report.failed == 0 else 2


def _cmd_prepare_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    small_chunks_path = Path(args.small_chunks) if args.small_chunks else corpus_root / "chunks" / "small_chunks.jsonl"
    output_dir = Path(args.output_dir) if args.output_dir else corpus_root / "index_inputs"

    if not small_chunks_path.exists():
        print(f"Error: small chunk JSONL not found: {small_chunks_path}", file=sys.stderr)
        return 1

    config = IndexPreparationConfig(
        min_clean_chars=args.min_clean_chars,
        min_clean_words=args.min_clean_words,
        header_footer_max_chars=args.header_footer_max_chars,
    )
    report = prepare_index_records(
        small_chunks_path=small_chunks_path,
        output_dir=output_dir,
        config=config,
    )
    _write_json_output(report.to_dict())
    return 0


def _cmd_build_dense_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    records_path = Path(args.records) if args.records else corpus_root / "index_inputs" / "small_index_records.jsonl"
    index_dir = Path(args.index_dir) if args.index_dir else corpus_root / "indexes" / "dense" / _provider_index_name(args.provider)
    provider = _create_embedding_provider(args.provider, model_path=args.model_path)
    report = build_dense_index(records_path, index_dir, provider, batch_size=args.batch_size)
    _write_json_output(report.to_dict())
    return 0


def _cmd_query_dense_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    index_dir = Path(args.index_dir) if args.index_dir else corpus_root / "indexes" / "dense" / _provider_index_name(args.provider)
    provider = _create_embedding_provider(args.provider, model_path=args.model_path)
    retriever = DenseRetriever.load(index_dir, provider)
    hits = retriever.query(args.query, top_k=args.top_k)
    if args.parents:
        parent_chunks_path = Path(args.parent_chunks) if args.parent_chunks else corpus_root / "chunks" / "parent_chunks.jsonl"
        hits = expand_hits_to_parents(hits, parent_chunks_path)
    _write_json_output({"query": args.query, "hits": [hit.to_dict() for hit in hits]})
    return 0


def _cmd_eval_dense_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    index_dir = Path(args.index_dir) if args.index_dir else corpus_root / "indexes" / "dense" / _provider_index_name(args.provider)
    cases_path = Path(args.cases) if args.cases else corpus_root / "eval" / "dense_gold_cases_zh_v1.jsonl"
    output_path = Path(args.output) if args.output else None
    provider = _create_embedding_provider(args.provider, model_path=args.model_path)
    retriever = DenseRetriever.load(index_dir, provider)
    cases = load_dense_eval_cases(cases_path)
    query_variants = _parse_csv_arg(args.query_variants)
    use_expanded_eval = (
        query_variants != ["zh"]
        or args.ranking_view != "small"
        or args.small_top_k is not None
        or args.query_expansions is not None
        or args.fusion is not None
        or args.require_reviewed_expansions
    )
    if use_expanded_eval:
        expansions_path = Path(args.query_expansions) if args.query_expansions else None
        expansions = load_query_expansions(expansions_path) if expansions_path is not None else {}
        report = evaluate_dense_retriever_with_expansion(
            retriever,
            cases,
            query_expansions=expansions,
            query_variants=query_variants,
            ranking_view=args.ranking_view,
            top_k=args.top_k,
            small_top_k=args.small_top_k,
            parent_aggregation=args.parent_aggregation,
            fusion=args.fusion,
            rrf_k=args.rrf_k,
            require_reviewed_expansions=args.require_reviewed_expansions,
            query_expansions_path=str(expansions_path) if expansions_path is not None else None,
        )
    else:
        report = evaluate_dense_retriever(retriever, cases, top_k=args.top_k)
    payload = report.to_dict()
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_json_output(payload)
    return 0


def _create_embedding_provider(provider_name: str, *, model_path: str | None = None):
    if provider_name == "fake":
        return FakeEmbeddingProvider()
    if provider_name == "bge-m3":
        from fireclaw_core.rag.bge_m3_provider import BGEM3EmbeddingProvider

        return BGEM3EmbeddingProvider(model_path=Path(model_path) if model_path else Path(".cache/models/bge-m3"))
    raise ValueError(f"Unsupported dense embedding provider: {provider_name}")


def _provider_index_name(provider_name: str) -> str:
    if provider_name == "bge-m3":
        return "bge-m3"
    return provider_name


def _parse_csv_arg(value: str) -> list[str]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items:
        raise ValueError("CSV argument must contain at least one item")
    return items


def _write_json_output(payload: Any, *, stream: TextIO | None = None) -> None:
    stream = stream or sys.stdout
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        print(text, file=stream)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe_text, file=stream)


if __name__ == "__main__":
    raise SystemExit(main())
```

## File: tests\test_rag_dense_cli.py
```python
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
```

## File: data\rag\fire_rescue\eval\query_expansions_zh_v1.jsonl
```jsonl
{"case_id":"dense_zh_001","query_zh":"在浓烟遮挡、光照差的室内火灾里，为什么应优先用热成像而不是普通RGB相机搜人？","llm_query_en":"In an indoor fire with dense smoke and poor lighting, why should thermal imaging be preferred over a regular RGB camera for victim search?","reviewed_query_en":"","term_query":"thermal imaging infrared camera victim detection fireground smoke low visibility RGB camera target detection firefighter search and rescue","terms":["thermal imaging","infrared camera","victim detection","fireground smoke","low visibility","RGB camera","target detection","firefighter search and rescue"],"status":"candidate","notes":"Candidate translation and terms for thermal victim search; user review required."}
{"case_id":"dense_zh_002","query_zh":"已知厂房平面图时，机器人如何规划远程气体扫描，尽快找出可燃气体泄漏源？","llm_query_en":"Given a known factory floor plan, how should a robot plan remote gas scanning to find combustible gas leak sources as quickly as possible?","reviewed_query_en":"","term_query":"remote gas detection methane leak TDLAS Remote Methane Leak Detector Next-Best-Smell coverage planning candidate locations information gain sensing time occupancy grid","terms":["remote gas detection","methane leak","TDLAS","Remote Methane Leak Detector","Next-Best-Smell","coverage planning","candidate locations","information gain","sensing time","occupancy grid"],"status":"candidate","notes":"Candidate translation and terms from the gas source detection miss-case analysis; user review required."}
{"case_id":"dense_zh_003","query_zh":"无人机在浓烟遮挡下怎样利用热成像发现被烟挡住的明火位置？","llm_query_en":"How can a UAV use thermal imaging to detect open flame locations that are obscured by dense smoke?","reviewed_query_en":"","term_query":"UAV thermal imaging flame detection smoke-obscured fire infrared thermal fire detection FlameFinder deep metric learning","terms":["UAV","thermal imaging","flame detection","smoke-obscured fire","infrared","thermal fire detection","FlameFinder","deep metric learning"],"status":"candidate","notes":"Candidate translation and terms for smoke-obscured flame detection; user review required."}
{"case_id":"dense_zh_004","query_zh":"建筑坍塌后，什么类型的机器人更适合进入狭小空洞搜索幸存者？","llm_query_en":"After a building collapse, what type of robot is better suited to enter narrow void spaces to search for survivors?","reviewed_query_en":"","term_query":"USAR void space search vine robot soft robot continuum robot confined spaces collapsed structure survivor search SPROUT","terms":["USAR","void space search","vine robot","soft robot","continuum robot","confined spaces","collapsed structure","survivor search","SPROUT"],"status":"candidate","notes":"Candidate translation and terms for void-space robot search; user review required."}
{"case_id":"dense_zh_005","query_zh":"坍塌废墟中的空洞入口危险度怎么分级，哪些情况对人类很难但对软体机器人更可行？","llm_query_en":"How should void entrance risk in collapsed rubble be classified, and which conditions are difficult for humans but more feasible for soft robots?","reviewed_query_en":"","term_query":"void entrance risk collapsed rubble soft robot vine robot constrained aperture unstable debris low clearance human entry risk USAR","terms":["void entrance risk","collapsed rubble","soft robot","vine robot","constrained aperture","unstable debris","low clearance","human entry risk","USAR"],"status":"candidate","notes":"Candidate translation and terms for USAR void entry risk; user review required."}
{"case_id":"dense_zh_006","query_zh":"消防救援机器人在采购前应该从哪些标准化能力维度做测试？","llm_query_en":"Before purchasing a firefighting or rescue robot, which standardized capability dimensions should be tested?","reviewed_query_en":"","term_query":"response robots standard test methods DHS-NIST-ASTM robot purchases representative test methods performance objectives lower capability thresholds mission capabilities operator proficiency","terms":["response robots","standard test methods","DHS-NIST-ASTM","robot purchases","representative test methods","performance objectives","lower capability thresholds","mission capabilities","operator proficiency"],"status":"candidate","notes":"Candidate translation and terms from the response robot test methods miss-case analysis; user review required."}
{"case_id":"dense_zh_007","query_zh":"大型建筑做消防预案时，消防队最关心哪些建筑消防系统和现场标识信息？","llm_query_en":"When preparing a fire preplan for a large building, which building fire protection systems and on-site signage information matter most to the fire service?","reviewed_query_en":"","term_query":"fire service features pre-incident planning building fire protection systems fire alarm sprinkler standpipe fire command center signage evacuation floor plans access points","terms":["fire service features","pre-incident planning","building fire protection systems","fire alarm","sprinkler","standpipe","fire command center","signage","evacuation","floor plans","access points"],"status":"candidate","notes":"Candidate translation and terms for building fire service features; user review required."}
{"case_id":"dense_zh_008","query_zh":"消防员连续作业多久、用完几瓶空呼后，必须进入rehab做补水和医疗评估？","llm_query_en":"When must firefighters enter rehab for hydration and medical evaluation after continuous work or after using SCBA cylinders?","reviewed_query_en":"","term_query":"SCBA self-contained breathing apparatus SCBA cylinder NFPA 1584 emergency incident rehabilitation self-rehab formal rehab medical evaluation hydration work-to-rest ratio vital signs","terms":["SCBA","self-contained breathing apparatus","SCBA cylinder","NFPA 1584","emergency incident rehabilitation","self-rehab","formal rehab","medical evaluation","hydration","work-to-rest ratio","vital signs"],"status":"candidate","notes":"Candidate translation and terms from the firefighter rehab threshold miss-case analysis; user review required."}
{"case_id":"dense_zh_009","query_zh":"在疑似有毒泄漏或危险品污染的坍塌现场，USAR队进入前必须检查哪些风险？","llm_query_en":"At a collapsed structure site suspected of toxic leakage or hazardous materials contamination, what risks must a USAR team check before entry?","reviewed_query_en":"","term_query":"USAR hazmat hazardous materials contaminated site contaminated environment PPE personal protective equipment go/no-go conditions risk-benefit analysis detection and monitoring decontamination clean entry points","terms":["USAR","hazmat","hazardous materials","contaminated site","contaminated environment","PPE","personal protective equipment","go/no-go conditions","risk-benefit analysis","detection and monitoring","decontamination","clean entry points"],"status":"candidate","notes":"Candidate translation and terms from the USAR hazmat entry safety miss-case analysis; user review required."}
{"case_id":"dense_zh_010","query_zh":"机器人要穿过有多个火源的区域时，怎样根据热辐射代价图规划一条更安全的路线？","llm_query_en":"When a robot must traverse an area with multiple fire sources, how can it plan a safer path using a thermal radiation cost map?","reviewed_query_en":"","term_query":"thermal radiation cost map fire-aware navigation thermal occupancy grid heat flux path planning multiple fire sources safe robot navigation thermally aware path planning","terms":["thermal radiation cost map","fire-aware navigation","thermal occupancy grid","heat flux","path planning","multiple fire sources","safe robot navigation","thermally aware path planning"],"status":"candidate","notes":"Candidate translation and terms for thermal radiation path planning; user review required."}
```

## File: data\rag\fire_rescue\eval\glossary_candidates_zh_v1.jsonl
```jsonl
{"entry_id":"remote_gas_detection_core","category":"remote_gas_detection","zh_aliases":["远程气体检测","气体扫描","可燃气体泄漏"],"en_terms":["remote gas detection","methane leak","TDLAS","Remote Methane Leak Detector","Next-Best-Smell","coverage planning","candidate locations","information gain","sensing time"],"source_parent_ids":["arxiv_1801_06819_next_best_smell_mobile_robot_gas_detection__parent_00001","arxiv_1801_06819_next_best_smell_mobile_robot_gas_detection__parent_00002"],"status":"candidate","notes":"Candidate terms extracted from the remote gas source detection miss-case analysis."}
{"entry_id":"response_robot_testing_core","category":"response_robot_test_methods","zh_aliases":["消防救援机器人测试","采购前测试","标准化能力测试"],"en_terms":["response robots","standard test methods","DHS-NIST-ASTM","robot purchases","representative test methods","performance objectives","lower capability thresholds","mission capabilities","operator proficiency"],"source_parent_ids":["nist_response_robot_test_methods_guide__parent_00003","nist_response_robot_test_methods_guide__parent_00016"],"status":"candidate","notes":"Candidate terms extracted from NIST response robot test method hits."}
{"entry_id":"firefighter_rehab_scba","category":"firefighter_rehab","zh_aliases":["空呼","空气呼吸器","消防员康复","补水和医疗评估"],"en_terms":["SCBA","self-contained breathing apparatus","SCBA cylinder","NFPA 1584","emergency incident rehabilitation","self-rehab","formal rehab","medical evaluation","hydration","work-to-rest ratio","vital signs"],"source_parent_ids":["usfa_emergency_incident_rehabilitation_fa_314__parent_00068","usfa_emergency_incident_rehabilitation_fa_314__parent_00102"],"status":"candidate","notes":"Candidate terms extracted from firefighter rehab threshold analysis."}
{"entry_id":"usar_hazmat_entry","category":"usar_hazmat_entry_safety","zh_aliases":["危险品","有毒泄漏","污染现场","进入前风险检查"],"en_terms":["USAR","hazmat","hazardous materials","contaminated site","contaminated environment","PPE","personal protective equipment","go/no-go conditions","risk-benefit analysis","detection and monitoring","decontamination","clean entry points"],"source_parent_ids":["insarag_guidelines_2020_volume_iii_operational_field_guide__parent_00021","insarag_guidelines_2020_volume_iii_operational_field_guide__parent_00022"],"status":"candidate","notes":"Candidate terms extracted from INSARAG hazmat entry safety analysis."}
{"entry_id":"thermal_victim_search","category":"thermal_victim_search","zh_aliases":["热成像搜人","浓烟搜救","低能见度搜救"],"en_terms":["thermal imaging","infrared camera","victim detection","fireground smoke","low visibility","RGB camera","target detection","firefighter search and rescue"],"source_parent_ids":["arxiv_1910_03617_thermal_image_target_detection_firefighting__parent_00002"],"status":"candidate","notes":"Candidate terms for thermal victim search."}
{"entry_id":"thermal_radiation_navigation","category":"thermal_radiation_path_planning","zh_aliases":["热辐射代价图","热安全路径规划","多火源路径规划"],"en_terms":["thermal radiation cost map","fire-aware navigation","thermal occupancy grid","heat flux","path planning","multiple fire sources","safe robot navigation","thermally aware path planning"],"source_parent_ids":["arxiv_2603_19063_fire_as_a_service_robot_simulators_fire_dynamics__parent_00007"],"status":"candidate","notes":"Candidate terms for thermal radiation path planning."}
```
