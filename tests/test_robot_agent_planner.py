from __future__ import annotations

import json

import pytest

from fireclaw_core.provider.provider import ChatCompletion, TokenUsage, ToolCall, ProviderTimeoutError
from fireclaw_core.agent.robot_agent import (
    LLMRobotAgentPlanner,
    RobotAgentPlannerError,
    RobotAgentTaskEnvelope,
    build_robot_agent_messages,
    _local_plan_from_arguments,
)


class FakeRuntime:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.last_tools = None

    def chat_completion(self, *, messages, tools, temperature=0.0, max_tokens=4096):
        self.last_tools = tools
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if self.error is not None:
            raise self.error
        return self.response

    def status(self):
        return {"type": "fake", "model": "fake-model"}


def _envelope() -> RobotAgentTaskEnvelope:
    return RobotAgentTaskEnvelope(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        command="去2楼搜索",
        task_type="search",
        target={"floor": 2},
        allowed_skills=["navigate_to_floor", "report_status"],
        required_skills=["navigate_to_floor"],
        constraints={},
        risk_level="low",
        operator_id="operator-1",
    )


def test_llm_robot_agent_planner_parses_tool_call():
    runtime = FakeRuntime(
        response=ChatCompletion(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call-1",
                    name="create_robot_local_plan",
                    arguments={
                        "intent": "search",
                        "steps": [
                            {
                                "skill_name": "report_status",
                                "inputs": {"floor": 2},
                                "reason": "announce",
                            },
                            {
                                "skill_name": "navigate_to_floor",
                                "inputs": {"floor": 2},
                            },
                        ],
                        "rationale": "Report before moving.",
                        "confidence": 0.7,
                    },
                )
            ],
            usage=TokenUsage(1, 1, 2),
            model="fake-model",
            finish_reason="tool_calls",
        )
    )

    plan = LLMRobotAgentPlanner(runtime).plan(_envelope(), context={"robot_state": {}})

    assert [step.skill_name for step in plan.steps] == ["report_status", "navigate_to_floor"]
    assert plan.steps[0].reason == "announce"
    assert runtime.calls[0]["tools"][0]["function"]["name"] == "create_robot_local_plan"


def test_llm_robot_agent_planner_raises_on_missing_tool_call():
    runtime = FakeRuntime(
        response=ChatCompletion(
            content="free text",
            tool_calls=None,
            usage=TokenUsage(1, 1, 2),
            model="fake-model",
            finish_reason="stop",
        )
    )

    with pytest.raises(RobotAgentPlannerError, match="tool"):
        LLMRobotAgentPlanner(runtime).plan(_envelope(), context={})


def test_llm_robot_agent_planner_wraps_provider_timeout():
    runtime = FakeRuntime(error=ProviderTimeoutError("timed out"))

    with pytest.raises(RobotAgentPlannerError, match="timed out"):
        LLMRobotAgentPlanner(runtime).plan(_envelope(), context={})


# --- _local_plan_from_arguments edge cases ---


def test_local_plan_from_arguments_rejects_empty_steps():
    with pytest.raises(RobotAgentPlannerError, match="at least one step"):
        _local_plan_from_arguments({"intent": "search", "steps": []})


def test_local_plan_from_arguments_rejects_step_missing_skill_name():
    with pytest.raises(RobotAgentPlannerError, match="skill_name"):
        _local_plan_from_arguments({"intent": "search", "steps": [{"inputs": {}}]})


def test_local_plan_from_arguments_rejects_non_dict_inputs():
    with pytest.raises(RobotAgentPlannerError, match="inputs must be an object"):
        _local_plan_from_arguments({
            "intent": "search",
            "steps": [{"skill_name": "report_status", "inputs": "bad"}],
        })


def test_local_plan_from_arguments_preserves_optional_fields():
    plan = _local_plan_from_arguments({
        "intent": "search",
        "steps": [{"skill_name": "report_status", "inputs": {"floor": 2}, "reason": "announce"}],
        "rationale": "Report first.",
        "confidence": 0.85,
    })

    assert plan.intent == "search"
    assert plan.steps[0].reason == "announce"
    assert plan.rationale == "Report first."
    assert plan.confidence == 0.85


# --- build_robot_agent_messages content ---


def test_build_robot_agent_messages_contains_envelope_fields():
    envelope = _envelope()
    messages = build_robot_agent_messages(envelope, context={"robot_state": {}})

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "消防机器人" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    payload = json.loads(messages[1]["content"])
    assert payload["task"]["allowed_skills"] == ["navigate_to_floor", "report_status"]
    assert payload["task"]["task_id"] == "task-1"
    assert payload["context"] == {"robot_state": {}}


def test_llm_robot_agent_planner_accepts_direct_skill_tool_calls():
    runtime = FakeRuntime(
        response=ChatCompletion(
            content=None,
            tool_calls=[
                ToolCall(id="call-1", name="navigate_to_floor", arguments={"floor": 2}),
                ToolCall(id="call-2", name="report_status", arguments={"floor": 2}),
            ],
            usage=TokenUsage(1, 1, 2),
            model="fake-model",
            finish_reason="tool_calls",
        )
    )
    envelope = _envelope()
    skill_tools = [
        {
            "type": "function",
            "function": {
                "name": "navigate_to_floor",
                "description": "Navigate robot to a target floor.",
                "parameters": {"type": "object", "properties": {"floor": {"type": "integer"}}, "required": ["floor"]},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "report_status",
                "description": "Report status to operator.",
                "parameters": {"type": "object", "properties": {"floor": {"type": "integer"}}, "required": ["floor"]},
            },
        },
    ]

    plan = LLMRobotAgentPlanner(runtime).plan(envelope, context={"skill_tools": skill_tools})

    assert [step.skill_name for step in plan.steps] == ["navigate_to_floor", "report_status"]
    assert plan.steps[0].inputs == {"floor": 2}
    assert runtime.last_tools[0]["function"]["name"] == "create_robot_local_plan"
    assert runtime.last_tools[1]["function"]["name"] == "navigate_to_floor"
