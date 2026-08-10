from __future__ import annotations

import json

import pytest

from fireclaw_core.agent.robot_agent import (
    LLMRobotAgentPlanner,
    RobotAgentPlannerError,
    RobotAgentPolicy,
    RobotAgentTaskEnvelope,
    _local_plan_from_arguments,
    build_robot_agent_messages,
    envelope_from_structured_task,
)
from fireclaw_core.provider.provider import (
    ChatCompletion,
    ProviderTimeoutError,
    TokenUsage,
    ToolCall,
)
from fireclaw_core.task.task_contract import StructuredRobotTask


POINT_TARGET = {
    "frame_id": "map",
    "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
}
POINT_INPUTS = {
    "x": 2.0,
    "y": 1.5,
    "yaw": 0.0,
    "frame_id": "map",
}


class FakeRuntime:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.last_tools = None

    def chat_completion(
        self,
        *,
        messages,
        tools,
        temperature=0.0,
        max_tokens=4096,
    ):
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
        command="导航到 map 坐标 (2.0, 1.5)",
        task_type="navigate",
        target=POINT_TARGET,
        allowed_skills=["navigate_to_point", "inspect_local_hazard"],
        required_skills=["navigate_to_point"],
        constraints={},
        risk_level="low",
        operator_id="operator-1",
    )


def _completion(tool_calls: list[ToolCall]) -> ChatCompletion:
    return ChatCompletion(
        content=None,
        tool_calls=tool_calls,
        usage=TokenUsage(1, 1, 2),
        model="fake-model",
        finish_reason="tool_calls",
    )


def test_llm_robot_agent_planner_parses_plan_tool_call() -> None:
    runtime = FakeRuntime(
        response=_completion(
            [
                ToolCall(
                    id="call-1",
                    name="create_robot_local_plan",
                    arguments={
                        "intent": "approach_target",
                        "steps": [
                            {
                                "skill_name": "inspect_local_hazard",
                                "inputs": {"radius": 2.0},
                                "reason": "check route",
                            },
                            {
                                "skill_name": "navigate_to_point",
                                "inputs": POINT_INPUTS,
                            },
                        ],
                        "rationale": "Observe before moving.",
                        "confidence": 0.7,
                    },
                )
            ]
        )
    )

    plan = LLMRobotAgentPlanner(runtime).plan(
        _envelope(),
        context={"robot_state": {}},
    )

    assert [step.skill_name for step in plan.steps] == [
        "inspect_local_hazard",
        "navigate_to_point",
    ]
    assert plan.steps[0].reason == "check route"
    assert runtime.calls[0]["tools"][0]["function"]["name"] == (
        "create_robot_local_plan"
    )


def test_llm_robot_agent_planner_raises_on_missing_tool_call() -> None:
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


def test_llm_robot_agent_planner_wraps_provider_timeout() -> None:
    runtime = FakeRuntime(error=ProviderTimeoutError("timed out"))

    with pytest.raises(RobotAgentPlannerError, match="timed out"):
        LLMRobotAgentPlanner(runtime).plan(_envelope(), context={})


def test_local_plan_from_arguments_rejects_empty_steps() -> None:
    with pytest.raises(RobotAgentPlannerError, match="at least one step"):
        _local_plan_from_arguments({"intent": "navigate", "steps": []})


def test_local_plan_from_arguments_rejects_step_missing_tool_name() -> None:
    with pytest.raises(RobotAgentPlannerError, match="skill_name"):
        _local_plan_from_arguments(
            {"intent": "navigate", "steps": [{"inputs": {}}]}
        )


def test_local_plan_from_arguments_rejects_non_object_inputs() -> None:
    with pytest.raises(
        RobotAgentPlannerError,
        match="inputs must be an object",
    ):
        _local_plan_from_arguments(
            {
                "intent": "inspect",
                "steps": [
                    {"skill_name": "inspect_local_hazard", "inputs": "bad"}
                ],
            }
        )


def test_local_plan_from_arguments_preserves_optional_fields() -> None:
    plan = _local_plan_from_arguments(
        {
            "intent": "inspect",
            "steps": [
                {
                    "skill_name": "inspect_local_hazard",
                    "inputs": {"radius": 2.0},
                    "reason": "check route",
                }
            ],
            "rationale": "Inspect first.",
            "confidence": 0.85,
        }
    )

    assert plan.intent == "inspect"
    assert plan.steps[0].reason == "check route"
    assert plan.rationale == "Inspect first."
    assert plan.confidence == 0.85


