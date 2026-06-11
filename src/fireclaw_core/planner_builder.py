"""Shared planner construction logic for CLI entry points."""
from __future__ import annotations

from typing import Any

from fireclaw_core.llm_planner import LLMMissionPlanner
from fireclaw_core.llm_trace import LLMTraceStore
from fireclaw_core.mission_planner import MissionPlanner
from fireclaw_core.provider import OpenAICompatProvider
from fireclaw_core.provider_runtime import SimpleProviderRuntime


def build_planner(
    *,
    planner_type: str = "deterministic",
    provider_base_url: str | None = None,
    provider_api_key: str | None = None,
    model: str = "gpt-4o",
    llm_trace_path: str | None = None,
) -> Any:
    if planner_type == "llm":
        if not provider_base_url:
            raise ValueError("provider_base_url is required when planner_type='llm'")
        if not provider_api_key:
            raise ValueError("provider_api_key is required when planner_type='llm'")
        if not model:
            raise ValueError("model is required when planner_type='llm'")
        provider = OpenAICompatProvider(base_url=provider_base_url, api_key=provider_api_key)
        runtime = SimpleProviderRuntime(provider=provider, model_id=model)
        trace_store = LLMTraceStore(llm_trace_path) if llm_trace_path else None
        return LLMMissionPlanner(provider_runtime=runtime, trace_store=trace_store)
    return MissionPlanner()
