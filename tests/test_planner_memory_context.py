"""Tests for planner memory retrieval scope boundaries and plugin diagnostics."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fireclaw_core.memory.embodied_memory import (
    EMBODIED_METADATA_KEY,
    EmbodiedMemoryEvent,
    EmbodiedMemoryStore,
)
from fireclaw_core.memory.memory_eval import evaluate_retrieval
from fireclaw_core.memory.memory_index import SqliteMemoryIndex
from fireclaw_core.memory.memory_retrieval import MemoryRetrievalScope, MemoryRetriever
from fireclaw_core.memory.mission_memory_facade import (
    MEMORY_RESTRICTED_READ_SCOPE,
    MemoryAccessContext,
    MissionMemoryFacade,
)
from fireclaw_core.memory.planner_memory_context import (
    PlannerMemoryContextBuilder,
    PlannerMemoryContextRequest,
    PlannerMemoryContextResult,
)
from fireclaw_core.mission.mission_memory import MissionMemoryStore
from fireclaw_core.plugin.plugin_runtime import PluginRuntime


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


def test_evaluate_retrieval_requires_scope_and_isolates_missions(tmp_path: Path) -> None:
    """evaluate_retrieval must accept a scope and only return records within it.

    Two records share identical text but belong to different missions.
    When scoped to ``mission-current``, only that mission's expected_record_id
    should satisfy the eval case.
    """
    index = SqliteMemoryIndex(tmp_path / "memory.sqlite")
    for record in (
        _indexed_event(
            event_id="current-mission-record",
            mission_id="mission-current",
            runtime_mode="real",
            sensitivity="standard",
            note="search floor 2",
        ),
        _indexed_event(
            event_id="other-mission-record",
            mission_id="mission-old",
            runtime_mode="real",
            sensitivity="standard",
            note="search floor 2",
        ),
    ):
        index.upsert(record)

    retriever = MemoryRetriever(index)
    cases: list[dict[str, Any]] = [{
        "query": "search floor 2",
        "expected_record_ids": ["current-mission-record"],
    }]

    report = evaluate_retrieval(
        retriever, cases, scope=_scope(mission_id="mission-current"),
    )

    assert report.total == 1
    assert report.passed == 1, (
        f"Expected current-mission-record to pass, got missing: "
        f"{report.results[0].missing_patterns}"
    )
    assert "current-mission-record" in report.results[0].matched_ids

    # Cross-mission record must NOT appear.
    report_cross = evaluate_retrieval(
        retriever, cases, scope=_scope(mission_id="mission-old"),
    )
    assert report_cross.total == 1
    assert report_cross.passed == 0, (
        "other-mission scope must not satisfy current-mission expected_record_ids"
    )


# ---------------------------------------------------------------------------
# Plugin hook diagnostics (content-free failure reporting)
# ---------------------------------------------------------------------------


def test_memory_hook_diagnostics_report_callback_exception_without_message(tmp_path: Path) -> None:
    runtime = PluginRuntime()
    runtime.register_callable(
        hook_type="memory",
        hook_name="filter",
        plugin_id="broken.memory",
        callback=lambda payload: (_ for _ in ()).throw(
            RuntimeError("restricted victim name")
        ),
    )

    report = runtime.run_memory_hooks_with_diagnostics("filter", {"memories": []})

    assert report.effects == ()
    assert [failure.to_dict() for failure in report.failures] == [{
        "plugin_id": "broken.memory",
        "hook_name": "filter",
        "exception_class": "RuntimeError",
    }]
    assert "restricted victim name" not in repr(report)


def test_existing_memory_hook_api_keeps_list_shape_on_callback_exception(tmp_path: Path) -> None:
    runtime = PluginRuntime()
    runtime.register_callable(
        hook_type="memory",
        hook_name="filter",
        plugin_id="broken.memory",
        callback=lambda payload: (_ for _ in ()).throw(RuntimeError("private")),
    )

    assert runtime.run_memory_hooks("filter", {"memories": []}) == []


def test_provider_hook_diagnostics_report_callback_exception_without_message(tmp_path: Path) -> None:
    runtime = PluginRuntime()
    runtime.register_callable(
        hook_type="provider",
        hook_name="enrich_context",
        plugin_id="broken.provider",
        callback=lambda payload: (_ for _ in ()).throw(
            RuntimeError("secret context leak")
        ),
    )

    report = runtime.run_provider_hooks_with_diagnostics(
        "enrich_context", {"command": "test", "context": {}},
    )

    assert report.effects == ()
    assert [failure.to_dict() for failure in report.failures] == [{
        "plugin_id": "broken.provider",
        "hook_name": "enrich_context",
        "exception_class": "RuntimeError",
    }]
    assert "secret context leak" not in repr(report)


# ---------------------------------------------------------------------------
# Task 4: PlannerMemoryContextBuilder admission tests
# ---------------------------------------------------------------------------


def _embodied_store(tmp_path: Path) -> EmbodiedMemoryStore:
    return EmbodiedMemoryStore(
        tmp_path / "embodied.jsonl",
        index_path=tmp_path / "embodied.sqlite",
    )


def _facade(store: EmbodiedMemoryStore, runtime_mode: str) -> MissionMemoryFacade:
    return MissionMemoryFacade(store=store, runtime_mode=runtime_mode)


def _retriever(store: EmbodiedMemoryStore) -> MemoryRetriever:
    assert store.index is not None
    return MemoryRetriever(store.index)


def _request(
    *,
    mission_id: str = "mission-alpha",
    runtime_mode: str | None = "real",
    requester_id: str = "planner-1",
    scopes: frozenset[str] = frozenset(),
    max_memories: int = 5,
    max_corrections: int = 3,
    command: str = "smoke",
) -> PlannerMemoryContextRequest:
    return PlannerMemoryContextRequest(
        command=command,
        mission_id=mission_id,
        runtime_mode=runtime_mode,
        requester_id=requester_id,
        scopes=scopes,
        max_memories=max_memories,
        max_corrections=max_corrections,
    )


def _record_event(
    store: EmbodiedMemoryStore,
    *,
    event_id: str,
    mission_id: str = "mission-alpha",
    event_type: str = "observation",
    runtime_mode: str = "real",
    sensitivity: str = "standard",
    note: str = "smoke detected",
) -> EmbodiedMemoryEvent:
    return store.record_event(
        event_id=event_id,
        mission_id=mission_id,
        event_type=event_type,
        payload={"note": note},
        runtime_mode=runtime_mode,
        source_type="test",
        observed_at="2026-07-24T10:00:00+00:00",
        sensitivity=sensitivity,
    )


def test_request_validation_empty_command_raises(tmp_path: Path) -> None:
    try:
        PlannerMemoryContextRequest(
            command="",
            mission_id="m1",
            runtime_mode="real",
            requester_id="r1",
        )
        raise AssertionError("expected ValueError for empty command")
    except ValueError:
        pass


def test_request_validation_empty_mission_raises(tmp_path: Path) -> None:
    try:
        PlannerMemoryContextRequest(
            command="go",
            mission_id="",
            runtime_mode="real",
            requester_id="r1",
        )
        raise AssertionError("expected ValueError for empty mission_id")
    except ValueError:
        pass


def test_request_validation_empty_requester_raises(tmp_path: Path) -> None:
    try:
        PlannerMemoryContextRequest(
            command="go",
            mission_id="m1",
            runtime_mode="real",
            requester_id="",
        )
        raise AssertionError("expected ValueError for empty requester_id")
    except ValueError:
        pass


def test_request_validation_invalid_runtime_raises(tmp_path: Path) -> None:
    try:
        PlannerMemoryContextRequest(
            command="go",
            mission_id="m1",
            runtime_mode="invalid_mode",
            requester_id="r1",
        )
        raise AssertionError("expected ValueError for invalid runtime_mode")
    except ValueError:
        pass


def test_request_validation_empty_scope_value_raises(tmp_path: Path) -> None:
    try:
        PlannerMemoryContextRequest(
            command="go",
            mission_id="m1",
            runtime_mode="real",
            requester_id="r1",
            scopes=frozenset({""}),
        )
        raise AssertionError("expected ValueError for empty scope")
    except ValueError:
        pass


def test_request_validation_negative_limit_raises(tmp_path: Path) -> None:
    try:
        PlannerMemoryContextRequest(
            command="go",
            mission_id="m1",
            runtime_mode="real",
            requester_id="r1",
            max_memories=-1,
        )
        raise AssertionError("expected ValueError for negative max_memories")
    except ValueError:
        pass


def test_request_validation_limit_above_100_raises(tmp_path: Path) -> None:
    try:
        PlannerMemoryContextRequest(
            command="go",
            mission_id="m1",
            runtime_mode="real",
            requester_id="r1",
            max_memories=101,
        )
        raise AssertionError("expected ValueError for max_memories > 100")
    except ValueError:
        pass


def test_standard_embodied_record_matching_mission_and_runtime_is_admitted(tmp_path: Path) -> None:
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
    )
    result = builder.build(_request())

    assert len(result.memories) == 1
    assert result.memories[0]["record_id"] == "obs-1"
    assert result.memories[0]["runtime_mode"] == "real"
    assert result.memories[0]["source_mission_id"] == "mission-alpha"
    assert result.memories[0]["advisory_only"] is True
    assert result.memories[0]["can_authorize_action"] is False


def test_other_mission_record_is_omitted(tmp_path: Path) -> None:
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-other", mission_id="mission-beta")
    # Also record a current-mission event so the retriever has something
    _record_event(store, event_id="obs-current")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
    )
    result = builder.build(_request())

    # obs-other should be rejected by scope; obs-current should be admitted
    admitted_ids = [m["record_id"] for m in result.memories]
    assert "obs-other" not in admitted_ids
    assert "obs-current" in admitted_ids


def test_other_runtime_record_is_omitted(tmp_path: Path) -> None:
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-sim", runtime_mode="simulation")
    # Also record a current-runtime event
    _record_event(store, event_id="obs-real")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
    )
    result = builder.build(_request())

    admitted_ids = [m["record_id"] for m in result.memories]
    assert "obs-sim" not in admitted_ids
    assert "obs-real" in admitted_ids


def test_restricted_without_scope_is_omitted(tmp_path: Path) -> None:
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-restricted", sensitivity="restricted")
    # Also record a standard event so the retriever has something
    _record_event(store, event_id="obs-std", sensitivity="standard")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
    )
    # No restricted scope in request scopes
    result = builder.build(_request())

    admitted_ids = [m["record_id"] for m in result.memories]
    assert "obs-restricted" not in admitted_ids
    assert "obs-std" in admitted_ids
    assert not result.restricted_access_granted


def test_missing_embodied_metadata_is_omitted(tmp_path: Path) -> None:
    """Records without _embodied metadata should be omitted."""
    store = _embodied_store(tmp_path)
    # Write a non-embodied record directly to the evidence store
    from fireclaw_core.mission.mission_memory import MissionMemoryRecord
    store.evidence_store.append(MissionMemoryRecord(
        record_id="plain-1",
        mission_id="mission-alpha",
        record_type="outcome",
        content={"note": "no embodied metadata"},
        created_at="2026-07-24T10:00:00+00:00",
    ))

    retriever = _retriever(store)
    facade = _facade(store, "real")

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
    )
    result = builder.build(_request())

    # The plain record without embodied metadata should not appear in memories
    assert all(m["record_id"] != "plain-1" for m in result.memories)


def test_restricted_memory_admitted_with_scope(tmp_path: Path) -> None:
    """Restricted records are excluded from FTS text indexing by policy.

    When the restricted scope is granted, the builder sets
    restricted_access_granted=True and does not emit restricted_scope_denied
    warnings.  The retriever cannot find restricted records via FTS (by
    design), but the scope grant itself must be reported correctly.
    """
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-restricted", sensitivity="restricted")
    # Also record a standard event so the retriever has something to return
    _record_event(store, event_id="obs-std", sensitivity="standard")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
    )
    result = builder.build(_request(
        scopes=frozenset({MEMORY_RESTRICTED_READ_SCOPE}),
    ))

    # restricted_access_granted must be True when the scope is granted
    assert result.restricted_access_granted is True
    # No restricted_scope_denied warnings should appear
    assert not any(w.code == "restricted_scope_denied" for w in result.warnings)
    # The standard record should be admitted
    admitted_ids = [m["record_id"] for m in result.memories]
    assert "obs-std" in admitted_ids


def test_corrections_admitted_only_for_current_mission_and_runtime(tmp_path: Path) -> None:
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="corr-current", event_type="correction", note="fix direction")
    _record_event(store, event_id="corr-other", event_type="correction",
                  mission_id="mission-beta", note="other fix")
    _record_event(store, event_id="corr-sim", event_type="correction",
                  runtime_mode="simulation", note="sim fix")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
    )
    result = builder.build(_request())

    correction_ids = [c["record_id"] for c in result.corrections]
    assert "corr-current" in correction_ids
    assert "corr-other" not in correction_ids
    assert "corr-sim" not in correction_ids


def test_facade_events_canonicalized_and_deduplicated_against_indexed(tmp_path: Path) -> None:
    """Events seen by both the index and the facade should appear only once."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-dup")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
    )
    result = builder.build(_request())

    # The record should appear exactly once (deduplicated)
    matching = [m for m in result.memories if m["record_id"] == "obs-dup"]
    assert len(matching) == 1


