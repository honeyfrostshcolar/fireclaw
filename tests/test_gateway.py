from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from urllib import request
from urllib.error import HTTPError

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


def _json_error_request(base_url: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    try:
        return 200, _json_request(base_url, method, path, payload)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


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


def _write_slow_policy_skill(skills_dir: Path) -> None:
    skills_dir.mkdir()
    (skills_dir / "slow_policy.py").write_text(
        "import json, time\n"
        "time.sleep(1)\n"
        "print(json.dumps({'ok': True, 'data': {'policy': 'slow'}}))\n",
        encoding="utf-8",
    )
    (skills_dir / "slow_policy.skill.json").write_text(
        json.dumps(
            {
                "name": "slow_policy",
                "description": "Slow policy skill used to test cooperative cancellation.",
                "runtime": "subprocess",
                "command": [sys.executable, "slow_policy.py"],
                "timeout_seconds": 2,
                "dry_run_only": True,
                "risk_level": "low",
                "input_schema": {
                    "type": "object",
                    "additionalProperties": True,
                },
            }
        ),
        encoding="utf-8",
    )


def _wait_for_task_result(gateway: FireClawGateway, task_id: str, timeout_seconds: float = 2.0) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        trace = gateway.task_trace(task_id)
        result = trace.get("result")
        if isinstance(result, dict):
            return result
        time.sleep(0.01)
    raise AssertionError(f"Task {task_id} did not finish before timeout.")


def _wait_for_event_type(gateway: FireClawGateway, task_id: str, event_type: str, timeout_seconds: float = 2.0) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for event in gateway.events.events_for_task(task_id):
            if event.get("type") == event_type:
                return event
        time.sleep(0.01)
    raise AssertionError(f"Task {task_id} did not emit {event_type} before timeout.")


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
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "去二楼救人", "session_id": "operator-a"},
        )
        result = _wait_for_task_result(gateway, accepted["task_id"])
        recent = _json_request(
            gateway.base_url,
            "GET",
            "/memory/recent?session_id=operator-a&limit=3",
        )
        task = _json_request(gateway.base_url, "GET", f"/tasks/{accepted['task_id']}")
        events = _json_request(gateway.base_url, "GET", f"/tasks/{accepted['task_id']}/events")
        recent_events = _json_request(
            gateway.base_url,
            "GET",
            "/events/recent?session_id=operator-a&limit=20",
        )
    finally:
        gateway.stop()

    assert accepted["status"] == "accepted"
    assert accepted["task_id"].startswith("task-")
    assert accepted["session_id"] == "operator-a"
    assert result["status"] == "succeeded"
    assert result["task_id"] == accepted["task_id"]
    assert result["session"]["session_id"] == "operator-a"
    assert result["execution"]["steps"][0]["output"]["mode"] == "simulator"
    assert recent["records"][0]["command"] == "去二楼救人"
    assert task["result"]["task_id"] == result["task_id"]
    assert task["state"]["task"]["status"] == "succeeded"
    assert task["state"]["task"]["skill_count"] == 5
    assert task["state"]["task"]["action_count"] == 5
    assert task["state"]["skills"][0]["skill_name"] == "navigate_to_floor"
    assert task["state"]["skills"][0]["action_ids"][0].startswith("action-")
    assert task["state"]["actions"][0]["action_type"] == "navigate_to_floor"
    assert task["state"]["actions"][0]["status"] == "succeeded"
    event_types = [event["type"] for event in events["events"]]
    assert event_types == [
        "task.received",
        "operator.identified",
        "control.decision",
        "task.planned",
        "safety.decided",
        "skill.started",
        "action.requested",
        "action.started",
        "action.succeeded",
        "skill.attempted",
        "skill.succeeded",
        "skill.started",
        "action.requested",
        "action.started",
        "action.succeeded",
        "skill.attempted",
        "skill.succeeded",
        "skill.started",
        "action.requested",
        "action.started",
        "action.succeeded",
        "skill.attempted",
        "skill.succeeded",
        "skill.started",
        "action.requested",
        "action.started",
        "action.succeeded",
        "skill.attempted",
        "skill.succeeded",
        "skill.started",
        "action.requested",
        "action.started",
        "action.succeeded",
        "skill.attempted",
        "skill.succeeded",
        "task.completed",
    ]
    first_action = next(event for event in events["events"] if event["type"] == "action.requested")
    assert first_action["payload"]["task_id"] == accepted["task_id"]
    assert first_action["payload"]["skill_name"] == "navigate_to_floor"
    assert recent_events["events"][0]["type"] == "task.completed"
    assert events["events"][5]["payload"]["skill_name"] == "navigate_to_floor"
    assert events["events"][9]["payload"]["attempt_number"] == 1


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


