# Model Provider Runtime v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LLM provider abstraction and replace the deterministic mission planner with an LLM-driven planner using tool calling for structured output.

**Architecture:** OpenAI-compatible HTTP client (`httpx`) as the single provider implementation, model catalog for model metadata, LLM planner using tool calling to produce structured `MissionPlan` JSON, and JSONL trace recording for debugging/audit. No `openai` SDK dependency.

**Tech Stack:** Python 3.10+, httpx, pytest

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/fireclaw_core/provider.py` | `ModelProvider` protocol, `OpenAICompatProvider`, `ChatCompletion`/`ToolCall`/`TokenUsage` data types, error types |
| `src/fireclaw_core/model_catalog.py` | `ModelDescriptor`, `ModelCatalog` (load from JSON, resolve by id) |
| `src/fireclaw_core/llm_planner.py` | `LLMMissionPlanner` (implements `MissionPlannerProtocol`), system prompt, tool schema, output validation |
| `src/fireclaw_core/llm_trace.py` | `LLMTraceRecord`, `LLMTraceStore` (JSONL append-only) |
| `tests/test_provider.py` | Provider protocol and `OpenAICompatProvider` tests |
| `tests/test_model_catalog.py` | `ModelCatalog` tests |
| `tests/test_llm_planner.py` | `LLMMissionPlanner` tests with mock provider |
| `tests/test_llm_trace.py` | `LLMTraceStore` tests |

---

## Task 1: Provider Protocol and OpenAICompatProvider

**Files:**
- Create: `src/fireclaw_core/provider.py`
- Create: `tests/test_provider.py`

- [ ] **Step 1: Write the failing test for provider data types**

```python
# tests/test_provider.py
from fireclaw_core.provider import (
    ChatCompletion,
    ToolCall,
    TokenUsage,
    ProviderError,
    ProviderTimeoutError,
    ProviderAPIError,
    ProviderAuthError,
)


def test_chat_completion_fields():
    tc = ChatCompletion(
        content="hello",
        tool_calls=None,
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        model="test-model",
        finish_reason="stop",
    )
    assert tc.content == "hello"
    assert tc.tool_calls is None
    assert tc.usage.total_tokens == 15
    assert tc.finish_reason == "stop"


def test_tool_call_fields():
    tc = ToolCall(id="call_1", name="create_plan", arguments={"intent": "search"})
    assert tc.id == "call_1"
    assert tc.name == "create_plan"
    assert tc.arguments["intent"] == "search"


def test_token_usage_fields():
    tu = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    assert tu.prompt_tokens == 100
    assert tu.completion_tokens == 50
    assert tu.total_tokens == 150


def test_provider_error_hierarchy():
    assert issubclass(ProviderTimeoutError, ProviderError)
    assert issubclass(ProviderAPIError, ProviderError)
    assert issubclass(ProviderAuthError, ProviderAPIError)


def test_provider_api_error_fields():
    err = ProviderAPIError(status_code=401, message="Invalid API key")
    assert err.status_code == 401
    assert "Invalid API key" in str(err)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fireclaw_core.provider'`

- [ ] **Step 3: Implement provider data types and errors**

```python
# src/fireclaw_core/provider.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# --- Data types ---

@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatCompletion:
    content: str | None
    tool_calls: list[ToolCall] | None
    usage: TokenUsage
    model: str
    finish_reason: str


# --- Errors ---

class ProviderError(Exception):
    """Base error for all provider operations."""


class ProviderTimeoutError(ProviderError):
    """Request to provider timed out."""


class ProviderAPIError(ProviderError):
    """Provider returned an API error."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"Provider API error {status_code}: {message}")


class ProviderAuthError(ProviderAPIError):
    """Provider authentication failed (401)."""

    def __init__(self, message: str = "Invalid API key") -> None:
        super().__init__(status_code=401, message=message)


# --- Protocol ---

class ModelProvider(Protocol):
    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> ChatCompletion: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_provider.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Write the failing test for OpenAICompatProvider**

Append to `tests/test_provider.py`:

```python
import json
from unittest.mock import MagicMock, patch
from fireclaw_core.provider import OpenAICompatProvider


def _mock_httpx_response(status_code: int, json_body: dict) -> MagicMock:
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.text = json.dumps(json_body)
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_value = Exception(f"HTTP {status_code}")
    return resp


def _sample_openai_response() -> dict:
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello!",
                },
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
    provider = OpenAICompatProvider(base_url="https://api.test.com", api_key="sk-test")
    mock_resp = _mock_httpx_response(200, _sample_openai_response())

    with patch("fireclaw_core.provider.httpx.post", return_value=mock_resp):
        result = provider.chat_completion(
            messages=[{"role": "user", "content": "hello"}],
            model="deepseek-chat",
        )

    assert result.content == "Hello!"
    assert result.tool_calls is None
    assert result.model == "deepseek-chat"
    assert result.finish_reason == "stop"
    assert result.usage.total_tokens == 15