def test_build_robot_agent_messages_contains_envelope_fields() -> None:
    messages = build_robot_agent_messages(
        _envelope(),
        context={"robot_state": {}},
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "消防机器人" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    payload = json.loads(messages[1]["content"])
    assert payload["task"]["allowed_skills"] == [
        "navigate_to_point",
        "inspect_local_hazard",
    ]
    assert payload["task"]["task_id"] == "task-1"
    assert payload["context"] == {"robot_state": {}}


def test_messages_include_plugin_inventory_and_planning_rules() -> None:
    envelope = RobotAgentTaskEnvelope(
        task_id="t1",
        mission_id="m1",
        robot_id="r1",
        command="检查并前往指定目标点",
        task_type="primitive_composition",
        target=POINT_TARGET,
        allowed_skills=["navigate_to_point", "inspect_local_hazard"],
        required_skills=[],
        constraints={},
        risk_level="low",
        operator_id=None,
    )
    messages = build_robot_agent_messages(
        envelope,
        context={
            "skill_inventory": {
                "skills": [
                    {
                        "name": "navigate_to_point",
                        "kind": "primitive",
                        "primitive_capability": "navigation",
                    },
                    {
                        "name": "approach_target",
                        "kind": "composite",
                        "chain": [
                            "inspect_local_hazard",
                            "navigate_to_point",
                        ],
                    },
                ]
            }
        },
    )

    payload = json.loads(messages[1]["content"])
    encoded_inventory = json.dumps(payload["skill_inventory"])
    assert "primitive" in encoded_inventory
    assert "composite" in encoded_inventory
    assert "navigate_to_point" in encoded_inventory
    assert isinstance(payload["planning_rules"], list)
    assert len(payload["planning_rules"]) >= 4


def test_llm_planner_accepts_direct_plugin_tool_calls() -> None:
    runtime = FakeRuntime(
        response=_completion(
            [
                ToolCall(
                    id="call-1",
                    name="inspect_local_hazard",
                    arguments={"radius": 2.0},
                ),
                ToolCall(
                    id="call-2",
                    name="navigate_to_point",
                    arguments=POINT_INPUTS,
                ),
            ]
        )
    )
    skill_tools = [
        {
            "type": "function",
            "function": {
                "name": "inspect_local_hazard",
                "description": "Inspect local route hazards.",
                "parameters": {
                    "type": "object",
                    "properties": {"radius": {"type": "number"}},
                    "required": ["radius"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "navigate_to_point",
                "description": "Navigate to a map point.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                    },
                    "required": ["x", "y"],
                },
            },
        },
    ]

    plan = LLMRobotAgentPlanner(runtime).plan(
        _envelope(),
        context={"skill_tools": skill_tools},
    )

    assert [step.skill_name for step in plan.steps] == [
        "inspect_local_hazard",
        "navigate_to_point",
    ]
    assert plan.steps[0].inputs == {"radius": 2.0}
    assert runtime.last_tools[0]["function"]["name"] == (
        "create_robot_local_plan"
    )
    assert runtime.last_tools[1]["function"]["name"] == (
        "inspect_local_hazard"
    )


def test_llm_plan_from_inventory_passes_generic_delegation_policy() -> None:
    runtime = FakeRuntime(
        response=_completion(
            [
                ToolCall(
                    id="call-1",
                    name="create_robot_local_plan",
                    arguments={
                        "intent": "approach_target",
                        "steps": [
                            {
                                "skill_name": "inspect_local_hazard",
                                "inputs": {"radius": 2.0},
                            },
                            {
                                "skill_name": "navigate_to_point",
                                "inputs": POINT_INPUTS,
                            },
                        ],
                        "rationale": "Inspect and navigate.",
                        "confidence": 0.9,
                    },
                )
            ]
        )
    )
    envelope = RobotAgentTaskEnvelope(
        task_id="t1",
        mission_id="m1",
        robot_id="r1",
        command="检查并前往指定目标点",
        task_type="primitive_composition",
        target=POINT_TARGET,
        allowed_skills=["navigate_to_point", "inspect_local_hazard"],
        required_skills=[],
        constraints={},
        risk_level="low",
        operator_id=None,
    )
    context = {
        "skill_inventory": {
            "skills": [
                {
                    "name": "navigate_to_point",
                    "kind": "primitive",
                    "primitive_capability": "navigation",
                },
                {
                    "name": "inspect_local_hazard",
                    "kind": "primitive",
                    "primitive_capability": "perception",
                },
            ]
        }
    }

    plan = LLMRobotAgentPlanner(runtime).plan(envelope, context=context)

    assert [step.skill_name for step in plan.steps] == [
        "inspect_local_hazard",
        "navigate_to_point",
    ]
    user_payload = json.loads(runtime.calls[0]["messages"][1]["content"])
    inventory = user_payload["planning_context"]["authoritative"][
        "skill_inventory"
    ]
    assert "navigate_to_point" in json.dumps(inventory)
    assert "primitive" in json.dumps(inventory)
    assert plan.context_manifest is not None
    assert plan.context_manifest["scope"] == "robot_local_planner"
    assert runtime.calls[0]["max_tokens"] == 2048
    assert RobotAgentPolicy().validate(envelope, plan).status == "allow"


def test_envelope_preserves_explicit_allowed_tools_without_supplements() -> None:
    task = StructuredRobotTask.from_dict(
        {
            "task_id": "t1",
            "task_type": "primitive_composition",
            "target": POINT_TARGET,
            "required_skills": [],
            "allowed_skills": ["navigate_to_point"],
            "robot_id": "r1",
        }
    )

    envelope = envelope_from_structured_task(
        task,
        fallback_robot_id="fallback",
    )

    assert envelope.allowed_skills == ["navigate_to_point"]
    assert envelope.required_skills == []
