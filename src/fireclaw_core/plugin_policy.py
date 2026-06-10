"""Plugin policy enforcement and audit trail for FireClaw.

Inspired by OpenClaw's plugin-control-plane-context.ts — enforces that
plugins can only register hooks declared in their descriptor, and records
every hook registration/execution for incident audit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fireclaw_core.plugin_descriptor import FireClawPluginDescriptor


@dataclass(frozen=True)
class PluginHookPermission:
    """Result of evaluating whether a plugin may register a hook."""
    allowed: bool
    plugin_id: str
    hook_type: str
    hook_name: str
    reason: str = ""


@dataclass(frozen=True)
class PluginHookAuditRecord:
    """Audit trail entry for a hook registration or execution."""
    timestamp: str
    plugin_id: str
    hook_type: str
    hook_name: str
    allowed: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "plugin_id": self.plugin_id,
            "hook_type": self.hook_type,
            "hook_name": self.hook_name,
            "allowed": self.allowed,
            "reason": self.reason,
        }


class PluginPolicy:
    """Enforces plugin hook permissions and maintains an audit trail.

    Usage:
        policy = PluginPolicy(descriptors=runtime.descriptors)
        result = policy.evaluate_registration(descriptor, "provider", "enrich_context")
        if not result.allowed:
            raise ValueError(result.reason)
    """

    def __init__(
        self,
        *,
        descriptors: list[FireClawPluginDescriptor] | None = None,
    ) -> None:
        self._descriptor_hooks: dict[str, set[tuple[str, str]]] = {}
        self._audit: list[PluginHookAuditRecord] = []
        if descriptors:
            for d in descriptors:
                self._index_descriptor(d)

    def _index_descriptor(self, descriptor: FireClawPluginDescriptor) -> None:
        hooks: set[tuple[str, str]] = set()
        for h in descriptor.provider_hooks:
            hooks.add(("provider", h))
        for h in descriptor.memory_hooks:
            hooks.add(("memory", h))
        for h in descriptor.tool_approval_hooks:
            hooks.add(("tool_approval", h))
        self._descriptor_hooks[descriptor.plugin_id] = hooks

    def evaluate_registration(
        self,
        descriptor: FireClawPluginDescriptor,
        hook_type: str,
        hook_name: str,
    ) -> PluginHookPermission:
        """Check whether *descriptor* is allowed to register *hook_type*/*hook_name*."""
        allowed_hooks = self._descriptor_hooks.get(descriptor.plugin_id, set())
        declared = (hook_type, hook_name) in allowed_hooks

        if declared:
            reason = ""
        else:
            reason = (
                f"Plugin '{descriptor.plugin_id}' did not declare "
                f"{hook_type} hook '{hook_name}' in its descriptor"
            )

        now = datetime.now(timezone.utc).isoformat()
        self._audit.append(PluginHookAuditRecord(
            timestamp=now,
            plugin_id=descriptor.plugin_id,
            hook_type=hook_type,
            hook_name=hook_name,
            allowed=declared,
            reason=reason,
        ))

        return PluginHookPermission(
            allowed=declared,
            plugin_id=descriptor.plugin_id,
            hook_type=hook_type,
            hook_name=hook_name,
            reason=reason,
        )

    @property
    def audit_records(self) -> list[PluginHookAuditRecord]:
        """Return all audit records (copy)."""
        return list(self._audit)

    def audit_records_as_dicts(self) -> list[dict[str, Any]]:
        """Return all audit records as plain dicts."""
        return [r.to_dict() for r in self._audit]
