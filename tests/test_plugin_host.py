from __future__ import annotations

import pytest

from fireclaw_core.plugin.plugin_host import (
    FireClawPluginHost,
    PluginRegistrationError,
)
from fireclaw_core.agent.agent import FireClawAgent
from fireclaw_core.plugin.plugin_descriptor import FireClawPluginDescriptor
from fireclaw_core.plugin.plugin_runtime import PluginRuntime


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


class _Harness:
    id = "test-harness"
    label = "Test Harness"


def test_plugin_host_commits_owned_contributions_atomically() -> None:
    host = FireClawPluginHost()

    host.activate(
        "plugin.alpha",
        lambda api: (
            api.register_tool(_Tool("inspect_state")),
            api.register_service("state-reader", object()),
            api.register_agent_harness(_Harness()),
        ),
        version="1.2.0",
        source="test",
    )

    assert host.get("tool", "inspect_state").owner_plugin_id == "plugin.alpha"
    assert host.get("service", "state-reader").owner_plugin_id == "plugin.alpha"
    assert host.get("agent_harness", "test-harness").owner_plugin_id == (
        "plugin.alpha"
    )
    record = host.record("plugin.alpha")
    assert record is not None
    assert record.status == "active"
    assert len(record.contribution_keys) == 3


def test_plugin_host_rolls_back_whole_activation_on_conflict() -> None:
    host = FireClawPluginHost()
    host.activate(
        "plugin.owner",
        lambda api: api.register_tool(_Tool("shared_tool")),
    )

    def conflicting_registration(api) -> None:
        api.register_service("temporary-service", object())
        api.register_tool(_Tool("shared_tool"))

    with pytest.raises(PluginRegistrationError) as error:
        host.activate("plugin.conflict", conflicting_registration)

    assert host.get("service", "temporary-service") is None
    assert host.get("tool", "shared_tool").owner_plugin_id == "plugin.owner"
    failed = host.record("plugin.conflict")
    assert failed is not None
    assert failed.status == "failed"
    assert failed.diagnostics[0].code == "contribution_conflict"
    assert error.value.diagnostic.contribution_id == "shared_tool"


def test_plugin_host_dispose_removes_only_owned_contributions() -> None:
    host = FireClawPluginHost()
    disposed: list[str] = []
    host.activate(
        "plugin.alpha",
        lambda api: (
            api.register_tool(_Tool("alpha")),
            api.register_dispose(lambda: disposed.append("alpha")),
        ),
    )
    host.activate(
        "plugin.beta",
        lambda api: api.register_tool(_Tool("beta")),
    )

    host.dispose_plugin("plugin.alpha")

    assert disposed == ["alpha"]
    assert host.get("tool", "alpha") is None
    assert host.get("tool", "beta") is not None
    assert host.record("plugin.alpha").status == "disposed"


def test_plugin_host_rejects_unsupported_api_without_partial_state() -> None:
    host = FireClawPluginHost()

    with pytest.raises(PluginRegistrationError):
        host.activate(
            "plugin.future",
            lambda api: api.register_tool(_Tool("future")),
            api_version="99",
        )

    assert host.get("tool", "future") is None
    assert host.record("plugin.future").diagnostics[0].code == (
        "unsupported_api_version"
    )


def test_legacy_registries_project_into_one_shared_host() -> None:
    host = FireClawPluginHost()
    runtime = PluginRuntime(host)
    runtime.register_descriptor(
        FireClawPluginDescriptor(
            plugin_id="fireclaw.test.context",
            capabilities=("context",),
            preconditions=("host_available",),
            risk_level="low",
            required_sensors=(),
            adapter_bindings=(),
            memory_hooks=("filter",),
        )
    )
    runtime.register_callable(
        hook_type="memory",
        hook_name="filter",
        plugin_id="fireclaw.test.context",
        callback=lambda payload: payload,
    )

    agent = FireClawAgent(plugin_host=host)

    assert agent.registry.host is host
    assert host.get("physical_capability", "navigate_to_point") is not None
    assert host.get("tool", "navigate_to_point") is not None
    assert host.get(
        "service",
        "fireclaw.test.context:descriptor",
    ) is not None
    hooks = host.contributions("hook")
    assert hooks[0].owner_plugin_id == "fireclaw.test.context"
