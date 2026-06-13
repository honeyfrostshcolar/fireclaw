from __future__ import annotations

from pathlib import Path

import pytest

from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig, resolve_gateway_config_with_profile


def test_gateway_config_applies_robot_profile_paths(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    data_dir = tmp_path / "robots" / "profile-robot"
    profile_path.write_text(
        f"""
[robot]
id = "profile-robot"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".strip(),
        encoding="utf-8",
    )

    config = GatewayConfig(
        adapter="dry-run",
        robot_id="cli-robot",
        memory_path=str(tmp_path / "old-memory.jsonl"),
        event_path=str(tmp_path / "old-events.jsonl"),
        task_queue_path=str(tmp_path / "old-tasks.jsonl"),
        robot_profile_path=str(profile_path),
    )

    resolved = resolve_gateway_config_with_profile(config)

    assert resolved.robot_id == "profile-robot"
    assert resolved.adapter == "simulator"
    assert resolved.memory_path.endswith("profile-robot/memory.jsonl")
    assert resolved.event_path.endswith("profile-robot/events.jsonl")
    assert resolved.task_queue_path.endswith("profile-robot/tasks.jsonl")


def test_gateway_config_without_profile_returns_unchanged(tmp_path: Path) -> None:
    config = GatewayConfig(
        adapter="dry-run",
        robot_id="cli-robot",
        memory_path=str(tmp_path / "memory.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        task_queue_path=str(tmp_path / "tasks.jsonl"),
    )

    resolved = resolve_gateway_config_with_profile(config)

    assert resolved.robot_id == "cli-robot"
    assert resolved.adapter == "dry-run"


def test_gateway_rejects_invalid_robot_profile_before_start(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "bad-robot"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/bad-robot"
capabilities = ["search_for_victims"]
enabled_skills = ["missing_skill"]
llm_exposed_skills = ["missing_skill"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="enabled skill 'missing_skill' is not registered"):
        FireClawGateway(GatewayConfig(
            port=0,
            robot_profile_path=str(profile_path),
            memory_path=str(tmp_path / "mem.jsonl"),
            event_path=str(tmp_path / "ev.jsonl"),
            task_queue_path=str(tmp_path / "tq.jsonl"),
            workspace_skills_dir=None,
        ))


def test_invalid_profile_does_not_create_gateway_store_files(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    data_dir = tmp_path / "robot-data"
    profile_path.write_text(
        f"""
[robot]
id = "bad-robot"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["missing_skill"]
llm_exposed_skills = ["missing_skill"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="enabled skill 'missing_skill' is not registered"):
        FireClawGateway(GatewayConfig(port=0, robot_profile_path=str(profile_path), workspace_skills_dir=None))

    assert not (data_dir / "memory.jsonl").exists()
    assert not (data_dir / "events.jsonl").exists()
    assert not (data_dir / "tasks.jsonl").exists()
