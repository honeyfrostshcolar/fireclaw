from __future__ import annotations

import json
from pathlib import Path

from fireclaw_core.memory.rag_indexing import (
    build_memory_rag_indexes,
    project_mission_memory_record,
)
from fireclaw_core.mission.mission_memory import MissionMemoryRecord
from fireclaw_core.rag.bm25_retrieval import BM25Retriever


def _record() -> MissionMemoryRecord:
    return MissionMemoryRecord(
        record_id="event-1",
        mission_id="mission-1",
        record_type="observation",
        content={
            "note": "thermal camera found a victim",
            "_embodied": {
                "runtime_mode": "simulation",
                "sensitivity": "standard",
            },
        },
        created_at="2026-07-27T00:00:00+00:00",
    )


def test_projection_marks_authority_namespace_and_preserves_id() -> None:
    projected = project_mission_memory_record(_record())

    assert projected["record_id"] == "event-1"
    assert projected["source_kind"] == "mission_memory"
    assert projected["node_type"] == "observation"
    assert projected["indexable"] is True
    assert "thermal camera" in projected["clean_text"]


def test_build_memory_bm25_index_is_queryable(tmp_path: Path) -> None:
    memory_path = tmp_path / "memory.jsonl"
    memory_path.write_text(
        json.dumps(_record().to_dict(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    report = build_memory_rag_indexes(memory_path, tmp_path / "rag")
    hits = BM25Retriever.load(tmp_path / "rag" / "bm25").query("thermal victim")

    assert report.record_count == 1
    assert report.dense is None
    assert hits[0].record["record_id"] == "event-1"
    assert hits[0].record["source_kind"] == "mission_memory"
