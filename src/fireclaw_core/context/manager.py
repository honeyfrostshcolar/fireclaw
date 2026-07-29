from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
from typing import Any, Callable, Iterable, Protocol

from fireclaw_core.provider.model_catalog import ModelDescriptor


RequestBuilder = Callable[
    [
        dict[str, Any],
        dict[str, Any],
        dict[str, list[dict[str, Any]]],
        dict[str, Any],
    ],
    tuple[list[dict[str, Any]], list[dict[str, Any]]],
]


class TokenCounter(Protocol):
    name: str
    exact_model_tokenizer: bool

    def count_text(self, text: str) -> int:
        ...

    def count_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> int:
        ...


class ContextBudgetExceeded(ValueError):
    pass


@dataclass(frozen=True)
class ContextManagementPolicy:
    output_reserve_tokens: int
    minimum_safety_margin_tokens: int = 256
    safety_margin_ratio: float = 0.02
    keep_recent_items: int = 4
    fallback_context_window_tokens: int = 32_768

    def __post_init__(self) -> None:
        if self.output_reserve_tokens < 1:
            raise ValueError("output_reserve_tokens must be positive")
        if self.minimum_safety_margin_tokens < 0:
            raise ValueError(
                "minimum_safety_margin_tokens must not be negative"
            )
        if not 0 <= self.safety_margin_ratio < 1:
            raise ValueError(
                "safety_margin_ratio must be in the range [0, 1)"
            )
        if self.keep_recent_items < 1:
            raise ValueError("keep_recent_items must be positive")
        if self.fallback_context_window_tokens < 1:
            raise ValueError(
                "fallback_context_window_tokens must be positive"
            )


@dataclass(frozen=True)
class SemanticCompactionRecord:
    summary_id: str
    section: str
    source_refs: tuple[str, ...]
    source_count: int
    source_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "section": self.section,
            "source_refs": list(self.source_refs),
            "source_count": self.source_count,
            "source_digest": self.source_digest,
        }


@dataclass(frozen=True)
class ManagedContextManifest:
    context_id: str
    scope: str
    model_id: str
    context_window_tokens: int
    output_reserve_tokens: int
    safety_margin_tokens: int
    max_input_tokens: int
    used_input_tokens: int
    token_counter: str
    exact_model_tokenizer: bool
    compacted_sections: tuple[SemanticCompactionRecord, ...] = ()
    omitted_refs: tuple[tuple[str, str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "scope": self.scope,
            "model_id": self.model_id,
            "context_window_tokens": self.context_window_tokens,
            "output_reserve_tokens": self.output_reserve_tokens,
            "safety_margin_tokens": self.safety_margin_tokens,
            "max_input_tokens": self.max_input_tokens,
            "used_input_tokens": self.used_input_tokens,
            "token_counter": self.token_counter,
            "exact_model_tokenizer": self.exact_model_tokenizer,
            "compacted_sections": [
                item.to_dict() for item in self.compacted_sections
            ],
            "omitted_refs": [
                {
                    "section": section,
                    "ref": reference,
                    "reason": reason,
                }
                for section, reference, reason in self.omitted_refs
            ],
        }


@dataclass(frozen=True)
class ManagedContextResult:
    authoritative: dict[str, Any]
    continuity: dict[str, Any]
    advisory: dict[str, list[dict[str, Any]]]
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    manifest: ManagedContextManifest


class CjkHeuristicTokenCounter:
    """Provider-independent conservative fallback adapted from OpenClaw."""

    name = "cjk_heuristic_v1"
    exact_model_tokenizer = False
    _common_cjk = re.compile(
        r"[\u3000-\u319f\u4e00-\u9fff\uac00-\ud7af\uff01-\uff60]"
    )

    def count_text(self, text: str) -> int:
        common_cjk = len(self._common_cjk.findall(text))
        weighted_chars = len(text) + common_cjk * 3
        return math.ceil(weighted_chars / 4)

    def count_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> int:
        message_tokens = sum(
            self.count_text(_canonical_json(message)) + 4
            for message in messages
        )
        tool_tokens = self.count_text(_canonical_json(tools))
        return message_tokens + tool_tokens + 8


class HuggingFaceTokenCounter:
    """Count a request with one concrete model tokenizer."""

    exact_model_tokenizer = True

    def __init__(self, tokenizer: Any, *, tokenizer_id: str) -> None:
        self._tokenizer = tokenizer
        self.name = f"huggingface:{tokenizer_id}"

    @classmethod
    def from_local_model(cls, tokenizer_id: str) -> HuggingFaceTokenCounter:
        try:
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "The optional 'rag' dependencies are required for a "
                "Hugging Face tokenizer."
            ) from exc
        tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_id,
            local_files_only=True,
            trust_remote_code=False,
        )
        return cls(tokenizer, tokenizer_id=tokenizer_id)

    def count_text(self, text: str) -> int:
        encoded = self._tokenizer.encode(
            text,
            add_special_tokens=False,
        )
        return len(encoded)

    def count_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> int:
        apply_template = getattr(
            self._tokenizer,
            "apply_chat_template",
            None,
        )
        if callable(apply_template):
            try:
                encoded = apply_template(
                    messages,
                    tools=tools or None,
                    tokenize=True,
                    add_generation_prompt=True,
                )
                return _encoded_length(encoded)
            except (TypeError, ValueError, KeyError, AttributeError):
                pass
        return self.count_text(
            _canonical_json({
                "messages": messages,
                "tools": tools,
                "add_generation_prompt": True,
            })
        )


