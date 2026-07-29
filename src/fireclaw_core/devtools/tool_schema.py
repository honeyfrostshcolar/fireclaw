from __future__ import annotations

from typing import Any

from fireclaw_core.planner.planner import PlannerContext


FIRECLAW_METADATA_KEYS = (
    "runtime",
    "dry_run_only",
    "max_attempts",
    "idempotent",
    "required_sensors",
    "failure_categories",
    "allow_real_robot",
    "timeout_seconds",
    "risk_level",
)


def skill_metadata_to_tool_schema(skill_metadata: dict[str, Any]) -> dict[str, Any]:
    name = _required_string(skill_metadata, "name")
    description = _optional_string(skill_metadata, "description", "No description provided.")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": _input_schema(skill_metadata),
        },
        "x-fireclaw": {
            key: _metadata_default(key, skill_metadata.get(key))
            for key in FIRECLAW_METADATA_KEYS
        },
    }


def build_tool_schemas(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        skill_metadata_to_tool_schema(skill)
        for skill in sorted(skills, key=lambda item: str(item.get("name", "")))
    ]


def planner_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["status", "message"],
        "properties": {
            "status": {"type": "string", "enum": ["planned", "clarify"]},
            "message": {"type": "string"},
            "intent": {"type": ["string", "null"]},
            "target_floor": {"type": ["integer", "null"]},
            "target_pose": {
                "type": ["object", "null"],
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "yaw": {"type": "number"},
                    "frame_id": {"type": "string"},
                },
                "required": ["x", "y"],
                "additionalProperties": False,
            },
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["skill_name"],
                    "properties": {
                        "skill_name": {"type": "string"},
                        "inputs": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
    }


def build_planner_request(command: str, context: PlannerContext | None = None) -> dict[str, Any]:
    if context is None:
        context_payload = {
            "session_id": None,
            "turn_index": None,
            "recent_records": [],
            "skills": [],
        }
        skills: list[dict[str, Any]] = []
    else:
        context_payload = {
            "session_id": context.session_id,
            "turn_index": context.turn_index,
            "recent_records": context.recent_records,
            "skills": context.skills,
        }
        skills = context.skills
    return {
        "command": command,
        "context": context_payload,
        "tools": build_tool_schemas(skills),
        "response_schema": planner_response_schema(),
        "instructions": (
            "You are the FireClaw planner for a firefighting robot dry-run core. "
            "Return only a structured planning response matching response_schema. "
            "Use only listed tools, request clarification when required safety or task details are missing, "
            "and never invent unregistered skill names."
        ),
    }


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Tool schema field '{key}' must be a non-empty string.")
    return value


def _optional_string(payload: dict[str, Any], key: str, default: str) -> str:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, str) or not value:
        raise ValueError(f"Tool schema field '{key}' must be a non-empty string.")
    return value


def _metadata_default(key: str, value: Any) -> Any:
    if key in {"runtime"}:
        return "unknown" if value is None else value
    if key in {"dry_run_only"}:
        return True if value is None else value
    if key in {"max_attempts"}:
        return 1 if value is None else value
    if key in {"idempotent", "allow_real_robot"}:
        return False if value is None else value
    if key in {"required_sensors", "failure_categories"}:
        return [] if value is None else value
    if key == "timeout_seconds":
        return value
    if key == "risk_level":
        return "low" if value is None else value
    return value


def _input_schema(skill_metadata: dict[str, Any]) -> dict[str, Any]:
    value = skill_metadata.get("input_schema")
    if isinstance(value, dict) and value.get("type") == "object":
        return dict(value)
    return {
        "type": "object",
        "additionalProperties": True,
    }
