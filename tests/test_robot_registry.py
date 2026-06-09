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


def test_robot_registry_presence_tracking():
    from fireclaw_core.robot_registry import RobotRegistry
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765"),
    ])

    assert registry.is_online("r1") is False
    assert registry.get_last_seen_at("r1") is None

    registry.update_presence("r1", "2026-06-08T00:00:00+00:00")

    assert registry.is_online("r1") is True
    assert registry.is_online("r2") is False
    assert registry.get_last_seen_at("r1") == "2026-06-08T00:00:00+00:00"


def test_robot_registry_online_entries():
    from fireclaw_core.robot_registry import RobotRegistry
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765"),
        RobotRegistryEntry(robot_id="r3", base_url="http://r3:8765", enabled=False),
    ])
    registry.update_presence("r1", "2026-06-08T00:00:00+00:00")

    online = registry.online_entries()

    assert len(online) == 1
    assert online[0].robot_id == "r1"


# --- Heartbeat expiration / stale detection tests ---

def test_is_stale_returns_true_when_never_seen():
    from fireclaw_core.robot_registry import RobotRegistry
    registry = RobotRegistry(
        [RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765")],
        heartbeat_timeout_seconds=30.0,
    )
    assert registry.is_stale("r1") is True


def test_is_stale_returns_false_when_recently_seen():
    from datetime import datetime, timezone
    from fireclaw_core.robot_registry import RobotRegistry
    registry = RobotRegistry(
        [RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765")],
        heartbeat_timeout_seconds=30.0,
    )
    now = datetime.now(timezone.utc).isoformat()
    registry.update_presence("r1", now)
    assert registry.is_stale("r1") is False


def test_is_stale_returns_true_when_heartbeat_expired():
    from fireclaw_core.robot_registry import RobotRegistry
    registry = RobotRegistry(
        [RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765")],
        heartbeat_timeout_seconds=30.0,
    )
    registry.update_presence("r1", "2000-01-01T00:00:00+00:00")
    assert registry.is_stale("r1") is True


def test_is_stale_uses_custom_timeout():
    from datetime import datetime, timezone, timedelta
    from fireclaw_core.robot_registry import RobotRegistry
    registry = RobotRegistry(
        [RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765")],
        heartbeat_timeout_seconds=5.0,
    )
    # 10 seconds ago is stale with 5s timeout
    ten_sec_ago = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    registry.update_presence("r1", ten_sec_ago)
    assert registry.is_stale("r1") is True


def test_enabled_entries_excludes_stale_by_default():
    from fireclaw_core.robot_registry import RobotRegistry
    registry = RobotRegistry(
        [
            RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
            RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765"),
        ],
        heartbeat_timeout_seconds=30.0,
    )
    registry.update_presence("r1", "2000-01-01T00:00:00+00:00")  # stale
    # r2 never seen -> also stale

    enabled = registry.enabled_entries()
    assert enabled == []


def test_enabled_entries_includes_stale_when_requested():
    from fireclaw_core.robot_registry import RobotRegistry
    registry = RobotRegistry(
        [
            RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
            RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765"),
        ],
        heartbeat_timeout_seconds=30.0,
    )
    registry.update_presence("r1", "2000-01-01T00:00:00+00:00")

    enabled = registry.enabled_entries(include_stale=True)
    assert len(enabled) == 2


def test_stale_entries_returns_only_enabled_stale():
    from fireclaw_core.robot_registry import RobotRegistry
    registry = RobotRegistry(
        [
            RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
            RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765"),
            RobotRegistryEntry(robot_id="r3", base_url="http://r3:8765", enabled=False),
        ],
        heartbeat_timeout_seconds=30.0,
    )
    from datetime import datetime, timezone
    registry.update_presence("r2", datetime.now(timezone.utc).isoformat())  # fresh
    # r1 never seen -> stale, r3 disabled -> excluded

    stale = registry.stale_entries()
    assert len(stale) == 1
    assert stale[0].robot_id == "r1"
