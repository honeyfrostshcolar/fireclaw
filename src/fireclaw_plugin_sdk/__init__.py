"""Public, host-neutral SDK for FireClaw native extensions.

Plugins should import contracts from this package instead of importing
``fireclaw_core`` implementation modules.  The host converts these immutable
specifications to its internal Agent Tool model during registration.
"""

from fireclaw_plugin_sdk.tools import (
    AgentRole,
    DeploymentMode,
    PhysicalToolHandler,
    PhysicalToolSpec,
    PluginApi,
    TaskInputBindingSpec,
    ToolEffect,
    ToolHandler,
    ToolSpec,
)

__all__ = [
    "AgentRole",
    "DeploymentMode",
    "PluginApi",
    "PhysicalToolHandler",
    "PhysicalToolSpec",
    "TaskInputBindingSpec",
    "ToolEffect",
    "ToolHandler",
    "ToolSpec",
]
