"""Shared model/tool turn boundary for Mission and Robot agents."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import time
from typing import Any, Protocol

from fireclaw_core.context.manager import (
    ContextBudgetExceeded,
    ManagedContextResult,
    ModelAwareContextManager,
)
from fireclaw_core.provider.provider import (
    ChatCompletion,
    ProviderAPIError,
    ProviderError,
    ProviderTimeoutError,
    ToolCall,
)
from fireclaw_core.provider.provider_runtime import (
    FallbackSummaryError,
    ProviderRuntime,
)


AgentRequestBuilder = Callable[
    [
        dict[str, Any],
        dict[str, Any],
        dict[str, list[dict[str, Any]]],
        dict[str, Any],
    ],
    tuple[list[dict[str, Any]], list[dict[str, Any]]],
]
AgentHarnessTraceSink = Callable[[dict[str, Any]], None]


class AgentHarnessError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        managed_context: ManagedContextResult | None = None,
        response: ChatCompletion | None = None,
    ) -> None:
        self.code = code
        self.managed_context = managed_context
        self.response = response
        super().__init__(message)


@dataclass(frozen=True)
class AgentHarnessSupport:
    supported: bool
    reason: str | None = None
    priority: int = 0


@dataclass(frozen=True)
class AgentHarnessAttempt:
    role: str
    run_id: str
    scope: str
    context_id: str
    authoritative: dict[str, Any]
    continuity: dict[str, Any]
    advisory: dict[str, Iterable[dict[str, Any]]]
    build_request: AgentRequestBuilder
    compact_sections: tuple[str, ...] = ()
    minimum_tool_calls: int = 1
    maximum_tool_calls: int | None = 1
    allowed_tool_names: frozenset[str] | None = None
    cancellation_requested: Callable[[], bool] | None = None
    temperature: float = 0.0


@dataclass(frozen=True)
class AgentHarnessAttemptResult:
    response: ChatCompletion
    tool_calls: tuple[ToolCall, ...]
    managed_context: ManagedContextResult
    classification: str
    latency_ms: float

    @property
    def context_manifest(self) -> dict[str, Any]:
        return self.managed_context.manifest.to_dict()


class AgentHarness(Protocol):
    id: str
    label: str

    def supports(self, *, role: str, runtime: ProviderRuntime) -> AgentHarnessSupport:
        ...

    def run_attempt(self, attempt: AgentHarnessAttempt) -> AgentHarnessAttemptResult:
        ...

    def dispose(self) -> None:
        ...


class ProviderAgentHarness:
    """One host-enforced provider/tool round used by every LLM agent role."""

    id = "fireclaw.provider"
    label = "FireClaw Provider Harness"

    def __init__(
        self,
        *,
        provider_runtime: ProviderRuntime,
        context_manager: ModelAwareContextManager,
        trace_sink: AgentHarnessTraceSink | None = None,
        harness_id: str | None = None,
    ) -> None:
        if harness_id is not None:
            if not harness_id.strip():
                raise ValueError("harness_id must not be empty")
            self.id = harness_id.strip()
        self.provider_runtime = provider_runtime
        self.context_manager = context_manager
        self.trace_sink = trace_sink
        self._disposed = False

    def supports(
        self,
        *,
        role: str,
        runtime: ProviderRuntime,
    ) -> AgentHarnessSupport:
        if self._disposed:
            return AgentHarnessSupport(False, "harness_disposed")
        if runtime is not self.provider_runtime:
            return AgentHarnessSupport(False, "provider_runtime_mismatch")
        if not role.strip():
            return AgentHarnessSupport(False, "role_missing")
        return AgentHarnessSupport(True, priority=100)

    def run_attempt(
        self,
        attempt: AgentHarnessAttempt,
    ) -> AgentHarnessAttemptResult:
        if self._disposed:
            raise AgentHarnessError("harness_disposed", "Agent Harness is disposed.")
        if self._cancelled(attempt):
            raise AgentHarnessError(
                "cancelled_before_provider_call",
                "Agent turn was cancelled before the provider call.",
            )
        started = time.monotonic()
        managed: ManagedContextResult | None = None
        response: ChatCompletion | None = None
        try:
            managed = self.context_manager.fit(
                scope=attempt.scope,
                context_id=attempt.context_id,
                authoritative=attempt.authoritative,
                continuity=attempt.continuity,
                advisory=attempt.advisory,
                build_request=attempt.build_request,
                compact_sections=attempt.compact_sections,
            )
            visible_tool_names = _normalize_tool_schemas(managed.tools)
            if attempt.allowed_tool_names is not None:
                undeclared = attempt.allowed_tool_names - visible_tool_names
                if undeclared:
                    raise AgentHarnessError(
                        "tool_policy_mismatch",
                        (
                            "Agent tool policy references tools absent from the "
                            f"projected schema: {sorted(undeclared)}"
                        ),
                        managed_context=managed,
                    )
                allowed_tool_names = set(attempt.allowed_tool_names)
            else:
                allowed_tool_names = visible_tool_names
            if self._cancelled(attempt):
                raise AgentHarnessError(
                    "cancelled_before_provider_call",
                    "Agent turn was cancelled before the provider call.",
                    managed_context=managed,
                )
            response = self.provider_runtime.chat_completion(
                messages=managed.messages,
                tools=managed.tools,
                temperature=attempt.temperature,
                max_tokens=managed.manifest.output_reserve_tokens,
            )
            if self._cancelled(attempt):
                raise AgentHarnessError(
                    "cancelled_after_provider_call",
                    "Agent turn was cancelled after the provider call.",
                    managed_context=managed,
                    response=response,
                )
            calls = tuple(response.tool_calls or ())
            _validate_tool_calls(
                calls,
                minimum=attempt.minimum_tool_calls,
                maximum=attempt.maximum_tool_calls,
                allowed_names=allowed_tool_names,
                managed_context=managed,
                response=response,
            )
            result = AgentHarnessAttemptResult(
                response=response,
                tool_calls=calls,
                managed_context=managed,
                classification="ok",
                latency_ms=round((time.monotonic() - started) * 1000, 2),
            )
            self._trace(attempt, result=result)
            return result
        except AgentHarnessError as exc:
            self._trace(attempt, error=exc, started=started)
            raise
        except ContextBudgetExceeded as exc:
            error = AgentHarnessError("context_budget_exceeded", str(exc))
            self._trace(attempt, error=error, started=started)
            raise error from exc
        except ProviderTimeoutError as exc:
            error = AgentHarnessError(
                "provider_timeout",
                "Provider call timed out.",
                managed_context=managed,
            )
            self._trace(attempt, error=error, started=started)
            raise error from exc
        except ProviderAPIError as exc:
            error = AgentHarnessError(
                "provider_api_error",
                f"Provider API error: {exc}",
                managed_context=managed,
            )
            self._trace(attempt, error=error, started=started)
            raise error from exc
        except FallbackSummaryError as exc:
            error = AgentHarnessError(
                "provider_fallback_exhausted",
                f"Provider fallback exhausted: {exc}",
                managed_context=managed,
            )
            self._trace(attempt, error=error, started=started)
            raise error from exc
        except ProviderError as exc:
            error = AgentHarnessError(
                "provider_error",
                f"Provider call failed: {exc}",
                managed_context=managed,
            )
            self._trace(attempt, error=error, started=started)
            raise error from exc

    def dispose(self) -> None:
        self._disposed = True

    def _trace(
        self,
        attempt: AgentHarnessAttempt,
        *,
        result: AgentHarnessAttemptResult | None = None,
        error: AgentHarnessError | None = None,
        started: float | None = None,
    ) -> None:
        if self.trace_sink is None:
            return
        latency_ms = (
            result.latency_ms
            if result is not None
            else round((time.monotonic() - (started or time.monotonic())) * 1000, 2)
        )
        self.trace_sink(
            {
                "harness_id": self.id,
                "role": attempt.role,
                "run_id": attempt.run_id,
                "scope": attempt.scope,
                "context_id": attempt.context_id,
                "status": "ok" if error is None else "error",
                "classification": (
                    result.classification
                    if result is not None
                    else error.code if error is not None else "unknown"
                ),
                "latency_ms": latency_ms,
                "tool_names": (
                    [call.name for call in result.tool_calls]
                    if result is not None
                    else []
                ),
                "context_manifest": (
                    result.context_manifest if result is not None else None
                ),
            }
        )

    @staticmethod
    def _cancelled(attempt: AgentHarnessAttempt) -> bool:
        return bool(
            attempt.cancellation_requested is not None
            and attempt.cancellation_requested()
        )


def _normalize_tool_schemas(tools: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise AgentHarnessError(
                "malformed_tool_schema",
                f"Tool schema at index {index} is not a function tool.",
            )
        function = tool.get("function")
        if not isinstance(function, dict):
            raise AgentHarnessError(
                "malformed_tool_schema",
                f"Tool schema at index {index} has no function object.",
            )
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            raise AgentHarnessError(
                "malformed_tool_schema",
                f"Tool schema at index {index} has no valid name.",
            )
        normalized = name.strip()
        if normalized in names:
            raise AgentHarnessError(
                "duplicate_tool_name",
                f"Tool {normalized!r} is projected more than once.",
            )
        parameters = function.get("parameters")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise AgentHarnessError(
                "malformed_tool_schema",
                f"Tool {normalized!r} must declare an object parameter schema.",
            )
        names.add(normalized)
    return names


def _validate_tool_calls(
    calls: tuple[ToolCall, ...],
    *,
    minimum: int,
    maximum: int | None,
    allowed_names: set[str],
    managed_context: ManagedContextResult,
    response: ChatCompletion,
) -> None:
    if len(calls) < minimum or (maximum is not None and len(calls) > maximum):
        if minimum == maximum == 1:
            expected = "exactly one"
        elif maximum is not None:
            expected = f"between {minimum} and {maximum}"
        else:
            expected = f"at least {minimum}"
        raise AgentHarnessError(
            "invalid_tool_call_count",
            (
                f"Agent must return {expected} tool call"
                f"{'' if minimum == maximum == 1 else 's'}; "
                f"received {len(calls)}."
            ),
            managed_context=managed_context,
            response=response,
        )
    for call in calls:
        if call.name not in allowed_names:
            raise AgentHarnessError(
                "unexpected_tool_name",
                f"Agent returned unexposed tool {call.name!r}.",
                managed_context=managed_context,
                response=response,
            )
        if not isinstance(call.arguments, dict):
            raise AgentHarnessError(
                "malformed_tool_arguments",
                f"Tool {call.name!r} returned non-object arguments.",
                managed_context=managed_context,
                response=response,
            )


def register_agent_harness(
    plugin_host: Any,
    harness: AgentHarness,
    *,
    owner_plugin_id: str,
) -> None:
    existing = plugin_host.get("agent_harness", harness.id)
    if existing is not None:
        if existing.value is harness:
            return
        raise ValueError(
            f"Agent Harness {harness.id!r} is already owned by "
            f"{existing.owner_plugin_id!r}."
        )
    plugin_host.activate(
        owner_plugin_id,
        lambda api: api.register_agent_harness(harness),
        name=harness.label,
        source="agent_harness",
    )
