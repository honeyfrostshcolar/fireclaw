from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Iterable


class PdfTextExtractionError(RuntimeError):
    """Raised when a PDF text backend cannot extract a document."""


@dataclass(frozen=True)
class SourceMetadata:
    doc_id: str
    source_file: str
    title: str | None = None
    source_url: str | None = None
    publisher: str | None = None
    authority_level: str | None = None
    allowed_use: str | None = None
    domain: str | None = None
    language: str = "en"


@dataclass(frozen=True)
class ExtractedPage:
    doc_id: str
    source_file: str
    page: int
    text: str
    char_count: int
    word_count: int
    text_quality: str
    extraction_method: str
    title: str | None = None
    source_url: str | None = None
    publisher: str | None = None
    authority_level: str | None = None
    allowed_use: str | None = None
    domain: str | None = None
    language: str = "en"


@dataclass(frozen=True)
class DocumentExtractionSummary:
    doc_id: str
    source_file: str
    status: str
    page_count: int = 0
    nonempty_pages: int = 0
    char_count: int = 0
    extraction_method: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ExtractionRunReport:
    status: str
    extracted_at: str
    raw_dir: str
    output_pages: str
    output_report: str
    document_count: int
    succeeded: int
    failed: int
    page_count: int
    char_count: int
    documents: list[DocumentExtractionSummary]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["documents"] = [asdict(doc) for doc in self.documents]
        return data


class PdftotextPageExtractor:
    """Extract page text with Poppler's pdftotext command."""

    method_name = "pdftotext"

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or shutil.which("pdftotext")
        if self.executable is None:
            raise PdfTextExtractionError(
                "pdftotext was not found on PATH. Install Poppler or add another PDF backend."
            )

    def extract_pages(self, pdf_path: Path) -> list[str]:
        completed = subprocess.run(
            [self.executable, "-enc", "UTF-8", str(pdf_path), "-"],
            check=False,
            capture_output=True,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise PdfTextExtractionError(stderr or f"pdftotext failed with exit code {completed.returncode}")
        raw_text = completed.stdout.decode("utf-8", errors="replace")
        pages = raw_text.split("\f")
        if pages and not pages[-1].strip():
            pages = pages[:-1]
        return [clean_page_text(page) for page in pages]


def clean_page_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def text_quality(text: str) -> str:
    if not text.strip():
        return "empty"
    replacement_ratio = text.count("\ufffd") / max(len(text), 1)
    if replacement_ratio > 0.02:
        return "suspect_encoding"
    if len(text.strip()) < 80:
        return "short"
    return "ok"


def count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def doc_id_from_path(path: Path) -> str:
    raw = path.stem.lower()
    raw = re.sub(r"[^a-z0-9]+", "_", raw)
    return raw.strip("_") or "document"


def load_manifest_metadata(corpus_root: Path, manifest_paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for manifest_path in manifest_paths:
        if not manifest_path.exists():
            continue
        with manifest_path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSONL in {manifest_path}:{line_no}: {exc}") from exc
                file_value = record.get("file")
                if not file_value:
                    continue
                resolved = (corpus_root / file_value).resolve()
                metadata[_path_key(resolved)] = record
                metadata[Path(file_value).name.lower()] = record
    return metadata


def source_metadata_for(pdf_path: Path, raw_dir: Path, manifest: dict[str, dict[str, Any]]) -> SourceMetadata:
    record = manifest.get(_path_key(pdf_path.resolve())) or manifest.get(pdf_path.name.lower()) or {}
    try:
        source_file = pdf_path.relative_to(raw_dir.parent).as_posix()
    except ValueError:
        source_file = pdf_path.as_posix()
    return SourceMetadata(
        doc_id=doc_id_from_path(pdf_path),
        source_file=source_file,
        title=record.get("title"),
        source_url=record.get("source_url"),
        publisher=record.get("publisher"),
        authority_level=record.get("authority_level"),
        allowed_use=record.get("allowed_use"),
        domain=record.get("domain"),
        language=record.get("language", "en"),
    )


def _path_key(path: Path) -> str:
    return str(path).replace("\\", "/").lower()
