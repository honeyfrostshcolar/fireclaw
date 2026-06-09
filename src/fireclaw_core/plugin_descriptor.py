"""Plugin descriptor runtime v1.

Provides a frozen dataclass that captures the runtime contract a FireClaw
plugin advertises: what it can do, what it needs, and how it integrates
with the broader agent stack (adapters, approval, memory, LLM providers).
"""
from __future__ import annotations

from dataclasses import dataclass

from fireclaw_core.skills import RISK_LEVELS, Skill


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


# ---------------------------------------------------------------------------
# Conversion from SkillManifest (Skill dataclass)
# ---------------------------------------------------------------------------

def descriptor_from_skill_manifest(skill: Skill) -> FireClawPluginDescriptor:
    """Build a :class:`FireClawPluginDescriptor` from an existing
    :class:`~fireclaw_core.skills.Skill` instance.

    The mapping is intentionally conservative — it translates fields that
    already exist on ``Skill`` without inventing new semantics.  Fields
    that have no direct ``Skill`` counterpart (``adapter_bindings``,
    ``approval_scope``, ``provider_hooks``, ``memory_hooks``) receive
    sensible defaults derived from the skill's metadata.
    """

    # --- adapter_bindings ---
    # Infer from domain and required_sensors.  A skill that needs sensors
    # implicitly needs the adapter(s) that expose those sensors.
    adapter_bindings: tuple[str, ...] = ()
    if skill.required_sensors:
        adapter_bindings = tuple(sorted(set(skill.required_sensors)))
    elif skill.domain:
        adapter_bindings = (skill.domain,)

    # --- approval_scope ---
    # Map risk_level → approval_scope so the safety gate can look it up
    # without re-deriving the mapping itself.
    _RISK_TO_SCOPE = {
        "low": None,
        "medium": "operator_confirm",
        "high": "safety_officer",
        "critical": "emergency_override",
    }
    approval_scope = _RISK_TO_SCOPE.get(skill.risk_level)

    # --- capabilities ---
    # Primary capability is the skill name; the domain adds a semantic tag.
    capabilities: list[str] = [skill.name]
    if skill.domain and skill.domain not in capabilities:
        capabilities.append(skill.domain)

    # --- required_sensors ---
    required_sensors = tuple(skill.required_sensors)

    # --- preconditions ---
    preconditions = tuple(skill.preconditions) if skill.preconditions else ("skill_available",)

    return FireClawPluginDescriptor(
        plugin_id=skill.name,
        capabilities=tuple(capabilities),
        preconditions=preconditions,
        risk_level=skill.risk_level,
        required_sensors=required_sensors,
        adapter_bindings=adapter_bindings,
        approval_scope=approval_scope,
        provider_hooks=(),
        memory_hooks=(),
    )
