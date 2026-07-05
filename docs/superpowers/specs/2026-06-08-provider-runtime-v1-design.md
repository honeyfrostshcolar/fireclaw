# Model Provider Runtime v1 Design

## Goal

Introduce an LLM provider abstraction and replace the deterministic mission planner with an LLM-driven planner that uses tool calling for structured output. This enables FireClaw to leverage large language models for mission planning while keeping the architecture clean and extensible.

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Provider API | OpenAI-compatible | Covers DeepSeek, Qwen, GLM, Moonshot, Ollama, etc. |
| Planner integration | LLM fully replaces deterministic | User choice; LLM handles intent + decomposition + assignment |
| Fallback strategy | No fallback, direct error | System depends on LLM availability |
| LLM traces | Full trace (prompt, response, latency, tokens) | Debugging, audit, replay capability |
| Output format | Tool calling structured output | Reliable JSON extraction from LLM |
| HTTP client | httpx (no openai SDK dependency) | Lightweight, no extra dependencies |

## Architecture Overview

```
MissionAgent
  -> LLMMissionPlanner (implements MissionPlannerProtocol)
    -> ModelProvider (protocol)
      -> OpenAICompatProvider (httpx-based)
    -> ModelCatalog (model metadata)
    -> LLMTraceStore (JSONL trace recording)
    -> Tool calling schema validation
```

The `MissionAgent` is unchanged. It receives a `MissionPlannerProtocol` implementation. `LLMMissionPlanner` implements that protocol and internally uses `ModelProvider` for LLM calls, `ModelCatalog` for model metadata, and `LLMTraceStore` for trace recording.

## Component 1: ModelProvider Protocol

### Interface

```python
class ModelProvider(Protocol):
    def chat_completion(
        self,
        messages: list[dict],
        model: str,
        *,
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> ChatCompletion: ...
```

### ChatCompletion Data Type

```python
@dataclass(frozen=True)
class ChatCompletion:
    content: str | None
    tool_calls: list[ToolCall] | None
    usage: TokenUsage
    model: str
    finish_reason: str

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict

@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
```

### OpenAICompatProvider Implementation

```python
class OpenAICompatProvider:
    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0): ...
```

- Uses `httpx` for HTTP POST to `{base_url}/chat/completions`
- Sends standard OpenAI-format request body
- Parses standard OpenAI-format response
- Raises `ProviderError` on HTTP errors, timeouts, API errors
- No dependency on `openai` SDK

### Error Types

```python
class ProviderError(Exception): ...
class ProviderTimeoutError(ProviderError): ...
class ProviderAPIError(ProviderError):
    def __init__(self, status_code: int, message: str): ...
class ProviderAuthError(ProviderAPIError): ...
```

## Component 2: ModelCatalog

### ModelDescriptor

```python
@dataclass(frozen=True)
class ModelDescriptor:
    id: str                    # e.g. "deepseek-chat"
    name: str                  # e.g. "DeepSeek V3"
    provider: str              # e.g. "deepseek"
    context_window: int        # e.g. 128000
    max_tokens: int            # e.g. 4096
    supports_tools: bool       # whether model supports tool calling
    cost_input: float | None   # cost per 1M input tokens
    cost_output: float | None  # cost per 1M output tokens
```

### ModelCatalog Class

```python
class ModelCatalog:
    def __init__(self, config_path: str | Path | None = None): ...
    def resolve(self, model_id: str) -> ModelDescriptor: ...
    def list_models(self) -> list[ModelDescriptor]: ...
```

### Configuration File Format

```json
{
  "models": [
    {
      "id": "deepseek-chat",
      "name": "DeepSeek V3",
      "provider": "deepseek",
      "context_window": 128000,
      "max_tokens": 4096,
      "supports_tools": true
    },
    {
      "id": "qwen-plus",
      "name": "Qwen Plus",
      "provider": "qwen",
      "context_window": 131072,
      "max_tokens": 8192,
      "supports_tools": true
    }
  ],
  "default_model": "deepseek-chat"
}
```

- `resolve()` raises `ModelNotFoundError` if model_id not found
- `list_models()` returns all registered models
- Optional config path: if None, catalog is empty (provider uses default model)

## Component 3: LLMMissionPlanner

### Interface

Implements existing `MissionPlannerProtocol`:

```python
class LLMMissionPlanner:
    def __init__(
        self,
        provider: ModelProvider,
        model_id: str,
        catalog: ModelCatalog | None = None,
        trace_store: LLMTraceStore | None = None,
    ): ...

    def plan(self, command: str, context: MissionPlannerContext | None = None) -> MissionPlanningResult: ...
```

### System Prompt Structure

```
你是消防机器人的任务规划器。根据操作员的自然语言指令，将任务分解为可执行的子任务，并分配给可用的机器人。

## 可用机器人
{for each robot:}
- {robot_id}: 能力=[{capabilities}], 区域={zone}, 楼层={floor}

## 输出要求
请调用 create_mission_plan 工具，输出结构化的任务计划。
- intent: 任务意图（search/patrol/firefight/recon/transport）
- subtasks: 子任务列表，每个子任务包含 robot_id, command, floor, capability_required, execution_group
- execution_group: 执行组编号，同组可并行，不同组按顺序执行
```

