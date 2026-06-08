"""Tests for fireclaw_core.llm_planner — LLMMissionPlanner with tool calling."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from fireclaw_core.llm_planner import (
    LLMMissionPlanner,
    MISSION_PLAN_TOOL,
    VALID_INTENTS,
    build_system_prompt,
)
from fireclaw_core.mission_planner import MissionPlannerContext
from fireclaw_core.provider import (
    ChatCompletion,
    ProviderAPIError,
    ProviderTimeoutError,
    TokenUsage,
    ToolCall,
)
from fireclaw_core.robot_registry import RobotRegistryEntry


# --- Helpers ---


def _make_context(*robots: RobotRegistryEntry) -> MissionPlannerContext:
    return MissionPlannerContext(available_robots=list(robots))


def _make_provider(response: ChatCompletion) -> MagicMock:
    """Return a mock ModelProvider that returns the given ChatCompletion."""
    provider = MagicMock()
    provider.chat_completion.return_value = response
    return provider


def _make_tool_call_response(
    intent: str = "search",
    subtasks: list[dict[str, Any]] | None = None,
    content: str | None = None,
) -> ChatCompletion:
    """Build a ChatCompletion that contains a tool call for create_mission_plan."""
    if subtasks is None:
        subtasks = [
            {
                "robot_id": "r1",
                "command": "去2楼搜索受困人员",
                "floor": 2,
                "capability_required": "search_for_victims",
                "execution_group": 0,
            }
        ]
    arguments = {"intent": intent, "subtasks": subtasks}
    return ChatCompletion(
        content=content,
        tool_calls=[
            ToolCall(
                id="call_001",
                name="create_mission_plan",
                arguments=arguments,
            )
        ],
        usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        model="gpt-4",
        finish_reason="stop",
    )


def _make_text_only_response(content: str = "I cannot create a plan.") -> ChatCompletion:
    """Build a ChatCompletion with no tool calls."""
    return ChatCompletion(
        content=content,
        tool_calls=None,
        usage=TokenUsage(prompt_tokens=100, completion_tokens=30, total_tokens=130),
        model="gpt-4",
        finish_reason="stop",
    )


# --- Test: tool schema structure ---


def test_mission_plan_tool_schema_has_required_fields():
    """The MISSION_PLAN_TOOL schema must have the correct structure."""
    assert MISSION_PLAN_TOOL["type"] == "function"
    func = MISSION_PLAN_TOOL["function"]
    assert func["name"] == "create_mission_plan"
    assert "description" in func

    params = func["parameters"]
    assert params["type"] == "object"
    assert "intent" in params["properties"]
    assert "subtasks" in params["properties"]
    assert params["required"] == ["intent", "subtasks"]

    # Intent enum must match VALID_INTENTS
    assert set(params["properties"]["intent"]["enum"]) == VALID_INTENTS

    # Subtask item schema
    subtask_props = params["properties"]["subtasks"]["items"]["properties"]
    assert "robot_id" in subtask_props
    assert "command" in subtask_props
    assert "floor" in subtask_props
    assert "capability_required" in subtask_props
    assert "execution_group" in subtask_props

    subtask_required = params["properties"]["subtasks"]["items"]["required"]
    assert "robot_id" in subtask_required
    assert "command" in subtask_required
    assert "floor" in subtask_required
    assert "capability_required" in subtask_required


# --- Test: system prompt includes robots ---


def test_build_system_prompt_includes_robots():
    """System prompt should list each robot's id, capabilities, zone, and enabled status."""
    ctx = _make_context(
        RobotRegistryEntry(
            robot_id="r1",
            base_url="http://r1:8765",
            capabilities=("search_for_victims", "recon"),
            zone="zone-a",
            enabled=True,
        ),
        RobotRegistryEntry(
            robot_id="r2",
            base_url="http://r2:8765",
            capabilities=("firefight",),
            zone=None,
            enabled=False,
        ),
    )

    prompt = build_system_prompt(ctx)

    assert "r1" in prompt
    assert "search_for_victims" in prompt
    assert "recon" in prompt
    assert "zone-a" in prompt
    assert "r2" in prompt
    assert "firefight" in prompt
    # Disabled status should be mentioned (Chinese prompt uses 已禁用)
    assert "已禁用" in prompt
    assert "已启用" in prompt


