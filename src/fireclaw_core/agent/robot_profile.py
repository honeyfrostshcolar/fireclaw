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
    for cap_name, chain in profile.capability_skill_chains.items():
        if cap_name not in profile.capabilities:
            errors.append(f"skill chain key {cap_name!r} is not in capabilities")
        for skill_name in chain:
            if skill_name not in enabled_set:
                errors.append(
                    f"skill chain {cap_name!r} references non-enabled skill {skill_name!r}"
                )
    return errors
