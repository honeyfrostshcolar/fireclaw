"""Tests for fireclaw_core.provider_runtime — ProviderRuntime protocol and fallback."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from fireclaw_core.provider.model_catalog import ModelCatalog, ModelDescriptor
from fireclaw_core.provider.provider import (
    ChatCompletion,
    ProviderAPIError,
    ProviderError,
    ProviderTimeoutError,
    TokenUsage,
    ToolCall,
)
from fireclaw_core.provider.provider_runtime import (
    FallbackAttempt,
    FallbackProviderRuntime,
    FallbackSummaryError,
    ModelCandidate,
    SimpleProviderRuntime,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_completion(model: str = "gpt-4") -> ChatCompletion:
    return ChatCompletion(
        content="ok",
        tool_calls=None,
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        model=model,
        finish_reason="stop",
    )


def _make_provider(response: ChatCompletion | None = None, side_effect: Exception | None = None) -> MagicMock:
    provider = MagicMock()
    if side_effect is not None:
        provider.chat_completion.side_effect = side_effect
    elif response is not None:
        provider.chat_completion.return_value = response
    return provider


# ---------------------------------------------------------------------------
# SimpleProviderRuntime
# ---------------------------------------------------------------------------


class TestSimpleProviderRuntime:
    def test_chat_completion_delegates_to_provider(self):
        completion = _make_completion()
        provider = _make_provider(response=completion)
        runtime = SimpleProviderRuntime(provider, "gpt-4")

        result = runtime.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        assert result is completion
        provider.chat_completion.assert_called_once_with(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4",
            tools=[],
            temperature=0.0,
            max_tokens=4096,
        )

    def test_status_returns_simple_info(self):
        provider = MagicMock()
        runtime = SimpleProviderRuntime(provider, "gpt-4")

        status = runtime.status()

        assert status["type"] == "simple"
        assert status["model"] == "gpt-4"

    def test_select_model_returns_descriptor(self):
        provider = MagicMock()
        runtime = SimpleProviderRuntime(provider, "gpt-4")

        desc = runtime.select_model(task="test task")

        assert desc.id == "gpt-4"
        assert desc.name == "gpt-4"
        assert desc.supports_tools is True

    def test_select_model_uses_catalog_when_available(self):
        catalog = MagicMock()
        catalog.resolve.return_value = ModelDescriptor(
            id="gpt-4", name="GPT-4", provider="openai",
            context_window=128000, max_tokens=16384, supports_tools=True,
        )
        provider = MagicMock()
        runtime = SimpleProviderRuntime(provider, "gpt-4", catalog=catalog)

        desc = runtime.select_model(task="test task")

        assert desc.context_window == 128000
        catalog.resolve.assert_called_once_with("gpt-4")


# ---------------------------------------------------------------------------
# FallbackProviderRuntime
# ---------------------------------------------------------------------------


class TestFallbackProviderRuntime:
    def test_first_candidate_succeeds(self):
        completion = _make_completion("gpt-4")
        p1 = _make_provider(response=completion)
        p2 = _make_provider(response=_make_completion("gpt-3.5"))

        runtime = FallbackProviderRuntime(
            provider_registry={"openai": p1, "backup": p2},
            candidates=[
                ModelCandidate("openai", "gpt-4"),
                ModelCandidate("backup", "gpt-3.5"),
            ],
        )

        result = runtime.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        assert result is completion
        p1.chat_completion.assert_called_once()
        p2.chat_completion.assert_not_called()

    def test_fallback_to_second_on_timeout(self):
        completion = _make_completion("gpt-3.5")
        p1 = _make_provider(side_effect=ProviderTimeoutError("timeout"))
        p2 = _make_provider(response=completion)

        runtime = FallbackProviderRuntime(
            provider_registry={"openai": p1, "backup": p2},
            candidates=[
                ModelCandidate("openai", "gpt-4"),
                ModelCandidate("backup", "gpt-3.5"),
            ],
        )

        result = runtime.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        assert result is completion
        p1.chat_completion.assert_called_once()
        p2.chat_completion.assert_called_once()

    def test_fallback_to_second_on_api_error(self):
        completion = _make_completion("claude")
        p1 = _make_provider(side_effect=ProviderAPIError(status_code=500, message="server error"))
        p2 = _make_provider(response=completion)

        runtime = FallbackProviderRuntime(
            provider_registry={"openai": p1, "anthropic": p2},
            candidates=[
                ModelCandidate("openai", "gpt-4"),
                ModelCandidate("anthropic", "claude"),
            ],
        )

        result = runtime.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        assert result is completion

    def test_all_candidates_exhausted_raises_fallback_summary(self):
        p1 = _make_provider(side_effect=ProviderTimeoutError("timeout"))
        p2 = _make_provider(side_effect=ProviderAPIError(status_code=429, message="rate limited"))

        runtime = FallbackProviderRuntime(
            provider_registry={"openai": p1, "anthropic": p2},
            candidates=[
                ModelCandidate("openai", "gpt-4"),
                ModelCandidate("anthropic", "claude"),
            ],
        )

        with pytest.raises(FallbackSummaryError) as exc_info:
            runtime.chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
            )

        assert len(exc_info.value.attempts) == 2
        assert all(not a.success for a in exc_info.value.attempts)
        assert exc_info.value.attempts[0].error_type == "ProviderTimeoutError"
        assert exc_info.value.attempts[1].error_type == "ProviderAPIError"

    def test_provider_not_in_registry_records_attempt(self):
        p1 = _make_provider(response=_make_completion("gpt-4"))

        runtime = FallbackProviderRuntime(
            provider_registry={"openai": p1},
            candidates=[
                ModelCandidate("nonexistent", "model-x"),
                ModelCandidate("openai", "gpt-4"),
            ],
        )

        result = runtime.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        assert result.model == "gpt-4"

    def test_empty_candidates_raises(self):
        runtime = FallbackProviderRuntime(
            provider_registry={},
            candidates=[],
        )

        with pytest.raises(FallbackSummaryError) as exc_info:
            runtime.chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
            )

        assert "No model candidates" in str(exc_info.value)

    def test_status_returns_fallback_info(self):
        runtime = FallbackProviderRuntime(
            provider_registry={"openai": MagicMock()},
            candidates=[
                ModelCandidate("openai", "gpt-4"),
                ModelCandidate("anthropic", "claude"),
            ],
        )

        status = runtime.status()

        assert status["type"] == "fallback"
        assert status["model"] == "gpt-4"
        assert status["candidate_count"] == 2
        assert status["candidates"][0]["provider"] == "openai"

    def test_status_model_is_first_candidate(self):
        """status() should expose the first candidate's model_id as 'model'."""
        runtime = FallbackProviderRuntime(
            provider_registry={"a": MagicMock(), "b": MagicMock()},
            candidates=[
                ModelCandidate("a", "model-alpha"),
                ModelCandidate("b", "model-beta"),
            ],
        )

        status = runtime.status()

        assert status["model"] == "model-alpha"

    def test_status_model_unknown_when_no_candidates(self):
        """status() should return 'unknown' when candidates list is empty."""
        runtime = FallbackProviderRuntime(
            provider_registry={},
            candidates=[],
        )

        status = runtime.status()

        assert status["model"] == "unknown"

    def test_status_model_updates_after_successful_completion(self):
        """status() should report the model that actually succeeded, not the first candidate."""
        p1 = _make_provider(side_effect=ProviderTimeoutError("timeout"))
        p2 = _make_provider(response=_make_completion("model-beta"))

        runtime = FallbackProviderRuntime(
            provider_registry={"a": p1, "b": p2},
            candidates=[
                ModelCandidate("a", "model-alpha"),
                ModelCandidate("b", "model-beta"),
            ],
        )

        # Before any completion: first candidate
        assert runtime.status()["model"] == "model-alpha"

        runtime.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        # After fallback succeeds on second: reports second
        assert runtime.status()["model"] == "model-beta"

    def test_generic_provider_error_triggers_fallback(self):
        completion = _make_completion("fallback-model")
        p1 = _make_provider(side_effect=ProviderError("connection refused"))
        p2 = _make_provider(response=completion)

        runtime = FallbackProviderRuntime(
            provider_registry={"primary": p1, "secondary": p2},
            candidates=[
                ModelCandidate("primary", "model-a"),
                ModelCandidate("secondary", "model-b"),
            ],
        )

        result = runtime.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )

        assert result is completion

    # --- select_model tests ---

    def test_select_model_returns_first_candidate_by_default(self):
        """Without preferred_model, select_model returns the first candidate."""
        runtime = FallbackProviderRuntime(
            provider_registry={"a": MagicMock()},
            candidates=[
                ModelCandidate("a", "model-alpha"),
                ModelCandidate("b", "model-beta"),
            ],
        )

        desc = runtime.select_model(task="test task")

        assert desc.id == "model-alpha"
        assert desc.provider == "a"

    def test_select_model_prefers_matching_candidate(self):
        """When preferred_model matches a candidate, that candidate is returned."""
        runtime = FallbackProviderRuntime(
            provider_registry={"a": MagicMock(), "b": MagicMock()},
            candidates=[
                ModelCandidate("a", "model-alpha"),
                ModelCandidate("b", "model-beta"),
            ],
        )

        desc = runtime.select_model(task="test task", preferred_model="model-beta")

        assert desc.id == "model-beta"
        assert desc.provider == "b"

    def test_select_model_falls_back_to_first_when_preferred_not_found(self):
        """When preferred_model doesn't match any candidate, first is returned."""
        runtime = FallbackProviderRuntime(
            provider_registry={"a": MagicMock()},
            candidates=[
                ModelCandidate("a", "model-alpha"),
            ],
        )

        desc = runtime.select_model(task="test task", preferred_model="nonexistent")

        assert desc.id == "model-alpha"

    def test_select_model_uses_catalog_when_available(self):
        """When a catalog is configured, select_model resolves from it."""
        catalog = MagicMock()
        catalog.resolve.return_value = ModelDescriptor(
            id="model-alpha", name="Alpha", provider="a",
            context_window=16384, max_tokens=8192, supports_tools=True,
        )
        runtime = FallbackProviderRuntime(
            provider_registry={"a": MagicMock()},
            candidates=[ModelCandidate("a", "model-alpha")],
            catalog=catalog,
        )

        desc = runtime.select_model(task="test task")

        assert desc.context_window == 16384
        catalog.resolve.assert_called_once_with("model-alpha")

    def test_select_model_empty_candidates_raises(self):
        """select_model with no candidates raises FallbackSummaryError."""
        runtime = FallbackProviderRuntime(
            provider_registry={},
            candidates=[],
        )

        with pytest.raises(FallbackSummaryError):
            runtime.select_model(task="test task")


