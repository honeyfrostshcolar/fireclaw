from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fireclaw_core.log_redaction import redact_secrets
from fireclaw_core.provider import TokenUsage


@dataclass(frozen=True)
class LLMTraceRecord:
    """A single LLM call trace record."""

    trace_id: str
    timestamp: str
    provider: str
    model: str
    messages: list[dict[str, Any]]
    response: dict[str, Any] | None
    tool_calls: list[dict[str, Any]] | None
    latency_ms: float
    token_usage: TokenUsage | None
    status: str
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Ensure token_usage is correctly serialized if present
        if self.token_usage:
            d["token_usage"] = asdict(self.token_usage)
        return d


class LLMTraceStore:
    """Append-only JSONL store for LLM trace records."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record(self, trace: LLMTraceRecord) -> None:
        """Append a trace record to the store."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def list_traces(self, limit: int = 100) -> list[LLMTraceRecord]:
        """List trace records, newest first, up to `limit`."""
        if limit <= 0:
            return []

        all_traces = self._read_all()
        # Return newest first (append order is oldest first)
        return list(reversed(all_traces))[:limit]

    def get_trace(self, trace_id: str) -> LLMTraceRecord | None:
        """Get a specific trace by ID."""
        all_traces = self._read_all()
        for trace in all_traces:
            if trace.trace_id == trace_id:
                return trace
        return None

    def redact_all(self) -> int:
        """Apply secret redaction to all stored traces.

        Returns the number of traces that were modified.
        """
        traces = self._read_all()
        if not traces:
            return 0

        redacted_count = 0
        redacted_traces: list[LLMTraceRecord] = []

        for trace in traces:
            original = trace.to_dict()
            new_messages = _redact_messages(trace.messages)
            new_response = _redact_dict_values(trace.response) if trace.response else None

            if new_messages != trace.messages or new_response != trace.response:
                redacted_count += 1

            redacted_traces.append(
                LLMTraceRecord(
                    trace_id=trace.trace_id,
                    timestamp=trace.timestamp,
                    provider=trace.provider,
                    model=trace.model,
                    messages=new_messages,
                    response=new_response,
                    tool_calls=trace.tool_calls,
                    latency_ms=trace.latency_ms,
                    token_usage=trace.token_usage,
                    status=trace.status,
                    error=trace.error,
                )
            )

        if redacted_count == 0:
            return 0

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            for trace in redacted_traces:
                handle.write(json.dumps(trace.to_dict(), ensure_ascii=False, sort_keys=True))
                handle.write("\n")

        return redacted_count

    def _read_all(self) -> list[LLMTraceRecord]:
        """Read all valid traces from the JSONL file."""
        if not self.path.exists():
            return []

        traces: list[LLMTraceRecord] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    continue

                if isinstance(data, dict):
                    traces.append(_trace_from_dict(data))
        return traces


def _trace_from_dict(data: dict[str, Any]) -> LLMTraceRecord:
    """Reconstruct a LLMTraceRecord from a dictionary."""
    # Handle token_usage reconstruction
    token_usage_data = data.get("token_usage")
    token_usage = None
    if isinstance(token_usage_data, dict):
        token_usage = TokenUsage(**token_usage_data)

    return LLMTraceRecord(
        trace_id=str(data.get("trace_id") or ""),
        timestamp=str(data.get("timestamp") or ""),
        provider=str(data.get("provider") or ""),
        model=str(data.get("model") or ""),
        messages=data.get("messages") if isinstance(data.get("messages"), list) else [],
        response=data.get("response") if isinstance(data.get("response"), dict) else None,
        tool_calls=data.get("tool_calls") if isinstance(data.get("tool_calls"), list) else None,
        latency_ms=float(data.get("latency_ms") or 0.0),
        token_usage=token_usage,
        status=str(data.get("status") or "unknown"),
        error=data.get("error") if isinstance(data.get("error"), str) else None,
    )


def _redact_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Redact secrets in message content fields."""
    result = []
    for msg in messages:
        new_msg = dict(msg)
        if isinstance(new_msg.get("content"), str):
            new_msg["content"] = redact_secrets(new_msg["content"])
        elif isinstance(new_msg.get("content"), list):
            new_msg["content"] = [
                {**part, "text": redact_secrets(part["text"])} if isinstance(part.get("text"), str) else part
                for part in new_msg["content"]
            ]
        result.append(new_msg)
    return result


def _redact_dict_values(data: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets in top-level string values of a dict."""
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = redact_secrets(value)
        else:
            result[key] = value
    return result
