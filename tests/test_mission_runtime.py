from __future__ import annotations

import json
from pathlib import Path

from fireclaw_core.mission_runtime import MissionRuntimePaths, build_mission_agent_from_paths


def _write_robot_registry(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps({"robots": entries}), encoding="utf-8")


def test_build_mission_agent_wires_persistent_runtime_stores(tmp_path: Path):
    registry_path = tmp_path / "robots.json"
    _write_robot_registry(registry_path, [
        {"robot_id": "r1", "base_url": "http://r1:8765", "capabilities": ["search_for_victims"]},
    ])
    paths = MissionRuntimePaths(
        robot_registry=registry_path,
        mission_registry=tmp_path / "missions.jsonl",
        mission_memory=tmp_path / "memory.jsonl",
        memory_index=tmp_path / "memory.sqlite",
        task_registry=tmp_path / "tasks.jsonl",
        subagent_registry=tmp_path / "subagents.jsonl",
        session_lineage=tmp_path / "lineage.jsonl",
        task_flow=tmp_path / "flows.jsonl",
        approvals=tmp_path / "approvals.jsonl",
    )

    agent = build_mission_agent_from_paths(paths, operator_id="op-a", role="operator")

    assert agent.mission_registry is not None
    assert agent.mission_memory is not None
    assert agent.memory_retriever is not None
    assert agent.task_registry is not None
    assert agent.subagent_registry is not None
    assert agent._session_lineage_store is not None
    assert agent._task_flow_store is not None
    assert agent.approval_store is not None