def test_openai_compat_provider_sends_tools():
    provider = OpenAICompatProvider(base_url="https://api.test.com", api_key="sk-test")

    tool_response = _sample_openai_response()
    tool_response["choices"][0]["message"] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "create_plan",
                    "arguments": json.dumps({"intent": "search"}),
                },
            }
        ],
    }
    tool_response["choices"][0]["finish_reason"] = "tool_calls"

    mock_resp = _mock_httpx_response(200, tool_response)

    with patch("fireclaw_core.provider.httpx.post", return_value=mock_resp) as mock_post:
        result = provider.chat_completion(
            messages=[{"role": "user", "content": "plan mission"}],
            model="deepseek-chat",
            tools=[{"type": "function", "function": {"name": "create_plan"}}],
        )

    assert result.tool_calls is not None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "create_plan"
    assert result.tool_calls[0].arguments == {"intent": "search"}
    assert result.finish_reason == "tool_calls"

    # Verify tools were sent in request
    call_kwargs = mock_post.call_args
    body = json.loads(call_kwargs.kwargs.get("content", call_kwargs[1].get("content", "{}")))
    assert "tools" in body


def test_openai_compat_provider_raises_auth_error():
    provider = OpenAICompatProvider(base_url="https://api.test.com", api_key="sk-bad")
    mock_resp = _mock_httpx_response(401, {"error": {"message": "Invalid API key"}})
    mock_resp.raise_for_status.side_effect = Exception("401 Unauthorized")

    with patch("fireclaw_core.provider.httpx.post", return_value=mock_resp):
        import pytest
        with pytest.raises(ProviderAuthError):
            provider.chat_completion(
                messages=[{"role": "user", "content": "hello"}],
                model="deepseek-chat",
            )


def test_openai_compat_provider_raises_api_error():
    provider = OpenAICompatProvider(base_url="https://api.test.com", api_key="sk-test")
    mock_resp = _mock_httpx_response(500, {"error": {"message": "Internal server error"}})
    mock_resp.raise_for_status.side_effect = Exception("500 Internal Server Error")

    with patch("fireclaw_core.provider.httpx.post", return_value=mock_resp):
        import pytest
        with pytest.raises(ProviderAPIError):
            provider.chat_completion(
                messages=[{"role": "user", "content": "hello"}],
                model="deepseek-chat",
            )
```

- [ ] **Step 6: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_provider.py::test_openai_compat_provider_returns_chat_completion -v`
Expected: FAIL with `cannot import name 'OpenAICompatProvider'`

- [ ] **Step 7: Implement OpenAICompatProvider**

Append to `src/fireclaw_core/provider.py`:

```python
import json as json_mod
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 60.0


class OpenAICompatProvider:
    """OpenAI-compatible chat completion provider using httpx."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.default_headers = default_headers or {}

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> ChatCompletion:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.default_headers,
        }
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools

        try:
            resp = httpx.post(
                url,
                headers=headers,
                content=json_mod.dumps(body, ensure_ascii=False),
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"Request timed out after {self.timeout}s") from exc

        if resp.status_code == 401:
            error_msg = _extract_error_message(resp)
            raise ProviderAuthError(error_msg)

        if resp.status_code >= 400:
            error_msg = _extract_error_message(resp)
            raise ProviderAPIError(status_code=resp.status_code, message=error_msg)

        data = resp.json()
        return _parse_chat_completion(data, model)


def _extract_error_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            if isinstance(err, dict) and "message" in err:
                return str(err["message"])
        return resp.text[:500]
    except Exception:
        return resp.text[:500]


def _parse_chat_completion(data: dict[str, Any], model: str) -> ChatCompletion:
    choices = data.get("choices", [])
    if not choices:
        raise ProviderAPIError(status_code=200, message="No choices in response")

    choice = choices[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "unknown")

    content = message.get("content")
    raw_tool_calls = message.get("tool_calls")
    tool_calls = None
    if raw_tool_calls:
        tool_calls = []
        for tc in raw_tool_calls:
            func = tc.get("function", {})
            args_str = func.get("arguments", "{}")
            try:
                args = json_mod.loads(args_str)
            except json_mod.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=func.get("name", ""),
                arguments=args,
            ))

    usage_data = data.get("usage", {})
    usage = TokenUsage(
        prompt_tokens=usage_data.get("prompt_tokens", 0),
        completion_tokens=usage_data.get("completion_tokens", 0),
        total_tokens=usage_data.get("total_tokens", 0),
    )

    return ChatCompletion(
        content=content,
        tool_calls=tool_calls,
        usage=usage,
        model=data.get("model", model),
        finish_reason=finish_reason,
    )
```

- [ ] **Step 8: Run all provider tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_provider.py -v`
Expected: PASS (9 tests)

- [ ] **Step 9: Commit**

```bash
git add src/fireclaw_core/provider.py tests/test_provider.py
git commit -m "feat: add ModelProvider protocol and OpenAICompatProvider v1"
```

---

## Task 2: ModelCatalog

**Files:**
- Create: `src/fireclaw_core/model_catalog.py`
- Create: `tests/test_model_catalog.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_model_catalog.py
import json
import pytest
from pathlib import Path

