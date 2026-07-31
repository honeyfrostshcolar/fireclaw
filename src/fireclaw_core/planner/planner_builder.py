"""Shared planner construction logic for CLI entry points."""
from __future__ import annotations

from typing import Any

from fireclaw_core.planner.llm_planner import LLMMissionPlanner
from fireclaw_core.planner.llm_trace import LLMTraceStore
from fireclaw_core.mission.mission_planner import MissionPlanner
from fireclaw_core.provider.provider import OpenAICompatProvider
from fireclaw_core.provider.provider_runtime import ProviderRuntime, SimpleProviderRuntime
from fireclaw_core.provider.model_catalog import ModelCatalog
from fireclaw_core.agent.computer_tools import ComputerSandbox
from fireclaw_core.agent.tool_runtime import AgentToolRuntime
from fireclaw_core.plugin.extension_loader import load_fireclaw_extensions
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.policy.deployment import DeploymentProfile


def build_planner(
    *,
    planner_type: str = "deterministic",
    provider_base_url: str | None = None,
    provider_api_key: str | None = None,
    model: str | None = None,
    llm_trace_path: str | None = None,
    model_catalog_path: str | None = None,
    deployment_profile: DeploymentProfile | None = None,
) -> Any:
    if planner_type == "llm":
        if not provider_base_url:
            raise ValueError("provider_base_url is required when planner_type='llm'")
        if not provider_api_key:
            raise ValueError("provider_api_key is required when planner_type='llm'")
        if not model:
            raise ValueError("model is required when planner_type='llm'")
        provider = OpenAICompatProvider(base_url=provider_base_url, api_key=provider_api_key)
        catalog = (
            ModelCatalog(model_catalog_path)
            if model_catalog_path
            else None
        )
        runtime = SimpleProviderRuntime(
            provider=provider,
            model_id=model,
            catalog=catalog,
        )
        trace_store = LLMTraceStore(llm_trace_path) if llm_trace_path else None
        plugin_host = FireClawPluginHost()
        agent_tool_runtime = None
        if (
            deployment_profile is not None
            and deployment_profile.sandbox.enabled
        ):
            load_fireclaw_extensions(
                plugin_host,
                ("extensions",),
                mode=deployment_profile.mode,
                role=deployment_profile.role,
                services={
                    "computer_sandbox": ComputerSandbox(
                        deployment_profile.sandbox
                    )
                },
            )
            agent_tool_runtime = AgentToolRuntime(
                plugin_host=plugin_host,
                profile=deployment_profile,
            )
        return LLMMissionPlanner(
            provider_runtime=runtime,
            trace_store=trace_store,
            plugin_host=plugin_host,
            agent_tool_runtime=agent_tool_runtime,
        )
    return MissionPlanner()


def build_provider_runtime(
    *,
    provider_base_url: str | None = None,
    provider_api_key: str | None = None,
    model: str | None = None,
    model_catalog_path: str | None = None,
) -> ProviderRuntime:
    if not provider_base_url:
        raise ValueError("provider_base_url is required")
    if not provider_api_key:
        raise ValueError("provider_api_key is required")
    if not model:
        raise ValueError("model is required")
    provider = OpenAICompatProvider(base_url=provider_base_url, api_key=provider_api_key)
    catalog = (
        ModelCatalog(model_catalog_path)
        if model_catalog_path
        else None
    )
    return SimpleProviderRuntime(
        provider=provider,
        model_id=model,
        catalog=catalog,
    )
