from __future__ import annotations

from pathlib import Path

from fireclaw_core.agent.agent import FireClawAgent
from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.agent.robot_tools import (
    build_robot_skill_tools,
    local_plan_from_direct_tool_calls,
)


EXTENSIONS = Path(__file__).resolve().parents[1] / "extensions"


def _navigation_registry():
    return FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="debug-robot-1"),
        extension_paths=(EXTENSIONS,),
        plugin_services={"adapter": "dry-run"},
    ).registry


def test_build_robot_skill_tools_filters_plugin_catalog_and_preserves_schema(
) -> None:
    tools = build_robot_skill_tools(
        _navigation_registry(),
        exposed_skill_names=("navigate_to_point",),
    )

    assert [tool["function"]["name"] for tool in tools] == [
        "navigate_to_point"
    ]
    navigation = tools[0]["function"]
    assert navigation["parameters"]["properties"]["x"]["type"] == "number"
    assert navigation["parameters"]["properties"]["frame_id"]["type"] == (
        "string"
    )
    assert navigation["parameters"]["required"] == ["x", "y"]
    assert tools[0]["x-fireclaw"]["idempotent"] is True


def test_local_plan_from_direct_tool_calls_preserves_order() -> None:
    calls = [
        {"name": "inspect_local_hazard", "arguments": {"radius": 2.0}},
        {
            "name": "navigate_to_point",
            "arguments": {
                "x": 2.0,
                "y": 1.5,
                "yaw": 0.0,
                "frame_id": "map",
            },
        },
    ]

    plan = local_plan_from_direct_tool_calls(calls, intent="approach_target")

    assert plan.intent == "approach_target"
    assert [step.skill_name for step in plan.steps] == [
        "inspect_local_hazard",
        "navigate_to_point",
    ]
    assert plan.steps[0].inputs == {"radius": 2.0}


def test_local_plan_from_direct_tool_calls_handles_empty_list() -> None:
    plan = local_plan_from_direct_tool_calls([], intent="navigate")

    assert plan.intent == "navigate"
    assert plan.steps == []


def test_local_plan_from_direct_tool_calls_handles_missing_arguments() -> None:
    calls = [
        {"name": "inspect_local_hazard"},
        {"name": "navigate_to_point", "arguments": None},
    ]

    plan = local_plan_from_direct_tool_calls(calls, intent="approach_target")

    assert [step.skill_name for step in plan.steps] == [
        "inspect_local_hazard",
        "navigate_to_point",
    ]
    assert plan.steps[0].inputs == {}
    assert plan.steps[1].inputs == {}


def test_build_robot_skill_tools_returns_empty_for_empty_exposed() -> None:
    tools = build_robot_skill_tools(
        _navigation_registry(),
        exposed_skill_names=(),
    )

    assert tools == []
