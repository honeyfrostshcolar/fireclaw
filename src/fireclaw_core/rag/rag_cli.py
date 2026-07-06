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
from fireclaw_core.rag.dense_retrieval import DenseRetriever
from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider
from fireclaw_core.rag.dense_retrieval import build_dense_index
from fireclaw_core.rag.dense_retrieval import expand_hits_to_parents
from fireclaw_core.rag.index_preparation import IndexPreparationConfig
from fireclaw_core.rag.index_preparation import prepare_index_records


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
