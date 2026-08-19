"""FireClaw Unified Configuration Schema and Validation Engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ConfigField:
    """Definition of a single configurable field with UI hints and validation metadata."""
    name: str
    type: str  # "string" | "integer" | "float" | "boolean" | "select" | "list"
    label_zh: str
    description_zh: str
    default: Any
    example: str
    required: bool = True
    secret: bool = False
    options: Optional[list[str]] = None
    probe_action: Optional[str] = None  # "ros_topic" | "ros_service" | "ros_action" | "http_ping" | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "label_zh": self.label_zh,
            "description_zh": self.description_zh,
            "default": self.default,
            "example": self.example,
            "required": self.required,
            "secret": self.secret,
            "options": list(self.options) if self.options is not None else None,
            "probe_action": self.probe_action,
        }


@dataclass
class PluginConfigSchema:
    """Schema descriptor for a plugin or core subsystem's configurable parameters."""
    plugin_name: str
    version: str
    fields: list[ConfigField] = field(default_factory=list)

    def validate(self, values: dict[str, Any]) -> tuple[bool, list[str]]:
        errors: list[str] = []

        for field_def in self.fields:
            name = field_def.name
            val = values.get(name)

            # Check required
            if field_def.required:
                if val is None or (isinstance(val, str) and not val.strip()):
                    errors.append(f"字段 '{name}' ({field_def.label_zh}) 为必填项，不可为空")
                    continue

            if val is None:
                continue

            # Type checking
            if field_def.type == "string":
                if not isinstance(val, str):
                    errors.append(f"字段 '{name}' 应为字符串类型，当前值为: {val!r}")
            elif field_def.type == "integer":
                if not isinstance(val, int) or isinstance(val, bool):
                    errors.append(f"字段 '{name}' 应为整数类型，当前值为: {val!r}")
            elif field_def.type == "float":
                if not isinstance(val, (int, float)) or isinstance(val, bool):
                    errors.append(f"字段 '{name}' 应为浮点数/数值类型，当前值为: {val!r}")
            elif field_def.type == "boolean":
                if not isinstance(val, bool):
                    errors.append(f"字段 '{name}' 应为布尔值类型 (True/False)，当前值为: {val!r}")
            elif field_def.type == "select":
                if field_def.options and val not in field_def.options:
                    errors.append(
                        f"字段 '{name}' 的值 {val!r} 不在允许的可选项列表 {field_def.options} 中"
                    )
            elif field_def.type == "list":
                if not isinstance(val, (list, tuple)):
                    errors.append(f"字段 '{name}' 应为列表类型，当前值为: {val!r}")

        return len(errors) == 0, errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_name": self.plugin_name,
            "version": self.version,
            "fields": [f.to_dict() for f in self.fields],
        }

    def get_defaults(self) -> dict[str, Any]:
        return {
            f.name: f.default
            for f in self.fields
            if f.default is not None
        }


