import json
import sys

import pytest

from fireclaw_core.infra.skill_manifest import load_subprocess_skill_from_manifest


def test_load_subprocess_skill_from_manifest(tmp_path, legacy_skill_executor):
    manifest_path = tmp_path / "rl_navigation.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "rl_navigation",
                "description": "Runs an isolated RL navigation policy.",
                "runtime": "subprocess",
                "command": [
                    sys.executable,
                    "-c",
                    "import json; print(json.dumps({'ok': True, 'data': {'action': 'move'}}))",
                ],
                "timeout_seconds": 2.0,
                "dry_run_only": True,
            }
        ),
        encoding="utf-8",
    )

    skill = load_subprocess_skill_from_manifest(
        manifest_path,
        sandbox_executor=legacy_skill_executor,
    )

    assert skill.name == "rl_navigation"
    assert skill.runtime == "sandboxed_subprocess"
    assert skill.dry_run_only is True
    assert skill.run({}).data == {"action": "move"}


def test_load_subprocess_skill_manifest_with_execution_and_safety_metadata(
    tmp_path,
    legacy_skill_executor,
):
    manifest_path = tmp_path / "rl_navigation.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "rl_navigation",
                "description": "Runs an isolated RL navigation policy.",
                "runtime": "subprocess",
                "command": [
                    sys.executable,
                    "-c",
                    "import json; print(json.dumps({'ok': True, 'data': {}}))",
                ],
                "timeout_seconds": 2.5,
                "dry_run_only": True,
                "max_attempts": 2,
                "idempotent": True,
                "required_sensors": ["rgb_camera", "thermal_camera"],
                "failure_categories": ["timeout", "perception_uncertain"],
                "allow_real_robot": False,
            }
        ),
        encoding="utf-8",
    )

    skill = load_subprocess_skill_from_manifest(
        manifest_path,
        sandbox_executor=legacy_skill_executor,
    )

    assert skill.max_attempts == 2
    assert skill.idempotent is True
    assert skill.required_sensors == ["rgb_camera", "thermal_camera"]
    assert skill.failure_categories == ["timeout", "perception_uncertain"]
    assert skill.allow_real_robot is False
    assert skill.timeout_seconds == 2.5


def test_load_subprocess_skill_manifest_with_input_schema(
    tmp_path,
    legacy_skill_executor,
):
    manifest_path = tmp_path / "echo.skill.json"
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    manifest_path.write_text(
        json.dumps(
            {
                "name": "echo",
                "description": "Echo text.",
                "runtime": "subprocess",
                "command": [
                    sys.executable,
                    "-c",
                    "import json; print(json.dumps({'ok': True, 'data': {}}))",
                ],
                "input_schema": input_schema,
            }
        ),
        encoding="utf-8",
    )

    skill = load_subprocess_skill_from_manifest(
        manifest_path,
        sandbox_executor=legacy_skill_executor,
    )

    assert skill.input_schema == input_schema


def test_load_subprocess_skill_manifest_with_risk_level(
    tmp_path,
    legacy_skill_executor,
):
    manifest_path = tmp_path / "entry.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "smoke_entry",
                "description": "High-risk smoke entry.",
                "runtime": "subprocess",
                "command": [
                    sys.executable,
                    "-c",
                    "import json; print(json.dumps({'ok': True, 'data': {}}))",
                ],
                "risk_level": "high",
            }
        ),
        encoding="utf-8",
    )

    skill = load_subprocess_skill_from_manifest(
        manifest_path,
        sandbox_executor=legacy_skill_executor,
    )

    assert skill.risk_level == "high"


def test_manifest_rejects_non_subprocess_runtime(tmp_path):
    manifest_path = tmp_path / "bad.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "description": "Bad runtime.",
                "runtime": "external_conda",
                "command": [sys.executable],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="runtime"):
        load_subprocess_skill_from_manifest(manifest_path)


def test_manifest_rejects_shell_string_command(tmp_path):
    manifest_path = tmp_path / "bad.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "description": "Bad command.",
                "runtime": "subprocess",
                "command": "python script.py",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="command"):
        load_subprocess_skill_from_manifest(manifest_path)


def test_manifest_rejects_retry_without_idempotency(tmp_path):
    manifest_path = tmp_path / "bad.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "description": "Retry is unsafe without idempotency.",
                "runtime": "subprocess",
                "command": [sys.executable],
                "max_attempts": 2,
                "idempotent": False,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="idempotent"):
        load_subprocess_skill_from_manifest(manifest_path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("dry_run_only", False, "dry_run_only=true"),
        ("allow_real_robot", True, "allow_real_robot=true"),
    ],
)
def test_manifest_cannot_claim_real_robot_execution(
    tmp_path,
    field,
    value,
    message,
):
    manifest_path = tmp_path / "unsafe.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "unsafe",
                "description": "Unsafe legacy process.",
                "runtime": "subprocess",
                "command": ["python3", "-c", "print('x')"],
                field: value,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_subprocess_skill_from_manifest(manifest_path, inspect_only=True)


def test_manifest_rejects_malformed_required_sensors(tmp_path):
    manifest_path = tmp_path / "bad.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "description": "Bad sensors.",
                "runtime": "subprocess",
                "command": [sys.executable],
                "required_sensors": ["rgb_camera", ""],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="required_sensors"):
        load_subprocess_skill_from_manifest(manifest_path)


