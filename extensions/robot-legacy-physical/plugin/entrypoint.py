"""Compatibility physical Tools for legacy robot profiles.

New capabilities should be separate domain Plugins.  These contracts keep
existing task/checkpoint names working while their adapter implementation is
selected through the generic ``robot_action_dispatch`` service.
"""

from __future__ import annotations

from typing import Any

from fireclaw_plugin_sdk import (
    PhysicalToolSpec,
    PluginApi,
    TaskInputBindingSpec,
)


_LOCAL_CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "floor": {
            "type": "integer",
            "minimum": 1,
            "description": "Legacy compatibility field.",
        }
    },
    "additionalProperties": False,
}
_FLOOR_SCHEMA = {
    "type": "object",
    "properties": {"floor": {"type": "integer", "minimum": 1}},
    "required": ["floor"],
    "additionalProperties": False,
}
_EMPTY_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
_EMPTY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {},
}
_NAVIGATE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "from_floor": {"type": "integer"},
    },
    "required": ["robot_id", "floor"],
}
_SEARCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "victims_found": {"type": "integer"},
    },
    "required": ["robot_id", "floor", "victims_found"],
}
_ASSESS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "condition": {"type": "string"},
    },
    "required": ["robot_id", "floor", "condition"],
}
_REPORT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "robot_id": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "floor": {"type": "integer"},
        "message": {"type": "string"},
    },
    "required": ["robot_id", "floor", "message"],
}


def _dispatch(
    dispatcher: Any,
    action: str,
    arguments: dict[str, Any],
    **kwargs: Any,
) -> Any:
    if not callable(dispatcher):
        return {
            "status": "blocked",
            "error": "robot_action_dispatch service is unavailable",
            "action": action,
        }
    return dispatcher(action, dict(arguments), **kwargs)


def _floor(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"floor": int(arguments["floor"])}


def _current_floor(arguments: dict[str, Any]) -> dict[str, Any]:
    value = arguments.get("floor")
    # Legacy task profiles often omit the optional floor for a search in the
    # robot's current single-floor operating area.  The old core contract
    # normalized that omission to floor 1; retain that compatibility default
    # in the Plugin-owned input contract.
    return {
        "floor": int(value)
        if isinstance(value, int) and not isinstance(value, bool)
        else 1
    }


def _empty(_arguments: dict[str, Any]) -> dict[str, Any]:
    return {}


