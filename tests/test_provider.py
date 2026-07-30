"""Tests for fireclaw_core.provider — data types, errors, protocol, OpenAICompatProvider."""

from __future__ import annotations

import json
from typing import Any
import httpx
import pytest

from fireclaw_core.provider.provider import (
    ChatCompletion,
    ModelProvider,
    OpenAICompatProvider,
    ProviderAPIError,
    ProviderAuthError,
    ProviderError,
    ProviderTimeoutError,
    TokenUsage,
    ToolCall,
)


# --- Data type tests ---


def test_chat_completion_fields():
    usage = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    cc = ChatCompletion(
        content="hello",
        tool_calls=None,
        usage=usage,
        model="gpt-4",
        finish_reason="stop",
    )
    assert cc.content == "hello"
    assert cc.tool_calls is None
    assert cc.usage is usage
    assert cc.model == "gpt-4"
    assert cc.finish_reason == "stop"


def test_tool_call_fields():
    tc = ToolCall(id="call_1", name="search", arguments={"query": "fire"})
    assert tc.id == "call_1"
    assert tc.name == "search"
    assert tc.arguments == {"query": "fire"}


def test_token_usage_fields():
    tu = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    assert tu.prompt_tokens == 100
    assert tu.completion_tokens == 50
    assert tu.total_tokens == 150


# --- Error hierarchy tests ---


def test_provider_error_hierarchy():
    assert issubclass(ProviderTimeoutError, ProviderError)
    assert issubclass(ProviderAPIError, ProviderError)
    assert issubclass(ProviderAuthError, ProviderAPIError)
    assert issubclass(ProviderError, Exception)


def test_provider_api_error_fields():
    err = ProviderAPIError(status_code=500, message="Internal Server Error")
    assert err.status_code == 500
    assert err.message == "Internal Server Error"
    assert "500" in str(err)

    auth_err = ProviderAuthError()
    assert auth_err.status_code == 401
    assert auth_err.message == "Invalid API key"


# --- OpenAICompatProvider tests ---


def _make_provider(
    *,
    status_code: int = 200,
    json_body: dict[str, Any] | None = None,
    raw_body: bytes | None = None,
    error: Exception | None = None,
    trust_env: bool = False,
    max_response_bytes: int = 4 * 1024 * 1024,
) -> tuple[OpenAICompatProvider, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if error is not None:
            raise error
        content = (
            raw_body
            if raw_body is not None
            else json.dumps(json_body or {}).encode("utf-8")
        )
        return httpx.Response(
            status_code,
            content=content,
            headers={"Content-Length": str(len(content))},
        )

    provider = OpenAICompatProvider(
        base_url="http://localhost:8080",
        api_key="sk-test",
        trust_env=trust_env,
        max_response_bytes=max_response_bytes,
        transport=httpx.MockTransport(_handler),
    )
    return provider, requests


def _sample_openai_response(
    content: str = "Hello!",
    tool_calls: list[dict[str, Any]] | None = None,
    model: str = "gpt-4",
) -> dict[str, Any]:
    """Build a minimal OpenAI-compatible response body."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }


def test_openai_compat_provider_returns_chat_completion():
    provider, requests = _make_provider(
        json_body=_sample_openai_response()
    )
    result = provider.chat_completion(
        messages=[{"role": "user", "content": "Hi"}],
        model="gpt-4",
    )

    assert isinstance(result, ChatCompletion)
    assert result.content == "Hello!"
    assert result.tool_calls is None
    assert result.model == "gpt-4"
    assert result.finish_reason == "stop"
    assert result.usage.prompt_tokens == 10
    assert result.usage.completion_tokens == 5
    assert result.usage.total_tokens == 15
    assert len(requests) == 1
    assert provider.trust_env is False


def test_openai_compat_provider_sends_tools():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search for victims",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
    ]

    provider, requests = _make_provider(
        json_body=_sample_openai_response()
    )
    provider.chat_completion(
        messages=[{"role": "user", "content": "search"}],
        model="gpt-4",
        tools=tools,
    )

    body = json.loads(requests[0].content.decode("utf-8"))
    assert body["tools"] == tools


def test_openai_compat_provider_can_opt_into_environment_proxy():
    provider, _ = _make_provider(
        json_body=_sample_openai_response(),
        trust_env=True,
    )
    provider.chat_completion(
        messages=[{"role": "user", "content": "Hi"}],
        model="gpt-4",
    )

    assert provider.trust_env is True


def test_openai_compat_provider_raises_auth_error():
    provider, _ = _make_provider(
        status_code=401,
        json_body={"error": {"message": "Unauthorized"}},
    )
    with pytest.raises(ProviderAuthError) as exc_info:
        provider.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4",
        )
    assert exc_info.value.status_code == 401


def test_openai_compat_provider_raises_api_error():
    provider, _ = _make_provider(
        status_code=500,
        json_body={"error": {"message": "Internal Server Error"}},
    )
    with pytest.raises(ProviderAPIError) as exc_info:
        provider.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4",
        )
    assert exc_info.value.status_code == 500


def test_openai_compat_provider_raises_timeout_error():
    provider, _ = _make_provider(
        error=httpx.ReadTimeout("Connection timed out"),
    )
    with pytest.raises(ProviderTimeoutError, match="Connection timed out"):
        provider.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4",
        )


def test_openai_compat_provider_raises_on_malformed_response():
    """Response with missing 'choices' key should raise ProviderAPIError."""
    provider, _ = _make_provider(json_body={"model": "gpt-4"})
    with pytest.raises(ProviderAPIError, match="Malformed response body"):
        provider.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4",
        )


def test_openai_compat_provider_raises_on_invalid_json_response():
    """Response with non-JSON body should raise ProviderAPIError."""
    provider, _ = _make_provider(
        raw_body=b"<html>Gateway Error</html>"
    )
    with pytest.raises(ProviderAPIError, match="Invalid JSON"):
        provider.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4",
        )


def test_openai_compat_provider_raises_on_connection_error():
    provider, _ = _make_provider(
        error=httpx.ConnectError("Connection refused"),
    )
    with pytest.raises(ProviderError, match="Connection refused"):
        provider.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4",
        )


def test_openai_compat_provider_raises_on_invalid_json_error_body():
    """Error response with non-JSON body should fall back to response.text."""
    provider, _ = _make_provider(
        status_code=502,
        raw_body=b"<html>Bad Gateway</html>",
    )
    with pytest.raises(ProviderAPIError, match="Bad Gateway"):
        provider.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4",
        )


def test_openai_compat_provider_rejects_oversized_streamed_response() -> None:
    provider, _ = _make_provider(
        raw_body=b"x" * 65,
        max_response_bytes=64,
    )

    with pytest.raises(ProviderError, match="max_response_bytes"):
        provider.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4",
        )


def test_openai_compat_provider_rejects_remote_plaintext_url() -> None:
    with pytest.raises(ValueError, match="require HTTPS"):
        OpenAICompatProvider(
            base_url="http://provider.example",
            api_key="sk-test",
        )
