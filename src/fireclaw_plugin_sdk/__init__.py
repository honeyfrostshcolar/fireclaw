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
from fireclaw_plugin_sdk.safety import (
    HARDWARE_STOP_EVIDENCE_CLASS,
    RUNTIME_STATIONARITY_EVIDENCE_CLASS,
    RuntimeStopEvidenceProvider,
    STOP_EVIDENCE_SERVICE_PREFIX,
    stop_evidence_service_id,
)

__all__ = [
    "AgentRole",
    "DeploymentMode",
    "HARDWARE_STOP_EVIDENCE_CLASS",
    "PluginApi",
    "PhysicalToolHandler",
    "PhysicalToolSpec",
    "RUNTIME_STATIONARITY_EVIDENCE_CLASS",
    "RuntimeStopEvidenceProvider",
    "STOP_EVIDENCE_SERVICE_PREFIX",
    "TaskInputBindingSpec",
    "ToolEffect",
    "ToolHandler",
    "ToolSpec",
    "stop_evidence_service_id",
]
