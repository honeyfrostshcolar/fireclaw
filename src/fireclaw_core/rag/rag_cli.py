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
from fireclaw_core.rag.bm25_retrieval import BM25Retriever
from fireclaw_core.rag.bm25_retrieval import build_bm25_index
from fireclaw_core.rag.dense_eval import evaluate_bm25_retriever_with_expansion
from fireclaw_core.rag.dense_eval import evaluate_dense_retriever
from fireclaw_core.rag.dense_eval import evaluate_dense_retriever_with_expansion
from fireclaw_core.rag.dense_eval import load_dense_eval_cases
from fireclaw_core.rag.dense_retrieval import DenseRetriever
from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider
from fireclaw_core.rag.dense_retrieval import build_dense_index
from fireclaw_core.rag.dense_retrieval import expand_hits_to_parents
from fireclaw_core.rag.hybrid_eval import evaluate_hybrid_retrievers_with_expansion
from fireclaw_core.rag.index_preparation import IndexPreparationConfig
from fireclaw_core.rag.index_preparation import prepare_index_records
from fireclaw_core.rag.query_expansion import load_query_expansions
from fireclaw_core.rag.relevance_eval import load_relevance_judgments
from fireclaw_core.rag.rerank_eval import evaluate_hybrid_retrievers_with_rerank
from fireclaw_core.rag.reranking import BGEFlagRerankerProvider
from fireclaw_core.rag.reranking import FakeRerankerProvider
from fireclaw_core.rag.reranking import load_parent_texts
from fireclaw_core.rag.reranking import load_small_texts


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

    bm25_build = subparsers.add_parser("build-bm25-index", help="Build a BM25 lexical index from prepared records.")
    bm25_build.add_argument("--corpus-root", default="data/rag/fire_rescue")
    bm25_build.add_argument("--records", default=None)
    bm25_build.add_argument("--index-dir", default=None)
    bm25_build.add_argument("--k1", type=float, default=1.5)
    bm25_build.add_argument("--b", type=float, default=0.75)

    bm25_query = subparsers.add_parser("query-bm25-index", help="Query a BM25 lexical index.")
    bm25_query.add_argument("--corpus-root", default="data/rag/fire_rescue")
    bm25_query.add_argument("--index-dir", default=None)
    bm25_query.add_argument("--query", required=True)
    bm25_query.add_argument("--top-k", type=int, default=5)

    bm25_eval = subparsers.add_parser("eval-bm25-index", help="Evaluate BM25 retrieval against gold cases.")
    bm25_eval.add_argument("--corpus-root", default="data/rag/fire_rescue")
    bm25_eval.add_argument("--index-dir", default=None)
    bm25_eval.add_argument("--cases", default=None)
    bm25_eval.add_argument("--top-k", type=int, default=10)
    bm25_eval.add_argument("--small-top-k", type=int, default=50)
    bm25_eval.add_argument("--output", default=None)
    bm25_eval.add_argument("--query-expansions", required=True)
    bm25_eval.add_argument("--query-variants", default="en,terms")
    bm25_eval.add_argument("--ranking-view", choices=["small", "parent"], default="parent")
    bm25_eval.add_argument("--parent-aggregation", choices=["max"], default="max")
    bm25_eval.add_argument("--fusion", choices=["none", "rrf"], default=None)
    bm25_eval.add_argument("--rrf-k", type=int, default=60)
    bm25_eval.add_argument("--require-reviewed-expansions", action="store_true")

    hybrid_eval = subparsers.add_parser("eval-hybrid-index", help="Evaluate hybrid dense+BM25 retrieval against gold cases.")
    hybrid_eval.add_argument("--corpus-root", default="data/rag/fire_rescue")
    hybrid_eval.add_argument("--dense-index-dir", default=None)
    hybrid_eval.add_argument("--bm25-index-dir", default=None)
    hybrid_eval.add_argument("--provider", choices=["fake", "bge-m3"], default="fake")
    hybrid_eval.add_argument("--model-path", default=None)
    hybrid_eval.add_argument("--cases", default=None)
    hybrid_eval.add_argument("--query-expansions", required=True)
    hybrid_eval.add_argument("--relevance-judgments", default=None)
    hybrid_eval.add_argument("--relevance-threshold", type=int, default=2)
    hybrid_eval.add_argument("--dense-query-variants", default="zh,en,terms")
    hybrid_eval.add_argument("--bm25-query-variants", default="en,terms")
    hybrid_eval.add_argument("--small-top-k", type=int, default=50)
    hybrid_eval.add_argument("--top-k", type=int, default=10)
    hybrid_eval.add_argument("--rrf-k", type=int, default=60)
    hybrid_eval.add_argument("--output", default=None)
    hybrid_eval.add_argument("--require-reviewed-expansions", action="store_true")

    hybrid_rerank_eval = subparsers.add_parser(
        "eval-hybrid-rerank-index",
        help="Evaluate hybrid dense+BM25 retrieval with parent-level reranking.",
    )
    hybrid_rerank_eval.add_argument("--corpus-root", default="data/rag/fire_rescue")
    hybrid_rerank_eval.add_argument("--dense-index-dir", default=None)
    hybrid_rerank_eval.add_argument("--bm25-index-dir", default=None)
    hybrid_rerank_eval.add_argument("--provider", choices=["fake", "bge-m3"], default="fake")
    hybrid_rerank_eval.add_argument("--model-path", default=None)
    hybrid_rerank_eval.add_argument("--reranker-provider", choices=["fake", "bge-reranker"], default="fake")
    hybrid_rerank_eval.add_argument("--reranker-model-path", default=None)
    hybrid_rerank_eval.add_argument("--reranker-device", default=None)
    hybrid_rerank_eval.add_argument("--reranker-batch-size", type=int, default=32)
    hybrid_rerank_eval.add_argument("--reranker-max-length", type=int, default=512)
    hybrid_rerank_eval.add_argument("--parent-chunks", default=None)
    hybrid_rerank_eval.add_argument("--small-chunks", default=None)
    hybrid_rerank_eval.add_argument("--cases", default=None)
    hybrid_rerank_eval.add_argument("--query-expansions", required=True)
    hybrid_rerank_eval.add_argument("--relevance-judgments", default=None)
    hybrid_rerank_eval.add_argument("--relevance-threshold", type=int, default=2)
    hybrid_rerank_eval.add_argument("--dense-query-variants", default="zh,en,terms")
    hybrid_rerank_eval.add_argument("--bm25-query-variants", default="en,terms")
    hybrid_rerank_eval.add_argument("--rerank-query-variant", choices=["zh", "en", "terms"], default="en")
    hybrid_rerank_eval.add_argument("--rerank-query-variants", default=None)
    hybrid_rerank_eval.add_argument("--rerank-level", choices=["parent", "small"], default="parent")
    hybrid_rerank_eval.add_argument("--joint-fusion", choices=["none", "rrf"], default="none")
    hybrid_rerank_eval.add_argument("--small-top-k", type=int, default=50)
    hybrid_rerank_eval.add_argument("--rerank-pool-size", type=int, default=50)
    hybrid_rerank_eval.add_argument("--top-k", type=int, default=10)
    hybrid_rerank_eval.add_argument("--rrf-k", type=int, default=60)
    hybrid_rerank_eval.add_argument("--max-passage-chars", type=int, default=6000)
    hybrid_rerank_eval.add_argument("--output", default=None)
    hybrid_rerank_eval.add_argument("--require-reviewed-expansions", action="store_true")

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
    if args.command == "build-bm25-index":
        return _cmd_build_bm25_index(args)
    if args.command == "query-bm25-index":
        return _cmd_query_bm25_index(args)
    if args.command == "eval-bm25-index":
        return _cmd_eval_bm25_index(args)
    if args.command == "eval-hybrid-index":
        return _cmd_eval_hybrid_index(args)
    if args.command == "eval-hybrid-rerank-index":
        return _cmd_eval_hybrid_rerank_index(args)
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


