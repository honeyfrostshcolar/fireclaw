from __future__ import annotations

from pathlib import Path

from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.agent.robot_profile import (
    RobotCapabilityProfile,
    load_robot_capability_profile,
    load_robot_capability_profiles,
    validate_robot_capability_profile,
)
from fireclaw_core.execution.skills import create_default_skill_registry
from fireclaw_core.ros.ros1_config import load_ros1_adapter_config
from fireclaw_core.sensors.discovery import DiscoveryFingerprint


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


def test_profile_validation_rejects_llm_exposed_skill_not_in_enabled_skills() -> None:
    profile = RobotCapabilityProfile(
        robot_id="debug-robot-1",
        base_url="http://127.0.0.1:8765",
        adapter="simulator",
        ros1_config=None,
        data_dir=Path("data/robots/debug-robot-1"),
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "search_for_victims"),
        llm_exposed_skills=("navigate_to_floor", "assess_victim"),
    )
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="debug-robot-1"))

    errors = validate_robot_capability_profile(profile, registry)

    assert "LLM-exposed skill 'assess_victim' is not in enabled_skills" in errors


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
        enabled_skills=("navigate_to_point", "search_for_victims", "report_status", "return_to_safe_zone"),
        llm_exposed_skills=("navigate_to_point", "search_for_victims", "report_status", "return_to_safe_zone"),
    )
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="debug-robot-1"))
    ros1_config = load_ros1_adapter_config(profile.ros1_config)

    errors = validate_robot_capability_profile(profile, registry, ros1_config=ros1_config)

    assert errors == []


def test_load_robot_capability_profile_with_skill_chains(tmp_path: Path) -> None:
    profile_path = tmp_path / "debug-robot-1.toml"
    profile_path.write_text(
        """
[robot]
id = "debug-robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/debug-robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]

[capability_skill_chains]
search_for_victims = ["navigate_to_floor", "search_for_victims", "report_status"]
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)

    assert profile.capability_skill_chains == {
        "search_for_victims": ("navigate_to_floor", "search_for_victims", "report_status"),
    }


def test_profile_validation_rejects_skill_chain_outside_enabled_skills() -> None:
    profile = RobotCapabilityProfile(
        robot_id="debug-robot-1",
        base_url="http://127.0.0.1:8765",
        adapter="simulator",
        ros1_config=None,
        data_dir=Path("data/robots/debug-robot-1"),
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "search_for_victims"),
        llm_exposed_skills=("navigate_to_floor", "search_for_victims"),
        capability_skill_chains={
            "search_for_victims": ("navigate_to_floor", "disabled_skill"),
        },
    )
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="debug-robot-1"))

    errors = validate_robot_capability_profile(profile, registry)

    assert "skill chain 'search_for_victims' references non-enabled skill 'disabled_skill'" in errors


def test_profile_validation_rejects_skill_chain_capability_not_in_capabilities() -> None:
    profile = RobotCapabilityProfile(
        robot_id="debug-robot-1",
        base_url="http://127.0.0.1:8765",
        adapter="simulator",
        ros1_config=None,
        data_dir=Path("data/robots/debug-robot-1"),
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "search_for_victims"),
        llm_exposed_skills=("navigate_to_floor", "search_for_victims"),
        capability_skill_chains={
            "unknown_capability": ("navigate_to_floor",),
        },
    )
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="debug-robot-1"))

    errors = validate_robot_capability_profile(profile, registry)

    assert "skill chain key 'unknown_capability' is not in capabilities" in errors


def test_example_gazebo_turtlebot3_profile_loads_and_validates() -> None:
    profile = load_robot_capability_profile("examples/robot_profiles/gazebo_turtlebot3.toml")
    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id=profile.robot_id))
    ros1_config = load_ros1_adapter_config(profile.ros1_config)

    assert profile.robot_id == "gazebo_turtlebot3"
    assert profile.adapter == "ros1"
    assert "navigate_to_point" in profile.llm_exposed_skills
    assert validate_robot_capability_profile(profile, registry, ros1_config=ros1_config) == []


def test_robot_profile_loads_sensor_discovery_rules(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "data/robots/robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]

[robot.sensor_discovery]
enabled = true
message_timeout_seconds = 1.5

[[robot.sensor_discovery.rules]]
topic_pattern = "/front_camera/image_raw"
message_type = "sensor_msgs/Image"
sensor = "rgb_camera"
confidence = 0.95
confirmed = true
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)

    assert profile.sensor_discovery.enabled is True
    assert profile.sensor_discovery.message_timeout_seconds == 1.5
    assert profile.sensor_discovery.rules[0].topic_pattern == "/front_camera/image_raw"
    assert profile.sensor_discovery.rules[0].sensor == "rgb_camera"
    assert profile.sensor_discovery.rules[0].confirmed is True


def test_robot_profile_sensor_discovery_defaults(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor"]
llm_exposed_skills = ["navigate_to_floor"]
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)

    assert profile.sensor_discovery.enabled is True
    assert profile.sensor_discovery.message_timeout_seconds == 2.0
    assert profile.sensor_discovery.rules == ()


def test_robot_profile_sensor_discovery_rejects_missing_topic_pattern(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor"]
llm_exposed_skills = ["navigate_to_floor"]

[[robot.sensor_discovery.rules]]
message_type = "sensor_msgs/Image"
sensor = "rgb_camera"
""".strip(),
        encoding="utf-8",
    )

    import pytest

    with pytest.raises(ValueError, match="topic_pattern"):
        load_robot_capability_profile(profile_path)