def test_gateway_records_operator_and_control_decision_for_task_submission(tmp_path):
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
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去二楼救人",
                "session_id": "operator-a",
                "operator": {
                    "operator_id": "op-1",
                    "display_name": "Operator One",
                    "role": "operator",
                },
            },
        )
        result = _wait_for_task_result(gateway, accepted["task_id"])
        events = _json_request(gateway.base_url, "GET", f"/tasks/{accepted['task_id']}/events")
    finally:
        gateway.stop()

    assert result["status"] == "succeeded"
    event_types = [event["type"] for event in events["events"]]
    assert event_types[:3] == ["task.received", "operator.identified", "control.decision"]
    operator_event = events["events"][1]
    decision_event = events["events"][2]
    assert operator_event["payload"]["operator_id"] == "op-1"
    assert operator_event["payload"]["role"] == "operator"
    assert decision_event["payload"]["status"] == "allow"
    assert decision_event["payload"]["action"] == "task.submit"


def test_gateway_sync_run_agent_still_returns_completed_result(tmp_path):
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

    result = gateway.run_agent("去二楼救人", session_id="operator-a")

    assert result["status"] == "succeeded"
    assert result["task_id"].startswith("task-")
    assert gateway.task_trace(result["task_id"])["result"]["status"] == "succeeded"


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
        pending_result = _wait_for_task_result(gateway, pending["task_id"])
        confirmed = _json_request(
            gateway.base_url,
            "POST",
            "/confirm",
            {"session_id": "operator-a"},
        )
        confirmed_result = _wait_for_task_result(gateway, confirmed["task_id"])
        pending_events = _json_request(gateway.base_url, "GET", f"/tasks/{pending['task_id']}/events")
        confirmed_events = _json_request(
            gateway.base_url,
            "GET",
            f"/tasks/{confirmed['task_id']}/events",
        )
    finally:
        gateway.stop()

    assert pending["status"] == "accepted"
    assert pending["task_id"].startswith("task-")
    assert pending_result["status"] == "awaiting_confirmation"
    assert pending_result["execution"] is None
    assert confirmed["status"] == "accepted"
    assert confirmed["task_id"].startswith("task-")
    assert confirmed_result["status"] == "succeeded"
    assert confirmed_result["confirmation"]["status"] == "confirmed"
    assert confirmed_result["execution"]["steps"][0]["skill_name"] == "smoke_entry"
    assert "confirmation.pending" in [event["type"] for event in pending_events["events"]]
    assert "confirmation.confirmed" in [event["type"] for event in confirmed_events["events"]]


def test_gateway_cancels_active_task_between_skills(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_slow_policy_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=str(skills_dir),
        )
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "去二楼救人 使用 slow_policy", "session_id": "operator-a"},
        )
        _wait_for_event_type(gateway, accepted["task_id"], "skill.started")
        started = time.monotonic()
        cancel = _json_request(gateway.base_url, "POST", f"/tasks/{accepted['task_id']}/cancel")
        result = _wait_for_task_result(gateway, accepted["task_id"])
        elapsed = time.monotonic() - started
        events = _json_request(gateway.base_url, "GET", f"/tasks/{accepted['task_id']}/events")
    finally:
        gateway.stop()

    event_types = [event["type"] for event in events["events"]]
    skill_names = [
        event["payload"].get("skill_name")
        for event in events["events"]
        if event["type"] == "skill.started"
    ]
    assert cancel["status"] == "cancel_requested"
    assert cancel["task_id"] == accepted["task_id"]
    assert result["status"] == "cancelled"
    assert result["message"] == "任务已取消。"
    assert elapsed < 0.8
    assert "task.cancel_requested" in event_types
    assert "task.cancelled" in event_types
    assert skill_names == ["slow_policy"]


