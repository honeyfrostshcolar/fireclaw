"""FireClaw entrypoint owned by the move_base extension package.

The generic host discovers this module from ``fireclaw.plugin.json``.  The
module supplies the move_base-specific backend selection, parameter policy and
Tool contributions; Gateway code does not name these Tools.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fireclaw_plugin_sdk import PluginApi
from fireclaw_plugin_sdk import stop_evidence_service_id

from .move_base import (
    InMemoryMoveBaseBackend,
    MoveBaseParameterPolicy,
    Ros1MoveBaseBackend,
    move_base_navigation_physical_tools,
    move_base_navigation_agent_tools,
)


BACKEND_SERVICE = "fireclaw.navigation.move-base.backend"
STOP_EVIDENCE_SERVICE = stop_evidence_service_id("navigation-move-base")


def _backend(api: PluginApi) -> Any | None:
    # A deployment or acceptance harness may inject a namespaced backend
    # through the host's generic service bag. FireClaw core does not know this
    # key and never supplies a navigation-specific fallback.
    injected = api.services.get(BACKEND_SERVICE)
    if injected is not None:
        return injected
    adapter = str(api.services.get("adapter") or "")
    if adapter == "ros1":
        return Ros1MoveBaseBackend()
    if api.mode == "simulation" and adapter in {
        "dry-run",
        "simulator",
        "mock-ros1",
        "mock-ros2",
    }:
        return InMemoryMoveBaseBackend()
    return None


def register(api: PluginApi) -> None:
    """Register the extension's six typed move_base Agent Tools."""

    if not bool(api.config.get("enabled", True)):
        return
    backend = _backend(api)
    if backend is None:
        return

    # Simulation recovery may use this trusted service to reassert a stop and
    # prove stationarity. Real robots require a hardware-owned witness that
    # covers every actuator, so this navigation-only witness is not registered
    # in real mode.
    if api.mode == "simulation" and callable(
        getattr(backend, "collect_stop_evidence", None)
    ):
        api.register_service(STOP_EVIDENCE_SERVICE, backend)

    # Physical motion is contributed by the Navigation Plugin itself.  The
    # core only projects this contract through its generic lifecycle/safety
    # runtime; no domain action method is required on RobotAdapter.
    if api.role == "robot_agent" and callable(
        getattr(backend, "navigate_to_point", None)
    ):
        for physical_tool in move_base_navigation_physical_tools(
            backend,
            timeout_seconds=api.config.get(
                "navigate_timeout_seconds",
                120.0,
            ),
            cancellation_ack_timeout_seconds=api.config.get(
                "cancellation_ack_timeout_seconds",
                2.0,
            ),
        ):
            api.register_physical_tool(physical_tool)

    real_mutation_enabled = bool(api.config.get("real_mutation_enabled", False))
    raw_allowlist = api.config.get("real_mutable_parameters", ())
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
