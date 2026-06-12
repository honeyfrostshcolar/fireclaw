# tests/test_gateway_structured_task.py
from __future__ import annotations

import json
import time
from urllib import request
from urllib.error import HTTPError

from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig


def _json_request(base_url: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "X-Operator-Scopes": "admin"},
    )
    with request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_task_done(base_url: str, task_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        trace = _json_request(base_url, "GET", f"/tasks/{task_id}")
        if trace.get("result") is not None:
            return trace
        time.sleep(0.05)
    return _json_request(base_url, "GET", f"/tasks/{task_id}")


def _events_for_task(base_url: str, task_id: str) -> list[dict]:
    body = _json_request(base_url, "GET", f"/events?task_id={task_id}")
    return body.get("events", [])


def test_gateway_accepts_structured_task_payload(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去2楼搜索受困人员",
                "structured_task": {
                    "task_id": "structured-1",
                    "task_type": "search",
                    "target": {"floor": 2},
                    "required_skills": ["navigate_to_floor", "search_for_victims", "report_status"],
                },
            },
        )
        trace = _wait_for_task_done(gateway.base_url, accepted["task_id"])

        assert trace["structured_task"]["task_id"] == "structured-1"
        assert trace["result"]["structured_task"]["task_type"] == "search"
    finally:
        gateway.stop()


def test_gateway_rejects_invalid_structured_task_payload(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        try:
            _json_request(
                gateway.base_url,
                "POST",
                "/tasks",
                {
                    "command": "去2楼搜索受困人员",
                    "structured_task": {
                        "task_id": "bad-structured-task",
                        "task_type": "search",
                        "target": {"floor": 2},
                        "required_skills": [],
                    },
                },
            )
        except HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 400
            assert body["status"] == "error"
            assert "required_skills" in body["message"].lower()
        else:
            raise AssertionError("Expected HTTP 400 for invalid structured_task")
    finally:
        gateway.stop()


def test_gateway_robot_agent_mode_emits_robot_agent_events(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
            robot_agent_enabled=True,
            robot_agent_planner="deterministic",
        )
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去2楼搜索受困人员",
                "structured_task": {
                    "task_id": "structured-robot-agent-1",
                    "task_type": "search",
                    "target": {"floor": 2},
                    "required_skills": ["navigate_to_floor", "report_status"],
                },
            },
        )
        trace = _wait_for_task_done(gateway.base_url, accepted["task_id"])
        events = _events_for_task(gateway.base_url, accepted["task_id"])

        assert trace["result"]["status"] in {"completed", "succeeded"}
        assert any(event.get("type") == "robot_agent.plan_requested" for event in events)
        assert any(event.get("type") == "robot_agent.plan_accepted" for event in events)
    finally:
        gateway.stop()


def test_gateway_robot_agent_mode_falls_back_for_high_risk_task(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
            robot_agent_enabled=True,
            robot_agent_planner="deterministic",
        )
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去2楼搜索受困人员",
                "structured_task": {
                    "task_id": "structured-robot-agent-high-risk",
                    "task_type": "search",
                    "target": {"floor": 2},
                    "required_skills": ["navigate_to_floor", "report_status"],
                    "risk_level": "high",
                },
            },
        )
        trace = _wait_for_task_done(gateway.base_url, accepted["task_id"])
        events = _events_for_task(gateway.base_url, accepted["task_id"])

        assert trace["result"]["status"] in {"clarify", "awaiting_confirmation"}
        assert any(event.get("type") == "robot_agent.policy_rejected" for event in events)
    finally:
        gateway.stop()
