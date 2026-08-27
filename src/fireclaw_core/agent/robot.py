from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any
from typing import Protocol

from fireclaw_core.ros.ros1_config import Ros1AdapterConfig
from fireclaw_core.ros.ros1_config import Ros1EndpointConfig
from fireclaw_core.ros.ros1_template import render_ros1_template
from fireclaw_core.ros.ros1_transport import Ros1Transport
from fireclaw_core.ros.ros1_runtime import Ros1RuntimeLifecycle
from fireclaw_core.sensors.backends import SensorDiscoveryBackend, ensure_real_mode_backend_allowed


@dataclass
class RobotActionResult:
    ok: bool
    status: str
    robot_id: str
    mode: str
    action: str
    dry_run: bool
    data: dict[str, Any]
    timestamp: str
    error: str | None = None


@dataclass
class RobotState:
    robot_id: str
    mode: str
    dry_run: bool
    online: bool
    battery_percent: float | None
    current_floor: int | None
    available_sensors: list[str] | None
    supports_real_execution: bool
    sensor_diagnostics: dict[str, Any] | None = None
    pose: dict[str, Any] | None = None


@dataclass
class EnvironmentState:
    reachable_floors: list[int] | None
    hazards: list[str] = field(default_factory=list)
    victims_by_floor: dict[int, int] = field(default_factory=dict)


def validate_simulator_real_separation(
    adapter_mode: str,
    dry_run: bool,
    action: str,
    *,
    allow_real: bool = False,
) -> str | None:
    """Return an error message if simulator/real-robot separation is violated.

    Returns None if the combination is valid.
    """
    if adapter_mode == "simulator" and not dry_run:
        return "Simulator adapter must not execute real actions (dry_run=False on simulator)."
    if adapter_mode in {"ros1", "ros2"} and not allow_real and not dry_run:
        return f"Real adapter ({adapter_mode}) requires allow_real_robot=True for non-dry-run execution."
    return None


class RobotAdapter(Protocol):
    """Robot state and fail-safe boundary used by the FireClaw core.

    Domain actions are intentionally absent. Plugins own their typed Tools,
    runtime backends, feedback, cancellation, and terminal-result mapping.
    """

    robot_id: str
    mode: str
    dry_run: bool

    def emergency_stop(self, reason: str | None = None) -> RobotActionResult:
        ...

    def get_robot_state(self) -> RobotState:
        ...

    def get_environment_state(self) -> EnvironmentState:
        ...


@dataclass
class DryRunRobotAdapter:
    robot_id: str
    dry_run: bool = True
    mode: str = "dry_run"
    current_floor: int = 1
    available_sensors: list[str] = field(default_factory=lambda: ["rgb_camera", "thermal_camera", "lidar"])
    reachable_floors: list[int] = field(default_factory=lambda: [1])
    victims_by_floor: dict[int, int] = field(default_factory=lambda: {1: 1})
    emergency_stopped: bool = False
    emergency_stop_reason: str | None = None
    initial_pose: dict[str, Any] | None = field(
        default_factory=lambda: {"x": -2.0, "y": -0.5, "yaw": 0.0, "frame_id": "map"}
    )

    def get_robot_state(self) -> RobotState:
        return RobotState(
            robot_id=self.robot_id,
            mode=self.mode,
            dry_run=self.dry_run,
            online=not self.emergency_stopped,
            battery_percent=100.0,
            current_floor=self.current_floor,
            available_sensors=list(self.available_sensors),
            supports_real_execution=False,
            pose=dict(self.initial_pose) if self.initial_pose is not None else None,
        )

    def emergency_stop(self, reason: str | None = None) -> RobotActionResult:
        self.emergency_stopped = True
        self.emergency_stop_reason = reason
        return _emergency_stop_result(self.robot_id, self.mode, self.dry_run, reason)

    def get_environment_state(self) -> EnvironmentState:
        return EnvironmentState(
            reachable_floors=list(self.reachable_floors),
            hazards=[],
            victims_by_floor=dict(self.victims_by_floor),
        )


