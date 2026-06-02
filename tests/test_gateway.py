from __future__ import annotations

import json
from pathlib import Path
import sys
from urllib import request

from fireclaw_core.gateway import FireClawGateway, GatewayConfig


def _json_request(base_url: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _write_high_risk_skill(skills_dir: Path) -> None:
    skills_dir.mkdir()
    (skills_dir / "smoke_entry.py").write_text(
        "import json\nprint(json.dumps({'ok': True, 'data': {'confirmed': True}}))\n",
        encoding="utf-8",
    )
    (skills_dir / "smoke_entry.skill.json").write_text(
        json.dumps(
            {
                "name": "smoke_entry",
                "description": "High-risk dry-run skill.",
                "runtime": "subprocess",
                "command": [sys.executable, "smoke_entry.py"],
                "dry_run_only": True,
                "risk_level": "high",
            }
        ),
        encoding="utf-8",
    )


def test_gateway_returns_health_and_state(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        health = _json_request(gateway.base_url, "GET", "/health")
        state = _json_request(gateway.base_url, "GET", "/state")
    finally:
        gateway.stop()

    assert health["status"] == "ok"
    assert health["robot_id"] == "robot-gateway"
    assert health["adapter"] == "simulator"
    assert state["robot_state"]["mode"] == "simulator"
    assert state["environment_state"]["reachable_floors"] == [1, 2, 3]


def test_gateway_runs_task_and_returns_recent_memory(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        result = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "去二楼救人", "session_id": "operator-a"},
        )
        recent = _json_request(
            gateway.base_url,
            "GET",
            "/memory/recent?session_id=operator-a&limit=3",
        )
        task = _json_request(gateway.base_url, "GET", f"/tasks/{result['task_id']}")
        events = _json_request(gateway.base_url, "GET", f"/tasks/{result['task_id']}/events")
        recent_events = _json_request(
            gateway.base_url,
            "GET",
            "/events/recent?session_id=operator-a&limit=20",
        )
    finally:
        gateway.stop()

    assert result["status"] == "succeeded"
    assert result["task_id"].startswith("task-")
    assert result["session"]["session_id"] == "operator-a"
    assert result["execution"]["steps"][0]["output"]["mode"] == "simulator"
    assert recent["records"][0]["command"] == "去二楼救人"
    assert task["result"]["task_id"] == result["task_id"]
    assert [event["type"] for event in events["events"]] == [
        "task.received",
        "task.planned",
        "safety.decided",
        "skill.started",
        "skill.attempted",
        "skill.succeeded",
        "skill.started",
        "skill.attempted",
        "skill.succeeded",
        "skill.started",
        "skill.attempted",
        "skill.succeeded",
        "skill.started",
        "skill.attempted",
        "skill.succeeded",
        "skill.started",
        "skill.attempted",
        "skill.succeeded",
        "task.completed",
    ]
    assert recent_events["events"][0]["type"] == "task.completed"
    assert events["events"][3]["payload"]["skill_name"] == "navigate_to_floor"
    assert events["events"][4]["payload"]["attempt_number"] == 1


def test_gateway_lists_skills(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        result = _json_request(gateway.base_url, "GET", "/skills")
    finally:
        gateway.stop()

    assert result["status"] == "skills"
    assert "navigate_to_floor" in [skill["name"] for skill in result["skills"]]


def test_gateway_confirms_pending_high_risk_skill(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_high_risk_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=str(skills_dir),
        )
    )
    gateway.start()
    try:
        pending = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "运行 smoke_entry", "session_id": "operator-a"},
        )
        confirmed = _json_request(
            gateway.base_url,
            "POST",
            "/confirm",
            {"session_id": "operator-a"},
        )
        pending_events = _json_request(gateway.base_url, "GET", f"/tasks/{pending['task_id']}/events")
        confirmed_events = _json_request(
            gateway.base_url,
            "GET",
            f"/tasks/{confirmed['task_id']}/events",
        )
    finally:
        gateway.stop()

    assert pending["status"] == "awaiting_confirmation"
    assert pending["task_id"].startswith("task-")
    assert pending["execution"] is None
    assert confirmed["status"] == "succeeded"
    assert confirmed["task_id"].startswith("task-")
    assert confirmed["confirmation"]["status"] == "confirmed"
    assert confirmed["execution"]["steps"][0]["skill_name"] == "smoke_entry"
    assert "confirmation.pending" in [event["type"] for event in pending_events["events"]]
    assert "confirmation.confirmed" in [event["type"] for event in confirmed_events["events"]]
