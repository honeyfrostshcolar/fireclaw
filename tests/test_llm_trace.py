import json
import pytest
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

from fireclaw_core.planner.llm_trace import LLMTraceRecord, LLMTraceStore
from fireclaw_core.provider.provider import TokenUsage

@pytest.fixture
def trace_store(tmp_path):
    return LLMTraceStore(path=tmp_path / "traces.jsonl")

@pytest.fixture
def sample_trace():
    return LLMTraceRecord(
        trace_id="trace-123",
        timestamp=datetime.now(timezone.utc).isoformat(),
        provider="openai",
        model="gpt-4",
        messages=[{"role": "user", "content": "Hello"}],
        response={"choices": [{"text": "Hi"}]},
        tool_calls=None,
        latency_ms=120.5,
        token_usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        status="success",
        error=None
    )

def test_trace_record_fields(sample_trace):
    """Test that LLMTraceRecord has all required fields."""
    assert sample_trace.trace_id == "trace-123"
    assert sample_trace.provider == "openai"
    assert sample_trace.model == "gpt-4"
    assert sample_trace.status == "success"
    assert sample_trace.token_usage is not None
    assert sample_trace.token_usage.total_tokens == 15

def test_trace_store_append_and_list(trace_store, sample_trace):
    """Test that records can be appended and listed."""
    trace_store.record(sample_trace)
    traces = trace_store.list_traces()
    assert len(traces) == 1
    assert traces[0].trace_id == "trace-123"

def test_trace_store_get_by_id(trace_store, sample_trace):
    """Test retrieving a specific trace by ID."""
    trace_store.record(sample_trace)
    found = trace_store.get_trace("trace-123")
    assert found is not None
    assert found.trace_id == "trace-123"

    not_found = trace_store.get_trace("non-existent")
    assert not_found is None

def test_trace_store_get_missing_returns_none(trace_store):
    """Test that missing ID returns None."""
    assert trace_store.get_trace("missing-id") is None

def test_trace_store_skips_corrupt_lines(trace_store, sample_trace, tmp_path):
    """Test that corrupt JSONL lines are skipped."""
    # Manually write a corrupt line and a valid line
    path = trace_store.path
    with open(path, "w") as f:
        f.write("this is corrupt json\n")
        f.write(json.dumps(sample_trace.to_dict()) + "\n")

    traces = trace_store.list_traces()
    assert len(traces) == 1
    assert traces[0].trace_id == "trace-123"

def test_trace_store_list_limit(trace_store, sample_trace):
    """Test that list_traces limit parameter works."""
    for i in range(5):
        t = LLMTraceRecord(
            trace_id=f"trace-{i}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            provider="openai",
            model="gpt-4",
            messages=[],
            response=None,
            tool_calls=None,
            latency_ms=0.0,
            token_usage=None,
            status="success",
            error=None
        )
        trace_store.record(t)

    traces = trace_store.list_traces(limit=2)
    assert len(traces) == 2