@dataclass
class Ros1CommandSpec:
    interface: str
    name: str
    action: str
    payload: dict[str, Any]
    cancel_supported: bool = True
    feedback_supported: bool = False


@dataclass
class MockRos1RobotAdapter:
    robot_id: str
    dry_run: bool = True
    mode: str = "mock_ros1"
    current_floor: int = 1
    available_sensors: list[str] = field(default_factory=lambda: ["rgb_camera", "thermal_camera", "lidar"])
    reachable_floors: list[int] = field(default_factory=lambda: [1])
    victims_by_floor: dict[int, int] = field(default_factory=lambda: {1: 1})
    emergency_stopped: bool = False
    emergency_stop_reason: str | None = None
    initial_pose: dict[str, Any] | None = field(
        default_factory=lambda: {"x": -2.0, "y": -0.5, "yaw": 0.0, "frame_id": "map"}
    )

    def get_robot_state(self) -> RobotState:
        return RobotState(
            robot_id=self.robot_id,
            mode=self.mode,
            dry_run=self.dry_run,
            online=not self.emergency_stopped,
            battery_percent=100.0,
            current_floor=self.current_floor,
            available_sensors=list(self.available_sensors),
            supports_real_execution=False,
            pose=dict(self.initial_pose) if self.initial_pose is not None else None,
        )

    def emergency_stop(self, reason: str | None = None) -> RobotActionResult:
        self.emergency_stopped = True
        self.emergency_stop_reason = reason
        return _emergency_stop_result(self.robot_id, self.mode, self.dry_run, reason)

    def get_environment_state(self) -> EnvironmentState:
        return EnvironmentState(
            reachable_floors=list(self.reachable_floors),
            hazards=[],
            victims_by_floor=dict(self.victims_by_floor),
        )


def _discovered_sensor_state(
    *,
    backend: SensorDiscoveryBackend | None,
    mode: str,
    dry_run: bool,
) -> tuple[list[str] | None, dict[str, Any] | None]:
    if backend is None:
        return None, None
    ensure_real_mode_backend_allowed(backend, mode=mode, dry_run=dry_run)
    report = backend.discover()
    return report.verified_sensors(), report.to_dict()


