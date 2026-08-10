from fireclaw_core.planner.planner import PlannerContext
from fireclaw_core.devtools.tool_schema import (
    build_planner_request,
    build_tool_schemas,
    planner_response_schema,
    skill_metadata_to_tool_schema,
)


def test_skill_metadata_to_tool_schema_includes_function_and_fireclaw_metadata():
    schema = skill_metadata_to_tool_schema(
        {
            "name": "thermal_policy",
            "description": "Runs thermal victim search.",
            "runtime": "subprocess",
            "dry_run_only": True,
            "max_attempts": 2,
            "idempotent": True,
            "required_sensors": ["thermal_camera"],
            "failure_categories": ["timeout"],
            "allow_real_robot": False,
            "timeout_seconds": 5.0,
        }
    )

    assert schema == {
        "type": "function",
        "function": {
            "name": "thermal_policy",
            "description": "Runs thermal victim search.",
            "parameters": {
                "type": "object",
                "additionalProperties": True,
            },
        },
        "x-fireclaw": {
            "runtime": "subprocess",
            "dry_run_only": True,
            "max_attempts": 2,
            "idempotent": True,
            "required_sensors": ["thermal_camera"],
            "failure_categories": ["timeout"],
            "allow_real_robot": False,
            "timeout_seconds": 5.0,
            "risk_level": "low",
        },
    }


def test_skill_metadata_to_tool_schema_uses_declared_input_schema():
    input_schema = {
        "type": "object",
        "properties": {"floor": {"type": "integer"}},
        "required": ["floor"],
        "additionalProperties": False,
    }

    schema = skill_metadata_to_tool_schema(
        {
            "name": "navigate_to_waypoint",
            "description": "Navigate.",
            "input_schema": input_schema,
        }
    )

    assert schema["function"]["parameters"] == input_schema


def test_build_tool_schemas_sorts_by_function_name():
    schemas = build_tool_schemas(
        [
            {"name": "z_skill", "description": "Z."},
            {"name": "a_skill", "description": "A."},
        ]
    )

    assert [schema["function"]["name"] for schema in schemas] == ["a_skill", "z_skill"]


def test_build_planner_request_includes_context_tools_schema_and_instructions():
    context = PlannerContext(
        session_id="session-a",
        turn_index=2,
        recent_records=[{"command": "救人", "status": "clarify"}],
        skills=[
            {
                "name": "echo_policy",
                "description": "Echo policy.",
                "runtime": "subprocess",
            }
        ],
    )

    request = build_planner_request("二楼", context)

    assert request["command"] == "二楼"
    assert request["context"] == {
        "session_id": "session-a",
        "turn_index": 2,
        "recent_records": [{"command": "救人", "status": "clarify"}],
        "skills": context.skills,
    }
    assert request["tools"][0]["function"]["name"] == "echo_policy"
    assert request["response_schema"] == planner_response_schema()
    assert "FireClaw" in request["instructions"]
