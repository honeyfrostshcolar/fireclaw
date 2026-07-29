from __future__ import annotations

import json

from fireclaw_core.context import (
    ContextEvaluationCase,
    ContextManagementPolicy,
    HuggingFaceTokenCounter,
    ModelAwareContextManager,
    advisory_tuples,
    evaluate_context_case,
)
from fireclaw_core.provider.model_catalog import ModelDescriptor


class _CharacterTokenizer:
    def encode(self, text, *, add_special_tokens=False):
        del add_special_tokens
        return list(text)


def _build_request(authoritative, continuity, advisory, policy):
    payload = {
        "authoritative": authoritative,
        "continuity": continuity,
        "advisory": advisory,
        "context_policy": policy,
    }
    return [
        {"role": "system", "content": "plan safely"},
        {
            "role": "user",
            "content": json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    ], [{"type": "function", "function": {"name": "propose_plan"}}]


def test_evaluation_reports_budget_effect_without_losing_critical_context() -> None:
    counter = HuggingFaceTokenCounter(
        _CharacterTokenizer(),
        tokenizer_id="test-tokenizer",
    )
    manager = ModelAwareContextManager(
        runtime=None,
        task="mission_planning",
        policy=ContextManagementPolicy(
            output_reserve_tokens=100,
            minimum_safety_margin_tokens=10,
            safety_margin_ratio=0,
            keep_recent_items=2,
        ),
        token_counter=counter,
        model_descriptor=ModelDescriptor(
            id="test-model",
            name="Test Model",
            provider="test",
            context_window=1300,
            max_tokens=100,
            supports_tools=True,
            tokenizer_id="test-tokenizer",
        ),
    )
    history = [
        {
            "record_id": f"event-{index}",
            "status": "succeeded",
            "content": "old observation " + ("x" * 80),
        }
        for index in range(8)
    ]
    case = ContextEvaluationCase(
        case_id="long-mission-history",
        scope="mission_planner",
        context_id="mission-1:context:1",
        authoritative={
            "snapshot_id": "mission-1:state:1",
            "emergency_stop": False,
        },
        continuity={"active_plan_id": "mission-1:plan:1"},
        advisory=advisory_tuples({"session_history": history}),
        compact_sections=("session_history",),
    )

    report = evaluate_context_case(
        manager,
        token_counter=counter,
        case=case,
        build_request=_build_request,
    )

    assert report.raw_input_tokens > report.managed_input_tokens
    assert report.token_savings > 0
    assert 0 < report.token_savings_ratio < 1
    assert report.authoritative_preserved is True
    assert report.continuity_preserved is True
    assert report.compacted_source_count == 6
    assert report.managed.authoritative["snapshot_id"] == (
        "mission-1:state:1"
    )
    assert (
        report.managed_input_tokens
        <= report.managed.manifest.max_input_tokens
    )
