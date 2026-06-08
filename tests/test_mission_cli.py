import json
import subprocess
import sys
import time
from pathlib import Path

from fireclaw_core.gateway import FireClawGateway, GatewayConfig


def _write_slow_policy_skill(skills_dir: Path) -> None:
    skills_dir.mkdir()
    (skills_dir / "slow_policy.py").write_text(
        "import json, time\n"
        "time.sleep(10)\n"
        "print(json.dumps({'ok': True, 'data': {'policy': 'slow'}}))\n",
        encoding="utf-8",
    )
    (skills_dir / "slow_policy.skill.json").write_text(
        json.dumps(
            {
                "name": "slow_policy",
                "description": "Slow policy skill used to test mission cancellation.",
                "runtime": "subprocess",
                "command": [sys.executable, "slow_policy.py"],
                "timeout_seconds": 15,
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


def _wait_for_cli_trace(mission_registry_path, robot_registry_path, mission_id):
    deadline = time.time() + 3
    while time.time() < deadline:
        completed = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "trace",
                mission_id,
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
            ],
            check=False,
            cwd=".",
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout)
        if result.get("status") == "succeeded":
            return result
        time.sleep(0.01)
    raise AssertionError(f"Mission {mission_id} did not finish before timeout.")


def test_mission_cli_submit_subtask_records_mission(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "robot-memory.jsonl"),
            event_path=str(tmp_path / "robot-events.jsonl"),
            task_queue_path=str(tmp_path / "robot-tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        robot_registry_path = tmp_path / "robots.json"
        mission_registry_path = tmp_path / "missions.jsonl"
        robot_registry_path.write_text(
            json.dumps(
                {
                    "robots": [
                        {
                            "robot_id": "robot-1",
                            "base_url": gateway.base_url,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "submit-subtask",
                "--robot",
                "robot-1",
                "--command",
                "去二楼救人",
                "--session-id",
                "mission-cli-1",
                "--dedupe-key",
                "mission-cli-1-robot-1-floor-2",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
            ],
            check=True,
            cwd=".",
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout)
    finally:
        gateway.stop()

    assert result["status"] == "accepted"
    assert result["mission_id"] == "mission-cli-1"
    assert result["robot_id"] == "robot-1"
    assert result["task_id"].startswith("task-")
    assert mission_registry_path.exists()


def test_mission_cli_trace_aggregates_robot_subagent_trace(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "robot-memory.jsonl"),
            event_path=str(tmp_path / "robot-events.jsonl"),
            task_queue_path=str(tmp_path / "robot-tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        robot_registry_path = tmp_path / "robots.json"
        mission_registry_path = tmp_path / "missions.jsonl"
        robot_registry_path.write_text(
            json.dumps({"robots": [{"robot_id": "robot-1", "base_url": gateway.base_url}]}),
            encoding="utf-8",
        )
        subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "submit-subtask",
                "--robot",
                "robot-1",
                "--command",
                "去二楼救人",
                "--session-id",
                "mission-cli-2",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
            ],
            check=True,
            cwd=".",
            text=True,
            capture_output=True,
        )

        trace = _wait_for_cli_trace(mission_registry_path, robot_registry_path, "mission-cli-2")
    finally:
        gateway.stop()

    assert trace["mission_id"] == "mission-cli-2"
    assert trace["status"] == "succeeded"
    assert trace["subtasks"][0]["robot_id"] == "robot-1"
    assert trace["subtasks"][0]["robot_trace"]["result"]["status"] == "succeeded"


def test_mission_cli_cancel_requests_robot_subagent_cancellation(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_slow_policy_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="robot-1",
            memory_path=str(tmp_path / "robot-memory.jsonl"),
            event_path=str(tmp_path / "robot-events.jsonl"),
            task_queue_path=str(tmp_path / "robot-tasks.jsonl"),
            workspace_skills_dir=str(skills_dir),
        )
    )
    gateway.start()
    try:
        robot_registry_path = tmp_path / "robots.json"
        mission_registry_path = tmp_path / "missions.jsonl"
        robot_registry_path.write_text(
            json.dumps({"robots": [{"robot_id": "robot-1", "base_url": gateway.base_url}]}),
            encoding="utf-8",
        )
        subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "submit-subtask",
                "--robot",
                "robot-1",
                "--command",
                "去二楼救人 使用 slow_policy",
                "--session-id",
                "mission-cli-cancel",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
            ],
            check=True,
            cwd=".",
            text=True,
            capture_output=True,
        )

        completed = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "cancel",
                "mission-cli-cancel",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
            ],
            check=True,
            cwd=".",
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout)
    finally:
        gateway.stop()

    assert result["status"] == "cancel_requested"
    assert result["cancelled_subtask_count"] == 1
    assert result["subtasks"][0]["robot_id"] == "robot-1"
    assert result["subtasks"][0]["status"] == "cancel_requested"


def test_mission_cli_plan_mission_submits_subtasks(tmp_path):
    gateway1 = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "robot1-memory.jsonl"),
            event_path=str(tmp_path / "robot1-events.jsonl"),
            task_queue_path=str(tmp_path / "robot1-tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway2 = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-2",
            memory_path=str(tmp_path / "robot2-memory.jsonl"),
            event_path=str(tmp_path / "robot2-events.jsonl"),
            task_queue_path=str(tmp_path / "robot2-tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway1.start()
    gateway2.start()
    try:
        robot_registry_path = tmp_path / "robots.json"
        mission_registry_path = tmp_path / "missions.jsonl"
        robot_registry_path.write_text(
            json.dumps(
                {
                    "robots": [
                        {"robot_id": "robot-1", "base_url": gateway1.base_url, "capabilities": ["search_for_victims"]},
                        {"robot_id": "robot-2", "base_url": gateway2.base_url, "capabilities": ["search_for_victims"]},
                    ]
                }
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "plan-mission",
                "--command",
                "去二楼和三楼搜索受困人员",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
            ],
            check=True,
            cwd=".",
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout)
    finally:
        gateway1.stop()
        gateway2.stop()

    assert result["status"] == "planned"
    assert result["intent"] == "search"
    assert len(result["subtask_results"]) == 2
    robot_ids = {r["robot_id"] for r in result["subtask_results"]}
    assert robot_ids == {"robot-1", "robot-2"}


def test_mission_cli_rejects_submit_without_mission_scope(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "robot-memory.jsonl"),
            event_path=str(tmp_path / "robot-events.jsonl"),
            task_queue_path=str(tmp_path / "robot-tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        robot_registry_path = tmp_path / "robots.json"
        mission_registry_path = tmp_path / "missions.jsonl"
        robot_registry_path.write_text(
            json.dumps({"robots": [{"robot_id": "robot-1", "base_url": gateway.base_url}]}),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "submit-subtask",
                "--robot",
                "robot-1",
                "--command",
                "去二楼救人",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
                "--operator-id",
                "test-observer",
                "--role",
                "observer",
            ],
            check=False,
            cwd=".",
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout)
    finally:
        gateway.stop()

    assert result["status"] == "denied"
    assert "mission.submit" in result["message"]