# ---------------------------------------------------------------------------
# FallbackAttempt / FallbackSummaryError
# ---------------------------------------------------------------------------


class TestFallbackAttemptAndError:
    def test_fallback_attempt_fields(self):
        attempt = FallbackAttempt(
            provider_name="openai",
            model_id="gpt-4",
            success=False,
            error_type="ProviderTimeoutError",
            error_message="timed out",
            latency_ms=1234.5,
        )
        assert attempt.provider_name == "openai"
        assert attempt.model_id == "gpt-4"
        assert attempt.success is False
        assert attempt.error_type == "ProviderTimeoutError"
        assert attempt.latency_ms == 1234.5

    def test_fallback_summary_error_str(self):
        attempts = [
            FallbackAttempt("openai", "gpt-4", False, "ProviderTimeoutError", "timeout"),
            FallbackAttempt("anthropic", "claude", False, "ProviderAPIError", "rate limited"),
        ]
        err = FallbackSummaryError(attempts=attempts)
        msg = str(err)
        assert "openai" in msg
        assert "gpt-4" in msg
        assert "FAIL" in msg

    def test_fallback_summary_error_custom_message(self):
        err = FallbackSummaryError(attempts=[], message="custom error")
        assert str(err) == "custom error"

    def test_fallback_summary_error_is_exception(self):
        err = FallbackSummaryError(attempts=[], message="test")
        assert isinstance(err, Exception)