from fireclaw_core.model_catalog import ModelCatalog, ModelDescriptor


def test_model_descriptor_fields():
    md = ModelDescriptor(
        id="deepseek-chat",
        name="DeepSeek V3",
        provider="deepseek",
        context_window=128000,
        max_tokens=4096,
        supports_tools=True,
    )
    assert md.id == "deepseek-chat"
    assert md.name == "DeepSeek V3"
    assert md.supports_tools is True
    assert md.cost_input is None


def test_model_catalog_loads_from_json(tmp_path):
    config = {
        "models": [
            {
                "id": "deepseek-chat",
                "name": "DeepSeek V3",
                "provider": "deepseek",
                "context_window": 128000,
                "max_tokens": 4096,
                "supports_tools": True,
            },
            {
                "id": "qwen-plus",
                "name": "Qwen Plus",
                "provider": "qwen",
                "context_window": 131072,
                "max_tokens": 8192,
                "supports_tools": True,
            },
        ],
        "default_model": "deepseek-chat",
    }
    config_path = tmp_path / "models.json"
    config_path.write_text(json.dumps(config))

    catalog = ModelCatalog(config_path)
    assert len(catalog.list_models()) == 2


def test_model_catalog_resolve_by_id(tmp_path):
    config = {
        "models": [
            {
                "id": "deepseek-chat",
                "name": "DeepSeek V3",
                "provider": "deepseek",
                "context_window": 128000,
                "max_tokens": 4096,
                "supports_tools": True,
            },
        ],
    }
    config_path = tmp_path / "models.json"
    config_path.write_text(json.dumps(config))

    catalog = ModelCatalog(config_path)
    model = catalog.resolve("deepseek-chat")
    assert model.id == "deepseek-chat"
    assert model.context_window == 128000


def test_model_catalog_resolve_raises_on_missing(tmp_path):
    config = {"models": []}
    config_path = tmp_path / "models.json"
    config_path.write_text(json.dumps(config))

    catalog = ModelCatalog(config_path)
    with pytest.raises(ModelNotFoundError):
        catalog.resolve("nonexistent")


def test_model_catalog_default_model(tmp_path):
    config = {
        "models": [
            {"id": "m1", "name": "M1", "provider": "p", "context_window": 1000, "max_tokens": 100, "supports_tools": False},
            {"id": "m2", "name": "M2", "provider": "p", "context_window": 2000, "max_tokens": 200, "supports_tools": True},
        ],
        "default_model": "m2",
    }
    config_path = tmp_path / "models.json"
    config_path.write_text(json.dumps(config))

    catalog = ModelCatalog(config_path)
    assert catalog.default_model_id == "m2"


def test_model_catalog_optional_cost(tmp_path):
    config = {
        "models": [
            {
                "id": "m1",
                "name": "M1",
                "provider": "p",
                "context_window": 1000,
                "max_tokens": 100,
                "supports_tools": False,
                "cost_input": 0.5,
                "cost_output": 1.5,
            },
        ],
    }
    config_path = tmp_path / "models.json"
    config_path.write_text(json.dumps(config))

    catalog = ModelCatalog(config_path)
    model = catalog.resolve("m1")
    assert model.cost_input == 0.5
    assert model.cost_output == 1.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_model_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fireclaw_core.model_catalog'`

- [ ] **Step 3: Implement ModelCatalog**

```python
# src/fireclaw_core/model_catalog.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ModelNotFoundError(Exception):
    """Raised when a model id is not found in the catalog."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        super().__init__(f"Model not found in catalog: {model_id}")


@dataclass(frozen=True)
class ModelDescriptor:
    id: str
    name: str
    provider: str
    context_window: int
    max_tokens: int
    supports_tools: bool
    cost_input: float | None = None
    cost_output: float | None = None


class ModelCatalog:
    def __init__(self, config_path: str | Path | None = None) -> None:
        self._models: dict[str, ModelDescriptor] = {}
        self.default_model_id: str | None = None

        if config_path is not None:
            self._load(Path(config_path))

    def _load(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.get("models", []):
            desc = ModelDescriptor(
                id=entry["id"],
                name=entry["name"],
                provider=entry["provider"],
                context_window=entry["context_window"],
                max_tokens=entry["max_tokens"],
                supports_tools=entry["supports_tools"],
                cost_input=entry.get("cost_input"),
                cost_output=entry.get("cost_output"),
            )
            self._models[desc.id] = desc
        self.default_model_id = data.get("default_model")

    def resolve(self, model_id: str) -> ModelDescriptor:
        if model_id not in self._models:
            raise ModelNotFoundError(model_id)
        return self._models[model_id]

    def list_models(self) -> list[ModelDescriptor]:
        return list(self._models.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_model_catalog.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/model_catalog.py tests/test_model_catalog.py
git commit -m "feat: add ModelCatalog v1 for model metadata management"
```

