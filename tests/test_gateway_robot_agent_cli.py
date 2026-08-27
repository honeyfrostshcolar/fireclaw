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
    assert "--provider-timeout-seconds" in completed.stdout
    assert "--mission-group-timeout-seconds" in completed.stdout
    assert "--robot-agent-model" in completed.stdout
    assert "--catalog" in completed.stdout
    assert "--robot-agent-catalog" in completed.stdout
    assert "--api-token" in completed.stdout
    assert "--robot-gateway-api-token" in completed.stdout
    assert "--runtime-root" in completed.stdout


def test_robot_gateway_cli_exposes_config_flag():
    completed = subprocess.run(
        [sys.executable, "-m", "fireclaw_core", "robot-gateway", "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--config" in completed.stdout
    assert "--robot-agent-provider-timeout-seconds" in completed.stdout
    assert "--robot-agent-loop-timeout-seconds" in completed.stdout
    assert "--robot-agent" in completed.stdout
    assert "--robot-agent-planner" in completed.stdout
    assert "--robot-agent-catalog" in completed.stdout
    assert "--api-token" in completed.stdout
    assert "--runtime-root" in completed.stdout


def test_robot_gateway_runtime_uses_defaults_for_omitted_config(
    monkeypatch,
) -> None:
    from fireclaw_core.gateway import gateway as gateway_module

    captured = {}

    class FakeGateway:
        def __init__(self, config):
            captured["config"] = config
            self.config = config
            self.base_url = f"http://{config.host}:{config.port}"

        def serve_forever(self):
            captured["served"] = True

    monkeypatch.setattr(gateway_module, "FireClawGateway", FakeGateway)

    assert gateway_module._run_robot_gateway({}, object()) == 0

    config = captured["config"]
    assert config.host == "127.0.0.1"
    assert config.port == 8765
    assert config.adapter == "dry-run"
    assert config.robot_id == "fireclaw-gateway"
    assert config.default_session_id == "default"
    assert config.max_active_execution_tasks == 1
    assert config.dry_run is True
    assert config.robot_agent_enabled is False
    assert config.robot_agent_planner == "deterministic"
    assert config.robot_agent_provider_timeout_seconds == 60.0
    assert config.robot_agent_provider_thinking is None
    assert config.robot_agent_loop_timeout_seconds == 600.0
    assert captured["served"] is True


def test_robot_gateway_cli_handles_keyboard_interrupt_cleanly(
    monkeypatch,
    capsys,
) -> None:
    from fireclaw_core.gateway import gateway as gateway_module

    class FakeGateway:
        def __init__(self, config):
            self.config = config
            self.base_url = f"http://{config.host}:{config.port}"

        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(gateway_module, "FireClawGateway", FakeGateway)

    assert gateway_module._run_robot_gateway({}, object()) == 0
    assert "Robot Gateway stopped." in capsys.readouterr().out


def test_robot_gateway_wires_provider_and_outer_loop_timeouts(
    monkeypatch,
    tmp_path,
) -> None:
    from fireclaw_core.gateway import gateway as gateway_module

    captured = {}
    provider_runtime = object()

    def fake_build_provider_runtime(**kwargs):
        captured.update(kwargs)
        return provider_runtime

    monkeypatch.setattr(
        gateway_module,
        "build_provider_runtime",
        fake_build_provider_runtime,
    )
    gateway = gateway_module.FireClawGateway(
        gateway_module.GatewayConfig(
            robot_agent_enabled=True,
            robot_agent_planner="llm",
            robot_agent_provider_base_url="https://example.invalid/v1",
            robot_agent_provider_api_key="test-key",
            robot_agent_provider_timeout_seconds=60.0,
            robot_agent_provider_thinking=False,
            robot_agent_model="test-model",
            robot_agent_loop_timeout_seconds=600.0,
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            runtime_state_path=str(tmp_path / "runtime.sqlite3"),
        )
    )

    assert captured["provider_timeout_seconds"] == 60.0
    assert captured["provider_thinking"] is False
    assert gateway.robot_agent_runtime is not None
    assert gateway.robot_agent_runtime.limits.timeout_seconds == 600.0


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
timeout_seconds = 60
thinking = false

[robot_gateway]
host = "127.0.0.1"
port = 8765
adapter = "simulator"
robot_id = "debug-robot-1"
memory_path = "data/debug-sim/robot-memory.jsonl"
event_path = "data/debug-sim/robot-events.jsonl"
task_queue_path = "data/debug-sim/robot-tasks.jsonl"
runtime_state_path = "data/debug-sim/robot-runtime.sqlite3"
dry_run = true
available_sensors = ["thermal_camera"]

[robot_agent]
enabled = true
planner = "llm"
loop_timeout_seconds = 600

[mission]
group_timeout_seconds = 720
planning_timeout_seconds = 180
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
    assert (
        cfg["robot_gateway_runtime_state_path"]
        == "data/debug-sim/robot-runtime.sqlite3"
    )
    assert cfg["robot_gateway_dry_run"] is True
    assert cfg["robot_gateway_available_sensors"] == ["thermal_camera"]
    assert cfg["robot_agent_provider_base_url"] == "https://example.invalid/v1"
    assert cfg["robot_agent_provider_api_key"] == "secret"
    assert cfg["provider_timeout_seconds"] == 60
    assert cfg["provider_thinking"] is False
    assert cfg["robot_agent_provider_timeout_seconds"] == 60
    assert cfg["robot_agent_provider_thinking"] is False
    assert cfg["robot_agent_model"] == "mimo-v2.5"
    assert cfg["model_catalog_path"] == "models.json"
    assert cfg["robot_agent_model_catalog_path"] == "models.json"
    assert cfg["robot_agent_loop_timeout_seconds"] == 600
    assert cfg["mission_group_timeout_seconds"] == 720
    assert cfg["mission_planning_timeout_seconds"] == 180


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


def test_config_allows_robot_agent_thinking_override(tmp_path):
    config_path = tmp_path / "fireclaw.toml"
    config_path.write_text(
        """
[provider]
thinking = false

[robot_agent.provider]
thinking = true
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg["provider_thinking"] is False
    assert cfg["robot_agent_provider_thinking"] is True


def test_config_loads_gateway_server_and_outbound_robot_tokens(tmp_path):
    config_path = tmp_path / "fireclaw.toml"
    config_path.write_text(
        """
[server]
api_token = "mission-secret"

[mission]
robot_gateway_api_token = "outbound-robot-secret"

[robot_gateway]
api_token = "robot-listener-secret"
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg["api_token"] == "mission-secret"
    assert cfg["robot_gateway_api_token"] == "robot-listener-secret"
    assert cfg["robot_gateway_client_api_token"] == "outbound-robot-secret"


def test_config_reuses_robot_listener_token_for_outbound_client(tmp_path):
    config_path = tmp_path / "fireclaw.toml"
    config_path.write_text(
        """
[robot_gateway]
api_token = "shared-robot-secret"
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg["robot_gateway_client_api_token"] == "shared-robot-secret"


def test_config_loads_gateway_tls_and_outbound_robot_trust(tmp_path):
    config_path = tmp_path / "fireclaw.toml"
    config_path.write_text(
        """
[server.tls]
enabled = true
cert_file = "mission.crt"
key_file = "mission.key"
ca_file = "ca.crt"
require_client_cert = true

[mission.robot_gateway_tls]
ca_file = "robot-ca.crt"
cert_file = "mission-client.crt"
key_file = "mission-client.key"

[robot_gateway.tls]
enabled = true
cert_file = "robot.crt"
key_file = "robot.key"
ca_file = "ca.crt"
require_client_cert = true
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg["tls_enabled"] is True
    assert cfg["tls_cert_file"] == "mission.crt"
    assert cfg["tls_key_file"] == "mission.key"
    assert cfg["tls_ca_file"] == "ca.crt"
    assert cfg["tls_require_client_cert"] is True
    assert cfg["robot_gateway_client_tls_ca_file"] == "robot-ca.crt"
    assert cfg["robot_gateway_client_tls_cert_file"] == "mission-client.crt"
    assert cfg["robot_gateway_client_tls_key_file"] == "mission-client.key"
    assert cfg["robot_gateway_tls_enabled"] is True
    assert cfg["robot_gateway_tls_cert_file"] == "robot.crt"
    assert cfg["robot_gateway_tls_key_file"] == "robot.key"
    assert cfg["robot_gateway_tls_ca_file"] == "ca.crt"
    assert cfg["robot_gateway_tls_require_client_cert"] is True


def test_config_preserves_structured_deployment_policy(tmp_path):
    config_path = tmp_path / "fireclaw.toml"
    config_path.write_text(
        """
[deployment]
mode = "simulation"

[deployment.tools.mission_agent]
allow = ["group:computer"]
deny = ["group:credential_access"]

[deployment.sandbox.mission_agent]
enabled = true
backend = "docker"
workspace_root = "data/mission-agent-workspace"
image = "fireclaw-agent-sim:local"
network = "none"
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg["deployment"]["mode"] == "simulation"
    assert cfg["deployment"]["tools"]["mission_agent"]["allow"] == [
        "group:computer"
    ]
    assert cfg["deployment"]["sandbox"]["mission_agent"]["enabled"] is True
    assert (
        cfg["deployment"]["sandbox"]["mission_agent"]["workspace_root"]
        == "data/mission-agent-workspace"
    )


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
