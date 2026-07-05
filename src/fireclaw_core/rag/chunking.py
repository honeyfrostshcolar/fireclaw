from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Callable, Iterable

from fireclaw_core.rag.extraction import count_words


SPLIT_METHOD = "page_aware_recursive_heading_paragraph_sentence"


@dataclass(frozen=True)
class ChunkingConfig:
    parent_target_chars: int = 7000
    parent_max_chars: int = 9000
    parent_min_chars: int = 800
    parent_max_pages: int = 2
    small_target_chars: int = 1800
    small_max_chars: int = 2600
    small_min_chars: int = 300
    small_overlap_chars: int = 300


@dataclass(frozen=True)
class PageRecord:
    doc_id: str
    source_file: str
    page: int
    text: str
    title: str | None = None
    source_url: str | None = None
    publisher: str | None = None
    authority_level: str | None = None
    allowed_use: str | None = None
    domain: str | None = None
    language: str = "en"


@dataclass(frozen=True)
class TextUnit:
    text: str
    page_start: int
    page_end: int
    heading: str | None = None


@dataclass(frozen=True)
class ParentChunk:
    parent_id: str
    doc_id: str
    source_file: str
    page_start: int
    page_end: int
    heading: str | None
    text: str
    char_count: int
    word_count: int
    title: str | None = None
    source_url: str | None = None
    publisher: str | None = None
    authority_level: str | None = None
    allowed_use: str | None = None
    domain: str | None = None
    language: str = "en"
    split_method: str = SPLIT_METHOD

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SmallChunk:
    chunk_id: str
    parent_id: str
    doc_id: str
    source_file: str
    page_start: int
    page_end: int
    heading: str | None
    text: str
    char_count: int
    word_count: int
    title: str | None = None
    source_url: str | None = None
    publisher: str | None = None
    authority_level: str | None = None
    allowed_use: str | None = None
    domain: str | None = None
    language: str = "en"
    split_method: str = SPLIT_METHOD

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DocumentChunkingSummary:
    doc_id: str
    source_file: str
    status: str
    page_count: int
    parent_chunk_count: int
    small_chunk_count: int
    char_count: int
    error: str | None = None


@dataclass(frozen=True)
class ChunkingRunReport:
    status: str
    chunked_at: str
    input_pages: str
    output_parent_chunks: str
    output_small_chunks: str
    output_report: str
    document_count: int
    succeeded: int
    failed: int
    page_count: int
    parent_chunk_count: int
    small_chunk_count: int
    parent_average_chars: float
    small_average_chars: float
    parent_too_short_count: int
    parent_too_long_count: int
    small_too_short_count: int
    small_too_long_count: int
    documents: list[DocumentChunkingSummary]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["documents"] = [asdict(doc) for doc in self.documents]
        return data


def page_record_from_dict(record: dict[str, Any]) -> PageRecord:
    return PageRecord(
        doc_id=record["doc_id"],
        source_file=record["source_file"],
        page=int(record["page"]),
        text=record.get("text") or "",
        title=record.get("title"),
        source_url=record.get("source_url"),
        publisher=record.get("publisher"),
        authority_level=record.get("authority_level"),
        allowed_use=record.get("allowed_use"),
        domain=record.get("domain"),
        language=record.get("language", "en"),
    )


def chunk_document(pages: list[PageRecord], config: ChunkingConfig | None = None) -> tuple[list[ParentChunk], list[SmallChunk]]:
    if not pages:
        return [], []

    config = config or ChunkingConfig()
    pages = sorted(pages, key=lambda page: page.page)
    units = _page_units(pages, config)
    parent_chunks = _build_parent_chunks(pages[0], units, config)
    small_chunks: list[SmallChunk] = []
    for parent in parent_chunks:
        small_chunks.extend(_build_small_chunks(parent, config))
    return parent_chunks, small_chunks


def recursive_split_text(text: str, max_chars: int) -> list[str]:
    text = normalize_chunk_text(text)
    if len(text) <= max_chars:
        return [text] if text else []

    return _recursive_split(
        text,
        max_chars=max_chars,
        splitters=(
            split_by_heading,
            split_by_paragraph,
            split_by_sentence,
        ),
    )


