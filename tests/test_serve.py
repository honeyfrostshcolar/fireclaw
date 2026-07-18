from __future__ import annotations

import json
from pathlib import Path
from urllib import request

from fireclaw_core.gateway.serve import start_server
from fireclaw_core.mission.mission_planning_audit import JsonlMissionPlanningAuditSink
from fireclaw_core.subagent.subagent_client import RobotSubagentClient


def _fake_check_presence(self, entry):
    """Return online=True for all robots so tests work without real robots."""
    return {
        "robot_id": entry.robot_id,
        "online": True,
        "last_seen_at": "2026-06-11T00:00:00+00:00",
        "state": {},
    }


def _fake_submit_task(self, entry, **kwargs):
    """Accept tasks without requiring a real robot gateway."""
    return {
        "status": "accepted",
        "task_id": f"task-{entry.robot_id}",
        "session_id": kwargs.get("session_id"),
        "robot_id": entry.robot_id,
    }


def _fake_get_task_trace(self, entry, task_id):
    """Return a terminal trace for missions submitted to the fake robot."""
    return {
        "status": "succeeded",
        "task_id": task_id,
        "robot_id": entry.robot_id,
        "result": {"status": "succeeded"},
    }


def test_start_server_creates_data_dir_and_robots_json(tmp_path: Path):
    data_dir = tmp_path / "data"
    gw = start_server(data_dir=data_dir, port=0, planner_type="deterministic")
    try:
        assert data_dir.exists()
        assert (data_dir / "robots.json").exists()
        robots = json.loads((data_dir / "robots.json").read_text())
        assert "robots" in robots
    finally:
        gw.stop()


def test_start_server_with_deterministic_planner(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(RobotSubagentClient, "check_presence", _fake_check_presence)
    data_dir = tmp_path / "data"
    gw = start_server(data_dir=data_dir, port=0, planner_type="deterministic")
    try:
        url = gw.base_url
        resp = request.urlopen(f"{url}/fleet/state", timeout=5)
        assert resp.status == 200
    finally:
        gw.stop()


def test_start_server_submit_and_trace(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(RobotSubagentClient, "check_presence", _fake_check_presence)
    monkeypatch.setattr(RobotSubagentClient, "submit_task", _fake_submit_task)
    monkeypatch.setattr(RobotSubagentClient, "get_task_trace", _fake_get_task_trace)
    data_dir = tmp_path / "data"
    gw = start_server(data_dir=data_dir, port=0, planner_type="deterministic")
    try:
        url = gw.base_url
        # use_scheduler=False avoids the scheduler's poll loop which would
        # time out because no real robot is running to complete subtasks.
        body = json.dumps({
            "command": "去二楼搜救受困人员",
            "use_scheduler": False,
        }).encode()
        req = request.Request(
            f"{url}/missions",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "X-Operator-Scopes": "admin"},
        )
        resp = request.urlopen(req, timeout=5)
        result = json.loads(resp.read())
        assert "mission_id" in result

        mission_id = result["mission_id"]
        resp = request.urlopen(f"{url}/missions/{mission_id}/trace", timeout=5)
        trace = json.loads(resp.read())
        assert trace["mission_id"] == mission_id
    finally:
        gw.stop()


def test_start_server_wires_default_mission_planning_audit_sink(tmp_path: Path):
    data_dir = tmp_path / "data"
    gw = start_server(data_dir=data_dir, port=0, planner_type="deterministic")
    try:
        sink = gw.mission_agent.mission_planning_audit_sink
        assert isinstance(sink, JsonlMissionPlanningAuditSink)
        assert sink.path == data_dir / "mission-planning-audit.jsonl"
    finally:
        gw.stop()