@dataclass
class Ros1RobotAdapter:
    config: Ros1AdapterConfig
    transport: Ros1Transport | None = None
    commands: list[Ros1CommandSpec] = field(default_factory=list)
    dry_run: bool = False
    mode: str = "ros1"
    current_floor: int | None = None
    available_sensors: list[str] = field(default_factory=list)
    sensor_discovery: SensorDiscoveryBackend | None = None
    emergency_stopped: bool = False
    emergency_stop_reason: str | None = None
    pose_map_frame: str = "map"
    pose_base_frame: str = "base_footprint"
    pose_lookup_timeout_seconds: float = 0.5
    pose_startup_timeout_seconds: float = 2.0
    # Compatibility-only fallback for explicitly configured ROS dry runs. A
    # real adapter must never present a configured pose as live TF evidence.
    initial_pose: dict[str, Any] | None = None
    runtime_lifecycle: Ros1RuntimeLifecycle = field(
        default_factory=Ros1RuntimeLifecycle,
        repr=False,
    )
    _tf_buffer: Any | None = field(default=None, init=False, repr=False)
    _tf_listener: Any | None = field(default=None, init=False, repr=False)
    _pose_lookup_attempted: bool = field(default=False, init=False, repr=False)
    _last_pose_observed_at: str | None = field(
        default=None,
        init=False,
        repr=False,
    )

    @property
    def robot_id(self) -> str:
        return self.config.robot_id

    def start_runtime(self) -> dict[str, Any]:
        """Initialize this process's ROS node before Gateway request intake."""

        snapshot = self.runtime_lifecycle.start()
        if snapshot.status == "ready":
            deadline = time.monotonic() + max(
                0.0,
                float(self.pose_startup_timeout_seconds),
            )
            while True:
                remaining = max(0.0, deadline - time.monotonic())
                pose = self._lookup_pose(
                    timeout_seconds=min(
                        max(0.0, float(self.pose_lookup_timeout_seconds)),
                        remaining,
                    )
                )
                if pose is not None or remaining <= 0:
                    break
                time.sleep(min(0.05, remaining))
        return self.runtime_state()

    def stop_runtime(self) -> dict[str, Any]:
        snapshot = self.runtime_lifecycle.stop()
        self._tf_listener = None
        self._tf_buffer = None
        return {
            **snapshot.to_dict(),
            "pose_status": "unavailable",
            "pose_frame": self.pose_map_frame,
            "last_pose_observed_at": self._last_pose_observed_at,
        }

    def runtime_state(self) -> dict[str, Any]:
        snapshot = self.runtime_lifecycle.snapshot()
        return {
            **snapshot.to_dict(),
            "pose_status": (
                "ready"
                if self._last_pose_observed_at is not None
                else "unavailable"
                if self._pose_lookup_attempted
                else "not_observed"
            ),
            "pose_frame": self.pose_map_frame,
            "last_pose_observed_at": self._last_pose_observed_at,
        }

    def _lookup_pose(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any] | None:
        self._pose_lookup_attempted = True
        try:
            from importlib import import_module
            from math import atan2
            rospy = import_module("rospy")
            if not rospy.core.is_initialized():
                # Node lifecycle belongs to the live ROS runtime. Initializing
                # it from a read-only state probe can block indefinitely while
                # the ROS master is unavailable.
                return (
                    dict(self.initial_pose)
                    if self.dry_run and self.initial_pose is not None
                    else None
                )
            tf2_ros = import_module("tf2_ros")
            if self._tf_buffer is None or self._tf_listener is None:
                self._tf_buffer = tf2_ros.Buffer()
                self._tf_listener = tf2_ros.TransformListener(self._tf_buffer)
            base_frames = tuple(
                dict.fromkeys((self.pose_base_frame, "base_footprint", "base_link"))
            )
            total_timeout = (
                max(0.0, float(self.pose_lookup_timeout_seconds))
                if timeout_seconds is None
                else max(0.0, float(timeout_seconds))
            )
            timeout_per_frame = total_timeout / len(
                base_frames
            )
            for base_frame in base_frames:
                try:
                    transform = self._tf_buffer.lookup_transform(
                        self.pose_map_frame,
                        base_frame,
                        rospy.Time(0),
                        rospy.Duration(timeout_per_frame),
                    )
                    translation = transform.transform.translation
                    rotation = transform.transform.rotation
                    yaw = atan2(
                        2.0 * (rotation.w * rotation.z + rotation.x * rotation.y),
                        1.0 - 2.0 * (rotation.y**2 + rotation.z**2),
                    )
                    pose = {
                        "x": float(translation.x),
                        "y": float(translation.y),
                        "yaw": float(yaw),
                        "frame_id": str(
                            transform.header.frame_id or self.pose_map_frame
                        ),
                    }
                    self._last_pose_observed_at = datetime.now(
                        timezone.utc
                    ).isoformat()
                    return pose
                except Exception:
                    continue
        except Exception:
            pass
        return (
            dict(self.initial_pose)
            if self.dry_run and self.initial_pose is not None
            else None
        )

    def get_robot_state(self) -> RobotState:
        import logging

        sensor_diagnostics: dict[str, Any] | None = None
        available_sensors: list[str] | None = None
        if self.sensor_discovery is not None:
            try:
                available_sensors, sensor_diagnostics = _discovered_sensor_state(
                    backend=self.sensor_discovery,
                    mode=self.mode,
                    dry_run=self.dry_run,
                )
            except ValueError:
                raise
            except Exception:
                logging.warning("Sensor discovery failed, falling back to static sensors", exc_info=True)
                if self.available_sensors:
                    available_sensors = list(self.available_sensors)
        elif self.available_sensors:
            available_sensors = list(self.available_sensors)
        runtime_status = self.runtime_lifecycle.snapshot().status
        return RobotState(
            robot_id=self.robot_id,
            mode=self.mode,
            dry_run=self.dry_run,
            online=(
                not self.emergency_stopped
                and runtime_status not in {"degraded", "stopped"}
            ),
            battery_percent=None,
            current_floor=self.current_floor,
            available_sensors=available_sensors,
            supports_real_execution=True,
            sensor_diagnostics=sensor_diagnostics,
            pose=self._lookup_pose(),
        )

    def emergency_stop(self, reason: str | None = None) -> RobotActionResult:
        self.emergency_stopped = True
        self.emergency_stop_reason = reason
        endpoint = None
        if self.config.emergency_stop is not None:
            endpoint = Ros1EndpointConfig(
                interface=self.config.emergency_stop.interface,
                name=self.config.emergency_stop.name,
                type=self.config.emergency_stop.type,
            )
        return self._execute_emergency_stop(endpoint=endpoint, reason=reason)

    def get_environment_state(self) -> EnvironmentState:
        return EnvironmentState(reachable_floors=None)

    def _execute_emergency_stop(
        self,
        *,
        endpoint: Ros1EndpointConfig | None,
        reason: str | None,
    ) -> RobotActionResult:
        action = "emergency_stop"
        payload = {"reason": reason}
        timestamp = datetime.now(timezone.utc).isoformat()
        if endpoint is None:
            return RobotActionResult(
                ok=False,
                status="not_configured",
                robot_id=self.robot_id,
                mode=self.mode,
                action=action,
                dry_run=self.dry_run,
                data={"robot_id": self.robot_id, "dry_run": self.dry_run, **payload},
                timestamp=timestamp,
                error=f"ROS1 endpoint for {action} is not configured.",
            )
        command = Ros1CommandSpec(
            interface=endpoint.interface,
            name=endpoint.name,
            action=action,
            payload=payload,
            cancel_supported=endpoint.cancel_supported,
            feedback_supported=endpoint.feedback_supported,
        )
        self.commands.append(command)
        template = endpoint.goal_template if endpoint.interface == "action" else endpoint.request_template
        ros1_payload = render_ros1_template(
            template or payload,
            inputs=payload,
            targets={},
        )
        base_data = {
            "robot_id": self.robot_id,
            "dry_run": self.dry_run,
            "ros1_interface": endpoint.interface,
            "ros1_name": endpoint.name,
            "ros1_type": endpoint.type,
            "ros1_profile": endpoint.profile,
            "goal_template": dict(endpoint.goal_template),
            "request_template": dict(endpoint.request_template),
            "ros1_payload": ros1_payload,
            **payload,
        }
        if self.dry_run:
            return RobotActionResult(
                ok=True,
                status="succeeded",
                robot_id=self.robot_id,
                mode=self.mode,
                action=action,
                dry_run=True,
                data=base_data,
                timestamp=timestamp,
            )
        if self.config.transport.enabled:
            try:
                transport = self.transport or Ros1Transport()
                transport_result = transport.execute(
                    endpoint,
                    ros1_payload,
                    self.config.transport,
                )
            except Exception as exc:
                return RobotActionResult(
                    ok=False,
                    status="failed",
                    robot_id=self.robot_id,
                    mode=self.mode,
                    action=action,
                    dry_run=self.dry_run,
                    data=base_data,
                    timestamp=timestamp,
                    error=str(exc),
                )
            status = transport_result.get("status", "failed")
            ok = status == "succeeded"
            return RobotActionResult(
                ok=ok,
                status=status,
                robot_id=self.robot_id,
                mode=self.mode,
                action=action,
                dry_run=self.dry_run,
                data={**base_data, "ros1_response": transport_result.get("response")},
                timestamp=timestamp,
                error=transport_result.get("error"),
            )
        return RobotActionResult(
            ok=False,
            status="not_configured",
            robot_id=self.robot_id,
            mode=self.mode,
            action=action,
            dry_run=self.dry_run,
            data=base_data,
            timestamp=timestamp,
            error="ROS1 transport is disabled by configuration. Set transport.enabled=true to execute live ROS1 commands.",
        )