---

## Task 3: LLM Trace Store

**Files:**
- Create: `src/fireclaw_core/llm_trace.py`
- Create: `tests/test_llm_trace.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_trace.py
import json
from pathlib import Path

from fireclaw_core.llm_trace import LLMTraceRecord, LLMTraceStore
from fireclaw_core.provider import TokenUsage


def test_trace_record_fields():
    trace = LLMTraceRecord(
        trace_id="t1",
        timestamp="2026-06-08T12:00:00Z",
        provider="https://api.deepseek.com",
        model="deepseek-chat",
        messages=[{"role": "user", "content": "hello"}],
        response={"choices": []},
        tool_calls=None,
        latency_ms=500.0,
        token_usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        status="success",
        error=None,
    )
    assert trace.trace_id == "t1"
    assert trace.status == "success"
    assert trace.token_usage.total_tokens == 15


def test_trace_store_append_and_list(tmp_path):
    store = LLMTraceStore(tmp_path / "traces.jsonl")
    trace = LLMTraceRecord(
        trace_id="t1",
        timestamp="2026-06-08T12:00:00Z",
        provider="https://api.deepseek.com",
        model="deepseek-chat",
        messages=[{"role": "user", "content": "hello"}],
        response={"choices": []},
        tool_calls=None,
        latency_ms=500.0,
        token_usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        status="success",
        error=None,
    )
    store.record(trace)
    traces = store.list_traces()
    assert len(traces) == 1
    assert traces[0].trace_id == "t1"


def test_trace_store_get_by_id(tmp_path):
    store = LLMTraceStore(tmp_path / "traces.jsonl")
    trace = LLMTraceRecord(
        trace_id="t1",
        timestamp="2026-06-08T12:00:00Z",
        provider="https://api.deepseek.com",
        model="deepseek-chat",
        messages=[],
        response=None,
        tool_calls=None,
        latency_ms=100.0,
        token_usage=None,
        status="error",
        error="timeout",
    )
    store.record(trace)
    result = store.get_trace("t1")
    assert result is not None
    assert result.status == "error"
    assert result.error == "timeout"


def test_trace_store_get_missing_returns_none(tmp_path):
    store = LLMTraceStore(tmp_path / "traces.jsonl")
    assert store.get_trace("nonexistent") is None


def test_trace_store_skips_corrupt_lines(tmp_path):
    path = tmp_path / "traces.jsonl"
    path.write_text("not valid json\n", encoding="utf-8")

    store = LLMTraceStore(path)
    trace = LLMTraceRecord(
        trace_id="t1",
        timestamp="2026-06-08T12:00:00Z",
        provider="https://api.deepseek.com",
        model="deepseek-chat",
        messages=[],
        response=None,
        tool_calls=None,
        latency_ms=100.0,
        token_usage=None,
        status="success",
        error=None,
    )
    store.record(trace)
    traces = store.list_traces()
    assert len(traces) == 1
    assert traces[0].trace_id == "t1"


def test_trace_store_list_limit(tmp_path):
    store = LLMTraceStore(tmp_path / "traces.jsonl")
    for i in range(5):
        store.record(LLMTraceRecord(
            trace_id=f"t{i}",
            timestamp=f"2026-06-08T12:00:0{i}Z",
            provider="https://api.test.com",
            model="test",
            messages=[],
            response=None,
            tool_calls=None,
            latency_ms=100.0,
            token_usage=None,
            status="success",
            error=None,
        ))
    traces = store.list_traces(limit=3)
    assert len(traces) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_llm_trace.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fireclaw_core.llm_trace'`

- [ ] **Step 3: Implement LLMTraceStore**

```python
# src/fireclaw_core/llm_trace.py
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fireclaw_core.provider import TokenUsage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMTraceRecord:
    trace_id: str
    timestamp: str
    provider: str
    model: str
    messages: list[dict[str, Any]]
    response: dict[str, Any] | None
    tool_calls: list[dict[str, Any]] | None
    latency_ms: float
    token_usage: TokenUsage | None
    status: str  # "success" | "error"
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


class LLMTraceStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record(self, trace: LLMTraceRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def list_traces(self, limit: int = 100) -> list[LLMTraceRecord]:
        records = self._read_all()
        return records[-limit:]

    def get_trace(self, trace_id: str) -> LLMTraceRecord | None:
        for record in self._read_all():
            if record.trace_id == trace_id:
                return record
        return None

    def _read_all(self) -> list[LLMTraceRecord]:
        if not self.path.exists():
            return []
        records: list[LLMTraceRecord] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    records.append(_dict_to_trace(data))
                except (json.JSONDecodeError, KeyError, TypeError):
                    logger.warning("Skipping corrupt LLM trace line: %s", line[:100])
        return records


def _dict_to_trace(data: dict[str, Any]) -> LLMTraceRecord:
    usage_data = data.get("token_usage")
    token_usage = None
    if usage_data is not None:
        token_usage = TokenUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )
    return LLMTraceRecord(
        trace_id=data["trace_id"],
        timestamp=data["timestamp"],
        provider=data["provider"],
        model=data["model"],
        messages=data.get("messages", []),
        response=data.get("response"),
        tool_calls=data.get("tool_calls"),
        latency_ms=data.get("latency_ms", 0.0),
        token_usage=token_usage,
        status=data.get("status", "unknown"),
        error=data.get("error"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_llm_trace.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/llm_trace.py tests/test_llm_trace.py
git commit -m "feat: add LLMTraceStore v1 for provider call recording"
```