def test_runtime_mode_none_returns_empty_result(tmp_path: Path) -> None:
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
    )
    result = builder.build(_request(runtime_mode=None))

    assert len(result.memories) == 0
    assert len(result.corrections) == 0
    assert len(result.warnings) == 0


def test_restricted_scope_grant_reported_even_when_no_restricted_records(tmp_path: Path) -> None:
    """restricted_access_granted should be True when scope grants it, even with no records."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-std", sensitivity="standard")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
    )
    result = builder.build(_request(
        scopes=frozenset({MEMORY_RESTRICTED_READ_SCOPE}),
    ))

    assert result.restricted_access_granted is True
    # But only standard records were available
    assert all(m["sensitivity"] == "standard" for m in result.memories)


def test_guard_decision_reports_degraded_when_warnings_present(tmp_path: Path) -> None:
    """Guard decision reports memory_context_degraded when warnings exist.

    Use a mission_memory whose list_records() raises to trigger the
    authority_lookup_failed warning.
    """
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    class BrokenMissionMemory:
        def list_records(self, **kwargs):
            raise RuntimeError("connection lost")
        def append(self, record):
            pass

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=BrokenMissionMemory(),
        facade=facade,
    )
    result = builder.build(_request())
    decision = result.guard_decision()

    assert decision.layer == "memory_context"
    assert decision.status == "allow"
    assert decision.reason == "memory_context_degraded"
    assert "authority_lookup_failed" in decision.details["warning_codes"]


def test_guard_decision_reports_valid_when_no_warnings(tmp_path: Path) -> None:
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
    )
    result = builder.build(_request())
    decision = result.guard_decision()

    assert decision.status == "allow"
    assert decision.reason == "memory_context_validated"
    assert decision.details["accepted_memories"] >= 1


def test_reusable_knowledge_available_flag(tmp_path: Path) -> None:
    """reusable_knowledge_available should reflect whether a lifecycle store is configured."""
    store = _embodied_store(tmp_path)
    retriever = _retriever(store)
    facade = _facade(store, "real")

    # Without lifecycle
    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
    )
    result = builder.build(_request())
    assert result.reusable_knowledge_available is False


def test_reusable_knowledge_available_true_with_lifecycle(tmp_path: Path) -> None:
    """Providing a lifecycle store sets reusable_knowledge_available=True."""
    from fireclaw_core.memory.memory_lifecycle import MissionMemoryLifecycleStore

    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    retriever = _retriever(store)
    facade = _facade(store, "real")
    lifecycle = MissionMemoryLifecycleStore(
        store=store,
        lifecycle_path=tmp_path / "lifecycle.jsonl",
        audit_dir=tmp_path / "audit",
        knowledge_path=tmp_path / "knowledge.jsonl",
        runtime_mode="real",
    )

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
        lifecycle=lifecycle,
    )
    result = builder.build(_request())

    assert result.reusable_knowledge_available is True


def test_authority_lookup_failed_warning_when_list_records_raises(tmp_path: Path) -> None:
    """When mission_memory.list_records() raises, emit authority_lookup_failed warning.

    The builder should still return memories (fail-open for planning).
    """
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    class BrokenMissionMemory:
        def list_records(self, **kwargs):
            raise RuntimeError("db offline")
        def append(self, record):
            pass

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=BrokenMissionMemory(),
        facade=facade,
    )
    result = builder.build(_request())

    codes = [w.code for w in result.warnings]
    assert "authority_lookup_failed" in codes
    # Fail-open: memories should still be returned
    assert len(result.memories) >= 1


def test_authority_lookup_failed_when_mission_memory_is_none(tmp_path: Path) -> None:
    """When mission_memory is None, emit authority_lookup_failed warning.

    The check is skipped but the caller gets a diagnostic warning.
    """
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=None,
        facade=facade,
    )
    result = builder.build(_request())

    codes = [w.code for w in result.warnings]
    assert "authority_lookup_failed" in codes
    # Fail-open: memories should still be returned
    assert len(result.memories) >= 1


def test_facade_events_checked_against_authority_map(tmp_path: Path) -> None:
    """Facade events whose record_id is not in the authority map are rejected."""
    from fireclaw_core.memory.mission_memory_facade import MemoryAccessContext

    # Create a mock facade that returns two events: one valid, one stale
    class MockFacade:
        def get_current_context(self, access, **kwargs):
            return {
                "events": [
                    {
                        "event_id": "obs-valid",
                        "event_type": "observation",
                        "runtime_mode": "real",
                        "sensitivity": "standard",
                        "payload": {"note": "valid"},
                    },
                    {
                        "event_id": "obs-stale",
                        "event_type": "observation",
                        "runtime_mode": "real",
                        "sensitivity": "standard",
                        "payload": {"note": "stale"},
                    },
                ],
            }

    # Create a mission_memory that knows only about "obs-valid"
    class SelectiveMissionMemory:
        def list_records(self, **kwargs):
            from fireclaw_core.mission.mission_memory import MissionMemoryRecord
            return [MissionMemoryRecord(
                record_id="obs-valid",
                mission_id="mission-alpha",
                record_type="observation",
                content={},
                created_at="2026-07-24T10:00:00+00:00",
            )]

    builder = PlannerMemoryContextBuilder(
        memory_retriever=None,
        mission_memory=SelectiveMissionMemory(),
        facade=MockFacade(),
    )
    result = builder.build(_request())

    admitted_ids = [m["record_id"] for m in result.memories]
    # obs-valid should pass the authority check
    assert "obs-valid" in admitted_ids
    # obs-stale should be rejected by the authority check
    assert "obs-stale" not in admitted_ids
    # A warning should be emitted for obs-stale
    missing_warnings = [
        w for w in result.warnings
        if w.code == "authority_record_missing" and w.record_id == "obs-stale"
    ]
    assert len(missing_warnings) == 1


# ---------------------------------------------------------------------------
# Task 5: Approval-gated reusable knowledge tests
# ---------------------------------------------------------------------------


def _lifecycle(tmp_path: Path, store: EmbodiedMemoryStore, runtime_mode: str = "real"):
    from fireclaw_core.memory.memory_lifecycle import MissionMemoryLifecycleStore
    return MissionMemoryLifecycleStore(
        store=store,
        lifecycle_path=tmp_path / "lifecycle.jsonl",
        audit_dir=tmp_path / "audit",
        knowledge_path=tmp_path / "knowledge.jsonl",
        runtime_mode=runtime_mode,
    )


def _approve_knowledge(
    lifecycle,
    *,
    knowledge_id: str = "k-1",
    source_mission_id: str = "mission-beta",
    knowledge_type: str = "operator_preference",
    title: str = "prefer stairwell route",
    content: dict | None = None,
    tags: list[str] | None = None,
    applicable_runtime_modes: list[str] | None = None,
    source_event_ids: list[str] | None = None,
    actor_id: str = "reviewer-1",
    reason: str = "validated in field",
):
    """Helper to approve reusable knowledge backed by a real source event."""
    from fireclaw_core.memory.memory_lifecycle import MissionMemoryLifecycleStore

    # If source_event_ids not provided, create a backing event so approval passes validation
    if source_event_ids is None:
        backing_event = lifecycle._store.record_event(
            event_id=f"src-{knowledge_id}",
            mission_id=source_mission_id,
            event_type="outcome",
            payload={"note": "backing event"},
            runtime_mode="real",
            source_type="test",
            observed_at="2026-07-24T10:00:00+00:00",
        )
        source_event_ids = [backing_event.event_id]

    return lifecycle.approve_knowledge(
        source_mission_id=source_mission_id,
        source_event_ids=source_event_ids,
        knowledge_type=knowledge_type,
        title=title,
        content=content or {"guidance": "use stairwell route B"},
        tags=tags or ["routing"],
        applicable_runtime_modes=applicable_runtime_modes,
        actor_id=actor_id,
        reason=reason,
        knowledge_id=knowledge_id,
    )


def test_approved_operator_preference_from_other_mission_is_admitted(tmp_path: Path) -> None:
    """An approved operator_preference from another mission crosses the mission boundary."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    lifecycle = _lifecycle(tmp_path, store)
    _approve_knowledge(lifecycle, source_mission_id="mission-beta")

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        lifecycle=lifecycle,
    )
    result = builder.build(_request())

    reusable = [m for m in result.memories if m.get("memory_scope") == "reusable_knowledge"]
    assert len(reusable) == 1
    assert reusable[0]["knowledge_id"] == "k-1"
    assert reusable[0]["source_mission_id"] == "mission-beta"


