"""Tests for planner memory retrieval scope boundaries."""
from __future__ import annotations

from pathlib import Path

from fireclaw_core.memory.embodied_memory import EmbodiedMemoryEvent
from fireclaw_core.memory.memory_index import SqliteMemoryIndex
from fireclaw_core.memory.memory_retrieval import MemoryRetrievalScope, MemoryRetriever


def _indexed_event(
    *,
    event_id: str,
    mission_id: str,
    runtime_mode: str,
    sensitivity: str,
    note: str,
) -> dict:
    return EmbodiedMemoryEvent(
        event_id=event_id,
        mission_id=mission_id,
        event_type="outcome",
        payload={"note": note},
        runtime_mode=runtime_mode,
        source_type="test",
        observed_at="2026-07-24T00:00:00+00:00",
        sensitivity=sensitivity,
    ).to_mission_record().to_dict()


def _scope(
    *,
    mission_id: str = "mission-current",
    runtime_mode: str = "real",
    sensitivities: tuple[str, ...] = ("standard",),
) -> MemoryRetrievalScope:
    return MemoryRetrievalScope(
        mission_ids=(mission_id,),
        runtime_modes=(runtime_mode,),
        allowed_sensitivities=sensitivities,
    )


def test_memory_retriever_requires_explicit_scope(tmp_path: Path) -> None:
    retriever = MemoryRetriever(SqliteMemoryIndex(tmp_path / "memory.sqlite"))
    try:
        retriever.retrieve("smoke")
    except TypeError:
        return
    raise AssertionError("retrieve() accepted an unscoped query")


def test_memory_retriever_filters_mission_runtime_and_sensitivity(tmp_path: Path) -> None:
    index = SqliteMemoryIndex(tmp_path / "memory.sqlite")
    for record in (
        _indexed_event(
            event_id="allowed",
            mission_id="mission-current",
            runtime_mode="real",
            sensitivity="standard",
            note="smoke route",
        ),
        _indexed_event(
            event_id="other-mission",
            mission_id="mission-old",
            runtime_mode="real",
            sensitivity="standard",
            note="smoke route",
        ),
        _indexed_event(
            event_id="simulation",
            mission_id="mission-current",
            runtime_mode="simulation",
            sensitivity="standard",
            note="smoke route",
        ),
        _indexed_event(
            event_id="restricted",
            mission_id="mission-current",
            runtime_mode="real",
            sensitivity="restricted",
            note="smoke route",
        ),
    ):
        index.upsert(record)

    results = MemoryRetriever(index).retrieve("smoke", scope=_scope(), limit=10)

    assert [result.record_id for result in results] == ["allowed"]


def test_memory_retriever_admits_restricted_when_scope_allows_it(tmp_path: Path) -> None:
    index = SqliteMemoryIndex(tmp_path / "memory.sqlite")
    index.upsert(_indexed_event(
        event_id="restricted",
        mission_id="mission-current",
        runtime_mode="real",
        sensitivity="restricted",
        note="smoke route",
    ))

    results = MemoryRetriever(index).retrieve(
        "smoke",
        scope=_scope(sensitivities=("standard", "restricted")),
    )

    assert [result.record_id for result in results] == ["restricted"]


# ---------------------------------------------------------------------------
# Lightweight test runner (no pytest dependency)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    _ALL_TESTS = [
        test_memory_retriever_requires_explicit_scope,
        test_memory_retriever_filters_mission_runtime_and_sensitivity,
        test_memory_retriever_admits_restricted_when_scope_allows_it,
    ]

    passed = 0
    failed = 0
    for fn in _ALL_TESTS:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                fn(Path(tmp))
                print(f"PASS: {fn.__name__}")
                passed += 1
            except Exception as exc:
                print(f"FAIL: {fn.__name__}: {exc}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed out of {len(_ALL_TESTS)}")
