from __future__ import annotations

from typing import Any

from fireclaw_core.execution.skill_plugin import (
    PhysicalSkillCatalog,
    TaskInputBinding,
    define_physical_skill_plugin,
)


GENERIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}
FLOOR_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"floor": {"type": "integer", "minimum": 1}},
    "required": ["floor"],
    "additionalProperties": False,
}
LOCAL_CONTEXT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "floor": {
            "type": "integer",
            "minimum": 1,
            "description": "Legacy multi-floor compatibility field.",
        },
    },
    "additionalProperties": False,
}
POINT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "x": {"type": "number"},
        "y": {"type": "number"},
        "yaw": {"type": "number", "default": 0.0},
        "frame_id": {"type": "string", "minLength": 1, "default": "map"},
    },
    "required": ["x", "y"],
    "additionalProperties": False,
}
EMPTY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

GENERIC_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}
NAVIGATE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "from_floor": {"type": "integer"},
    },
    "required": ["robot_id", "floor"],
}
NAVIGATE_POINT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "yaw": {"type": "number"},
        "frame_id": {"type": "string"},
    },
    "required": ["robot_id", "x", "y", "yaw", "frame_id"],
}
SEARCH_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "victims_found": {"type": "integer"},
    },
    "required": ["robot_id", "floor", "victims_found"],
}
ASSESS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "condition": {"type": "string"},
    },
    "required": ["robot_id", "floor", "condition"],
}
REPORT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "message": {"type": "string"},
    },
    "required": ["robot_id", "floor", "message"],
}
EMPTY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
}


def _point_inputs(_robot: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "x": float(inputs["x"]),
        "y": float(inputs["y"]),
        "yaw": float(inputs.get("yaw", 0.0)),
        "frame_id": str(inputs.get("frame_id", "map")),
    }


