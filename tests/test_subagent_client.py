import json
import sys
import time
from pathlib import Path

from fireclaw_core.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.robot_registry import RobotRegistryEntry
from fireclaw_core.subagent_client import RobotSubagentClient


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
    _write_slow_policy_skill(skills_dir)
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
    finally:
        gateway.stop()

    assert cancelled["status"] == "cancel_requested"
    assert cancelled["robot_id"] == "robot-2"
