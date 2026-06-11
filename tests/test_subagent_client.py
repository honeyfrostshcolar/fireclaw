import json
import sys
import time
from pathlib import Path

from fireclaw_core.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.robot_registry import RobotRegistryEntry
from fireclaw_core.subagent_client import RobotSubagentClient


def _write_slow_policy_skill(skills_dir: Path, release_path: Path | None = None) -> None:
    skills_dir.mkdir()
    release_literal = str(release_path or (skills_dir / "release")).replace("\\", "\\\\").replace("'", "\\'")
    (skills_dir / "slow_policy.py").write_text(
        "import json, pathlib, time\n"
        f"release = pathlib.Path('{release_literal}')\n"
        "deadline = time.monotonic() + 10\n"
        "while not release.exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.02)\n"
        "print(json.dumps({'ok': True, 'data': {'policy': 'slow'}}))\n",
        encoding="utf-8",
    )
    (skills_dir / "slow_policy.skill.json").write_text(
        json.dumps(
            {
                "name": "slow_policy",
                "description": "Slow policy skill used to test subagent cancellation.",
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
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        entry = RobotRegistryEntry(robot_id="robot-1", base_url=gateway.base_url)
        client = RobotSubagentClient()

        state = client.get_state(entry)
        submitted = client.submit_task(
            entry,
            command="去二楼救人",
            session_id="mission-1",
            dedupe_key="mission-1-robot-1-floor-2",
        )
        result = _wait_for_result(client, entry, submitted["task_id"])
        trace = client.get_task_trace(entry, submitted["task_id"])
    finally:
        gateway.stop()

    assert state["robot_state"]["robot_id"] == "robot-1"
    assert submitted["status"] == "accepted"
    assert submitted["robot_id"] == "robot-1"
    assert result["status"] == "succeeded"
    assert trace["queue_record"]["status"] == "completed"


def test_robot_subagent_client_cancels_task(tmp_path):
    skills_dir = tmp_path / "skills"
    release_path = tmp_path / "release-slow-policy"
    _write_slow_policy_skill(skills_dir, release_path)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="robot-2",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=str(skills_dir),
        )
    )
    gateway.start()
    try:
        entry = RobotRegistryEntry(robot_id="robot-2", base_url=gateway.base_url)
        client = RobotSubagentClient()
        submitted = client.submit_task(entry, command="slow_policy", session_id="mission-1")

        cancelled = client.cancel_task(entry, submitted["task_id"], operator={"scopes": ["task.cancel"]})
        release_path.write_text("release", encoding="utf-8")
    finally:
        release_path.write_text("release", encoding="utf-8")
        gateway.stop()

    assert cancelled["status"] == "cancel_requested"
    assert cancelled["robot_id"] == "robot-2"


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
            workspace_skills_dir=None,
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


def test_robot_subagent_client_sends_structured_task_payload(tmp_path):
    from fireclaw_core.task_contract import StructuredRobotTask

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
        entry = RobotRegistryEntry(
            robot_id="robot-1",
            base_url=gateway.base_url,
            capabilities=["search_for_victims"],
        )
        client = RobotSubagentClient()
        task = StructuredRobotTask(
            task_id="structured-1",
            task_type="search",
            target={"floor": 2},
            required_skills=["navigate_to_floor", "search_for_victims", "report_status"],
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
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        entry = RobotRegistryEntry(robot_id="robot-1", base_url=gateway.base_url)
        client = RobotSubagentClient()

        submitted = client.submit_task(
            entry,
            command="去二楼救人",
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
            workspace_skills_dir=None,
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

        submitted = client_with_token.submit_task(entry, command="去二楼救人", session_id="mission-1")
        assert submitted["status"] == "accepted"
        result = _wait_for_result(client_with_token, entry, submitted["task_id"])
        assert result["status"] == "succeeded"

        unauthorized_result = client_without_token.get_state(entry)
        assert unauthorized_result.get("error") == "Unauthorized"
        assert unauthorized_result.get("http_status") == 401
    finally:
        gateway.stop()