def _bm25_index_dir(corpus_root: Path, value: str | None) -> Path:
    return Path(value) if value else corpus_root / "indexes" / "bm25" / "small_v1"


def _cmd_build_bm25_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    records_path = Path(args.records) if args.records else corpus_root / "index_inputs" / "small_index_records.jsonl"
    index_dir = _bm25_index_dir(corpus_root, args.index_dir)
    report = build_bm25_index(records_path, index_dir, k1=args.k1, b=args.b)
    _write_json_output(report.to_dict())
    return 0


def _cmd_query_bm25_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    index_dir = _bm25_index_dir(corpus_root, args.index_dir)
    retriever = BM25Retriever.load(index_dir)
    hits = retriever.query(args.query, top_k=args.top_k)
    _write_json_output({"query": args.query, "hits": [hit.to_dict() for hit in hits]})
    return 0


def _cmd_eval_bm25_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    index_dir = _bm25_index_dir(corpus_root, args.index_dir)
    cases_path = Path(args.cases) if args.cases else corpus_root / "eval" / "dense_gold_cases_zh_v1.jsonl"
    output_path = Path(args.output) if args.output else None
    expansions_path = Path(args.query_expansions)
    retriever = BM25Retriever.load(index_dir)
    cases = load_dense_eval_cases(cases_path)
    expansions = load_query_expansions(expansions_path)
    report = evaluate_bm25_retriever_with_expansion(
        retriever,
        cases,
        query_expansions=expansions,
        query_variants=_parse_csv_arg(args.query_variants),
        ranking_view=args.ranking_view,
        top_k=args.top_k,
        small_top_k=args.small_top_k,
        parent_aggregation=args.parent_aggregation,
        fusion=args.fusion,
        rrf_k=args.rrf_k,
        require_reviewed_expansions=args.require_reviewed_expansions,
        query_expansions_path=str(expansions_path),
    )
    payload = report.to_dict()
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_json_output(payload)
    return 0


