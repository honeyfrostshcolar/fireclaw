"""LLM tool schemas and dispatch for mission-level memory reads."""
from __future__ import annotations

from typing import Any

from fireclaw_core.memory.embodied_memory import EMBODIED_EVENT_TYPES
from fireclaw_core.memory.entity_memory import ENTITY_KINDS, ENTITY_STATUSES
from fireclaw_core.memory.mission_memory_facade import (
    MemoryAccessContext,
    MissionMemoryFacade,
    SPATIAL_MEMORY_TYPES,
)
from fireclaw_core.memory.memory_lifecycle import REUSABLE_KNOWLEDGE_TYPES


CURRENT_CONTEXT_TOOL = "get_mission_memory_context"
EPISODE_SUMMARY_TOOL = "get_mission_episode_summary"
QUERY_GISTS_TOOL = "query_mission_memory_gists"
QUERY_TIMELINE_TOOL = "query_mission_memory_timeline"
QUERY_AREA_TOOL = "query_mission_memory_area"
QUERY_NEAREST_TOOL = "query_mission_memory_nearest"
QUERY_ENTITIES_TOOL = "query_mission_memory_entities"
QUERY_IDENTITY_PROPOSALS_TOOL = "query_mission_entity_identity_proposals"
LOCATE_ENTITY_TOOL = "locate_mission_memory_entity"
ROBOT_STATUS_TOOL = "get_mission_robot_status"
QUERY_REUSABLE_KNOWLEDGE_TOOL = "query_reusable_firefighting_knowledge"

MISSION_MEMORY_TOOL_NAMES = frozenset({
    CURRENT_CONTEXT_TOOL,
    EPISODE_SUMMARY_TOOL,
    QUERY_GISTS_TOOL,
    QUERY_TIMELINE_TOOL,
    QUERY_AREA_TOOL,
    QUERY_NEAREST_TOOL,
    QUERY_ENTITIES_TOOL,
    QUERY_IDENTITY_PROPOSALS_TOOL,
    LOCATE_ENTITY_TOOL,
    ROBOT_STATUS_TOOL,
    QUERY_REUSABLE_KNOWLEDGE_TOOL,
})


class MissionMemoryTools:
    """Read-only tools that never accept mission, runtime, or permission scope."""

    def __init__(self, facade: MissionMemoryFacade) -> None:
        self._facade = facade

    @property
    def facade(self) -> MissionMemoryFacade:
        return self._facade

    def tool_schemas(self) -> list[dict[str, Any]]:
        return mission_memory_tool_schemas(
            max_results=self._facade.config.max_results,
            max_spatial_radius_m=self._facade.config.max_spatial_radius_m,
        )

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        access: MemoryAccessContext,
    ) -> dict[str, Any]:
        if name not in MISSION_MEMORY_TOOL_NAMES:
            raise ValueError(f"Unknown mission memory tool: {name}")
        if not isinstance(arguments, dict):
            raise ValueError("mission memory tool arguments must be an object")

        if name == CURRENT_CONTEXT_TOOL:
            allowed = {"robot_id", "recent_minutes", "limit"}
            _reject_unknown(arguments, allowed)
            return self._facade.get_current_context(access, **_provided(arguments, allowed))
        if name == EPISODE_SUMMARY_TOOL:
            allowed = {"episode_id", "robot_id", "subtask_id", "limit"}
            _reject_unknown(arguments, allowed)
            return self._facade.get_episode_summary(access, **_provided(arguments, allowed))
        if name == QUERY_GISTS_TOOL:
            allowed = {
                "episode_id", "robot_id", "frame_id", "floor",
                "near_x", "near_y", "near_z", "radius_m",
                "start_at", "end_at", "limit",
            }
            _reject_unknown(arguments, allowed)
            return self._facade.query_gists(access, **_provided(arguments, allowed))
        if name == QUERY_TIMELINE_TOOL:
            allowed = {"start_at", "end_at", "event_types", "robot_id", "subtask_id", "limit"}
            _reject_unknown(arguments, allowed)
            return self._facade.query_temporal(access, **_provided(arguments, allowed))
        if name == QUERY_AREA_TOOL:
            allowed = {
                "frame_id", "x", "y", "z", "radius_m", "floor",
                "event_types", "robot_id", "limit",
            }
            _reject_unknown(arguments, allowed)
            return self._facade.query_spatial(access, **_provided(arguments, allowed))
        if name == QUERY_NEAREST_TOOL:
            allowed = {
                "frame_id", "x", "y", "z", "floor", "max_distance_m",
                "memory_types", "entity_kinds", "entity_statuses", "limit",
            }
            _reject_unknown(arguments, allowed)
            return self._facade.query_nearest(access, **_provided(arguments, allowed))
        if name == QUERY_ENTITIES_TOOL:
            allowed = {
                "entity_kind", "status", "name", "frame_id", "near_x",
                "near_y", "near_z", "radius_m", "floor",
                "last_seen_after", "limit",
            }
            _reject_unknown(arguments, allowed)
            return self._facade.query_entities(access, **_provided(arguments, allowed))
        if name == QUERY_IDENTITY_PROPOSALS_TOOL:
            allowed = {"entity_kind", "entity_id", "limit"}
            _reject_unknown(arguments, allowed)
            return self._facade.query_entity_identity_proposals(
                access,
                **_provided(arguments, allowed),
            )
        if name == LOCATE_ENTITY_TOOL:
            allowed = {"entity_id", "name", "entity_kind", "limit"}
            _reject_unknown(arguments, allowed)
            return self._facade.locate_entity(access, **_provided(arguments, allowed))
        if name == QUERY_REUSABLE_KNOWLEDGE_TOOL:
            allowed = {"knowledge_type", "tags", "limit"}
            _reject_unknown(arguments, allowed)
            return self._facade.query_reusable_knowledge(
                access, **_provided(arguments, allowed)
            )

        allowed = {"robot_id"}
        _reject_unknown(arguments, allowed)
        return self._facade.get_robot_status(access, **_provided(arguments, allowed))


