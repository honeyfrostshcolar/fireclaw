from __future__ import annotations

from pathlib import Path

from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.execution.skills import Skill, create_default_skill_registry
from fireclaw_core.plugin.extension_loader import load_fireclaw_extensions
from fireclaw_core.plugin.plugin_host import FireClawPluginHost


OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
}
EXTENSIONS = Path(__file__).resolve().parents[1] / "extensions"


def _navigation_registry():
    host = FireClawPluginHost()
    load_fireclaw_extensions(
        host,
        (EXTENSIONS / "navigation-move-base",),
        mode="simulation",
        role="robot_agent",
        services={"adapter": "dry-run"},
        strict=True,
    )
    return create_default_skill_registry(
        DryRunRobotAdapter(robot_id="r1"),
        plugin_host=host,
    )


def test_skill_exposes_typed_execution_metadata() -> None:
    skill = Skill(
        name="test",
        description="test skill",
        handler=lambda inputs: None,
        output_schema=OUTPUT_SCHEMA,
        domain="perception",
        preconditions=["robot_online", "camera_available"],
        degraded_mode_policy="retry",
    )

    assert skill.output_schema == OUTPUT_SCHEMA
    assert skill.domain == "perception"
    assert skill.preconditions == ["robot_online", "camera_available"]
    assert skill.degraded_mode_policy == "retry"


def test_skill_metadata_defaults_are_generic() -> None:
    skill = Skill(name="t", description="d", handler=lambda inputs: None)

    assert skill.output_schema == {"type": "object", "additionalProperties": True}
    assert skill.preconditions == []
    assert skill.degraded_mode_policy is None


def test_manifest_loaded_navigation_has_typed_metadata() -> None:
    registry = _navigation_registry()
    navigation = registry.get("navigate_to_point")

    assert navigation is not None
    assert navigation.domain == "navigation"
    assert navigation.required_sensors == ["lidar"]
    assert navigation.preconditions == ["robot_online", "target_point_reachable"]
    assert navigation.degraded_mode_policy == "retry"
    assert navigation.metadata["plugin_id"] == "fireclaw.navigation.move-base"
    assert navigation.metadata["kind"] == "primitive"
    assert navigation.metadata["primitive_capability"] == "navigation"


def test_registry_metadata_contains_plugin_contract_fields() -> None:
    metadata = _navigation_registry().list_metadata()
    navigation = next(item for item in metadata if item["name"] == "navigate_to_point")

    assert navigation["domain"] == "navigation"
    assert navigation["input_schema"]["required"] == ["x", "y"]
    assert navigation["degraded_mode_policy"] == "retry"
