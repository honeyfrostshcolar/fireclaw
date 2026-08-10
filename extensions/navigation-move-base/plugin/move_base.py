"""OpenClaw-shaped move_base navigation plugin.

The navigation runtime stays outside the Agent loop.  This module contributes
typed, bounded Tools to the shared Plugin Host and translates those calls to a
trusted backend.  The backend may be a ROS1 dynamic-reconfigure adapter or an
in-memory simulator used by tests.

The LLM never receives a ROS namespace, parameter-server handle, shell command,
or arbitrary parameter key.  It first sees ``move_base_parameter_catalog`` and
then may submit only the fixed scopes and parameter names in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from math import cos, isfinite, sin
from threading import RLock
from time import monotonic
from typing import Any, Literal, Mapping, Protocol, Sequence

from fireclaw_plugin_sdk import (
    DeploymentMode,
    PhysicalToolSpec,
    TaskInputBindingSpec,
    ToolSpec,
)


MoveBaseScope = Literal["move_base", "dwa", "local_costmap", "global_costmap"]
MoveBaseClearScope = Literal["local", "global", "both"]
MoveBaseParameterType = Literal["number", "integer", "boolean"]

MOVE_BASE_PLUGIN_ID = "fireclaw.navigation.move-base"
MOVE_BASE_SCOPES: tuple[MoveBaseScope, ...] = (
    "move_base",
    "dwa",
    "local_costmap",
    "global_costmap",
)
MOVE_BASE_CLEAR_SCOPES: tuple[MoveBaseClearScope, ...] = (
    "local",
    "global",
    "both",
)


@dataclass(frozen=True)
class MoveBaseParameterSpec:
    """One host-approved dynamic-reconfigure parameter."""

    name: str
    scope: MoveBaseScope
    value_type: MoveBaseParameterType
    description: str
    minimum: int | float | None = None
    maximum: int | float | None = None

    def schema(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self.value_type,
            "description": self.description,
        }
        if self.minimum is not None:
            result["minimum"] = self.minimum
        if self.maximum is not None:
            result["maximum"] = self.maximum
        return result

    def to_dict(self, *, mutable: bool) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "scope": self.scope,
            "type": self.value_type,
            "description": self.description,
            "mutable": mutable,
        }
        if self.minimum is not None:
            result["minimum"] = self.minimum
        if self.maximum is not None:
            result["maximum"] = self.maximum
        return result


def _spec(
    name: str,
    scope: MoveBaseScope,
    value_type: MoveBaseParameterType,
    description: str,
    minimum: int | float | None = None,
    maximum: int | float | None = None,
) -> MoveBaseParameterSpec:
    return MoveBaseParameterSpec(
        name=name,
        scope=scope,
        value_type=value_type,
        description=description,
        minimum=minimum,
        maximum=maximum,
    )


# This is the intentionally finite FireClaw contract.  It covers the
# navigation tuning knobs that are useful in simulation and excludes frames,
# topics, footprint, plugin class names, hardware limits, and credentials.
MOVE_BASE_PARAMETER_SPECS: tuple[MoveBaseParameterSpec, ...] = (
    _spec("controller_frequency", "move_base", "number", "Controller loop frequency in Hz.", 0.0, 100.0),
    _spec("planner_frequency", "move_base", "number", "Global planner loop frequency in Hz.", 0.0, 100.0),
    _spec("planner_patience", "move_base", "number", "Seconds allowed for global planning.", 0.0, 600.0),
    _spec("controller_patience", "move_base", "number", "Seconds allowed without valid local control.", 0.0, 600.0),
    _spec("max_planning_retries", "move_base", "integer", "Maximum global-planner retries before recovery; -1 means unlimited.", -1, 1000),
    _spec("oscillation_timeout", "move_base", "number", "Seconds before oscillation recovery is triggered.", 0.0, 600.0),
    _spec("oscillation_distance", "move_base", "number", "Distance required to reset oscillation detection in meters.", 0.0, 100.0),
    _spec("conservative_reset_dist", "move_base", "number", "Distance used by conservative costmap reset in meters.", 0.0, 100.0),
    _spec("recovery_behavior_enabled", "move_base", "boolean", "Whether move_base may run configured recovery behaviors."),
    _spec("clearing_rotation_allowed", "move_base", "boolean", "Whether recovery may rotate to clear space."),
    _spec("shutdown_costmaps", "move_base", "boolean", "Whether costmaps shut down when move_base is inactive."),
    _spec("make_plan_clear_costmap", "move_base", "boolean", "Whether make_plan clears the global costmap."),
    _spec("make_plan_add_unreachable_goal", "move_base", "boolean", "Whether make_plan adds an unreachable original goal."),
    _spec("restore_defaults", "move_base", "boolean", "Restore move_base dynamic-reconfigure defaults; simulation experiments only."),
    _spec("max_vel_x", "dwa", "number", "Maximum forward velocity in meters per second.", 0.0, 5.0),
    _spec("min_vel_x", "dwa", "number", "Minimum forward velocity in meters per second.", -5.0, 5.0),
    _spec("max_vel_y", "dwa", "number", "Maximum lateral velocity in meters per second.", 0.0, 5.0),
    _spec("min_vel_y", "dwa", "number", "Minimum lateral velocity in meters per second.", -5.0, 5.0),
    _spec("max_vel_theta", "dwa", "number", "Maximum angular velocity in radians per second.", 0.0, 20.0),
    _spec("min_vel_theta", "dwa", "number", "Minimum angular velocity in radians per second.", -20.0, 20.0),
    _spec("acc_lim_x", "dwa", "number", "Maximum forward acceleration in meters per second squared.", 0.0, 20.0),
    _spec("acc_lim_y", "dwa", "number", "Maximum lateral acceleration in meters per second squared.", 0.0, 20.0),
    _spec("acc_lim_theta", "dwa", "number", "Maximum angular acceleration in radians per second squared.", 0.0, 40.0),
    _spec("sim_time", "dwa", "number", "Forward simulation horizon in seconds.", 0.1, 20.0),
    _spec("sim_granularity", "dwa", "number", "Linear simulation granularity in meters.", 0.001, 1.0),
    _spec("angular_sim_granularity", "dwa", "number", "Angular simulation granularity in radians.", 0.001, 1.0),
    _spec("vx_samples", "dwa", "integer", "Number of forward velocity samples.", 1, 200),
    _spec("vy_samples", "dwa", "integer", "Number of lateral velocity samples.", 1, 200),
    _spec("vth_samples", "dwa", "integer", "Number of angular velocity samples.", 1, 400),
    _spec("path_distance_bias", "dwa", "number", "Weight for staying near the global path.", 0.0, 1000.0),
    _spec("goal_distance_bias", "dwa", "number", "Weight for progressing toward the local goal.", 0.0, 1000.0),
    _spec("occdist_scale", "dwa", "number", "Weight for obstacle clearance.", 0.0, 1000.0),
    _spec("twirling_scale", "dwa", "number", "Weight for penalizing heading changes.", 0.0, 1000.0),
    _spec("forward_point_distance", "dwa", "number", "Distance of the forward scoring point in meters.", 0.0, 20.0),
    _spec("stop_time_buffer", "dwa", "number", "Required stopping-time buffer in seconds.", 0.0, 20.0),
    _spec("scaling_speed", "dwa", "number", "Speed at which footprint scaling begins.", 0.0, 5.0),
    _spec("max_scaling_factor", "dwa", "number", "Maximum dynamic footprint scaling factor.", 0.0, 10.0),
    _spec("xy_goal_tolerance", "dwa", "number", "Position tolerance for a navigation goal in meters.", 0.0, 10.0),
    _spec("yaw_goal_tolerance", "dwa", "number", "Heading tolerance for a navigation goal in radians.", 0.0, 6.283185307179586),
    _spec("trans_stopped_vel", "dwa", "number", "Translational stopped threshold.", 0.0, 2.0),
    _spec("rot_stopped_vel", "dwa", "number", "Rotational stopped threshold.", 0.0, 5.0),
    _spec("oscillation_reset_dist", "dwa", "number", "Distance required to reset local planner oscillation detection.", 0.0, 100.0),
    _spec("oscillation_reset_angle", "dwa", "number", "Angle required to reset local planner oscillation detection.", 0.0, 6.283185307179586),
    _spec("use_dwa", "dwa", "boolean", "Whether DWA constrains samples to the dynamic window."),
    _spec("restore_defaults", "dwa", "boolean", "Restore DWA dynamic-reconfigure defaults; simulation experiments only."),
    _spec("inflation_radius", "local_costmap", "number", "Local costmap inflation radius in meters.", 0.0, 20.0),
    _spec("cost_scaling_factor", "local_costmap", "number", "Local costmap inflation cost scaling factor.", 0.0, 100.0),
    _spec("obstacle_range", "local_costmap", "number", "Maximum local obstacle observation range in meters.", 0.0, 200.0),
    _spec("raytrace_range", "local_costmap", "number", "Maximum local free-space raytrace range in meters.", 0.0, 200.0),
    _spec("update_frequency", "local_costmap", "number", "Local costmap update frequency in Hz.", 0.0, 100.0),
    _spec("publish_frequency", "local_costmap", "number", "Local costmap publish frequency in Hz.", 0.0, 100.0),
    _spec("transform_tolerance", "local_costmap", "number", "Local costmap TF tolerance in seconds.", 0.0, 10.0),
    _spec("enabled", "local_costmap", "boolean", "Whether the local costmap plugin is enabled."),
    _spec("inflate_unknown", "local_costmap", "boolean", "Whether unknown local costmap cells are inflated."),
    _spec("inflation_radius", "global_costmap", "number", "Global costmap inflation radius in meters.", 0.0, 20.0),
    _spec("cost_scaling_factor", "global_costmap", "number", "Global costmap inflation cost scaling factor.", 0.0, 100.0),
    _spec("obstacle_range", "global_costmap", "number", "Maximum global obstacle observation range in meters.", 0.0, 200.0),
    _spec("raytrace_range", "global_costmap", "number", "Maximum global free-space raytrace range in meters.", 0.0, 200.0),
    _spec("update_frequency", "global_costmap", "number", "Global costmap update frequency in Hz.", 0.0, 100.0),
    _spec("publish_frequency", "global_costmap", "number", "Global costmap publish frequency in Hz.", 0.0, 100.0),
    _spec("transform_tolerance", "global_costmap", "number", "Global costmap TF tolerance in seconds.", 0.0, 10.0),
    _spec("enabled", "global_costmap", "boolean", "Whether the global costmap plugin is enabled."),
    _spec("inflate_unknown", "global_costmap", "boolean", "Whether unknown global costmap cells are inflated."),
)


def _parameter_specs_by_name() -> dict[str, tuple[MoveBaseParameterSpec, ...]]:
    result: dict[str, list[MoveBaseParameterSpec]] = {}
    for spec in MOVE_BASE_PARAMETER_SPECS:
        result.setdefault(spec.name, []).append(spec)
    return {name: tuple(items) for name, items in result.items()}


_SPECS_BY_NAME = _parameter_specs_by_name()
_ALL_PARAMETER_NAMES = tuple(sorted(_SPECS_BY_NAME))


class MoveBaseNavigationBackend(Protocol):
    """Trusted adapter interface behind the LLM-facing navigation Tools."""

    def get_status(self) -> Mapping[str, Any]:
        ...

    def navigate_to_point(
        self,
        x: float,
        y: float,
        yaw: float = 0.0,
        frame_id: str = "map",
        feedback_sink: Any = None,
        cancellation_requested: Any = None,
    ) -> Mapping[str, Any]:
        ...

    def get_parameters(
        self,
        scope: MoveBaseScope,
        names: Sequence[str] | None = None,
    ) -> Mapping[str, Any]:
        ...

    def set_parameters(
        self,
        scope: MoveBaseScope,
        parameters: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...

    def cancel_navigation(self, reason: str | None = None) -> Mapping[str, Any]:
        ...

    def clear_costmaps(self, scope: MoveBaseClearScope = "both") -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class MoveBaseParameterPolicy:
    """Mode-specific parameter mutation policy.

    Simulation exposes every parameter in the finite catalog.  Real mode is
    closed by default and can be opened only with an explicit parameter
    allowlist; the deployment policy still requires exact operator approval for
    every real bounded mutation.
    """

    mode: DeploymentMode
    real_mutation_enabled: bool = False
    real_mutable_parameters: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.mode not in {"simulation", "real"}:
            raise ValueError("move_base policy mode must be simulation or real")
        valid_keys = {
            *(_ALL_PARAMETER_NAMES),
            *{
                f"{spec.scope}/{spec.name}"
                for spec in MOVE_BASE_PARAMETER_SPECS
            },
        }
        unknown = set(self.real_mutable_parameters) - valid_keys
        if unknown:
            raise ValueError(
                "Unknown real move_base parameters: "
                + ", ".join(sorted(unknown))
            )

    @property
    def mutation_enabled(self) -> bool:
        return self.mode == "simulation" or self.real_mutation_enabled

    def mutable(self, spec: MoveBaseParameterSpec) -> bool:
        if self.mode == "simulation":
            return True
        return self.real_mutation_enabled and (
            spec.name in self.real_mutable_parameters
            or f"{spec.scope}/{spec.name}" in self.real_mutable_parameters
        )

    def specs(self, scope: MoveBaseScope | None = None) -> tuple[MoveBaseParameterSpec, ...]:
        return tuple(
            spec
            for spec in MOVE_BASE_PARAMETER_SPECS
            if scope is None or spec.scope == scope
        )

    def validate_updates(
        self,
        scope: MoveBaseScope,
        parameters: Mapping[str, Any],
    ) -> list[str]:
        errors: list[str] = []
        if not isinstance(parameters, Mapping) or not parameters:
            return ["parameters must be a non-empty object"]
        scope_specs = {spec.name: spec for spec in self.specs(scope)}
        for name, value in parameters.items():
            if name not in scope_specs:
                if name in _SPECS_BY_NAME:
                    errors.append(
                        f"parameter {name!r} belongs to another move_base scope"
                    )
                else:
                    errors.append(f"parameter {name!r} is not in the move_base catalog")
                continue
            spec = scope_specs[name]
            if not self.mutable(spec):
                errors.append(
                    f"parameter {name!r} is not mutable in {self.mode} mode"
                )
                continue
            errors.extend(_validate_parameter_value(spec, value))
        errors.extend(_validate_velocity_order(parameters, scope))
        if self.mode == "real" and not self.real_mutation_enabled:
            errors.append("real move_base parameter mutation is disabled by policy")
        return errors

    def catalog(self, scope: MoveBaseScope | None = None) -> list[dict[str, Any]]:
        return [
            spec.to_dict(mutable=self.mutable(spec))
            for spec in self.specs(scope)
        ]


def _validate_parameter_value(
    spec: MoveBaseParameterSpec,
    value: Any,
) -> list[str]:
    errors: list[str] = []
    if spec.value_type == "boolean":
        if not isinstance(value, bool):
            return [f"parameter {spec.name!r} must be boolean"]
        return []
    if spec.value_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return [f"parameter {spec.name!r} must be integer"]
    elif not isinstance(value, (int, float)) or isinstance(value, bool):
        return [f"parameter {spec.name!r} must be number"]
    if not isfinite(float(value)):
        errors.append(f"parameter {spec.name!r} must be finite")
    if spec.minimum is not None and value < spec.minimum:
        errors.append(f"parameter {spec.name!r} must be at least {spec.minimum}")
    if spec.maximum is not None and value > spec.maximum:
        errors.append(f"parameter {spec.name!r} must be at most {spec.maximum}")
    return errors


def _validate_velocity_order(
    parameters: Mapping[str, Any],
    scope: MoveBaseScope,
) -> list[str]:
    if scope != "dwa":
        return []
    errors: list[str] = []
    for minimum, maximum in (
        ("min_vel_x", "max_vel_x"),
        ("min_vel_y", "max_vel_y"),
        ("min_vel_theta", "max_vel_theta"),
    ):
        if minimum not in parameters or maximum not in parameters:
            continue
        if parameters[minimum] > parameters[maximum]:
            errors.append(
                f"parameter {minimum!r} must not exceed {maximum!r}"
            )
    return errors


class InMemoryMoveBaseBackend:
    """Deterministic simulation backend for plugin and LLM contract tests."""

    def __init__(
        self,
        *,
        status: Mapping[str, Any] | None = None,
        parameters: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self._lock = RLock()
        self.calls: list[dict[str, Any]] = []
        self.status = dict(status or {"status": "idle", "goal_active": False})
        self.parameters: dict[str, dict[str, Any]] = {
            scope: {} for scope in MOVE_BASE_SCOPES
        }
        for scope, values in (parameters or {}).items():
            if scope in self.parameters:
                self.parameters[scope].update(dict(values))

    def get_status(self) -> Mapping[str, Any]:
        with self._lock:
            self.calls.append({"operation": "get_status"})
            return {"status": "succeeded", **dict(self.status)}

    def navigate_to_point(
        self,
        x: float,
        y: float,
        yaw: float = 0.0,
        frame_id: str = "map",
        feedback_sink: Any = None,
        cancellation_requested: Any = None,
    ) -> Mapping[str, Any]:
        target = {
            "x": float(x),
            "y": float(y),
            "yaw": float(yaw),
            "frame_id": str(frame_id),
        }
        with self._lock:
            self.calls.append({"operation": "navigate_to_point", **target})
            self.status.update(
                {
                    "status": "succeeded",
                    "goal_active": False,
                    "target": target,
                }
            )
        if callable(feedback_sink):
            feedback_sink({"progress": 1.0, "message": "navigation goal reached", **target})
        if callable(cancellation_requested) and cancellation_requested():
            return {
                "status": "cancelled",
                "cancelled": True,
                "cancellation_acknowledged": True,
                "runtime_stopped": True,
                "resource_release_safe": True,
                **target,
            }
        return {
            "status": "succeeded",
            "goal_reached": True,
            "runtime_stopped": True,
            "resource_release_safe": True,
            **target,
        }

    def get_parameters(
        self,
        scope: MoveBaseScope,
        names: Sequence[str] | None = None,
    ) -> Mapping[str, Any]:
        with self._lock:
            self.calls.append({"operation": "get_parameters", "scope": scope, "names": list(names or [])})
            values = dict(self.parameters[scope])
            if names:
                values = {name: values.get(name) for name in names}
            return {"status": "succeeded", "scope": scope, "parameters": values}

    def set_parameters(
        self,
        scope: MoveBaseScope,
        parameters: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        with self._lock:
            values = dict(parameters)
            self.calls.append({"operation": "set_parameters", "scope": scope, "parameters": values})
            self.parameters[scope].update(values)
            return {"status": "succeeded", "scope": scope, "updated": values}

    def cancel_navigation(self, reason: str | None = None) -> Mapping[str, Any]:
        with self._lock:
            self.calls.append({"operation": "cancel_navigation", "reason": reason})
            self.status.update({"status": "cancelled", "goal_active": False})
            return {"status": "succeeded", "cancelled": True, "reason": reason}

    def clear_costmaps(self, scope: MoveBaseClearScope = "both") -> Mapping[str, Any]:
        with self._lock:
            self.calls.append({"operation": "clear_costmaps", "scope": scope})
            return {"status": "succeeded", "scope": scope, "cleared": True}


@dataclass
class Ros1MoveBaseBackend:
    """Lazy ROS1 adapter for dynamic_reconfigure and move_base control.

    ROS modules are imported only when a Tool is actually called.  FireClaw
    can therefore load and test the plugin on machines without ROS installed.
    The LLM can select only the fixed scope mapping below; it cannot provide a
    ROS namespace or service name.
    """

    action_name: str = "/move_base"
    clear_costmaps_service: str = "/move_base/clear_costmaps"
    scope_namespaces: dict[MoveBaseScope, str] = field(
        default_factory=lambda: {
            "move_base": "/move_base",
            "dwa": "/move_base/DWAPlannerROS",
            "local_costmap": "/move_base/local_costmap",
            "global_costmap": "/move_base/global_costmap",
        }
    )
    timeout_seconds: float = 2.0
    cancellation_ack_timeout_seconds: float = 2.0
    _clients: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _action_client: Any | None = field(default=None, init=False, repr=False)

    def _rospy(self) -> Any:
        return import_module("rospy")

    def _dynamic_client(self, scope: MoveBaseScope) -> Any:
        client = self._clients.get(scope)
        if client is not None:
            return client
        module = import_module("dynamic_reconfigure.client")
        namespace = self.scope_namespaces[scope]
        client = module.Client(namespace, timeout=self.timeout_seconds)
        self._clients[scope] = client
        return client

    def _action(self) -> Any:
        if self._action_client is None:
            module = import_module("actionlib")
            messages = import_module("move_base_msgs.msg")
            self._action_client = module.SimpleActionClient(
                self.action_name,
                messages.MoveBaseAction,
            )
        return self._action_client

    def get_status(self) -> Mapping[str, Any]:
        client = self._action()
        if not client.wait_for_server(timeout=self._rospy().Duration(self.timeout_seconds)):
            return {"status": "error", "error_code": "move_base_unavailable"}
        state = int(client.get_state())
        return {
            "status": "succeeded",
            "goal_state": state,
            "goal_state_name": _ACTION_STATE_NAMES.get(state, "unknown"),
            "goal_active": state in {0, 1, 6, 7},
            "action_name": self.action_name,
        }

    def navigate_to_point(
        self,
        x: float,
        y: float,
        yaw: float = 0.0,
        frame_id: str = "map",
        feedback_sink: Any = None,
        cancellation_requested: Any = None,
    ) -> Mapping[str, Any]:
        rospy = self._rospy()
        goal_type = import_module("move_base_msgs.msg").MoveBaseGoal
        goal = goal_type()
        goal.target_pose.header.frame_id = str(frame_id)
        goal.target_pose.header.stamp = rospy.Time.now()
        goal.target_pose.pose.position.x = float(x)
        goal.target_pose.pose.position.y = float(y)
        goal.target_pose.pose.orientation.z = sin(float(yaw) / 2.0)
        goal.target_pose.pose.orientation.w = cos(float(yaw) / 2.0)
        client = self._action()

        def on_feedback(feedback: Any) -> None:
            if callable(feedback_sink):
                feedback_sink(
                    {
                        "message": "move_base feedback",
                        "feedback_type": type(feedback).__name__,
                    }
                )

        client.send_goal(goal, feedback_cb=on_feedback)
        while not client.wait_for_result(rospy.Duration(0.1)):
            if callable(cancellation_requested) and cancellation_requested():
                return self._cancel_goal_and_wait(
                    client=client,
                    rospy=rospy,
                    cancellation_requested=cancellation_requested,
                    target={
                        "x": float(x),
                        "y": float(y),
                        "yaw": float(yaw),
                        "frame_id": str(frame_id),
                    },
                )
        state = int(client.get_state())
        status = _ACTION_STATE_NAMES.get(state, "unknown")
        try:
            goal_status_text = str(client.get_goal_status_text() or "")
        except Exception:
            goal_status_text = ""
        runtime_stopped = state in _ACTION_STOP_CONFIRMED_STATES
        result = {
            "status": (
                "succeeded"
                if state == 3
                else "cancelled"
                if state in {2, 8}
                else "lost"
                if state == 9
                else status
            ),
            "goal_reached": state == 3,
            "goal_state": state,
            "goal_state_name": status,
            "goal_status_text": goal_status_text,
            "cancellation_acknowledged": state in {2, 8},
            "runtime_stopped": runtime_stopped,
            "resource_release_safe": runtime_stopped,
            "x": float(x),
            "y": float(y),
            "yaw": float(yaw),
            "frame_id": str(frame_id),
        }
        error_code = _ACTION_FAILURE_CODES.get(state)
        if error_code is not None:
            result["error_code"] = error_code
        return result

    def _cancel_goal_and_wait(
        self,
        *,
        client: Any,
        rospy: Any,
        cancellation_requested: Any,
        target: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        client.cancel_goal()
        deadline = _cancellation_ack_deadline(
            cancellation_requested,
            fallback_seconds=self.cancellation_ack_timeout_seconds,
        )
        reason = getattr(cancellation_requested, "reason", None)
        return self._wait_for_cancel_acknowledgement(
            client=client,
            rospy=rospy,
            deadline=deadline,
            reason=reason,
            acknowledged_status="cancelled",
            target=target,
        )

    def _wait_for_cancel_acknowledgement(
        self,
        *,
        client: Any,
        rospy: Any,
        deadline: float,
        reason: str | None,
        acknowledged_status: str,
        target: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        state: int | None = None
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            if client.wait_for_result(
                rospy.Duration(min(0.05, remaining))
            ):
                state = int(client.get_state())
                acknowledged = state in _ACTION_STOP_CONFIRMED_STATES
                return {
                    "status": acknowledged_status if acknowledged else "lost",
                    "cancelled": acknowledged,
                    "cancellation_reason": reason,
                    "cancellation_acknowledged": acknowledged,
                    "runtime_stopped": acknowledged,
                    "resource_release_safe": acknowledged,
                    "goal_state": state,
                    "goal_state_name": _ACTION_STATE_NAMES.get(
                        state,
                        "unknown",
                    ),
                    **dict(target),
                }
        try:
            state = int(client.get_state())
        except Exception:
            state = None
        return {
            "status": "lost",
            "cancelled": False,
            "cancellation_reason": reason,
            "cancellation_acknowledged": False,
            "runtime_stopped": False,
            "resource_release_safe": False,
            "goal_state": state,
            "goal_state_name": _ACTION_STATE_NAMES.get(
                state,
                "unknown",
            ),
            "error_code": "move_base_cancel_unacknowledged",
            **dict(target),
        }

    def get_parameters(
        self,
        scope: MoveBaseScope,
        names: Sequence[str] | None = None,
    ) -> Mapping[str, Any]:
        configuration = self._dynamic_client(scope).get_configuration()
        values = _dynamic_configuration_to_dict(configuration)
        if names:
            values = {name: values.get(name) for name in names}
        return {"status": "succeeded", "scope": scope, "parameters": values}

    def set_parameters(
        self,
        scope: MoveBaseScope,
        parameters: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        result = self._dynamic_client(scope).update_configuration(dict(parameters))
        return {
            "status": "succeeded",
            "scope": scope,
            "updated": dict(parameters),
            "configuration": _dynamic_configuration_to_dict(result),
        }

    def cancel_navigation(self, reason: str | None = None) -> Mapping[str, Any]:
        client = self._action()
        client.cancel_all_goals()
        return self._wait_for_cancel_acknowledgement(
            client=client,
            rospy=self._rospy(),
            deadline=monotonic() + self.cancellation_ack_timeout_seconds,
            reason=reason,
            acknowledged_status="succeeded",
            target={},
        )

    def clear_costmaps(self, scope: MoveBaseClearScope = "both") -> Mapping[str, Any]:
        rospy = self._rospy()
        service_type = import_module("std_srvs.srv").Empty
        # move_base exposes one clear_costmaps service which clears both
        # layers.  Keep the logical scope in the Tool contract for audit
        # purposes rather than inventing untrusted layer-specific endpoints.
        rospy.ServiceProxy(self.clear_costmaps_service, service_type)()
        return {"status": "succeeded", "scope": scope, "cleared": True}


_ACTION_STATE_NAMES = {
    0: "pending",
    1: "active",
    2: "preempted",
    3: "succeeded",
    4: "aborted",
    5: "rejected",
    6: "preempting",
    7: "recalling",
    8: "recalled",
    9: "lost",
}
_ACTION_STOP_CONFIRMED_STATES = frozenset({2, 3, 4, 5, 8})
_ACTION_FAILURE_CODES = {
    4: "move_base_aborted",
    5: "move_base_rejected",
}


def _cancellation_ack_deadline(
    cancellation_requested: Any,
    *,
    fallback_seconds: float,
) -> float:
    remaining_provider = getattr(
        cancellation_requested,
        "remaining_ack_seconds",
        None,
    )
    if callable(remaining_provider):
        try:
            remaining = float(remaining_provider())
        except (TypeError, ValueError):
            remaining = fallback_seconds
        if isfinite(remaining):
            return monotonic() + max(0.0, remaining)
    requested_deadline = getattr(
        cancellation_requested,
        "cancellation_ack_deadline_monotonic",
        None,
    )
    if (
        isinstance(requested_deadline, (int, float))
        and isfinite(float(requested_deadline))
    ):
        return float(requested_deadline)
    return monotonic() + fallback_seconds


def _dynamic_configuration_to_dict(configuration: Any) -> dict[str, Any]:
    """Convert dynamic_reconfigure Config or a mapping to JSON-safe values."""
    if isinstance(configuration, Mapping):
        return {
            str(key): value
            for key, value in configuration.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
    values: dict[str, Any] = {}
    for field_name in ("bools", "ints", "strs", "doubles"):
        entries = getattr(configuration, field_name, ())
        for entry in entries or ():
            name = getattr(entry, "name", None)
            value = getattr(entry, "value", None)
            if isinstance(name, str) and (isinstance(value, (str, int, float, bool)) or value is None):
                values[name] = value
    return values


def _tool_schema_for_parameters() -> dict[str, Any]:
    properties = {
        spec.name: spec.schema()
        for spec in MOVE_BASE_PARAMETER_SPECS
    }
    return {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": list(MOVE_BASE_SCOPES),
            },
            "parameters": {
                "type": "object",
                "properties": properties,
                "additionalProperties": False,
                "minProperties": 1,
            },
        },
        "required": ["scope", "parameters"],
        "additionalProperties": False,
    }


def _tool_schema_for_get_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": list(MOVE_BASE_SCOPES),
            },
            "names": {
                "type": "array",
                "items": {"type": "string", "enum": list(_ALL_PARAMETER_NAMES)},
                "maxItems": 64,
            },
        },
        "required": ["scope"],
        "additionalProperties": False,
    }


def _tool_schema_for_cancel() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "maxLength": 256},
        },
        "additionalProperties": False,
    }


def _tool_schema_for_clear_costmaps() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": list(MOVE_BASE_CLEAR_SCOPES),
                "default": "both",
            },
        },
        "additionalProperties": False,
    }


def _navigation_point_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "x": {"type": "number"},
            "y": {"type": "number"},
            "yaw": {"type": "number", "default": 0.0},
            "frame_id": {
                "type": "string",
                "minLength": 1,
                "default": "map",
            },
        },
        "required": ["x", "y"],
        "additionalProperties": False,
    }


def _navigation_point_inputs(arguments: dict[str, Any]) -> dict[str, Any]:
    x = float(arguments["x"])
    y = float(arguments["y"])
    yaw = float(arguments.get("yaw", 0.0))
    if not all(isfinite(value) for value in (x, y, yaw)):
        raise ValueError("navigation point coordinates must be finite")
    frame_id = str(arguments.get("frame_id", "map")).strip()
    if not frame_id:
        raise ValueError("frame_id must not be empty")
    return {"x": x, "y": y, "yaw": yaw, "frame_id": frame_id}


def move_base_navigation_physical_tools(
    backend: MoveBaseNavigationBackend,
    *,
    timeout_seconds: float = 120.0,
    cancellation_ack_timeout_seconds: float = 2.0,
) -> tuple[PhysicalToolSpec, ...]:
    """Build Plugin-owned physical navigation Tool contracts.

    This is intentionally separate from the six read/tuning Tools below.  A
    physical Tool is still projected through the shared lifecycle and safety
    runtime, but its actual handler is owned by this extension backend.
    """

    return (
        PhysicalToolSpec(
            plugin_id=MOVE_BASE_PLUGIN_ID,
            name="navigate_to_point",
            label="Navigate to point",
            description=(
                "Navigate within the current single-floor map to a target "
                "point expressed in an explicit coordinate frame."
            ),
            input_schema=_navigation_point_schema(),
            output_schema={
                "type": "object",
                "properties": {
                    "robot_id": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "yaw": {"type": "number"},
                    "frame_id": {"type": "string"},
                },
                "required": ["x", "y", "yaw", "frame_id"],
            },
            action="navigate_to_point",
            input_builder=_navigation_point_inputs,
            handler=lambda arguments, **kwargs: backend.navigate_to_point(
                **_navigation_point_inputs(arguments),
                feedback_sink=kwargs.get("feedback_sink"),
                cancellation_requested=kwargs.get("cancellation_requested"),
            ),
            domain="navigation",
            safety_class="motion",
            required_sensors=("lidar",),
            preconditions=("robot_online", "target_point_reachable"),
            degraded_mode_policy="retry",
            idempotent=True,
            allow_real_robot=True,
            timeout_seconds=timeout_seconds,
            cancellation_ack_timeout_seconds=(
                cancellation_ack_timeout_seconds
            ),
            task_input_bindings=(
                TaskInputBindingSpec(
                    "x", (("pose", "x"),), required=True, coercion="float"
                ),
                TaskInputBindingSpec(
                    "y", (("pose", "y"),), required=True, coercion="float"
                ),
                TaskInputBindingSpec(
                    "yaw", (("pose", "yaw"),), default=0.0, coercion="float"
                ),
                TaskInputBindingSpec(
                    "frame_id",
                    (("frame_id",), ("pose", "frame_id")),
                    default="map",
                    coercion="string",
                ),
            ),
            auto_include_for_target=True,
            resource_locks=("robot_motion", "local_navigation"),
            success_evidence=("navigation_goal_reached",),
            operator_started_message=lambda inputs: (
                f"正在前往 {inputs.get('frame_id') or 'map'} 坐标系中的目标点 "
                f"({inputs.get('x')}, {inputs.get('y')})。"
            ),
            metadata={
                "kind": "primitive",
                "primitive_capability": "navigation",
                "spatial_scope": "single_floor_2d",
                "plugin_owned_physical_handler": True,
            },
        ),
    )


def _safe_backend_call(callback, *args, **kwargs) -> dict[str, Any]:
    try:
        output = callback(*args, **kwargs)
    except Exception as exc:  # Backend boundary fails closed without payloads.
        return {
            "status": "error",
            "error_code": "move_base_backend_error",
            "message": f"move_base backend failed with {type(exc).__name__}",
        }
    if isinstance(output, Mapping):
        return {str(key): value for key, value in output.items()}
    return {
        "status": "error",
        "error_code": "move_base_backend_invalid_result",
        "message": "move_base backend returned a non-object result",
    }


def move_base_navigation_agent_tools(
    backend: MoveBaseNavigationBackend,
    *,
    policy: MoveBaseParameterPolicy,
    mutation_modes: tuple[DeploymentMode, ...],
) -> tuple[ToolSpec, ...]:
    """Build the tool contributions for one Gateway's fixed deployment mode."""

    common = {
        "roles": ("robot_agent",),
        "requires_sandbox": False,
        "metadata": {
            "family": "navigation_move_base",
            "runtime": "ros1_move_base",
            "parameter_policy": "finite_catalog",
        },
        "max_execution_seconds": 10.0,
        "max_result_bytes": 128 * 1024,
    }
    return (
        ToolSpec(
            name="move_base_parameter_catalog",
            description=(
                "List the fixed move_base tuning parameters that FireClaw "
                "allows the Robot Agent to inspect or modify in the current "
                "deployment mode. This is the required first step before "
                "parameter tuning."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": list(MOVE_BASE_SCOPES),
                    },
                },
                "additionalProperties": False,
            },
            handler=lambda arguments: {
                "status": "succeeded",
                "mode": policy.mode,
                "parameters": policy.catalog(arguments.get("scope")),
            },
            effect="read",
            modes=("simulation", "real"),
            **common,
        ),
        ToolSpec(
            name="move_base_navigation_status",
            description=(
                "Read the current structured move_base action status. "
                "Use this after a navigation timeout or unexpected motion."
            ),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=lambda _arguments: _safe_backend_call(backend.get_status),
            effect="read",
            modes=("simulation", "real"),
            **common,
        ),
        ToolSpec(
            name="move_base_get_parameters",
            description=(
                "Read current values for one fixed move_base scope. "
                "Parameter names must come from move_base_parameter_catalog."
            ),
            input_schema=_tool_schema_for_get_parameters(),
            handler=lambda arguments: _get_parameters(backend, policy, arguments),
            effect="read",
            modes=("simulation", "real"),
            **common,
        ),
        ToolSpec(
            name="move_base_set_parameters",
            description=(
                "Apply bounded tuning values to one move_base scope. In "
                "simulation every parameter in the fixed catalog is allowed. "
                "In real mode the backend additionally requires the exact "
                "operator authorization and the configured real allowlist."
            ),
            input_schema=_tool_schema_for_parameters(),
            handler=lambda arguments: _set_parameters(backend, policy, arguments),
            effect="bounded_mutation",
            modes=mutation_modes,
            **common,
        ),
        ToolSpec(
            name="move_base_cancel_navigation",
            description=(
                "Cancel the active move_base goal through the trusted adapter. "
                "This is a bounded control operation, never a raw velocity command."
            ),
            input_schema=_tool_schema_for_cancel(),
            handler=lambda arguments: _safe_backend_call(
                backend.cancel_navigation,
                arguments.get("reason"),
            ),
            effect="bounded_mutation",
            modes=mutation_modes,
            **common,
        ),
        ToolSpec(
            name="move_base_clear_costmaps",
            description=(
                "Request a bounded clear of local, global, or both move_base "
                "costmaps. Never changes map files or costmap geometry."
            ),
            input_schema=_tool_schema_for_clear_costmaps(),
            handler=lambda arguments: _safe_backend_call(
                backend.clear_costmaps,
                arguments.get("scope", "both"),
            ),
            effect="bounded_mutation",
            modes=mutation_modes,
            **common,
        ),
    )


