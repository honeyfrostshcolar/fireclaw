"""Deterministic evaluation helpers for context-management policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from fireclaw_core.context.manager import (
    ManagedContextResult,
    ModelAwareContextManager,
    RequestBuilder,
    TokenCounter,
)


@dataclass(frozen=True)
class ContextEvaluationCase:
    case_id: str
    scope: str
    context_id: str
    authoritative: dict[str, Any]
    continuity: dict[str, Any]
    advisory: dict[str, tuple[dict[str, Any], ...]]
    compact_sections: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextEvaluationReport:
    case_id: str
    raw_input_tokens: int
    managed_input_tokens: int
    token_savings: int
    token_savings_ratio: float
    authoritative_preserved: bool
    continuity_preserved: bool
    compacted_source_count: int
    omitted_advisory_count: int
    managed: ManagedContextResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "raw_input_tokens": self.raw_input_tokens,
            "managed_input_tokens": self.managed_input_tokens,
            "token_savings": self.token_savings,
            "token_savings_ratio": self.token_savings_ratio,
            "authoritative_preserved": self.authoritative_preserved,
            "continuity_preserved": self.continuity_preserved,
            "compacted_source_count": self.compacted_source_count,
            "omitted_advisory_count": self.omitted_advisory_count,
            "managed_manifest": self.managed.manifest.to_dict(),
        }


def evaluate_context_case(
    manager: ModelAwareContextManager,
    *,
    token_counter: TokenCounter,
    case: ContextEvaluationCase,
    build_request: RequestBuilder,
) -> ContextEvaluationReport:
    """Compare a managed request with the same uncompressed input."""

    raw_advisory = {
        name: [dict(item) for item in items]
        for name, items in case.advisory.items()
    }
    managed = manager.fit(
        scope=case.scope,
        context_id=case.context_id,
        authoritative=case.authoritative,
        continuity=case.continuity,
        advisory=raw_advisory,
        build_request=build_request,
        compact_sections=case.compact_sections,
    )
    manifest = managed.manifest
    raw_policy = {
        "scope": case.scope,
        "model_id": manifest.model_id,
        "context_window_tokens": manifest.context_window_tokens,
        "output_reserve_tokens": manifest.output_reserve_tokens,
        "safety_margin_tokens": manifest.safety_margin_tokens,
        "max_input_tokens": manifest.max_input_tokens,
        "token_counter": manifest.token_counter,
        "exact_model_tokenizer": manifest.exact_model_tokenizer,
        "semantic_compaction_count": 0,
        "safety_critical_context_preserved": True,
        "omitted_advisory_items": 0,
    }
    raw_messages, raw_tools = build_request(
        case.authoritative,
        case.continuity,
        raw_advisory,
        raw_policy,
    )
    raw_tokens = token_counter.count_request(raw_messages, raw_tools)
    savings = raw_tokens - manifest.used_input_tokens
    ratio = savings / raw_tokens if raw_tokens else 0.0
    compacted_source_count = sum(
        record.source_count for record in manifest.compacted_sections
    )
    return ContextEvaluationReport(
        case_id=case.case_id,
        raw_input_tokens=raw_tokens,
        managed_input_tokens=manifest.used_input_tokens,
        token_savings=savings,
        token_savings_ratio=ratio,
        authoritative_preserved=managed.authoritative == case.authoritative,
        continuity_preserved=managed.continuity == case.continuity,
        compacted_source_count=compacted_source_count,
        omitted_advisory_count=len(manifest.omitted_refs),
        managed=managed,
    )


def advisory_tuples(
    values: dict[str, Iterable[dict[str, Any]]],
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Freeze mutable benchmark fixtures before evaluating a policy."""

    return {
        name: tuple(dict(item) for item in items)
        for name, items in values.items()
    }
