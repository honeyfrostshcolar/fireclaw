"""Tests for FireClawPluginDescriptor and its validation."""
import pytest

from fireclaw_core.plugin_descriptor import FireClawPluginDescriptor


# ---------------------------------------------------------------------------
# Construction helpers
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


# ---------------------------------------------------------------------------
# Happy-path construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_minimal_valid_descriptor(self):
        desc = _make_descriptor()
        assert desc.plugin_id == "navigate_to_floor"
        assert desc.capabilities == ("navigate_to_floor", "navigation")
        assert desc.preconditions == ("robot_online", "floor_reachable")
        assert desc.risk_level == "low"
        assert desc.required_sensors == ()
        assert desc.adapter_bindings == ("navigation",)
        assert desc.approval_scope is None
        assert desc.provider_hooks == ()
        assert desc.memory_hooks == ()

    def test_descriptor_with_all_optional_fields(self):
        desc = _make_descriptor(
            approval_scope="operator_confirm",
            provider_hooks=("openai_chat",),
            memory_hooks=("episodic_write",),
        )
        assert desc.approval_scope == "operator_confirm"
        assert desc.provider_hooks == ("openai_chat",)
        assert desc.memory_hooks == ("episodic_write",)

    def test_descriptor_is_frozen(self):
        desc = _make_descriptor()
        with pytest.raises(AttributeError):
            desc.plugin_id = "changed"  # type: ignore[misc]

    def test_all_risk_levels_accepted(self):
        for level in ("low", "medium", "high", "critical"):
            desc = _make_descriptor(risk_level=level)
            assert desc.risk_level == level


# ---------------------------------------------------------------------------
# Validation: plugin_id
# ---------------------------------------------------------------------------


class TestPluginIdValidation:
    def test_rejects_empty_plugin_id(self):
        with pytest.raises(ValueError, match="plugin_id"):
            _make_descriptor(plugin_id="")

    def test_rejects_whitespace_only_plugin_id(self):
        with pytest.raises(ValueError, match="plugin_id"):
            _make_descriptor(plugin_id="   ")

    def test_rejects_non_string_plugin_id(self):
        with pytest.raises(ValueError, match="plugin_id"):
            _make_descriptor(plugin_id=123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Validation: capabilities
# ---------------------------------------------------------------------------


class TestCapabilitiesValidation:
    def test_rejects_empty_capabilities(self):
        with pytest.raises(ValueError, match="capabilities"):
            _make_descriptor(capabilities=())

    def test_rejects_empty_string_in_capabilities(self):
        with pytest.raises(ValueError, match="capabilities"):
            _make_descriptor(capabilities=("valid", ""))

    def test_rejects_non_tuple_capabilities(self):
        with pytest.raises(ValueError, match="capabilities"):
            _make_descriptor(capabilities=["list", "not", "tuple"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Validation: preconditions
# ---------------------------------------------------------------------------


class TestPreconditionsValidation:
    def test_rejects_empty_preconditions(self):
        with pytest.raises(ValueError, match="preconditions"):
            _make_descriptor(preconditions=())

    def test_rejects_empty_string_in_preconditions(self):
        with pytest.raises(ValueError, match="preconditions"):
            _make_descriptor(preconditions=("robot_online", ""))


# ---------------------------------------------------------------------------
# Validation: risk_level
# ---------------------------------------------------------------------------


class TestRiskLevelValidation:
    def test_rejects_unknown_risk_level(self):
        with pytest.raises(ValueError, match="risk_level"):
            _make_descriptor(risk_level="extreme")

    def test_rejects_empty_risk_level(self):
        with pytest.raises(ValueError, match="risk_level"):
            _make_descriptor(risk_level="")

    def test_rejects_non_string_risk_level(self):
        with pytest.raises(ValueError, match="risk_level"):
            _make_descriptor(risk_level=42)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Validation: required_sensors (may be empty, but must be a tuple)
# ---------------------------------------------------------------------------


class TestRequiredSensorsValidation:
    def test_empty_required_sensors_is_valid(self):
        desc = _make_descriptor(required_sensors=())
        assert desc.required_sensors == ()

    def test_rejects_empty_string_in_required_sensors(self):
        with pytest.raises(ValueError, match="required_sensors"):
            _make_descriptor(required_sensors=("rgb_camera", ""))

    def test_rejects_non_tuple_required_sensors(self):
        with pytest.raises(ValueError, match="required_sensors"):
            _make_descriptor(required_sensors=["rgb_camera"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Validation: adapter_bindings (may be empty, must be a tuple)
# ---------------------------------------------------------------------------


class TestAdapterBindingsValidation:
    def test_empty_adapter_bindings_is_valid(self):
        desc = _make_descriptor(adapter_bindings=())
        assert desc.adapter_bindings == ()

    def test_rejects_empty_string_in_adapter_bindings(self):
        with pytest.raises(ValueError, match="adapter_bindings"):
            _make_descriptor(adapter_bindings=("navigation", ""))

    def test_rejects_non_tuple_adapter_bindings(self):
        with pytest.raises(ValueError, match="adapter_bindings"):
            _make_descriptor(adapter_bindings=["navigation"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Validation: approval_scope (optional)
# ---------------------------------------------------------------------------


class TestApprovalScopeValidation:
    def test_none_approval_scope_is_valid(self):
        desc = _make_descriptor(approval_scope=None)
        assert desc.approval_scope is None

    def test_non_empty_approval_scope_is_valid(self):
        desc = _make_descriptor(approval_scope="operator_confirm")
        assert desc.approval_scope == "operator_confirm"

    def test_rejects_empty_string_approval_scope(self):
        with pytest.raises(ValueError, match="approval_scope"):
            _make_descriptor(approval_scope="")

    def test_rejects_whitespace_only_approval_scope(self):
        with pytest.raises(ValueError, match="approval_scope"):
            _make_descriptor(approval_scope="   ")


# ---------------------------------------------------------------------------
# Validation: provider_hooks
# ---------------------------------------------------------------------------


class TestProviderHooksValidation:
    def test_empty_provider_hooks_is_valid(self):
        desc = _make_descriptor(provider_hooks=())
        assert desc.provider_hooks == ()

    def test_rejects_empty_string_in_provider_hooks(self):
        with pytest.raises(ValueError, match="provider_hooks"):
            _make_descriptor(provider_hooks=("openai_chat", ""))


# ---------------------------------------------------------------------------
# Validation: memory_hooks
# ---------------------------------------------------------------------------


class TestMemoryHooksValidation:
    def test_empty_memory_hooks_is_valid(self):
        desc = _make_descriptor(memory_hooks=())
        assert desc.memory_hooks == ()

    def test_rejects_empty_string_in_memory_hooks(self):
        with pytest.raises(ValueError, match="memory_hooks"):
            _make_descriptor(memory_hooks=("episodic_write", ""))