---

## Task 4: LLM Mission Planner

**Files:**
- Create: `src/fireclaw_core/llm_planner.py`
- Create: `tests/test_llm_planner.py`

- [ ] **Step 1: Write the failing test for tool schema and system prompt**

```python
# tests/test_llm_planner.py
import json
from unittest.mock import MagicMock

from fireclaw_core.llm_planner import LLMMissionPlanner, MISSION_PLAN_TOOL, build_system_prompt
from fireclaw_core.mission_planner import MissionPlannerContext
from fireclaw_core.provider import ChatCompletion, ToolCall, TokenUsage
from fireclaw_core.robot_registry import RobotRegistryEntry


def _robots(entries):
    return MissionPlannerContext(available_robots=list(entries))


def _make_completion(tool_calls: list[dict] | None = None, content: str | None = None) -> ChatCompletion:
    tc_list = None
    if tool_calls:
        tc_list = [ToolCall(id=f"call_{i}", name="create_mission_plan", arguments=args) for i, args in enumerate(tool_calls)]
    return ChatCompletion(
        content=content,
        tool_calls=tc_list,
        usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        model="deepseek-chat",
        finish_reason="tool_calls" if tool_calls else "stop",
    )


def test_mission_plan_tool_schema_has_required_fields():
    schema = MISSION_PLAN_TOOL
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "create_mission_plan"
    params = schema["function"]["parameters"]
    assert "intent" in params["properties"]
    assert "subtasks" in params["properties"]
    assert "intent" in params["required"]
    assert "subtasks" in params["required"]


def test_build_system_prompt_includes_robots():
    robots = _robots([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("patrol",)),
    ])
    prompt = build_system_prompt(robots)
    assert "r1" in prompt
    assert "r2" in prompt
    assert "search_for_victims" in prompt
    assert "patrol" in prompt


def test_llm_planner_returns_plan_from_tool_call():
    mock_provider = MagicMock()
    mock_provider.chat_completion.return_value = _make_completion(tool_calls=[{
        "intent": "search",
        "subtasks": [
            {"robot_id": "r1", "command": "去二楼搜索受困人员", "floor": 2, "capability_required": "search_for_victims", "execution_group": 0},
        ],
    }])

    planner = LLMMissionPlanner(provider=mock_provider, model_id="deepseek-chat")
    ctx = _robots([RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",))])
    result = planner.plan("去二楼搜索受困人员", context=ctx)

    assert result.status == "planned"
    assert result.plan is not None
    assert result.plan.intent == "search"
    assert len(result.plan.subtasks) == 1
    assert result.plan.subtasks[0].robot_id == "r1"


def test_llm_planner_returns_error_when_no_tool_call():
    mock_provider = MagicMock()
    mock_provider.chat_completion.return_value = _make_completion(content="I don't understand")

    planner = LLMMissionPlanner(provider=mock_provider, model_id="deepseek-chat")
    result = planner.plan("做点什么")

    assert result.status == "error"
    assert "工具调用" in result.message


def test_llm_planner_returns_error_on_provider_timeout():
    from fireclaw_core.provider import ProviderTimeoutError
    mock_provider = MagicMock()
    mock_provider.chat_completion.side_effect = ProviderTimeoutError("timed out")

    planner = LLMMissionPlanner(provider=mock_provider, model_id="deepseek-chat")
    result = planner.plan("去二楼搜索")

    assert result.status == "error"
    assert "超时" in result.message


def test_llm_planner_returns_error_on_provider_api_error():
    from fireclaw_core.provider import ProviderAPIError
    mock_provider = MagicMock()
    mock_provider.chat_completion.side_effect = ProviderAPIError(status_code=500, message="server error")

    planner = LLMMissionPlanner(provider=mock_provider, model_id="deepseek-chat")
    result = planner.plan("去二楼搜索")

    assert result.status == "error"
    assert "API 错误" in result.message


def test_llm_planner_validates_robot_id():
    mock_provider = MagicMock()
    mock_provider.chat_completion.return_value = _make_completion(tool_calls=[{
        "intent": "search",
        "subtasks": [
            {"robot_id": "nonexistent", "command": "去二楼搜索", "floor": 2, "capability_required": "search_for_victims", "execution_group": 0},
        ],
    }])

    planner = LLMMissionPlanner(provider=mock_provider, model_id="deepseek-chat")
    ctx = _robots([RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",))])
    result = planner.plan("去二楼搜索", context=ctx)

    assert result.status == "error"
    assert "robot_id" in result.message or "不存在" in result.message


def test_llm_planner_records_trace(tmp_path):
    from fireclaw_core.llm_trace import LLMTraceStore

    mock_provider = MagicMock()
    mock_provider.chat_completion.return_value = _make_completion(tool_calls=[{
        "intent": "search",
        "subtasks": [
            {"robot_id": "r1", "command": "去二楼搜索", "floor": 2, "capability_required": "search_for_victims", "execution_group": 0},
        ],
    }])

    trace_store = LLMTraceStore(tmp_path / "traces.jsonl")
    planner = LLMMissionPlanner(provider=mock_provider, model_id="deepseek-chat", trace_store=trace_store)
    ctx = _robots([RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",))])
    planner.plan("去二楼搜索", context=ctx)

    traces = trace_store.list_traces()
    assert len(traces) == 1
    assert traces[0].model == "deepseek-chat"
    assert traces[0].status == "success"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_llm_planner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fireclaw_core.llm_planner'`

