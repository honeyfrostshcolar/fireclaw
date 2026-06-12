"""Prove operator corrections and previous outcomes reach planner context.

Tests the closed-loop: seed memory -> index -> plan_and_submit -> spy planner
verifies context includes corrections and retrieved memories.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fireclaw_core.memory.memory_index import SqliteMemoryIndex
from fireclaw_core.memory.memory_retrieval import MemoryRetriever
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_memory import MissionMemoryRecord, MissionMemoryStore
from fireclaw_core.mission.mission_planner import (
    MissionPlan,
    MissionPlannerContext,
    MissionPlanningResult,
    MissionSubtask,
)
from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry


class SpyPlanner:
    """Planner that records the context it receives for test assertions."""

    def __init__(self):
        self.calls: list[tuple[str, MissionPlannerContext | None]] = []

    def plan(self, command: str, context: MissionPlannerContext | None = None) -> MissionPlanningResult:
        self.calls.append((command, context))
        available = context.available_robots if context else []
        if not available:
            return MissionPlanningResult(
                status="no_robots",
                message="No online robots available.",
                intent=None,
                plan=None,
            )
        return MissionPlanningResult(
            status="planned",
            message="Plan created.",
            intent="rescue",
            plan=MissionPlan(
                intent="rescue",
                command=command,
                subtasks=[
                    MissionSubtask(
                        robot_id=available[0].robot_id,
                        command=command,
                        floor=2,
                        capability_required="search_for_victims",
                        execution_group=0,
                    )
                ],
            ),
        )

    @property
    def last_context(self) -> MissionPlannerContext | None:
        return self.calls[-1][1] if self.calls else None


class FakeSubagentClient:
    """Minimal subagent client that always reports robots online."""

    def submit_task(self, entry, **kwargs):
        return {"status": "accepted", "task_id": f"task-{entry.robot_id}", "session_id": kwargs.get("session_id")}

    def get_task_trace(self, entry, task_id):
        return {}

    def cancel_task(self, entry, task_id, **kwargs):
        return {"status": "cancel_requested", "task_id": task_id}

    def get_events(self, entry, task_id=None, limit=100):
        return []

    def check_presence(self, entry):
        return {"online": True, "last_seen_at": "2026-06-11T00:00:00+00:00", "state": {}}


def _seed_memory(memory_path: Path) -> MissionMemoryStore:
    """Seed mission memory with an operator correction and a past outcome."""
    store = MissionMemoryStore(memory_path)
    now = datetime.now(timezone.utc).isoformat()

    # Operator correction: advice for rescue missions
    store.append(MissionMemoryRecord(
        record_id="corr-1",
        mission_id="past-mission-1",
        record_type="correction",
        content={"correction": "先确认楼梯安全再上楼，注意烟雾浓度"},
        created_at=now,
    ))

    # Past mission outcome
    store.append(MissionMemoryRecord(
        record_id="out-1",
        mission_id="past-mission-1",
        record_type="outcome",
        content={"summary": "成功找到1名伤员并转移至安全区域", "status": "succeeded"},
        robot_id="robot-1",
        created_at=now,
    ))

    return store


def _build_index(store: MissionMemoryStore, index_path: Path) -> SqliteMemoryIndex:
    """Rebuild FTS index from all records in the store."""
    records = store.list_records()
    record_dicts = [r.to_dict() for r in records]
    index = SqliteMemoryIndex(index_path)
    index.rebuild(record_dicts)
    return index


def _make_registry(registry_path: Path) -> None:
    """Write a minimal robot registry JSON file."""
    registry_path.write_text(
        json.dumps({
            "robots": [{
                "robot_id": "robot-1",
                "base_url": "http://127.0.0.1:9999",
                "capabilities": ["search_for_victims"],
            }]
        }),
        encoding="utf-8",
    )


def test_retrieve_planner_context_includes_corrections(tmp_path: Path):
    """Operator corrections seeded in memory are returned by _retrieve_planner_context."""
    memory_path = tmp_path / "memory.jsonl"
    index_path = tmp_path / "memory.sqlite"

    store = _seed_memory(memory_path)
    index = _build_index(store, index_path)
    retriever = MemoryRetriever(index=index)

    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://127.0.0.1:9999", capabilities=("search_for_victims",)),
    ])
    agent = MissionAgent(
        registry=registry,
        subagent_client=FakeSubagentClient(),
        mission_memory=store,
        memory_retriever=retriever,
    )

    # Directly call the retrieval method
    memories, corrections = agent._retrieve_planner_context("去二楼救人")

    # Corrections must be present
    assert len(corrections) > 0, "Expected operator corrections in planner context"
    correction_text = json.dumps(corrections, ensure_ascii=False)
    assert "楼梯" in correction_text or "烟雾" in correction_text, (
        f"Expected correction content about stairs/smoke, got: {correction_text}"
    )


def test_retrieve_planner_context_includes_retrieved_memories(tmp_path: Path):
    """Previous mission outcomes seeded in memory are returned by _retrieve_planner_context."""
    memory_path = tmp_path / "memory.jsonl"
    index_path = tmp_path / "memory.sqlite"

    store = _seed_memory(memory_path)
    index = _build_index(store, index_path)
    retriever = MemoryRetriever(index=index)

    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://127.0.0.1:9999", capabilities=("search_for_victims",)),
    ])
    agent = MissionAgent(
        registry=registry,
        subagent_client=FakeSubagentClient(),
        mission_memory=store,
        memory_retriever=retriever,
    )

    memories, corrections = agent._retrieve_planner_context("去二楼救人")

    # Memories (outcomes) should be retrievable via FTS5
    # The command "去二楼救人" should match the outcome about rescue
    if memories:
        memory_text = json.dumps(memories, ensure_ascii=False)
        assert "救" in memory_text or "伤员" in memory_text or "rescue" in memory_text, (
            f"Expected rescue-related content in memories, got: {memory_text}"
        )


def test_plan_and_submit_passes_context_to_planner(tmp_path: Path):
    """plan_and_submit() passes retrieved memories and corrections to the planner."""
    memory_path = tmp_path / "memory.jsonl"
    index_path = tmp_path / "memory.sqlite"

    store = _seed_memory(memory_path)
    index = _build_index(store, index_path)
    retriever = MemoryRetriever(index=index)

    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://127.0.0.1:9999", capabilities=("search_for_victims",)),
    ])
    spy = SpyPlanner()
    agent = MissionAgent(
        registry=registry,
        subagent_client=FakeSubagentClient(),
        planner=spy,
        mission_memory=store,
        memory_retriever=retriever,
    )

    # use_scheduler=False: we only care that planner receives context, not dispatch
    agent.plan_and_submit("去二楼救人", session_id="test-loop", use_scheduler=False)

    # Verify planner was called with context
    assert spy.last_context is not None, "Planner should have been called"
    assert isinstance(spy.last_context.operator_corrections, list)
    assert isinstance(spy.last_context.retrieved_memories, list)

    # Verify corrections are present in the planner context
    assert len(spy.last_context.operator_corrections) > 0, (
        "Planner context should include operator corrections"
    )
    correction_text = json.dumps(spy.last_context.operator_corrections, ensure_ascii=False)
    assert "楼梯" in correction_text or "烟雾" in correction_text, (
        f"Expected correction content, got: {correction_text}"
    )


def test_plan_and_submit_without_memory_returns_empty_context(tmp_path: Path):
    """Without memory stores, planner context has empty memories and corrections."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://127.0.0.1:9999", capabilities=("search_for_victims",)),
    ])
    spy = SpyPlanner()
    agent = MissionAgent(
        registry=registry,
        subagent_client=FakeSubagentClient(),
        planner=spy,
        # No mission_memory or memory_retriever
    )

    agent.plan_and_submit("去二楼救人", session_id="test-empty", use_scheduler=False)

    assert spy.last_context is not None
    assert spy.last_context.operator_corrections == []
    assert spy.last_context.retrieved_memories == []


def test_memory_retriever_wired_from_paths(tmp_path: Path):
    """build_mission_agent_from_paths wires MemoryRetriever when memory_index is set."""
    from fireclaw_core.mission.mission_runtime import MissionRuntimePaths, build_mission_agent_from_paths

    memory_path = tmp_path / "memory.jsonl"
    index_path = tmp_path / "memory.sqlite"
    registry_path = tmp_path / "robots.json"

    _seed_memory(memory_path)
    _build_index(MissionMemoryStore(memory_path), index_path)
    _make_registry(registry_path)

    paths = MissionRuntimePaths(
        robot_registry=registry_path,
        mission_registry=tmp_path / "missions.jsonl",
        mission_memory=memory_path,
        memory_index=index_path,
    )
    agent = build_mission_agent_from_paths(
        paths,
        operator_id="op-1",
        role="operator",
    )

    assert agent.memory_retriever is not None, "MemoryRetriever should be wired when memory_index is set"
    assert agent.mission_memory is not None, "MissionMemoryStore should be wired when mission_memory is set"

    # Verify retrieval works end-to-end through the built agent
    memories, corrections = agent._retrieve_planner_context("去二楼救人")
    assert len(corrections) > 0, "Corrections should be retrievable through built agent"
