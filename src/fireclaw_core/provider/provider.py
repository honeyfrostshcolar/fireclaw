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
from urllib.parse import urlparse

import httpx

from fireclaw_core.gateway.auth import is_loopback_host


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

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 60.0,
        *,
        trust_env: bool = False,
        max_response_bytes: int = 4 * 1024 * 1024,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Provider base_url must be an absolute HTTP(S) URL.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Provider base_url must not contain userinfo.")
        if parsed.scheme == "http" and not is_loopback_host(parsed.hostname):
            raise ValueError(
                "Remote model providers require HTTPS; plaintext HTTP is "
                "allowed only for loopback development endpoints."
            )
        if max_response_bytes <= 0 or max_response_bytes > 16 * 1024 * 1024:
            raise ValueError(
                "Provider max_response_bytes must be between 1 and 16777216."
            )
        self.api_key = api_key
        self.timeout = timeout
        self.trust_env = trust_env
        self.max_response_bytes = max_response_bytes
        self.transport = transport

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
            with httpx.Client(
                timeout=self.timeout,
                trust_env=self.trust_env,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    json=body,
                    headers=headers,
                ) as streaming_response:
                    declared_length = streaming_response.headers.get(
                        "Content-Length"
                    )
                    if declared_length is not None:
                        try:
                            parsed_length = int(declared_length)
                        except ValueError as exc:
                            raise ProviderError(
                                "Provider response Content-Length is invalid."
                            ) from exc
                        if parsed_length < 0:
                            raise ProviderError(
                                "Provider response Content-Length is invalid."
                            )
                        if parsed_length > self.max_response_bytes:
                            raise ProviderError(
                                "Provider response exceeds max_response_bytes."
                            )
                    chunks: list[bytes] = []
                    observed = 0
                    for chunk in streaming_response.iter_bytes():
                        observed += len(chunk)
                        if observed > self.max_response_bytes:
                            raise ProviderError(
                                "Provider response exceeds max_response_bytes."
                            )
                        chunks.append(chunk)
                    return httpx.Response(
                        status_code=streaming_response.status_code,
                        headers=streaming_response.headers,
                        content=b"".join(chunks),
                        request=streaming_response.request,
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
            raise ProviderAPIError(
                status_code=status,
                message=str(msg)[:1_000],
            )

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderAPIError(
                status_code=status,
                message=(
                    "Invalid JSON in response body: "
                    f"{response.text[:1_000]!r}"
                ),
            ) from exc

        try:
            choice = data["choices"][0]
            message = choice["message"]
            finish_reason = choice.get("finish_reason", "stop")

            # Parse tool calls (function arguments arrive as a JSON string).
            raw_tool_calls = message.get("tool_calls")
            tool_calls: list[ToolCall] | None = None
            if raw_tool_calls:
                if not isinstance(raw_tool_calls, list):
                    raise ValueError("tool_calls must be a list")
                if len(raw_tool_calls) > 32:
                    raise ValueError("tool_calls exceeds the hard limit")
                tool_calls = []
                for tc in raw_tool_calls:
                    func = tc["function"]
                    args_raw = func.get("arguments", "{}")
                    arguments = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    if not isinstance(arguments, dict):
                        raise ValueError(
                            "Tool call arguments must decode to an object"
                        )
                    tool_calls.append(
                        ToolCall(
                            id=str(tc["id"])[:512],
                            name=str(func["name"])[:512],
                            arguments=arguments,
                        )
                    )

            usage_raw = data.get("usage", {})
            usage = TokenUsage(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),
                completion_tokens=usage_raw.get("completion_tokens", 0),
                total_tokens=usage_raw.get("total_tokens", 0),
            )
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise ProviderAPIError(
                status_code=status,
                message=(
                    "Malformed response body: "
                    f"{response.text[:1_000]!r}"
                ),
            ) from exc

        return ChatCompletion(
            content=message.get("content"),
            tool_calls=tool_calls,
            usage=usage,
            model=data.get("model", requested_model),
            finish_reason=finish_reason,
        )
