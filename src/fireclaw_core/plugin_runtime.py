"""Plugin runtime v1 — loads descriptors and exposes registered hooks.

Loads :class:`~fireclaw_core.plugin_descriptor.FireClawPluginDescriptor`
instances from JSON files in a directory, validates hook names against known
sets, and aggregates hooks for integration with the planner and memory layers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fireclaw_core.plugin_descriptor import FireClawPluginDescriptor

# ---------------------------------------------------------------------------
# Known hook name sets
# ---------------------------------------------------------------------------

KNOWN_PROVIDER_HOOKS: frozenset[str] = frozenset(
    {"enrich_context", "suggest_model", "validate_output"}
)
KNOWN_MEMORY_HOOKS: frozenset[str] = frozenset(
    {"rerank", "filter", "summarize"}
)
KNOWN_TOOL_APPROVAL_HOOKS: frozenset[str] = frozenset(
    {"add_reason", "require_scope", "auto_approve"}
)


def _validate_hook_names(
    names: tuple[str, ...],
    known: frozenset[str],
    hook_type: str,
    plugin_id: str,
) -> None:
    """Raise ``ValueError`` if *names* contains any name not in *known*."""
    for name in names:
        if name not in known:
            allowed = ", ".join(sorted(known))
            raise ValueError(
                f"Plugin '{plugin_id}' declares unknown {hook_type} hook "
                f"'{name}'. Known {hook_type} hooks: {allowed}"
            )


# ---------------------------------------------------------------------------
# PluginRuntime
# ---------------------------------------------------------------------------


class PluginRuntime:
    """Loads plugin descriptors and exposes registered hooks."""

    VALID_HOOK_TYPES: frozenset[str] = frozenset(
        {"provider", "memory", "tool_approval"}
    )

    def __init__(self) -> None:
        self._descriptors: list[FireClawPluginDescriptor] = []

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_from_directory(self, directory: Path) -> int:
        """Load all ``*.json`` descriptor files from *directory*.

        Returns the number of descriptors successfully loaded.  Files that
        contain invalid JSON or produce an invalid descriptor are silently
        skipped.
        """
        count = 0
        for json_file in sorted(directory.glob("*.json")):
            descriptor = _descriptor_from_json_file(json_file)
            if descriptor is not None:
                self.register_descriptor(descriptor)
                count += 1
        return count

    def register_descriptor(self, descriptor: FireClawPluginDescriptor) -> None:
        """Register a single descriptor after validating its hook names."""
        _validate_hook_names(
            descriptor.provider_hooks,
            KNOWN_PROVIDER_HOOKS,
            "provider",
            descriptor.plugin_id,
        )
        _validate_hook_names(
            descriptor.memory_hooks,
            KNOWN_MEMORY_HOOKS,
            "memory",
            descriptor.plugin_id,
        )
        _validate_hook_names(
            descriptor.tool_approval_hooks,
            KNOWN_TOOL_APPROVAL_HOOKS,
            "tool_approval",
            descriptor.plugin_id,
        )
        self._descriptors.append(descriptor)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def descriptors(self) -> list[FireClawPluginDescriptor]:
        """Return all registered descriptors (copy)."""
        return list(self._descriptors)

    def provider_hooks(self) -> list[str]:
        """Return all registered provider hook names across all descriptors."""
        hooks: list[str] = []
        for d in self._descriptors:
            hooks.extend(d.provider_hooks)
        return hooks

    def memory_hooks(self) -> list[str]:
        """Return all registered memory hook names across all descriptors."""
        hooks: list[str] = []
        for d in self._descriptors:
            hooks.extend(d.memory_hooks)
        return hooks

    def tool_approval_hooks(self) -> list[str]:
        """Return all registered tool approval hook names across all descriptors."""
        hooks: list[str] = []
        for d in self._descriptors:
            hooks.extend(d.tool_approval_hooks)
        return hooks


# ---------------------------------------------------------------------------
# JSON deserialization
# ---------------------------------------------------------------------------


def _descriptor_from_json_file(path: Path) -> FireClawPluginDescriptor | None:
    """Try to read *path* as a descriptor JSON file.

    Returns ``None`` if the file is missing, contains invalid JSON, or
    produces invalid descriptor data.
    """
    try:
        raw = path.read_text(encoding="utf-8")
        data: dict[str, Any] = json.loads(raw)
        return _descriptor_from_dict(data)
    except (json.JSONDecodeError, OSError, TypeError, KeyError, ValueError):
        return None


def _descriptor_from_dict(data: dict[str, Any]) -> FireClawPluginDescriptor:
    """Build a :class:`FireClawPluginDescriptor` from a plain dict.

    Lists are coerced to tuples so that the frozen dataclass accepts them.
    """
    return FireClawPluginDescriptor(
        plugin_id=str(data["plugin_id"]),
        capabilities=tuple(data["capabilities"]),
        preconditions=tuple(data["preconditions"]),
        risk_level=str(data["risk_level"]),
        required_sensors=tuple(data.get("required_sensors", ())),
        adapter_bindings=tuple(data.get("adapter_bindings", ())),
        approval_scope=data.get("approval_scope"),
        provider_hooks=tuple(data.get("provider_hooks", ())),
        memory_hooks=tuple(data.get("memory_hooks", ())),
        tool_approval_hooks=tuple(data.get("tool_approval_hooks", ())),
    )
