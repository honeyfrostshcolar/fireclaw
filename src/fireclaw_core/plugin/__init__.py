"""FireClaw plugin subpackage."""

from fireclaw_core.plugin.plugin_host import (
    FireClawPluginApi,
    FireClawPluginHost,
    PluginContribution,
    PluginDiagnostic,
    PluginRecord,
    PluginRegistrationError,
    PluginTrustLevel,
)
from fireclaw_core.plugin.extension_loader import (
    EXTENSION_MANIFEST_NAME,
    FIRECLAW_EXTENSION_API_VERSION,
    FireClawExtensionApi,
    FireClawExtensionCandidate,
    FireClawExtensionContext,
    FireClawExtensionDiagnostic,
    FireClawExtensionDiscovery,
    FireClawExtensionLoadReport,
    FireClawExtensionManifest,
    discover_fireclaw_extensions,
    load_fireclaw_extensions,
)

__all__ = [
    "FireClawPluginApi",
    "FireClawPluginHost",
    "PluginContribution",
    "PluginDiagnostic",
    "PluginRecord",
    "PluginRegistrationError",
    "PluginTrustLevel",
    "EXTENSION_MANIFEST_NAME",
    "FIRECLAW_EXTENSION_API_VERSION",
    "FireClawExtensionApi",
    "FireClawExtensionCandidate",
    "FireClawExtensionContext",
    "FireClawExtensionDiagnostic",
    "FireClawExtensionDiscovery",
    "FireClawExtensionLoadReport",
    "FireClawExtensionManifest",
    "discover_fireclaw_extensions",
    "load_fireclaw_extensions",
]
