from __future__ import annotations

from typing import Any

from fireclaw_core.execution.skills import SkillRegistry


def build_robot_skill_inventory(
    *,
    registry: SkillRegistry,
    primitive_skills: tuple[str, ...],
    composite_chains: dict[str, tuple[str, ...]],
    verified_sensors: set[str],
) -> dict[str, Any]:
    """Build an OpenClaw-like runtime skill inventory for the robot-local LLM.

    Returns a dict with a ``skills`` list.  Primitives appear first, then
    composites.  Each item carries enough metadata for the LLM to understand
    what is available, what it needs, and whether it is currently usable.
    """
    skills: list[dict[str, Any]] = []
    added: set[str] = set()

    for name in primitive_skills:
        skill = registry.get(name)
        if skill is None:
            continue
        metadata = dict(skill.metadata)
        if metadata.get("kind") != "primitive":
            continue
        skills.append(_inventory_item(name, skill, metadata, verified_sensors))
        added.add(name)

    for capability, chain in composite_chains.items():
        if capability in added:
            continue
        skills.append({
            "name": capability,
            "kind": "composite",
            "chain": list(chain),
            "description": f"Composite capability {capability}",
            "input_schema": {
                "type": "object",
                "properties": {"floor": {"type": "integer", "minimum": 1}},
                "required": ["floor"],
            },
            "available": all(
                step in primitive_skills or registry.get(step) is not None
                for step in chain
            ),
        })
        added.add(capability)

    return {"skills": skills}


def _inventory_item(
    name: str,
    skill: Any,
    metadata: dict[str, Any],
    verified_sensors: set[str],
) -> dict[str, Any]:
    # Prefer Skill-level required_sensors; fall back to metadata
    required_sensors = set(getattr(skill, "required_sensors", None) or metadata.get("required_sensors") or [])
    return {
        "name": name,
        "kind": metadata.get("kind", "primitive"),
        "primitive_capability": metadata.get("primitive_capability"),
        "description": metadata.get("description", name),
        "input_schema": metadata.get("input_schema", {"type": "object"}),
        "safety_class": metadata.get("safety_class", "unknown"),
        "requires_approval": bool(metadata.get("requires_approval", False)),
        "required_sensors": sorted(required_sensors),
        "available": required_sensors.issubset(verified_sensors),
    }
