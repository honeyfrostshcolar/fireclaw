"""Plugin control-plane context for FireClaw.

Inspired by OpenClaw's plugin-control-plane-context.ts — provides a
frozen dataclass representing the full context of the plugin control
plane, including discovery, policy, inventory, and activation
fingerprints for auditable plugin state management.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class PluginDiscoveryContext:
    """Immutable context for plugin discovery configuration."""
    roots: tuple[str, ...]
    load_paths: tuple[str, ...]


@dataclass(frozen=True)
class PluginControlPlaneContext:
    """Immutable context representing the complete plugin control plane state.

    Attributes
    ----------
    discovery:
        The configuration describing where plugins are discovered from.
    policy_fingerprint:
        A stable hash representing the current policy state.
    inventory_fingerprint:
        A stable hash representing the current set of registered descriptors,
        or None if inventory has not been fingerprinted.
    activation_fingerprint:
        A stable hash representing which plugins/hooks are currently active,
        or None if activation has not been fingerprinted.
    """
    discovery: PluginDiscoveryContext
    policy_fingerprint: str
    inventory_fingerprint: str | None = None
    activation_fingerprint: str | None = None


def hash_json(data: object) -> str:
    """Produce a stable, deterministic SHA-256 hex digest of *data*.

    Uses ``json.dumps`` with ``sort_keys=True`` to ensure identical
    objects always produce the same hash regardless of key ordering.
    """
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def fingerprint_plugin_control_plane_context(
    discovery: PluginDiscoveryContext,
    policy_fingerprint: str,
    inventory_fingerprint: str | None = None,
    activation_fingerprint: str | None = None,
) -> str:
    """Produce a single hash encompassing all control-plane fingerprints."""
    payload = {
        "discovery": {
            "roots": list(discovery.roots),
            "load_paths": list(discovery.load_paths),
        },
        "policy_fingerprint": policy_fingerprint,
        "inventory_fingerprint": inventory_fingerprint,
        "activation_fingerprint": activation_fingerprint,
    }
    return hash_json(payload)


def resolve_plugin_control_plane_context(
    discovery: PluginDiscoveryContext,
    policy_fingerprint: str,
    inventory_fingerprint: str | None = None,
    activation_fingerprint: str | None = None,
) -> PluginControlPlaneContext:
    """Assemble a fully-resolved :class:`PluginControlPlaneContext`.

    This convenience function validates that *policy_fingerprint* is
    non-empty, then constructs the frozen dataclass.
    """
    if not policy_fingerprint or not policy_fingerprint.strip():
        raise ValueError("policy_fingerprint must be a non-empty string.")
    return PluginControlPlaneContext(
        discovery=discovery,
        policy_fingerprint=policy_fingerprint,
        inventory_fingerprint=inventory_fingerprint,
        activation_fingerprint=activation_fingerprint,
    )
