from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from typing import Protocol

from fireclaw_core.ros.ros1_config import Ros1AdapterConfig
from fireclaw_core.ros.ros1_config import Ros1EndpointConfig
from fireclaw_core.ros.ros1_template import render_ros1_template
from fireclaw_core.ros.ros1_transport import FeedbackSink, CancellationCheck, Ros1Transport
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


@dataclass
class EnvironmentState:
    reachable_floors: list[int] | None
    hazards: list[str] = field(default_factory=list)
    victims_by_floor: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterCapabilities:
    """Declares what an adapter can do and under what constraints."""
    supported_actions: set[str]
    supported_modes: set[str]
    supports_dry_run: bool
    supports_real_execution: bool
    supports_feedback: bool
    supports_cancellation: bool
    max_concurrent_actions: int
    required_sensors: list[str]
    is_simulator: bool


ALL_ROBOT_ACTIONS = {
    "navigate_to_floor",
    "search_for_victims",
    "assess_victim",
    "report_status",
    "return_to_safe_zone",
    "emergency_stop",
}


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
    robot_id: str
    mode: str
    dry_run: bool

    def navigate_to_floor(self, floor: int) -> RobotActionResult:
        ...

    def search_for_victims(self, floor: int) -> RobotActionResult:
        ...

    def assess_victim(self, floor: int) -> RobotActionResult:
        ...

    def report_status(self, floor: int) -> RobotActionResult:
        ...

    def return_to_safe_zone(self) -> RobotActionResult:
        ...

    def emergency_stop(self, reason: str | None = None) -> RobotActionResult:
        ...

    def get_robot_state(self) -> RobotState:
        ...

    def get_environment_state(self) -> EnvironmentState:
        ...

    def capabilities(self) -> AdapterCapabilities:
        ...