def _floor_inputs(_robot: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    return {"floor": int(inputs["floor"])}


def _current_floor_inputs(robot: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    floor = inputs.get("floor")
    if isinstance(floor, int) and not isinstance(floor, bool):
        return {"floor": floor}
    current_floor = getattr(robot, "current_floor", None)
    return {"floor": current_floor if isinstance(current_floor, int) else 1}


def _empty_inputs(_robot: Any, _inputs: dict[str, Any]) -> dict[str, Any]:
    return {}


def _point_operator_message(inputs: dict[str, Any]) -> str:
    if inputs.get("x") is None or inputs.get("y") is None:
        return "正在前往目标点。"
    return (
        f"正在前往 {inputs.get('frame_id') or 'map'} 坐标系中的目标点 "
        f"({inputs['x']}, {inputs['y']})。"
    )


def _floor_operator_message(inputs: dict[str, Any]) -> str:
    return f"正在前往{inputs.get('floor', '目标')}楼。"


def _search_operator_message(inputs: dict[str, Any]) -> str:
    floor = inputs.get("floor")
    if floor is None:
        return "正在搜索当前目标区域的被困人员。"
    return f"正在搜索{floor}楼被困人员。"


def builtin_physical_skill_catalog() -> PhysicalSkillCatalog:
    catalog = PhysicalSkillCatalog()
    catalog.register(
        define_physical_skill_plugin(
            plugin_id="fireclaw.navigation.point",
            name="navigate_to_point",
            label="Navigate to point",
            description=(
                "Navigate within the current single-floor map to a target "
                "point expressed in an explicit coordinate frame."
            ),
            parameters=POINT_INPUT_SCHEMA,
            output_schema=NAVIGATE_POINT_OUTPUT_SCHEMA,
            action="navigate_to_point",
            action_input_builder=_point_inputs,
            domain="navigation",
            safety_class="motion",
            required_sensors=("lidar",),
            preconditions=("robot_online", "target_point_reachable"),
            degraded_mode_policy="retry",
            idempotent=True,
            allow_real_robot=True,
            task_input_bindings=(
                TaskInputBinding(
                    "x", (("pose", "x"),), required=True, coercion="float"
                ),
                TaskInputBinding(
                    "y", (("pose", "y"),), required=True, coercion="float"
                ),
                TaskInputBinding(
                    "yaw",
                    (("pose", "yaw"),),
                    default=0.0,
                    coercion="float",
                ),
                TaskInputBinding(
                    "frame_id",
                    (("frame_id",), ("pose", "frame_id")),
                    default="map",
                    coercion="string",
                ),
            ),
            auto_include_for_target=True,
            resource_locks=("robot_motion", "local_navigation"),
            success_evidence=("navigation_goal_reached",),
            operator_started_message=_point_operator_message,
            metadata={
                "kind": "primitive",
                "primitive_capability": "navigation",
                "spatial_scope": "single_floor_2d",
                "requires_approval": False,
            },
        )
    )
    catalog.register(
        define_physical_skill_plugin(
            plugin_id="fireclaw.navigation.floor-legacy",
            name="navigate_to_floor",
            label="Navigate to floor (legacy)",
            description="Navigate robot to a target floor.",
            parameters=FLOOR_INPUT_SCHEMA,
            output_schema=NAVIGATE_OUTPUT_SCHEMA,
            action="navigate_to_floor",
            action_input_builder=_floor_inputs,
            domain="navigation",
            safety_class="motion",
            required_sensors=("lidar",),
            preconditions=("robot_online", "floor_reachable"),
            degraded_mode_policy="retry",
            idempotent=True,
            allow_real_robot=True,
            task_input_bindings=(
                TaskInputBinding(
                    "floor", (("floor",),), required=True, coercion="integer"
                ),
            ),
            resource_locks=("robot_motion", "floor_transition"),
            success_evidence=("target_floor_reached",),
            operator_started_message=_floor_operator_message,
            metadata={
                "kind": "primitive",
                "primitive_capability": "navigation",
                "legacy": True,
                "spatial_scope": "multi_floor_extension",
                "requires_approval": False,
            },
        )
    )
    catalog.register(
        define_physical_skill_plugin(
            plugin_id="fireclaw.perception.victim-search",
            name="search_for_victims",
            label="Search for victims",
            description="Search for victims in the robot's current operating area.",
            parameters=LOCAL_CONTEXT_INPUT_SCHEMA,
            output_schema=SEARCH_OUTPUT_SCHEMA,
            action="search_for_victims",
            action_input_builder=_current_floor_inputs,
            domain="perception",
            safety_class="victim_perception",
            required_sensors=("rgb_camera",),
            sensor_alternatives={"rgb_camera": ("thermal_camera",)},
            preconditions=("robot_online", "camera_available"),
            degraded_mode_policy="fallback",
            allow_real_robot=True,
            task_type="search",
            default_followup_skills=("report_status",),
            task_input_bindings=(
                TaskInputBinding(
                    "floor",
                    (("floor",),),
                    required=False,
                    coercion="integer",
                ),
            ),
            resource_locks=("perception_pipeline",),
            success_evidence=("victim_search_report",),
            operator_started_message=_search_operator_message,
            metadata={
                "kind": "composite",
                "primitive_capability": "perception",
                "requires_approval": False,
            },
        )
    )
    catalog.register(
        define_physical_skill_plugin(
            plugin_id="fireclaw.perception.victim-assessment",
            name="assess_victim",
            label="Assess victim",
            description="Assess victim condition in the current operating area.",
            parameters=LOCAL_CONTEXT_INPUT_SCHEMA,
            output_schema=ASSESS_OUTPUT_SCHEMA,
            action="assess_victim",
            action_input_builder=_current_floor_inputs,
            domain="perception",
            safety_class="victim_perception",
            required_sensors=("rgb_camera", "thermal_camera"),
            sensor_alternatives={
                "rgb_camera": ("thermal_camera",),
                "thermal_camera": ("rgb_camera",),
            },
            preconditions=("robot_online", "victim_detected"),
            degraded_mode_policy="skip",
            allow_real_robot=True,
            task_input_bindings=(
                TaskInputBinding(
                    "floor",
                    (("floor",),),
                    required=False,
                    coercion="integer",
                ),
            ),
            resource_locks=("perception_pipeline",),
            success_evidence=("victim_assessment_report",),
            operator_started_message=lambda _inputs: "正在评估被困人员状态。",
            metadata={
                "kind": "composite",
                "primitive_capability": "perception",
                "requires_approval": False,
            },
        )
    )
    catalog.register(
        define_physical_skill_plugin(
            plugin_id="fireclaw.communication.status-report",
            name="report_status",
            label="Report status",
            description="Report status to operator.",
            parameters=LOCAL_CONTEXT_INPUT_SCHEMA,
            output_schema=REPORT_OUTPUT_SCHEMA,
            action="report_status",
            action_input_builder=_current_floor_inputs,
            domain="communication",
            safety_class="reporting",
            preconditions=("robot_online",),
            degraded_mode_policy="retry",
            idempotent=True,
            allow_real_robot=True,
            robot_agent_supplemental=True,
            task_input_bindings=(
                TaskInputBinding(
                    "floor",
                    (("floor",),),
                    required=False,
                    coercion="integer",
                ),
            ),
            resource_locks=("operator_channel",),
            success_evidence=("operator_report_delivered",),
            operator_started_message=lambda _inputs: "正在向操作员报告现场状态。",
            metadata={
                "kind": "primitive",
                "primitive_capability": "communication",
                "requires_approval": False,
            },
        )
    )
    catalog.register(
        define_physical_skill_plugin(
            plugin_id="fireclaw.safety.return-safe-zone",
            name="return_to_safe_zone",
            label="Return to safe zone",
            description="Return robot to safe zone.",
            parameters=EMPTY_INPUT_SCHEMA,
            output_schema=EMPTY_OUTPUT_SCHEMA,
            action="return_to_safe_zone",
            action_input_builder=_empty_inputs,
            domain="safety",
            safety_class="motion",
            risk_level="medium",
            preconditions=("robot_online",),
            degraded_mode_policy="abort",
            idempotent=True,
            allow_real_robot=True,
            robot_agent_supplemental=True,
            resource_locks=("robot_motion", "local_navigation"),
            success_evidence=("safe_zone_reached",),
            operator_started_message=lambda _inputs: "正在返回安全区域。",
            metadata={
                "kind": "composite",
                "primitive_capability": "navigation",
                "requires_approval": False,
            },
        )
    )
    return catalog


_BUILTIN_CATALOG = builtin_physical_skill_catalog()


def get_builtin_physical_skill(name: str):
    return _BUILTIN_CATALOG.get(name)


def builtin_physical_skill_names() -> set[str]:
    return _BUILTIN_CATALOG.names()


def auto_skills_for_target(target: dict[str, Any]) -> list[str]:
    return _BUILTIN_CATALOG.auto_skills_for_target(target)


def skill_chain_for_capability(capability: str) -> list[str]:
    return _BUILTIN_CATALOG.skill_chain_for_capability(capability)


def task_type_for_capability(capability: str) -> str:
    return _BUILTIN_CATALOG.task_type_for_capability(capability)


def supplemental_physical_skill_names() -> tuple[str, ...]:
    return _BUILTIN_CATALOG.supplemental_skill_names()


def builtin_physical_action_names() -> set[str]:
    return {
        plugin.action for plugin in _BUILTIN_CATALOG.plugins.values()
    }


def iter_builtin_physical_skills():
    return tuple(_BUILTIN_CATALOG.plugins.values())