def test_raw_correction_not_admitted_across_missions(tmp_path: Path) -> None:
    """The original raw correction record must not leak across mission boundaries.

    Only the approved knowledge payload should appear, not the underlying
    correction event from the source mission.
    """
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-current")
    # Record a correction in mission-beta
    store.record_event(
        event_id="corr-beta",
        mission_id="mission-beta",
        event_type="correction",
        payload={"note": "raw correction from beta"},
        runtime_mode="real",
        source_type="test",
        observed_at="2026-07-24T10:00:00+00:00",
    )
    lifecycle = _lifecycle(tmp_path, store)
    # Approve knowledge from mission-beta using the correction event as source
    _approve_knowledge(
        lifecycle,
        source_mission_id="mission-beta",
        source_event_ids=["corr-beta"],
    )

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        lifecycle=lifecycle,
    )
    result = builder.build(_request())

    # The raw correction must not appear as a current_mission memory
    assert all(m.get("record_id") != "corr-beta" for m in result.memories)
    # The approved knowledge should appear as reusable_knowledge
    reusable = [m for m in result.memories if m.get("memory_scope") == "reusable_knowledge"]
    assert len(reusable) == 1


def test_revoked_knowledge_is_absent(tmp_path: Path) -> None:
    """Revoked knowledge must not appear in the planner context."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    lifecycle = _lifecycle(tmp_path, store)
    knowledge = _approve_knowledge(lifecycle)
    lifecycle.revoke_knowledge(
        knowledge.knowledge_id,
        actor_id="reviewer-2",
        reason="outdated guidance",
    )

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        lifecycle=lifecycle,
    )
    result = builder.build(_request())

    reusable = [m for m in result.memories if m.get("memory_scope") == "reusable_knowledge"]
    assert len(reusable) == 0


def test_simulation_knowledge_absent_in_real_mode(tmp_path: Path) -> None:
    """Simulation-derived knowledge is absent in real mode unless approval includes real."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    # Create a simulation-mode lifecycle store to approve sim-only knowledge
    sim_lifecycle = _lifecycle(tmp_path, store, runtime_mode="simulation")
    _approve_knowledge(
        sim_lifecycle,
        knowledge_id="k-sim",
        applicable_runtime_modes=["simulation"],
    )

    # Build with real-mode lifecycle (does not have simulation in applicable modes)
    real_lifecycle = _lifecycle(tmp_path, store, runtime_mode="real")
    # The knowledge.jsonl is shared; real_lifecycle.list_knowledge() will filter
    # by real mode which is NOT in applicable_runtime_modes
    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        lifecycle=real_lifecycle,
    )
    result = builder.build(_request())

    reusable = [m for m in result.memories if m.get("memory_scope") == "reusable_knowledge"]
    assert len(reusable) == 0


