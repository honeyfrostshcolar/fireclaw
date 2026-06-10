"""Tests for plugin policy — permission enforcement and audit trail."""
from __future__ import annotations

from pathlib import Path

from fireclaw_core.plugin_descriptor import FireClawPluginDescriptor
from fireclaw_core.plugin_policy import PluginPolicy, PluginHookAuditRecord
from fireclaw_core.plugin_runtime import PluginRuntime


def _make_descriptor(plugin_id: str = "test.plugin", **kwargs) -> FireClawPluginDescriptor:
    defaults = dict(
        plugin_id=plugin_id,
        capabilities=("test_cap",),
        preconditions=("precond",),
        risk_level="low",
        required_sensors=(),
        adapter_bindings=(),
    )
    defaults.update(kwargs)
    return FireClawPluginDescriptor(**defaults)


class TestPluginPolicyEvaluateRegistration:
    def test_allows_registration_when_descriptor_declares_hook(self) -> None:
        d = _make_descriptor(provider_hooks=("enrich_context",))
        policy = PluginPolicy(descriptors=[d])

        result = policy.evaluate_registration(d, "provider", "enrich_context")

        assert result.allowed is True

    def test_rejects_registration_when_descriptor_does_not_declare_hook(self) -> None:
        d = _make_descriptor(provider_hooks=())  # no provider hooks declared
        policy = PluginPolicy(descriptors=[d])

        result = policy.evaluate_registration(d, "provider", "enrich_context")

        assert result.allowed is False
        assert "enrich_context" in result.reason

    def test_allows_when_descriptor_declares_memory_hook(self) -> None:
        d = _make_descriptor(memory_hooks=("rerank",))
        policy = PluginPolicy(descriptors=[d])

        result = policy.evaluate_registration(d, "memory", "rerank")

        assert result.allowed is True

    def test_rejects_when_descriptor_missing_memory_hook(self) -> None:
        d = _make_descriptor(memory_hooks=())
        policy = PluginPolicy(descriptors=[d])

        result = policy.evaluate_registration(d, "memory", "rerank")

        assert result.allowed is False


class TestPluginPolicyAuditTrail:
    def test_audit_records_created_on_hook_registration(self) -> None:
        runtime = PluginRuntime()
        d = _make_descriptor(memory_hooks=("filter",))
        runtime.register_descriptor(d)
        policy = PluginPolicy(descriptors=[d])
        runtime.plugin_policy = policy

        runtime.register_callable(
            hook_type="memory", hook_name="filter",
            plugin_id="test.plugin",
            callback=lambda p: {"memories": []},
        )

        records = policy.audit_records
        assert len(records) == 1
        last = records[-1]
        assert last.plugin_id == "test.plugin"
        assert last.hook_type == "memory"
        assert last.hook_name == "filter"
        assert last.allowed is True

    def test_audit_record_records_rejected_registration(self) -> None:
        d = _make_descriptor(provider_hooks=())
        policy = PluginPolicy(descriptors=[d])

        result = policy.evaluate_registration(d, "provider", "enrich_context")

        assert result.allowed is False
        records = policy.audit_records
        assert len(records) == 1
        assert records[0].allowed is False
        assert "enrich_context" in records[0].reason


class TestPluginPolicyWiring:
    def test_register_callable_rejects_undeclared_hook(self) -> None:
        """PluginRuntime.register_callable should reject hooks not declared in descriptor."""
        runtime = PluginRuntime()
        d = _make_descriptor(provider_hooks=())  # no enrich_context declared
        runtime.register_descriptor(d)
        policy = PluginPolicy(descriptors=[d])
        runtime.plugin_policy = policy

        import pytest
        with pytest.raises(ValueError, match="did not declare"):
            runtime.register_callable(
                hook_type="provider", hook_name="enrich_context",
                plugin_id="test.plugin",
                callback=lambda p: p,
            )

    def test_register_callable_allows_declared_hook(self) -> None:
        runtime = PluginRuntime()
        d = _make_descriptor(provider_hooks=("enrich_context",))
        runtime.register_descriptor(d)
        policy = PluginPolicy(descriptors=[d])
        runtime.plugin_policy = policy

        # Should not raise
        runtime.register_callable(
            hook_type="provider", hook_name="enrich_context",
            plugin_id="test.plugin",
            callback=lambda p: p,
        )
