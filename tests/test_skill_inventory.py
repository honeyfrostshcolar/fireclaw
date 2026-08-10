from pathlib import Path

from fireclaw_core.agent.agent import FireClawAgent
from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.agent.skill_inventory import build_robot_skill_inventory


EXTENSIONS = Path(__file__).resolve().parents[1] / "extensions"


def _navigation_registry():
    return FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="r1"),
        extension_paths=(EXTENSIONS,),
        plugin_services={"adapter": "dry-run"},
    ).registry


def test_inventory_exposes_profile_selected_plugin_primitive() -> None:
    inventory = build_robot_skill_inventory(
        registry=_navigation_registry(),
        primitive_skills=("navigate_to_point",),
        composite_chains={
            "navigation_acceptance": ("navigate_to_point",),
        },
        verified_sensors={"lidar"},
    )

    assert [item["name"] for item in inventory["skills"]] == [
        "navigate_to_point",
        "navigation_acceptance",
    ]
    navigation = inventory["skills"][0]
    assert navigation["kind"] == "primitive"
    assert navigation["primitive_capability"] == "navigation"
    assert navigation["input_schema"]["required"] == ["x", "y"]
    assert navigation["available"] is True


def test_inventory_marks_plugin_primitive_unavailable_without_sensor() -> None:
    inventory = build_robot_skill_inventory(
        registry=_navigation_registry(),
        primitive_skills=("navigate_to_point",),
        composite_chains={},
        verified_sensors=set(),
    )

    navigation = inventory["skills"][0]
    assert navigation["name"] == "navigate_to_point"
    assert navigation["required_sensors"] == ["lidar"]
    assert navigation["available"] is False


def test_inventory_skips_unknown_profile_primitive() -> None:
    inventory = build_robot_skill_inventory(
        registry=_navigation_registry(),
        primitive_skills=("uninstalled_plugin_tool", "navigate_to_point"),
        composite_chains={},
        verified_sensors={"lidar"},
    )

    assert [item["name"] for item in inventory["skills"]] == [
        "navigate_to_point"
    ]


def test_inventory_composite_availability_uses_registered_chain_steps() -> None:
    inventory = build_robot_skill_inventory(
        registry=_navigation_registry(),
        primitive_skills=(),
        composite_chains={
            "navigation_acceptance": ("navigate_to_point",),
            "unavailable_workflow": (
                "navigate_to_point",
                "uninstalled_plugin_tool",
            ),
        },
        verified_sensors=set(),
    )

    by_name = {item["name"]: item for item in inventory["skills"]}
    assert by_name["navigation_acceptance"]["available"] is True
    assert by_name["navigation_acceptance"]["input_schema"] == {
        "type": "object",
        "additionalProperties": True,
    }
    assert by_name["unavailable_workflow"]["available"] is False


def test_inventory_empty_inputs() -> None:
    inventory = build_robot_skill_inventory(
        registry=_navigation_registry(),
        primitive_skills=(),
        composite_chains={},
        verified_sensors=set(),
    )

    assert inventory == {"skills": []}