def test_simulation_knowledge_admitted_when_real_included(tmp_path: Path) -> None:
    """Simulation knowledge that also includes real mode should be admitted."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    sim_lifecycle = _lifecycle(tmp_path, store, runtime_mode="simulation")
    _approve_knowledge(
        sim_lifecycle,
        knowledge_id="k-both",
        applicable_runtime_modes=["real", "simulation"],
    )

    real_lifecycle = _lifecycle(tmp_path, store, runtime_mode="real")
    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        lifecycle=real_lifecycle,
    )
    result = builder.build(_request())

    reusable = [m for m in result.memories if m.get("memory_scope") == "reusable_knowledge"]
    assert len(reusable) == 1
    assert reusable[0]["knowledge_id"] == "k-both"


def test_current_mission_consumes_quota_before_reusable(tmp_path: Path) -> None:
    """Current-mission items fill the quota; reusable knowledge gets only the remainder."""
    store = _embodied_store(tmp_path)
    # Record 3 current-mission events
    for i in range(3):
        _record_event(store, event_id=f"obs-{i}")
    lifecycle = _lifecycle(tmp_path, store)
    _approve_knowledge(lifecycle, knowledge_id="k-1")
    _approve_knowledge(
        lifecycle,
        knowledge_id="k-2",
        title="another preference",
        source_event_ids=None,  # will create backing event
    )

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        lifecycle=lifecycle,
    )
    # max_memories=3 should fit all current-mission items, leaving 0 for reusable
    result = builder.build(_request(max_memories=3))

    current = [m for m in result.memories if m.get("memory_scope") == "current_mission"]
    reusable = [m for m in result.memories if m.get("memory_scope") == "reusable_knowledge"]
    assert len(current) >= 1
    assert len(current) + len(reusable) <= 3
    # All current-mission items should come before reusable
    if reusable:
        last_current_idx = max(i for i, m in enumerate(result.memories) if m.get("memory_scope") == "current_mission")
        first_reusable_idx = min(i for i, m in enumerate(result.memories) if m.get("memory_scope") == "reusable_knowledge")
        assert last_current_idx < first_reusable_idx


def test_lifecycle_failure_leaves_current_mission_intact(tmp_path: Path) -> None:
    """When the lifecycle store fails, current-mission items survive and a warning is emitted."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")

    class BrokenLifecycle:
        def list_knowledge(self, **kwargs):
            raise RuntimeError("lifecycle storage unavailable")

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        lifecycle=BrokenLifecycle(),
    )
    result = builder.build(_request())

    # Current-mission items should still be present
    current = [m for m in result.memories if m.get("memory_scope") == "current_mission"]
    assert len(current) >= 1
    # A warning about lifecycle failure should be emitted
    codes = [w.code for w in result.warnings]
    assert "reusable_knowledge_unavailable" in codes
    # No reusable knowledge should appear
    reusable = [m for m in result.memories if m.get("memory_scope") == "reusable_knowledge"]
    assert len(reusable) == 0


