"""TOML config file loading for FireClaw gateway.

Supports a ``fireclaw.toml`` config file so users don't have to pass
every flag on the command line.  CLI flags always override config values.

Config file structure::

    [server]
    host = "0.0.0.0"
    port = 8766
    data_dir = "./data"
    adapter = "simulator"
    ros1_config = "ros1.yaml"

    [planner]
    type = "deterministic"           # or "llm"

    [provider]
    base_url = "https://api.deepseek.com/v1"
    api_key = "sk-..."
    model = "deepseek-chat"

    [robot_agent]
    enabled = true
    planner = "llm"                  # or "deterministic"

    [robot_agent.provider]           # optional, falls back to [provider]
    base_url = "https://api.deepseek.com/v1"
    api_key = "sk-..."
    model = "deepseek-chat"

    [robot_gateway]
    host = "127.0.0.1"
    port = 8765
    adapter = "simulator"
    robot_id = "debug-robot-1"
    memory_path = "data/debug-sim/robot-memory.jsonl"
    event_path = "data/debug-sim/robot-events.jsonl"
    task_queue_path = "data/debug-sim/robot-tasks.jsonl"
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]


DEFAULT_CONFIG_PATHS = (
    Path("fireclaw.toml"),
    Path("fireclaw.yaml"),  # reserved for future yaml support
)


def find_config(path: str | Path | None = None) -> Path | None:
    """Return the config file path, or ``None`` if not found."""
    if path is not None:
        p = Path(path)
        if p.exists():
            return p
        return None
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.exists() and candidate.suffix == ".toml":
            return candidate
    return None


def load_config(path: Path) -> dict[str, Any]:
    """Load and return the TOML config as a flat dict suitable for merging."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    cfg: dict[str, Any] = {}

    # [server]
    server = raw.get("server", {})
    cfg["host"] = server.get("host")
    cfg["port"] = server.get("port")
    cfg["data_dir"] = server.get("data_dir")
    cfg["adapter"] = server.get("adapter")
    cfg["ros1_config"] = server.get("ros1_config")

    # [planner]
    planner = raw.get("planner", {})
    cfg["planner_type"] = planner.get("type")

    # [provider]
    provider = raw.get("provider", {})
    cfg["provider_base_url"] = provider.get("base_url")
    cfg["provider_api_key"] = provider.get("api_key")
    cfg["model"] = provider.get("model")
    cfg["model_catalog_path"] = provider.get("catalog")

    # [robot_agent]
    ra = raw.get("robot_agent", {})
    cfg["robot_agent_enabled"] = ra.get("enabled")
    cfg["robot_agent_planner"] = ra.get("planner")

    # [robot_agent.provider] — falls back to [provider] if absent
    ra_provider = ra.get("provider", {})
    cfg["robot_agent_provider_base_url"] = ra_provider.get("base_url") or cfg.get("provider_base_url")
    cfg["robot_agent_provider_api_key"] = ra_provider.get("api_key") or cfg.get("provider_api_key")
    cfg["robot_agent_model"] = ra_provider.get("model") or cfg.get("model")
    cfg["robot_agent_model_catalog_path"] = (
        ra_provider.get("catalog") or cfg.get("model_catalog_path")
    )

    # [robot_gateway]
    rg = raw.get("robot_gateway", {})
    cfg["robot_gateway_host"] = rg.get("host")
    cfg["robot_gateway_port"] = rg.get("port")
    cfg["robot_gateway_adapter"] = rg.get("adapter")
    cfg["robot_gateway_robot_id"] = rg.get("robot_id")
    cfg["robot_gateway_ros1_config"] = rg.get("ros1_config")
    cfg["robot_gateway_memory_path"] = rg.get("memory_path")
    cfg["robot_gateway_event_path"] = rg.get("event_path")
    cfg["robot_gateway_task_queue_path"] = rg.get("task_queue_path")
    cfg["robot_gateway_workspace_skills_dir"] = rg.get("workspace_skills_dir")
    cfg["robot_gateway_dry_run"] = rg.get("dry_run")
    cfg["robot_gateway_available_sensors"] = rg.get("available_sensors")
    cfg["robot_gateway_default_session_id"] = rg.get("default_session_id")
    cfg["robot_gateway_max_active_execution_tasks"] = rg.get("max_active_execution_tasks")
    cfg["robot_gateway_api_token"] = rg.get("api_token")
    cfg["robot_gateway_profile_path"] = rg.get("profile_path")
    cfg["robot_gateway_embodied_memory_path"] = rg.get("embodied_memory_path")
    cfg["robot_gateway_embodied_memory_index"] = rg.get("embodied_memory_index")
    cfg["robot_gateway_embodied_runtime_mode"] = rg.get("embodied_runtime_mode")

    # [mission]
    mission = raw.get("mission", {})
    cfg["mission_robot_profiles"] = mission.get("robot_profiles")
    cfg["embodied_runtime_mode"] = mission.get("embodied_runtime_mode")

    rag_fields = (
        "backend",
        "bm25_index_dir",
        "dense_index_dir",
        "generation_root",
        "embedding_provider",
        "embedding_model_path",
        "reranker_provider",
        "reranker_model_path",
        "device",
        "candidate_multiplier",
        "rrf_k",
    )
    for section_name, key_prefix in (
        ("memory_rag", "memory_rag"),
        ("knowledge_rag", "knowledge_rag"),
    ):
        section = mission.get(section_name, {})
        if not isinstance(section, dict):
            section = {}
        for field_name in rag_fields:
            flat_key = f"{key_prefix}_{field_name}"
            cfg[flat_key] = section.get(field_name, raw.get(flat_key))

    return cfg


def merge_config(config: dict[str, Any], cli_args: dict[str, Any]) -> dict[str, Any]:
    """Merge config file values with CLI args.  CLI args (non-None) win."""
    merged = dict(config)
    for key, value in cli_args.items():
        if value is not None:
            merged[key] = value
    return merged
