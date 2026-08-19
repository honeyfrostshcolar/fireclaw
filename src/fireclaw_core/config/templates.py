"""FireClaw Robot Model Templates and Profile Generation Engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RobotTemplate:
    """Standard robot model template containing defaults and recommended topics."""
    template_id: str
    name_zh: str
    mode: str  # "simulation" | "real"
    description_zh: str
    chassis_type: str  # "diff_drive" | "tracked" | "quadruped" | "omni"
    default_config: dict[str, Any]
    recommended_topics: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "name_zh": self.name_zh,
            "mode": self.mode,
            "description_zh": self.description_zh,
            "chassis_type": self.chassis_type,
            "recommended_topics": dict(self.recommended_topics),
            "default_config": self.default_config,
        }


def _format_toml_value(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    elif isinstance(val, (int, float)):
        return str(val)
    elif isinstance(val, str):
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    elif isinstance(val, (list, tuple)):
        items = [_format_toml_value(item) for item in val]
        return f"[{', '.join(items)}]"
    elif isinstance(val, dict):
        items = [f"{k} = {_format_toml_value(v)}" for k, v in val.items()]
        return f"{{{', '.join(items)}}}"
    return str(val)


def _dict_to_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []

    # 1. Top-level scalar keys
    for k, v in data.items():
        if not isinstance(v, dict) and not (isinstance(v, (list, tuple)) and v and isinstance(v[0], dict)):
            lines.append(f"{k} = {_format_toml_value(v)}")

    # Helper to recursively format tables
    def _format_table(prefix: str, table_data: dict[str, Any]) -> None:
        scalars: list[tuple[str, Any]] = []
        nested_tables: list[tuple[str, dict[str, Any]]] = []
        nested_lists_of_tables: list[tuple[str, list[dict[str, Any]]]] = []

        for k, v in table_data.items():
            if isinstance(v, dict):
                nested_tables.append((k, v))
            elif isinstance(v, (list, tuple)) and v and isinstance(v[0], dict):
                nested_lists_of_tables.append((k, list(v)))
            else:
                scalars.append((k, v))

        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"[{prefix}]")
        for k, v in scalars:
            lines.append(f"{k} = {_format_toml_value(v)}")

        for k, v in nested_tables:
            _format_table(f"{prefix}.{k}", v)

        for k, list_of_dicts in nested_lists_of_tables:
            array_prefix = f"{prefix}.{k}"
            for item in list_of_dicts:
                lines.append("")
                lines.append(f"[[{array_prefix}]]")
                for item_k, item_v in item.items():
                    lines.append(f"{item_k} = {_format_toml_value(item_v)}")

    # 2. Top-level tables
    for k, v in data.items():
        if isinstance(v, dict):
            _format_table(k, v)
        elif isinstance(v, (list, tuple)) and v and isinstance(v[0], dict):
            for item in v:
                lines.append("")
                lines.append(f"[[{k}]]")
                for item_k, item_v in item.items():
                    lines.append(f"{item_k} = {_format_toml_value(item_v)}")

    return "\n".join(lines) + "\n"


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = dict(base)
    for k, v in overrides.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


BUILTIN_TEMPLATES: list[RobotTemplate] = [
    # 1. Gazebo TurtleBot3 Burger (Simulation)
    RobotTemplate(
        template_id="gazebo_turtlebot3_burger",
        name_zh="Gazebo 仿真 TurtleBot3 Burger",
        mode="simulation",
        description_zh="适用于 Gazebo 仿真环境的标准 TurtleBot3 Burger 差速移动机器人，配备单线激光雷达与 2D 导航栈",
        chassis_type="diff_drive",
        recommended_topics={
            "laser_scan_topic": "/scan",
            "odometry_topic": "/odom",
            "cmd_vel_topic": "/cmd_vel",
            "navigation_action": "/move_base",
        },
        default_config={
            "robot": {
                "id": "gazebo_turtlebot3_burger",
                "base_url": "http://127.0.0.1:8765",
                "adapter": "ros1",
                "ros1_config": "ros1_configs/gazebo_turtlebot3_burger.yaml",
                "data_dir": "data/robots/gazebo_turtlebot3_burger",
                "capabilities": ["navigation", "patrol"],
                "enabled_skills": ["navigate_to_point"],
                "llm_exposed_skills": ["navigate_to_point"],
                "primitive_skills": ["navigate_to_point"],
                "sensor_discovery": {
                    "enabled": True,
                    "message_timeout_seconds": 2.0,
                    "rules": [
                        {
                            "topic_pattern": "/scan",
                            "message_type": "sensor_msgs/LaserScan",
                            "sensor": "lidar",
                            "confidence": 0.99,
                            "confirmed": True,
                        }
                    ],
                },
            },
            "capability_skill_chains": {
                "navigation": ["navigate_to_point"],
                "patrol": ["navigate_to_point"],
            },
        },
    ),
    # 2. Gazebo TurtleBot3 Waffle Pi (Simulation)
    RobotTemplate(
        template_id="gazebo_turtlebot3_waffle",
        name_zh="Gazebo 仿真 TurtleBot3 Waffle Pi",
        mode="simulation",
        description_zh="Gazebo 仿真环境下的 TurtleBot3 Waffle Pi 机器人，配备 RGB 摄像头、单线激光雷达与导航支持",
        chassis_type="diff_drive",
        recommended_topics={
            "laser_scan_topic": "/scan",
            "odometry_topic": "/odom",
            "cmd_vel_topic": "/cmd_vel",
            "rgb_camera_topic": "/camera/rgb/image_raw",
            "navigation_action": "/move_base",
        },
        default_config={
            "robot": {
                "id": "gazebo_turtlebot3_waffle",
                "base_url": "http://127.0.0.1:8765",
                "adapter": "ros1",
                "ros1_config": "ros1_configs/gazebo_turtlebot3_waffle.yaml",
                "data_dir": "data/robots/gazebo_turtlebot3_waffle",
                "capabilities": ["navigation", "patrol", "vision_inspection"],
                "enabled_skills": ["navigate_to_point", "capture_image"],
                "llm_exposed_skills": ["navigate_to_point", "capture_image"],
                "primitive_skills": ["navigate_to_point", "capture_image"],
                "sensor_discovery": {
                    "enabled": True,
                    "message_timeout_seconds": 2.0,
                    "rules": [
                        {
                            "topic_pattern": "/scan",
                            "message_type": "sensor_msgs/LaserScan",
                            "sensor": "lidar",
                            "confidence": 0.99,
                            "confirmed": True,
                        },
                        {
                            "topic_pattern": "/camera/rgb/image_raw",
                            "message_type": "sensor_msgs/Image",
                            "sensor": "rgb_camera",
                            "confidence": 0.95,
                            "confirmed": True,
                        },
                    ],
                },
            },
            "capability_skill_chains": {
                "navigation": ["navigate_to_point"],
                "patrol": ["navigate_to_point"],
                "vision_inspection": ["capture_image"],
            },
        },
    ),
    # 3. Real Firefighting Tracked Robot
    RobotTemplate(
        template_id="real_firefighting_tracked",
        name_zh="真实履带式消防灭火救援机器人",
        mode="real",
        description_zh="重型履带式高机动消防机器人，搭载双光云台（可见光+红外热成像）、气体探测器与遥控水炮",
        chassis_type="tracked",
        recommended_topics={
            "laser_scan_topic": "/scan",
            "odometry_topic": "/odom",
            "cmd_vel_topic": "/cmd_vel",
            "rgb_camera_topic": "/camera/image_raw",
            "thermal_camera_topic": "/thermal/image_raw",
            "gas_sensor_topic": "/sensors/gas",
            "water_cannon_topic": "/actuators/water_cannon",
            "navigation_action": "/move_base",
        },
        default_config={
            "robot": {
                "id": "real_firefighting_tracked",
                "base_url": "http://127.0.0.1:8765",
                "adapter": "ros1",
                "ros1_config": "ros1_configs/real_firefighting_tracked.yaml",
                "data_dir": "data/robots/real_firefighting_tracked",
                "capabilities": ["navigation", "fire_reconnaissance", "fire_suppression", "gas_monitoring"],
                "enabled_skills": [
                    "navigate_to_point",
                    "measure_fire_temperature",
                    "detect_toxic_gas",
                    "aim_and_spray_water",
                ],
                "llm_exposed_skills": [
                    "navigate_to_point",
                    "measure_fire_temperature",
                    "detect_toxic_gas",
                    "aim_and_spray_water",
                ],
                "primitive_skills": [
                    "navigate_to_point",
                    "measure_fire_temperature",
                    "detect_toxic_gas",
                    "aim_and_spray_water",
                ],
                "sensor_discovery": {
                    "enabled": True,
                    "message_timeout_seconds": 2.0,
                    "rules": [
                        {
                            "topic_pattern": "/scan",
                            "message_type": "sensor_msgs/LaserScan",
                            "sensor": "lidar",
                            "confidence": 0.99,
                            "confirmed": True,
                        },
                        {
                            "topic_pattern": "/thermal/image_raw",
                            "message_type": "sensor_msgs/Image",
                            "sensor": "thermal_camera",
                            "confidence": 0.95,
                            "confirmed": True,
                        },
                        {
                            "topic_pattern": "/sensors/gas",
                            "message_type": "std_msgs/Float32",
                            "sensor": "gas_detector",
                            "confidence": 0.9,
                            "confirmed": True,
                        },
                    ],
                },
            },
            "capability_skill_chains": {
                "navigation": ["navigate_to_point"],
                "fire_reconnaissance": ["measure_fire_temperature"],
                "fire_suppression": ["aim_and_spray_water"],
                "gas_monitoring": ["detect_toxic_gas"],
            },
        },
    ),
    # 4. Real Quadruped Rescue Robot Dog
    RobotTemplate(
        template_id="real_quadruped_rescue",
        name_zh="真实四足侦察巡检机器狗",
        mode="real",
        description_zh="四足仿生侦察机器人，适用于复杂废墟攀爬与灾害侦察，搭载 3D 激光雷达、防爆云台与有毒有害气体传感器",
        chassis_type="quadruped",
        recommended_topics={
            "laser_scan_topic": "/scan",
            "odometry_topic": "/odom",
            "cmd_vel_topic": "/cmd_vel",
            "gimbal_cmd_topic": "/gimbal/cmd",
            "gas_sensor_topic": "/sensors/gas",
            "rgb_camera_topic": "/camera/front/image_raw",
            "navigation_action": "/move_base",
        },
        default_config={
            "robot": {
                "id": "real_quadruped_rescue",
                "base_url": "http://127.0.0.1:8765",
                "adapter": "ros1",
                "ros1_config": "ros1_configs/real_quadruped_rescue.yaml",
                "data_dir": "data/robots/real_quadruped_rescue",
                "capabilities": ["navigation", "reconnaissance", "gas_monitoring"],
                "enabled_skills": [
                    "navigate_to_point",
                    "control_gimbal",
                    "detect_toxic_gas",
                ],
                "llm_exposed_skills": [
                    "navigate_to_point",
                    "control_gimbal",
                    "detect_toxic_gas",
                ],
                "primitive_skills": [
                    "navigate_to_point",
                    "control_gimbal",
                    "detect_toxic_gas",
                ],
                "sensor_discovery": {
                    "enabled": True,
                    "message_timeout_seconds": 2.0,
                    "rules": [
                        {
                            "topic_pattern": "/scan",
                            "message_type": "sensor_msgs/LaserScan",
                            "sensor": "lidar",
                            "confidence": 0.99,
                            "confirmed": True,
                        },
                        {
                            "topic_pattern": "/sensors/gas",
                            "message_type": "std_msgs/Float32",
                            "sensor": "gas_detector",
                            "confidence": 0.9,
                            "confirmed": True,
                        },
                    ],
                },
            },
            "capability_skill_chains": {
                "navigation": ["navigate_to_point"],
                "reconnaissance": ["control_gimbal"],
                "gas_monitoring": ["detect_toxic_gas"],
            },
        },
    ),
    # 5. Real Generic Diff Drive Robot
    RobotTemplate(
        template_id="real_generic_diff_drive",
        name_zh="真实通用轮式差速机器人",
        mode="real",
        description_zh="标准两轮差速室内外巡检/物流底盘，支持标准 ROS1 move_base 导航与激光雷达避障",
        chassis_type="diff_drive",
        recommended_topics={
            "laser_scan_topic": "/scan",
            "odometry_topic": "/odom",
            "cmd_vel_topic": "/cmd_vel",
            "navigation_action": "/move_base",
        },
        default_config={
            "robot": {
                "id": "real_generic_diff_drive",
                "base_url": "http://127.0.0.1:8765",
                "adapter": "ros1",
                "ros1_config": "ros1_configs/real_generic_diff_drive.yaml",
                "data_dir": "data/robots/real_generic_diff_drive",
                "capabilities": ["navigation", "patrol"],
                "enabled_skills": ["navigate_to_point"],
                "llm_exposed_skills": ["navigate_to_point"],
                "primitive_skills": ["navigate_to_point"],
                "sensor_discovery": {
                    "enabled": True,
                    "message_timeout_seconds": 2.0,
                    "rules": [
                        {
                            "topic_pattern": "/scan",
                            "message_type": "sensor_msgs/LaserScan",
                            "sensor": "lidar",
                            "confidence": 0.99,
                            "confirmed": True,
                        }
                    ],
                },
            },
            "capability_skill_chains": {
                "navigation": ["navigate_to_point"],
                "patrol": ["navigate_to_point"],
            },
        },
    ),
]


class TemplateManager:
    """Manages robot model templates and renders TOML configurations."""

    def __init__(self, templates: Optional[list[RobotTemplate]] = None) -> None:
        self._templates: dict[str, RobotTemplate] = {}
        target_list = templates if templates is not None else BUILTIN_TEMPLATES
        for t in target_list:
            self._templates[t.template_id] = t

    def list_templates(self) -> list[RobotTemplate]:
        return list(self._templates.values())

    def get_template(self, template_id: str) -> Optional[RobotTemplate]:
        return self._templates.get(template_id)

    def register_template(self, template: RobotTemplate) -> None:
        self._templates[template.template_id] = template

    def render_profile_toml(
        self,
        template_id: str,
        overrides: Optional[dict[str, Any]] = None,
    ) -> str:
        template = self.get_template(template_id)
        if template is None:
            raise KeyError(f"Template with id '{template_id}' not found.")

        config = dict(template.default_config)
        if overrides:
            config = _deep_merge(config, overrides)

        return _dict_to_toml(config)
