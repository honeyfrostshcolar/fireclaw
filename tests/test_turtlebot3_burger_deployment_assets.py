from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET

import yaml

from fireclaw_core.agent.robot_profile import load_robot_capability_profile
from fireclaw_core.deployment.profile import load_runtime_deployment_profile


REPO_ROOT = Path(__file__).resolve().parents[1]
ROBOT_ROOT = REPO_ROOT / "robots" / "turtlebot3_burger"
INTEGRATION_PACKAGE = (
    ROBOT_ROOT / "ros_ws" / "src" / "fireclaw_turtlebot3_burger"
)
PLUGIN_ROOT = REPO_ROOT / "extensions" / "navigation-move-base"
PLUGIN_LAUNCH = PLUGIN_ROOT / "launch" / "fireclaw_navigation.launch"
PLUGIN_CONFIG = PLUGIN_ROOT / "config" / "defaults"
PROFILE = REPO_ROOT / "fireclaw.sim.example.toml"
RUNTIME_DESCRIPTOR = PLUGIN_ROOT / "runtime" / "fireclaw.runtime.json"


def _launch(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


def test_root_profile_binds_turtlebot3_without_robot_navigation_launch() -> None:
    robot_profile = load_robot_capability_profile(PROFILE)
    deployment = load_runtime_deployment_profile(
        PROFILE,
        output_root="/tmp/fireclaw-test-deployments",
    )

    assert robot_profile.robot_id == "gazebo_turtlebot3"
    assert robot_profile.adapter == "ros1"
    assert deployment.mode == "simulation"
    assert deployment.selected_plugin_ids == ("fireclaw.navigation.move-base",)
    assert deployment.robot_launch == (
        INTEGRATION_PACKAGE / "launch" / "robot_base.launch"
    ).resolve()
    assert "navigation.stack_launch" not in deployment.bindings
    assert deployment.bindings["navigation.map_file"] == (
        "robots/turtlebot3_burger/ros_ws/src/turtlebot3/"
        "turtlebot3_navigation/maps/map.yaml"
    )
    assert deployment.bindings["navigation.scan_topic"] == "/scan"
    assert deployment.bindings["navigation.scan_frame"] == "base_scan"
    assert deployment.bindings["navigation.cmd_vel_topic"] == "/cmd_vel"
    assert deployment.bindings["navigation.odom_topic"] == "/odom"
    assert deployment.bindings["navigation.base_frame"] == "base_footprint"
    assert deployment.bindings["navigation.max_linear_velocity"] == 0.22
    assert deployment.bindings["navigation.max_angular_velocity"] == 2.75


def test_navigation_plugin_owns_stack_and_argument_contract() -> None:
    descriptor = json.loads(RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    descriptor_arguments = {
        item["name"] for item in descriptor["launch"]["arguments"]
    }
    root = _launch(PLUGIN_LAUNCH)
    launch_arguments = {item.attrib["name"] for item in root.findall("arg")}

    assert descriptor_arguments <= launch_arguments
    assert "stack_launch" not in descriptor_arguments

    nodes = {
        (item.attrib.get("pkg"), item.attrib.get("type"), item.attrib.get("name"))
        for item in root.findall("node")
    }
    assert ("map_server", "map_server", "map_server") in nodes
    assert ("amcl", "amcl", "amcl") in nodes
    assert ("move_base", "move_base", "move_base") in nodes

    rendered = ET.tostring(root, encoding="unicode")
    for binding in (
        "map_file",
        "scan_topic",
        "scan_frame",
        "cmd_vel_topic",
        "odom_topic",
        "map_frame",
        "odom_frame",
        "base_frame",
        "footprint",
        "max_linear_velocity",
        "max_angular_velocity",
    ):
        assert f"$(arg {binding})" in rendered
    assert "$(dirname)/../config/defaults/amcl.yaml" in rendered
    assert "$(dirname)/../config/defaults/dwa_local_planner.yaml" in rendered


def test_robot_base_launch_keeps_hardware_separate_from_navigation() -> None:
    root = _launch(INTEGRATION_PACKAGE / "launch" / "robot_base.launch")
    rendered = ET.tostring(root, encoding="unicode")

    assert "turtlebot3_gazebo" in rendered
    assert "turtlebot3_description" in rendered
    assert "robot_state_publisher" in rendered
    assert "spawn_model" in rendered
    assert "map_server" not in rendered
    assert 'pkg="amcl"' not in rendered
    assert 'pkg="move_base"' not in rendered
    assert not (INTEGRATION_PACKAGE / "launch" / "navigation_stack.launch").exists()
    assert not list((INTEGRATION_PACKAGE / "config").glob("*.yaml"))


def test_plugin_defaults_keep_profile_owned_values_out_of_yaml() -> None:
    amcl = yaml.safe_load((PLUGIN_CONFIG / "amcl.yaml").read_text(encoding="utf-8"))
    common = yaml.safe_load(
        (PLUGIN_CONFIG / "costmap_common.yaml").read_text(encoding="utf-8")
    )
    global_costmap = yaml.safe_load(
        (PLUGIN_CONFIG / "global_costmap.yaml").read_text(encoding="utf-8")
    )
    local_costmap = yaml.safe_load(
        (PLUGIN_CONFIG / "local_costmap.yaml").read_text(encoding="utf-8")
    )
    dwa = yaml.safe_load(
        (PLUGIN_CONFIG / "dwa_local_planner.yaml").read_text(encoding="utf-8")
    )

    assert "laser_max_range" not in amcl
    assert "odom_model_type" not in amcl
    assert not any(key.startswith("odom_alpha") for key in amcl)
    assert "footprint" not in common
    assert "topic" not in common["scan"]
    assert "sensor_frame" not in common["scan"]
    assert "obstacle_range" not in common
    assert "global_frame" not in global_costmap["global_costmap"]
    assert "robot_base_frame" not in global_costmap["global_costmap"]
    assert "global_frame" not in local_costmap["local_costmap"]
    assert "robot_base_frame" not in local_costmap["local_costmap"]
    for key in (
        "max_vel_x",
        "max_vel_y",
        "max_vel_trans",
        "max_vel_theta",
        "acc_lim_x",
        "acc_lim_y",
        "acc_lim_theta",
    ):
        assert key not in dwa["DWAPlannerROS"]


def test_reference_map_and_install_boundaries_exist() -> None:
    deployment = load_runtime_deployment_profile(
        PROFILE,
        output_root="/tmp/fireclaw-test-deployments",
    )
    map_path = (PROFILE.parent / deployment.bindings["navigation.map_file"]).resolve()
    map_config = yaml.safe_load(map_path.read_text(encoding="utf-8"))

    assert (map_path.parent / map_config["image"]).resolve().is_file()
    assert all(path.is_file() for path in PLUGIN_CONFIG.glob("*.yaml"))
    cmake = (INTEGRATION_PACKAGE / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "DIRECTORY launch" in cmake
    assert "DIRECTORY config launch" not in cmake
