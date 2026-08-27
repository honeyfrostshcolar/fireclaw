from __future__ import annotations

import json
import time
from pathlib import Path
from urllib import request

from fireclaw_core.gateway.serve import start_server
from fireclaw_core.mission.mission_gateway_client import MissionGatewayClient
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


def _write_active_profile(tmp_path: Path) -> Path:
    profile = tmp_path / "active-profile.toml"
    profile.write_text(
        f"""
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
data_dir = "robot-data"
capabilities = ["navigation", "patrol"]
enabled_skills = ["navigate_to_point"]
llm_exposed_skills = ["navigate_to_point"]

[capability_skill_chains]
navigation = ["navigate_to_point"]
patrol = ["navigate_to_point"]

[deployment]
id = "robot-1-deployment"
mode = "simulation"
output_root = "{tmp_path / 'deployments'}"

[deployment.ros1]
distro = "noetic"
setup_files = ["/opt/ros/noetic/setup.bash"]

[plugins]
paths = ["{tmp_path / 'extensions'}"]
selected = ["fireclaw.navigation.move-base"]
""",
        encoding="utf-8",
    )
    return profile


def test_start_server_creates_data_dir_and_robots_json(tmp_path: Path):
    data_dir = tmp_path / "data"
    gw = start_server(data_dir=data_dir, port=0, planner_type="deterministic")
    try:
        assert data_dir.exists()
        assert (data_dir / "robots.json").exists()
        robots = json.loads((data_dir / "robots.json").read_text())
        assert "robots" in robots
        assert (
            gw.mission_agent.mission_deliberation_runtime.limits.timeout_seconds
            == 180.0
        )
    finally:
        gw.stop()


def test_start_server_separates_provider_and_mission_planning_timeouts(tmp_path: Path):
    gw = start_server(
        data_dir=tmp_path / "data",
        port=0,
        planner_type="deterministic",
        provider_timeout_seconds=12.0,
        mission_planning_timeout_seconds=37.5,
    )
    try:
        assert (
            gw.mission_agent.mission_deliberation_runtime.limits.timeout_seconds
            == 37.5
        )
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


def test_start_server_wires_inbound_and_outbound_gateway_tokens(tmp_path: Path):
    data_dir = tmp_path / "data"
    gw = start_server(
        data_dir=data_dir,
        port=0,
        planner_type="deterministic",
        api_token="mission-secret",
        robot_gateway_api_token="robot-secret",
    )
    try:
        assert gw.config.api_token == "mission-secret"
        assert gw.subagent_client.api_token == "robot-secret"
        assert gw.mission_agent.subagent_client is gw.subagent_client
    finally:
        gw.stop()


def test_start_server_submit_and_trace(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(RobotSubagentClient, "check_presence", _fake_check_presence)
    monkeypatch.setattr(RobotSubagentClient, "submit_task", _fake_submit_task)
    monkeypatch.setattr(RobotSubagentClient, "get_task_trace", _fake_get_task_trace)
    data_dir = tmp_path / "data"
    active_profile = _write_active_profile(tmp_path)
    gw = start_server(
        data_dir=data_dir,
        port=0,
        planner_type="deterministic",
        embodied_runtime_mode="simulation",
        active_profile_path=active_profile,
    )
    try:
        client = MissionGatewayClient(gw.base_url)
        preview = client.preview_mission(
            "前往坐标 (2.0, 1.5) 巡逻",
            target_robot="robot-1",
        )
        result = client.confirm_plan(preview, operator_confirmed=True)
        assert preview["status"] == "preview_ready"
        assert result["status"] == "accepted"
        assert "mission_id" in result

        mission_id = result["mission_id"]
        deadline = time.monotonic() + 5.0
        trace = {}
        while time.monotonic() < deadline:
            trace = client.get_mission_trace(mission_id)
            if trace.get("status") in {"succeeded", "completed", "failed"}:
                break
            time.sleep(0.05)
        assert trace["mission_id"] == mission_id
        assert trace["status"] in {"succeeded", "completed"}
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
