"""Tests for fireclaw_core.provider — data types, errors, protocol, OpenAICompatProvider."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

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


def _make_mock_response(status_code: int, json_body: dict[str, Any]) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    return resp


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


@patch("fireclaw_core.provider.provider.httpx.post")
def test_openai_compat_provider_returns_chat_completion(mock_post: MagicMock):
    mock_post.return_value = _make_mock_response(200, _sample_openai_response())

    provider = OpenAICompatProvider(base_url="http://localhost:8080", api_key="sk-test")
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
    assert mock_post.call_args.kwargs["trust_env"] is False


@patch("fireclaw_core.provider.provider.httpx.post")
def test_openai_compat_provider_sends_tools(mock_post: MagicMock):
    mock_post.return_value = _make_mock_response(200, _sample_openai_response())

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

    provider = OpenAICompatProvider(base_url="http://localhost:8080", api_key="sk-test")
    provider.chat_completion(
        messages=[{"role": "user", "content": "search"}],
        model="gpt-4",
        tools=tools,
    )

    call_kwargs = mock_post.call_args
    body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json") or call_kwargs[0][1]
    assert body["tools"] == tools
    assert call_kwargs.kwargs["trust_env"] is False


@patch("fireclaw_core.provider.provider.httpx.post")
def test_openai_compat_provider_can_opt_into_environment_proxy(mock_post: MagicMock):
    mock_post.return_value = _make_mock_response(200, _sample_openai_response())

    provider = OpenAICompatProvider(
        base_url="http://localhost:8080",
        api_key="sk-test",
        trust_env=True,
    )
    provider.chat_completion(
        messages=[{"role": "user", "content": "Hi"}],
        model="gpt-4",
    )

    assert mock_post.call_args.kwargs["trust_env"] is True


@patch("fireclaw_core.provider.provider.httpx.post")
def test_openai_compat_provider_raises_auth_error(mock_post: MagicMock):
    mock_post.return_value = _make_mock_response(401, {"error": {"message": "Unauthorized"}})

    provider = OpenAICompatProvider(base_url="http://localhost:8080", api_key="sk-bad")
    with pytest.raises(ProviderAuthError) as exc_info:
        provider.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4",
        )
    assert exc_info.value.status_code == 401


@patch("fireclaw_core.provider.provider.httpx.post")
def test_openai_compat_provider_raises_api_error(mock_post: MagicMock):
    mock_post.return_value = _make_mock_response(500, {"error": {"message": "Internal Server Error"}})

    provider = OpenAICompatProvider(base_url="http://localhost:8080", api_key="sk-test")
    with pytest.raises(ProviderAPIError) as exc_info:
        provider.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4",
        )
    assert exc_info.value.status_code == 500


@patch("fireclaw_core.provider.provider.httpx.post")
def test_openai_compat_provider_raises_timeout_error(mock_post: MagicMock):
    mock_post.side_effect = httpx.TimeoutException("Connection timed out")

    provider = OpenAICompatProvider(base_url="http://localhost:8080", api_key="sk-test")
    with pytest.raises(ProviderTimeoutError, match="Connection timed out"):
        provider.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4",
        )


@patch("fireclaw_core.provider.provider.httpx.post")
def test_openai_compat_provider_raises_on_malformed_response(mock_post: MagicMock):
    """Response with missing 'choices' key should raise ProviderAPIError."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"model": "gpt-4"}  # no "choices"
    resp.text = '{"model": "gpt-4"}'
    mock_post.return_value = resp

    provider = OpenAICompatProvider(base_url="http://localhost:8080", api_key="sk-test")
    with pytest.raises(ProviderAPIError, match="Malformed response body"):
        provider.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4",
        )


@patch("fireclaw_core.provider.provider.httpx.post")
def test_openai_compat_provider_raises_on_invalid_json_response(mock_post: MagicMock):
    """Response with non-JSON body should raise ProviderAPIError."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
    resp.text = "<html>Gateway Error</html>"
    mock_post.return_value = resp

    provider = OpenAICompatProvider(base_url="http://localhost:8080", api_key="sk-test")
    with pytest.raises(ProviderAPIError, match="Invalid JSON"):
        provider.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4",
        )


@patch("fireclaw_core.provider.provider.httpx.post")
def test_openai_compat_provider_raises_on_connection_error(mock_post: MagicMock):
    mock_post.side_effect = httpx.ConnectError("Connection refused")

    provider = OpenAICompatProvider(base_url="http://localhost:8080", api_key="sk-test")
    with pytest.raises(ProviderError, match="Connection refused"):
        provider.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4",
        )


@patch("fireclaw_core.provider.provider.httpx.post")
def test_openai_compat_provider_raises_on_invalid_json_error_body(mock_post: MagicMock):
    """Error response with non-JSON body should fall back to response.text."""
    resp = MagicMock()
    resp.status_code = 502
    resp.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
    resp.text = "<html>Bad Gateway</html>"
    mock_post.return_value = resp

    provider = OpenAICompatProvider(base_url="http://localhost:8080", api_key="sk-test")
    with pytest.raises(ProviderAPIError, match="Bad Gateway"):
        provider.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4",
        )
