"""Tests for plugin control-plane context and fingerprinting."""

from __future__ import annotations

import pytest

from fireclaw_core.plugin_control_plane import (
    PluginControlPlaneContext,
    PluginDiscoveryContext,
    fingerprint_plugin_control_plane_context,
    hash_json,
    resolve_plugin_control_plane_context,
)


class TestHashJson:
    def test_produces_deterministic_hash(self) -> None:
        data = {"b": 2, "a": 1}
        h1 = hash_json(data)
        h2 = hash_json(data)
        assert h1 == h2

    def test_order_independent(self) -> None:
        assert hash_json({"a": 1, "b": 2}) == hash_json({"b": 2, "a": 1})

    def test_different_data_different_hash(self) -> None:
        assert hash_json({"a": 1}) != hash_json({"a": 2})

    def test_returns_hex_string(self) -> None:
        h = hash_json({"x": 1})
        assert len(h) == 64  # SHA-256 hex
        int(h, 16)  # Should not raise


class TestPluginDiscoveryContext:
    def test_frozen_dataclass(self) -> None:
        ctx = PluginDiscoveryContext(roots=("/a",), load_paths=("/b",))
        with pytest.raises(AttributeError):
            ctx.roots = ("/c",)  # type: ignore[misc]

    def test_tuple_fields(self) -> None:
        ctx = PluginDiscoveryContext(roots=("/a", "/b"), load_paths=("/c",))
        assert ctx.roots == ("/a", "/b")
        assert ctx.load_paths == ("/c",)


class TestPluginControlPlaneContext:
    def test_frozen_dataclass(self) -> None:
        ctx = PluginControlPlaneContext(
            discovery=PluginDiscoveryContext(roots=(), load_paths=()),
            policy_fingerprint="abc123",
        )
        with pytest.raises(AttributeError):
            ctx.policy_fingerprint = "new"  # type: ignore[misc]

    def test_optional_fingerprints_default_none(self) -> None:
        ctx = PluginControlPlaneContext(
            discovery=PluginDiscoveryContext(roots=(), load_paths=()),
            policy_fingerprint="abc",
        )
        assert ctx.inventory_fingerprint is None
        assert ctx.activation_fingerprint is None

    def test_all_fingerprints_set(self) -> None:
        ctx = PluginControlPlaneContext(
            discovery=PluginDiscoveryContext(roots=("/r",), load_paths=("/lp",)),
            policy_fingerprint="pol",
            inventory_fingerprint="inv",
            activation_fingerprint="act",
        )
        assert ctx.policy_fingerprint == "pol"
        assert ctx.inventory_fingerprint == "inv"
        assert ctx.activation_fingerprint == "act"


class TestFingerprintPluginControlPlaneContext:
    def test_deterministic(self) -> None:
        disc = PluginDiscoveryContext(roots=("/a",), load_paths=("/b",))
        h1 = fingerprint_plugin_control_plane_context(disc, "p1")
        h2 = fingerprint_plugin_control_plane_context(disc, "p1")
        assert h1 == h2

    def test_changes_with_policy_fingerprint(self) -> None:
        disc = PluginDiscoveryContext(roots=("/a",), load_paths=("/b",))
        h1 = fingerprint_plugin_control_plane_context(disc, "p1")
        h2 = fingerprint_plugin_control_plane_context(disc, "p2")
        assert h1 != h2

    def test_changes_with_discovery(self) -> None:
        disc1 = PluginDiscoveryContext(roots=("/a",), load_paths=("/b",))
        disc2 = PluginDiscoveryContext(roots=("/c",), load_paths=("/b",))
        h1 = fingerprint_plugin_control_plane_context(disc1, "p1")
        h2 = fingerprint_plugin_control_plane_context(disc2, "p1")
        assert h1 != h2

    def test_changes_with_inventory_fingerprint(self) -> None:
        disc = PluginDiscoveryContext(roots=(), load_paths=())
        h1 = fingerprint_plugin_control_plane_context(disc, "p1", inventory_fingerprint="inv1")
        h2 = fingerprint_plugin_control_plane_context(disc, "p1", inventory_fingerprint="inv2")
        assert h1 != h2

    def test_changes_with_activation_fingerprint(self) -> None:
        disc = PluginDiscoveryContext(roots=(), load_paths=())
        h1 = fingerprint_plugin_control_plane_context(disc, "p1", activation_fingerprint="act1")
        h2 = fingerprint_plugin_control_plane_context(disc, "p1", activation_fingerprint="act2")
        assert h1 != h2


class TestResolvePluginControlPlaneContext:
    def test_constructs_context(self) -> None:
        disc = PluginDiscoveryContext(roots=("/r",), load_paths=("/lp",))
        ctx = resolve_plugin_control_plane_context(disc, "pol123")
        assert isinstance(ctx, PluginControlPlaneContext)
        assert ctx.policy_fingerprint == "pol123"
        assert ctx.discovery is disc

    def test_passes_optional_fingerprints(self) -> None:
        disc = PluginDiscoveryContext(roots=(), load_paths=())
        ctx = resolve_plugin_control_plane_context(
            disc, "pol", inventory_fingerprint="inv", activation_fingerprint="act",
        )
        assert ctx.inventory_fingerprint == "inv"
        assert ctx.activation_fingerprint == "act"

    def test_rejects_empty_policy_fingerprint(self) -> None:
        disc = PluginDiscoveryContext(roots=(), load_paths=())
        with pytest.raises(ValueError, match="policy_fingerprint"):
            resolve_plugin_control_plane_context(disc, "")

    def test_rejects_whitespace_policy_fingerprint(self) -> None:
        disc = PluginDiscoveryContext(roots=(), load_paths=())
        with pytest.raises(ValueError, match="policy_fingerprint"):
            resolve_plugin_control_plane_context(disc, "   ")
