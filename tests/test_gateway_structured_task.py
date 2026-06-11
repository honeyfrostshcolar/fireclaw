# tests/test_gateway_structured_task.py
from __future__ import annotations

import json
import time
from urllib import request

from fireclaw_core.gateway import FireClawGateway, GatewayConfig


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
