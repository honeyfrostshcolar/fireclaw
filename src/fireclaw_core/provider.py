"""LLM provider abstraction for FireClaw.

Defines the ``ModelProvider`` protocol, standard data types for chat
completions, a concrete ``OpenAICompatProvider`` that talks to any
OpenAI-compatible ``/chat/completions`` endpoint via *httpx*, and a
structured error hierarchy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


# ---------------------------------------------------------------------------
# Data types (frozen dataclasses)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenUsage:
    """Token usage reported by the model."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class ToolCall:
    """A single tool/function call requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatCompletion:
    """Structured result of a chat completion request."""

    content: str | None
    tool_calls: list[ToolCall] | None
    usage: TokenUsage
    model: str
    finish_reason: str


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Base error for all provider operations."""


class ProviderTimeoutError(ProviderError):
    """The provider request timed out."""


class ProviderAPIError(ProviderError):
    """The provider returned a non-success HTTP status code."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"Provider API error {status_code}: {message}")


class ProviderAuthError(ProviderAPIError):
    """Authentication failed (HTTP 401)."""

    def __init__(self, message: str = "Invalid API key") -> None:
        super().__init__(status_code=401, message=message)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class ModelProvider(Protocol):
    """Minimal interface every LLM provider must satisfy."""

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> ChatCompletion: ...


# ---------------------------------------------------------------------------
# OpenAI-compatible implementation
# ---------------------------------------------------------------------------


class OpenAICompatProvider:
    """Provider that posts to any OpenAI-compatible ``/chat/completions`` endpoint.

    Uses *httpx* for HTTP — no dependency on the ``openai`` SDK.
    """

    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # -- public API --------------------------------------------------------

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> ChatCompletion:
        """Send a chat completion request and return a structured result."""

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools is not None:
            body["tools"] = tools

        response = self._post(body)
        return self._parse_response(response, model)

    # -- internal helpers --------------------------------------------------

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        """Execute the HTTP POST, translating transport errors."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            return httpx.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers=headers,
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(str(exc)) from exc
        except httpx.TransportError as exc:
            raise ProviderError(str(exc)) from exc

    @staticmethod
    def _parse_response(response: httpx.Response, requested_model: str) -> ChatCompletion:
        """Parse an OpenAI-format JSON response into a ``ChatCompletion``."""

        status = response.status_code
        if status == 401:
            raise ProviderAuthError()
        if status >= 400:
            try:
                data = response.json()
            except (json.JSONDecodeError, ValueError):
                data = {}
            msg = data.get("error", {}).get("message", response.text)
            raise ProviderAPIError(status_code=status, message=str(msg))

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderAPIError(
                status_code=status,
                message=f"Invalid JSON in response body: {response.text!r}",
            ) from exc

        try:
            choice = data["choices"][0]
            message = choice["message"]
            finish_reason = choice.get("finish_reason", "stop")

            # Parse tool calls (function arguments arrive as a JSON string).
            raw_tool_calls = message.get("tool_calls")
            tool_calls: list[ToolCall] | None = None
            if raw_tool_calls:
                tool_calls = []
                for tc in raw_tool_calls:
                    func = tc["function"]
                    args_raw = func.get("arguments", "{}")
                    arguments = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    tool_calls.append(
                        ToolCall(id=tc["id"], name=func["name"], arguments=arguments)
                    )

            usage_raw = data.get("usage", {})
            usage = TokenUsage(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),
                completion_tokens=usage_raw.get("completion_tokens", 0),
                total_tokens=usage_raw.get("total_tokens", 0),
            )
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise ProviderAPIError(
                status_code=status,
                message=f"Malformed response body: {response.text!r}",
            ) from exc

        return ChatCompletion(
            content=message.get("content"),
            tool_calls=tool_calls,
            usage=usage,
            model=data.get("model", requested_model),
            finish_reason=finish_reason,
        )
