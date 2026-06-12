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

    # [robot_agent]
    ra = raw.get("robot_agent", {})
    cfg["robot_agent_enabled"] = ra.get("enabled")
    cfg["robot_agent_planner"] = ra.get("planner")

    # [robot_agent.provider] — falls back to [provider] if absent
    ra_provider = ra.get("provider", {})
    cfg["robot_agent_provider_base_url"] = ra_provider.get("base_url") or cfg.get("provider_base_url")
    cfg["robot_agent_provider_api_key"] = ra_provider.get("api_key") or cfg.get("provider_api_key")
    cfg["robot_agent_model"] = ra_provider.get("model") or cfg.get("model")

    return cfg


def merge_config(config: dict[str, Any], cli_args: dict[str, Any]) -> dict[str, Any]:
    """Merge config file values with CLI args.  CLI args (non-None) win."""
    merged = dict(config)
    for key, value in cli_args.items():
        if value is not None:
            merged[key] = value
    return merged
