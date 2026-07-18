from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.agent.skill_inventory import build_robot_skill_inventory
from fireclaw_core.execution.skills import create_default_skill_registry


def test_build_robot_skill_inventory_exposes_profile_primitives():
    robot = DryRunRobotAdapter(robot_id="r1")
    registry = create_default_skill_registry(robot)

    inventory = build_robot_skill_inventory(
        registry=registry,
        primitive_skills=("navigate_to_floor", "report_status"),
        composite_chains={
            "search_for_victims": ("navigate_to_floor", "search_for_victims", "report_status"),
        },
        verified_sensors={"lidar"},
    )

    names = [item["name"] for item in inventory["skills"]]
    assert names == ["navigate_to_floor", "report_status", "search_for_victims"]

    navigate = next(item for item in inventory["skills"] if item["name"] == "navigate_to_floor")
    assert navigate["kind"] == "primitive"
    assert navigate["primitive_capability"] == "navigation"
    assert navigate["input_schema"]["required"] == ["floor"]


def test_build_robot_skill_inventory_marks_availability():
    robot = DryRunRobotAdapter(robot_id="r1")
    registry = create_default_skill_registry(robot)

    # navigate_to_floor requires lidar — not verified → not available
    inventory = build_robot_skill_inventory(
        registry=registry,
        primitive_skills=("navigate_to_floor", "report_status"),
        composite_chains={},
        verified_sensors=set(),
    )

    navigate = next(item for item in inventory["skills"] if item["name"] == "navigate_to_floor")
    assert navigate["available"] is False

    report = next(item for item in inventory["skills"] if item["name"] == "report_status")
    assert report["available"] is True  # no required_sensors in metadata


def test_build_robot_skill_inventory_skips_nonprimitive_in_primitive_list():
    robot = DryRunRobotAdapter(robot_id="r1")
    registry = create_default_skill_registry(robot)

    # search_for_victims has kind=composite — should be skipped in primitive pass
    inventory = build_robot_skill_inventory(
        registry=registry,
        primitive_skills=("search_for_victims", "report_status"),
        composite_chains={},
        verified_sensors=set(),
    )

    names = [item["name"] for item in inventory["skills"]]
    assert "search_for_victims" not in names
    assert "report_status" in names


def test_build_robot_skill_inventory_composite_chain_availability():
    robot = DryRunRobotAdapter(robot_id="r1")
    registry = create_default_skill_registry(robot)

    # All chain steps exist in registry → available
    inventory = build_robot_skill_inventory(
        registry=registry,
        primitive_skills=(),
        composite_chains={
            "rescue_mission": ("navigate_to_floor", "search_for_victims", "report_status"),
        },
        verified_sensors=set(),
    )

    rescue = inventory["skills"][0]
    assert rescue["name"] == "rescue_mission"
    assert rescue["kind"] == "composite"
    assert rescue["available"] is True


def test_build_robot_skill_inventory_empty_inputs():
    robot = DryRunRobotAdapter(robot_id="r1")
    registry = create_default_skill_registry(robot)

    inventory = build_robot_skill_inventory(
        registry=registry,
        primitive_skills=(),
        composite_chains={},
        verified_sensors=set(),
    )

    assert inventory == {"skills": []}