def _cmd_eval_hybrid_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    dense_index_dir = (
        Path(args.dense_index_dir)
        if args.dense_index_dir
        else corpus_root / "indexes" / "dense" / _provider_index_name(args.provider)
    )
    bm25_index_dir = _bm25_index_dir(corpus_root, args.bm25_index_dir)
    cases_path = Path(args.cases) if args.cases else corpus_root / "eval" / "dense_gold_cases_zh_v1.jsonl"
    output_path = Path(args.output) if args.output else None
    expansions_path = Path(args.query_expansions)
    relevance_judgments_path = Path(args.relevance_judgments) if args.relevance_judgments else None
    provider = _create_embedding_provider(args.provider, model_path=args.model_path)
    dense_retriever = DenseRetriever.load(dense_index_dir, provider)
    bm25_retriever = BM25Retriever.load(bm25_index_dir)
    cases = load_dense_eval_cases(cases_path)
    expansions = load_query_expansions(expansions_path)
    relevance_judgments = (
        load_relevance_judgments(relevance_judgments_path)
        if relevance_judgments_path is not None
        else None
    )
    report = evaluate_hybrid_retrievers_with_expansion(
        dense_retriever,
        bm25_retriever,
        cases,
        query_expansions=expansions,
        dense_query_variants=_parse_csv_arg(args.dense_query_variants),
        bm25_query_variants=_parse_csv_arg(args.bm25_query_variants),
        small_top_k=args.small_top_k,
        top_k=args.top_k,
        rrf_k=args.rrf_k,
        require_reviewed_expansions=args.require_reviewed_expansions,
        query_expansions_path=str(expansions_path),
        relevance_judgments=relevance_judgments,
        relevance_threshold=args.relevance_threshold,
        relevance_judgments_path=str(relevance_judgments_path) if relevance_judgments_path is not None else None,
    )
    payload = report.to_dict()
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_json_output(payload)
    return 0


