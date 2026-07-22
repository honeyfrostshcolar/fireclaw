from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from fireclaw_core.rag.extraction import count_words


@dataclass(frozen=True)
class IndexPreparationConfig:
    min_clean_chars: int = 300
    min_clean_words: int = 20
    header_footer_max_chars: int = 220


@dataclass(frozen=True)
class IndexRecord:
    chunk_id: str
    parent_id: str
    doc_id: str
    source_file: str
    page_start: int
    page_end: int
    heading: str | None
    clean_text: str
    clean_char_count: int
    clean_word_count: int
    indexable: bool
    retrieval_weight: float
    cleaning_flags: list[str]
    title: str | None = None
    source_url: str | None = None
    publisher: str | None = None
    authority_level: str | None = None
    allowed_use: str | None = None
    domain: str | None = None
    language: str = "en"
    source_chunk_char_count: int = 0
    source_chunk_word_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IndexPreparationReport:
    status: str
    prepared_at: str
    input_small_chunks: str
    output_index_records: str
    output_report: str
    source_chunk_count: int
    index_record_count: int
    indexable_count: int
    non_indexable_count: int
    control_char_record_count: int
    too_short_count: int
    blank_page_like_count: int
    toc_like_count: int
    cover_page_like_count: int
    header_footer_like_count: int
    average_clean_chars: float
    flags: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prepare_index_records(
    *,
    small_chunks_path: Path,
    output_dir: Path,
    config: IndexPreparationConfig | None = None,
) -> IndexPreparationReport:
    config = config or IndexPreparationConfig()
    small_chunks_path = small_chunks_path.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    records_path = output_dir / "small_index_records.jsonl"
    report_path = output_dir / "index_preparation_report.json"

    source_rows = _load_jsonl(small_chunks_path)
    records: list[IndexRecord] = []
    flag_counts: dict[str, int] = {}

    with records_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in source_rows:
            record = prepare_index_record(row, config)
            records.append(record)
            for flag in record.cleaning_flags:
                flag_counts[flag] = flag_counts.get(flag, 0) + 1
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    report = IndexPreparationReport(
        status="completed",
        prepared_at=datetime.now(timezone.utc).isoformat(),
        input_small_chunks=str(small_chunks_path),
        output_index_records=str(records_path),
        output_report=str(report_path),
        source_chunk_count=len(source_rows),
        index_record_count=len(records),
        indexable_count=sum(1 for record in records if record.indexable),
        non_indexable_count=sum(1 for record in records if not record.indexable),
        control_char_record_count=flag_counts.get("control_chars_removed", 0),
        too_short_count=flag_counts.get("too_short", 0),
        blank_page_like_count=flag_counts.get("blank_page_like", 0),
        toc_like_count=flag_counts.get("toc_like", 0),
        cover_page_like_count=flag_counts.get("cover_page_like", 0),
        header_footer_like_count=flag_counts.get("header_footer_like", 0),
        average_clean_chars=_average([record.clean_char_count for record in records]),
        flags=dict(sorted(flag_counts.items())),
    )
    report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def prepare_index_record(chunk: dict[str, Any], config: IndexPreparationConfig | None = None) -> IndexRecord:
    config = config or IndexPreparationConfig()
    original_text = chunk.get("text") or ""
    clean_text, removed_control_chars = clean_text_for_embedding(original_text)
    flags = cleaning_flags_for(chunk, clean_text, removed_control_chars, config)
    indexable = is_indexable(flags)
    retrieval_weight = retrieval_weight_for(flags, indexable)

    return IndexRecord(
        chunk_id=chunk["chunk_id"],
        parent_id=chunk["parent_id"],
        doc_id=chunk["doc_id"],
        source_file=chunk["source_file"],
        page_start=int(chunk["page_start"]),
        page_end=int(chunk["page_end"]),
        heading=chunk.get("heading"),
        clean_text=clean_text,
        clean_char_count=len(clean_text),
        clean_word_count=count_words(clean_text),
        indexable=indexable,
        retrieval_weight=retrieval_weight,
        cleaning_flags=flags,
        title=chunk.get("title"),
        source_url=chunk.get("source_url"),
        publisher=chunk.get("publisher"),
        authority_level=chunk.get("authority_level"),
        allowed_use=chunk.get("allowed_use"),
        domain=chunk.get("domain"),
        language=chunk.get("language", "en"),
        source_chunk_char_count=int(chunk.get("char_count", len(original_text))),
        source_chunk_word_count=int(chunk.get("word_count", count_words(original_text))),
    )


def clean_text_for_embedding(text: str) -> tuple[str, int]:
    removed = 0
    cleaned_chars: list[str] = []
    for char in text:
        if ord(char) < 32 and char not in "\n\t":
            removed += 1
            continue
        cleaned_chars.append(char)

    cleaned = "".join(cleaned_chars)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip(), removed


