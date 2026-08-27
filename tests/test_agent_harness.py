from __future__ import annotations

import json

import pytest

from fireclaw_core.agent.harness import (
    AgentHarnessAttempt,
    AgentHarnessError,
    ProviderAgentHarness,
)
from fireclaw_core.context.manager import (
    ContextManagementPolicy,
    HuggingFaceTokenCounter,
    ModelAwareContextManager,
)
from fireclaw_core.provider.model_catalog import ModelDescriptor
from fireclaw_core.provider.provider import (
    ChatCompletion,
    TokenUsage,
    ToolCall,
)


class _CharacterTokenizer:
    def encode(self, text, *, add_special_tokens=False):
        del add_special_tokens
        return list(text)


class _Runtime:
    def __init__(self, response: ChatCompletion) -> None:
        self.response = response
        self.calls: list[dict] = []

    def chat_completion(
        self,
        *,
        messages,
        tools,
        temperature=0.0,
        max_tokens=4096,
        seed=None,
    ):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "seed": seed,
            }
        )
        return self.response

    def status(self):
        return {"type": "test", "model": "test-model"}

    def select_model(self, *, task, preferred_model=None):
        del task, preferred_model
        return _descriptor()


def _descriptor() -> ModelDescriptor:
    return ModelDescriptor(
        id="test-model",
        name="Test Model",
        provider="test",
        context_window=4000,
        max_tokens=300,
        supports_tools=True,
        tokenizer_id="test-tokenizer",
    )


def _response(tool_name: str = "propose") -> ChatCompletion:
    return ChatCompletion(
        content=None,
        tool_calls=[
            ToolCall(id="call-1", name=tool_name, arguments={"value": 1})
        ],
        usage=TokenUsage(10, 5, 15),
        model="test-model",
        finish_reason="tool_calls",
    )


def _manager(runtime: _Runtime) -> ModelAwareContextManager:
    return ModelAwareContextManager(
        runtime=runtime,
        task="test",
        policy=ContextManagementPolicy(
            output_reserve_tokens=300,
            minimum_safety_margin_tokens=10,
            safety_margin_ratio=0,
        ),
        token_counter=HuggingFaceTokenCounter(
            _CharacterTokenizer(),
            tokenizer_id="test-tokenizer",
        ),
        model_descriptor=_descriptor(),
    )


def _builder(authoritative, continuity, advisory, context_policy):
    payload = {
        "authoritative": authoritative,
        "continuity": continuity,
        "advisory": advisory,
        "context_policy": context_policy,
    }
    return [
        {"role": "system", "content": "system"},
        {
            "role": "user",
            "content": json.dumps(payload, sort_keys=True),
        },
    ], [
        {
            "type": "function",
            "function": {
                "name": "propose",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                },
            },
        }
    ]


def _attempt(**overrides) -> AgentHarnessAttempt:
    values = dict(
        role="mission_agent",
        run_id="run-1",
        scope="mission",
        context_id="context-1",
        authoritative={"snapshot_id": "snapshot-1"},
        continuity={"iteration": 1},
        advisory={"memory": [{"record_id": "memory-1"}]},
        build_request=_builder,
    )
    values.update(overrides)
    return AgentHarnessAttempt(**values)


def test_harness_enforces_context_fit_and_returns_manifest() -> None:
    runtime = _Runtime(_response())
    traces: list[dict] = []
    harness = ProviderAgentHarness(
        provider_runtime=runtime,
        context_manager=_manager(runtime),
        trace_sink=traces.append,
    )

    result = harness.run_attempt(_attempt())

    assert result.tool_calls[0].name == "propose"
    assert result.context_manifest["context_id"] == "context-1"
    assert runtime.calls[0]["max_tokens"] == 300
    sent_payload = json.loads(runtime.calls[0]["messages"][1]["content"])
    assert sent_payload["authoritative"]["snapshot_id"] == "snapshot-1"
    assert traces[0]["harness_id"] == "fireclaw.provider"
    assert traces[0]["classification"] == "ok"


def test_harness_emits_context_and_provider_stage_timings() -> None:
    runtime = _Runtime(_response())
    stages: list[tuple[str, dict]] = []
    harness = ProviderAgentHarness(
        provider_runtime=runtime,
        context_manager=_manager(runtime),
    )

    harness.run_attempt(
        _attempt(
            stage_sink=lambda stage, payload: stages.append(
                (stage, dict(payload))
            )
        )
    )

    assert [stage for stage, _ in stages] == [
        "context_fit",
        "provider_request",
    ]
    assert all(payload["duration_ms"] >= 0 for _, payload in stages)
    assert stages[0][1]["input_tokens"] > 0
    assert stages[1][1]["model"] == "test-model"


def test_harness_forwards_and_traces_optional_seed() -> None:
    runtime = _Runtime(_response())
    traces: list[dict] = []
    harness = ProviderAgentHarness(
        provider_runtime=runtime,
        context_manager=_manager(runtime),
        trace_sink=traces.append,
    )

    harness.run_attempt(_attempt(seed=17, temperature=0.25))

    assert runtime.calls[0]["seed"] == 17
    assert runtime.calls[0]["temperature"] == 0.25
    assert traces[0]["seed"] == 17
    assert traces[0]["temperature"] == 0.25


def test_harness_rejects_unexposed_tool_after_provider_call() -> None:
    runtime = _Runtime(_response("direct_robot_motion"))
    harness = ProviderAgentHarness(
        provider_runtime=runtime,
        context_manager=_manager(runtime),
    )

    with pytest.raises(AgentHarnessError) as error:
        harness.run_attempt(_attempt())

    assert error.value.code == "unexpected_tool_name"
    assert len(runtime.calls) == 1


def test_harness_rejects_malformed_tool_schema_before_provider_call() -> None:
    runtime = _Runtime(_response())
    harness = ProviderAgentHarness(
        provider_runtime=runtime,
        context_manager=_manager(runtime),
    )

    def malformed_builder(*_args):
        return [{"role": "user", "content": "test"}], [
            {"type": "function", "function": {"name": "broken"}}
        ]

    with pytest.raises(AgentHarnessError) as error:
        harness.run_attempt(_attempt(build_request=malformed_builder))

    assert error.value.code == "malformed_tool_schema"
    assert runtime.calls == []


def test_harness_honors_cancellation_before_provider_call() -> None:
    runtime = _Runtime(_response())
    harness = ProviderAgentHarness(
        provider_runtime=runtime,
        context_manager=_manager(runtime),
    )

    with pytest.raises(AgentHarnessError) as error:
        harness.run_attempt(
            _attempt(cancellation_requested=lambda: True)
        )

    assert error.value.code == "cancelled_before_provider_call"
    assert runtime.calls == []
