from __future__ import annotations

from pathlib import Path

from fireclaw_core.gateway.config import load_config
from fireclaw_core.infra import tomllib_compat as tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]
SIM_TEMPLATE = REPO_ROOT / "fireclaw.sim.example.toml"
REAL_TEMPLATE = REPO_ROOT / "fireclaw.real.example.toml"


def _read_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def test_simulation_template_is_explicit_and_live_gazebo_only() -> None:
    raw = _read_toml(SIM_TEMPLATE)
    config = load_config(SIM_TEMPLATE)

    assert raw["deployment"]["mode"] == "simulation"
    assert raw["mission"]["robot_profiles"] == ["fireclaw.sim.toml"]
    assert raw["mission"]["embodied_runtime_mode"] == "simulation"
    assert raw["robot_gateway"]["profile_path"] == "fireclaw.sim.toml"
    assert raw["robot_gateway"]["dry_run"] is False
    assert raw["robot_gateway"]["embodied_runtime_mode"] == "simulation"
    assert "gazebo_turtlebot3" in raw["robot_gateway"]["embodied_memory_path"]
    assert config["robot_gateway_embodied_runtime_mode"] == "simulation"
    assert "emergency_stop" in raw["robot"]["capabilities"]
    assert "emergency_stop" not in raw["robot"]["llm_exposed_skills"]
    assert raw["plugins"]["config"]["fireclaw.navigation.move-base"][
        "navigate_timeout_seconds"
    ] == 360


def test_real_template_is_distinct_and_fails_closed() -> None:
    raw = _read_toml(REAL_TEMPLATE)
    config = load_config(REAL_TEMPLATE)

    assert raw["deployment"]["mode"] == "real"
    assert raw["robot_gateway"]["profile_path"].endswith("fireclaw.real.toml")
    assert raw["robot_gateway"]["dry_run"] is True
    assert raw["robot_gateway"]["embodied_runtime_mode"] == "real"
    assert raw["robot_agent"]["enabled"] is False
    assert raw["hardware_safety_acceptance"]["profile_reviewed"] is False
    assert "fireclaw.safety.ros1-hardware" in raw["plugins"]["selected"]
    assert "api_key" not in raw["provider"]
    assert config["robot_gateway_embodied_runtime_mode"] == "real"


def test_private_mode_specific_configs_are_ignored() -> None:
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "/fireclaw.sim.toml" in ignore
    assert "/fireclaw.real.toml" in ignore