### Tool Calling Schema

```python
MISSION_PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "create_mission_plan",
        "description": "创建消防机器人任务计划",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": ["search", "patrol", "firefight", "recon", "transport"]},
                "subtasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"},
                            "command": {"type": "string"},
                            "floor": {"type": "integer"},
                            "capability_required": {"type": "string"},
                            "execution_group": {"type": "integer", "default": 0}
                        },
                        "required": ["robot_id", "command", "floor", "capability_required"]
                    }
                }
            },
            "required": ["intent", "subtasks"]
        }
    }
}
```

### Validation

- LLM response must contain exactly one `create_mission_plan` tool call
- Returned JSON must pass schema validation
- Each subtask's `robot_id` must exist in `context.available_robots`
- Each subtask's `capability_required` must be in the robot's capabilities
- If validation fails, return `MissionPlanningResult(status="error", message="LLM 输出验证失败: ...")`

### Error Handling

- `ProviderTimeoutError` -> `MissionPlanningResult(status="error", message="LLM 调用超时")`
- `ProviderAPIError` -> `MissionPlanningResult(status="error", message="LLM API 错误: ...")`
- No tool call in response -> `MissionPlanningResult(status="error", message="LLM 未返回工具调用")`
- Schema validation failure -> `MissionPlanningResult(status="error", message="LLM 输出验证失败: ...")`

## Component 4: LLMTraceStore

### LLMTraceRecord

```python
@dataclass
class LLMTraceRecord:
    trace_id: str
    timestamp: str              # ISO 8601
    provider: str               # provider base_url
    model: str                  # model id
    messages: list[dict]        # full prompt messages
    response: dict | None       # full LLM response (None on error)
    tool_calls: list[dict] | None
    latency_ms: float
    token_usage: TokenUsage | None
    status: str                 # "success" | "error"
    error: str | None
```

### LLMTraceStore Class

```python
class LLMTraceStore:
    def __init__(self, path: str | Path): ...
    def record(self, trace: LLMTraceRecord) -> None: ...
    def list_traces(self, limit: int = 100) -> list[LLMTraceRecord]: ...
    def get_trace(self, trace_id: str) -> LLMTraceRecord | None: ...
```

- Append-only JSONL storage
- Corrupt line resilience (skip malformed lines on read)
- Consistent with existing `MissionMemoryStore`, `EventLedger`, `JsonlTaskQueue` patterns

## Component 5: CLI Integration

### New CLI Flags for plan-mission

```bash
python -m fireclaw_core.mission_cli plan-mission "去二楼救人" \
  --planner llm \
  --provider-base-url https://api.deepseek.com \
  --provider-api-key sk-xxx \
  --model deepseek-chat \
  --catalog config/models.json \
  --llm-trace-path logs/llm-traces.jsonl
```

### New CLI Subcommand: llm-trace

```bash
python -m fireclaw_core.mission_cli llm-trace list [--limit N]
python -m fireclaw_core.mission_cli llm-trace show <trace_id>
```

### Provider Config File

```bash
python -m fireclaw_core.mission_cli plan-mission "去二楼救人" \
  --provider-config config/provider.json
```

```json
{
  "provider": {
    "base_url": "https://api.deepseek.com",
    "api_key": "${DEEPSEEK_API_KEY}",
    "timeout": 60.0
  },
  "model": "deepseek-chat",
  "catalog": "config/models.json",
  "llm_trace_path": "logs/llm-traces.jsonl"
}
```

### Backward Compatibility

- `--planner deterministic` uses the existing `MissionPlanner` (regex-based)
- `--planner llm` (default when provider configured) uses `LLMMissionPlanner`
- If no provider config and no `--planner` flag, defaults to deterministic

## File Structure

| File | Responsibility |
|------|---------------|
| `src/fireclaw_core/provider.py` | `ModelProvider` protocol, `OpenAICompatProvider`, error types, `ChatCompletion`/`ToolCall`/`TokenUsage` data types |
| `src/fireclaw_core/model_catalog.py` | `ModelDescriptor`, `ModelCatalog`, config loading |
| `src/fireclaw_core/llm_planner.py` | `LLMMissionPlanner`, system prompt, tool schema, validation |
| `src/fireclaw_core/llm_trace.py` | `LLMTraceRecord`, `LLMTraceStore` |
| `tests/test_provider.py` | Provider protocol and OpenAICompatProvider tests |
| `tests/test_model_catalog.py` | ModelCatalog tests |
| `tests/test_llm_planner.py` | LLMMissionPlanner tests with mock provider |
| `tests/test_llm_trace.py` | LLMTraceStore tests |

## Testing Strategy

- **Provider tests**: Mock httpx responses, verify request format, test error handling (timeout, 401, 500, malformed response)
- **ModelCatalog tests**: Load from JSON, resolve by id, handle missing models
- **LLM Planner tests**: Mock provider, verify system prompt construction, verify tool schema, verify output validation, verify error handling
- **LLM Trace tests**: Write/read JSONL, corrupt line resilience, trace retrieval

## Dependencies

- `httpx` (HTTP client) - already available in most Python environments
- No dependency on `openai` SDK

## Open Questions

None. All design decisions confirmed with user.