def _legacy_tools(dispatcher: Any) -> tuple[PhysicalToolSpec, ...]:
    def handler(action: str):
        return lambda arguments, **kwargs: _dispatch(
            dispatcher,
            action,
            arguments,
            **kwargs,
        )

    return (
        PhysicalToolSpec(
            plugin_id="fireclaw.robot.legacy-physical",
            name="navigate_to_floor",
            label="Navigate to floor (legacy)",
            description="Navigate robot to a target floor.",
            input_schema=_FLOOR_SCHEMA,
            output_schema=_NAVIGATE_OUTPUT_SCHEMA,
            action="navigate_to_floor",
            handler=handler("navigate_to_floor"),
            input_builder=_floor,
            domain="navigation",
            safety_class="motion",
            required_sensors=("lidar",),
            preconditions=("robot_online", "floor_reachable"),
            degraded_mode_policy="retry",
            idempotent=True,
            allow_real_robot=True,
            task_input_bindings=(
                TaskInputBindingSpec("floor", (("floor",),), required=True, coercion="integer"),
            ),
            resource_locks=("robot_motion", "floor_transition"),
            success_evidence=("target_floor_reached",),
            operator_started_message=lambda inputs: f"正在前往{inputs.get('floor', '目标')}楼。",
            metadata={
                "kind": "primitive",
                "primitive_capability": "navigation",
                "legacy": True,
                "spatial_scope": "multi_floor_extension",
                "requires_approval": False,
            },
        ),
        PhysicalToolSpec(
            plugin_id="fireclaw.robot.legacy-physical",
            name="search_for_victims",
            label="Search for victims",
            description="Search for victims in the current operating area.",
            input_schema=_LOCAL_CONTEXT_SCHEMA,
            output_schema=_SEARCH_OUTPUT_SCHEMA,
            action="search_for_victims",
            handler=handler("search_for_victims"),
            input_builder=_current_floor,
            domain="perception",
            safety_class="victim_perception",
            required_sensors=("rgb_camera",),
            sensor_alternatives={"rgb_camera": ("thermal_camera",)},
            preconditions=("robot_online", "camera_available"),
            degraded_mode_policy="fallback",
            allow_real_robot=True,
            task_type="search",
            default_followup_tools=("report_status",),
            task_input_bindings=(
                TaskInputBindingSpec("floor", (("floor",),), coercion="integer"),
            ),
            resource_locks=("perception_pipeline",),
            success_evidence=("victim_search_report",),
            operator_started_message=lambda inputs: (
                f"正在搜索{inputs['floor']}楼被困人员。"
                if inputs.get("floor") is not None
                else "正在搜索当前目标区域的被困人员。"
            ),
            metadata={
                "kind": "composite",
                "primitive_capability": "perception",
                "requires_approval": False,
            },
        ),
        PhysicalToolSpec(
            plugin_id="fireclaw.robot.legacy-physical",
            name="assess_victim",
            label="Assess victim",
            description="Assess victim condition in the current operating area.",
            input_schema=_LOCAL_CONTEXT_SCHEMA,
            output_schema=_ASSESS_OUTPUT_SCHEMA,
            action="assess_victim",
            handler=handler("assess_victim"),
            input_builder=_current_floor,
            domain="perception",
            safety_class="victim_perception",
            required_sensors=("rgb_camera", "thermal_camera"),
            preconditions=("robot_online", "victim_detected"),
            degraded_mode_policy="skip",
            allow_real_robot=True,
            task_input_bindings=(
                TaskInputBindingSpec("floor", (("floor",),), coercion="integer"),
            ),
            resource_locks=("perception_pipeline",),
            success_evidence=("victim_assessment_report",),
            operator_started_message=lambda _inputs: "正在评估被困人员状态。",
            metadata={
                "kind": "composite",
                "primitive_capability": "perception",
                "requires_approval": False,
            },
        ),
        PhysicalToolSpec(
            plugin_id="fireclaw.robot.legacy-physical",
            name="report_status",
            label="Report status",
            description="Report status to operator.",
            input_schema=_LOCAL_CONTEXT_SCHEMA,
            output_schema=_REPORT_OUTPUT_SCHEMA,
            action="report_status",
            handler=handler("report_status"),
            input_builder=_current_floor,
            domain="communication",
            safety_class="reporting",
            preconditions=("robot_online",),
            degraded_mode_policy="retry",
            idempotent=True,
            allow_real_robot=True,
            robot_agent_supplemental=True,
            task_input_bindings=(
                TaskInputBindingSpec("floor", (("floor",),), coercion="integer"),
            ),
            resource_locks=("operator_channel",),
            success_evidence=("operator_report_delivered",),
            operator_started_message=lambda _inputs: "正在向操作员报告现场状态。",
            metadata={
                "kind": "primitive",
                "primitive_capability": "communication",
                "requires_approval": False,
            },
        ),
        PhysicalToolSpec(
            plugin_id="fireclaw.robot.legacy-physical",
            name="return_to_safe_zone",
            label="Return to safe zone",
            description="Return robot to safe zone.",
            input_schema=_EMPTY_SCHEMA,
            output_schema=_EMPTY_OUTPUT_SCHEMA,
            action="return_to_safe_zone",
            handler=handler("return_to_safe_zone"),
            input_builder=_empty,
            domain="safety",
            safety_class="motion",
            risk_level="medium",
            preconditions=("robot_online",),
            degraded_mode_policy="abort",
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
        ),
    )


def register(api: PluginApi) -> None:
    if api.role != "robot_agent" or not bool(api.config.get("enabled", True)):
        return
    dispatcher = api.services.get("robot_action_dispatch")
    for tool in _legacy_tools(dispatcher):
        api.register_physical_tool(tool)