# --- Test: successful plan from tool call ---


def test_llm_planner_returns_plan_from_tool_call():
    """When LLM returns a valid tool call, the planner should return a MissionPlan."""
    robots = _make_context(
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    )
    provider = _make_provider(_make_tool_call_response())
    planner = LLMMissionPlanner(provider=provider, model_id="gpt-4")

    result = planner.plan("去二楼搜索受困人员", context=robots)

    assert result.status == "planned"
    assert result.intent == "search"
    assert result.plan is not None
    assert len(result.plan.subtasks) == 1
    assert result.plan.subtasks[0].robot_id == "r1"
    assert result.plan.subtasks[0].floor == 2
    assert result.plan.subtasks[0].capability_required == "search_for_victims"


# --- Test: no tool call returns error ---


def test_llm_planner_returns_error_when_no_tool_call():
    """When LLM returns text only (no tool calls), the planner should return an error."""
    robots = _make_context(
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    )
    provider = _make_provider(_make_text_only_response())
    planner = LLMMissionPlanner(provider=provider, model_id="gpt-4")

    result = planner.plan("去二楼搜索受困人员", context=robots)

    assert result.status == "error"
    assert "未返回工具调用" in result.message


# --- Test: provider timeout ---


def test_llm_planner_returns_error_on_provider_timeout():
    """ProviderTimeoutError should produce a user-facing error result."""
    robots = _make_context(
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    )
    provider = MagicMock()
    provider.chat_completion.side_effect = ProviderTimeoutError("Connection timed out")
    planner = LLMMissionPlanner(provider=provider, model_id="gpt-4")

    result = planner.plan("去二楼搜索受困人员", context=robots)

    assert result.status == "error"
    assert "超时" in result.message


# --- Test: provider API error ---


def test_llm_planner_returns_error_on_provider_api_error():
    """ProviderAPIError should produce a user-facing error result."""
    robots = _make_context(
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    )
    provider = MagicMock()
    provider.chat_completion.side_effect = ProviderAPIError(status_code=500, message="Internal Server Error")
    planner = LLMMissionPlanner(provider=provider, model_id="gpt-4")

    result = planner.plan("去二楼搜索受困人员", context=robots)

    assert result.status == "error"
    assert "API 错误" in result.message


# --- Test: robot_id validation ---


def test_llm_planner_validates_robot_id():
    """Tool call referencing a nonexistent robot_id should return an error."""
    robots = _make_context(
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    )
    provider = _make_provider(
        _make_tool_call_response(
            subtasks=[
                {
                    "robot_id": "nonexistent_robot",
                    "command": "去2楼搜索受困人员",
                    "floor": 2,
                    "capability_required": "search_for_victims",
                    "execution_group": 0,
                }
            ]
        )
    )
    planner = LLMMissionPlanner(provider=provider, model_id="gpt-4")

    result = planner.plan("去二楼搜索受困人员", context=robots)

    assert result.status == "error"
    assert "nonexistent_robot" in result.message


# --- Test: trace recording ---


def test_llm_planner_records_trace():
    """When trace_store is configured, each plan() call should record a trace."""
    robots = _make_context(
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    )
    provider = _make_provider(_make_tool_call_response())
    trace_store = MagicMock()
    planner = LLMMissionPlanner(provider=provider, model_id="gpt-4", trace_store=trace_store)

    planner.plan("去二楼搜索受困人员", context=robots)

    trace_store.record.assert_called_once()
    trace = trace_store.record.call_args[0][0]
    assert trace.model == "gpt-4"
    assert trace.status == "success"
    assert trace.tool_calls is not None
