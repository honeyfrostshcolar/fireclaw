from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from fireclaw_core.execution.skills import SkillRegistry
from fireclaw_core.ros.ros1_config import Ros1AdapterConfig
from fireclaw_core.sensors.discovery import DiscoveryFingerprint, SensorMappingRule


@dataclass(frozen=True)
class SensorDiscoveryProfileConfig:
    enabled: bool = True
    message_timeout_seconds: float = 2.0
    rules: tuple[SensorMappingRule, ...] = ()


@dataclass(frozen=True)
class RobotCapabilityProfile:
    robot_id: str
    base_url: str
    adapter: str
    ros1_config: str | None
    data_dir: Path
    capabilities: tuple[str, ...]
    enabled_skills: tuple[str, ...]
    llm_exposed_skills: tuple[str, ...]
    capability_skill_chains: dict[str, tuple[str, ...]] = field(default_factory=dict)
    primitive_skills: tuple[str, ...] = ()
    sensor_discovery: SensorDiscoveryProfileConfig = field(
        default_factory=SensorDiscoveryProfileConfig
    )
    discovery_fingerprint: DiscoveryFingerprint | None = None
    enabled: bool = True

    @property
    def memory_path(self) -> Path:
        return self.data_dir / "memory.jsonl"

    @property
    def event_path(self) -> Path:
        return self.data_dir / "events.jsonl"

    @property
    def task_queue_path(self) -> Path:
        return self.data_dir / "tasks.jsonl"

    def to_robot_registry_entry(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "base_url": self.base_url,
            "capabilities": list(self.capabilities),
            "enabled": self.enabled,
        }


def load_robot_capability_profiles(
    paths: list[str | Path] | tuple[str | Path, ...],
) -> list[RobotCapabilityProfile]:
    return [load_robot_capability_profile(path) for path in paths]


def load_robot_capability_profile(path: str | Path) -> RobotCapabilityProfile:
    target = Path(path)
    with target.open("rb") as handle:
        raw = tomllib.load(handle)
    robot = raw.get("robot")
    if not isinstance(robot, dict):
        raise ValueError("robot profile requires a [robot] table.")
    robot_id = _required_string(robot, "id")
    base_url = _required_string(robot, "base_url")
    adapter = _required_string(robot, "adapter")
    data_dir = Path(_required_string(robot, "data_dir"))
    capabilities = _string_tuple(robot, "capabilities")
    enabled_skills = _string_tuple(robot, "enabled_skills")
    llm_exposed_skills = _string_tuple(robot, "llm_exposed_skills")
    capability_skill_chains = _skill_chain_map(raw, "capability_skill_chains")
    primitive_skills = _string_tuple(robot, "primitive_skills") if "primitive_skills" in robot else ()
    sensor_discovery = _sensor_discovery_config(robot)
    discovery_fingerprint = _discovery_fingerprint(robot)
    return RobotCapabilityProfile(
        robot_id=robot_id,
        base_url=base_url.rstrip("/"),
        adapter=adapter,
        ros1_config=_optional_string(robot, "ros1_config"),
        data_dir=data_dir,
        capabilities=capabilities,
        enabled_skills=enabled_skills,
        llm_exposed_skills=llm_exposed_skills,
        capability_skill_chains=capability_skill_chains,
        primitive_skills=primitive_skills,
        sensor_discovery=sensor_discovery,
        discovery_fingerprint=discovery_fingerprint,
        enabled=bool(robot.get("enabled", True)),
    )


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"robot.{key} must be a non-empty string.")
    return value.strip()


def _optional_string(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"robot.{key} must be a non-empty string when provided.")
    return value.strip()


def _string_tuple(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"robot.{key} must be a non-empty list of strings.")
    items = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(items) != len(value):
        raise ValueError(f"robot.{key} must contain only non-empty strings.")
    return items


def _skill_chain_map(raw: dict[str, Any], key: str) -> dict[str, tuple[str, ...]]:
    value = raw.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a table mapping capability names to skill lists.")
    result: dict[str, tuple[str, ...]] = {}
    for cap_name, chain_list in value.items():
        if not isinstance(chain_list, list) or not chain_list:
            raise ValueError(f"{key}.{cap_name} must be a non-empty list of strings.")
        items = tuple(item.strip() for item in chain_list if isinstance(item, str) and item.strip())
        if len(items) != len(chain_list):
            raise ValueError(f"{key}.{cap_name} must contain only non-empty strings.")
        result[cap_name] = items
    return result


