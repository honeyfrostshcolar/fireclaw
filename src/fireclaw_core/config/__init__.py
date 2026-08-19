"""Experimental FireClaw configuration-assistant components."""
from __future__ import annotations

from fireclaw_core.config.templates import (
    RobotTemplate,
    TemplateManager,
    BUILTIN_TEMPLATES,
)
from fireclaw_core.config.discovery import (
    DiscoveryReport,
    RosGraphDiscoverer,
)
from fireclaw_core.config.schema import (
    ConfigField,
    PluginConfigSchema,
    get_core_config_schemas,
)
from fireclaw_core.config.diff_engine import (
    DiffField,
    ConfigDiffResult,
    ConfigDiffEngine,
)
from fireclaw_core.config.snapshots import (
    ConfigSnapshot,
    ProfileSnapshotManager,
)
from fireclaw_core.config.secrets import (
    SecretManager,
)

__all__ = [
    "RobotTemplate",
    "TemplateManager",
    "BUILTIN_TEMPLATES",
    "DiscoveryReport",
    "RosGraphDiscoverer",
    "ConfigField",
    "PluginConfigSchema",
    "get_core_config_schemas",
    "DiffField",
    "ConfigDiffResult",
    "ConfigDiffEngine",
    "ConfigSnapshot",
    "ProfileSnapshotManager",
    "SecretManager",
]