def test_manifest_rejects_real_robot_allowance_when_dry_run_only(tmp_path):
    manifest_path = tmp_path / "bad.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "description": "Contradictory robot execution flags.",
                "runtime": "subprocess",
                "command": [sys.executable],
                "dry_run_only": True,
                "allow_real_robot": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="allow_real_robot"):
        load_subprocess_skill_from_manifest(manifest_path)


def test_manifest_rejects_malformed_input_schema(tmp_path):
    manifest_path = tmp_path / "bad.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "description": "Bad input schema.",
                "runtime": "subprocess",
                "command": [sys.executable],
                "input_schema": {"type": "array"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="input_schema"):
        load_subprocess_skill_from_manifest(manifest_path)


def test_manifest_rejects_invalid_risk_level(tmp_path):
    manifest_path = tmp_path / "bad.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "description": "Bad risk level.",
                "runtime": "subprocess",
                "command": [sys.executable],
                "risk_level": "extreme",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="risk_level"):
        load_subprocess_skill_from_manifest(manifest_path)


# ---------------------------------------------------------------------------
# descriptor_from_skill_manifest conversion tests
# ---------------------------------------------------------------------------

from fireclaw_core.plugin.plugin_descriptor import (
    FireClawPluginDescriptor,
    descriptor_from_skill_manifest,
)
from fireclaw_core.execution.skills import Skill


def _make_skill(**overrides):
    """Return a minimal valid Skill, optionally overriding fields."""
    defaults = dict(
        name="navigate_to_floor",
        description="Navigate robot to a target floor.",
        handler=lambda inputs: None,
        domain="navigation",
        preconditions=["robot_online", "floor_reachable"],
        required_sensors=[],
        risk_level="low",
    )
    defaults.update(overrides)
    return Skill(**defaults)


class TestDescriptorFromSkillManifest:
    def test_basic_conversion(self):
        skill = _make_skill()
        desc = descriptor_from_skill_manifest(skill)

        assert isinstance(desc, FireClawPluginDescriptor)
        assert desc.plugin_id == "navigate_to_floor"
        assert "navigate_to_floor" in desc.capabilities
        assert "navigation" in desc.capabilities
        assert desc.risk_level == "low"

    def test_capabilities_include_name_and_domain(self):
        skill = _make_skill(name="search_for_victims", domain="perception")
        desc = descriptor_from_skill_manifest(skill)

        assert "search_for_victims" in desc.capabilities
        assert "perception" in desc.capabilities

    def test_preconditions_from_skill(self):
        skill = _make_skill(preconditions=["robot_online", "camera_available"])
        desc = descriptor_from_skill_manifest(skill)

        assert desc.preconditions == ("robot_online", "camera_available")

    def test_fallback_precondition_when_empty(self):
        skill = _make_skill(preconditions=[])
        desc = descriptor_from_skill_manifest(skill)

        assert desc.preconditions == ("skill_available",)

    def test_required_sensors_forwarded(self):
        skill = _make_skill(required_sensors=["rgb_camera", "thermal_camera"])
        desc = descriptor_from_skill_manifest(skill)

        assert desc.required_sensors == ("rgb_camera", "thermal_camera")

    def test_adapter_bindings_from_sensors(self):
        skill = _make_skill(required_sensors=["rgb_camera", "thermal_camera"])
        desc = descriptor_from_skill_manifest(skill)

        # Sensors are sorted and deduplicated
        assert "rgb_camera" in desc.adapter_bindings
        assert "thermal_camera" in desc.adapter_bindings

    def test_adapter_bindings_from_domain_when_no_sensors(self):
        skill = _make_skill(required_sensors=[], domain="safety")
        desc = descriptor_from_skill_manifest(skill)

        assert desc.adapter_bindings == ("safety",)

    def test_approval_scope_maps_risk_low(self):
        skill = _make_skill(risk_level="low")
        desc = descriptor_from_skill_manifest(skill)

        assert desc.approval_scope is None

    def test_approval_scope_maps_risk_medium(self):
        skill = _make_skill(risk_level="medium")
        desc = descriptor_from_skill_manifest(skill)

        assert desc.approval_scope == "operator_confirm"

    def test_approval_scope_maps_risk_high(self):
        skill = _make_skill(risk_level="high")
        desc = descriptor_from_skill_manifest(skill)

        assert desc.approval_scope == "safety_officer"

    def test_approval_scope_maps_risk_critical(self):
        skill = _make_skill(risk_level="critical")
        desc = descriptor_from_skill_manifest(skill)

        assert desc.approval_scope == "emergency_override"

    def test_provider_hooks_default_empty(self):
        skill = _make_skill()
        desc = descriptor_from_skill_manifest(skill)

        assert desc.provider_hooks == ()

    def test_memory_hooks_default_empty(self):
        skill = _make_skill()
        desc = descriptor_from_skill_manifest(skill)

        assert desc.memory_hooks == ()

    def test_converted_descriptor_is_frozen(self):
        skill = _make_skill()
        desc = descriptor_from_skill_manifest(skill)

        with pytest.raises(AttributeError):
            desc.plugin_id = "changed"  # type: ignore[misc]

    def test_conversion_with_real_skill_registry_entries(self):
        """Smoke-test conversion against every default skill in the registry."""
        from unittest.mock import MagicMock

        from fireclaw_core.execution.skills import create_default_skill_registry

        robot = MagicMock()
        robot.dry_run = True
        registry = create_default_skill_registry(robot)

        for skill_name in registry.names():
            skill = registry.get(skill_name)
            assert skill is not None
            desc = descriptor_from_skill_manifest(skill)
            assert desc.plugin_id == skill_name
            assert desc.risk_level == skill.risk_level
