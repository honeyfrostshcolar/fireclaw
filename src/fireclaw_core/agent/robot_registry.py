from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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


_HEARTBEAT_DISABLED = -1.0


class RobotRegistry:
    def __init__(
        self,
        entries: list[RobotRegistryEntry],
        *,
        heartbeat_timeout_seconds: float = _HEARTBEAT_DISABLED,
    ) -> None:
        by_id: dict[str, RobotRegistryEntry] = {}
        for entry in entries:
            if entry.robot_id in by_id:
                raise ValueError(f"Duplicate robot_id in robot registry: {entry.robot_id}")
            by_id[entry.robot_id] = entry
        self._entries = by_id
        self._last_seen_at: dict[str, str] = {}
        self._heartbeat_timeout_seconds = heartbeat_timeout_seconds

    def get(self, robot_id: str) -> RobotRegistryEntry | None:
        return self._entries.get(robot_id)

    @property
    def _heartbeat_enabled(self) -> bool:
        return self._heartbeat_timeout_seconds > 0

    def is_stale(self, robot_id: str) -> bool:
        """Return True if the robot's last heartbeat is missing or older than timeout.

        Returns False when heartbeat_timeout_seconds is not set (disabled).
        """
        if not self._heartbeat_enabled:
            return False
        last_seen = self._last_seen_at.get(robot_id)
        if last_seen is None:
            return True
        try:
            seen_at = datetime.fromisoformat(last_seen)
        except (ValueError, TypeError):
            return True
        if seen_at.tzinfo is None:
            seen_at = seen_at.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - seen_at).total_seconds()
        return elapsed > self._heartbeat_timeout_seconds

    def enabled_entries(self, *, include_stale: bool = False) -> list[RobotRegistryEntry]:
        entries = [entry for entry in self._entries.values() if entry.enabled]
        if not include_stale and self._heartbeat_enabled:
            entries = [entry for entry in entries if not self.is_stale(entry.robot_id)]
        return entries

    def stale_entries(self) -> list[RobotRegistryEntry]:
        """Return enabled entries whose heartbeat has expired or was never seen."""
        if not self._heartbeat_enabled:
            return []
        return [entry for entry in self._entries.values() if entry.enabled and self.is_stale(entry.robot_id)]

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


def robot_registry_from_profiles(
    profiles: list["RobotCapabilityProfile"],
) -> RobotRegistry:
    from fireclaw_core.agent.robot_profile import RobotCapabilityProfile

    return RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id=profile.robot_id,
                base_url=profile.base_url,
                capabilities=profile.capabilities,
                enabled=profile.enabled,
            )
            for profile in profiles
        ]
    )


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
