from __future__ import annotations

import subprocess
import sys

from fireclaw_core.gateway.config import load_config


def test_gateway_cli_exposes_robot_agent_flags():
    completed = subprocess.run(
        [sys.executable, "-m", "fireclaw_core", "serve", "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--robot-agent" in completed.stdout
    assert "--robot-agent-planner" in completed.stdout
    assert "--robot-agent-provider-base-url" in completed.stdout
    assert "--robot-agent-provider-api-key" in completed.stdout
    assert "--robot-agent-model" in completed.stdout
    assert "--catalog" in completed.stdout
    assert "--robot-agent-catalog" in completed.stdout


def test_robot_gateway_cli_exposes_config_flag():
    completed = subprocess.run(
        [sys.executable, "-m", "fireclaw_core", "robot-gateway", "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--config" in completed.stdout
    assert "--robot-agent" in completed.stdout
    assert "--robot-agent-planner" in completed.stdout
    assert "--robot-agent-catalog" in completed.stdout


def test_gateway_package_module_cli_exposes_config_flag():
    completed = subprocess.run(
        [sys.executable, "-m", "fireclaw_core.gateway", "--help"],
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
catalog = "models.json"

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
    assert cfg["model_catalog_path"] == "models.json"
    assert cfg["robot_agent_model_catalog_path"] == "models.json"


def test_config_allows_robot_agent_catalog_override(tmp_path):
    config_path = tmp_path / "fireclaw.toml"
    config_path.write_text(
        """
[provider]
catalog = "central-models.json"

[robot_agent.provider]
catalog = "robot-models.json"
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg["model_catalog_path"] == "central-models.json"
    assert cfg["robot_agent_model_catalog_path"] == "robot-models.json"


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


def test_config_loads_mission_external_knowledge_rag(tmp_path):
    config_path = tmp_path / "fireclaw.toml"
    config_path.write_text(
        """
[mission]
robot_profiles = ["robot.toml"]
embodied_runtime_mode = "simulation"

[mission.knowledge_rag]
backend = "hybrid"
bm25_index_dir = "/srv/fireclaw/knowledge/bm25"
dense_index_dir = "/srv/fireclaw/knowledge/dense"
embedding_provider = "bge-m3"
embedding_model_path = "/srv/fireclaw/models/bge-m3"
device = "cuda"
candidate_multiplier = 4
rrf_k = 40
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg["embodied_runtime_mode"] == "simulation"
    assert cfg["knowledge_rag_backend"] == "hybrid"
    assert cfg["knowledge_rag_bm25_index_dir"] == "/srv/fireclaw/knowledge/bm25"
    assert cfg["knowledge_rag_dense_index_dir"] == "/srv/fireclaw/knowledge/dense"
    assert cfg["knowledge_rag_embedding_provider"] == "bge-m3"
    assert cfg["knowledge_rag_embedding_model_path"] == "/srv/fireclaw/models/bge-m3"
    assert cfg["knowledge_rag_device"] == "cuda"
    assert cfg["knowledge_rag_candidate_multiplier"] == 4
    assert cfg["knowledge_rag_rrf_k"] == 40
