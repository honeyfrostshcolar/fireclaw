import json

import pytest

from fireclaw_core.robot_registry import RobotRegistryEntry, load_robot_registry


def test_load_robot_registry_parses_entries(tmp_path):
    config_path = tmp_path / "robots.json"
    config_path.write_text(
        json.dumps(
            {
                "robots": [
                    {
                        "robot_id": "robot-1",
                        "base_url": "http://127.0.0.1:8765",
                        "capabilities": ["navigate", "search_for_victims"],
                        "zone": "building-a",
                    },
                    {
                        "robot_id": "robot-2",
                        "base_url": "http://127.0.0.1:8766/",
                        "enabled": False,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    registry = load_robot_registry(config_path)

    assert registry.get("robot-1") == RobotRegistryEntry(
        robot_id="robot-1",
        base_url="http://127.0.0.1:8765",
        capabilities=("navigate", "search_for_victims"),
        zone="building-a",
        enabled=True,
    )
    assert registry.get("robot-2").base_url == "http://127.0.0.1:8766"
    assert [entry.robot_id for entry in registry.enabled_entries()] == ["robot-1"]


def test_load_robot_registry_rejects_missing_required_fields(tmp_path):
    config_path = tmp_path / "robots.json"
    config_path.write_text(json.dumps({"robots": [{"robot_id": "robot-1"}]}), encoding="utf-8")

    with pytest.raises(ValueError, match="base_url"):
        load_robot_registry(config_path)


def test_load_robot_registry_rejects_duplicate_robot_ids(tmp_path):
    config_path = tmp_path / "robots.json"
    config_path.write_text(
        json.dumps(
            {
                "robots": [
                    {"robot_id": "robot-1", "base_url": "http://127.0.0.1:8765"},
                    {"robot_id": "robot-1", "base_url": "http://127.0.0.1:8766"},
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate robot_id"):
        load_robot_registry(config_path)