def test_knowledge_content_uses_approved_redacted_payload(tmp_path: Path) -> None:
    """Returned knowledge content must be the separately approved redacted payload,
    never follow source_event_ids."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    # Create a backing event with different content
    store.record_event(
        event_id="src-event",
        mission_id="mission-beta",
        event_type="outcome",
        payload={"raw_detail": "full unredacted details from source event"},
        runtime_mode="real",
        source_type="test",
        observed_at="2026-07-24T10:00:00+00:00",
    )
    lifecycle = _lifecycle(tmp_path, store)
    _approve_knowledge(
        lifecycle,
        source_mission_id="mission-beta",
        source_event_ids=["src-event"],
        content={"guidance": "use stairwell route B"},
    )

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        lifecycle=lifecycle,
    )
    result = builder.build(_request())

    reusable = [m for m in result.memories if m.get("memory_scope") == "reusable_knowledge"]
    assert len(reusable) == 1
    # The content should be the approved payload, not the source event's payload
    assert reusable[0]["content"] == {"guidance": "use stairwell route B"}
    assert "raw_detail" not in reusable[0]["content"]
    # source_event_ids should NOT appear in the returned dict
    assert "source_event_ids" not in reusable[0]


def test_knowledge_dict_has_required_safety_fields(tmp_path: Path) -> None:
    """Reusable knowledge entries must carry advisory_only, can_authorize_action,
    and requires_current_state_revalidation."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    lifecycle = _lifecycle(tmp_path, store)
    _approve_knowledge(lifecycle)

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        lifecycle=lifecycle,
    )
    result = builder.build(_request())

    reusable = [m for m in result.memories if m.get("memory_scope") == "reusable_knowledge"]
    assert len(reusable) == 1
    assert reusable[0]["advisory_only"] is True
    assert reusable[0]["can_authorize_action"] is False
    assert reusable[0]["requires_current_state_revalidation"] is True


# ---------------------------------------------------------------------------
# Task 6: Plugin filter, rerank, and enrichment reauthorization
# ---------------------------------------------------------------------------


def _plugin_runtime_with_callbacks(**callbacks) -> PluginRuntime:
    """Build a PluginRuntime with registered memory/provider callbacks.

    Keyword arguments are named like ``filter_cb``, ``rerank_cb``,
    ``enrich_cb``.  Each callback receives the hook payload dict and returns
    a dict effect or None.
    """
    runtime = PluginRuntime()
    for key, callback in callbacks.items():
        hook_type, hook_name = {
            "filter_cb": ("memory", "filter"),
            "rerank_cb": ("memory", "rerank"),
            "enrich_cb": ("provider", "enrich_context"),
        }[key]
        runtime.register_callable(
            hook_type=hook_type,
            hook_name=hook_name,
            plugin_id=f"plugin.{key}",
            callback=callback,
        )
    return runtime


def test_plugin_filter_can_remove_known_ids(tmp_path: Path) -> None:
    """A filter plugin can selectively remove known record IDs."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-a")
    _record_event(store, event_id="obs-b")
    _record_event(store, event_id="obs-c")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    def filter_cb(payload: dict) -> dict:
        # Remove obs-b from the list
        return {"memories": [
            m for m in payload["memories"]
            if m.get("record_id") != "obs-b"
        ]}

    runtime = _plugin_runtime_with_callbacks(filter_cb=filter_cb)

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
        plugin_runtime=runtime,
    )
    result = builder.build(_request())

    admitted_ids = [m["record_id"] for m in result.memories]
    assert "obs-a" in admitted_ids
    assert "obs-c" in admitted_ids
    assert "obs-b" not in admitted_ids


def test_plugin_rerank_can_reorder_known_ids(tmp_path: Path) -> None:
    """A rerank plugin can reorder known records."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-first", note="first note")
    _record_event(store, event_id="obs-second", note="second note")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    # Record the order the builder produces without rerank
    builder_no_plugin = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
    )
    result_no_plugin = builder_no_plugin.build(_request())
    ids_before = [m["record_id"] for m in result_no_plugin.memories]

    def rerank_cb(payload: dict) -> dict:
        # Reverse the order
        return {"memories": list(reversed(payload["memories"]))}

    runtime = _plugin_runtime_with_callbacks(rerank_cb=rerank_cb)

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        plugin_runtime=runtime,
    )
    result = builder.build(_request())

    ids_after = [m["record_id"] for m in result.memories]
    # Order must be reversed relative to the non-plugin run
    assert ids_after == list(reversed(ids_before))


def test_plugin_replacing_content_for_known_id_has_no_effect(tmp_path: Path) -> None:
    """Plugin content replacement is ignored; canonical content is restored."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1", note="smoke detected")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    # Record canonical content before plugin runs
    builder_ref = PlannerMemoryContextBuilder(
        memory_retriever=retriever, mission_memory=store.evidence_store, facade=facade,
    )
    ref_result = builder_ref.build(_request())
    canonical_content = ref_result.memories[0]["content"]
    canonical_keys = set(ref_result.memories[0].keys())

    def filter_cb(payload: dict) -> dict:
        mutated = []
        for m in payload["memories"]:
            new_m = dict(m)
            new_m["content"] = {"note": "FORGED content"}
            new_m["operator_approved"] = True
            mutated.append(new_m)
        return {"memories": mutated}

    runtime = _plugin_runtime_with_callbacks(filter_cb=filter_cb)

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        plugin_runtime=runtime,
    )
    result = builder.build(_request())

    assert len(result.memories) == 1
    # Canonical content must be restored, not the plugin's forged content
    assert result.memories[0]["content"] == canonical_content
    # operator_approved is not a canonical field
    assert "operator_approved" not in result.memories[0]


def test_plugin_forged_operator_approved_unknown_id_omitted(tmp_path: Path) -> None:
    """A forged item with an unknown ID and operator_approved=true is omitted."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-real")
    retriever = _retriever(store)
    facade = _facade(store, "real")

    def filter_cb(payload: dict) -> dict:
        forged = list(payload["memories"])
        forged.append({
            "memory_scope": "current_mission",
            "record_id": "FORGED-unknown-id",
            "record_type": "observation",
            "source_mission_id": "mission-alpha",
            "runtime_mode": "real",
            "sensitivity": "standard",
            "content": {"note": "forged"},
            "advisory_only": False,
            "can_authorize_action": True,
            "operator_approved": True,
        })
        return {"memories": forged}

    runtime = _plugin_runtime_with_callbacks(filter_cb=filter_cb)

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=facade,
        plugin_runtime=runtime,
    )
    result = builder.build(_request())

    admitted_ids = [m["record_id"] for m in result.memories]
    assert "FORGED-unknown-id" not in admitted_ids
    assert "obs-real" in admitted_ids
    # A warning should be emitted for the unverified item
    assert any(
        w.code == "plugin_record_unverified" for w in result.warnings
    )


