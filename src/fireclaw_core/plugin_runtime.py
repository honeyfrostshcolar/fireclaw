"""Plugin runtime v1 — loads descriptors and exposes registered hooks.

Loads :class:`~fireclaw_core.plugin_descriptor.FireClawPluginDescriptor`
instances from JSON files in a directory, validates hook names against known
sets, and aggregates hooks for integration with the planner and memory layers.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

from fireclaw_core.plugin_descriptor import FireClawPluginDescriptor

# ---------------------------------------------------------------------------
# Callable hook types
# ---------------------------------------------------------------------------

PluginHookCallback = Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]


@dataclass(frozen=True)
class PluginHookEffect:
    """Structured result of a single hook callable invocation."""

    plugin_id: str
    hook_name: str
    effect: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "hook_name": self.hook_name,
            "effect": dict(self.effect),
        }

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
        self._callables: dict[tuple[str, str], list[tuple[str, PluginHookCallback]]] = {}
        self.plugin_policy: Any = None  # PluginPolicy | None — avoid circular import

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

    def inventory(self) -> dict[str, Any]:
        """Return the current runtime inventory as a plain dict.

        This is a safe snapshot that can be used for auditing or
        fingerprinting without importing any external code.
        """
        descriptor_ids: list[str] = []
        hook_names: list[str] = []
        for d in self._descriptors:
            descriptor_ids.append(d.plugin_id)
            hook_names.extend(d.provider_hooks)
            hook_names.extend(d.memory_hooks)
            hook_names.extend(d.tool_approval_hooks)
        return {
            "descriptor_ids": tuple(descriptor_ids),
            "hook_names": tuple(hook_names),
            "policy_active": self.plugin_policy is not None,
        }

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

    # ------------------------------------------------------------------
    # Callable hook registration and execution
    # ------------------------------------------------------------------

    def register_callable(
        self,
        *,
        hook_type: str,
        hook_name: str,
        plugin_id: str,
        callback: PluginHookCallback,
    ) -> None:
        """Register a callable for a known hook.

        Raises ``ValueError`` if *hook_type* or *hook_name* is not in the
        known sets, or if a plugin policy rejects the registration.
        """
        self._validate_known_hook(hook_type, hook_name, plugin_id)
        if self.plugin_policy is not None:
            descriptor = self._find_descriptor(plugin_id)
            if descriptor is None:
                result = self.plugin_policy.reject_unknown_plugin_registration(
                    plugin_id=plugin_id,
                    hook_type=hook_type,
                    hook_name=hook_name,
                )
                raise ValueError(result.reason)
            else:
                result = self.plugin_policy.evaluate_registration(
                    descriptor, hook_type, hook_name,
                )
                if not result.allowed:
                    raise ValueError(result.reason)
        self._callables.setdefault((hook_type, hook_name), []).append(
            (plugin_id, callback)
        )

    def _find_descriptor(self, plugin_id: str) -> FireClawPluginDescriptor | None:
        """Return the descriptor with *plugin_id*, or ``None``."""
        for d in self._descriptors:
            if d.plugin_id == plugin_id:
                return d
        return None

    def run_provider_hooks(
        self, hook_name: str, payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Execute all registered provider *hook_name* callables."""
        return self._run_hooks("provider", hook_name, payload)

    def run_memory_hooks(
        self, hook_name: str, payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Execute all registered memory *hook_name* callables."""
        return self._run_hooks("memory", hook_name, payload)

    def run_tool_approval_hooks(
        self, hook_name: str, payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Execute all registered tool_approval *hook_name* callables."""
        return self._run_hooks("tool_approval", hook_name, payload)

    def _run_hooks(
        self, hook_type: str, hook_name: str, payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        effects: list[dict[str, Any]] = []
        for plugin_id, callback in self._callables.get(
            (hook_type, hook_name), []
        ):
            try:
                result = callback(dict(payload))  # copy to prevent mutation
            except Exception:
                # A buggy plugin must not prevent other plugins from running.
                logger.warning(
                    "Plugin '%s' %s hook '%s' raised an exception; skipping.",
                    plugin_id,
                    hook_type,
                    hook_name,
                    exc_info=True,
                )
                continue
            if result is None:
                continue
            if not isinstance(result, dict):
                raise ValueError(
                    f"Plugin '{plugin_id}' {hook_type} hook '{hook_name}' "
                    f"must return a dict or None."
                )
            effects.append(
                PluginHookEffect(plugin_id, hook_name, result).to_dict()
            )
        return effects

    def _validate_known_hook(
        self, hook_type: str, hook_name: str, plugin_id: str
    ) -> None:
        if hook_type == "provider":
            _validate_hook_names(
                (hook_name,), KNOWN_PROVIDER_HOOKS, hook_type, plugin_id
            )
        elif hook_type == "memory":
            _validate_hook_names(
                (hook_name,), KNOWN_MEMORY_HOOKS, hook_type, plugin_id
            )
        elif hook_type == "tool_approval":
            _validate_hook_names(
                (hook_name,), KNOWN_TOOL_APPROVAL_HOOKS, hook_type, plugin_id
            )
        else:
            raise ValueError(f"Unknown hook type: {hook_type}")


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
