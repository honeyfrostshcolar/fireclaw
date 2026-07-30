"""FireClaw agent subpackage.

Public conveniences are resolved lazily so importing a low-level module such
as ``agent.robot`` does not pull the Agent Harness and policy runtime into the
robot adapter dependency graph.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "AgentHarness": ("fireclaw_core.agent.harness", "AgentHarness"),
    "AgentHarnessAttempt": (
        "fireclaw_core.agent.harness",
        "AgentHarnessAttempt",
    ),
    "AgentHarnessAttemptResult": (
        "fireclaw_core.agent.harness",
        "AgentHarnessAttemptResult",
    ),
    "AgentHarnessError": (
        "fireclaw_core.agent.harness",
        "AgentHarnessError",
    ),
    "ProviderAgentHarness": (
        "fireclaw_core.agent.harness",
        "ProviderAgentHarness",
    ),
    "AgentTool": ("fireclaw_core.agent.tool_runtime", "AgentTool"),
    "AgentToolExecution": (
        "fireclaw_core.agent.tool_runtime",
        "AgentToolExecution",
    ),
    "AgentToolProjection": (
        "fireclaw_core.agent.tool_runtime",
        "AgentToolProjection",
    ),
    "AgentToolRuntime": (
        "fireclaw_core.agent.tool_runtime",
        "AgentToolRuntime",
    ),
    "register_agent_tool": (
        "fireclaw_core.agent.tool_runtime",
        "register_agent_tool",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
