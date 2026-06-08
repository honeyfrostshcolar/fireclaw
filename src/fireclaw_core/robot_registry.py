from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RobotRegistryEntry:
    robot_id: str
    base_url: str
    capabilities: tuple[str, ...] = ()
    zone: str | None = None
    enabled: bool = True


class RobotRegistry:
    def __init__(self, entries: list[RobotRegistryEntry]) -> None:
        by_id: dict[str, RobotRegistryEntry] = {}
        for entry in entries:
            if entry.robot_id in by_id:
                raise ValueError(f"Duplicate robot_id in robot registry: {entry.robot_id}")
            by_id[entry.robot_id] = entry
        self._entries = by_id
        self._last_seen_at: dict[str, str] = {}

    def get(self, robot_id: str) -> RobotRegistryEntry | None:
        return self._entries.get(robot_id)

    def enabled_entries(self) -> list[RobotRegistryEntry]:
        return [entry for entry in self._entries.values() if entry.enabled]

    def list_entries(self) -> list[RobotRegistryEntry]:
        return list(self._entries.values())

    def update_presence(self, robot_id: str, last_seen_at: str) -> None:
        self._last_seen_at[robot_id] = last_seen_at

    def get_last_seen_at(self, robot_id: str) -> str | None:
        return self._last_seen_at.get(robot_id)

    def is_online(self, robot_id: str) -> bool:
        return robot_id in self._last_seen_at

    def online_entries(self) -> list[RobotRegistryEntry]:
        return [entry for entry in self._entries.values() if entry.enabled and entry.robot_id in self._last_seen_at]


def load_robot_registry(path: str | Path) -> RobotRegistry:
    raw = Path(path).read_text(encoding="utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Robot registry must be a JSON object.")
    robots = parsed.get("robots")
    if not isinstance(robots, list):
        raise ValueError("Robot registry must contain a 'robots' list.")
    return RobotRegistry([_parse_entry(entry, index) for index, entry in enumerate(robots)])


def _parse_entry(value: Any, index: int) -> RobotRegistryEntry:
    if not isinstance(value, dict):
        raise ValueError(f"Robot registry entry at index {index} must be an object.")
    robot_id = _required_string(value, "robot_id", index)
    base_url = _required_string(value, "base_url", index).rstrip("/")
    capabilities_value = value.get("capabilities", [])
    if not isinstance(capabilities_value, list):
        raise ValueError(f"Robot registry entry {robot_id} field 'capabilities' must be a list.")
    capabilities = tuple(str(item) for item in capabilities_value if isinstance(item, str) and item.strip())
    zone_value = value.get("zone")
    enabled_value = value.get("enabled", True)
    return RobotRegistryEntry(
        robot_id=robot_id,
        base_url=base_url,
        capabilities=capabilities,
        zone=zone_value if isinstance(zone_value, str) and zone_value.strip() else None,
        enabled=enabled_value if isinstance(enabled_value, bool) else True,
    )


def _required_string(value: dict[str, Any], key: str, index: int) -> str:
    field = value.get(key)
    if not isinstance(field, str) or not field.strip():
        raise ValueError(f"Robot registry entry at index {index} requires field '{key}'.")
    return field.strip()
