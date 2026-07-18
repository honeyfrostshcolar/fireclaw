from __future__ import annotations

import subprocess

from fireclaw_core.gateway.config import load_config


def test_gateway_cli_exposes_robot_agent_flags():
    completed = subprocess.run(
        [".venv/bin/python", "-m", "fireclaw_core", "serve", "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--robot-agent" in completed.stdout
    assert "--robot-agent-planner" in completed.stdout
    assert "--robot-agent-provider-base-url" in completed.stdout
    assert "--robot-agent-provider-api-key" in completed.stdout
    assert "--robot-agent-model" in completed.stdout


def test_robot_gateway_cli_exposes_config_flag():
    completed = subprocess.run(
        [".venv/bin/python", "-m", "fireclaw_core", "robot-gateway", "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--config" in completed.stdout
    assert "--robot-agent" in completed.stdout
    assert "--robot-agent-planner" in completed.stdout


def test_gateway_package_module_cli_exposes_config_flag():
    completed = subprocess.run(
        [".venv/bin/python", "-m", "fireclaw_core.gateway", "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--config" in completed.stdout
    assert "--robot-agent" in completed.stdout


def test_config_loads_robot_gateway_settings_and_reuses_provider(tmp_path):
    config_path = tmp_path / "fireclaw.toml"
    config_path.write_text(
        """
[provider]
base_url = "https://example.invalid/v1"
api_key = "secret"
model = "mimo-v2.5"

[robot_gateway]
host = "127.0.0.1"
port = 8765
adapter = "simulator"
robot_id = "debug-robot-1"
memory_path = "data/debug-sim/robot-memory.jsonl"
event_path = "data/debug-sim/robot-events.jsonl"
task_queue_path = "data/debug-sim/robot-tasks.jsonl"
workspace_skills_dir = "skills"
dry_run = true
available_sensors = ["thermal_camera"]

[robot_agent]
enabled = true
planner = "llm"
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg["robot_gateway_host"] == "127.0.0.1"
    assert cfg["robot_gateway_port"] == 8765
    assert cfg["robot_gateway_adapter"] == "simulator"
    assert cfg["robot_gateway_robot_id"] == "debug-robot-1"
    assert cfg["robot_gateway_memory_path"] == "data/debug-sim/robot-memory.jsonl"
    assert cfg["robot_gateway_event_path"] == "data/debug-sim/robot-events.jsonl"
    assert cfg["robot_gateway_task_queue_path"] == "data/debug-sim/robot-tasks.jsonl"
    assert cfg["robot_gateway_workspace_skills_dir"] == "skills"
    assert cfg["robot_gateway_dry_run"] is True
    assert cfg["robot_gateway_available_sensors"] == ["thermal_camera"]
    assert cfg["robot_agent_provider_base_url"] == "https://example.invalid/v1"
    assert cfg["robot_agent_provider_api_key"] == "secret"
    assert cfg["robot_agent_model"] == "mimo-v2.5"


def test_config_loads_robot_gateway_profile_path(tmp_path):
    config_path = tmp_path / "fireclaw.toml"
    config_path.write_text(
        """
[robot_gateway]
profile_path = "examples/robot_profiles/gazebo_turtlebot3.toml"
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg["robot_gateway_profile_path"] == "examples/robot_profiles/gazebo_turtlebot3.toml"
