"""Compatibility bridge for the provider-owned ROS1 diagnostic Plugin.

The Tool schemas live in ``extensions/ros1-diagnostics``.  This module is
kept only for older callers that imported the historical registration helper.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.plugin.sdk_adapter import normalize_registered_tool

if TYPE_CHECKING:
    from fireclaw_core.agent.tool_runtime import AgentTool


ROS1_DIAGNOSTIC_TOOL_PLUGIN_ID = "fireclaw.agent-tools.ros1-diagnostics"
_PROVIDER_PATH = (
    Path(__file__).resolve().parents[3]
    / "extensions"
    / "ros1-diagnostics"
    / "plugin"
    / "entrypoint.py"
)


def _provider() -> Any:
    module_name = "fireclaw_ros1_diagnostics_provider"
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(module_name, _PROVIDER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("ros1-diagnostics extension provider is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def ros1_diagnostic_agent_tools(backend: Any) -> tuple[Any, ...]:
    """Compatibility projection of provider-owned public ToolSpecs."""

    return tuple(
        normalize_registered_tool(value)
        for value in _provider()._tools(backend)
    )


def register_ros1_diagnostic_tool_plugin(
    host: FireClawPluginHost,
    backend: Any,
    *,
    plugin_id: str = ROS1_DIAGNOSTIC_TOOL_PLUGIN_ID,
) -> None:
    """Compatibility registration for pre-manifest callers."""

    tools = ros1_diagnostic_agent_tools(backend)

    def register(api: Any) -> None:
        for tool in tools:
            api.register_tool(
                tool,
                metadata={
                    "tool_class": "agent_tool",
                    "effect": tool.effect,
                    "roles": list(tool.roles),
                    "modes": list(tool.modes),
                    "requires_sandbox": tool.requires_sandbox,
                    "family": "ros1_diagnostics",
                    "read_only": True,
                    "bounded_sampling": True,
                },
            )
            api.register_hook(
                "before_tool_call",
                tool.name,
                _provider()._policy_hook(backend, tool.name),
            )

    host.activate(
        plugin_id,
        register,
        name="ROS1 diagnostic tools",
        description="Typed, read-only and bounded ROS1 observations.",
        source="compatibility:ros1-diagnostics-extension",
        trust_level="builtin",
    )


__all__ = [
    "ROS1_DIAGNOSTIC_TOOL_PLUGIN_ID",
    "register_ros1_diagnostic_tool_plugin",
    "ros1_diagnostic_agent_tools",
]
