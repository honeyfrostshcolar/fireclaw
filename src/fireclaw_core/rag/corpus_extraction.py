from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

from fireclaw_core.rag.extraction import DocumentExtractionSummary
from fireclaw_core.rag.extraction import ExtractedPage
from fireclaw_core.rag.extraction import ExtractionRunReport
from fireclaw_core.rag.extraction import PdftotextPageExtractor
from fireclaw_core.rag.extraction import count_words
from fireclaw_core.rag.extraction import load_manifest_metadata
from fireclaw_core.rag.extraction import source_metadata_for
from fireclaw_core.rag.extraction import text_quality


def extract_corpus_pages(
    *,
    corpus_root: Path,
    raw_dir: Path,
    output_dir: Path,
    manifest_paths: list[Path],
    extractor: PdftotextPageExtractor | None = None,
    limit: int | None = None,
) -> ExtractionRunReport:
    raw_dir = raw_dir.resolve()
    corpus_root = corpus_root.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pages_path = output_dir / "pages.jsonl"
    report_path = output_dir / "extraction_report.json"
    extractor = extractor or PdftotextPageExtractor()
    manifest = load_manifest_metadata(corpus_root, manifest_paths)
    pdf_paths = sorted(raw_dir.glob("*.pdf"))
    if limit is not None:
        pdf_paths = pdf_paths[:limit]

    summaries: list[DocumentExtractionSummary] = []
    total_pages = 0
    total_chars = 0

    with pages_path.open("w", encoding="utf-8", newline="\n") as handle:
        for pdf_path in pdf_paths:
            metadata = source_metadata_for(pdf_path, raw_dir, manifest)
            try:
                page_texts = extractor.extract_pages(pdf_path)
            except Exception as exc:
                summaries.append(
                    DocumentExtractionSummary(
                        doc_id=metadata.doc_id,
                        source_file=metadata.source_file,
                        status="failed",
                        extraction_method=getattr(extractor, "method_name", None),
                        error=str(exc),
                    )
                )
                continue

            nonempty_pages = 0
            doc_chars = 0
            for page_number, text in enumerate(page_texts, start=1):
                char_count = len(text)
                if text.strip():
                    nonempty_pages += 1
                doc_chars += char_count
                page = ExtractedPage(
                    doc_id=metadata.doc_id,
                    source_file=metadata.source_file,
                    page=page_number,
                    text=text,
                    char_count=char_count,
                    word_count=count_words(text),
                    text_quality=text_quality(text),
                    extraction_method=getattr(extractor, "method_name", "unknown"),
                    title=metadata.title,
                    source_url=metadata.source_url,
                    publisher=metadata.publisher,
                    authority_level=metadata.authority_level,
                    allowed_use=metadata.allowed_use,
                    domain=metadata.domain,
                    language=metadata.language,
                )
                handle.write(json.dumps(asdict(page), ensure_ascii=False) + "\n")

            total_pages += len(page_texts)
            total_chars += doc_chars
            summaries.append(
                DocumentExtractionSummary(
                    doc_id=metadata.doc_id,
                    source_file=metadata.source_file,
                    status="extracted",
                    page_count=len(page_texts),
                    nonempty_pages=nonempty_pages,
                    char_count=doc_chars,
                    extraction_method=getattr(extractor, "method_name", None),
                )
            )

    failed = sum(1 for summary in summaries if summary.status == "failed")
    report = ExtractionRunReport(
        status="completed" if failed == 0 else "completed_with_errors",
        extracted_at=datetime.now(timezone.utc).isoformat(),
        raw_dir=str(raw_dir),
        output_pages=str(pages_path),
        output_report=str(report_path),
        document_count=len(summaries),
        succeeded=len(summaries) - failed,
        failed=failed,
        page_count=total_pages,
        char_count=total_chars,
        documents=summaries,
    )
    report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return report