- [ ] **Step 3: Implement LLMMissionPlanner**

```python
# src/fireclaw_core/llm_planner.py
from __future__ import annotations

import json
import logging
import time
from typing import Any
from uuid import uuid4

from fireclaw_core.llm_trace import LLMTraceRecord, LLMTraceStore
from fireclaw_core.mission_planner import (
    MissionPlan,
    MissionPlannerContext,
    MissionPlanningResult,
    MissionSubtask,
)
from fireclaw_core.provider import (
    ChatCompletion,
    ModelProvider,
    ProviderAPIError,
    ProviderError,
    ProviderTimeoutError,
)
from fireclaw_core.robot_registry import RobotRegistryEntry

logger = logging.getLogger(__name__)

VALID_INTENTS = {"search", "patrol", "firefight", "recon", "transport"}

MISSION_PLAN_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "create_mission_plan",
        "description": "创建消防机器人任务计划",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": list(VALID_INTENTS),
                    "description": "任务意图",
                },
                "subtasks": {
                    "type": "array",
                    "description": "子任务列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string", "description": "机器人 ID"},
                            "command": {"type": "string", "description": "子任务指令"},
                            "floor": {"type": "integer", "description": "目标楼层"},
                            "capability_required": {"type": "string", "description": "所需能力"},
                            "execution_group": {
                                "type": "integer",
                                "description": "执行组编号，同组可并行，不同组按顺序执行",
                                "default": 0,
                            },
                        },
                        "required": ["robot_id", "command", "floor", "capability_required"],
                    },
                },
            },
            "required": ["intent", "subtasks"],
        },
    },
}


def build_system_prompt(context: MissionPlannerContext) -> str:
    robot_lines: list[str] = []
    for r in context.available_robots:
        caps = ", ".join(r.capabilities) if r.capabilities else "无"
        status = "启用" if r.enabled else "禁用"
        robot_lines.append(f"- {r.robot_id}: 能力=[{caps}], 区域={r.zone or '未知'}, 状态={status}")

    robots_section = "\n".join(robot_lines) if robot_lines else "（无可用机器人）"

    return (
        "你是消防机器人的任务规划器。根据操作员的自然语言指令，将任务分解为可执行的子任务，并分配给可用的机器人。\n\n"
        "## 可用机器人\n"
        f"{robots_section}\n\n"
        "## 输出要求\n"
        "请调用 create_mission_plan 工具，输出结构化的任务计划。\n"
        "- intent: 任务意图（search/patrol/firefight/recon/transport）\n"
        "- subtasks: 子任务列表，每个子任务包含 robot_id, command, floor, capability_required, execution_group\n"
        "- execution_group: 执行组编号，同组可并行，不同组按顺序执行\n"
        "- robot_id 必须是上面列出的可用机器人之一\n"
        "- capability_required 必须是该机器人具备的能力之一\n"
    )


class LLMMissionPlanner:
    """LLM-driven mission planner using tool calling for structured output."""

    def __init__(
        self,
        provider: ModelProvider,
        model_id: str,
        trace_store: LLMTraceStore | None = None,
    ) -> None:
        self.provider = provider
        self.model_id = model_id
        self.trace_store = trace_store

    def plan(self, command: str, context: MissionPlannerContext | None = None) -> MissionPlanningResult:
        effective_context = context or MissionPlannerContext()
        system_prompt = build_system_prompt(effective_context)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": command},
        ]

        start_time = time.monotonic()
        trace_id = uuid4().hex[:12]
        try:
            completion = self.provider.chat_completion(
                messages=messages,
                model=self.model_id,
                tools=[MISSION_PLAN_TOOL],
                temperature=0.0,
                max_tokens=4096,
            )
        except ProviderTimeoutError as exc:
            self._record_trace(trace_id, messages, None, start_time, "error", str(exc))
            return MissionPlanningResult(
                status="error",
                message=f"LLM 调用超时: {exc}",
            )
        except ProviderAPIError as exc:
            self._record_trace(trace_id, messages, None, start_time, "error", str(exc))
            return MissionPlanningResult(
                status="error",
                message=f"LLM API 错误 ({exc.status_code}): {exc.message}",
            )
        except ProviderError as exc:
            self._record_trace(trace_id, messages, None, start_time, "error", str(exc))
            return MissionPlanningResult(
                status="error",
                message=f"LLM 调用失败: {exc}",
            )

        self._record_trace(trace_id, messages, completion, start_time, "success", None)
        return self._parse_completion(completion, effective_context)

    def _parse_completion(
        self,
        completion: ChatCompletion,
        context: MissionPlannerContext,
    ) -> MissionPlanningResult:
        if not completion.tool_calls:
            return MissionPlanningResult(
                status="error",
                message="LLM 未返回工具调用，无法生成任务计划。",
            )

        tool_call = completion.tool_calls[0]
        args = tool_call.arguments

        # Validate intent
        intent = args.get("intent", "")
        if intent not in VALID_INTENTS:
            return MissionPlanningResult(
                status="error",
                message=f"LLM 返回了无效的意图: {intent}",
            )

        # Validate subtasks
        raw_subtasks = args.get("subtasks", [])
        if not raw_subtasks:
            return MissionPlanningResult(
                status="error",
                message="LLM 返回了空的子任务列表。",
            )

        robot_map = {r.robot_id: r for r in context.available_robots}
        subtasks: list[MissionSubtask] = []
        for i, st in enumerate(raw_subtasks):
            robot_id = st.get("robot_id", "")
            if robot_id not in robot_map:
                available = list(robot_map.keys())
                return MissionPlanningResult(
                    status="error",
                    message=f"子任务 {i} 的 robot_id '{robot_id}' 不存在。可用机器人: {available}",
                )
            subtasks.append(MissionSubtask(
                robot_id=robot_id,
                command=st.get("command", ""),
                floor=st.get("floor", 0),
                capability_required=st.get("capability_required", ""),
                execution_group=st.get("execution_group", 0),
            ))

        plan = MissionPlan(intent=intent, command="", subtasks=subtasks)
        return MissionPlanningResult(
            status="planned",
            message=f"LLM 已生成任务计划：{len(subtasks)} 个子任务，{plan.execution_groups} 个执行组。",
            intent=intent,
            plan=plan,
        )

    def _record_trace(
        self,
        trace_id: str,
        messages: list[dict[str, Any]],
        completion: ChatCompletion | None,
        start_time: float,
        status: str,
        error: str | None,
    ) -> None:
        if self.trace_store is None:
            return
        latency_ms = (time.monotonic() - start_time) * 1000
        trace = LLMTraceRecord(
            trace_id=trace_id,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            provider=str(self.provider),
            model=self.model_id,
            messages=messages,
            response=_completion_to_dict(completion) if completion else None,
            tool_calls=[tc.arguments for tc in completion.tool_calls] if completion and completion.tool_calls else None,
            latency_ms=latency_ms,
            token_usage=completion.usage if completion else None,
            status=status,
            error=error,
        )
        try:
            self.trace_store.record(trace)
        except Exception:
            logger.warning("Failed to record LLM trace", exc_info=True)


def _completion_to_dict(completion: ChatCompletion) -> dict[str, Any]:
    return {
        "content": completion.content,
        "finish_reason": completion.finish_reason,
        "model": completion.model,
    }
```