def normalize_chunk_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    blocks = re.split(r"\n\s*\n", text)
    normalized_blocks: list[str] = []

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        parts: list[str] = []
        paragraph_lines: list[str] = []
        for line in lines:
            if is_heading_line(line):
                if paragraph_lines:
                    parts.append(" ".join(paragraph_lines))
                    paragraph_lines = []
                parts.append(line)
            else:
                paragraph_lines.append(line)
        if paragraph_lines:
            parts.append(" ".join(paragraph_lines))
        normalized_blocks.append("\n\n".join(parts))

    return "\n\n".join(normalized_blocks).strip()


def is_heading_line(line: str) -> bool:
    line = line.strip()
    if len(line) < 3 or len(line) > 140:
        return False
    if line.endswith((".", ",", ";", "\u3002", "\uff0c", "\uff1b")) and not re.match(r"^[A-Z]\.", line):
        return False

    patterns = (
        r"^(?:WARNING|Warning|CAUTION|Caution|DANGER|Danger|NOTE|Note)\b[:\uff1a]?$",
        r"^(?:\u8b66\u544a|\u5371\u9669|\u6ce8\u610f|\u63d0\u793a)[:\uff1a]?$",
        r"^(?:CHAPTER|Chapter|SECTION|Section|APPENDIX|Appendix)\b",
        r"^[IVXLCDM]+\.\s+[A-Z]",
        r"^\d+(?:\.\d+){0,5}\.?\s+\S+",
        r"^\u7b2c[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\u5343\u4e070-9]+[\u7ae0\u8282\u7bc7\u90e8\u5206]",
        r"^[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+\u3001\s*\S+",
        r"^\uff08[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u53410-9]+\uff09\s*\S+",
        r"^\d+[\u3001.]\s*[\u4e00-\u9fff]",
    )
    if any(re.match(pattern, line) for pattern in patterns):
        return True

    letters = re.sub(r"[^A-Za-z]", "", line)
    if 3 <= len(letters) and line.upper() == line and len(line.split()) <= 14:
        return True
    return False

def split_by_heading(text: str) -> list[str]:
    lines = text.splitlines()
    pieces: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        if is_heading_line(line) and current:
            pieces.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        pieces.append(current)

    return [_clean_piece("\n".join(piece)) for piece in pieces if _clean_piece("\n".join(piece))]


def split_by_paragraph(text: str) -> list[str]:
    return [_clean_piece(piece) for piece in re.split(r"\n\s*\n", text) if _clean_piece(piece)]


def split_by_sentence(text: str) -> list[str]:
    rough_sentences = re.split(r"(?<=[.!?;。！？；])\s+", text)
    return [_clean_piece(piece) for piece in rough_sentences if _clean_piece(piece)]


def _page_units(pages: list[PageRecord], config: ChunkingConfig) -> list[TextUnit]:
    units: list[TextUnit] = []
    current_heading: str | None = None

    for page in pages:
        if not page.text.strip():
            continue
        for piece in recursive_split_text(page.text, config.parent_max_chars):
            heading = first_heading(piece) or current_heading
            if first_heading(piece):
                current_heading = first_heading(piece)
            units.append(
                TextUnit(
                    text=piece,
                    page_start=page.page,
                    page_end=page.page,
                    heading=heading,
                )
            )
    return units