@dataclass
class DryRunRobotAdapter:
    robot_id: str
    fail_actions: set[str] = field(default_factory=set)
    actions: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = True
    mode: str = "dry_run"
    current_floor: int = 1
    available_sensors: list[str] = field(default_factory=lambda: ["rgb_camera", "thermal_camera", "lidar"])
    reachable_floors: list[int] = field(default_factory=lambda: [1, 2, 3])
    victims_by_floor: dict[int, int] = field(default_factory=lambda: {2: 1})
    emergency_stopped: bool = False
    emergency_stop_reason: str | None = None

    def navigate_to_floor(self, floor: int) -> RobotActionResult:
        return self._record("navigate_to_floor", {"floor": floor})

    def search_for_victims(self, floor: int) -> RobotActionResult:
        return self._record("search_for_victims", {"floor": floor, "victims_found": 1})

    def assess_victim(self, floor: int) -> RobotActionResult:
        return self._record("assess_victim", {"floor": floor, "condition": "needs_assistance"})

    def report_status(self, floor: int) -> RobotActionResult:
        return self._record("report_status", {"floor": floor, "message": "victim located"})

    def return_to_safe_zone(self) -> RobotActionResult:
        return self._record("return_to_safe_zone", {})

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

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supported_actions=set(ALL_ROBOT_ACTIONS),
            supported_modes={"dry_run"},
            supports_dry_run=True,
            supports_real_execution=False,
            supports_feedback=False,
            supports_cancellation=False,
            max_concurrent_actions=1,
            required_sensors=list(self.available_sensors),
            is_simulator=False,
        )

    def _record(self, action: str, data: dict[str, Any]) -> RobotActionResult:
        action_record = {"action": action, **{key: value for key, value in data.items() if key == "floor"}, "dry_run": True}
        self.actions.append(action_record)
        timestamp = datetime.now(timezone.utc).isoformat()
        if action in self.fail_actions:
            return RobotActionResult(
                ok=False,
                status="failed",
                robot_id=self.robot_id,
                mode=self.mode,
                action=action,
                dry_run=True,
                data=data,
                timestamp=timestamp,
                error=f"{action} failed in dry-run adapter",
            )
        return RobotActionResult(
            ok=True,
            status="succeeded",
            robot_id=self.robot_id,
            mode=self.mode,
            action=action,
            dry_run=True,
            data={"robot_id": self.robot_id, "dry_run": True, **data},
            timestamp=timestamp,
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
    commands: list[Ros1CommandSpec] = field(default_factory=list)
    dry_run: bool = True
    mode: str = "mock_ros1"
    current_floor: int = 1
    available_sensors: list[str] = field(default_factory=lambda: ["rgb_camera", "thermal_camera", "lidar"])
    reachable_floors: list[int] = field(default_factory=lambda: [1, 2, 3])
    victims_by_floor: dict[int, int] = field(default_factory=lambda: {2: 1})
    emergency_stopped: bool = False
    emergency_stop_reason: str | None = None

    def navigate_to_floor(self, floor: int) -> RobotActionResult:
        return self._record(
            name=f"/fireclaw/{self.robot_id}/navigation",
            action="navigate_to_floor",
            payload={"floor": floor},
            feedback_supported=True,
        )

    def search_for_victims(self, floor: int) -> RobotActionResult:
        return self._record(
            name=f"/fireclaw/{self.robot_id}/perception",
            action="search_for_victims",
            payload={"floor": floor, "victims_found": 1},
        )

    def assess_victim(self, floor: int) -> RobotActionResult:
        return self._record(
            name=f"/fireclaw/{self.robot_id}/perception",
            action="assess_victim",
            payload={"floor": floor, "condition": "needs_assistance"},
        )

    def report_status(self, floor: int) -> RobotActionResult:
        return self._record(
            name=f"/fireclaw/{self.robot_id}/operator_report",
            action="report_status",
            payload={"floor": floor, "message": "victim located"},
            cancel_supported=False,
        )

    def return_to_safe_zone(self) -> RobotActionResult:
        return self._record(
            name=f"/fireclaw/{self.robot_id}/navigation",
            action="return_to_safe_zone",
            payload={},
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

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supported_actions=set(ALL_ROBOT_ACTIONS),
            supported_modes={"mock_ros1"},
            supports_dry_run=True,
            supports_real_execution=False,
            supports_feedback=True,
            supports_cancellation=True,
            max_concurrent_actions=1,
            required_sensors=list(self.available_sensors),
            is_simulator=False,
        )

    def action_feedback(self, action_type: str, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        if action_type != "navigate_to_floor":
            return []
        floor = int(inputs["floor"])
        return [
            {
                "progress": 0.25,
                "message": "leaving safe zone",
                "current_floor": self.current_floor,
                "target_floor": floor,
            },
            {
                "progress": 0.75,
                "message": "near target floor",
                "current_floor": self.current_floor,
                "target_floor": floor,
            },
        ]

    def _record(
        self,
        *,
        name: str,
        action: str,
        payload: dict[str, Any],
        interface: str = "topic",
        cancel_supported: bool = True,
        feedback_supported: bool = False,
    ) -> RobotActionResult:
        command = Ros1CommandSpec(
            interface=interface,
            name=name,
            action=action,
            payload=payload,
            cancel_supported=cancel_supported,
            feedback_supported=feedback_supported,
        )
        self.commands.append(command)
        timestamp = datetime.now(timezone.utc).isoformat()
        return RobotActionResult(
            ok=True,
            status="succeeded",
            robot_id=self.robot_id,
            mode=self.mode,
            action=action,
            dry_run=True,
            data={
                "robot_id": self.robot_id,
                "dry_run": True,
                "ros1_interface": interface,
                "ros1_name": name,
                **payload,
            },
            timestamp=timestamp,
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

    @property
    def robot_id(self) -> str:
        return self.config.robot_id

    def navigate_to_floor(
        self,
        floor: int,
        feedback_sink: FeedbackSink | None = None,
        cancellation_requested: CancellationCheck | None = None,
    ) -> RobotActionResult:
        return self._record_configured_action(
            "navigate_to_floor",
            {"floor": floor},
            feedback_sink=feedback_sink,
            cancellation_requested=cancellation_requested,
        )

    def search_for_victims(
        self,
        floor: int,
        feedback_sink: FeedbackSink | None = None,
        cancellation_requested: CancellationCheck | None = None,
    ) -> RobotActionResult:
        return self._record_configured_action(
            "search_for_victims",
            {"floor": floor},
            feedback_sink=feedback_sink,
            cancellation_requested=cancellation_requested,
        )

    def assess_victim(
        self,
        floor: int,
        feedback_sink: FeedbackSink | None = None,
        cancellation_requested: CancellationCheck | None = None,
    ) -> RobotActionResult:
        return self._record_configured_action(
            "assess_victim",
            {"floor": floor},
            feedback_sink=feedback_sink,
            cancellation_requested=cancellation_requested,
        )

    def report_status(
        self,
        floor: int,
        feedback_sink: FeedbackSink | None = None,
        cancellation_requested: CancellationCheck | None = None,
    ) -> RobotActionResult:
        return self._record_configured_action(
            "report_status",
            {"floor": floor},
            feedback_sink=feedback_sink,
            cancellation_requested=cancellation_requested,
        )

    def return_to_safe_zone(
        self,
        feedback_sink: FeedbackSink | None = None,
        cancellation_requested: CancellationCheck | None = None,
    ) -> RobotActionResult:
        return self._record_configured_action(
            "return_to_safe_zone",
            {},
            feedback_sink=feedback_sink,
            cancellation_requested=cancellation_requested,
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
        return RobotState(
            robot_id=self.robot_id,
            mode=self.mode,
            dry_run=self.dry_run,
            online=not self.emergency_stopped,
            battery_percent=None,
            current_floor=self.current_floor,
            available_sensors=available_sensors,
            supports_real_execution=True,
            sensor_diagnostics=sensor_diagnostics,
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
        return self._record_configured_action("emergency_stop", {"reason": reason}, endpoint=endpoint)

    def get_environment_state(self) -> EnvironmentState:
        return EnvironmentState(reachable_floors=None)

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supported_actions=set(ALL_ROBOT_ACTIONS),
            supported_modes={"ros1"},
            supports_dry_run=True,
            supports_real_execution=True,
            supports_feedback=True,
            supports_cancellation=True,
            max_concurrent_actions=1,
            required_sensors=list(self.available_sensors),
            is_simulator=False,
        )

    def _record_configured_action(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        endpoint: Ros1EndpointConfig | None = None,
        feedback_sink: FeedbackSink | None = None,
        cancellation_requested: CancellationCheck | None = None,
    ) -> RobotActionResult:
        endpoint = endpoint or self.config.endpoints.get(action)
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
        ros1_payload = render_ros1_template(template or payload, inputs=payload, targets=self.config.targets)
        base_data = {
            "robot_id": self.robot_id,
            "dry_run": self.dry_run,
            "ros1_interface": endpoint.interface,
            "ros1_name": endpoint.name,
            "ros1_type": endpoint.type,
            "ros1_profile": endpoint.profile,
            "goal_template": dict(endpoint.goal_template),
            "request_template": dict(endpoint.request_template),
            "targets": dict(self.config.targets),
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
                transport = self.transport or Ros1Transport(feedback_sink=feedback_sink)
                if transport.feedback_sink is None:
                    transport.feedback_sink = feedback_sink
                transport_result = transport.execute(
                    endpoint,
                    ros1_payload,
                    self.config.transport,
                    cancellation_requested=cancellation_requested,
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
    commands: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = True
    mode: str = "mock_ros2"
    current_floor: int = 1
    available_sensors: list[str] = field(default_factory=lambda: ["rgb_camera", "thermal_camera", "lidar"])
    reachable_floors: list[int] = field(default_factory=lambda: [1, 2, 3])
    victims_by_floor: dict[int, int] = field(default_factory=lambda: {2: 1})
    emergency_stopped: bool = False
    emergency_stop_reason: str | None = None

    def navigate_to_floor(self, floor: int) -> RobotActionResult:
        return self._record(
            topic=f"/fireclaw/{self.robot_id}/navigation",
            action="navigate_to_floor",
            payload={"floor": floor},
        )

    def search_for_victims(self, floor: int) -> RobotActionResult:
        return self._record(
            topic=f"/fireclaw/{self.robot_id}/perception",
            action="search_for_victims",
            payload={"floor": floor, "victims_found": 1},
        )

    def assess_victim(self, floor: int) -> RobotActionResult:
        return self._record(
            topic=f"/fireclaw/{self.robot_id}/perception",
            action="assess_victim",
            payload={"floor": floor, "condition": "needs_assistance"},
        )

    def report_status(self, floor: int) -> RobotActionResult:
        return self._record(
            topic=f"/fireclaw/{self.robot_id}/operator_report",
            action="report_status",
            payload={"floor": floor, "message": "victim located"},
        )

    def return_to_safe_zone(self) -> RobotActionResult:
        return self._record(
            topic=f"/fireclaw/{self.robot_id}/navigation",
            action="return_to_safe_zone",
            payload={},
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

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supported_actions=set(ALL_ROBOT_ACTIONS),
            supported_modes={"mock_ros2"},
            supports_dry_run=True,
            supports_real_execution=False,
            supports_feedback=False,
            supports_cancellation=False,
            max_concurrent_actions=1,
            required_sensors=list(self.available_sensors),
            is_simulator=False,
        )

    def _record(self, *, topic: str, action: str, payload: dict[str, Any]) -> RobotActionResult:
        command = {
            "topic": topic,
            "action": action,
            "payload": payload,
            "dry_run": True,
        }
        self.commands.append(command)
        timestamp = datetime.now(timezone.utc).isoformat()
        return RobotActionResult(
            ok=True,
            status="succeeded",
            robot_id=self.robot_id,
            mode=self.mode,
            action=action,
            dry_run=True,
            data={"robot_id": self.robot_id, "dry_run": True, "topic": topic, **payload},
            timestamp=timestamp,
        )


@dataclass
class SimulatorRobotAdapter:
    robot_id: str
    current_floor: int = 1
    reachable_floors: list[int] = field(default_factory=lambda: [1, 2, 3])
    victims_by_floor: dict[int, int] = field(default_factory=lambda: {2: 1})
    hazards: list[str] = field(default_factory=list)
    available_sensors: list[str] = field(default_factory=lambda: ["rgb_camera", "thermal_camera", "lidar"])
    online: bool = True
    battery_percent: float = 100.0
    dry_run: bool = True
    mode: str = "simulator"
    actions: list[dict[str, Any]] = field(default_factory=list)
    emergency_stopped: bool = False
    emergency_stop_reason: str | None = None
    sensor_discovery: SensorDiscoveryBackend | None = None

    def navigate_to_floor(self, floor: int) -> RobotActionResult:
        from_floor = self.current_floor
        if floor not in self.reachable_floors:
            return self._record(
                "navigate_to_floor",
                {"from_floor": from_floor, "floor": floor},
                ok=False,
                error=f"floor {floor} is unreachable in simulator",
            )
        self.current_floor = floor
        return self._record("navigate_to_floor", {"from_floor": from_floor, "floor": floor})

    def search_for_victims(self, floor: int) -> RobotActionResult:
        return self._record(
            "search_for_victims",
            {"floor": floor, "victims_found": self.victims_by_floor.get(floor, 0)},
        )

    def assess_victim(self, floor: int) -> RobotActionResult:
        victims_found = self.victims_by_floor.get(floor, 0)
        condition = "needs_assistance" if victims_found else "none_found"
        return self._record("assess_victim", {"floor": floor, "condition": condition})

    def report_status(self, floor: int) -> RobotActionResult:
        victims_found = self.victims_by_floor.get(floor, 0)
        message = "victim located" if victims_found else "no victim located"
        return self._record("report_status", {"floor": floor, "message": message})

    def return_to_safe_zone(self) -> RobotActionResult:
        from_floor = self.current_floor
        self.current_floor = 1
        return self._record("return_to_safe_zone", {"from_floor": from_floor, "floor": 1})

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

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            supported_actions=set(ALL_ROBOT_ACTIONS),
            supported_modes={"simulator"},
            supports_dry_run=True,
            supports_real_execution=False,
            supports_feedback=False,
            supports_cancellation=False,
            max_concurrent_actions=1,
            required_sensors=list(self.available_sensors),
            is_simulator=True,
        )

    def _record(
        self,
        action: str,
        data: dict[str, Any],
        *,
        ok: bool = True,
        error: str | None = None,
    ) -> RobotActionResult:
        timestamp = datetime.now(timezone.utc).isoformat()
        action_record = {
            "action": action,
            "dry_run": self.dry_run,
            **{key: value for key, value in data.items() if key in {"floor", "from_floor"}},
        }
        self.actions.append(action_record)
        return RobotActionResult(
            ok=ok,
            status="succeeded" if ok else "failed",
            robot_id=self.robot_id,
            mode=self.mode,
            action=action,
            dry_run=self.dry_run,
            data={"robot_id": self.robot_id, "dry_run": self.dry_run, **data},
            timestamp=timestamp,
            error=error,
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
