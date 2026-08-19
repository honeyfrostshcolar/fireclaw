"""Tests for FireClaw Zero-Handwritten Config Core Engine (Task 1).

Covers:
1. Model Templates (RobotTemplate, TemplateManager, builtin templates, TOML rendering & overrides)
2. ROS Graph Discovery (RosGraphDiscoverer, topic heuristic matching, action server detection, offline fallback)
3. Plugin Config Schema (ConfigField, PluginConfigSchema, core schemas, validation logic)
4. Integration with RobotCapabilityProfile loader
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from typing import Any

from fireclaw_core.config import (
    RobotTemplate,
    TemplateManager,
    BUILTIN_TEMPLATES,
    DiscoveryReport,
    RosGraphDiscoverer,
    ConfigField,
    PluginConfigSchema,
    get_core_config_schemas,
)
from fireclaw_core.infra import tomllib_compat as tomllib
from fireclaw_core.agent.robot_profile import load_robot_capability_profile


class TestRobotTemplates(unittest.TestCase):
    """Test RobotTemplate and TemplateManager."""

    def setUp(self) -> None:
        self.manager = TemplateManager()

    def test_builtin_templates_count_and_ids(self) -> None:
        templates = self.manager.list_templates()
        self.assertEqual(len(templates), 5)
        template_ids = {t.template_id for t in templates}
        expected_ids = {
            "gazebo_turtlebot3_burger",
            "gazebo_turtlebot3_waffle",
            "real_firefighting_tracked",
            "real_quadruped_rescue",
            "real_generic_diff_drive",
        }
        self.assertEqual(template_ids, expected_ids)

    def test_get_template_by_id(self) -> None:
        burger = self.manager.get_template("gazebo_turtlebot3_burger")
        self.assertIsNotNone(burger)
        assert burger is not None
        self.assertEqual(burger.template_id, "gazebo_turtlebot3_burger")
        self.assertEqual(burger.mode, "simulation")
        self.assertEqual(burger.chassis_type, "diff_drive")
        self.assertIn("scan", burger.recommended_topics.get("laser_scan_topic", ""))
        self.assertIn("odom", burger.recommended_topics.get("odometry_topic", ""))
        self.assertIn("cmd_vel", burger.recommended_topics.get("cmd_vel_topic", ""))

        non_existent = self.manager.get_template("non_existent_id")
        self.assertIsNone(non_existent)

    def test_tracked_firefighting_template(self) -> None:
        tracked = self.manager.get_template("real_firefighting_tracked")
        self.assertIsNotNone(tracked)
        assert tracked is not None
        self.assertEqual(tracked.mode, "real")
        self.assertEqual(tracked.chassis_type, "tracked")
        self.assertIn("thermal", tracked.recommended_topics.get("thermal_camera_topic", ""))
        self.assertIn("gas", tracked.recommended_topics.get("gas_sensor_topic", ""))
        self.assertIn("water_cannon", tracked.recommended_topics.get("water_cannon_topic", ""))

    def test_quadruped_rescue_template(self) -> None:
        dog = self.manager.get_template("real_quadruped_rescue")
        self.assertIsNotNone(dog)
        assert dog is not None
        self.assertEqual(dog.mode, "real")
        self.assertEqual(dog.chassis_type, "quadruped")
        self.assertIn("gimbal", dog.recommended_topics.get("gimbal_cmd_topic", ""))

    def test_generic_diff_drive_template(self) -> None:
        diff = self.manager.get_template("real_generic_diff_drive")
        self.assertIsNotNone(diff)
        assert diff is not None
        self.assertEqual(diff.mode, "real")
        self.assertEqual(diff.chassis_type, "diff_drive")

    def test_custom_template_registration(self) -> None:
        custom = RobotTemplate(
            template_id="custom_scout_hexapod",
            name_zh="六足仿生排爆机器人",
            mode="real",
            description_zh="搭载机械臂与多光谱传感器的六足全地形机器人",
            chassis_type="omni",
            recommended_topics={"laser_scan_topic": "/hexapod/scan"},
            default_config={"robot": {"id": "hexapod_01", "adapter": "ros1"}},
        )
        self.manager.register_template(custom)
        found = self.manager.get_template("custom_scout_hexapod")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.chassis_type, "omni")
        self.assertEqual(len(self.manager.list_templates()), 6)

    def test_render_profile_toml_validity(self) -> None:
        for t in self.manager.list_templates():
            toml_str = self.manager.render_profile_toml(t.template_id)
            self.assertIsInstance(toml_str, str)
            self.assertTrue(len(toml_str) > 0)
            parsed = tomllib.loads(toml_str)
            self.assertIn("robot", parsed)
            self.assertIn("id", parsed["robot"])
            self.assertIn("adapter", parsed["robot"])
            self.assertEqual(parsed["robot"]["adapter"], "ros1")

    def test_render_profile_toml_with_overrides(self) -> None:
        overrides = {
            "robot": {
                "id": "custom_fire_bot_01",
                "base_url": "http://192.168.1.100:8765",
            }
        }
        toml_str = self.manager.render_profile_toml("real_firefighting_tracked", overrides=overrides)
        parsed = tomllib.loads(toml_str)
        self.assertEqual(parsed["robot"]["id"], "custom_fire_bot_01")
        self.assertEqual(parsed["robot"]["base_url"], "http://192.168.1.100:8765")
        self.assertEqual(parsed["robot"]["adapter"], "ros1")

    def test_render_profile_toml_missing_template_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.manager.render_profile_toml("unknown_template_xyz")

    def test_template_to_dict(self) -> None:
        template = self.manager.get_template("gazebo_turtlebot3_waffle")
        assert template is not None
        d = template.to_dict()
        self.assertEqual(d["template_id"], "gazebo_turtlebot3_waffle")
        self.assertEqual(d["mode"], "simulation")
        self.assertIn("recommended_topics", d)
        self.assertIn("default_config", d)

    def test_rendered_toml_profile_loader_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            data_dir = tmp_path / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            overrides = {
                "robot": {
                    "data_dir": str(data_dir),
                    "ros1_config": str(tmp_path / "ros1.yaml"),
                }
            }
            toml_content = self.manager.render_profile_toml("gazebo_turtlebot3_burger", overrides=overrides)
            profile_file = tmp_path / "robot.toml"
            profile_file.write_text(toml_content, encoding="utf-8")

            # Load using core RobotCapabilityProfile loader
            profile = load_robot_capability_profile(profile_file)
            self.assertEqual(profile.robot_id, "gazebo_turtlebot3_burger")
            self.assertEqual(profile.adapter, "ros1")
            self.assertIn("navigation", profile.capabilities)
            self.assertIn("navigate_to_point", profile.enabled_skills)


class TestRosGraphDiscovery(unittest.TestCase):
    """Test RosGraphDiscoverer matching heuristics and probe fallback."""

    def setUp(self) -> None:
        self.discoverer = RosGraphDiscoverer()

    def test_match_topics_heuristics(self) -> None:
        active_topics = [
            ("/robot/laser/scan", "sensor_msgs/LaserScan"),
            ("/base_pose_ground_truth", "nav_msgs/Odometry"),
            ("/mobile_base/commands/velocity", "geometry_msgs/Twist"),
            ("/thermal/image_raw", "sensor_msgs/Image"),
            ("/camera/rgb/image_raw", "sensor_msgs/Image"),
            ("/gas_detector/reading", "std_msgs/Float32"),
            ("/move_base/goal", "move_base_msgs/MoveBaseActionGoal"),
            ("/move_base/result", "move_base_msgs/MoveBaseActionResult"),
            ("/gimbal/cmd", "geometry_msgs/Vector3"),
            ("/actuators/water_cannon/cmd", "std_msgs/Bool"),
            ("/imu/data", "sensor_msgs/Imu"),
            ("/velodyne_points", "sensor_msgs/PointCloud2"),
        ]

        matched = self.discoverer.match_topics(active_topics)

        self.assertEqual(matched.get("laser_scan_topic"), "/robot/laser/scan")
        self.assertEqual(matched.get("odometry_topic"), "/base_pose_ground_truth")
        self.assertEqual(matched.get("cmd_vel_topic"), "/mobile_base/commands/velocity")
        self.assertEqual(matched.get("thermal_camera_topic"), "/thermal/image_raw")
        self.assertEqual(matched.get("rgb_camera_topic"), "/camera/rgb/image_raw")
        self.assertEqual(matched.get("gas_sensor_topic"), "/gas_detector/reading")
        self.assertEqual(matched.get("navigation_action"), "/move_base")
        self.assertEqual(matched.get("gimbal_cmd_topic"), "/gimbal/cmd")
        self.assertEqual(matched.get("water_cannon_topic"), "/actuators/water_cannon/cmd")
        self.assertEqual(matched.get("imu_topic"), "/imu/data")
        self.assertEqual(matched.get("pointcloud_topic"), "/velodyne_points")

    def test_match_actions(self) -> None:
        active_topics = [
            ("/fire_extinguish_nav/goal", "actionlib_msgs/GoalID"),
            ("/fire_extinguish_nav/result", "actionlib_msgs/GoalStatusArray"),
        ]
        matched = self.discoverer.match_topics(active_topics)
        self.assertEqual(matched.get("navigation_action"), "/fire_extinguish_nav")

    @patch("fireclaw_core.config.discovery.xmlrpc.client.ServerProxy")
    def test_probe_ros_master_online_success(self, mock_server_proxy: MagicMock) -> None:
        mock_master = MagicMock()
        mock_server_proxy.return_value = mock_master

        mock_master.getSystemState.return_value = [
            1,
            "Success",
            [
                [["/scan", ["/node1"]], ["/odom", ["/node2"]]],  # publishers
                [["/cmd_vel", ["/node3"]]],  # subscribers
                [["/rosout/get_loggers", ["/rosout"]]],  # services
            ],
        ]
        mock_master.getTopicTypes.return_value = [
            1,
            "Success",
            [
                ["/scan", "sensor_msgs/LaserScan"],
                ["/odom", "nav_msgs/Odometry"],
                ["/cmd_vel", "geometry_msgs/Twist"],
            ],
        ]

        report = self.discoverer.probe_ros_master("http://localhost:11311", timeout=1.0)

        self.assertTrue(report.discovered)
        self.assertEqual(report.master_uri, "http://localhost:11311")
        self.assertEqual(len(report.active_topics), 3)
        self.assertEqual(report.matched_topics.get("laser_scan_topic"), "/scan")
        self.assertEqual(report.matched_topics.get("odometry_topic"), "/odom")
        self.assertEqual(report.matched_topics.get("cmd_vel_topic"), "/cmd_vel")
        self.assertEqual(report.active_services, ["/rosout/get_loggers"])

        d = report.to_dict()
        self.assertEqual(d["discovered"], True)
        self.assertEqual(d["master_uri"], "http://localhost:11311")

    def test_probe_ros_master_offline_graceful_fallback(self) -> None:
        # Intentionally invalid port / unreachable host
        report = self.discoverer.probe_ros_master("http://127.0.0.1:65530", timeout=0.1)

        self.assertFalse(report.discovered)
        self.assertIsNotNone(report.reason)
        self.assertTrue(len(report.offline_fallback_recommendations) > 0)
        # Should include offline recommendations for standard topics
        recommendations_str = " ".join(report.offline_fallback_recommendations)
        self.assertTrue(
            "/scan" in recommendations_str or "ROS" in recommendations_str or "roscore" in recommendations_str
        )

        d = report.to_dict()
        self.assertEqual(d["discovered"], False)
        self.assertIn("reason", d)


class TestConfigSchema(unittest.TestCase):
    """Test ConfigField, PluginConfigSchema, and get_core_config_schemas."""

    def test_config_field_to_dict(self) -> None:
        field = ConfigField(
            name="max_speed",
            type="float",
            label_zh="最大线速度",
            description_zh="机器人在导航过程中的最高运行线速度",
            default=0.5,
            example="0.5",
            required=True,
            secret=False,
            probe_action="ros_topic",
        )
        d = field.to_dict()
        self.assertEqual(d["name"], "max_speed")
        self.assertEqual(d["type"], "float")
        self.assertEqual(d["default"], 0.5)
        self.assertEqual(d["required"], True)
        self.assertEqual(d["probe_action"], "ros_topic")

    def test_plugin_config_schema_validation_success(self) -> None:
        schema = PluginConfigSchema(
            plugin_name="fireclaw.navigation.test",
            version="1.0.0",
            fields=[
                ConfigField(
                    name="scan_topic",
                    type="string",
                    label_zh="雷达 Topic",
                    description_zh="激光雷达数据话题",
                    default="/scan",
                    example="/scan",
                    required=True,
                ),
                ConfigField(
                    name="port",
                    type="integer",
                    label_zh="端口",
                    description_zh="通信端口",
                    default=8765,
                    example="8765",
                    required=True,
                ),
                ConfigField(
                    name="max_speed",
                    type="float",
                    label_zh="最高速度",
                    description_zh="机器人线速度上限",
                    default=0.5,
                    example="0.5",
                    required=True,
                ),
                ConfigField(
                    name="allow_reverse",
                    type="boolean",
                    label_zh="允许倒车",
                    description_zh="路径规划是否允许倒车",
                    default=False,
                    example="false",
                    required=False,
                ),
                ConfigField(
                    name="mode",
                    type="select",
                    label_zh="模式",
                    description_zh="运行模式",
                    default="auto",
                    example="auto",
                    options=["auto", "manual"],
                ),
                ConfigField(
                    name="tags",
                    type="list",
                    label_zh="标签列表",
                    description_zh="标签",
                    default=["patrol"],
                    example="['patrol']",
                    required=False,
                ),
            ],
        )

        valid, errors = schema.validate({
            "scan_topic": "/robot/scan",
            "port": 9000,
            "max_speed": 1.2,
            "allow_reverse": True,
            "mode": "auto",
            "tags": ["patrol", "fire"],
        })
        self.assertTrue(valid)
        self.assertEqual(len(errors), 0)

    def test_plugin_config_schema_validation_failures(self) -> None:
        schema = PluginConfigSchema(
            plugin_name="fireclaw.navigation.test",
            version="1.0.0",
            fields=[
                ConfigField(
                    name="scan_topic",
                    type="string",
                    label_zh="雷达 Topic",
                    description_zh="激光雷达数据话题",
                    default="/scan",
                    example="/scan",
                    required=True,
                ),
                ConfigField(
                    name="port",
                    type="integer",
                    label_zh="端口",
                    description_zh="通信端口",
                    default=8765,
                    example="8765",
                    required=True,
                ),
                ConfigField(
                    name="max_speed",
                    type="float",
                    label_zh="最高速度",
                    description_zh="机器人线速度上限",
                    default=0.5,
                    example="0.5",
                    required=True,
                ),
                ConfigField(
                    name="mode",
                    type="select",
                    label_zh="模式",
                    description_zh="运行模式",
                    default="auto",
                    example="auto",
                    options=["auto", "manual"],
                ),
                ConfigField(
                    name="tags",
                    type="list",
                    label_zh="标签",
                    description_zh="标签",
                    default=[],
                    example="[]",
                    required=True,
                ),
            ],
        )

        # Missing scan_topic & invalid port (string) & invalid float & invalid select option & invalid list
        valid, errors = schema.validate({
            "port": "not-a-port",
            "max_speed": "not-a-number",
            "mode": "invalid_mode",
            "tags": "not-a-list",
        })
        self.assertFalse(valid)
        self.assertTrue(len(errors) >= 4)

    def test_get_core_config_schemas(self) -> None:
        schemas = get_core_config_schemas()
        self.assertTrue(len(schemas) >= 4)
        names = {s.plugin_name for s in schemas}
        self.assertIn("fireclaw.core.robot", names)
        self.assertIn("fireclaw.navigation.move_base", names)
        self.assertIn("fireclaw.sensors.thermal", names)
        self.assertIn("fireclaw.actuators.water_cannon", names)

        # Check dictionary conversion & defaults
        for s in schemas:
            d = s.to_dict()
            self.assertIn("plugin_name", d)
            self.assertIn("version", d)
            self.assertIn("fields", d)
            defaults = s.get_defaults()
            self.assertIsInstance(defaults, dict)


if __name__ == "__main__":
    unittest.main()