def test_load_robot_capability_profiles_preserves_order(tmp_path: Path) -> None:
    template = """
[robot]
id = "{robot_id}"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/{robot_id}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor"]
llm_exposed_skills = ["navigate_to_floor"]
""".strip()

    first = tmp_path / "r1.toml"
    second = tmp_path / "r2.toml"
    first.write_text(template.format(robot_id="r1"), encoding="utf-8")
    second.write_text(template.format(robot_id="r2"), encoding="utf-8")

    profiles = load_robot_capability_profiles([first, second])

    assert [profile.robot_id for profile in profiles] == ["r1", "r2"]


def test_load_robot_profile_with_discovery_fingerprint(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "data/robots/robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims"]

[robot.discovery_fingerprint]
source = "ros1"
topics_hash = "sha256:abc"
nodes_hash = "sha256:nodes"
created_at = "2026-06-14T12:00:00+08:00"
confirmed_by = "operator"
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)

    assert profile.discovery_fingerprint == DiscoveryFingerprint(
        source="ros1",
        topics_hash="sha256:abc",
        nodes_hash="sha256:nodes",
        created_at="2026-06-14T12:00:00+08:00",
        confirmed_by="operator",
    )


def test_load_robot_profile_without_discovery_fingerprint_is_backward_compatible(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims"]
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)

    assert profile.discovery_fingerprint is None


def test_load_robot_profile_rejects_empty_discovery_fingerprint_source(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "data/robots/robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims"]

