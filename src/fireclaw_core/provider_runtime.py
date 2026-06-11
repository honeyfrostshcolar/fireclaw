"""Provider runtime abstraction for FireClaw mission planning.

Provides a ``ProviderRuntime`` protocol that decouples the LLM mission
planner from a specific provider/model pair, and a
``FallbackProviderRuntime`` that tries configured model candidates in
order — mirroring OpenClaw's ``runWithModelFallback`` pattern.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from fireclaw_core.model_catalog import ModelCatalog, ModelDescriptor
from fireclaw_core.provider import (
    ChatCompletion,
    ModelProvider,
    ProviderAPIError,
    ProviderError,
    ProviderTimeoutError,
)


# ---------------------------------------------------------------------------
# Error types (normalised from provider errors)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FallbackAttempt:
    """Record of a single provider/model attempt within a fallback run."""

    provider_name: str
    model_id: str
    success: bool
    error_type: str | None = None
    error_message: str | None = None
    latency_ms: float = 0.0


@dataclass(frozen=True)
class FallbackSummaryError(Exception):
    """Raised when all fallback candidates have been exhausted.

    Carries per-attempt details so callers can build informative
    user-facing messages.
    """

    attempts: list[FallbackAttempt]
    message: str = ""

    def __post_init__(self) -> None:
        # frozen dataclass cannot call super().__init__ normally;
        # Exception.__init__ is called via object.__new__.
        # We set the args tuple so str(exc) works.
        object.__setattr__(self, "args", (self.message,))

    def __str__(self) -> str:
        if self.message:
            return self.message
        parts = []
        for a in self.attempts:
            status = "ok" if a.success else f"FAIL({a.error_type}: {a.error_message})"
            parts.append(f"  {a.provider_name}/{a.model_id}: {status}")
        return "All fallback candidates exhausted:\n" + "\n".join(parts)


# ---------------------------------------------------------------------------
# ProviderRuntime protocol
# ---------------------------------------------------------------------------


class ProviderRuntime(Protocol):
    """Protocol that the LLM mission planner uses to obtain completions.

    Implementations may wrap a single provider/model pair, perform
    fallback across multiple candidates, or delegate to a remote service.

    Mirrors OpenClaw's model control-plane pattern where the runtime
    owns model selection and the caller observes which model was used.
    """

    def select_model(
        self,
        *,
        task: str,
        preferred_model: str | None = None,
    ) -> ModelDescriptor:
        """Select a model for *task* and return its descriptor.

        When *preferred_model* is given and is a valid candidate, it is
        preferred.  Otherwise the runtime chooses the best available
        candidate (e.g. the first in the fallback chain).
        """
        ...

    def chat_completion(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> ChatCompletion:
        """Execute a chat completion request and return the result."""
        ...

    def status(self) -> dict[str, Any]:
        """Return runtime status information (provider, model, etc.)."""
        ...


# ---------------------------------------------------------------------------
# SimpleProviderRuntime — wraps a single (provider, model_id) pair
# ---------------------------------------------------------------------------


class SimpleProviderRuntime:
    """Minimal runtime that delegates directly to a single ``ModelProvider``."""

    def __init__(
        self,
        provider: ModelProvider,
        model_id: str,
        catalog: ModelCatalog | None = None,
    ) -> None:
        self._provider = provider
        self._model_id = model_id
        self._catalog = catalog

    def select_model(
        self,
        *,
        task: str,
        preferred_model: str | None = None,
    ) -> ModelDescriptor:
        """Return the descriptor for this runtime's single model.

        Uses the catalog when available; otherwise returns a descriptor
        with sensible defaults.
        """
        if self._catalog is not None:
            try:
                return self._catalog.resolve(self._model_id)
            except Exception:
                pass
        return ModelDescriptor(
            id=self._model_id,
            name=self._model_id,
            provider="unknown",
            context_window=8192,
            max_tokens=4096,
            supports_tools=True,
        )

    def chat_completion(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> ChatCompletion:
        return self._provider.chat_completion(
            messages=messages,
            model=self._model_id,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def status(self) -> dict[str, Any]:
        return {
            "type": "simple",
            "model": self._model_id,
        }


# ---------------------------------------------------------------------------
# FallbackProviderRuntime — tries candidates in order
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelCandidate:
    """A single provider/model pair to try during fallback."""

    provider_name: str
    model_id: str


@dataclass
class FallbackProviderRuntime:
    """Runtime that tries configured model candidates in order.

    Mirrors OpenClaw's ``runWithModelFallback``: iterates over candidates,
    records each attempt, and returns the first successful result.  When
    all candidates fail, raises ``FallbackSummaryError`` with structured
    attempt details.

    After a successful ``chat_completion`` call, ``last_used_model``
    reflects the candidate that actually produced the result — not
    necessarily the first candidate.
    """

    provider_registry: dict[str, ModelProvider]
    candidates: list[ModelCandidate]
    catalog: ModelCatalog | None = None
    _last_used_model: str = field(init=False, repr=False, default="")

    def _build_descriptor(self, candidate: ModelCandidate) -> ModelDescriptor:
        """Build a descriptor from the catalog or with sensible defaults."""
        if self.catalog is not None:
            try:
                return self.catalog.resolve(candidate.model_id)
            except Exception:
                pass
        return ModelDescriptor(
            id=candidate.model_id,
            name=candidate.model_id,
            provider=candidate.provider_name,
            context_window=8192,
            max_tokens=4096,
            supports_tools=True,
        )

    def select_model(
        self,
        *,
        task: str,
        preferred_model: str | None = None,
    ) -> ModelDescriptor:
        """Select the best candidate and return its descriptor.

        When *preferred_model* matches a candidate and is present in the
        catalog (when a catalog is configured), that candidate is chosen.
        Otherwise the first candidate is used.
        """
        if not self.candidates:
            raise FallbackSummaryError(
                attempts=[],
                message="No model candidates configured for selection.",
            )

        if preferred_model is not None:
            for c in self.candidates:
                if c.model_id == preferred_model:
                    return self._build_descriptor(c)

        return self._build_descriptor(self.candidates[0])

    def chat_completion(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> ChatCompletion:
        """Try each candidate in order; return the first successful result."""
        if not self.candidates:
            raise FallbackSummaryError(
                attempts=[],
                message="No model candidates configured for fallback.",
            )

        attempts: list[FallbackAttempt] = []
        last_error: Exception | None = None

        for candidate in self.candidates:
            provider = self.provider_registry.get(candidate.provider_name)
            if provider is None:
                attempts.append(
                    FallbackAttempt(
                        provider_name=candidate.provider_name,
                        model_id=candidate.model_id,
                        success=False,
                        error_type="ProviderNotFound",
                        error_message=f"Provider '{candidate.provider_name}' not found in registry.",
                    )
                )
                continue

            start = time.monotonic()
            try:
                result = provider.chat_completion(
                    messages=messages,
                    model=candidate.model_id,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                latency_ms = (time.monotonic() - start) * 1000
                attempts.append(
                    FallbackAttempt(
                        provider_name=candidate.provider_name,
                        model_id=candidate.model_id,
                        success=True,
                        latency_ms=round(latency_ms, 2),
                    )
                )
                self._last_used_model = candidate.model_id
                return result
            except ProviderTimeoutError as exc:
                latency_ms = (time.monotonic() - start) * 1000
                attempts.append(
                    FallbackAttempt(
                        provider_name=candidate.provider_name,
                        model_id=candidate.model_id,
                        success=False,
                        error_type="ProviderTimeoutError",
                        error_message=str(exc),
                        latency_ms=round(latency_ms, 2),
                    )
                )
                last_error = exc
            except ProviderAPIError as exc:
                latency_ms = (time.monotonic() - start) * 1000
                attempts.append(
                    FallbackAttempt(
                        provider_name=candidate.provider_name,
                        model_id=candidate.model_id,
                        success=False,
                        error_type="ProviderAPIError",
                        error_message=str(exc),
                        latency_ms=round(latency_ms, 2),
                    )
                )
                last_error = exc
            except ProviderError as exc:
                latency_ms = (time.monotonic() - start) * 1000
                attempts.append(
                    FallbackAttempt(
                        provider_name=candidate.provider_name,
                        model_id=candidate.model_id,
                        success=False,
                        error_type="ProviderError",
                        error_message=str(exc),
                        latency_ms=round(latency_ms, 2),
                    )
                )
                last_error = exc

        # All candidates exhausted
        raise FallbackSummaryError(
            attempts=attempts,
            message=f"All {len(attempts)} fallback candidates failed. "
            f"Last error: {last_error}",
        )

    def status(self) -> dict[str, Any]:
        # After a successful completion, report the model that actually
        # produced the result.  Before any completion, report the first
        # candidate as a hint.
        if self._last_used_model:
            active_model = self._last_used_model
        elif self.candidates:
            active_model = self.candidates[0].model_id
        else:
            active_model = "unknown"
        return {
            "type": "fallback",
            "model": active_model,
            "candidates": [
                {"provider": c.provider_name, "model": c.model_id}
                for c in self.candidates
            ],
            "candidate_count": len(self.candidates),
        }
