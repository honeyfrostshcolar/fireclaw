from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

from fireclaw_core.rag.chunking import ChunkingConfig
from fireclaw_core.rag.chunking import ChunkingRunReport
from fireclaw_core.rag.chunking import DocumentChunkingSummary
from fireclaw_core.rag.chunking import PageRecord
from fireclaw_core.rag.chunking import chunk_document
from fireclaw_core.rag.chunking import page_record_from_dict


def chunk_corpus_pages(
    *,
    pages_path: Path,
    output_dir: Path,
    config: ChunkingConfig | None = None,
    limit_docs: int | None = None,
) -> ChunkingRunReport:
    config = config or ChunkingConfig()
    pages_path = pages_path.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    parent_chunks_path = output_dir / "parent_chunks.jsonl"
    small_chunks_path = output_dir / "small_chunks.jsonl"
    report_path = output_dir / "chunk_report.json"

    grouped_pages = _load_pages_by_doc(pages_path)
    doc_ids = sorted(grouped_pages)
    if limit_docs is not None:
        doc_ids = doc_ids[:limit_docs]

    summaries: list[DocumentChunkingSummary] = []
    total_pages = 0
    total_parent_chunks = 0
    total_small_chunks = 0
    parent_chars: list[int] = []
    small_chars: list[int] = []

    with parent_chunks_path.open("w", encoding="utf-8", newline="\n") as parent_handle:
        with small_chunks_path.open("w", encoding="utf-8", newline="\n") as small_handle:
            for doc_id in doc_ids:
                pages = grouped_pages[doc_id]
                page_count = len(pages)
                total_pages += page_count
                try:
                    parent_chunks, small_chunks = chunk_document(pages, config)
                    for parent in parent_chunks:
                        parent_handle.write(json.dumps(parent.to_dict(), ensure_ascii=False) + "\n")
                        parent_chars.append(parent.char_count)
                    for small in small_chunks:
                        small_handle.write(json.dumps(small.to_dict(), ensure_ascii=False) + "\n")
                        small_chars.append(small.char_count)

                    total_parent_chunks += len(parent_chunks)
                    total_small_chunks += len(small_chunks)
                    summaries.append(
                        DocumentChunkingSummary(
                            doc_id=doc_id,
                            source_file=pages[0].source_file,
                            status="chunked",
                            page_count=page_count,
                            parent_chunk_count=len(parent_chunks),
                            small_chunk_count=len(small_chunks),
                            char_count=sum(parent.char_count for parent in parent_chunks),
                        )
                    )
                except Exception as exc:
                    summaries.append(
                        DocumentChunkingSummary(
                            doc_id=doc_id,
                            source_file=pages[0].source_file if pages else "",
                            status="failed",
                            page_count=page_count,
                            parent_chunk_count=0,
                            small_chunk_count=0,
                            char_count=0,
                            error=str(exc),
                        )
                    )

    failed = sum(1 for summary in summaries if summary.status == "failed")
    report = ChunkingRunReport(
        status="completed" if failed == 0 else "completed_with_errors",
        chunked_at=datetime.now(timezone.utc).isoformat(),
        input_pages=str(pages_path),
        output_parent_chunks=str(parent_chunks_path),
        output_small_chunks=str(small_chunks_path),
        output_report=str(report_path),
        document_count=len(summaries),
        succeeded=len(summaries) - failed,
        failed=failed,
        page_count=total_pages,
        parent_chunk_count=total_parent_chunks,
        small_chunk_count=total_small_chunks,
        parent_average_chars=_average(parent_chars),
        small_average_chars=_average(small_chars),
        parent_too_short_count=sum(1 for value in parent_chars if value < config.parent_min_chars),
        parent_too_long_count=sum(1 for value in parent_chars if value > config.parent_max_chars),
        small_too_short_count=sum(1 for value in small_chars if value < config.small_min_chars),
        small_too_long_count=sum(1 for value in small_chars if value > config.small_max_chars),
        documents=summaries,
    )
    report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _load_pages_by_doc(pages_path: Path) -> dict[str, list[PageRecord]]:
    if not pages_path.exists():
        raise FileNotFoundError(f"Page JSONL not found: {pages_path}")

    grouped: dict[str, list[PageRecord]] = {}
    with pages_path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL in {pages_path}:{line_no}: {exc}") from exc
            page = page_record_from_dict(record)
            grouped.setdefault(page.doc_id, []).append(page)

    for pages in grouped.values():
        pages.sort(key=lambda page: page.page)
    return grouped


def _average(values: list[int]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)