- [ ] **Step 4: Run all LLM planner tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_llm_planner.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/llm_planner.py tests/test_llm_planner.py
git commit -m "feat: add LLMMissionPlanner v1 with tool calling and trace recording"
```

---

## Task 5: CLI Integration

**Files:**
- Modify: `src/fireclaw_core/mission_cli.py`
- Modify: `tests/test_mission_cli.py`

- [ ] **Step 1: Write the failing test for LLM planner CLI flags**

Append to `tests/test_mission_cli.py`:

```python
def test_mission_cli_plan_mission_with_llm_flag(tmp_path, monkeypatch):
    """plan-mission --planner llm should use LLMMissionPlanner."""
    from unittest.mock import MagicMock, patch

    registry_path = tmp_path / "robots.json"
    registry_path.write_text(json.dumps([
        {"robot_id": "r1", "base_url": "http://r1:8765", "capabilities": ["search_for_victims"]},
    ]))
    registry_out = tmp_path / "registry.jsonl"
    memory_path = tmp_path / "memory.jsonl"
    trace_path = tmp_path / "traces.jsonl"

    # Mock LLMMissionPlanner to avoid real LLM calls
    mock_plan = MagicMock()
    mock_plan.status = "planned"
    mock_plan.message = "LLM 已生成任务计划：1 个子任务。"
    mock_plan.intent = "search"
    mock_plan.plan = MagicMock()
    mock_plan.plan.to_dict.return_value = {
        "intent": "search",
        "command": "去二楼搜索受困人员",
        "execution_groups": 1,
        "subtasks": [{"robot_id": "r1", "command": "去二楼搜索受困人员", "floor": 2, "capability_required": "search_for_victims", "execution_group": 0}],
    }

    with patch("fireclaw_core.mission_cli.LLMMissionPlanner") as MockPlanner:
        instance = MockPlanner.return_value
        instance.plan.return_value = mock_plan

        result = run_cli([
            "plan-mission",
            "--command", "去二楼搜索受困人员",
            "--registry-path", str(registry_path),
            "--registry-out", str(registry_out),
            "--memory-path", str(memory_path),
            "--planner", "llm",
            "--provider-base-url", "https://api.deepseek.com",
            "--provider-api-key", "sk-test",
            "--model", "deepseek-chat",
            "--llm-trace-path", str(trace_path),
        ])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "planned"