class StructuredSemanticCompactor:
    """Produce deterministic, provenance-preserving summaries of old records."""

    _semantic_keys = (
        "command",
        "status",
        "message",
        "intent",
        "reason",
        "reason_code",
        "robot_id",
        "task_id",
        "subtask_id",
        "floor",
        "area_id",
        "risk_level",
        "created_at",
        "timestamp",
    )

    def __init__(self, *, max_values_per_key: int = 8) -> None:
        if max_values_per_key < 1:
            raise ValueError("max_values_per_key must be positive")
        self.max_values_per_key = max_values_per_key

    def compact(
        self,
        section: str,
        items: Iterable[dict[str, Any]],
    ) -> tuple[dict[str, Any], SemanticCompactionRecord]:
        records = [dict(item) for item in items]
        canonical = _canonical_json(records)
        digest = sha256(canonical.encode("utf-8")).hexdigest()[:16]
        refs = tuple(
            _item_ref(item, index=index)
            for index, item in enumerate(records)
        )
        facts: dict[str, list[Any]] = {}
        for item in records:
            extracted: dict[str, list[Any]] = {}
            _collect_semantic_values(item, extracted)
            for key in self._semantic_keys:
                for value in extracted.get(key, []):
                    values = facts.setdefault(key, [])
                    if value not in values and len(values) < (
                        self.max_values_per_key
                    ):
                        values.append(value)
        summary_id = f"{section}:summary:{digest}"
        summary = {
            "summary_id": summary_id,
            "summary_type": "structured_semantic_history",
            "source_section": section,
            "source_count": len(records),
            "source_digest": digest,
            "semantic_facts": facts,
            "advisory_only": True,
        }
        return summary, SemanticCompactionRecord(
            summary_id=summary_id,
            section=section,
            source_refs=refs,
            source_count=len(records),
            source_digest=digest,
        )


