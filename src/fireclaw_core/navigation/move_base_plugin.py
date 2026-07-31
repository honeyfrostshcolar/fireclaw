"""Compatibility imports for the external ``navigation-move-base`` plugin.

Move-base is an extension-owned capability.  The canonical manifest,
entrypoint, parameter catalog, ROS adapter, and Tool contracts live under
``extensions/navigation-move-base/plugin`` and are discovered by the generic
extension loader.  This module remains only for callers that used the old
FireClaw 0.1 import path; it does not define or register navigation behavior.

New integrations should load the extension from its
``fireclaw.plugin.json`` manifest instead of importing this compatibility
module.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from fireclaw_plugin_sdk import DeploymentMode
from fireclaw_core.plugin.plugin_host import FireClawPluginHost, PluginRecord


_PROVIDER_MODULE_NAME = "fireclaw_navigation_move_base_provider"
_PROVIDER_PATH = (
    Path(__file__).resolve().parents[3]
    / "extensions"
    / "navigation-move-base"
    / "plugin"
    / "move_base.py"
)


def _provider() -> ModuleType:
    loaded = sys.modules.get(_PROVIDER_MODULE_NAME)
    if loaded is not None:
        return loaded
    if not _PROVIDER_PATH.is_file():
        raise ImportError(
            "navigation-move-base extension is not installed at "
            f"{_PROVIDER_PATH}"
        )
    spec = importlib.util.spec_from_file_location(
        _PROVIDER_MODULE_NAME,
        _PROVIDER_PATH,
    )
    if spec is None or spec.loader is None:
        raise ImportError("navigation-move-base extension has no import loader")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PROVIDER_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_PROVIDER_MODULE_NAME, None)
        raise
    return module


def __getattr__(name: str) -> Any:
    """Resolve legacy symbols from the provider-owned implementation."""

    return getattr(_provider(), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_provider())))


def register_move_base_navigation_plugin(
    host: FireClawPluginHost,
    backend: Any,
    *,
    mode: DeploymentMode,
    real_mutation_enabled: bool = False,
    real_mutable_parameters: Sequence[str] = (),
    plugin_id: str = "fireclaw.navigation.move-base",
) -> PluginRecord:
    """Compatibility registration for callers of the pre-manifest API.

    The provider now contributes public ``fireclaw_plugin_sdk.ToolSpec``
    values.  This legacy helper remains in FireClaw core only so old callers
    do not force the extension package to import the internal Plugin Host.
    New code should load the extension manifest instead.
    """

    provider = _provider()
    policy = provider.MoveBaseParameterPolicy(
        mode=mode,
        real_mutation_enabled=real_mutation_enabled,
        real_mutable_parameters=frozenset(
            str(item) for item in real_mutable_parameters
        ),
    )
    mutation_modes: tuple[DeploymentMode, ...]
    if mode == "simulation" or real_mutation_enabled:
        mutation_modes = (mode,)
    else:
        mutation_modes = ("simulation",)
    tools = provider.move_base_navigation_agent_tools(
        backend,
        policy=policy,
        mutation_modes=mutation_modes,
    )

    def register(api: Any) -> None:
        for tool in tools:
            api.register_tool(
                tool,
                metadata={
                    **dict(tool.metadata),
                    "tool_class": "agent_tool",
                    "bounded_navigation_control": tool.effect
                    == "bounded_mutation",
                    "simulation_allows_catalog_mutation": (
                        tool.name == "move_base_set_parameters"
                    ),
                },
            )

    return host.activate(
        plugin_id,
        register,
        name="move_base navigation tools",
        description=(
            "Typed navigation status, bounded tuning, cancel and costmap "
            "tools for ROS1 move_base."
        ),
        source="navigation_extension",
        trust_level="trusted",
    )


__all__ = [
    "InMemoryMoveBaseBackend",
    "MoveBaseNavigationBackend",
    "MoveBaseParameterPolicy",
    "Ros1MoveBaseBackend",
    "MOVE_BASE_PLUGIN_ID",
    "move_base_navigation_agent_tools",
    "move_base_parameter_catalog",
    "register_move_base_navigation_plugin",
]