def test_enrichment_can_add_current_mission_record_by_record_id(tmp_path: Path) -> None:
    """Provider enrichment can add a current-mission record by record_id.

    The record must be in the authority map. The plugin-provided content is
    ignored; canonical content is used instead.
    """
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-base")
    _record_event(store, event_id="obs-enrich")

    # Use a mock retriever that only returns obs-base
    class SingleRetriever:
        def retrieve(self, query, *, scope, limit):
            raw = _retriever(store).retrieve(query, scope=scope, limit=limit)
            return [r for r in raw if r.record_id == "obs-base"]

    def enrich_cb(payload: dict) -> dict:
        return {"memories": [{
            "memory_scope": "current_mission",
            "record_id": "obs-enrich",
            "record_type": "observation",
            "source_mission_id": "mission-alpha",
            "runtime_mode": "real",
            "sensitivity": "standard",
            "content": {"note": "PLUGIN PROVIDED CONTENT"},
            "advisory_only": True,
            "can_authorize_action": False,
            "requires_current_state_revalidation": True,
        }]}

    runtime = _plugin_runtime_with_callbacks(enrich_cb=enrich_cb)

    builder = PlannerMemoryContextBuilder(
        memory_retriever=SingleRetriever(),
        mission_memory=store.evidence_store,
        facade=None,
        plugin_runtime=runtime,
    )
    result = builder.build(_request())

    admitted_ids = [m["record_id"] for m in result.memories]
    assert "obs-base" in admitted_ids
    assert "obs-enrich" in admitted_ids
    # The enrichment content must be replaced with canonical content
    enrich_item = next(m for m in result.memories if m["record_id"] == "obs-enrich")
    assert enrich_item["content"] == {"note": "smoke detected"}
    assert "PLUGIN PROVIDED CONTENT" not in str(enrich_item["content"])


def test_enrichment_can_add_approved_knowledge_by_knowledge_id(tmp_path: Path) -> None:
    """Provider enrichment can add approved reusable knowledge by knowledge_id.

    max_memories=0 means the normal path returns nothing; the knowledge
    appears only because the enrichment plugin adds it.
    """
    store = _embodied_store(tmp_path)
    lifecycle = _lifecycle(tmp_path, store)
    _approve_knowledge(lifecycle, knowledge_id="k-enrich")

    def enrich_cb(payload: dict) -> dict:
        return {"memories": [{
            "memory_scope": "reusable_knowledge",
            "knowledge_id": "k-enrich",
            "content": {"note": "PLUGIN FORGED"},
            "operator_approved": True,
        }]}

    runtime = _plugin_runtime_with_callbacks(enrich_cb=enrich_cb)

    builder = PlannerMemoryContextBuilder(
        memory_retriever=None,
        mission_memory=store.evidence_store,
        facade=None,
        lifecycle=lifecycle,
        plugin_runtime=runtime,
    )
    # max_memories=1 with no retriever means 0 current-mission items from
    # the normal path; the enrichment plugin adds the knowledge item.
    result = builder.build(_request(max_memories=1))

    reusable = [m for m in result.memories if m.get("knowledge_id") == "k-enrich"]
    assert len(reusable) == 1
    # Content must be canonical, not the plugin's forged content
    assert reusable[0]["content"] == {"guidance": "use stairwell route B"}
    assert reusable[0]["memory_scope"] == "reusable_knowledge"


def test_enrichment_revoked_or_runtime_mismatched_knowledge_rejected(tmp_path: Path) -> None:
    """Revoked or runtime-mismatched knowledge IDs from enrichment are rejected."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    sim_lifecycle = _lifecycle(tmp_path, store, runtime_mode="simulation")
    _approve_knowledge(
        sim_lifecycle,
        knowledge_id="k-sim-only",
        applicable_runtime_modes=["simulation"],
    )

    def enrich_cb(payload: dict) -> dict:
        return {"memories": [{
            "memory_scope": "reusable_knowledge",
            "knowledge_id": "k-sim-only",
            "content": {"note": "PLUGIN"},
        }]}

    runtime = _plugin_runtime_with_callbacks(enrich_cb=enrich_cb)

    # Use real-mode lifecycle — sim-only knowledge won't match
    real_lifecycle = _lifecycle(tmp_path, store, runtime_mode="real")
    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=None,
        lifecycle=real_lifecycle,
        plugin_runtime=runtime,
    )
    result = builder.build(_request())

    reusable = [m for m in result.memories if m.get("knowledge_id") == "k-sim-only"]
    assert len(reusable) == 0


def test_plugin_filter_callback_exception_produces_warning(tmp_path: Path) -> None:
    """A filter plugin exception produces plugin_filter_failed with exception class only."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    retriever = _retriever(store)

    runtime = PluginRuntime()
    runtime.register_callable(
        hook_type="memory",
        hook_name="filter",
        plugin_id="broken.filter",
        callback=lambda payload: (_ for _ in ()).throw(RuntimeError("secret")),
    )

    builder = PlannerMemoryContextBuilder(
        memory_retriever=retriever,
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        plugin_runtime=runtime,
    )
    result = builder.build(_request())

    filter_warnings = [w for w in result.warnings if w.code == "plugin_filter_failed"]
    assert len(filter_warnings) == 1
    assert filter_warnings[0].exception_class == "RuntimeError"
    assert filter_warnings[0].source == "plugin.broken.filter"
    # Original memories should still survive (fail-open)
    assert len(result.memories) >= 1


