"""Focused tests for startup working-memory hydration."""
from __future__ import annotations

from pathlib import Path


def test_list_missions_reports_derived_statuses(tmp_path: Path) -> None:
    from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
    registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    registry.create_mission(mission_id="m1", session_id=None, command="test1", created_at="2026-07-24T10:00:00+00:00")
    registry.create_mission(mission_id="m2", session_id=None, command="test2", created_at="2026-07-24T10:01:00+00:00")
    registry.record_subtask(
        mission_id="m1", robot_id="r1", task_id="t1",
        command="sub1", status="succeeded", created_at="2026-07-24T10:02:00+00:00",
    )
    missions = registry.list_missions()
    assert len(missions) == 2
    m1 = next(m for m in missions if m.mission_id == "m1")
    m2 = next(m for m in missions if m.mission_id == "m2")
    assert m1.status == "succeeded"
    assert m2.status == "created"


def test_hydration_selects_only_active_mission_and_runtime(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryEvent, EmbodiedMemoryStore
    from fireclaw_core.memory.working_memory import EmbodiedWorkingMemory
    from fireclaw_core.mission.mission_registry import JsonlMissionRegistry

    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    wm = EmbodiedWorkingMemory()

    # Add events for two missions, two runtimes
    events = [
        EmbodiedMemoryEvent(
            event_id="evt-1", mission_id="m-active", event_type="observation",
            payload={}, runtime_mode="real", source_type="test",
            observed_at="2026-07-24T10:00:50+00:00",
        ),
        EmbodiedMemoryEvent(
            event_id="evt-2", mission_id="m-completed", event_type="observation",
            payload={}, runtime_mode="real", source_type="test",
            observed_at="2026-07-24T10:00:51+00:00",
        ),
        EmbodiedMemoryEvent(
            event_id="evt-3", mission_id="m-active", event_type="observation",
            payload={}, runtime_mode="simulation", source_type="test",
            observed_at="2026-07-24T10:00:52+00:00",
        ),
    ]
    for event in events:
        store.append_event(event)

    registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    registry.create_mission(mission_id="m-active", session_id=None, command="a", created_at="2026-07-24T09:59:00+00:00")
    registry.create_mission(mission_id="m-completed", session_id=None, command="b", created_at="2026-07-24T09:59:00+00:00")
    registry.record_subtask(
        mission_id="m-completed", robot_id="r1", task_id="t1",
        command="sub", status="succeeded", created_at="2026-07-24T10:00:00+00:00",
    )

    report = wm.hydrate_recent(
        store=store,
        registry=registry,
        runtime_mode="real",
        reference_at="2026-07-24T10:01:00+00:00",
    )
    # Only m-active/real should be hydrated
    assert report.added == 1
    snap = wm.snapshot(runtime_mode="real", mission_id="m-active", reference_at="2026-07-24T10:01:00+00:00")
    assert len(snap.events) == 1
    assert snap.events[0].event_id == "evt-1"


def test_hydration_excludes_stale_and_future_skewed_events(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryEvent, EmbodiedMemoryStore
    from fireclaw_core.memory.working_memory import EmbodiedWorkingMemory, WorkingMemoryConfig
    from fireclaw_core.mission.mission_registry import JsonlMissionRegistry

    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    config = WorkingMemoryConfig(observation_max_age_seconds=30.0, future_clock_skew_seconds=5.0)
    wm = EmbodiedWorkingMemory(config=config)

    events = [
        # Fresh
        EmbodiedMemoryEvent(
            event_id="evt-fresh", mission_id="m1", event_type="observation",
            payload={}, runtime_mode="real", source_type="test",
            observed_at="2026-07-24T10:00:40+00:00",
        ),
        # Stale (120s old, max_age=30s)
        EmbodiedMemoryEvent(
            event_id="evt-stale", mission_id="m1", event_type="observation",
            payload={}, runtime_mode="real", source_type="test",
            observed_at="2026-07-24T09:58:00+00:00",
        ),
        # Future (beyond skew)
        EmbodiedMemoryEvent(
            event_id="evt-future", mission_id="m1", event_type="observation",
            payload={}, runtime_mode="real", source_type="test",
            observed_at="2026-07-24T10:01:10+00:00",
        ),
    ]
    for event in events:
        store.append_event(event)

    registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    registry.create_mission(mission_id="m1", session_id=None, command="test", created_at="2026-07-24T09:59:00+00:00")

    report = wm.hydrate_recent(
        store=store,
        registry=registry,
        runtime_mode="real",
        reference_at="2026-07-24T10:01:00+00:00",
    )
    assert report.stale >= 1
    snap = wm.snapshot(runtime_mode="real", mission_id="m1", reference_at="2026-07-24T10:01:00+00:00")
    # Only the fresh event should be in the snapshot
    assert len(snap.events) == 1
    assert snap.events[0].event_id == "evt-fresh"


def test_hydration_is_deterministic_and_capacity_bounded(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryEvent, EmbodiedMemoryStore
    from fireclaw_core.memory.working_memory import EmbodiedWorkingMemory, WorkingMemoryConfig
    from fireclaw_core.mission.mission_registry import JsonlMissionRegistry

    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    config = WorkingMemoryConfig(capacity=5)
    wm = EmbodiedWorkingMemory(config=config)

    # Add 10 events
    for i in range(10):
        store.append_event(EmbodiedMemoryEvent(
            event_id=f"evt-{i}", mission_id="m1", event_type="observation",
            payload={"i": i}, runtime_mode="real", source_type="test",
            observed_at=f"2026-07-24T10:00:{i:02d}+00:00",
        ))

    registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    registry.create_mission(mission_id="m1", session_id=None, command="test", created_at="2026-07-24T09:59:00+00:00")

    report = wm.hydrate_recent(
        store=store,
        registry=registry,
        runtime_mode="real",
        reference_at="2026-07-24T10:00:30+00:00",
    )
    # Capacity is 5, so at most 5 events
    assert wm.count <= 5
    # Running again should give same result
    wm2 = EmbodiedWorkingMemory(config=config)
    report2 = wm2.hydrate_recent(
        store=store,
        registry=registry,
        runtime_mode="real",
        reference_at="2026-07-24T10:00:30+00:00",
    )
    assert report.added == report2.added


def test_hydration_reserves_fair_capacity_for_active_missions(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryEvent, EmbodiedMemoryStore
    from fireclaw_core.memory.working_memory import EmbodiedWorkingMemory, WorkingMemoryConfig
    from fireclaw_core.mission.mission_registry import JsonlMissionRegistry

    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    config = WorkingMemoryConfig(capacity=10)
    wm = EmbodiedWorkingMemory(config=config)

    # 5 events for m1, 5 for m2
    for i in range(5):
        store.append_event(EmbodiedMemoryEvent(
            event_id=f"m1-{i}", mission_id="m1", event_type="observation",
            payload={}, runtime_mode="real", source_type="test",
            observed_at=f"2026-07-24T10:00:{i:02d}+00:00",
        ))
        store.append_event(EmbodiedMemoryEvent(
            event_id=f"m2-{i}", mission_id="m2", event_type="observation",
            payload={}, runtime_mode="real", source_type="test",
            observed_at=f"2026-07-24T10:00:{i:02d}+00:00",
        ))

    registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    registry.create_mission(mission_id="m1", session_id=None, command="a", created_at="2026-07-24T09:59:00+00:00")
    registry.create_mission(mission_id="m2", session_id=None, command="b", created_at="2026-07-24T09:59:00+00:00")

    report = wm.hydrate_recent(
        store=store,
        registry=registry,
        runtime_mode="real",
        reference_at="2026-07-24T10:00:30+00:00",
    )
    snap = wm.snapshot(runtime_mode="real", mission_id="m1", reference_at="2026-07-24T10:00:30+00:00")
    snap2 = wm.snapshot(runtime_mode="real", mission_id="m2", reference_at="2026-07-24T10:00:30+00:00")
    # Both missions should have events
    assert len(snap.events) > 0
    assert len(snap2.events) > 0


def test_hydration_does_not_append_authority_records(tmp_path: Path) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryEvent, EmbodiedMemoryStore
    from fireclaw_core.memory.working_memory import EmbodiedWorkingMemory
    from fireclaw_core.mission.mission_registry import JsonlMissionRegistry

    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    wm = EmbodiedWorkingMemory()

    store.append_event(EmbodiedMemoryEvent(
        event_id="evt-1", mission_id="m1", event_type="observation",
        payload={}, runtime_mode="real", source_type="test",
        observed_at="2026-07-24T10:00:00+00:00",
    ))

    registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    registry.create_mission(mission_id="m1", session_id=None, command="test", created_at="2026-07-24T09:59:00+00:00")

    count_before = len(store.list_events(mission_id="m1"))
    wm.hydrate_recent(
        store=store,
        registry=registry,
        runtime_mode="real",
        reference_at="2026-07-24T10:00:30+00:00",
    )
    count_after = len(store.list_events(mission_id="m1"))
    assert count_before == count_after


def test_runtime_hydration_failure_starts_with_empty_projection(tmp_path: Path) -> None:
    from fireclaw_core.memory.working_memory import EmbodiedWorkingMemory
    from fireclaw_core.mission.mission_registry import JsonlMissionRegistry

    wm = EmbodiedWorkingMemory()
    registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    # Non-existent store path
    class FakeStore:
        def list_events(self, **kw):
            raise RuntimeError("store unavailable")
    report = wm.hydrate_recent(
        store=FakeStore(),
        registry=registry,
        runtime_mode="real",
        reference_at="2026-07-24T10:00:30+00:00",
    )
    assert report.added == 0
    assert wm.count == 0


# --- Task 3 RED tests: newest-first round-robin ---


def _active_registry(
    tmp_path: Path,
    *,
    mission_ids: tuple[str, ...],
):
    from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
    registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    for index, mission_id in enumerate(mission_ids):
        registry.create_mission(
            mission_id=mission_id,
            session_id=None,
            command="search",
            created_at=f"2026-07-24T09:00:0{index}+00:00",
        )
    return registry


def _append_event(
    store,
    *,
    event_id: str,
    mission_id: str,
    observed_at: str,
) -> None:
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryEvent
    store.append_event(EmbodiedMemoryEvent(
        event_id=event_id,
        mission_id=mission_id,
        event_type="command",
        payload={"command": "search"},
        runtime_mode="real",
        source_type="operator",
        observed_at=observed_at,
    ))


def test_hydration_capacity_keeps_newest_events(tmp_path: Path) -> None:
    """Capacity pressure must preserve the newest events."""
    from fireclaw_core.memory.working_memory import EmbodiedWorkingMemory, WorkingMemoryConfig

    registry = _active_registry(tmp_path, mission_ids=("mission-1",))
    store = __import__("fireclaw_core.memory.embodied_memory", fromlist=["EmbodiedMemoryStore"]).EmbodiedMemoryStore(
        tmp_path / "memory.jsonl"
    )
    for index in range(3):
        _append_event(
            store,
            event_id=f"evt-{index}",
            mission_id="mission-1",
            observed_at=f"2026-07-24T10:00:0{index}+00:00",
        )
    working = EmbodiedWorkingMemory(
        WorkingMemoryConfig(capacity=2, task_context_max_age_seconds=600)
    )
    report = working.hydrate_recent(
        store=store,
        registry=registry,
        runtime_mode="real",
        reference_at="2026-07-24T10:00:10+00:00",
    )
    snapshot = working.snapshot(
        runtime_mode="real",
        mission_id="mission-1",
        reference_at="2026-07-24T10:00:10+00:00",
        include_stale=True,
    )
    assert report.selected == 2
    assert [event.event_id for event in snapshot.events] == ["evt-1", "evt-2"]


def test_hydration_round_robin_does_not_let_one_mission_fill_capacity(
    tmp_path: Path,
) -> None:
    """Round-robin must give both missions representation."""
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryStore
    from fireclaw_core.memory.working_memory import EmbodiedWorkingMemory, WorkingMemoryConfig

    registry = _active_registry(
        tmp_path,
        mission_ids=("mission-a", "mission-b"),
    )
    store = EmbodiedMemoryStore(tmp_path / "memory.jsonl")
    for index in range(4):
        _append_event(
            store,
            event_id=f"a-{index}",
            mission_id="mission-a",
            observed_at=f"2026-07-24T10:00:0{index}+00:00",
        )
    _append_event(
        store,
        event_id="b-0",
        mission_id="mission-b",
        observed_at="2026-07-24T10:00:04+00:00",
    )
    working = EmbodiedWorkingMemory(WorkingMemoryConfig(capacity=3))
    working.hydrate_recent(
        store=store,
        registry=registry,
        runtime_mode="real",
        reference_at="2026-07-24T10:00:05+00:00",
    )
    snapshot_a = working.snapshot(
        runtime_mode="real",
        mission_id="mission-a",
        reference_at="2026-07-24T10:00:05+00:00",
    )
    snapshot_b = working.snapshot(
        runtime_mode="real",
        mission_id="mission-b",
        reference_at="2026-07-24T10:00:05+00:00",
    )
    # Both missions should have events
    assert len(snapshot_a.events) > 0
    assert len(snapshot_b.events) > 0


def test_runtime_builder_hydrates_active_mission_memory(tmp_path: Path) -> None:
    """build_mission_agent_from_paths must hydrate working memory at startup."""
    from datetime import datetime, timezone
    from fireclaw_core.memory.embodied_memory import EmbodiedMemoryStore
    from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
    from fireclaw_core.mission.mission_runtime import (
        MissionRuntimePaths,
        build_mission_agent_from_paths,
    )

    registry_path = tmp_path / "robots.json"
    registry_path.write_text('{"robots": [{"robot_id": "r1", "base_url": "http://r1:8765"}]}')
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission_registry.create_mission(
        mission_id="mission-1",
        session_id=None,
        command="search",
        created_at="2026-07-24T10:00:00+00:00",
    )
    store = EmbodiedMemoryStore(tmp_path / "memory.jsonl")
    store.record_event(
        mission_id="mission-1",
        event_type="command",
        payload={"command": "search"},
        runtime_mode="real",
        source_type="operator",
        observed_at=datetime.now(timezone.utc).isoformat(),
        event_id="startup-event",
    )
    agent = build_mission_agent_from_paths(
        MissionRuntimePaths(
            robot_registry=registry_path,
            mission_registry=tmp_path / "missions.jsonl",
            mission_memory=tmp_path / "memory.jsonl",
            embodied_runtime_mode="real",
        ),
        operator_id="operator-1",
        role="operator",
    )
    assert agent.embodied_working_memory is not None
    assert agent.embodied_working_memory.count == 1
    assert agent.working_memory_hydration_report is not None
    assert agent.working_memory_hydration_report.added == 1