```

Note: you need to add `import json` to the test file header if not already present, and ensure `run_cli` helper exists.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mission_cli.py::test_mission_cli_plan_mission_with_llm_flag -v`
Expected: FAIL with missing `--planner` flag or import error

- [ ] **Step 3: Add LLM planner flags to CLI**

In `src/fireclaw_core/mission_cli.py`, modify the `plan-mission` subparser and the `plan-mission` handler. Add these imports at the top:

```python
from fireclaw_core.llm_planner import LLMMissionPlanner
from fireclaw_core.llm_trace import LLMTraceStore
from fireclaw_core.model_catalog import ModelCatalog
from fireclaw_core.provider import OpenAICompatProvider
```

Add arguments to the `plan` subparser (after the existing `_add_shared_paths(plan)` line):

```python
    plan.add_argument("--planner", choices=["deterministic", "llm"], default="deterministic",
                       help="Planner backend: 'deterministic' (regex rules) or 'llm' (LLM with tool calling).")
    plan.add_argument("--provider-base-url", default=None, help="LLM provider base URL (required when --planner=llm).")
    plan.add_argument("--provider-api-key", default=None, help="LLM provider API key (required when --planner=llm).")
    plan.add_argument("--model", default=None, help="LLM model id (required when --planner=llm).")
    plan.add_argument("--catalog", default=None, help="Path to model catalog JSON file.")
    plan.add_argument("--llm-trace-path", default=None, help="Path to LLM trace JSONL file.")
```

Add a helper to build the planner:

```python
def _build_planner(args: argparse.Namespace) -> Any:
    """Build the appropriate planner based on CLI flags."""
    if args.planner == "llm":
        if not args.provider_base_url or not args.provider_api_key or not args.model:
            raise SystemExit("--provider-base-url, --provider-api-key, and --model are required when --planner=llm")
        provider = OpenAICompatProvider(base_url=args.provider_base_url, api_key=args.provider_api_key)
        trace_store = LLMTraceStore(args.llm_trace_path) if args.llm_trace_path else None
        return LLMMissionPlanner(provider=provider, model_id=args.model, trace_store=trace_store)
    return MissionPlanner()
```

Modify the `plan-mission` handler to use `_build_planner(args)` instead of hardcoded `MissionPlanner()`.

- [ ] **Step 4: Run all CLI tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mission_cli.py -v`
Expected: PASS (all existing + new tests)

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/mission_cli.py tests/test_mission_cli.py
git commit -m "feat: add LLM planner CLI flags to plan-mission command"
```

---

## Task 6: Full Suite Verification and Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all tests including new provider, catalog, planner, trace tests)

- [ ] **Step 2: Update README.md with provider runtime documentation**

Add a new section after the existing "Mission Planner" section:

```markdown
### Model Provider Runtime v1

FireClaw supports LLM-driven mission planning through an OpenAI-compatible provider abstraction.

**Supported providers:** Any OpenAI-compatible API (DeepSeek, Qwen, GLM, Moonshot, Ollama, etc.)

**CLI usage:**

```bash
# LLM-driven planning
python -m fireclaw_core.mission_cli plan-mission \
  --command "去二楼和三楼搜索受困人员" \
  --planner llm \
  --provider-base-url https://api.deepseek.com \
  --provider-api-key sk-xxx \
  --model deepseek-chat \
  --registry-path robots.json \
  --registry-out missions.jsonl \
  --llm-trace-path logs/llm-traces.jsonl

# Deterministic planning (default, backward compatible)
python -m fireclaw_core.mission_cli plan-mission \
  --command "去二楼搜索受困人员" \
  --registry-path robots.json \
  --registry-out missions.jsonl
```

**Components:**
- `provider.py` — `ModelProvider` protocol and `OpenAICompatProvider` (httpx-based)
- `model_catalog.py` — `ModelCatalog` for model metadata (context window, capabilities, cost)
- `llm_planner.py` — `LLMMissionPlanner` with tool calling for structured output
- `llm_trace.py` — `LLMTraceStore` for recording full LLM call traces (prompt, response, tokens, latency)

**LLM Trace:** Record every LLM call for debugging and audit. Use `--llm-trace-path` to enable.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add provider runtime v1 documentation to README"
```
