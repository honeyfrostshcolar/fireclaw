from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from fireclaw_core.mission.mission_planner import MissionPlan, MissionPlanningResult, MissionSubtask
from fireclaw_core.mission.mission_planning_audit import GuardDecision, JsonlMissionPlanningAuditSink, MissionPlanningAuditRecord
from fireclaw_core.mission.mission_runtime import MissionRuntimePaths, build_mission_agent_from_paths


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


class FakeRuntimeSubagentClient:
    def __init__(self):
        self.calls = []

    def check_presence(self, entry):
        return {
            "robot_id": entry.robot_id,
            "online": True,
            "last_seen_at": "2026-07-05T00:00:00+00:00",
            "state": {},
        }

    def submit_task(self, entry, **kwargs):
        self.calls.append((entry, kwargs))
        return {
            "status": "accepted",
            "task_id": f"task-{entry.robot_id}",
            "session_id": kwargs.get("session_id"),
            "robot_id": entry.robot_id,
        }


def _mission_planning_audit_record(command="去二楼搜索"):
    return MissionPlanningAuditRecord(
        command=command,
        available_robots=[
            {"robot_id": "r1", "capabilities": ["search_for_victims"], "enabled": True, "zone": None}
        ],
        tool_schema={"type": "function"},
        llm_tool_call={"id": "call-1", "name": "create_mission_plan", "arguments": {"intent": "search"}},
        decisions=[
            GuardDecision(
                layer="parser",
                status="allow",
                reason="mission_plan_parsed",
                message="LLM mission plan parsed.",
            )
        ],
        final_status="planned",
        final_message="planned",
        created_at="2026-07-05T00:00:00+00:00",
    )


def test_build_mission_agent_wires_mission_planning_audit_sink(tmp_path: Path):
    registry_path = tmp_path / "robots.json"
    _write_robot_registry(registry_path, [
        {"robot_id": "r1", "base_url": "http://r1:8765", "capabilities": ["search_for_victims"]},
    ])
    paths = MissionRuntimePaths(
        robot_registry=registry_path,
        mission_registry=tmp_path / "missions.jsonl",
        mission_planning_audit=tmp_path / "mission-planning-audit.jsonl",
    )

    agent = build_mission_agent_from_paths(paths, operator_id="op-a", role="operator")

    assert isinstance(agent.mission_planning_audit_sink, JsonlMissionPlanningAuditSink)
    assert agent.mission_planning_audit_sink.path == tmp_path / "mission-planning-audit.jsonl"


def test_runtime_mission_agent_persists_planning_audit_on_plan_and_submit(tmp_path: Path):
    registry_path = tmp_path / "robots.json"
    _write_robot_registry(registry_path, [
        {"robot_id": "r1", "base_url": "http://r1:8765", "capabilities": ["search_for_victims"]},
    ])
    planner = MagicMock()
    planner.plan.return_value = MissionPlanningResult(
        status="planned",
        message="planned",
        intent="search",
        plan=MissionPlan(
            intent="search",
            command="去二楼搜索",
            subtasks=[
                MissionSubtask(
                    robot_id="r1",
                    command="去2楼搜索受困人员",
                    floor=2,
                    capability_required="search_for_victims",
                )
            ],
        ),
        audit_record=_mission_planning_audit_record(),
    )
    paths = MissionRuntimePaths(
        robot_registry=registry_path,
        mission_registry=tmp_path / "missions.jsonl",
        mission_planning_audit=tmp_path / "mission-planning-audit.jsonl",
    )
    agent = build_mission_agent_from_paths(paths, operator_id="op-a", role="operator", planner=planner)
    agent.subagent_client = FakeRuntimeSubagentClient()

    result = agent.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    records = JsonlMissionPlanningAuditSink(tmp_path / "mission-planning-audit.jsonl").list_records()
    assert len(records) == 1
    assert records[0].mission_id == "mission-1"
    assert records[0].decisions[-1].reason == "mission_plan_valid"