class ModelAwareContextManager:
    """Fit trusted and advisory context into one model-specific request."""

    def __init__(
        self,
        *,
        runtime: Any | None,
        task: str,
        policy: ContextManagementPolicy,
        token_counter: TokenCounter | None = None,
        model_descriptor: ModelDescriptor | None = None,
        compactor: StructuredSemanticCompactor | None = None,
    ) -> None:
        self.runtime = runtime
        self.task = task
        self.policy = policy
        self._explicit_counter = token_counter
        self._explicit_descriptor = model_descriptor
        self.compactor = compactor or StructuredSemanticCompactor()
        self._resolved: tuple[ModelDescriptor, TokenCounter] | None = None

    def fit(
        self,
        *,
        scope: str,
        context_id: str,
        authoritative: dict[str, Any],
        continuity: dict[str, Any] | None,
        advisory: dict[str, Iterable[dict[str, Any]]],
        build_request: RequestBuilder,
        compact_sections: Iterable[str] = (),
    ) -> ManagedContextResult:
        descriptor, counter = self._resolve()
        output_reserve = min(
            self.policy.output_reserve_tokens,
            max(1, descriptor.max_tokens),
        )
        safety_margin = max(
            self.policy.minimum_safety_margin_tokens,
            math.ceil(
                descriptor.context_window
                * self.policy.safety_margin_ratio
            ),
        )
        max_input = (
            descriptor.context_window
            - output_reserve
            - safety_margin
        )
        if max_input < 1:
            raise ContextBudgetExceeded(
                "Model context window leaves no room for planner input after "
                "output and safety reserves."
            )

        continuity_value = dict(continuity or {})
        compact_names = set(compact_sections)
        prepared: dict[str, list[dict[str, Any]]] = {}
        compactions: list[SemanticCompactionRecord] = []
        for name, raw_items in advisory.items():
            items = [
                dict(item) for item in raw_items
                if isinstance(item, dict)
            ]
            if (
                name in compact_names
                and len(items) > self.policy.keep_recent_items
            ):
                split = len(items) - self.policy.keep_recent_items
                summary, record = self.compactor.compact(
                    name,
                    items[:split],
                )
                items = [summary, *items[split:]]
                compactions.append(record)
            prepared[name] = items

        policy_value = self._context_policy(
            scope=scope,
            descriptor=descriptor,
            counter=counter,
            output_reserve=output_reserve,
            safety_margin=safety_margin,
            max_input=max_input,
            compaction_count=len(compactions),
        )
        messages, tools = build_request(
            authoritative,
            continuity_value,
            prepared,
            policy_value,
        )
        used = counter.count_request(messages, tools)
        omitted: list[tuple[str, str, str]] = []
        selected = prepared

        if used > max_input:
            selected = {name: [] for name in prepared}
            critical_messages, critical_tools = build_request(
                authoritative,
                continuity_value,
                selected,
                policy_value,
            )
            critical_tokens = counter.count_request(
                critical_messages,
                critical_tools,
            )
            if critical_tokens > max_input:
                raise ContextBudgetExceeded(
                    "Safety-critical planner context and tool schemas require "
                    f"{critical_tokens} tokens but the model input budget is "
                    f"{max_input}; no authoritative content was truncated."
                )
            for name, items in prepared.items():
                admission_order = sorted(
                    range(len(items)),
                    key=lambda index: (
                        _is_semantic_summary(items[index]),
                        index,
                    ),
                )
                admitted_indexes: set[int] = set()
                for index in admission_order:
                    candidate_indexes = admitted_indexes | {index}
                    candidate_items = [
                        item for item_index, item in enumerate(items)
                        if item_index in candidate_indexes
                    ]
                    candidate = {
                        **selected,
                        name: candidate_items,
                    }
                    candidate_messages, candidate_tools = build_request(
                        authoritative,
                        continuity_value,
                        candidate,
                        policy_value,
                    )
                    candidate_tokens = counter.count_request(
                        candidate_messages,
                        candidate_tools,
                    )
                    if candidate_tokens <= max_input:
                        selected = candidate
                        admitted_indexes = candidate_indexes
                    else:
                        omitted.append((
                            name,
                            _item_ref(items[index], index=index),
                            "model_token_budget",
                        ))
            messages, tools = build_request(
                authoritative,
                continuity_value,
                selected,
                policy_value,
            )
            used = counter.count_request(messages, tools)

        final_policy = {
            **policy_value,
            "omitted_advisory_items": len(omitted),
        }
        messages, tools = build_request(
            authoritative,
            continuity_value,
            selected,
            final_policy,
        )
        used = counter.count_request(messages, tools)
        if used > max_input:
            raise ContextBudgetExceeded(
                "Final context manifest pushed the request over the model "
                "input budget."
            )
        manifest = ManagedContextManifest(
            context_id=context_id,
            scope=scope,
            model_id=descriptor.id,
            context_window_tokens=descriptor.context_window,
            output_reserve_tokens=output_reserve,
            safety_margin_tokens=safety_margin,
            max_input_tokens=max_input,
            used_input_tokens=used,
            token_counter=counter.name,
            exact_model_tokenizer=counter.exact_model_tokenizer,
            compacted_sections=tuple(compactions),
            omitted_refs=tuple(omitted),
        )
        return ManagedContextResult(
            authoritative=dict(authoritative),
            continuity=continuity_value,
            advisory=selected,
            messages=messages,
            tools=tools,
            manifest=manifest,
        )

    def _resolve(self) -> tuple[ModelDescriptor, TokenCounter]:
        if self._resolved is not None:
            return self._resolved
        descriptor = self._explicit_descriptor or self._runtime_descriptor()
        counter = self._explicit_counter
        if counter is None and descriptor.tokenizer_id:
            try:
                counter = HuggingFaceTokenCounter.from_local_model(
                    descriptor.tokenizer_id
                )
            except (RuntimeError, OSError, ValueError):
                counter = None
        if counter is None:
            counter = CjkHeuristicTokenCounter()
        self._resolved = descriptor, counter
        return self._resolved

    def _runtime_descriptor(self) -> ModelDescriptor:
        select_model = getattr(self.runtime, "select_model", None)
        if callable(select_model):
            try:
                descriptor = select_model(task=self.task)
                if isinstance(descriptor, ModelDescriptor):
                    return descriptor
            except Exception:
                pass
        status_fn = getattr(self.runtime, "status", None)
        status = status_fn() if callable(status_fn) else {}
        model_id = (
            str(status.get("model") or "unknown")
            if isinstance(status, dict)
            else "unknown"
        )
        return ModelDescriptor(
            id=model_id,
            name=model_id,
            provider="unknown",
            context_window=self.policy.fallback_context_window_tokens,
            max_tokens=self.policy.output_reserve_tokens,
            supports_tools=True,
        )

    @staticmethod
    def _context_policy(
        *,
        scope: str,
        descriptor: ModelDescriptor,
        counter: TokenCounter,
        output_reserve: int,
        safety_margin: int,
        max_input: int,
        compaction_count: int,
    ) -> dict[str, Any]:
        return {
            "scope": scope,
            "model_id": descriptor.id,
            "context_window_tokens": descriptor.context_window,
            "output_reserve_tokens": output_reserve,
            "safety_margin_tokens": safety_margin,
            "max_input_tokens": max_input,
            "token_counter": counter.name,
            "exact_model_tokenizer": counter.exact_model_tokenizer,
            "semantic_compaction_count": compaction_count,
            "safety_critical_context_preserved": True,
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _encoded_length(encoded: Any) -> int:
    if hasattr(encoded, "shape"):
        shape = getattr(encoded, "shape")
        if shape:
            return int(shape[-1])
    if isinstance(encoded, list):
        if encoded and isinstance(encoded[0], list):
            return len(encoded[0])
        return len(encoded)
    return len(encoded)


def _item_ref(value: dict[str, Any], *, index: int) -> str:
    for key in (
        "record_id",
        "event_id",
        "knowledge_id",
        "summary_id",
        "task_id",
        "id",
    ):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    digest = sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()[:12]
    return f"item-{index}:{digest}"


def _collect_semantic_values(
    value: Any,
    output: dict[str, list[Any]],
) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if (
                key in StructuredSemanticCompactor._semantic_keys
                and isinstance(nested, str | int | float | bool)
            ):
                output.setdefault(key, []).append(nested)
            _collect_semantic_values(nested, output)
    elif isinstance(value, list):
        for nested in value:
            _collect_semantic_values(nested, output)


def _is_semantic_summary(value: dict[str, Any]) -> bool:
    return value.get("summary_type") == "structured_semantic_history"
