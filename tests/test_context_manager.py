from __future__ import annotations

import json

import pytest

from fireclaw_core.context.manager import (
    ContextBudgetExceeded,
    ContextManagementPolicy,
    HuggingFaceTokenCounter,
    ModelAwareContextManager,
)
from fireclaw_core.provider.model_catalog import ModelDescriptor


class _CharacterTokenizer:
    def encode(self, text, *, add_special_tokens=False):
        del add_special_tokens
        return list(text)


def _descriptor(
    *,
    context_window: int = 2000,
    max_tokens: int = 200,
) -> ModelDescriptor:
    return ModelDescriptor(
        id="test-model",
        name="Test Model",
        provider="test",
        context_window=context_window,
        max_tokens=max_tokens,
        supports_tools=True,
        tokenizer_id="test-tokenizer",
    )


def _builder(authoritative, continuity, advisory, policy):
    payload = {
        "authoritative": authoritative,
        "continuity": continuity,
        "advisory": advisory,
        "context_policy": policy,
    }
    return [
        {"role": "system", "content": "system"},
        {
            "role": "user",
            "content": json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    ], [{"type": "function", "function": {"name": "plan"}}]


def _manager(
    *,
    context_window: int = 2000,
    max_tokens: int = 200,
    keep_recent_items: int = 2,
) -> ModelAwareContextManager:
    return ModelAwareContextManager(
        runtime=None,
        task="test",
        policy=ContextManagementPolicy(
            output_reserve_tokens=max_tokens,
            minimum_safety_margin_tokens=10,
            safety_margin_ratio=0,
            keep_recent_items=keep_recent_items,
        ),
        token_counter=HuggingFaceTokenCounter(
            _CharacterTokenizer(),
            tokenizer_id="test-tokenizer",
        ),
        model_descriptor=_descriptor(
            context_window=context_window,
            max_tokens=max_tokens,
        ),
    )


def test_huggingface_counter_uses_concrete_tokenizer() -> None:
    counter = HuggingFaceTokenCounter(
        _CharacterTokenizer(),
        tokenizer_id="test-tokenizer",
    )

    assert counter.count_text("火场A") == 3
    assert counter.exact_model_tokenizer is True
    assert counter.name == "huggingface:test-tokenizer"


def test_manager_semantically_compacts_old_history_with_provenance() -> None:
    history = [
        {
            "record_id": f"record-{index}",
            "command": f"任务{index}",
            "status": "succeeded",
            "floor": index,
        }
        for index in range(6)
    ]

    result = _manager(context_window=8000).fit(
        scope="robot_local_planner",
        context_id="ctx-1",
        authoritative={"robot_state": {"battery": 80}},
        continuity={},
        advisory={"session_history": history},
        build_request=_builder,
        compact_sections=("session_history",),
    )

    compacted = result.advisory["session_history"]
    assert len(compacted) == 3
    assert compacted[0]["summary_type"] == (
        "structured_semantic_history"
    )
    assert compacted[0]["semantic_facts"]["floor"] == [0, 1, 2, 3]
    assert [item["record_id"] for item in compacted[1:]] == [
        "record-4",
        "record-5",
    ]
    record = result.manifest.compacted_sections[0]
    assert record.source_refs == (
        "record-0",
        "record-1",
        "record-2",
        "record-3",
    )
    assert result.manifest.exact_model_tokenizer is True


def test_manager_omits_advisory_items_to_fit_model_budget() -> None:
    result = _manager(
        context_window=1200,
        max_tokens=100,
    ).fit(
        scope="mission_planner",
        context_id="ctx-2",
        authoritative={"mission_id": "m1"},
        continuity={},
        advisory={
            "operator_corrections": [
                {"record_id": "correction-1", "content": "x" * 40}
            ],
            "external_knowledge": [
                {"knowledge_id": "guide-1", "content": "y" * 800}
            ],
        },
        build_request=_builder,
    )

    assert result.advisory["operator_corrections"]
    assert result.advisory["external_knowledge"] == []
    assert result.manifest.omitted_refs == (
        (
            "external_knowledge",
            "guide-1",
            "model_token_budget",
        ),
    )
    assert (
        result.manifest.used_input_tokens
        <= result.manifest.max_input_tokens
    )
    counter = HuggingFaceTokenCounter(
        _CharacterTokenizer(),
        tokenizer_id="test-tokenizer",
    )
    assert result.manifest.used_input_tokens == counter.count_request(
        result.messages,
        result.tools,
    )


def test_manager_blocks_when_critical_request_exceeds_model_budget() -> None:
    with pytest.raises(
        ContextBudgetExceeded,
        match="no authoritative content was truncated",
    ):
        _manager(
            context_window=400,
            max_tokens=100,
        ).fit(
            scope="mission_planner",
            context_id="ctx-3",
            authoritative={"state": "z" * 1000},
            continuity={},
            advisory={},
            build_request=_builder,
        )
