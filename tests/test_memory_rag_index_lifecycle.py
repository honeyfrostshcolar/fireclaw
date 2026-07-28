from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fireclaw_core.memory.rag_index_lifecycle import (
    MemoryRagIndexLifecycleManager,
)
from fireclaw_core.memory.rag_indexing import build_memory_rag_indexes
from fireclaw_core.mission.mission_memory import MissionMemoryRecord
from fireclaw_core.rag.runtime_retrieval import (
    RagRuntimeConfig,
    build_runtime_rag_retriever,
)


def _record(record_id: str, note: str) -> dict[str, Any]:
    return MissionMemoryRecord(
        record_id=record_id,
        mission_id="mission-1",
        record_type="observation",
        content={
            "note": note,
            "_embodied": {
                "runtime_mode": "simulation",
                "sensitivity": "standard",
            },
        },
        created_at="2026-07-27T00:00:00+00:00",
    ).to_dict()


def _append(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _config(root: Path) -> RagRuntimeConfig:
    return RagRuntimeConfig(
        backend="bm25",
        source_kind="mission_memory",
        generation_root=root,
    )


def test_publishes_versioned_generation_and_hot_reloads(tmp_path: Path) -> None:
    memory_path = tmp_path / "memory.jsonl"
    generation_root = tmp_path / "rag-generations"
    _append(memory_path, _record("event-1", "thermal victim"))
    config = _config(generation_root)
    manager = MemoryRagIndexLifecycleManager(
        memory_path=memory_path,
        generation_root=generation_root,
        rag_config=config,
    )

    first = manager.refresh(trigger_reason="runtime_startup")
    retriever = build_runtime_rag_retriever(config)
    assert retriever.query("thermal", top_k=1)[0].record["record_id"] == "event-1"
    assert retriever.status()["source_current"] is True

    _append(memory_path, _record("event-2", "blocked stairwell"))
    assert retriever.status()["source_current"] is False
    second = manager.refresh(
        trigger_reason="mission_terminal",
        boundary_id="boundary-1",
    )

    assert first.status == "published"
    assert second.status == "published"
    assert second.previous_generation_id == first.generation_id
    assert second.generation_id != first.generation_id
    assert retriever.query("stairwell", top_k=1)[0].record["record_id"] == "event-2"
    assert retriever.status()["generation_id"] == second.generation_id
    assert retriever.status()["source_current"] is True


def test_failed_build_keeps_previous_generation_active(tmp_path: Path) -> None:
    memory_path = tmp_path / "memory.jsonl"
    generation_root = tmp_path / "rag-generations"
    _append(memory_path, _record("event-1", "thermal victim"))
    config = _config(generation_root)
    manager = MemoryRagIndexLifecycleManager(
        memory_path=memory_path,
        generation_root=generation_root,
        rag_config=config,
    )
    first = manager.refresh(trigger_reason="runtime_startup")
    retriever = build_runtime_rag_retriever(config)
    assert retriever.query("thermal", top_k=1)

    _append(memory_path, _record("event-2", "collapsed corridor"))

    def failing_builder(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected build failure")

    failing_manager = MemoryRagIndexLifecycleManager(
        memory_path=memory_path,
        generation_root=generation_root,
        rag_config=config,
        index_builder=failing_builder,
    )
    failed = failing_manager.refresh(
        trigger_reason="mission_terminal",
        boundary_id="boundary-2",
    )
    pointer = json.loads(
        (generation_root / "current.json").read_text(encoding="utf-8")
    )

    assert failed.status == "failed_using_previous"
    assert failed.generation_id == first.generation_id
    assert pointer["generation_id"] == first.generation_id
    assert retriever.query("thermal", top_k=1)[0].record["record_id"] == "event-1"
    assert retriever.query("corridor", top_k=1) == []
    assert retriever.status()["source_current"] is False
    assert not list((generation_root / "generations").glob(".building-*"))


def test_reload_failure_keeps_in_memory_retriever(tmp_path: Path) -> None:
    memory_path = tmp_path / "memory.jsonl"
    generation_root = tmp_path / "rag-generations"
    _append(memory_path, _record("event-1", "thermal victim"))
    config = _config(generation_root)
    manager = MemoryRagIndexLifecycleManager(
        memory_path=memory_path,
        generation_root=generation_root,
        rag_config=config,
    )
    first = manager.refresh(trigger_reason="runtime_startup")
    retriever = build_runtime_rag_retriever(config)
    assert retriever.query("thermal", top_k=1)

    pointer_path = generation_root / "current.json"
    broken = json.loads(pointer_path.read_text(encoding="utf-8"))
    broken["generation_id"] = "missing-generation"
    broken["bm25_index_dir"] = "generations/missing-generation/bm25"
    pointer_path.write_text(json.dumps(broken), encoding="utf-8")

    hits = retriever.query("thermal", top_k=1)

    assert hits[0].record["record_id"] == "event-1"
    assert retriever.status()["generation_id"] == first.generation_id
    assert retriever.status()["last_reload_error"] is not None


def test_rejects_generation_when_authority_changes_during_build(
    tmp_path: Path,
) -> None:
    memory_path = tmp_path / "memory.jsonl"
    generation_root = tmp_path / "rag-generations"
    _append(memory_path, _record("event-1", "thermal victim"))

    def changing_builder(*args: Any, **kwargs: Any) -> Any:
        report = build_memory_rag_indexes(*args, **kwargs)
        _append(memory_path, _record("event-2", "authority changed"))
        return report

    manager = MemoryRagIndexLifecycleManager(
        memory_path=memory_path,
        generation_root=generation_root,
        rag_config=_config(generation_root),
        index_builder=changing_builder,
    )
    report = manager.refresh(trigger_reason="mission_terminal")

    assert report.status == "failed_using_previous"
    assert report.error_code == "authority_changed_during_build"
    assert not (generation_root / "current.json").exists()
    assert not list((generation_root / "generations").glob(".building-*"))


def test_unchanged_source_reuses_current_generation(tmp_path: Path) -> None:
    memory_path = tmp_path / "memory.jsonl"
    generation_root = tmp_path / "rag-generations"
    _append(memory_path, _record("event-1", "thermal victim"))
    manager = MemoryRagIndexLifecycleManager(
        memory_path=memory_path,
        generation_root=generation_root,
        rag_config=_config(generation_root),
    )

    first = manager.refresh(trigger_reason="runtime_startup")
    second = manager.refresh(trigger_reason="subtask_terminal")

    assert second.status == "unchanged"
    assert second.generation_id == first.generation_id


def test_version_mismatch_forces_validated_rebuild(tmp_path: Path) -> None:
    memory_path = tmp_path / "memory.jsonl"
    generation_root = tmp_path / "rag-generations"
    _append(memory_path, _record("event-1", "thermal victim"))
    manager = MemoryRagIndexLifecycleManager(
        memory_path=memory_path,
        generation_root=generation_root,
        rag_config=_config(generation_root),
    )
    first = manager.refresh(trigger_reason="runtime_startup")
    manifest_path = (
        generation_root
        / "generations"
        / str(first.generation_id)
        / "generation.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_version"] = "corrupted-version"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    rebuilt = manager.refresh(trigger_reason="runtime_startup")

    assert rebuilt.status == "published"
    assert rebuilt.generation_id != first.generation_id
    assert rebuilt.previous_generation_id == first.generation_id