def _get_parameters(
    backend: MoveBaseNavigationBackend,
    policy: MoveBaseParameterPolicy,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scope = arguments.get("scope")
    names = arguments.get("names")
    if scope not in MOVE_BASE_SCOPES:
        return {"status": "blocked", "error_code": "invalid_move_base_scope"}
    if names is not None:
        invalid = [
            name
            for name in names
            if not any(spec.name == name and spec.scope == scope for spec in policy.specs(scope))
        ]
        if invalid:
            return {
                "status": "blocked",
                "error_code": "parameter_not_in_scope",
                "parameters": invalid,
            }
    return _safe_backend_call(backend.get_parameters, scope, names)


def _set_parameters(
    backend: MoveBaseNavigationBackend,
    policy: MoveBaseParameterPolicy,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scope = arguments.get("scope")
    parameters = arguments.get("parameters")
    if scope not in MOVE_BASE_SCOPES:
        return {"status": "blocked", "error_code": "invalid_move_base_scope"}
    if not isinstance(parameters, Mapping):
        return {"status": "blocked", "error_code": "parameters_must_be_object"}
    errors = policy.validate_updates(scope, parameters)
    if errors:
        return {
            "status": "blocked",
            "error_code": "move_base_parameter_policy_rejected",
            "errors": errors,
            "mode": policy.mode,
        }
    return _safe_backend_call(backend.set_parameters, scope, parameters)


def move_base_parameter_catalog(
    *,
    mode: DeploymentMode = "simulation",
    real_mutation_enabled: bool = False,
    real_mutable_parameters: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Return the catalog without constructing a backend (useful for docs/tests)."""
    policy = MoveBaseParameterPolicy(
        mode=mode,
        real_mutation_enabled=real_mutation_enabled,
        real_mutable_parameters=frozenset(real_mutable_parameters),
    )
    return policy.catalog()