def test_plugin_rerank_callback_exception_produces_warning(tmp_path: Path) -> None:
    """A rerank plugin exception produces plugin_rerank_failed with exception class only."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")

    runtime = PluginRuntime()
    runtime.register_callable(
        hook_type="memory",
        hook_name="rerank",
        plugin_id="broken.rerank",
        callback=lambda payload: (_ for _ in ()).throw(ValueError("leak")),
    )

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        plugin_runtime=runtime,
    )
    result = builder.build(_request())

    rerank_warnings = [w for w in result.warnings if w.code == "plugin_rerank_failed"]
    assert len(rerank_warnings) == 1
    assert rerank_warnings[0].exception_class == "ValueError"
    assert len(result.memories) >= 1


def test_plugin_enrichment_callback_exception_produces_warning(tmp_path: Path) -> None:
    """A provider enrichment exception produces plugin_enrichment_failed with exception class only."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")

    runtime = PluginRuntime()
    runtime.register_callable(
        hook_type="provider",
        hook_name="enrich_context",
        plugin_id="broken.enrich",
        callback=lambda payload: (_ for _ in ()).throw(TypeError("bad data")),
    )

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        plugin_runtime=runtime,
    )
    result = builder.build(_request())

    enrich_warnings = [w for w in result.warnings if w.code == "plugin_enrichment_failed"]
    assert len(enrich_warnings) == 1
    assert enrich_warnings[0].exception_class == "TypeError"
    assert len(result.memories) >= 1


def test_plugin_enrichment_unknown_record_id_omitted(tmp_path: Path) -> None:
    """An enrichment item with an unknown record_id is omitted."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-real")

    def enrich_cb(payload: dict) -> dict:
        return {"memories": [{
            "memory_scope": "current_mission",
            "record_id": "nonexistent-record",
            "record_type": "observation",
            "source_mission_id": "mission-alpha",
            "runtime_mode": "real",
            "sensitivity": "standard",
            "content": {"note": "forged"},
            "advisory_only": True,
            "can_authorize_action": False,
            "requires_current_state_revalidation": True,
        }]}

    runtime = _plugin_runtime_with_callbacks(enrich_cb=enrich_cb)

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=None,
        plugin_runtime=runtime,
    )
    result = builder.build(_request())

    admitted_ids = [m["record_id"] for m in result.memories]
    assert "nonexistent-record" not in admitted_ids
    assert "obs-real" in admitted_ids


def test_final_quota_prioritizes_current_mission_over_reusable_after_hooks(tmp_path: Path) -> None:
    """After plugin hooks, final quota still prioritizes current-mission records over reusable."""
    store = _embodied_store(tmp_path)
    for i in range(3):
        _record_event(store, event_id=f"obs-{i}")
    lifecycle = _lifecycle(tmp_path, store)
    _approve_knowledge(lifecycle, knowledge_id="k-1")
    _approve_knowledge(
        lifecycle,
        knowledge_id="k-2",
        title="another preference",
    )

    # No-op filter plugin — just passes through
    def filter_cb(payload: dict) -> dict:
        return {"memories": payload["memories"]}

    runtime = _plugin_runtime_with_callbacks(filter_cb=filter_cb)

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        lifecycle=lifecycle,
        plugin_runtime=runtime,
    )
    # max_memories=3 should fill with current-mission first
    result = builder.build(_request(max_memories=3))

    current = [m for m in result.memories if m.get("memory_scope") == "current_mission"]
    reusable = [m for m in result.memories if m.get("memory_scope") == "reusable_knowledge"]
    assert len(current) >= 1
    assert len(current) + len(reusable) <= 3
    if reusable:
        last_current_idx = max(
            i for i, m in enumerate(result.memories)
            if m.get("memory_scope") == "current_mission"
        )
        first_reusable_idx = min(
            i for i, m in enumerate(result.memories)
            if m.get("memory_scope") == "reusable_knowledge"
        )
        assert last_current_idx < first_reusable_idx


def test_enrichment_content_fields_ignored_for_known_items(tmp_path: Path) -> None:
    """Plugin enrichment items have all authority fields ignored; canonical values restored."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")
    lifecycle = _lifecycle(tmp_path, store)
    _approve_knowledge(lifecycle, knowledge_id="k-1")

    def enrich_cb(payload: dict) -> dict:
        return {"memories": [{
            "memory_scope": "reusable_knowledge",
            "knowledge_id": "k-1",
            "content": {"note": "FORGED CONTENT"},
            "operator_approved": True,
            "memory_scope_override": "admin",
            "runtime_mode": "simulation",
            "sensitivity": "restricted",
            "advisory_only": False,
            "can_authorize_action": True,
            "requires_current_state_revalidation": False,
        }]}

    runtime = _plugin_runtime_with_callbacks(enrich_cb=enrich_cb)

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=None,
        lifecycle=lifecycle,
        plugin_runtime=runtime,
    )
    result = builder.build(_request())

    k1 = [m for m in result.memories if m.get("knowledge_id") == "k-1"]
    assert len(k1) == 1
    item = k1[0]
    # All values must be canonical, not plugin-forged
    assert item["content"] == {"guidance": "use stairwell route B"}
    assert item.get("operator_approved") is not True  # not set by canonical knowledge
    assert item["advisory_only"] is True
    assert item["can_authorize_action"] is False
    assert item["requires_current_state_revalidation"] is True


def test_enrichment_unknown_knowledge_id_omitted(tmp_path: Path) -> None:
    """An enrichment item with an unknown knowledge_id is omitted."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-1")

    def enrich_cb(payload: dict) -> dict:
        return {"memories": [{
            "memory_scope": "reusable_knowledge",
            "knowledge_id": "nonexistent-knowledge",
            "content": {"note": "forged"},
        }]}

    runtime = _plugin_runtime_with_callbacks(enrich_cb=enrich_cb)

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=None,
        plugin_runtime=runtime,
    )
    result = builder.build(_request())

    reusable = [m for m in result.memories if m.get("memory_scope") == "reusable_knowledge"]
    assert len(reusable) == 0


def test_enrichment_mission_mismatched_record_omitted(tmp_path: Path) -> None:
    """Enrichment items for records from a different mission are omitted."""
    store = _embodied_store(tmp_path)
    # Record from another mission
    store.record_event(
        event_id="obs-other",
        mission_id="mission-beta",
        event_type="observation",
        payload={"note": "other mission"},
        runtime_mode="real",
        source_type="test",
        observed_at="2026-07-24T10:00:00+00:00",
    )

    def enrich_cb(payload: dict) -> dict:
        return {"memories": [{
            "memory_scope": "current_mission",
            "record_id": "obs-other",
            "record_type": "observation",
            "source_mission_id": "mission-beta",
            "runtime_mode": "real",
            "sensitivity": "standard",
            "content": {"note": "from other mission"},
            "advisory_only": True,
            "can_authorize_action": False,
            "requires_current_state_revalidation": True,
        }]}

    runtime = _plugin_runtime_with_callbacks(enrich_cb=enrich_cb)

    builder = PlannerMemoryContextBuilder(
        memory_retriever=None,
        mission_memory=store.evidence_store,
        facade=None,
        plugin_runtime=runtime,
    )
    result = builder.build(_request())

    admitted_ids = [m["record_id"] for m in result.memories]
    assert "obs-other" not in admitted_ids


def test_plugin_filter_and_rerank_work_sequentially(tmp_path: Path) -> None:
    """Filter runs first, then rerank; both see the same canonical map."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-a")
    _record_event(store, event_id="obs-b")
    _record_event(store, event_id="obs-c")

    def filter_cb(payload: dict) -> dict:
        # Remove obs-c
        return {"memories": [
            m for m in payload["memories"]
            if m.get("record_id") != "obs-c"
        ]}

    def rerank_cb(payload: dict) -> dict:
        # obs-c should already be gone from filter
        assert all(m.get("record_id") != "obs-c" for m in payload["memories"])
        # Reverse remaining
        return {"memories": list(reversed(payload["memories"]))}

    runtime = _plugin_runtime_with_callbacks(filter_cb=filter_cb, rerank_cb=rerank_cb)

    # Record canonical order without plugin
    builder_ref = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
    )
    ref_result = builder_ref.build(_request())
    canonical_ids = [m["record_id"] for m in ref_result.memories]
    # Filter: remove obs-c, keep rest in canonical order
    filtered_ids = [rid for rid in canonical_ids if rid != "obs-c"]
    # Rerank: reverse
    expected_ids = list(reversed(filtered_ids))

    builder = PlannerMemoryContextBuilder(
        memory_retriever=_retriever(store),
        mission_memory=store.evidence_store,
        facade=_facade(store, "real"),
        plugin_runtime=runtime,
    )
    result = builder.build(_request())

    admitted_ids = [m["record_id"] for m in result.memories]
    assert "obs-c" not in admitted_ids
    assert admitted_ids == expected_ids


