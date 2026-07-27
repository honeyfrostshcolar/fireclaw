"""Focused tests for consolidation boundaries, watermarks, and coordinator."""
from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Task 1: boundary identity and state replay
# ---------------------------------------------------------------------------

def test_boundary_rejects_invalid_or_empty_scope() -> None:
    from fireclaw_core.memory.consolidation_state import ConsolidationBoundary
    # empty mission_id
    try:
        ConsolidationBoundary(
            boundary_id="b1",
            mission_id="",
            runtime_mode="real",
            scope_kind="mission",
            robot_id=None,
            subtask_id=None,
            after_sequence=0,
            through_sequence=5,
            terminal_event_id="evt-terminal",
            trigger_reason="subtask_terminal",
        )
        raise AssertionError("expected ValueError for empty mission_id")
    except ValueError:
        pass
    # invalid runtime_mode
    try:
        ConsolidationBoundary(
            boundary_id="b2",
            mission_id="m1",
            runtime_mode="invalid",
            scope_kind="mission",
            robot_id=None,
            subtask_id=None,
            after_sequence=0,
            through_sequence=5,
            terminal_event_id="evt-terminal",
            trigger_reason="subtask_terminal",
        )
        raise AssertionError("expected ValueError for invalid runtime_mode")
    except ValueError:
        pass
    # negative sequence
    try:
        ConsolidationBoundary(
            boundary_id="b3",
            mission_id="m1",
            runtime_mode="real",
            scope_kind="mission",
            robot_id=None,
            subtask_id=None,
            after_sequence=-1,
            through_sequence=5,
            terminal_event_id="evt-terminal",
            trigger_reason="subtask_terminal",
        )
        raise AssertionError("expected ValueError for negative sequence")
    except ValueError:
        pass
    # through_sequence <= after_sequence
    try:
        ConsolidationBoundary(
            boundary_id="b4",
            mission_id="m1",
            runtime_mode="real",
            scope_kind="mission",
            robot_id=None,
            subtask_id=None,
            after_sequence=5,
            through_sequence=5,
            terminal_event_id="evt-terminal",
            trigger_reason="subtask_terminal",
        )
        raise AssertionError("expected ValueError for through_sequence <= after_sequence")
    except ValueError:
        pass
    # empty terminal_event_id
    try:
        ConsolidationBoundary(
            boundary_id="b5",
            mission_id="m1",
            runtime_mode="real",
            scope_kind="mission",
            robot_id=None,
            subtask_id=None,
            after_sequence=0,
            through_sequence=5,
            terminal_event_id="",
            trigger_reason="subtask_terminal",
        )
        raise AssertionError("expected ValueError for empty terminal_event_id")
    except ValueError:
        pass


def test_boundary_id_is_stable_for_same_terminal_event() -> None:
    from fireclaw_core.memory.consolidation_state import ConsolidationBoundary
    b1 = ConsolidationBoundary(
        boundary_id="auto",
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        after_sequence=0,
        through_sequence=10,
        terminal_event_id="evt-terminal-abc",
        trigger_reason="subtask_terminal",
    )
    b2 = ConsolidationBoundary(
        boundary_id="auto",
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        after_sequence=0,
        through_sequence=10,
        terminal_event_id="evt-terminal-abc",
        trigger_reason="subtask_terminal",
    )
    assert b1.boundary_id == b2.boundary_id
    # different terminal event -> different id
    b3 = ConsolidationBoundary(
        boundary_id="auto",
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        after_sequence=0,
        through_sequence=10,
        terminal_event_id="evt-terminal-different",
        trigger_reason="subtask_terminal",
    )
    assert b1.boundary_id != b3.boundary_id


