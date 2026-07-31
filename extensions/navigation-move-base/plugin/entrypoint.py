"""FireClaw entrypoint owned by the move_base extension package.

The generic host discovers this module from ``fireclaw.plugin.json``.  The
module supplies the move_base-specific backend selection, parameter policy and
Tool contributions; Gateway code does not name these Tools.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fireclaw_plugin_sdk import PluginApi

from .move_base import (
    AdapterDispatchMoveBaseBackend,
    InMemoryMoveBaseBackend,
    MoveBaseParameterPolicy,
    Ros1MoveBaseBackend,
    move_base_navigation_physical_tools,
    move_base_navigation_agent_tools,
)


def _config(api: PluginApi, key: str, legacy_key: str, default: Any) -> Any:
    """Read provider config, retaining old Gateway keys during migration."""

    value = api.config.get(key)
    if value is not None:
        return value
    gateway_config = api.services.get("gateway_config")
    return getattr(gateway_config, legacy_key, default)


def _backend(api: PluginApi) -> Any | None:
    injected = api.services.get("move_base_navigation_backend")
    if injected is not None:
        return injected
    adapter = str(api.services.get("adapter") or "")
    if adapter == "ros1":
        return Ros1MoveBaseBackend()
    if adapter in {"dry-run", "simulator", "mock-ros1", "mock-ros2"}:
        dispatcher = api.services.get("robot_action_dispatch")
        if callable(dispatcher):
            return AdapterDispatchMoveBaseBackend(dispatcher)
        return InMemoryMoveBaseBackend()
    return None


def register(api: PluginApi) -> None:
    """Register the extension's six typed move_base Agent Tools."""

    if not bool(
        _config(
            api,
            "enabled",
            "move_base_navigation_tools_enabled",
            True,
        )
    ):
        return
    backend = _backend(api)
    if backend is None:
        return

    # Physical motion is contributed by the Navigation Plugin itself.  The
    # core only projects this contract through its generic lifecycle/safety
    # runtime; it does not require Ros1RobotAdapter.navigate_to_point().
    if api.role == "robot_agent" and callable(
        getattr(backend, "navigate_to_point", None)
    ):
        for physical_tool in move_base_navigation_physical_tools(backend):
            api.register_physical_tool(physical_tool)

    real_mutation_enabled = bool(
        _config(api, "real_mutation_enabled", "move_base_real_mutation_enabled", False)
    )
    raw_allowlist = _config(
        api,
        "real_mutable_parameters",
        "move_base_real_mutable_parameters",
        (),
    )
    if isinstance(raw_allowlist, str):
        real_mutable_parameters: tuple[str, ...] = (raw_allowlist,)
    elif isinstance(raw_allowlist, Sequence):
        real_mutable_parameters = tuple(str(item) for item in raw_allowlist)
    else:
        real_mutable_parameters = ()

    policy = MoveBaseParameterPolicy(
        mode=api.mode,
        real_mutation_enabled=real_mutation_enabled,
        real_mutable_parameters=frozenset(real_mutable_parameters),
    )
    if api.mode == "simulation" or real_mutation_enabled:
        mutation_modes = (api.mode,)
    else:
        # Keep the contribution auditable without projecting mutation Tools to
        # a real Robot Agent whose policy has not explicitly enabled them.
        mutation_modes = ("simulation",)

    for tool in move_base_navigation_agent_tools(
        backend,
        policy=policy,
        mutation_modes=mutation_modes,
    ):
        api.register_tool(
            tool,
            metadata={
                **dict(tool.metadata),
                "tool_class": "agent_tool",
                "extension_owned": True,
                "bounded_navigation_control": tool.effect == "bounded_mutation",
                "simulation_allows_catalog_mutation": (
                    tool.name == "move_base_set_parameters"
                ),
            },
        )
