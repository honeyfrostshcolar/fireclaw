import json
import subprocess
import time

from fireclaw_core.gateway import FireClawGateway, GatewayConfig


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