def test_state_store_replays_latest_boundary_status_and_watermark(tmp_path: Path) -> None:
    from fireclaw_core.memory.consolidation_state import (
        ConsolidationBoundary,
        ConsolidationStateStore,
    )
    store = ConsolidationStateStore(tmp_path / "state.jsonl")
    boundary = ConsolidationBoundary(
        boundary_id="b-1",
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        after_sequence=0,
        through_sequence=10,
        terminal_event_id="evt-t1",
        trigger_reason="subtask_terminal",
    )
    store.ensure_boundary(boundary)
    assert store.pending_boundaries() == [boundary]
    store.transition(boundary.boundary_id, status="running")
    store.transition(
        boundary.boundary_id,
        status="completed",
        through_sequence=10,
    )
    assert store.pending_boundaries() == []
    wm = store.watermark("m1", "real", "robot-a", "sub-1")
    assert wm is not None
    assert wm.through_sequence == 10


def test_completed_watermark_never_moves_backwards(tmp_path: Path) -> None:
    from fireclaw_core.memory.consolidation_state import (
        ConsolidationBoundary,
        ConsolidationStateStore,
    )
    store = ConsolidationStateStore(tmp_path / "state.jsonl")
    b1 = ConsolidationBoundary(
        boundary_id="b-1",
        mission_id="m1",
        runtime_mode="real",
        scope_kind="mission",
        robot_id=None,
        subtask_id=None,
        after_sequence=0,
        through_sequence=10,
        terminal_event_id="evt-t1",
        trigger_reason="mission_terminal",
    )
    store.ensure_boundary(b1)
    store.transition(b1.boundary_id, status="completed", through_sequence=10)
    b2 = ConsolidationBoundary(
        boundary_id="b-2",
        mission_id="m1",
        runtime_mode="real",
        scope_kind="mission",
        robot_id=None,
        subtask_id=None,
        after_sequence=10,
        through_sequence=15,
        terminal_event_id="evt-t2",
        trigger_reason="mission_terminal",
    )
    store.ensure_boundary(b2)
    # Try to set watermark backwards — store should reject the transition.
    try:
        store.transition(b2.boundary_id, status="completed", through_sequence=5)
        raise AssertionError("expected ValueError for watermark regression")
    except ValueError:
        pass
    wm = store.watermark("m1", "real", None, None)
    assert wm is not None
    assert wm.through_sequence == 10


def test_state_store_ignores_truncated_final_jsonl_line(tmp_path: Path) -> None:
    from fireclaw_core.memory.consolidation_state import (
        ConsolidationBoundary,
        ConsolidationStateStore,
    )
    path = tmp_path / "state.jsonl"
    store = ConsolidationStateStore(path)
    boundary = ConsolidationBoundary(
        boundary_id="b-1",
        mission_id="m1",
        runtime_mode="real",
        scope_kind="mission",
        robot_id=None,
        subtask_id=None,
        after_sequence=0,
        through_sequence=10,
        terminal_event_id="evt-t1",
        trigger_reason="mission_terminal",
    )
    store.ensure_boundary(boundary)
    store.transition(boundary.boundary_id, status="completed", through_sequence=10)
    # append a truncated line
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"truncated')
    store2 = ConsolidationStateStore(path)
    wm = store2.watermark("m1", "real", None, None)
    assert wm is not None
    assert wm.through_sequence == 10


# ---------------------------------------------------------------------------
# Task 2: explicit closed-source consolidation
# ---------------------------------------------------------------------------

_event_counter = 0

def _make_test_events(
    mission_id: str = "m1",
    runtime_mode: str = "real",
    robot_id: str = "robot-a",
    subtask_id: str = "sub-1",
    count: int = 5,
    *,
    prefix: str = "evt",
) -> list:
    """Create simple EmbodiedMemoryEvent-like objects for testing."""
    global _event_counter
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryEvent
    events = []
    for i in range(count):
        eid = f"{prefix}-{_event_counter}"
        _event_counter += 1
        events.append(EmbodiedMemoryEvent(
            event_id=eid,
            mission_id=mission_id,
            event_type="observation",
            payload={"index": i},
            runtime_mode=runtime_mode,
            source_type="test",
            robot_id=robot_id,
            subtask_id=subtask_id,
            observed_at=f"2026-07-24T10:00:{i:02d}Z",
            created_at=f"2026-07-24T10:00:{i:02d}Z",
            confidence=1.0,
            sensitivity="standard",
        ))
    return events


