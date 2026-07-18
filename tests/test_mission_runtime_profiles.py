from __future__ import annotations

import json
from pathlib import Path

from fireclaw_core.mission.mission_runtime import MissionRuntimePaths, build_mission_agent_from_paths


def _write_robot_registry(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps({"robots": entries}), encoding="utf-8")


def test_build_mission_agent_from_profile_paths(tmp_path: Path) -> None:
    """When robot_profiles is set, registry is built from TOML profiles."""
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "profile-robot"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/profile-robot"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".strip(),
        encoding="utf-8",
    )
    paths = MissionRuntimePaths(
        robot_registry=tmp_path / "unused-robots.json",
        mission_registry=tmp_path / "missions.jsonl",
        robot_profiles=(profile_path,),
    )

    agent = build_mission_agent_from_paths(paths, operator_id="op", role="operator")

    assert agent.registry.get("profile-robot") is not None
    assert agent.registry.get("profile-robot").base_url == "http://127.0.0.1:8765"


def test_build_mission_agent_falls_back_to_robots_json(tmp_path: Path) -> None:
    """When robot_profiles is empty, falls back to robot_registry JSON."""
    registry_path = tmp_path / "robots.json"
    _write_robot_registry(registry_path, [
        {"robot_id": "r1", "base_url": "http://r1:8765"},
    ])
    paths = MissionRuntimePaths(
        robot_registry=registry_path,
        mission_registry=tmp_path / "missions.jsonl",
    )

    agent = build_mission_agent_from_paths(paths, operator_id="op", role="operator")

    assert agent.registry.get("r1") is not None


def test_mission_runtime_paths_default_robot_profiles_is_empty_tuple() -> None:
    """Default robot_profiles is an empty tuple, not None."""
    paths = MissionRuntimePaths(
        robot_registry=Path("/dummy"),
        mission_registry=Path("/dummy"),
    )
    assert paths.robot_profiles == ()