@dataclass
class MockRos2RobotAdapter:
    robot_id: str
    dry_run: bool = True
    mode: str = "mock_ros2"
    current_floor: int = 1
    available_sensors: list[str] = field(default_factory=lambda: ["rgb_camera", "thermal_camera", "lidar"])
    reachable_floors: list[int] = field(default_factory=lambda: [1])
    victims_by_floor: dict[int, int] = field(default_factory=lambda: {1: 1})
    emergency_stopped: bool = False
    emergency_stop_reason: str | None = None
    initial_pose: dict[str, Any] | None = field(
        default_factory=lambda: {"x": -2.0, "y": -0.5, "yaw": 0.0, "frame_id": "map"}
    )

    def get_robot_state(self) -> RobotState:
        return RobotState(
            robot_id=self.robot_id,
            mode=self.mode,
            dry_run=self.dry_run,
            online=not self.emergency_stopped,
            battery_percent=100.0,
            current_floor=self.current_floor,
            available_sensors=list(self.available_sensors),
            supports_real_execution=False,
            pose=dict(self.initial_pose) if self.initial_pose is not None else None,
        )

    def emergency_stop(self, reason: str | None = None) -> RobotActionResult:
        self.emergency_stopped = True
        self.emergency_stop_reason = reason
        return _emergency_stop_result(self.robot_id, self.mode, self.dry_run, reason)

    def get_environment_state(self) -> EnvironmentState:
        return EnvironmentState(
            reachable_floors=list(self.reachable_floors),
            hazards=[],
            victims_by_floor=dict(self.victims_by_floor),
        )