def cleaning_flags_for(
    chunk: dict[str, Any],
    clean_text: str,
    removed_control_chars: int,
    config: IndexPreparationConfig,
) -> list[str]:
    flags: list[str] = []
    clean_words = count_words(clean_text)

    if removed_control_chars:
        flags.append("control_chars_removed")
    if not clean_text:
        flags.append("empty_clean_text")
    if "\ufffd" in clean_text:
        flags.append("replacement_char_present")
    if len(clean_text) < config.min_clean_chars or clean_words < config.min_clean_words:
        flags.append("too_short")
    if is_blank_page_like(clean_text, chunk):
        flags.append("blank_page_like")
    if is_toc_like(clean_text, chunk):
        flags.append("toc_like")
    if is_cover_page_like(clean_text, chunk):
        flags.append("cover_page_like")
    if is_header_footer_like(clean_text, config):
        flags.append("header_footer_like")
    if is_formula_heavy(clean_text):
        flags.append("formula_or_table_heavy")

    return flags


def is_indexable(flags: list[str]) -> bool:
    blocking_flags = {
        "empty_clean_text",
        "blank_page_like",
        "cover_page_like",
        "header_footer_like",
    }
    return not any(flag in blocking_flags for flag in flags)


def retrieval_weight_for(flags: list[str], indexable: bool) -> float:
    if not indexable:
        return 0.0
    weight = 1.0
    if "too_short" in flags:
        weight *= 0.5
    if "toc_like" in flags:
        weight *= 0.25
    if "formula_or_table_heavy" in flags:
        weight *= 0.75
    if "control_chars_removed" in flags:
        weight *= 0.9
    return round(weight, 3)


def is_blank_page_like(text: str, chunk: dict[str, Any]) -> bool:
    normalized = _normalized_line_text(text)
    heading = _normalized_line_text(str(chunk.get("heading") or ""))
    if "blank page" in heading:
        return True
    if len(normalized) <= 160 and "blank page" in normalized:
        return True
    if len(normalized) < 80 and re.fullmatch(r"(?:\d+\s*)?(?:page\s*)?\d*", normalized):
        return True
    return False


def is_toc_like(text: str, chunk: dict[str, Any]) -> bool:
    lowered = text.lower()
    heading = str(chunk.get("heading") or "").lower()
    if "table of contents" in lowered or heading.strip() in {"contents", "table of contents"}:
        return True
    if not re.search(r"\bcontents\b", lowered[:500]) and "abbreviations" not in lowered[:500]:
        return False

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 4:
        return False

    dot_leader_lines = sum(1 for line in lines if re.search(r"\.{4,}\s*\d+\s*$", line))
    return dot_leader_lines >= 2


def is_cover_page_like(text: str, chunk: dict[str, Any]) -> bool:
    page_start = int(chunk.get("page_start", 0))
    if page_start > 2:
        return False

    lowered = text.lower()
    if "contents" in lowered or "table of contents" in lowered:
        return False

    title = str(chunk.get("title") or "").lower()
    publication_markers = (
        "nist special publication",
        "department of homeland security",
        "u.s. fire administration",
    )
    title_overlap = bool(title and len(title) >= 20 and title[:40] in lowered)
    has_publication_marker = any(marker in lowered for marker in publication_markers)
    has_abstract_or_body = any(marker in lowered for marker in ("abstract", "introduction", "chapter", "section"))
    return (title_overlap or has_publication_marker) and not has_abstract_or_body and len(text) < 1500


def is_header_footer_like(text: str, config: IndexPreparationConfig) -> bool:
    if len(text) > config.header_footer_max_chars:
        return False
    lowered = _normalized_line_text(text)
    patterns = (
        "united nations office for the coordination of humanitarian",
        "www.unocha.org",
        "firefighter autopsy protocol",
        "nist special publication",
    )
    if any(pattern in lowered for pattern in patterns):
        return True
    if re.fullmatch(r"(?:\d+\s+)?(?:chapter|section|annex)?\s*\d*", lowered):
        return True
    return False


def is_formula_heavy(text: str) -> bool:
    if len(text) < 200:
        return False
    formula_symbols = (
        "=+-*/<>"
        "\u2264\u2265\u2207\u2206\u2202\u2211\u221e"
        "\u03b1\u03b2\u03c1\u03c6\u03b8\u03bb\u03bc"
    )
    symbols = sum(1 for char in text if char in formula_symbols)
    digits = sum(1 for char in text if char.isdigit())
    return (symbols + digits) / max(len(text), 1) > 0.18


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL in {path}:{line_no}: {exc}") from exc
    return records


def _average(values: list[int]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def _normalized_line_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())