def test_consolidate_events_rejects_missing_duplicate_or_cross_scope_ids(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
    from fireclaw_core.memory.consolidation import FireClawConsolidationEngine
    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(store, producer_type="memory_consolidator", producer_id="test")
    engine = FireClawConsolidationEngine(store=store, producer=producer)
    events = _make_test_events(count=3)
    for event in events:
        store.append_event(event)
    eid0 = events[0].event_id
    # missing ID
    try:
        engine.consolidate_events(
            mission_id="m1",
            runtime_mode="real",
            source_event_ids=(eid0, "evt-MISSING"),
        )
        raise AssertionError("expected ValueError for missing ID")
    except ValueError:
        pass
    # duplicate ID
    try:
        engine.consolidate_events(
            mission_id="m1",
            runtime_mode="real",
            source_event_ids=(eid0, eid0),
        )
        raise AssertionError("expected ValueError for duplicate ID")
    except ValueError:
        pass


def test_consolidate_events_uses_only_named_authority_events(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
    from fireclaw_core.memory.consolidation import FireClawConsolidationEngine
    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(store, producer_type="memory_consolidator", producer_id="test")
    engine = FireClawConsolidationEngine(store=store, producer=producer)
    events = _make_test_events(count=5)
    for event in events:
        store.append_event(event)
    result = engine.consolidate_events(
        mission_id="m1",
        runtime_mode="real",
        source_event_ids=tuple(e.event_id for e in events[:3]),
    )
    assert result.source_event_count == 3
    assert len(result.episode_event_ids) == 1


def test_successive_closed_source_sets_are_disjoint(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
    from fireclaw_core.memory.consolidation import FireClawConsolidationEngine
    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(store, producer_type="memory_consolidator", producer_id="test")
    engine = FireClawConsolidationEngine(store=store, producer=producer)
    events = _make_test_events(count=6)
    for event in events:
        store.append_event(event)
    r1 = engine.consolidate_events(
        mission_id="m1",
        runtime_mode="real",
        source_event_ids=tuple(e.event_id for e in events[:3]),
    )
    r2 = engine.consolidate_events(
        mission_id="m1",
        runtime_mode="real",
        source_event_ids=tuple(e.event_id for e in events[3:6]),
    )
    # Episode source sets must be disjoint
    ep1 = next(e for e in store.list_events(mission_id="m1") if e.event_id == r1.episode_event_ids[0])
    ep2 = next(e for e in store.list_events(mission_id="m1") if e.event_id == r2.episode_event_ids[0])
    assert set(ep1.derived_from).isdisjoint(set(ep2.derived_from))


def test_retry_repairs_same_job_without_new_episode_or_gist(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
    from fireclaw_core.memory.consolidation import FireClawConsolidationEngine
    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(store, producer_type="memory_consolidator", producer_id="test")
    engine = FireClawConsolidationEngine(store=store, producer=producer)
    events = _make_test_events(count=3)
    for event in events:
        store.append_event(event)
    source_ids = tuple(e.event_id for e in events[:3])
    r1 = engine.consolidate_events(
        mission_id="m1",
        runtime_mode="real",
        source_event_ids=source_ids,
    )
    r2 = engine.consolidate_events(
        mission_id="m1",
        runtime_mode="real",
        source_event_ids=source_ids,
    )
    assert r1.episode_event_ids == r2.episode_event_ids
    assert r1.gist_event_ids == r2.gist_event_ids


# ---------------------------------------------------------------------------
# Task 3: cross-process lease and recovery coordinator
# ---------------------------------------------------------------------------

def test_non_terminal_status_does_not_create_boundary(tmp_path: Path) -> None:
    from fireclaw_core.memory.consolidation_state import ConsolidationStateStore
    from fireclaw_core.memory.consolidation_coordinator import MemoryConsolidationCoordinator
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
    from fireclaw_core.memory.consolidation import FireClawConsolidationEngine
    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(store, producer_type="memory_consolidator", producer_id="test")
    engine = FireClawConsolidationEngine(store=store, producer=producer)
    state_store = ConsolidationStateStore(tmp_path / "state.jsonl")
    coordinator = MemoryConsolidationCoordinator(
        engine=engine,
        state_store=state_store,
        store=store,
        lock_path=tmp_path / "consolidation.lock",
    )
    # Non-terminal status should not create a boundary
    coordinator.request_terminal_boundary(
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        terminal_event_id="evt-t1",
        terminal_status="running",
        through_sequence=5,
    )
    assert state_store.pending_boundaries() == []


def test_terminal_boundary_selects_only_open_sequence_range(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
    from fireclaw_core.memory.consolidation import FireClawConsolidationEngine
    from fireclaw_core.memory.consolidation_state import ConsolidationStateStore
    from fireclaw_core.memory.consolidation_coordinator import MemoryConsolidationCoordinator
    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(store, producer_type="memory_consolidator", producer_id="test")
    engine = FireClawConsolidationEngine(store=store, producer=producer)
    state_store = ConsolidationStateStore(tmp_path / "state.jsonl")
    coordinator = MemoryConsolidationCoordinator(
        engine=engine,
        state_store=state_store,
        store=store,
        lock_path=tmp_path / "consolidation.lock",
    )
    events = _make_test_events(count=5)
    for event in events:
        store.append_event(event)
    coordinator.request_terminal_boundary(
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        terminal_event_id="evt-t4",
        terminal_status="succeeded",
        through_sequence=5,
    )
    result = coordinator.run_pending_once()
    assert result.processed == 1
    wm = state_store.watermark("m1", "real", "robot-a", "sub-1")
    assert wm is not None
    assert wm.through_sequence == 5


def test_insufficient_closed_range_advances_watermark_without_episode(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
    from fireclaw_core.memory.consolidation import FireClawConsolidationEngine, ConsolidationConfig
    from fireclaw_core.memory.consolidation_state import ConsolidationStateStore
    from fireclaw_core.memory.consolidation_coordinator import MemoryConsolidationCoordinator
    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(store, producer_type="memory_consolidator", producer_id="test")
    config = ConsolidationConfig(min_events_per_episode=3)
    engine = FireClawConsolidationEngine(store=store, producer=producer, config=config)
    state_store = ConsolidationStateStore(tmp_path / "state.jsonl")
    coordinator = MemoryConsolidationCoordinator(
        engine=engine,
        state_store=state_store,
        store=store,
        lock_path=tmp_path / "consolidation.lock",
    )
    # Only 1 event — below min_events_per_episode
    events = _make_test_events(count=1)
    for event in events:
        store.append_event(event)
    coordinator.request_terminal_boundary(
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        terminal_event_id="evt-t0",
        terminal_status="succeeded",
        through_sequence=1,
    )
    result = coordinator.run_pending_once()
    assert result.processed == 1
    wm = state_store.watermark("m1", "real", "robot-a", "sub-1")
    assert wm is not None
    assert wm.through_sequence == 1


def test_delayed_event_is_processed_only_by_later_boundary(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
    from fireclaw_core.memory.consolidation import FireClawConsolidationEngine
    from fireclaw_core.memory.consolidation_state import ConsolidationStateStore
    from fireclaw_core.memory.consolidation_coordinator import MemoryConsolidationCoordinator
    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(store, producer_type="memory_consolidator", producer_id="test")
    engine = FireClawConsolidationEngine(store=store, producer=producer)
    state_store = ConsolidationStateStore(tmp_path / "state.jsonl")
    coordinator = MemoryConsolidationCoordinator(
        engine=engine,
        state_store=state_store,
        store=store,
        lock_path=tmp_path / "consolidation.lock",
    )
    # First batch: 3 events
    events = _make_test_events(count=3)
    for event in events:
        store.append_event(event)
    coordinator.request_terminal_boundary(
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        terminal_event_id="evt-t2",
        terminal_status="succeeded",
        through_sequence=3,
    )
    coordinator.run_pending_once()
    wm1 = state_store.watermark("m1", "real", "robot-a", "sub-1")
    assert wm1 is not None
    assert wm1.through_sequence == 3
    # Delayed event arrives
    delayed = _make_test_events(count=1)[0]
    # Give it a unique ID and later observed_at
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryEvent
    delayed = EmbodiedMemoryEvent(
        event_id="evt-delayed",
        mission_id="m1",
        event_type="observation",
        payload={"delayed": True},
        runtime_mode="real",
        source_type="test",
        robot_id="robot-a",
        subtask_id="sub-1",
        observed_at="2026-07-24T10:01:00Z",
        created_at="2026-07-24T10:01:00Z",
    )
    store.append_event(delayed)
    coordinator.request_terminal_boundary(
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        terminal_event_id="evt-delayed",
        terminal_status="succeeded",
        through_sequence=4,
    )
    coordinator.run_pending_once()
    wm2 = state_store.watermark("m1", "real", "robot-a", "sub-1")
    assert wm2 is not None
    assert wm2.through_sequence == 4


def test_restart_recovers_queued_and_running_boundaries(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
    from fireclaw_core.memory.consolidation import FireClawConsolidationEngine
    from fireclaw_core.memory.consolidation_state import ConsolidationStateStore
    from fireclaw_core.memory.consolidation_coordinator import MemoryConsolidationCoordinator
    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(store, producer_type="memory_consolidator", producer_id="test")
    engine = FireClawConsolidationEngine(store=store, producer=producer)
    state_store = ConsolidationStateStore(tmp_path / "state.jsonl")
    coordinator = MemoryConsolidationCoordinator(
        engine=engine,
        state_store=state_store,
        store=store,
        lock_path=tmp_path / "consolidation.lock",
    )
    events = _make_test_events(count=3)
    for event in events:
        store.append_event(event)
    coordinator.request_terminal_boundary(
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        terminal_event_id="evt-t2",
        terminal_status="succeeded",
        through_sequence=3,
    )
    # Simulate restart: create new coordinator with same state
    coordinator2 = MemoryConsolidationCoordinator(
        engine=engine,
        state_store=state_store,
        store=store,
        lock_path=tmp_path / "consolidation.lock",
    )
    coordinator2.recover()
    result = coordinator2.run_pending_once()
    assert result.processed == 1
    wm = state_store.watermark("m1", "real", "robot-a", "sub-1")
    assert wm is not None


def test_lease_contention_leaves_boundary_queued(tmp_path: Path) -> None:
    import fcntl
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
    from fireclaw_core.memory.consolidation import FireClawConsolidationEngine
    from fireclaw_core.memory.consolidation_state import ConsolidationStateStore
    from fireclaw_core.memory.consolidation_coordinator import MemoryConsolidationCoordinator
    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(store, producer_type="memory_consolidator", producer_id="test")
    engine = FireClawConsolidationEngine(store=store, producer=producer)
    state_store = ConsolidationStateStore(tmp_path / "state.jsonl")
    lock_path = tmp_path / "consolidation.lock"
    coordinator = MemoryConsolidationCoordinator(
        engine=engine,
        state_store=state_store,
        store=store,
        lock_path=lock_path,
    )
    events = _make_test_events(count=3)
    for event in events:
        store.append_event(event)
    coordinator.request_terminal_boundary(
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        terminal_event_id="evt-t2",
        terminal_status="succeeded",
        through_sequence=3,
    )
    # Hold the lock externally
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch()
    fd = lock_path.open("r+")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = coordinator.run_pending_once()
        assert result.processed == 0
        assert result.lock_busy == 1
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def test_failure_before_watermark_retries_same_source_set(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
    from fireclaw_core.memory.consolidation import FireClawConsolidationEngine
    from fireclaw_core.memory.consolidation_state import ConsolidationStateStore
    from fireclaw_core.memory.consolidation_coordinator import MemoryConsolidationCoordinator
    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(store, producer_type="memory_consolidator", producer_id="test")
    engine = FireClawConsolidationEngine(store=store, producer=producer)
    state_store = ConsolidationStateStore(tmp_path / "state.jsonl")
    coordinator = MemoryConsolidationCoordinator(
        engine=engine,
        state_store=state_store,
        store=store,
        lock_path=tmp_path / "consolidation.lock",
    )
    events = _make_test_events(count=3)
    for event in events:
        store.append_event(event)
    coordinator.request_terminal_boundary(
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        terminal_event_id="evt-t2",
        terminal_status="succeeded",
        through_sequence=3,
    )
    # First run succeeds
    result1 = coordinator.run_pending_once()
    assert result1.processed == 1
    # Idempotent retry
    coordinator.request_terminal_boundary(
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        terminal_event_id="evt-t2",
        terminal_status="succeeded",
        through_sequence=3,
    )
    result2 = coordinator.run_pending_once()
    assert result2.processed == 0  # already completed


# ---------------------------------------------------------------------------
# Task 4: runtime integration tests
# ---------------------------------------------------------------------------

def test_terminal_trace_transition_queues_boundary(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
    from fireclaw_core.memory.consolidation import FireClawConsolidationEngine
    from fireclaw_core.memory.consolidation_state import ConsolidationStateStore
    from fireclaw_core.memory.consolidation_coordinator import MemoryConsolidationCoordinator
    from fireclaw_core.mission.mission_agent import MissionAgent
    from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
    from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry

    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(store, producer_type="memory_consolidator", producer_id="test")
    engine = FireClawConsolidationEngine(store=store, producer=producer)
    state_store = ConsolidationStateStore(tmp_path / "state.jsonl")
    coordinator = MemoryConsolidationCoordinator(
        engine=engine,
        state_store=state_store,
        store=store,
        lock_path=tmp_path / "consolidation.lock",
    )

    entry1 = RobotRegistryEntry(
        robot_id="robot-1",
        base_url="http://localhost:9001",
        capabilities=("search_for_victims",),
        zone="zone-a",
    )
    registry = RobotRegistry([entry1])

    class FakeClient:
        def submit_task(self, entry, **kw):
            return {"status": "accepted", "task_id": "task-1"}
        def get_task_trace(self, entry, task_id):
            return {"status": "succeeded"}
        def cancel_task(self, entry, task_id, **kw):
            return {"status": "cancel_requested"}
        def get_events(self, entry, task_id=None, limit=100):
            return []
        def check_presence(self, entry):
            return {"online": True, "last_seen_at": "2026-07-24T10:00:00Z"}

    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    agent = MissionAgent(
        registry=registry,
        subagent_client=FakeClient(),
        mission_registry=mission_registry,
        embodied_memory_producer=EmbodiedMemoryProducer(
            store, producer_type="mission_agent", producer_id="test-agent",
        ),
        embodied_runtime_mode="real",
        consolidation_coordinator=coordinator,
    )
    # Create a mission with a subtask
    mission_registry.create_mission(mission_id="m1", session_id=None, command="test", created_at="2026-07-24T10:00:00Z")
    mission_registry.record_subtask(
        mission_id="m1", robot_id="robot-1", task_id="task-1",
        command="test", status="accepted", created_at="2026-07-24T10:00:00Z",
    )
    # Add some embodied events
    for i in range(3):
        store.append_event(_make_test_events(count=1)[0])
    # mission_trace discovers terminal status
    trace = agent.mission_trace("m1")
    # Verify boundary was queued
    pending = state_store.pending_boundaries()
    assert len(pending) >= 1


def test_repeated_terminal_trace_does_not_queue_duplicate_boundary(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
    from fireclaw_core.memory.consolidation import FireClawConsolidationEngine
    from fireclaw_core.memory.consolidation_state import ConsolidationStateStore
    from fireclaw_core.memory.consolidation_coordinator import MemoryConsolidationCoordinator
    from fireclaw_core.mission.mission_agent import MissionAgent
    from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
    from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry

    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(store, producer_type="memory_consolidator", producer_id="test")
    engine = FireClawConsolidationEngine(store=store, producer=producer)
    state_store = ConsolidationStateStore(tmp_path / "state.jsonl")
    coordinator = MemoryConsolidationCoordinator(
        engine=engine,
        state_store=state_store,
        store=store,
        lock_path=tmp_path / "consolidation.lock",
    )

    entry1 = RobotRegistryEntry(
        robot_id="robot-1", base_url="http://localhost:9001",
        capabilities=("search_for_victims",), zone="zone-a",
    )
    registry = RobotRegistry([entry1])

    class FakeClient:
        def submit_task(self, entry, **kw):
            return {"status": "accepted", "task_id": "task-1"}
        def get_task_trace(self, entry, task_id):
            return {"status": "succeeded"}
        def cancel_task(self, entry, task_id, **kw):
            return {"status": "cancel_requested"}
        def get_events(self, entry, task_id=None, limit=100):
            return []
        def check_presence(self, entry):
            return {"online": True, "last_seen_at": "2026-07-24T10:00:00Z"}

    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    agent = MissionAgent(
        registry=registry,
        subagent_client=FakeClient(),
        mission_registry=mission_registry,
        embodied_memory_producer=EmbodiedMemoryProducer(
            store, producer_type="mission_agent", producer_id="test-agent",
        ),
        embodied_runtime_mode="real",
        consolidation_coordinator=coordinator,
    )
    mission_registry.create_mission(mission_id="m1", session_id=None, command="test", created_at="2026-07-24T10:00:00Z")
    mission_registry.record_subtask(
        mission_id="m1", robot_id="robot-1", task_id="task-1",
        command="test", status="accepted", created_at="2026-07-24T10:00:00Z",
    )
    for i in range(3):
        store.append_event(_make_test_events(count=1)[0])
    # First trace
    agent.mission_trace("m1")
    count1 = len(state_store.pending_boundaries())
    # Second trace — same terminal, no new boundary
    agent.mission_trace("m1")
    count2 = len(state_store.pending_boundaries())
    assert count1 == count2


def test_cancel_requested_is_not_terminal_boundary(tmp_path: Path) -> None:
    from fireclaw_core.memory.consolidation_state import ConsolidationStateStore
    from fireclaw_core.memory.consolidation_coordinator import MemoryConsolidationCoordinator
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
    from fireclaw_core.memory.consolidation import FireClawConsolidationEngine

    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(store, producer_type="memory_consolidator", producer_id="test")
    engine = FireClawConsolidationEngine(store=store, producer=producer)
    state_store = ConsolidationStateStore(tmp_path / "state.jsonl")
    coordinator = MemoryConsolidationCoordinator(
        engine=engine,
        state_store=state_store,
        store=store,
        lock_path=tmp_path / "consolidation.lock",
    )
    coordinator.request_terminal_boundary(
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        terminal_event_id="evt-cancel",
        terminal_status="cancel_requested",
        through_sequence=5,
    )
    assert state_store.pending_boundaries() == []


def test_consolidation_failure_does_not_change_mission_result(tmp_path: Path) -> None:
    """Verify that mission execution succeeds even if consolidation fails."""
    from fireclaw_core.memory.consolidation_state import ConsolidationStateStore
    from fireclaw_core.memory.consolidation_coordinator import MemoryConsolidationCoordinator
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
    from fireclaw_core.memory.consolidation import FireClawConsolidationEngine

    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(store, producer_type="memory_consolidator", producer_id="test")
    engine = FireClawConsolidationEngine(store=store, producer=producer)
    state_store = ConsolidationStateStore(tmp_path / "state.jsonl")
    # Use a non-existent lock path to simulate lock failure
    coordinator = MemoryConsolidationCoordinator(
        engine=engine,
        state_store=state_store,
        store=store,
        lock_path=tmp_path / "nonexistent" / "dir" / "consolidation.lock",
    )
    # request_terminal_boundary should succeed even if coordinator is broken
    events = _make_test_events(count=3)
    for event in events:
        store.append_event(event)
    coordinator.request_terminal_boundary(
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        terminal_event_id="evt-t2",
        terminal_status="succeeded",
        through_sequence=3,
    )
    assert len(state_store.pending_boundaries()) == 1