def test_gateway_rejects_second_execution_task_when_robot_is_busy(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_slow_policy_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=str(skills_dir),
        )
    )
    gateway.start()
    try:
        first = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "去二楼救人 使用 slow_policy", "session_id": "operator-a"},
        )
        _wait_for_event_type(gateway, first["task_id"], "skill.started")
        status_code, busy = _json_error_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "去三楼救人", "session_id": "operator-b"},
        )
        state = _json_request(gateway.base_url, "GET", "/state")
        gateway.cancel_task(first["task_id"])
        _wait_for_task_result(gateway, first["task_id"])
    finally:
        gateway.stop()

    assert status_code == 409
    assert busy["status"] == "busy"
    assert busy["active_task_id"] == first["task_id"]
    assert busy["capacity"]["max_active_execution_tasks"] == 1
    assert state["task_capacity"]["active_execution_tasks"] == 1
    assert state["task_capacity"]["max_active_execution_tasks"] == 1
    assert state["active_tasks"][0]["task_id"] == first["task_id"]


def test_gateway_admin_emergency_stop_cancels_active_task_and_records_audit_events(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_slow_policy_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="mock-ros1",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=str(skills_dir),
        )
    )
    gateway.start()
    try:
        active = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "去二楼救人 使用 slow_policy", "session_id": "operator-a"},
        )
        _wait_for_event_type(gateway, active["task_id"], "skill.started")
        stopped = _json_request(
            gateway.base_url,
            "POST",
            "/emergency-stop",
            {
                "session_id": "operator-a",
                "reason": "smoke flashover risk",
                "operator": {"operator_id": "admin-1", "role": "admin"},
            },
        )
        result = _wait_for_task_result(gateway, active["task_id"])
        state = _json_request(gateway.base_url, "GET", "/state")
        recent_events = _json_request(gateway.base_url, "GET", "/events/recent?session_id=operator-a&limit=20")
        task_events = _json_request(gateway.base_url, "GET", f"/tasks/{active['task_id']}/events")
    finally:
        gateway.stop()

    assert stopped["status"] == "emergency_stopped"
    assert stopped["reason"] == "smoke flashover risk"
    assert stopped["cancelled_task_ids"] == [active["task_id"]]
    assert stopped["robot_result"]["status"] == "emergency_stopped"
    assert result["status"] == "cancelled"
    assert state["emergency_stop"]["active"] is True
    assert state["emergency_stop"]["reason"] == "smoke flashover risk"
    assert state["robot_state"]["online"] is False
    recent_types = [event["type"] for event in recent_events["events"]]
    task_types = [event["type"] for event in task_events["events"]]
    assert "emergency_stop.requested" in recent_types
    assert "emergency_stop.activated" in recent_types
    assert "task.cancel_requested" in task_types
    assert "task.cancelled" in task_types


def test_gateway_operator_emergency_stop_is_denied_without_cancelling_task(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_slow_policy_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="mock-ros1",
            robot_id="robot-gateway",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            workspace_skills_dir=str(skills_dir),
        )
    )
    gateway.start()
    try:
        active = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {"command": "去二楼救人 使用 slow_policy", "session_id": "operator-a"},
        )
        _wait_for_event_type(gateway, active["task_id"], "skill.started")
        status_code, denied = _json_error_request(
            gateway.base_url,
            "POST",
            "/emergency-stop",
            {
                "session_id": "operator-a",
                "reason": "operator attempted stop",
                "operator": {"operator_id": "op-1", "role": "operator"},
            },
        )
        running_trace = _json_request(gateway.base_url, "GET", f"/tasks/{active['task_id']}")
        state = _json_request(gateway.base_url, "GET", "/state")
        recent_events = _json_request(gateway.base_url, "GET", "/events/recent?session_id=operator-a&limit=20")
        gateway.cancel_task(active["task_id"])
        _wait_for_task_result(gateway, active["task_id"])
    finally:
        gateway.stop()

    assert status_code == 403
    assert denied["status"] == "denied"
    assert denied["control"]["status"] == "deny"
    assert running_trace["status"] == "running"
    assert state["emergency_stop"]["active"] is False
    assert state["robot_state"]["online"] is True
    assert "emergency_stop.denied" in [event["type"] for event in recent_events["events"]]
