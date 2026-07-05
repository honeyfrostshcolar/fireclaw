from __future__ import annotations

from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.agent.robot_tools import (
    build_robot_skill_tools,
    local_plan_from_direct_tool_calls,
)
from fireclaw_core.execution.skills import create_default_skill_registry


def test_build_robot_skill_tools_filters_and_preserves_input_schema() -> None:
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="debug-robot-1"))

    tools = build_robot_skill_tools(
        registry,
        exposed_skill_names=("navigate_to_floor", "report_status"),
    )

    names = [tool["function"]["name"] for tool in tools]
    assert names == ["navigate_to_floor", "report_status"]
    nav = tools[0]["function"]
    assert nav["parameters"]["properties"]["floor"]["type"] == "integer"
    assert nav["parameters"]["required"] == ["floor"]
    assert tools[0]["x-fireclaw"]["idempotent"] is True


def test_local_plan_from_direct_tool_calls_preserves_order() -> None:
    calls = [
        {"name": "navigate_to_floor", "arguments": {"floor": 2}},
        {"name": "report_status", "arguments": {"floor": 2}},
    ]

    plan = local_plan_from_direct_tool_calls(calls, intent="search")

    assert plan.intent == "search"
    assert [step.skill_name for step in plan.steps] == ["navigate_to_floor", "report_status"]
    assert plan.steps[0].inputs == {"floor": 2}


def test_local_plan_from_direct_tool_calls_handles_empty_list() -> None:
    plan = local_plan_from_direct_tool_calls([], intent="search")

    assert plan.intent == "search"
    assert plan.steps == []


def test_local_plan_from_direct_tool_calls_handles_missing_arguments() -> None:
    calls = [
        {"name": "navigate_to_floor"},
        {"name": "report_status", "arguments": None},
    ]

    plan = local_plan_from_direct_tool_calls(calls, intent="search")

    assert [step.skill_name for step in plan.steps] == ["navigate_to_floor", "report_status"]
    assert plan.steps[0].inputs == {}
    assert plan.steps[1].inputs == {}


def test_build_robot_skill_tools_returns_empty_for_empty_exposed() -> None:
    from fireclaw_core.agent.robot import DryRunRobotAdapter
    from fireclaw_core.execution.skills import create_default_skill_registry

    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="debug-robot-1"))

    tools = build_robot_skill_tools(registry, exposed_skill_names=())

    assert tools == []