@dataclass
class SimulatorRobotAdapter:
    robot_id: str
    current_floor: int = 1
    reachable_floors: list[int] = field(default_factory=lambda: [1])
    victims_by_floor: dict[int, int] = field(default_factory=lambda: {1: 1})
    hazards: list[str] = field(default_factory=list)
    available_sensors: list[str] = field(default_factory=lambda: ["rgb_camera", "thermal_camera", "lidar"])
    online: bool = True
    battery_percent: float = 100.0
    dry_run: bool = True
    mode: str = "simulator"
    emergency_stopped: bool = False
    emergency_stop_reason: str | None = None
    sensor_discovery: SensorDiscoveryBackend | None = None
    initial_pose: dict[str, Any] | None = field(
        default_factory=lambda: {"x": -2.0, "y": -0.5, "yaw": 0.0, "frame_id": "map"}
    )

    def get_robot_state(self) -> RobotState:
        sensor_diagnostics: dict[str, Any] | None = None
        available_sensors = list(self.available_sensors)
        if self.sensor_discovery is not None:
            discovered_sensors, sensor_diagnostics = _discovered_sensor_state(
                backend=self.sensor_discovery,
                mode=self.mode,
                dry_run=self.dry_run,
            )
            if discovered_sensors is not None:
                available_sensors = discovered_sensors
        return RobotState(
            robot_id=self.robot_id,
            mode=self.mode,
            dry_run=self.dry_run,
            online=self.online and not self.emergency_stopped,
            battery_percent=float(self.battery_percent),
            current_floor=self.current_floor,
            available_sensors=available_sensors,
            supports_real_execution=False,
            sensor_diagnostics=sensor_diagnostics,
            pose=dict(self.initial_pose) if self.initial_pose is not None else None,
        )

    def emergency_stop(self, reason: str | None = None) -> RobotActionResult:
        self.emergency_stopped = True
        self.emergency_stop_reason = reason
        self.online = False
        return _emergency_stop_result(self.robot_id, self.mode, self.dry_run, reason)

    def get_environment_state(self) -> EnvironmentState:
        return EnvironmentState(
            reachable_floors=list(self.reachable_floors),
            hazards=list(self.hazards),
            victims_by_floor=dict(self.victims_by_floor),
        )


def _emergency_stop_result(
    robot_id: str,
    mode: str,
    dry_run: bool,
    reason: str | None,
) -> RobotActionResult:
    timestamp = datetime.now(timezone.utc).isoformat()
    return RobotActionResult(
        ok=True,
        status="emergency_stopped",
        robot_id=robot_id,
        mode=mode,
        action="emergency_stop",
        dry_run=dry_run,
        data={
            "robot_id": robot_id,
            "dry_run": dry_run,
            "emergency_stopped": True,
            "reason": reason,
        },
        timestamp=timestamp,
    )