[robot.discovery_fingerprint]
source = ""
topics_hash = "sha256:abc"
""".strip(),
        encoding="utf-8",
    )

    import pytest

    with pytest.raises(ValueError, match="robot.discovery_fingerprint.source must be a non-empty string"):
        load_robot_capability_profile(profile_path)


def test_robot_profile_loads_sensor_rule_confirmation_audit_metadata(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "data/robots/robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims"]

[[robot.sensor_discovery.rules]]
topic_pattern = "/camera/image_raw"
message_type = "sensor_msgs/Image"
sensor = "rgb_camera"
confidence = 0.95
confirmed = true
confirmed_by = "operator-1"
confirmed_at = "2026-06-15T12:00:00+08:00"
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)
    rule = profile.sensor_discovery.rules[0]

    assert rule.confirmed is True
    assert rule.confirmed_by == "operator-1"
    assert rule.confirmed_at == "2026-06-15T12:00:00+08:00"


def test_robot_profile_parses_primitive_skills(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "r1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
data_dir = "data/robots/r1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
primitive_skills = ["navigate_to_floor", "report_status"]

[capability_skill_chains]
search_for_victims = ["navigate_to_floor", "search_for_victims", "report_status"]
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)

    assert profile.primitive_skills == ("navigate_to_floor", "report_status")
    assert "search_for_victims" in profile.capabilities


def test_robot_profile_primitive_skills_defaults_to_empty_tuple(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "r1"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/r1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor"]
llm_exposed_skills = ["navigate_to_floor"]
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)

    assert profile.primitive_skills == ()


def test_load_robot_profile_with_discovery_fingerprint_confirmation_time(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "data/robots/robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims"]

[robot.discovery_fingerprint]
source = "ros1"
topics_hash = "sha256:abc"
confirmed_by = "operator-1"
confirmed_at = "2026-06-15T12:00:00+08:00"
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)

    assert profile.discovery_fingerprint is not None
    assert profile.discovery_fingerprint.confirmed_by == "operator-1"
    assert profile.discovery_fingerprint.confirmed_at == "2026-06-15T12:00:00+08:00"


def test_profile_rejects_unknown_primitive_skill():
    from dataclasses import replace
    from fireclaw_core.agent.robot_profile import RobotCapabilityProfile, validate_robot_capability_profile
    from fireclaw_core.execution.skills import create_default_skill_registry
    from fireclaw_core.agent.robot import DryRunRobotAdapter

    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="test-robot"))
    profile = _make_valid_profile()
    profile = replace(profile, primitive_skills=("missing_skill",))

    errors = validate_robot_capability_profile(profile, registry)

    assert any("primitive skill 'missing_skill' is not registered" in error for error in errors)


def test_profile_rejects_composite_skill_in_primitive_skills():
    from dataclasses import replace
    from fireclaw_core.agent.robot_profile import RobotCapabilityProfile, validate_robot_capability_profile
    from fireclaw_core.execution.skills import create_default_skill_registry
    from fireclaw_core.agent.robot import DryRunRobotAdapter

    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="test-robot"))
    profile = _make_valid_profile()
    profile = replace(
        profile,
        primitive_skills=("search_for_victims",),
        enabled_skills=tuple(dict.fromkeys([*profile.enabled_skills, "search_for_victims"])),
    )

    errors = validate_robot_capability_profile(profile, registry)

    assert any("not marked as primitive" in error for error in errors)


def test_profile_rejects_primitive_skill_not_enabled():
    from dataclasses import replace
    from fireclaw_core.agent.robot_profile import RobotCapabilityProfile, validate_robot_capability_profile
    from fireclaw_core.execution.skills import create_default_skill_registry
    from fireclaw_core.agent.robot import DryRunRobotAdapter

    registry = create_default_skill_registry(DryRunRobotAdapter(robot_id="test-robot"))
    profile = _make_valid_profile()
    profile = replace(
        profile,
        primitive_skills=("report_status",),
        enabled_skills=tuple(skill for skill in profile.enabled_skills if skill != "report_status"),
    )

    errors = validate_robot_capability_profile(profile, registry)

    assert any("primitive skill 'report_status' is not in enabled_skills" in error for error in errors)


def _make_valid_profile():
    from pathlib import Path
    from fireclaw_core.agent.robot_profile import RobotCapabilityProfile
    return RobotCapabilityProfile(
        robot_id="test-robot",
        base_url="http://localhost:8765",
        adapter="dry_run",
        ros1_config=None,
        data_dir=Path("/tmp/test-robot"),
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "search_for_victims", "assess_victim", "report_status", "return_to_safe_zone"),
        llm_exposed_skills=("search_for_victims",),
        capability_skill_chains={
            "search_for_victims": ["navigate_to_floor", "search_for_victims", "report_status"],
        },
    )
