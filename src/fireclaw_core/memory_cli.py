from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FireClaw memory indexing and evaluation CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    idx = subparsers.add_parser("index", help="Index memory JSONL into SQLite FTS5.")
    idx.add_argument("--memory-path", required=True, help="Path to mission memory JSONL.")
    idx.add_argument("--index-path", required=True, help="Path to output SQLite index.")

    ev = subparsers.add_parser("eval", help="Evaluate retrieval quality against a fixture.")
    ev.add_argument("--index-path", required=True, help="Path to SQLite memory index.")
    ev.add_argument("--fixture", required=True, help="Path to eval cases JSON file.")
    ev.add_argument("--threshold", type=float, default=1.0, help="Minimum hit_rate to pass.")
    ev.add_argument("--limit", type=int, default=5, help="Max results per query.")

    args = parser.parse_args(argv)

    if args.command == "index":
        return _cmd_index(args)
    if args.command == "eval":
        return _cmd_eval(args)
    return 1


def _cmd_index(args: argparse.Namespace) -> int:
    from fireclaw_core.memory_index import SqliteMemoryIndex
    from fireclaw_core.mission_memory import MissionMemoryStore

    memory_path = Path(args.memory_path)
    if not memory_path.exists():
        print(f"Error: memory file not found: {memory_path}", file=sys.stderr)
        return 1

    store = MissionMemoryStore(memory_path)
    index = SqliteMemoryIndex(args.index_path)

    records = [record.to_dict() for record in store.list_records()]
    count = index.rebuild(records)

    print(json.dumps({"status": "indexed", "record_count": count, "index_path": str(args.index_path)}))
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    from fireclaw_core.memory_eval import evaluate_retrieval, load_eval_cases
    from fireclaw_core.memory_index import SqliteMemoryIndex
    from fireclaw_core.memory_retrieval import MemoryRetriever

    index_path = Path(args.index_path)
    fixture_path = Path(args.fixture)

    if not index_path.exists():
        print(f"Error: index not found: {index_path}", file=sys.stderr)
        return 1
    if not fixture_path.exists():
        print(f"Error: fixture not found: {fixture_path}", file=sys.stderr)
        return 1

    index = SqliteMemoryIndex(str(index_path))
    retriever = MemoryRetriever(index=index)
    cases = load_eval_cases(fixture_path)
    report = evaluate_retrieval(retriever, cases, limit=args.limit)

    result = report.to_dict()
    result["threshold"] = args.threshold
    result["meets_threshold"] = report.meets_threshold(args.threshold)

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not report.meets_threshold(args.threshold):
        print(f"FAIL: hit_rate {report.hit_rate:.3f} < threshold {args.threshold}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
