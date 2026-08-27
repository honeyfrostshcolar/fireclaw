import json
import sys
import time
from pathlib import Path

from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.agent.robot_registry import RobotRegistryEntry
from fireclaw_core.subagent.subagent_client import RobotSubagentClient


def _wait_for_result(client: RobotSubagentClient, entry: RobotRegistryEntry, task_id: str) -> dict:
    deadline = time.time() + 3
    while time.time() < deadline:
        trace = client.get_task_trace(entry, task_id)
        result = trace.get("result")
        if isinstance(result, dict):
            return result
        time.sleep(0.01)
    raise AssertionError(f"Task {task_id} did not finish before timeout.")


def test_robot_subagent_client_submits_task_and_reads_trace(tmp_path):
    entry = RobotRegistryEntry(robot_id="robot-1", base_url="")
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
        )
    )
    gateway.start()
    try:
        entry = RobotRegistryEntry(robot_id="robot-1", base_url=gateway.base_url)
        client = RobotSubagentClient()

        state = client.get_state(entry)
        submitted = client.submit_task(
            entry,
                command="去坐标 (2.0, 1.5) 救人",
                session_id="mission-1",
                dedupe_key="mission-1-robot-1-point-2-1.5",
        )
        result = _wait_for_result(client, entry, submitted["task_id"])
        trace = client.get_task_trace(entry, submitted["task_id"])
    finally:
        gateway.stop()

    assert state["robot_state"]["robot_id"] == "robot-1"
    assert submitted["status"] == "accepted"
    assert submitted["robot_id"] == "robot-1"
    assert result["status"] == "completed"
    assert trace["queue_record"]["status"] == "completed"


def test_robot_subagent_client_check_presence_online(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
        )
    )
    gateway.start()
    try:
        entry = RobotRegistryEntry(robot_id="robot-1", base_url=gateway.base_url)
        client = RobotSubagentClient()

        result = client.check_presence(entry)
    finally:
        gateway.stop()

    assert result["robot_id"] == "robot-1"
    assert result["online"] is True
    assert "last_seen_at" in result
    assert "state" in result


def test_robot_subagent_client_check_presence_offline():
    entry = RobotRegistryEntry(robot_id="robot-1", base_url="http://127.0.0.1:1")
    client = RobotSubagentClient(timeout_seconds=0.1)

    result = client.check_presence(entry)

    assert result["robot_id"] == "robot-1"
    assert result["online"] is False
    assert "error" in result


def test_robot_subagent_client_exposes_operator_status_and_recovery_routes():
    entry = RobotRegistryEntry(
        robot_id="robot-1",
        base_url="http://127.0.0.1:8765",
    )
    client = RobotSubagentClient()
    calls = []

    def request_json(method, base_url, path, payload=None):
        calls.append((method, base_url, path, payload))
        return {"status": "ok"}

    client._request_json = request_json

    client.get_health(entry)
    client.get_resource_admission(entry)
    client.request_resource_admission_recovery(
        entry,
        reason="operator inspected the scene",
        session_id="operator-1",
    )
    client.confirm_resource_admission_recovery(
        entry,
        request_id="recovery-1",
        confirmation_phrase="RECOVER robot-1 recovery-1",
    )

    assert calls == [
        ("GET", entry.base_url, "/health", None),
        ("GET", entry.base_url, "/resource-admission", None),
        (
            "POST",
            entry.base_url,
            "/resource-admission/recovery/request",
            {
                "reason": "operator inspected the scene",
                "session_id": "operator-1",
            },
        ),
        (
            "POST",
            entry.base_url,
            "/resource-admission/recovery/confirm",
            {
                "request_id": "recovery-1",
                "confirmation_phrase": "RECOVER robot-1 recovery-1",
            },
        ),
    ]


def test_robot_subagent_client_confirms_one_exact_task():
    entry = RobotRegistryEntry(
        robot_id="robot-1",
        base_url="http://127.0.0.1:8765",
    )
    client = RobotSubagentClient()
    calls = []

    def request_json(method, base_url, path, payload=None):
        calls.append((method, base_url, path, payload))
        return {"status": "accepted"}

    client._request_json = request_json

    result = client.confirm_task(
        entry,
        "task-1",
        session_id="mission-1",
    )

    assert calls == [
        (
            "POST",
            entry.base_url,
            "/confirm",
            {"task_id": "task-1", "session_id": "mission-1"},
        )
    ]
    assert result["task_id"] == "task-1"
    assert result["session_id"] == "mission-1"
    assert result["robot_id"] == "robot-1"


def test_robot_subagent_client_sends_structured_task_payload(tmp_path):
    from fireclaw_core.task.task_contract import StructuredRobotTask

    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
        )
    )
    gateway.start()
    try:
        entry = RobotRegistryEntry(
            robot_id="robot-1",
            base_url=gateway.base_url,
            capabilities=["victim_search"],
        )
        client = RobotSubagentClient()
        task = StructuredRobotTask(
            task_id="structured-1",
            task_type="search",
            target={"floor": 2},
            required_skills=["navigate_to_waypoint", "victim_search", "publish_operator_update"],
        )

        result = client.submit_task(entry, command="去2楼搜索受困人员", structured_task=task.to_dict())

        assert result["status"] in {"accepted", "completed", "require_confirmation"}
        trace = client.get_task_trace(entry, result["task_id"])
        assert trace["structured_task"]["task_id"] == "structured-1"
    finally:
        gateway.stop()


def test_robot_subagent_client_get_events(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
        )
    )
    gateway.start()
    try:
        entry = RobotRegistryEntry(robot_id="robot-1", base_url=gateway.base_url)
        client = RobotSubagentClient()

        submitted = client.submit_task(
            entry,
                command="去坐标 (2.0, 1.5) 救人",
            session_id="mission-1",
        )
        _wait_for_result(client, entry, submitted["task_id"])

        events = client.get_events(entry)
        assert isinstance(events, list)
        assert len(events) > 0
        assert events[0]["type"] == "task.completed"

        filtered = client.get_events(entry, task_id=submitted["task_id"])
        assert len(filtered) > 0
        for event in filtered:
            assert event["task_id"] == submitted["task_id"]

        limited = client.get_events(entry, limit=3)
        assert len(limited) <= 3
    finally:
        gateway.stop()


def test_robot_subagent_client_sends_auth_token_header(tmp_path):
    token = "test-api-token"
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            api_token=token,
        )
    )
    gateway.start()
    try:
        entry = RobotRegistryEntry(robot_id="robot-1", base_url=gateway.base_url)
        client_with_token = RobotSubagentClient(api_token=token)
        client_without_token = RobotSubagentClient()

        state = client_with_token.get_state(entry)
        assert state["robot_state"]["robot_id"] == "robot-1"

        submitted = client_with_token.submit_task(
            entry,
            command="去坐标 (2.0, 1.5) 救人",
            session_id="mission-1",
        )
        assert submitted["status"] == "accepted"
        result = _wait_for_result(client_with_token, entry, submitted["task_id"])
        assert result["status"] == "completed"

        unauthorized_result = client_without_token.get_state(entry)
        assert unauthorized_result.get("error") == "Unauthorized"
        assert unauthorized_result.get("http_status") == 401
    finally:
        gateway.stop()