def _cmd_eval_hybrid_rerank_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    dense_index_dir = (
        Path(args.dense_index_dir)
        if args.dense_index_dir
        else corpus_root / "indexes" / "dense" / _provider_index_name(args.provider)
    )
    bm25_index_dir = _bm25_index_dir(corpus_root, args.bm25_index_dir)
    cases_path = Path(args.cases) if args.cases else corpus_root / "eval" / "dense_gold_cases_zh_v1.jsonl"
    output_path = Path(args.output) if args.output else None
    expansions_path = Path(args.query_expansions)
    parent_chunks_path = Path(args.parent_chunks) if args.parent_chunks else corpus_root / "chunks" / "parent_chunks.jsonl"
    small_chunks_path = (
        Path(args.small_chunks)
        if args.small_chunks
        else corpus_root / "chunks" / "small_chunks.jsonl"
    )
    relevance_judgments_path = Path(args.relevance_judgments) if args.relevance_judgments else None

    provider = _create_embedding_provider(args.provider, model_path=args.model_path)
    dense_retriever = DenseRetriever.load(dense_index_dir, provider)
    bm25_retriever = BM25Retriever.load(bm25_index_dir)
    reranker = _create_reranker_provider(
        args.reranker_provider,
        model_path=args.reranker_model_path,
        device=args.reranker_device,
        batch_size=args.reranker_batch_size,
        max_length=args.reranker_max_length,
    )
    cases = load_dense_eval_cases(cases_path)
    expansions = load_query_expansions(expansions_path)
    parent_texts = load_parent_texts(parent_chunks_path)
    small_texts = load_small_texts(small_chunks_path) if args.rerank_level == "small" else None
    relevance_judgments = (
        load_relevance_judgments(relevance_judgments_path)
        if relevance_judgments_path is not None
        else None
    )
    report = evaluate_hybrid_retrievers_with_rerank(
        dense_retriever,
        bm25_retriever,
        reranker,
        cases,
        query_expansions=expansions,
        parent_texts=parent_texts,
        small_texts=small_texts,
        relevance_judgments=relevance_judgments,
        relevance_threshold=args.relevance_threshold,
        dense_query_variants=_parse_csv_arg(args.dense_query_variants),
        bm25_query_variants=_parse_csv_arg(args.bm25_query_variants),
        rerank_level=args.rerank_level,
        rerank_query_variant=args.rerank_query_variant,
        rerank_query_variants=_parse_csv_arg(args.rerank_query_variants) if args.rerank_query_variants else None,
        joint_fusion=None if args.joint_fusion == "none" else args.joint_fusion,
        rerank_pool_size=args.rerank_pool_size,
        top_k=args.top_k,
        small_top_k=args.small_top_k,
        rrf_k=args.rrf_k,
        max_passage_chars=args.max_passage_chars,
        require_reviewed_expansions=args.require_reviewed_expansions,
        query_expansions_path=str(expansions_path),
        parent_chunks_path=str(parent_chunks_path),
        small_chunks_path=str(small_chunks_path) if args.rerank_level == "small" else None,
        relevance_judgments_path=str(relevance_judgments_path) if relevance_judgments_path is not None else None,
    )
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


def _create_reranker_provider(
    provider_name: str,
    *,
    model_path: str | None = None,
    device: str | None = None,
    batch_size: int = 32,
    max_length: int = 512,
):
    if provider_name == "fake":
        return FakeRerankerProvider()
    if provider_name == "bge-reranker":
        path = Path(model_path) if model_path else Path(".cache/models/bge-reranker-v2-m3")
        return BGEFlagRerankerProvider(
            path,
            device=device,
            batch_size=batch_size,
            max_length=max_length,
        )
    raise ValueError(f"Unsupported reranker provider: {provider_name}")


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
