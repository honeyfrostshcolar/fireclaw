from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from fireclaw_core.rag.chunking import ChunkingConfig
from fireclaw_core.rag.corpus_chunking import chunk_corpus_pages
from fireclaw_core.rag.corpus_extraction import extract_corpus_pages
from fireclaw_core.rag.extraction import PdfTextExtractionError
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

    args = parser.parse_args(argv)
    if args.command == "extract-pages":
        return _cmd_extract_pages(args)
    if args.command == "chunk-pages":
        return _cmd_chunk_pages(args)
    if args.command == "prepare-index":
        return _cmd_prepare_index(args)
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

    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
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
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
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
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
