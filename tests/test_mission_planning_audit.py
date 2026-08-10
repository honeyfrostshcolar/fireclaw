from __future__ import annotations

import json

from fireclaw_core.agent.robot_registry import RobotRegistryEntry
from fireclaw_core.mission.mission_planning_audit import (
    GuardDecision,
    JsonlMissionPlanningAuditSink,
    MissionPlanningAuditRecord,
    append_guard_decision,
    build_available_robot_snapshot,
)


def test_guard_decision_serializes_stable_fields():
    decision = GuardDecision(
        layer="preflight",
        status="block",
        reason="no_available_robots",
        message="No available robots for mission planning.",
        details={"available_robot_count": 0},
    )

    assert decision.to_dict() == {
        "layer": "preflight",
        "status": "block",
        "reason": "no_available_robots",
        "message": "No available robots for mission planning.",
        "details": {"available_robot_count": 0},
    }


def test_audit_record_serializes_robot_snapshot_and_decisions():
    record = MissionPlanningAuditRecord(
        command="去二楼救人",
        available_robots=[
            {
                "robot_id": "gazebo_turtlebot3",
                "capabilities": ["victim_search"],
                "enabled": True,
                "zone": "training",
            }
        ],
        tool_schema={"type": "function"},
        llm_tool_call={"name": "create_mission_plan", "arguments": {"intent": "search"}},
        decisions=[
            GuardDecision(
                layer="parser",
                status="allow",
                reason="mission_plan_parsed",
                message="LLM mission plan parsed.",
            )
        ],
        final_status="planned",
        final_message="ready",
        created_at="2026-07-04T00:00:00+00:00",
        mission_id="mission-1",
    )

    data = record.to_dict()

    assert data["command"] == "去二楼救人"
    assert data["available_robots"][0]["robot_id"] == "gazebo_turtlebot3"
    assert data["tool_schema"] == {"type": "function"}
    assert data["llm_tool_call"]["name"] == "create_mission_plan"
    assert data["decisions"][0]["reason"] == "mission_plan_parsed"
    assert data["final_status"] == "planned"
    assert data["mission_id"] == "mission-1"


def test_build_available_robot_snapshot_is_serializable():
    snapshot = build_available_robot_snapshot(
        [
            RobotRegistryEntry(
                robot_id="r1",
                base_url="http://r1:8765",
                capabilities=("victim_search", "patrol"),
                zone=None,
                enabled=True,
            )
        ]
    )

    assert snapshot == [
        {
            "robot_id": "r1",
            "capabilities": ["victim_search", "patrol"],
            "enabled": True,
            "zone": None,
        }
    ]


def test_append_guard_decision_returns_updated_record_without_mutating_original():
    original = MissionPlanningAuditRecord(
        command="cmd",
        available_robots=[],
        tool_schema=None,
        llm_tool_call=None,
        decisions=[],
        final_status="error",
        final_message="blocked",
        created_at="2026-07-04T00:00:00+00:00",
    )
    decision = GuardDecision(
        layer="validator",
        status="block",
        reason="mission_plan_invalid",
        message="Mission plan failed deterministic validation.",
        details={"errors": ["Robot r2 is not registered."]},
    )

    updated = append_guard_decision(
        original,
        decision,
        final_status="blocked",
        final_message="Mission plan failed deterministic validation.",
    )

    assert original.decisions == []
    assert updated.decisions == [decision]
    assert updated.final_status == "blocked"
    assert updated.final_message == "Mission plan failed deterministic validation."


def test_jsonl_mission_planning_audit_sink_appends_records(tmp_path):
    path = tmp_path / "audit" / "mission-planning-audit.jsonl"
    sink = JsonlMissionPlanningAuditSink(path)
    record = MissionPlanningAuditRecord(
        command="去二楼搜索",
        available_robots=[{"robot_id": "r1", "capabilities": ["victim_search"], "enabled": True, "zone": None}],
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
        mission_id="mission-1",
    )

    sink.record(record)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["mission_id"] == "mission-1"
    assert data["command"] == "去二楼搜索"
    assert data["decisions"][0]["reason"] == "mission_plan_parsed"


def test_jsonl_mission_planning_audit_sink_reads_records_with_filters(tmp_path):
    path = tmp_path / "mission-planning-audit.jsonl"
    sink = JsonlMissionPlanningAuditSink(path)
    first = MissionPlanningAuditRecord(
        command="cmd-1",
        available_robots=[],
        tool_schema=None,
        llm_tool_call=None,
        decisions=[],
        final_status="error",
        final_message="blocked",
        created_at="2026-07-05T00:00:00+00:00",
        mission_id="mission-1",
    )
    second = MissionPlanningAuditRecord(
        command="cmd-2",
        available_robots=[],
        tool_schema=None,
        llm_tool_call=None,
        decisions=[
            GuardDecision(
                layer="validator",
                status="allow",
                reason="mission_plan_valid",
                message="Mission plan passed deterministic validation.",
            )
        ],
        final_status="planned",
        final_message="planned",
        created_at="2026-07-05T00:00:01+00:00",
        mission_id="mission-2",
    )
    sink.record(first)
    sink.record(second)

    records = sink.list_records(mission_id="mission-2")

    assert len(records) == 1
    assert records[0].mission_id == "mission-2"
    assert records[0].decisions[0].reason == "mission_plan_valid"
