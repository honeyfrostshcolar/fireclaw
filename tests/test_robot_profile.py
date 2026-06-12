from __future__ import annotations

from pathlib import Path

from fireclaw_core.agent.robot_profile import (
    RobotCapabilityProfile,
    load_robot_capability_profile,
)


def test_load_robot_capability_profile_from_toml(tmp_path: Path) -> None:
    profile_path = tmp_path / "debug-robot-1.toml"
    profile_path.write_text(
        """
[robot]
id = "debug-robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "examples/ros1_configs/gazebo_turtlebot3_move_base.yaml"
data_dir = "data/robots/debug-robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)

    assert profile == RobotCapabilityProfile(
        robot_id="debug-robot-1",
        base_url="http://127.0.0.1:8765",
        adapter="ros1",
        ros1_config="examples/ros1_configs/gazebo_turtlebot3_move_base.yaml",
        data_dir=Path("data/robots/debug-robot-1"),
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "search_for_victims", "report_status"),
        llm_exposed_skills=("navigate_to_floor", "search_for_victims", "report_status"),
    )


def test_profile_derives_gateway_paths_and_registry_entry(tmp_path: Path) -> None:
    profile_path = tmp_path / "debug-robot-1.toml"
    profile_path.write_text(
        """
[robot]
id = "debug-robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/debug-robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims"]
llm_exposed_skills = ["navigate_to_floor"]
""".strip(),
        encoding="utf-8",
    )
    profile = load_robot_capability_profile(profile_path)

    assert profile.memory_path == Path("data/robots/debug-robot-1/memory.jsonl")
    assert profile.event_path == Path("data/robots/debug-robot-1/events.jsonl")
    assert profile.task_queue_path == Path("data/robots/debug-robot-1/tasks.jsonl")
    assert profile.to_robot_registry_entry() == {
        "robot_id": "debug-robot-1",
        "base_url": "http://127.0.0.1:8765",
        "capabilities": ["search_for_victims"],
        "enabled": True,
    }


from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.execution.skills import create_default_skill_registry
from fireclaw_core.agent.robot_profile import validate_robot_capability_profile
from fireclaw_core.ros.ros1_config import load_ros1_adapter_config


def test_profile_validation_rejects_unknown_enabled_skill() -> None:
    profile = RobotCapabilityProfile(
        robot_id="debug-robot-1",
        base_url="http://127.0.0.1:8765",
        adapter="simulator",
        ros1_config=None,
        data_dir=Path("data/robots/debug-robot-1"),
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "unknown_skill"),
        llm_exposed_skills=("navigate_to_floor",),
    )
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="debug-robot-1"))

    errors = validate_robot_capability_profile(profile, registry)

    assert "enabled skill 'unknown_skill' is not registered" in errors


def test_profile_validation_rejects_ros1_skill_without_remap() -> None:
    profile = RobotCapabilityProfile(
        robot_id="debug-robot-1",
        base_url="http://127.0.0.1:8765",
        adapter="ros1",
        ros1_config="examples/ros1_configs/gazebo_turtlebot3_move_base.yaml",
        data_dir=Path("data/robots/debug-robot-1"),
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "assess_victim"),
        llm_exposed_skills=("navigate_to_floor", "assess_victim"),
    )
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="debug-robot-1"))
    ros1_config = load_ros1_adapter_config(profile.ros1_config)

    errors = validate_robot_capability_profile(profile, registry, ros1_config=ros1_config)

    assert "enabled skill 'assess_victim' has no ROS1 remap" in errors


def test_profile_validation_accepts_gazebo_profile() -> None:
    profile = RobotCapabilityProfile(
        robot_id="debug-robot-1",
        base_url="http://127.0.0.1:8765",
        adapter="ros1",
        ros1_config="examples/ros1_configs/gazebo_turtlebot3_move_base.yaml",
        data_dir=Path("data/robots/debug-robot-1"),
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "search_for_victims", "report_status", "return_to_safe_zone"),
        llm_exposed_skills=("navigate_to_floor", "search_for_victims", "report_status", "return_to_safe_zone"),
    )
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="debug-robot-1"))
    ros1_config = load_ros1_adapter_config(profile.ros1_config)

    errors = validate_robot_capability_profile(profile, registry, ros1_config=ros1_config)

    assert errors == []
