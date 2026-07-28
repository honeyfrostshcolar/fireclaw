"""Project authoritative mission memory into indexes owned by RAG."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable

from fireclaw_core.mission.mission_memory import (
    DEFAULT_INDEXABLE_TYPES,
    MissionMemoryRecord,
)
from fireclaw_core.rag.bm25_retrieval import build_bm25_index
from fireclaw_core.rag.dense_retrieval import (
    EmbeddingProvider,
    build_dense_index,
    write_jsonl,
)


@dataclass(frozen=True)
class MemoryRagIndexReport:
    records_path: str
    record_count: int
    bm25: dict[str, Any] | None
    dense: dict[str, Any] | None


def project_mission_memory_record(
    record: MissionMemoryRecord | dict[str, Any],
    *,
    indexable_types: Iterable[str] = DEFAULT_INDEXABLE_TYPES,
) -> dict[str, Any]:
    """Create a typed RAG record while preserving the authority record ID."""
    value = record.to_dict() if isinstance(record, MissionMemoryRecord) else dict(record)
    content = value.get("content")
    if not isinstance(content, dict):
        content = {}
    record_type = str(value.get("record_type") or "")
    return {
        **value,
        "content": content,
        "source_kind": "mission_memory",
        "node_type": record_type,
        "indexable": record_type in frozenset(indexable_types),
        "clean_text": _memory_text(value, content),
    }


def build_memory_rag_indexes(
    memory_path: Path,
    output_dir: Path,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    build_bm25: bool = True,
    build_dense: bool = False,
    indexable_types: Iterable[str] = DEFAULT_INDEXABLE_TYPES,
) -> MemoryRagIndexReport:
    """Build derived RAG indexes from the mission-memory authority JSONL."""
    if not build_bm25 and not build_dense:
        raise ValueError("at least one RAG index must be requested")
    if build_dense and embedding_provider is None:
        raise ValueError("build_dense requires an embedding_provider")

    memory_path = Path(memory_path)
    output_dir = Path(output_dir)
    records = _load_memory_records(memory_path)
    selected_types = frozenset(indexable_types)
    projected = [
        project_mission_memory_record(record, indexable_types=selected_types)
        for record in records
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    records_path = output_dir / "records.jsonl"
    write_jsonl(records_path, projected)

    bm25_report = (
        build_bm25_index(records_path, output_dir / "bm25").to_dict()
        if build_bm25
        else None
    )
    dense_report = (
        build_dense_index(
            records_path,
            output_dir / "dense",
            embedding_provider,
        ).to_dict()
        if build_dense and embedding_provider is not None
        else None
    )
    return MemoryRagIndexReport(
        records_path=str(records_path),
        record_count=len(projected),
        bm25=bm25_report,
        dense=dense_report,
    )


def _load_memory_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Mission memory JSONL not found: {path}")
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL in {path}:{line_no}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Mission memory record must be an object: {path}:{line_no}")
            records.append(value)
    return records


def _memory_text(record: dict[str, Any], content: dict[str, Any]) -> str:
    fields = [
        str(record.get("record_type") or ""),
        str(record.get("mission_id") or ""),
        json.dumps(content, ensure_ascii=False, sort_keys=True),
    ]
    return " ".join(field for field in fields if field)