def _sensor_discovery_config(robot: dict[str, Any]) -> SensorDiscoveryProfileConfig:
    raw = robot.get("sensor_discovery")
    if raw is None:
        return SensorDiscoveryProfileConfig()
    if not isinstance(raw, dict):
        raise ValueError("robot.sensor_discovery must be a table when provided.")
    rules_raw = raw.get("rules") or []
    if not isinstance(rules_raw, list):
        raise ValueError("robot.sensor_discovery.rules must be an array of tables.")
    rules: list[SensorMappingRule] = []
    for index, item in enumerate(rules_raw):
        if not isinstance(item, dict):
            raise ValueError(f"robot.sensor_discovery.rules[{index}] must be a table.")
        rules.append(
            SensorMappingRule(
                topic_pattern=_required_string(item, "topic_pattern"),
                message_type=_required_string(item, "message_type"),
                sensor=_required_string(item, "sensor"),
                source="profile",
                confidence=float(item.get("confidence", 0.9)),
                confirmed=bool(item.get("confirmed", False)),
                confirmed_by=_optional_string(item, "confirmed_by"),
                confirmed_at=_optional_string(item, "confirmed_at"),
            )
        )
    return SensorDiscoveryProfileConfig(
        enabled=bool(raw.get("enabled", True)),
        message_timeout_seconds=float(raw.get("message_timeout_seconds", 2.0)),
        rules=tuple(rules),
    )


def _discovery_fingerprint(robot: dict[str, Any]) -> DiscoveryFingerprint | None:
    raw = robot.get("discovery_fingerprint")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("robot.discovery_fingerprint must be a table when provided.")
    source = raw.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("robot.discovery_fingerprint.source must be a non-empty string.")
    topics_hash = raw.get("topics_hash")
    if not isinstance(topics_hash, str) or not topics_hash.strip():
        raise ValueError("robot.discovery_fingerprint.topics_hash must be a non-empty string.")
    nodes_hash = raw.get("nodes_hash")
    if nodes_hash is not None and (not isinstance(nodes_hash, str) or not nodes_hash.strip()):
        raise ValueError("robot.discovery_fingerprint.nodes_hash must be a non-empty string when provided.")
    created_at = raw.get("created_at")
    if created_at is not None and not isinstance(created_at, str):
        raise ValueError("robot.discovery_fingerprint.created_at must be a string when provided.")
    confirmed_by = raw.get("confirmed_by")
    if confirmed_by is not None and (not isinstance(confirmed_by, str) or not confirmed_by.strip()):
        raise ValueError("robot.discovery_fingerprint.confirmed_by must be a non-empty string when provided.")
    confirmed_at = raw.get("confirmed_at")
    if confirmed_at is not None and not isinstance(confirmed_at, str):
        raise ValueError("robot.discovery_fingerprint.confirmed_at must be a string when provided.")
    return DiscoveryFingerprint(
        source=source.strip(),
        topics_hash=topics_hash.strip(),
        nodes_hash=nodes_hash.strip() if isinstance(nodes_hash, str) else None,
        created_at=created_at.strip() if isinstance(created_at, str) else None,
        confirmed_by=confirmed_by.strip() if isinstance(confirmed_by, str) else None,
        confirmed_at=confirmed_at.strip() if isinstance(confirmed_at, str) else None,
    )


def validate_robot_capability_profile(
    profile: RobotCapabilityProfile,
    registry: SkillRegistry,
    *,
    ros1_config: Ros1AdapterConfig | None = None,
) -> list[str]:
    errors: list[str] = []
    registered = registry.names()
    for skill_name in profile.enabled_skills:
        if skill_name not in registered:
            errors.append(f"enabled skill {skill_name!r} is not registered")
    for skill_name in profile.llm_exposed_skills:
        if skill_name not in profile.enabled_skills:
            errors.append(f"LLM-exposed skill {skill_name!r} is not in enabled_skills")
    if profile.adapter == "ros1":
        if profile.ros1_config is None:
            errors.append("ros1 profile requires robot.ros1_config; ROS1 remap validation skipped")
        if ros1_config is not None:
            remapped = set(ros1_config.endpoints)
            for skill_name in profile.enabled_skills:
                if skill_name not in remapped:
                    errors.append(f"enabled skill {skill_name!r} has no ROS1 remap")
    if not profile.capabilities:
        errors.append("profile must declare at least one capability")
    enabled_set = set(profile.enabled_skills)
    for skill_name in profile.primitive_skills:
        skill = registry.get(skill_name)
        if skill is None:
            errors.append(f"primitive skill {skill_name!r} is not registered")
            continue
        if skill_name not in enabled_set:
            errors.append(f"primitive skill {skill_name!r} is not in enabled_skills")
        if skill.metadata.get("kind") != "primitive":
            errors.append(f"primitive skill {skill_name!r} is not marked as primitive")
    for cap_name, chain in profile.capability_skill_chains.items():
        if cap_name not in profile.capabilities:
            errors.append(f"skill chain key {cap_name!r} is not in capabilities")
        for skill_name in chain:
            if skill_name not in enabled_set:
                errors.append(
                    f"skill chain {cap_name!r} references non-enabled skill {skill_name!r}"
                )
    return errors
