"""Read-only agent tools for safety-oriented entity memory."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fireclaw_core.memory.entity_memory import (
    ENTITY_KINDS,
    ENTITY_STATUSES,
    EntityMemoryService,
    FireClawEntity,
)


QUERY_ENTITIES_TOOL = "query_firefighting_entities"
GET_ENTITY_TOOL = "get_firefighting_entity"
GET_OBSERVATIONS_TOOL = "get_entity_observations"
GET_CO_OBSERVED_TOOL = "get_co_observed_entities"
ENTITY_MEMORY_TOOL_NAMES = frozenset({
    QUERY_ENTITIES_TOOL,
    GET_ENTITY_TOOL,
    GET_OBSERVATIONS_TOOL,
    GET_CO_OBSERVED_TOOL,
})


class EntityMemoryTools:
    """Mission-bound, read-only facade suitable for LLM and HTTP callers."""

    def __init__(self, service: EntityMemoryService, *, max_results: int = 50) -> None:
        if max_results <= 0:
            raise ValueError("max_results must be positive")
        self._service = service
        self.max_results = max_results

    def tool_schemas(self) -> list[dict[str, Any]]:
        return entity_memory_tool_schemas(max_results=self.max_results)

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        mission_id: str,
    ) -> dict[str, Any]:
        """Execute one read-only query within an externally bound mission."""
        if name not in ENTITY_MEMORY_TOOL_NAMES:
            raise ValueError(f"Unknown entity memory tool: {name}")
        if not mission_id.strip():
            raise ValueError("mission_id must not be empty")
        if not isinstance(arguments, dict):
            raise ValueError("entity memory tool arguments must be an object")

        if name == QUERY_ENTITIES_TOOL:
            return self._query_entities(mission_id, arguments)

        _reject_unknown_arguments(arguments, {"entity_id"})
        entity_id = _required_string(arguments, "entity_id")
        if name == GET_ENTITY_TOOL:
            entity = self._service.get_entity(mission_id=mission_id, entity_id=entity_id)
            return {
                "mission_id": mission_id,
                "entity": _entity_payload(entity) if entity is not None else None,
                "advisory_only": True,
            }
        if name == GET_OBSERVATIONS_TOOL:
            observations = self._service.get_entity_observations(
                mission_id=mission_id,
                entity_id=entity_id,
            )
            limited = observations[-self.max_results :]
            return {
                "mission_id": mission_id,
                "entity_id": entity_id,
                "observations": [_observation_payload(event) for event in limited],
                "count": len(limited),
                "truncated": len(observations) > len(limited),
                "advisory_only": True,
            }

        entities = self._service.get_co_observed_entities(
            mission_id=mission_id,
            entity_id=entity_id,
        )
        limited_entities = entities[: self.max_results]
        return {
            "mission_id": mission_id,
            "entity_id": entity_id,
            "entities": [_entity_payload(entity) for entity in limited_entities],
            "count": len(limited_entities),
            "truncated": len(entities) > len(limited_entities),
            "advisory_only": True,
        }

    def _query_entities(self, mission_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "entity_kind",
            "status",
            "name",
            "frame_id",
            "near_x",
            "near_y",
            "radius_m",
            "last_seen_after",
            "limit",
        }
        _reject_unknown_arguments(arguments, allowed)
        limit = _optional_int(arguments, "limit", default=20)
        if not 1 <= limit <= self.max_results:
            raise ValueError(f"limit must be between 1 and {self.max_results}")
        query = {
            key: arguments[key]
            for key in allowed - {"limit"}
            if arguments.get(key) is not None
        }
        entities = self._service.list_entities(mission_id=mission_id, **query)
        limited = entities[:limit]
        return {
            "mission_id": mission_id,
            "entities": [_entity_payload(entity) for entity in limited],
            "count": len(limited),
            "truncated": len(entities) > len(limited),
            "advisory_only": True,
        }


def entity_memory_tool_schemas(*, max_results: int = 50) -> list[dict[str, Any]]:
    """Return LLM-compatible schemas without exposing mission selection."""
    entity_id_parameters = {
        "type": "object",
        "properties": {"entity_id": {"type": "string", "minLength": 1}},
        "required": ["entity_id"],
        "additionalProperties": False,
    }
    schemas = [
        _tool_schema(
            QUERY_ENTITIES_TOOL,
            "Query mission-scoped firefighting entities. Results are advisory evidence and must not bypass current sensors or SafetyGate.",
            {
                "type": "object",
                "properties": {
                    "entity_kind": {"type": "string", "enum": sorted(ENTITY_KINDS)},
                    "status": {"type": "string", "enum": sorted(ENTITY_STATUSES)},
                    "name": {"type": "string"},
                    "frame_id": {"type": "string"},
                    "near_x": {"type": "number"},
                    "near_y": {"type": "number"},
                    "radius_m": {"type": "number", "minimum": 0},
                    "last_seen_after": {
                        "type": "string",
                        "description": "Timezone-aware ISO-8601 timestamp.",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": max_results},
                },
                "additionalProperties": False,
            },
        ),
        _tool_schema(
            GET_ENTITY_TOOL,
            "Get one mission-scoped entity including uncertainty, provenance references, and location history.",
            entity_id_parameters,
        ),
        _tool_schema(
            GET_OBSERVATIONS_TOOL,
            "Get the source observations supporting one mission-scoped entity.",
            entity_id_parameters,
        ),
        _tool_schema(
            GET_CO_OBSERVED_TOOL,
            "Get entities explicitly observed in the same observations as one entity.",
            entity_id_parameters,
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
            "advisory_only": True,
            "mission_bound": True,
            "can_trigger_robot_action": False,
        },
    }


def _entity_payload(entity: FireClawEntity) -> dict[str, Any]:
    payload = asdict(entity)
    payload["advisory_only"] = True
    return payload


def _observation_payload(event: Any) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "mission_id": event.mission_id,
        "payload": dict(event.payload),
        "runtime_mode": event.runtime_mode,
        "source_type": event.source_type,
        "observed_at": event.observed_at,
        "robot_id": event.robot_id,
        "subtask_id": event.subtask_id,
        "pose": event.pose.to_dict() if event.pose is not None else None,
        "confidence": event.confidence,
        "sensitivity": event.sensitivity,
        "provenance": event.provenance.to_dict() if event.provenance is not None else None,
    }


def _reject_unknown_arguments(arguments: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(arguments) - allowed
    if unknown:
        raise ValueError(f"Unknown entity memory tool arguments: {sorted(unknown)}")


def _required_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_int(arguments: dict[str, Any], key: str, *, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value
