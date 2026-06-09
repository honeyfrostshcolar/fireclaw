"""Tests for PluginRuntime — plugin descriptor loading and hook aggregation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fireclaw_core.plugin_descriptor import FireClawPluginDescriptor
from fireclaw_core.plugin_runtime import PluginRuntime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_descriptor(**overrides):
    """Return a valid descriptor, optionally overriding specific fields."""
    defaults = dict(
        plugin_id="navigate_to_floor",
        capabilities=("navigate_to_floor", "navigation"),
        preconditions=("robot_online", "floor_reachable"),
        risk_level="low",
        required_sensors=(),
        adapter_bindings=("navigation",),
    )
    defaults.update(overrides)
    return FireClawPluginDescriptor(**defaults)


def _write_descriptor_json(directory: Path, filename: str, descriptor: FireClawPluginDescriptor) -> Path:
    """Serialize a descriptor to JSON and write it to *directory* / *filename*."""
    data = {
        "plugin_id": descriptor.plugin_id,
        "capabilities": list(descriptor.capabilities),
        "preconditions": list(descriptor.preconditions),
        "risk_level": descriptor.risk_level,
        "required_sensors": list(descriptor.required_sensors),
        "adapter_bindings": list(descriptor.adapter_bindings),
        "approval_scope": descriptor.approval_scope,
        "provider_hooks": list(descriptor.provider_hooks),
        "memory_hooks": list(descriptor.memory_hooks),
        "tool_approval_hooks": list(descriptor.tool_approval_hooks),
    }
    path = directory / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPluginRuntimeLoad:
    """Loading descriptors from a directory."""

    def test_plugin_runtime_loads_descriptors_from_directory(self, tmp_path: Path) -> None:
        d1 = _make_descriptor(plugin_id="alpha", provider_hooks=("enrich_context",))
        d2 = _make_descriptor(plugin_id="beta", memory_hooks=("rerank",))
        _write_descriptor_json(tmp_path, "alpha.json", d1)
        _write_descriptor_json(tmp_path, "beta.json", d2)

        runtime = PluginRuntime()
        count = runtime.load_from_directory(tmp_path)

        assert count == 2
        assert len(runtime.descriptors) == 2
        ids = {d.plugin_id for d in runtime.descriptors}
        assert ids == {"alpha", "beta"}

    def test_plugin_runtime_empty_directory(self, tmp_path: Path) -> None:
        runtime = PluginRuntime()
        count = runtime.load_from_directory(tmp_path)

        assert count == 0
        assert runtime.descriptors == []

    def test_plugin_runtime_skips_invalid_json_files(self, tmp_path: Path) -> None:
        valid = _make_descriptor(plugin_id="good")
        _write_descriptor_json(tmp_path, "good.json", valid)
        (tmp_path / "bad.json").write_text("NOT VALID JSON {{{", encoding="utf-8")

        runtime = PluginRuntime()
        count = runtime.load_from_directory(tmp_path)

        assert count == 1
        assert runtime.descriptors[0].plugin_id == "good"


class TestPluginRuntimeRegister:
    """Direct registration and hook name validation."""

    def test_plugin_runtime_register_descriptor_directly(self) -> None:
        d = _make_descriptor(
            plugin_id="manual",
            provider_hooks=("enrich_context",),
            memory_hooks=("rerank",),
        )
        runtime = PluginRuntime()
        runtime.register_descriptor(d)

        assert len(runtime.descriptors) == 1
        assert runtime.descriptors[0].plugin_id == "manual"

    def test_plugin_runtime_rejects_unknown_provider_hook(self) -> None:
        d = _make_descriptor(plugin_id="bad", provider_hooks=("nonexistent_hook",))
        runtime = PluginRuntime()

        with pytest.raises(ValueError, match="nonexistent_hook"):
            runtime.register_descriptor(d)

    def test_plugin_runtime_rejects_unknown_memory_hook(self) -> None:
        d = _make_descriptor(plugin_id="bad", memory_hooks=("bogus_rerank",))
        runtime = PluginRuntime()

        with pytest.raises(ValueError, match="bogus_rerank"):
            runtime.register_descriptor(d)

    def test_plugin_runtime_rejects_unknown_tool_approval_hook(self) -> None:
        d = _make_descriptor(plugin_id="bad", tool_approval_hooks=("unknown_approval",))
        runtime = PluginRuntime()

        with pytest.raises(ValueError, match="unknown_approval"):
            runtime.register_descriptor(d)

    def test_plugin_runtime_descriptors_returns_immutable_copy(self) -> None:
        d = _make_descriptor(plugin_id="x")
        runtime = PluginRuntime()
        runtime.register_descriptor(d)

        copy = runtime.descriptors
        copy.clear()

        assert len(runtime.descriptors) == 1


class TestPluginRuntimeHookAggregation:
    """Hook aggregation across multiple descriptors."""

    def test_plugin_runtime_provider_hooks_aggregated(self) -> None:
        d1 = _make_descriptor(plugin_id="a", provider_hooks=("enrich_context",))
        d2 = _make_descriptor(plugin_id="b", provider_hooks=("suggest_model", "validate_output"))
        d3 = _make_descriptor(plugin_id="c", provider_hooks=("enrich_context",))  # duplicate

        runtime = PluginRuntime()
        for d in (d1, d2, d3):
            runtime.register_descriptor(d)

        hooks = runtime.provider_hooks()
        assert "enrich_context" in hooks
        assert "suggest_model" in hooks
        assert "validate_output" in hooks

    def test_plugin_runtime_memory_hooks_aggregated(self) -> None:
        d1 = _make_descriptor(plugin_id="a", memory_hooks=("rerank",))
        d2 = _make_descriptor(plugin_id="b", memory_hooks=("filter", "summarize"))

        runtime = PluginRuntime()
        runtime.register_descriptor(d1)
        runtime.register_descriptor(d2)

        hooks = runtime.memory_hooks()
        assert "rerank" in hooks
        assert "filter" in hooks
        assert "summarize" in hooks

    def test_plugin_runtime_tool_approval_hooks_aggregated(self) -> None:
        d1 = _make_descriptor(plugin_id="a", tool_approval_hooks=("add_reason",))
        d2 = _make_descriptor(plugin_id="b", tool_approval_hooks=("require_scope", "auto_approve"))

        runtime = PluginRuntime()
        runtime.register_descriptor(d1)
        runtime.register_descriptor(d2)

        hooks = runtime.tool_approval_hooks()
        assert "add_reason" in hooks
        assert "require_scope" in hooks
        assert "auto_approve" in hooks
