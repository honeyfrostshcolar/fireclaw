"""Plugin descriptor runtime v1.

Provides a frozen dataclass that captures the runtime contract a FireClaw
plugin advertises: what it can do, what it needs, and how it integrates
with the broader agent stack (adapters, approval, memory, LLM providers).
"""
from __future__ import annotations

from dataclasses import dataclass

from fireclaw_core.execution.skills import RISK_LEVELS


@dataclass(frozen=True)
class FireClawPluginDescriptor:
    """Immutable descriptor for a FireClaw plugin / skill bundle.

    Every field is validated at construction time so that downstream
    consumers (safety gate, planner, adapter layer) can trust the
    descriptor without re-checking.
    """

    plugin_id: str
    capabilities: tuple[str, ...]
    preconditions: tuple[str, ...]
    risk_level: str
    required_sensors: tuple[str, ...]
    adapter_bindings: tuple[str, ...]
    approval_scope: str | None = None
    provider_hooks: tuple[str, ...] = ()
    memory_hooks: tuple[str, ...] = ()
    tool_approval_hooks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty_string(self.plugin_id, "plugin_id")
        _require_non_empty_tuple(self.capabilities, "capabilities")
        _require_non_empty_tuple(self.preconditions, "preconditions")
        _require_risk_level(self.risk_level)
        # required_sensors and adapter_bindings may be empty for skills
        # that have no hardware dependency (e.g. pure-logic helpers).
        _require_string_tuple(self.required_sensors, "required_sensors")
        _require_string_tuple(self.adapter_bindings, "adapter_bindings")
        if self.approval_scope is not None:
            _require_non_empty_string(self.approval_scope, "approval_scope")
        _require_string_tuple(self.provider_hooks, "provider_hooks")
        _require_string_tuple(self.memory_hooks, "memory_hooks")
        _require_string_tuple(self.tool_approval_hooks, "tool_approval_hooks")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _require_non_empty_string(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"PluginDescriptor field '{field_name}' must be a non-empty string.")


def _require_non_empty_tuple(value: tuple[str, ...], field_name: str) -> None:
    if not isinstance(value, tuple) or not value:
        raise ValueError(f"PluginDescriptor field '{field_name}' must be a non-empty tuple.")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"PluginDescriptor field '{field_name}' contains a non-empty-string entry."
            )


def _require_string_tuple(value: tuple[str, ...], field_name: str) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"PluginDescriptor field '{field_name}' must be a tuple.")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"PluginDescriptor field '{field_name}' contains a non-empty-string entry."
            )


def _require_risk_level(value: str) -> None:
    if not isinstance(value, str) or value not in RISK_LEVELS:
        allowed = ", ".join(sorted(RISK_LEVELS))
        raise ValueError(
            f"PluginDescriptor field 'risk_level' must be one of: {allowed}."
        )