def first_heading(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if is_heading_line(line):
            return line
    return None


def _build_parent_chunks(seed: PageRecord, units: list[TextUnit], config: ChunkingConfig) -> list[ParentChunk]:
    parent_chunks: list[ParentChunk] = []
    current: list[TextUnit] = []

    for unit in units:
        if current and _should_start_new_parent(current, unit, config):
            parent_chunks.append(_make_parent_chunk(seed, len(parent_chunks) + 1, current))
            current = []
        current.append(unit)

    if current:
        parent_chunks.append(_make_parent_chunk(seed, len(parent_chunks) + 1, current))
    return parent_chunks


def _should_start_new_parent(current: list[TextUnit], next_unit: TextUnit, config: ChunkingConfig) -> bool:
    current_text = _join_unit_text(current)
    next_len = len(next_unit.text)
    page_span = max(next_unit.page_end, current[-1].page_end) - current[0].page_start + 1
    if page_span > config.parent_max_pages:
        return True
    if len(current_text) + 2 + next_len > config.parent_max_chars:
        return True
    if len(current_text) >= config.parent_min_chars and len(current_text) + 2 + next_len > config.parent_target_chars:
        return True
    return False


def _make_parent_chunk(seed: PageRecord, index: int, units: list[TextUnit]) -> ParentChunk:
    text = _join_unit_text(units)
    return ParentChunk(
        parent_id=f"{seed.doc_id}__parent_{index:05d}",
        doc_id=seed.doc_id,
        source_file=seed.source_file,
        page_start=units[0].page_start,
        page_end=units[-1].page_end,
        heading=next((unit.heading for unit in units if unit.heading), None),
        text=text,
        char_count=len(text),
        word_count=count_words(text),
        title=seed.title,
        source_url=seed.source_url,
        publisher=seed.publisher,
        authority_level=seed.authority_level,
        allowed_use=seed.allowed_use,
        domain=seed.domain,
        language=seed.language,
    )


def _build_small_chunks(parent: ParentChunk, config: ChunkingConfig) -> list[SmallChunk]:
    pieces = recursive_split_text(parent.text, config.small_max_chars)
    packed = _pack_small_texts(pieces, config)
    chunks: list[SmallChunk] = []
    for index, text in enumerate(packed, start=1):
        chunks.append(
            SmallChunk(
                chunk_id=f"{parent.parent_id}__small_{index:03d}",
                parent_id=parent.parent_id,
                doc_id=parent.doc_id,
                source_file=parent.source_file,
                page_start=parent.page_start,
                page_end=parent.page_end,
                heading=parent.heading,
                text=text,
                char_count=len(text),
                word_count=count_words(text),
                title=parent.title,
                source_url=parent.source_url,
                publisher=parent.publisher,
                authority_level=parent.authority_level,
                allowed_use=parent.allowed_use,
                domain=parent.domain,
                language=parent.language,
            )
        )
    return chunks


def _pack_small_texts(pieces: list[str], config: ChunkingConfig) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []

    for piece in pieces:
        if current:
            current_text = "\n\n".join(current)
            next_len = len(piece)
            if len(current_text) + 2 + next_len > config.small_max_chars:
                chunks.append(current_text)
                current = _next_small_parts(current_text, piece, config)
                continue
            if len(current_text) >= config.small_min_chars and len(current_text) + 2 + next_len > config.small_target_chars:
                chunks.append(current_text)
                current = _next_small_parts(current_text, piece, config)
                continue
        current.append(piece)

    if current:
        chunks.append("\n\n".join(current))
    return [_clean_piece(chunk) for chunk in chunks if _clean_piece(chunk)]


def _next_small_parts(previous_text: str, piece: str, config: ChunkingConfig) -> list[str]:
    overlap = _overlap_tail(previous_text, config.small_overlap_chars)
    if overlap and len(overlap) + 2 + len(piece) <= config.small_max_chars:
        return [overlap, piece]
    return [piece]

def _recursive_split(text: str, *, max_chars: int, splitters: tuple[Callable[[str], list[str]], ...]) -> list[str]:
    text = _clean_piece(text)
    if len(text) <= max_chars:
        return [text] if text else []
    if not splitters:
        return _split_by_chars(text, max_chars)

    splitter = splitters[0]
    pieces = splitter(text)
    if len(pieces) <= 1:
        return _recursive_split(text, max_chars=max_chars, splitters=splitters[1:])

    result: list[str] = []
    for piece in pieces:
        result.extend(_recursive_split(piece, max_chars=max_chars, splitters=splitters[1:]))
    return result


def _split_by_chars(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(_clean_piece(text[start:end]))
        start = end
    return [chunk for chunk in chunks if chunk]


def _join_unit_text(units: Iterable[TextUnit]) -> str:
    return "\n\n".join(unit.text for unit in units if unit.text.strip()).strip()


def _overlap_tail(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text if max_chars > 0 else ""
    tail = text[-max_chars:]
    boundary = max(tail.find("\n\n"), tail.find(". "), tail.find("。"))
    if boundary > 0:
        tail = tail[boundary + 1 :]
    return _clean_piece(tail)


def _clean_piece(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

