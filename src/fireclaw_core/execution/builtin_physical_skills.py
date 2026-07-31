"""Compatibility projection for first-party physical Tool Plugins.

Concrete physical capabilities are owned by extension packages under
``extensions/``.  This module keeps the old import names used by task
contracts, profiles and checkpoints, but it no longer defines or registers a
domain action in FireClaw core.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fireclaw_core.execution.skill_plugin import PhysicalSkillCatalog
from fireclaw_core.plugin.extension_loader import load_fireclaw_extensions
from fireclaw_core.plugin.plugin_host import FireClawPluginHost


GENERIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}
FLOOR_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"floor": {"type": "integer", "minimum": 1}},
    "required": ["floor"],
    "additionalProperties": False,
}
LOCAL_CONTEXT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "floor": {
            "type": "integer",
            "minimum": 1,
            "description": "Legacy multi-floor compatibility field.",
        },
    },
    "additionalProperties": False,
}
POINT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "x": {"type": "number"},
        "y": {"type": "number"},
        "yaw": {"type": "number", "default": 0.0},
        "frame_id": {"type": "string", "minLength": 1, "default": "map"},
    },
    "required": ["x", "y"],
    "additionalProperties": False,
}
EMPTY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
GENERIC_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}
NAVIGATE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "from_floor": {"type": "integer"},
    },
    "required": ["robot_id", "floor"],
}
NAVIGATE_POINT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "yaw": {"type": "number"},
        "frame_id": {"type": "string"},
    },
    "required": ["robot_id", "x", "y", "yaw", "frame_id"],
}
SEARCH_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "victims_found": {"type": "integer"},
    },
    "required": ["robot_id", "floor", "victims_found"],
}
ASSESS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "condition": {"type": "string"},
    },
    "required": ["robot_id", "floor", "condition"],
}
REPORT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "message": {"type": "string"},
    },
    "required": ["robot_id", "floor", "message"],
}
EMPTY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
}


def builtin_physical_skill_catalog() -> PhysicalSkillCatalog:
    """Load first-party physical Tool contributions through manifests."""

    host = FireClawPluginHost()
    load_fireclaw_extensions(
        host,
        (Path(__file__).resolve().parents[3] / "extensions",),
        mode="simulation",
        role="robot_agent",
        services={"adapter": "dry-run"},
    )
    return PhysicalSkillCatalog(host=host)


_BUILTIN_CATALOG: PhysicalSkillCatalog | None = None


def _catalog() -> PhysicalSkillCatalog:
    """Load first-party extensions after the execution package is initialized.

    ``skills`` and the robot adapter are imported by the policy/tool runtime.
    Eagerly activating an extension here would make a plugin importing a
    public ``ToolSpec`` re-enter that partially initialized package.  The
    catalog is therefore a normal runtime dependency, not module-import work.
    """

    global _BUILTIN_CATALOG
    if _BUILTIN_CATALOG is None:
        _BUILTIN_CATALOG = builtin_physical_skill_catalog()
    return _BUILTIN_CATALOG


def get_builtin_physical_skill(name: str):
    return _catalog().get(name)


def builtin_physical_skill_names() -> set[str]:
    return _catalog().names()


def auto_skills_for_target(target: dict[str, Any]) -> list[str]:
    return _catalog().auto_skills_for_target(target)


def skill_chain_for_capability(capability: str) -> list[str]:
    return _catalog().skill_chain_for_capability(capability)


def task_type_for_capability(capability: str) -> str:
    return _catalog().task_type_for_capability(capability)


def supplemental_physical_skill_names() -> tuple[str, ...]:
    return _catalog().supplemental_skill_names()


def builtin_physical_action_names() -> set[str]:
    return {plugin.action for plugin in _catalog().plugins.values()}


def iter_builtin_physical_skills():
    return tuple(_catalog().plugins.values())