def get_core_config_schemas() -> list[PluginConfigSchema]:
    """Returns the standard configuration schemas for FireClaw core subsystems."""
    return [
        # 1. Robot Identity & Adapter
        PluginConfigSchema(
            plugin_name="fireclaw.core.robot",
            version="1.0.0",
            fields=[
                ConfigField(
                    name="id",
                    type="string",
                    label_zh="机器人唯一标识",
                    description_zh="机器人在整个机群中的唯一 ID 字符串，如 fireclaw_robot_1",
                    default="fireclaw_robot_1",
                    example="fireclaw_robot_1",
                    required=True,
                ),
                ConfigField(
                    name="base_url",
                    type="string",
                    label_zh="Robot Gateway 通信地址",
                    description_zh="机器人机载 Gateway REST/WS 监听 URL",
                    default="http://127.0.0.1:8765",
                    example="http://127.0.0.1:8765",
                    required=True,
                    probe_action="http_ping",
                ),
                ConfigField(
                    name="adapter",
                    type="select",
                    label_zh="底盘适配器类型",
                    description_zh="底层硬件或仿真通信驱动模式 (ROS1 / ROS2 / Mock)",
                    default="ros1",
                    example="ros1",
                    options=["ros1", "ros2", "mock", "stub"],
                    required=True,
                ),
                ConfigField(
                    name="ros_master_uri",
                    type="string",
                    label_zh="ROS Master URI",
                    description_zh="ROS 1 Master 节点服务地址与端口",
                    default="http://localhost:11311",
                    example="http://localhost:11311",
                    required=False,
                    probe_action="ros_master",
                ),
                ConfigField(
                    name="data_dir",
                    type="string",
                    label_zh="数据持久化目录",
                    description_zh="机器人本地记忆、任务队列与事件日志存储相对或绝对路径",
                    default="data/robots/fireclaw_robot_1",
                    example="data/robots/fireclaw_robot_1",
                    required=True,
                ),
            ],
        ),
        # 2. Navigation Move Base
        PluginConfigSchema(
            plugin_name="fireclaw.navigation.move_base",
            version="1.0.0",
            fields=[
                ConfigField(
                    name="scan_topic",
                    type="string",
                    label_zh="激光雷达 Topic",
                    description_zh="2D 激光雷达点云/扫描测距数据话题",
                    default="/scan",
                    example="/scan",
                    required=True,
                    probe_action="ros_topic",
                ),
                ConfigField(
                    name="odom_topic",
                    type="string",
                    label_zh="里程计 Topic",
                    description_zh="轮式里程计或融合定位状态话题",
                    default="/odom",
                    example="/odom",
                    required=True,
                    probe_action="ros_topic",
                ),
                ConfigField(
                    name="cmd_vel_topic",
                    type="string",
                    label_zh="底盘速度控制 Topic",
                    description_zh="底盘下发线速度与角速度控制指令的话题",
                    default="/cmd_vel",
                    example="/cmd_vel",
                    required=True,
                    probe_action="ros_topic",
                ),
                ConfigField(
                    name="move_base_action",
                    type="string",
                    label_zh="move_base Action 服务名",
                    description_zh="ROS actionlib move_base 导航动作服务命名空间",
                    default="/move_base",
                    example="/move_base",
                    required=True,
                    probe_action="ros_action",
                ),
                ConfigField(
                    name="max_linear_velocity",
                    type="float",
                    label_zh="最大前进线速度 (m/s)",
                    description_zh="机器人在自主导航过程中的安全最高线速度限制",
                    default=0.5,
                    example="0.5",
                    required=True,
                ),
                ConfigField(
                    name="max_angular_velocity",
                    type="float",
                    label_zh="最大旋转角速度 (rad/s)",
                    description_zh="机器人在原地或拐弯旋转时的安全最高角速度限制",
                    default=1.5,
                    example="1.5",
                    required=True,
                ),
            ],
        ),
        # 3. Thermal Camera Sensing
        PluginConfigSchema(
            plugin_name="fireclaw.sensors.thermal",
            version="1.0.0",
            fields=[
                ConfigField(
                    name="thermal_topic",
                    type="string",
                    label_zh="红外热成像图像 Topic",
                    description_zh="红外热成像传感器发布的 Image 或 CompressedImage 话题",
                    default="/thermal/image_raw",
                    example="/thermal/image_raw",
                    required=True,
                    probe_action="ros_topic",
                ),
                ConfigField(
                    name="high_temp_threshold",
                    type="float",
                    label_zh="火源高温告警阈值 (°C)",
                    description_zh="识别为明火与危险火源的红外温度临界点",
                    default=60.0,
                    example="60.0",
                    required=True,
                ),
            ],
        ),
        # 4. Water Cannon Actuator
        PluginConfigSchema(
            plugin_name="fireclaw.actuators.water_cannon",
            version="1.0.0",
            fields=[
                ConfigField(
                    name="cmd_topic",
                    type="string",
                    label_zh="水炮控制指令 Topic",
                    description_zh="消防水炮射击、开关阀与俯仰角度控制话题",
                    default="/actuators/water_cannon/cmd",
                    example="/actuators/water_cannon/cmd",
                    required=True,
                    probe_action="ros_topic",
                ),
                ConfigField(
                    name="max_pressure_bar",
                    type="float",
                    label_zh="最大喷射水压 (Bar)",
                    description_zh="消防供水系统的最大允许水压保护阈值",
                    default=16.0,
                    example="16.0",
                    required=True,
                ),
            ],
        ),
    ]