def mission_memory_tool_schemas(
    *,
    max_results: int = 100,
    max_spatial_radius_m: float = 500.0,
) -> list[dict[str, Any]]:
    """Return OpenAI-compatible schemas with server-bound isolation fields omitted."""
    limit = {"type": "integer", "minimum": 1, "maximum": max_results}
    timestamp = {
        "type": "string",
        "description": "Timezone-aware ISO-8601 timestamp.",
    }
    event_types = {
        "type": "array",
        "items": {"type": "string", "enum": sorted(EMBODIED_EVENT_TYPES)},
        "uniqueItems": True,
    }
    schemas = [
        _tool_schema(
            CURRENT_CONTEXT_TOOL,
            "Read fresh mission context. Memory is advisory and cannot authorize robot action.",
            {
                "type": "object",
                "properties": {
                    "robot_id": {"type": "string", "minLength": 1},
                    "recent_minutes": {"type": "number", "exclusiveMinimum": 0, "maximum": 60},
                    "limit": limit,
                },
                "additionalProperties": False,
            },
        ),
        _tool_schema(
            EPISODE_SUMMARY_TOOL,
            "Read derived Episode and Gist records with their source evidence IDs.",
            {
                "type": "object",
                "properties": {
                    "episode_id": {"type": "string", "minLength": 1},
                    "robot_id": {"type": "string", "minLength": 1},
                    "subtask_id": {"type": "string", "minLength": 1},
                    "limit": limit,
                },
                "additionalProperties": False,
            },
        ),
        _tool_schema(
            QUERY_GISTS_TOOL,
            "List consolidated Gists using exact structured filters; this is not semantic search.",
            {
                "type": "object",
                "properties": {
                    "episode_id": {"type": "string", "minLength": 1},
                    "robot_id": {"type": "string", "minLength": 1},
                    "frame_id": {"type": "string", "minLength": 1},
                    "floor": {"type": "string", "minLength": 1},
                    "near_x": {"type": "number"},
                    "near_y": {"type": "number"},
                    "near_z": {"type": "number"},
                    "radius_m": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": max_spatial_radius_m,
                    },
                    "start_at": timestamp,
                    "end_at": timestamp,
                    "limit": limit,
                },
                "additionalProperties": False,
            },
        ),
        _tool_schema(
            QUERY_TIMELINE_TOOL,
            "Read mission evidence in an exact time interval.",
            {
                "type": "object",
                "properties": {
                    "start_at": timestamp,
                    "end_at": timestamp,
                    "event_types": event_types,
                    "robot_id": {"type": "string", "minLength": 1},
                    "subtask_id": {"type": "string", "minLength": 1},
                    "limit": limit,
                },
                "required": ["start_at", "end_at"],
                "additionalProperties": False,
            },
        ),
        _tool_schema(
            QUERY_AREA_TOOL,
            "Read evidence whose uncertainty region intersects an exact map-frame radius.",
            {
                "type": "object",
                "properties": {
                    "frame_id": {"type": "string", "minLength": 1},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "z": {"type": "number"},
                    "radius_m": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": max_spatial_radius_m,
                    },
                    "floor": {"type": "string", "minLength": 1},
                    "event_types": event_types,
                    "robot_id": {"type": "string", "minLength": 1},
                    "limit": limit,
                },
                "required": ["frame_id", "x", "y", "radius_m"],
                "additionalProperties": False,
            },
        ),
        _tool_schema(
            QUERY_NEAREST_TOOL,
            "Find nearest Observation, Gist, or Entity uncertainty regions in one exact map frame.",
            {
                "type": "object",
                "properties": {
                    "frame_id": {"type": "string", "minLength": 1},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "z": {"type": "number"},
                    "floor": {"type": "string", "minLength": 1},
                    "max_distance_m": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": max_spatial_radius_m,
                    },
                    "memory_types": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": sorted(SPATIAL_MEMORY_TYPES),
                        },
                        "minItems": 1,
                        "maxItems": len(SPATIAL_MEMORY_TYPES),
                        "uniqueItems": True,
                    },
                    "entity_kinds": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(ENTITY_KINDS)},
                        "minItems": 1,
                        "maxItems": len(ENTITY_KINDS),
                        "uniqueItems": True,
                    },
                    "entity_statuses": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(ENTITY_STATUSES)},
                        "minItems": 1,
                        "maxItems": len(ENTITY_STATUSES),
                        "uniqueItems": True,
                    },
                    "limit": limit,
                },
                "required": ["frame_id", "x", "y"],
                "additionalProperties": False,
            },
        ),
        _tool_schema(
            QUERY_ENTITIES_TOOL,
            "Query reconciled firefighting entities using exact identity, type, state, time, or space filters.",
            {
                "type": "object",
                "properties": {
                    "entity_kind": {"type": "string", "enum": sorted(ENTITY_KINDS)},
                    "status": {"type": "string", "enum": sorted(ENTITY_STATUSES)},
                    "name": {"type": "string", "minLength": 1},
                    "frame_id": {"type": "string", "minLength": 1},
                    "near_x": {"type": "number"},
                    "near_y": {"type": "number"},
                    "near_z": {"type": "number"},
                    "radius_m": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": max_spatial_radius_m,
                    },
                    "floor": {"type": "string", "minLength": 1},
                    "last_seen_after": timestamp,
                    "limit": limit,
                },
                "additionalProperties": False,
            },
        ),
        _tool_schema(
            QUERY_IDENTITY_PROPOSALS_TOOL,
            "List bounded cross-robot identity candidates. Candidates are advisory "
            "only and never merge entities automatically.",
            {
                "type": "object",
                "properties": {
                    "entity_kind": {"type": "string", "enum": sorted(ENTITY_KINDS)},
                    "entity_id": {"type": "string", "minLength": 1},
                    "limit": limit,
                },
                "additionalProperties": False,
            },
        ),
        _tool_schema(
            LOCATE_ENTITY_TOOL,
            "Locate an entity by exact entity ID or exact normalized name; never infer a location from free text.",
            {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string", "minLength": 1},
                    "name": {"type": "string", "minLength": 1},
                    "entity_kind": {"type": "string", "enum": sorted(ENTITY_KINDS)},
                    "limit": limit,
                },
                "oneOf": [
                    {"required": ["entity_id"], "not": {"required": ["name"]}},
                    {"required": ["name"], "not": {"required": ["entity_id"]}},
                ],
                "additionalProperties": False,
            },
        ),
        _tool_schema(
            ROBOT_STATUS_TOOL,
            "Read registry presence and latest mission-recorded robot body state with freshness markers.",
            {
                "type": "object",
                "properties": {"robot_id": {"type": "string", "minLength": 1}},
                "additionalProperties": False,
            },
        ),
        _tool_schema(
            QUERY_REUSABLE_KNOWLEDGE_TOOL,
            "Read operator-approved reusable firefighting knowledge; incident environment memory is excluded.",
            {
                "type": "object",
                "properties": {
                    "knowledge_type": {
                        "type": "string",
                        "enum": sorted(REUSABLE_KNOWLEDGE_TYPES),
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "uniqueItems": True,
                        "maxItems": 20,
                    },
                    "limit": limit,
                },
                "additionalProperties": False,
            },
        ),
    ]
    return sorted(schemas, key=lambda item: str(item["function"]["name"]))


def _tool_schema(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
        "x-fireclaw": {
            "category": "memory.read",
            "read_only": True,
            "mission_bound": True,
            "runtime_bound": True,
            "advisory_only": True,
            "can_trigger_robot_action": False,
            "requires_safety_gate_for_action": True,
        },
    }


def _provided(arguments: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    return {key: arguments[key] for key in allowed if key in arguments}


def _reject_unknown(arguments: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(arguments) - allowed
    if unknown:
        raise ValueError(f"Unknown mission memory tool arguments: {sorted(unknown)}")