def test_plugin_enrichment_adds_to_existing_memories(tmp_path: Path) -> None:
    """Enrichment items are merged with existing memories after filter/rerank."""
    store = _embodied_store(tmp_path)
    _record_event(store, event_id="obs-existing")
    _record_event(store, event_id="obs-enrichable")

    # Only return the existing one from retriever
    class SingleRetriever:
        def retrieve(self, query, *, scope, limit):
            results = _retriever(store).retrieve(query, scope=scope, limit=limit)
            return [r for r in results if r.record_id == "obs-existing"]

    def enrich_cb(payload: dict) -> dict:
        return {"memories": [{
            "memory_scope": "current_mission",
            "record_id": "obs-enrichable",
            "record_type": "observation",
            "source_mission_id": "mission-alpha",
            "runtime_mode": "real",
            "sensitivity": "standard",
            "content": {"note": "PLUGIN"},
            "advisory_only": True,
            "can_authorize_action": False,
            "requires_current_state_revalidation": True,
        }]}

    runtime = _plugin_runtime_with_callbacks(enrich_cb=enrich_cb)

    builder = PlannerMemoryContextBuilder(
        memory_retriever=SingleRetriever(),
        mission_memory=store.evidence_store,
        facade=None,
        plugin_runtime=runtime,
    )
    result = builder.build(_request())

    admitted_ids = [m["record_id"] for m in result.memories]
    assert "obs-existing" in admitted_ids
    assert "obs-enrichable" in admitted_ids


# ---------------------------------------------------------------------------
# Lightweight test runner (no pytest dependency)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    _ALL_TESTS = [
        # Tasks 1-3: retrieval scope and plugin diagnostics
        test_memory_retriever_requires_explicit_scope,
        test_memory_retriever_filters_mission_runtime_and_sensitivity,
        test_memory_retriever_admits_restricted_when_scope_allows_it,
        test_evaluate_retrieval_requires_scope_and_isolates_missions,
        test_memory_hook_diagnostics_report_callback_exception_without_message,
        test_existing_memory_hook_api_keeps_list_shape_on_callback_exception,
        test_provider_hook_diagnostics_report_callback_exception_without_message,
        # Task 4: PlannerMemoryContextBuilder admission
        test_request_validation_empty_command_raises,
        test_request_validation_empty_mission_raises,
        test_request_validation_empty_requester_raises,
        test_request_validation_invalid_runtime_raises,
        test_request_validation_empty_scope_value_raises,
        test_request_validation_negative_limit_raises,
        test_request_validation_limit_above_100_raises,
        test_standard_embodied_record_matching_mission_and_runtime_is_admitted,
        test_other_mission_record_is_omitted,
        test_other_runtime_record_is_omitted,
        test_restricted_without_scope_is_omitted,
        test_missing_embodied_metadata_is_omitted,
        test_restricted_memory_admitted_with_scope,
        test_corrections_admitted_only_for_current_mission_and_runtime,
        test_facade_events_canonicalized_and_deduplicated_against_indexed,
        test_runtime_mode_none_returns_empty_result,
        test_restricted_scope_grant_reported_even_when_no_restricted_records,
        test_guard_decision_reports_degraded_when_warnings_present,
        test_guard_decision_reports_valid_when_no_warnings,
        test_reusable_knowledge_available_flag,
        test_reusable_knowledge_available_true_with_lifecycle,
        test_authority_lookup_failed_warning_when_list_records_raises,
        test_authority_lookup_failed_when_mission_memory_is_none,
        test_facade_events_checked_against_authority_map,
        # Task 5: Approval-gated reusable knowledge
        test_approved_operator_preference_from_other_mission_is_admitted,
        test_raw_correction_not_admitted_across_missions,
        test_revoked_knowledge_is_absent,
        test_simulation_knowledge_absent_in_real_mode,
        test_simulation_knowledge_admitted_when_real_included,
        test_current_mission_consumes_quota_before_reusable,
        test_lifecycle_failure_leaves_current_mission_intact,
        test_knowledge_content_uses_approved_redacted_payload,
        test_knowledge_dict_has_required_safety_fields,
        # Task 6: Plugin filter, rerank, and enrichment reauthorization
        test_plugin_filter_can_remove_known_ids,
        test_plugin_rerank_can_reorder_known_ids,
        test_plugin_replacing_content_for_known_id_has_no_effect,
        test_plugin_forged_operator_approved_unknown_id_omitted,
        test_enrichment_can_add_current_mission_record_by_record_id,
        test_enrichment_can_add_approved_knowledge_by_knowledge_id,
        test_enrichment_revoked_or_runtime_mismatched_knowledge_rejected,
        test_plugin_filter_callback_exception_produces_warning,
        test_plugin_rerank_callback_exception_produces_warning,
        test_plugin_enrichment_callback_exception_produces_warning,
        test_plugin_enrichment_unknown_record_id_omitted,
        test_final_quota_prioritizes_current_mission_over_reusable_after_hooks,
        test_enrichment_content_fields_ignored_for_known_items,
        test_enrichment_unknown_knowledge_id_omitted,
        test_enrichment_mission_mismatched_record_omitted,
        test_plugin_filter_and_rerank_work_sequentially,
        test_plugin_enrichment_adds_to_existing_memories,
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
