from __future__ import annotations

from pathlib import Path

import pytest

from fireclaw_core.plugin.plugin_host import (
    FireClawPluginHost,
    PluginRegistrationError,
)
from fireclaw_core.agent.agent import FireClawAgent
from fireclaw_core.plugin.plugin_descriptor import FireClawPluginDescriptor
from fireclaw_core.plugin.extension_loader import load_fireclaw_extensions
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
        trust_level="trusted",
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
        trust_level="trusted",
    )

    def conflicting_registration(api) -> None:
        api.register_service("temporary-service", object())
        api.register_tool(_Tool("shared_tool"))

    with pytest.raises(PluginRegistrationError) as error:
        host.activate(
            "plugin.conflict",
            conflicting_registration,
            trust_level="trusted",
        )

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
        trust_level="trusted",
    )
    host.activate(
        "plugin.beta",
        lambda api: api.register_tool(_Tool("beta")),
        trust_level="trusted",
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
            trust_level="trusted",
        )

    assert host.get("tool", "future") is None
    assert host.record("plugin.future").diagnostics[0].code == (
        "unsupported_api_version"
    )


def test_explicit_extension_and_descriptor_registries_share_one_host() -> None:
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
    report = load_fireclaw_extensions(
        host,
        (Path(__file__).resolve().parents[1] / "extensions",),
        mode="simulation",
        role="robot_agent",
        services={"adapter": "dry-run"},
    )
    assert report.ok

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


def test_descriptor_only_plugin_cannot_register_executable_contributions() -> None:
    host = FireClawPluginHost()

    with pytest.raises(ValueError, match="register_data_service"):
        host.activate(
            "plugin.untrusted",
            lambda api: api.register_tool(_Tool("unsafe")),
            trust_level="descriptor_only",
        )

    assert host.get("tool", "unsafe") is None


def test_descriptor_data_registration_never_accepts_a_plugin_callback() -> None:
    host = FireClawPluginHost()
    descriptor = {"plugin_id": "plugin.data", "capabilities": ["inspect"]}

    record = host.register_data_service(
        plugin_id="plugin.data",
        service_id="plugin.data:descriptor",
        value=descriptor,
    )

    assert record.trust_level == "descriptor_only"
    assert (
        host.get("service", "plugin.data:descriptor").value
        is descriptor
    )
