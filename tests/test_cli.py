from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def _run_cli(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "fireclaw_core", *args],
        check=check,
        cwd=".",
        text=True,
        capture_output=True,
    )


def test_module_cli_runs_plugin_owned_point_navigation(tmp_path: Path) -> None:
    memory_path = tmp_path / "cli-memory.jsonl"

    completed = _run_cli(
        "去坐标 (2.0, 1.5)",
        "--memory-path",
        str(memory_path),
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "succeeded"
    assert result["planning"]["target_pose"] == {
        "frame_id": "map",
        "x": 2.0,
        "y": 1.5,
        "yaw": 0.0,
    }
    assert [step["skill_name"] for step in result["execution"]["steps"]] == [
        "navigate_to_point"
    ]
    assert memory_path.exists()


def test_module_cli_accepts_robot_id(tmp_path: Path) -> None:
    completed = _run_cli(
        "去坐标 (2.0, 1.5)",
        "--memory-path",
        str(tmp_path / "memory.jsonl"),
        "--robot-id",
        "robot-cli",
    )

    result = json.loads(completed.stdout)
    assert result["execution"]["steps"][0]["output"]["robot_id"] == "robot-cli"


def test_module_cli_accepts_simulator_adapter(tmp_path: Path) -> None:
    completed = _run_cli(
        "去坐标 (2.0, 1.5)",
        "--adapter",
        "simulator",
        "--memory-path",
        str(tmp_path / "memory.jsonl"),
        "--robot-id",
        "sim-cli",
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "succeeded"
    assert result["robot_state"]["mode"] == "simulator"
    assert result["execution"]["steps"][0]["output"]["mode"] == "simulator"
    assert result["execution"]["steps"][0]["output"]["frame_id"] == "map"


def test_module_cli_accepts_mock_ros1_as_state_adapter_only(tmp_path: Path) -> None:
    completed = _run_cli(
        "去坐标 (2.0, 1.5)",
        "--adapter",
        "mock-ros1",
        "--memory-path",
        str(tmp_path / "memory.jsonl"),
        "--robot-id",
        "ros1-cli",
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "succeeded"
    assert result["robot_state"]["mode"] == "mock_ros1"
    assert result["execution"]["steps"][0]["output"]["mode"] == "mock_ros1"
    assert result["execution"]["steps"][0]["output"]["goal_reached"] is True
    assert "ros1_name" not in result["execution"]["steps"][0]["output"]


def test_module_cli_accepts_transport_only_ros1_adapter_config(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "ros1.json"
    config_path.write_text(
        json.dumps({"robot_id": "real-ros1-cli"}),
        encoding="utf-8",
    )

    completed = _run_cli(
        "随便看看",
        "--adapter",
        "ros1",
        "--ros1-config",
        str(config_path),
        "--memory-path",
        str(tmp_path / "memory.jsonl"),
        "--robot-id",
        "ignored-cli",
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "clarify"
    assert result["robot_state"]["mode"] == "ros1"
    assert result["robot_state"]["robot_id"] == "real-ros1-cli"


def test_module_cli_accepts_session_id(tmp_path: Path) -> None:
    completed = _run_cli(
        "去坐标 (2.0, 1.5)",
        "--memory-path",
        str(tmp_path / "memory.jsonl"),
        "--session-id",
        "cli-session",
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "succeeded"
    assert result["session"]["session_id"] == "cli-session"
    assert result["session"]["turn_index"] == 1


def test_module_cli_treats_recall_as_successful_command(tmp_path: Path) -> None:
    memory_path = tmp_path / "cli-memory.jsonl"
    _run_cli(
        "去坐标 (2.0, 1.5)",
        "--memory-path",
        str(memory_path),
    )

    completed = _run_cli(
        "之前做过什么",
        "--memory-path",
        str(memory_path),
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "recalled"
    assert len(result["memory"]["records"]) == 1


def test_module_cli_lists_manifest_loaded_tools(tmp_path: Path) -> None:
    memory_path = tmp_path / "cli-memory.jsonl"

    completed = _run_cli(
        "你有哪些技能",
        "--memory-path",
        str(memory_path),
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "skills"
    assert [skill["name"] for skill in result["skills"]] == ["navigate_to_point"]
    assert result["skills"][0]["metadata"]["plugin_id"] == (
        "fireclaw.navigation.move-base"
    )
    assert not memory_path.exists()


def test_module_cli_direct_invocation_of_missing_tool_exits_nonzero(
    tmp_path: Path,
) -> None:
    completed = _run_cli(
        "运行 missing_tool",
        "--memory-path",
        str(tmp_path / "memory.jsonl"),
        check=False,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert result["status"] == "block"
    assert "Missing skill: missing_tool" in result["message"]


def test_module_cli_real_run_does_not_synthesize_navigation_backend(
    tmp_path: Path,
) -> None:
    completed = _run_cli(
        "去坐标 (2.0, 1.5)",
        "--real-run",
        "--memory-path",
        str(tmp_path / "memory.jsonl"),
        check=False,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert result["status"] == "block"
    assert "navigate_to_point" in result["message"]
