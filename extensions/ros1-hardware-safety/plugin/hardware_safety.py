"""Hardware-owned ROS1 stop evidence for real FireClaw robots.

The Provider deliberately has no motion Tool.  It reasserts a vendor-owned
hardware stop and then observes independent safety and motion channels.  The
Gateway remains the authority that decides whether the resulting evidence is
fresh and strong enough to reopen resource admission.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib import import_module
from math import isfinite, sqrt
from threading import RLock
from time import monotonic, sleep
from typing import Any, Protocol

from fireclaw_plugin_sdk import HARDWARE_STOP_EVIDENCE_CLASS


HARDWARE_STOP_EVIDENCE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class StopServiceConfig:
    name: str
    service_type: str


@dataclass(frozen=True)
class BooleanSignalConfig:
    topic: str
    message_type: str
    field: str


@dataclass(frozen=True)
class WatchdogSignalConfig:
    topic: str
    message_type: str
    healthy_field: str
    stop_asserted_field: str


@dataclass(frozen=True)
class BrakeSignalConfig:
    required: bool
    topic: str | None
    message_type: str | None
    engaged_field: str | None


@dataclass(frozen=True)
class ActuatorSignalConfig:
    topic: str
    message_type: str
    expected_names: tuple[str, ...]
    ignored_names: tuple[str, ...]
    velocity_threshold: float


@dataclass(frozen=True)
class IndependentMotionConfig:
    topic: str
    message_type: str
    linear_velocity_threshold: float
    angular_velocity_threshold: float


@dataclass(frozen=True)
class HardwareSafetyConfig:
    stop: StopServiceConfig
    watchdog: WatchdogSignalConfig
    emergency_stop: BooleanSignalConfig
    driver: BooleanSignalConfig
    brake: BrakeSignalConfig
    actuators: ActuatorSignalConfig
    independent_motion: IndependentMotionConfig
    observation_timeout_seconds: float
    max_signal_age_seconds: float
    hold_seconds: float
    minimum_samples: int
    evidence_ttl_seconds: float

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "HardwareSafetyConfig":
        _reject_unknown(
            raw,
            {
                "enabled",
                "stop",
                "watchdog",
                "emergency_stop",
                "driver",
                "brake",
                "actuators",
                "independent_motion",
                "observation_timeout_seconds",
                "max_signal_age_seconds",
                "hold_seconds",
                "minimum_samples",
                "evidence_ttl_seconds",
            },
            "hardware safety config",
        )
        stop_raw = _required_object(raw, "stop")
        _reject_unknown(stop_raw, {"service", "type"}, "stop")
        service_type = _required_string(stop_raw, "type")
        if service_type not in {"std_srvs/Trigger", "std_srvs/SetBool"}:
            raise ValueError(
                "stop.type must be std_srvs/Trigger or std_srvs/SetBool"
            )

        watchdog_raw = _required_object(raw, "watchdog")
        _reject_unknown(
            watchdog_raw,
            {"topic", "message_type", "healthy_field", "stop_asserted_field"},
            "watchdog",
        )
        emergency_raw = _required_object(raw, "emergency_stop")
        driver_raw = _required_object(raw, "driver")
        for label, value in (
            ("emergency_stop", emergency_raw),
            ("driver", driver_raw),
        ):
            _reject_unknown(value, {"topic", "message_type", "field"}, label)

        brake_raw = _required_object(raw, "brake")
        _reject_unknown(
            brake_raw,
            {"required", "topic", "message_type", "engaged_field"},
            "brake",
        )
        brake_required = _required_bool(brake_raw, "required")
        if brake_required:
            brake_topic = _required_string(brake_raw, "topic")
            brake_message_type = _required_string(brake_raw, "message_type")
            brake_field = _required_string(brake_raw, "engaged_field")
        else:
            brake_topic = _optional_string(brake_raw, "topic")
            brake_message_type = _optional_string(brake_raw, "message_type")
            brake_field = _optional_string(brake_raw, "engaged_field")

        actuators_raw = _required_object(raw, "actuators")
        _reject_unknown(
            actuators_raw,
            {
                "topic",
                "message_type",
                "expected_names",
                "ignored_names",
                "velocity_threshold",
            },
            "actuators",
        )
        actuator_message_type = _required_string(actuators_raw, "message_type")
        if actuator_message_type != "sensor_msgs/JointState":
            raise ValueError(
                "actuators.message_type must be sensor_msgs/JointState"
            )
        expected_names = _string_tuple(
            actuators_raw,
            "expected_names",
            required=True,
        )
        ignored_names = _string_tuple(
            actuators_raw,
            "ignored_names",
            required=False,
        )
        overlap = set(expected_names) & set(ignored_names)
        if overlap:
            raise ValueError(
                "actuators expected_names and ignored_names must be disjoint"
            )

        motion_raw = _required_object(raw, "independent_motion")
        _reject_unknown(
            motion_raw,
            {
                "topic",
                "message_type",
                "linear_velocity_threshold",
                "angular_velocity_threshold",
            },
            "independent_motion",
        )
        motion_message_type = _required_string(motion_raw, "message_type")
        if motion_message_type != "nav_msgs/Odometry":
            raise ValueError(
                "independent_motion.message_type must be nav_msgs/Odometry"
            )

        observation_timeout = _number(
            raw,
            "observation_timeout_seconds",
            default=3.0,
            minimum=0.8,
            maximum=30.0,
        )
        max_signal_age = _number(
            raw,
            "max_signal_age_seconds",
            default=0.5,
            minimum=0.05,
            maximum=5.0,
        )
        hold_seconds = _number(
            raw,
            "hold_seconds",
            default=0.75,
            minimum=0.75,
            maximum=10.0,
        )
        if observation_timeout < hold_seconds:
            raise ValueError(
                "observation_timeout_seconds must be at least hold_seconds"
            )
        return cls(
            stop=StopServiceConfig(
                name=_required_string(stop_raw, "service"),
                service_type=service_type,
            ),
            watchdog=WatchdogSignalConfig(
                topic=_required_string(watchdog_raw, "topic"),
                message_type=_required_string(watchdog_raw, "message_type"),
                healthy_field=_required_string(watchdog_raw, "healthy_field"),
                stop_asserted_field=_required_string(
                    watchdog_raw,
                    "stop_asserted_field",
                ),
            ),
            emergency_stop=BooleanSignalConfig(
                topic=_required_string(emergency_raw, "topic"),
                message_type=_required_string(emergency_raw, "message_type"),
                field=_required_string(emergency_raw, "field"),
            ),
            driver=BooleanSignalConfig(
                topic=_required_string(driver_raw, "topic"),
                message_type=_required_string(driver_raw, "message_type"),
                field=_required_string(driver_raw, "field"),
            ),
            brake=BrakeSignalConfig(
                required=brake_required,
                topic=brake_topic,
                message_type=brake_message_type,
                engaged_field=brake_field,
            ),
            actuators=ActuatorSignalConfig(
                topic=_required_string(actuators_raw, "topic"),
                message_type=actuator_message_type,
                expected_names=expected_names,
                ignored_names=ignored_names,
                velocity_threshold=_number(
                    actuators_raw,
                    "velocity_threshold",
                    default=0.01,
                    minimum=0.000001,
                    maximum=0.02,
                ),
            ),
            independent_motion=IndependentMotionConfig(
                topic=_required_string(motion_raw, "topic"),
                message_type=motion_message_type,
                linear_velocity_threshold=_number(
                    motion_raw,
                    "linear_velocity_threshold",
                    default=0.01,
                    minimum=0.000001,
                    maximum=0.02,
                ),
                angular_velocity_threshold=_number(
                    motion_raw,
                    "angular_velocity_threshold",
                    default=0.02,
                    minimum=0.000001,
                    maximum=0.05,
                ),
            ),
            observation_timeout_seconds=observation_timeout,
            max_signal_age_seconds=max_signal_age,
            hold_seconds=hold_seconds,
            minimum_samples=_integer(
                raw,
                "minimum_samples",
                default=3,
                minimum=3,
                maximum=100,
            ),
            evidence_ttl_seconds=_number(
                raw,
                "evidence_ttl_seconds",
                default=15.0,
                minimum=1.0,
                maximum=30.0,
            ),
        )


class HardwareSafetyObserver(Protocol):
    def collect_hardware_stop_observation(
        self,
        *,
        reason: str | None = None,
    ) -> Mapping[str, Any]:
        ...

    def collect_hardware_state(self) -> Mapping[str, Any]:
        """Observe safety channels without invoking the hardware stop."""
        ...

    def preflight(self) -> Mapping[str, Any]:
        """Validate the live ROS contract without actuating the robot."""
        ...


@dataclass
class HardwareStopEvidenceProvider:
    config: HardwareSafetyConfig
    observer: HardwareSafetyObserver

    provider_id = "ros1-hardware-safety"
    evidence_class = HARDWARE_STOP_EVIDENCE_CLASS
    hardware_owned = True

    def preflight(self) -> Mapping[str, Any]:
        preflight = getattr(self.observer, "preflight", None)
        if not callable(preflight):
            return {
                "status": "blocked",
                "checks": [
                    {
                        "id": "observer.preflight",
                        "status": "fail",
                        "message": "hardware observer does not implement preflight()",
                    }
                ],
            }
        result = preflight()
        if not isinstance(result, Mapping):
            raise TypeError("hardware observer preflight result must be an object")
        return dict(result)

    def inspect_hardware_state(
        self,
        *,
        robot_id: str,
    ) -> Mapping[str, Any]:
        """Collect non-actuating evidence for an operator-created test state.

        This deliberately uses a different evidence class from stop evidence,
        so the Gateway can never mistake an acceptance observation for proof
        that a persistent admission freeze is safe to recover.
        """

        try:
            collect = getattr(self.observer, "collect_hardware_state", None)
            if not callable(collect):
                raise TypeError(
                    "hardware observer must implement collect_hardware_state()"
                )
            raw = collect()
            if not isinstance(raw, Mapping):
                raise TypeError("hardware observer result must be an object")
            details, moving = self._evaluate(raw)
            details["blockers"] = [
                blocker
                for blocker in details["blockers"]
                if blocker
                not in {
                    "hardware_stop_not_reasserted",
                    "hardware_stop_not_acknowledged",
                }
            ]
        except Exception as exc:
            details = self._failed_details(type(exc).__name__)
            details["blockers"] = ["hardware_observer_failed"]
            moving = False
        observed_at = datetime.now(timezone.utc)
        return {
            "provider_id": self.provider_id,
            "evidence_class": "hardware_safety_observation_v1",
            "robot_id": robot_id,
            "status": (
                "moving"
                if moving
                else "unavailable"
                if "hardware_observer_failed" in details["blockers"]
                else "observed"
            ),
            "deployment_mode": "real",
            "dry_run": False,
            "observed_at": observed_at.isoformat(),
            "details": details,
        }

    def collect_stop_evidence(
        self,
        *,
        robot_id: str,
        reason: str | None = None,
    ) -> Mapping[str, Any]:
        try:
            raw = self.observer.collect_hardware_stop_observation(reason=reason)
            if not isinstance(raw, Mapping):
                raise TypeError("hardware observer result must be an object")
            details, moving = self._evaluate(raw)
        except Exception as exc:
            details = self._failed_details(type(exc).__name__)
            moving = False
        status = (
            "stopped"
            if not details["blockers"]
            else "moving"
            if moving
            else "unknown"
        )
        observed_at = datetime.now(timezone.utc)
        return {
            "provider_id": self.provider_id,
            "evidence_class": self.evidence_class,
            "robot_id": robot_id,
            "status": status,
            "deployment_mode": "real",
            "dry_run": False,
            "observed_at": observed_at.isoformat(),
            "expires_at": (
                observed_at
                + timedelta(seconds=self.config.evidence_ttl_seconds)
            ).isoformat(),
            "details": details,
        }

    def _evaluate(
        self,
        raw: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        blockers: list[str] = []
        stop_reasserted = raw.get("stop_reasserted") is True
        stop_acknowledged = raw.get("stop_acknowledged") is True
        if not stop_reasserted:
            blockers.append("hardware_stop_not_reasserted")
        if not stop_acknowledged:
            blockers.append("hardware_stop_not_acknowledged")

        watchdog_raw = _mapping(raw.get("watchdog"))
        watchdog = {
            "fresh": watchdog_raw.get("fresh") is True,
            "healthy": watchdog_raw.get("healthy") is True,
            "stop_asserted": watchdog_raw.get("stop_asserted") is True,
            "sample_count": _safe_count(watchdog_raw.get("sample_count")),
        }
        if not watchdog["fresh"]:
            blockers.append("watchdog_state_stale")
        if not watchdog["healthy"]:
            blockers.append("watchdog_unhealthy")
        if not watchdog["stop_asserted"]:
            blockers.append("watchdog_stop_not_asserted")
        if watchdog["sample_count"] < self.config.minimum_samples:
            blockers.append("watchdog_samples_insufficient")

        emergency_raw = _mapping(raw.get("emergency_stop"))
        emergency_stop = {
            "fresh": emergency_raw.get("fresh") is True,
            "active": emergency_raw.get("active") is True,
            "sample_count": _safe_count(emergency_raw.get("sample_count")),
        }
        if not emergency_stop["fresh"]:
            blockers.append("emergency_stop_state_stale")
        if not emergency_stop["active"]:
            blockers.append("hardware_emergency_stop_not_active")
        if emergency_stop["sample_count"] < self.config.minimum_samples:
            blockers.append("emergency_stop_samples_insufficient")

        driver_raw = _mapping(raw.get("driver"))
        driver = {
            "fresh": driver_raw.get("fresh") is True,
            "enabled": (
                driver_raw.get("enabled")
                if isinstance(driver_raw.get("enabled"), bool)
                else None
            ),
            "sample_count": _safe_count(driver_raw.get("sample_count")),
        }
        if not driver["fresh"]:
            blockers.append("driver_state_stale")
        if driver["enabled"] is not False:
            blockers.append("hardware_driver_not_disabled")
        if driver["sample_count"] < self.config.minimum_samples:
            blockers.append("driver_samples_insufficient")

        brake_raw = _mapping(raw.get("brake"))
        brake = {
            "required": self.config.brake.required,
            "fresh": brake_raw.get("fresh") is True,
            "engaged": (
                brake_raw.get("engaged")
                if isinstance(brake_raw.get("engaged"), bool)
                else None
            ),
            "sample_count": _safe_count(brake_raw.get("sample_count")),
        }
        if self.config.brake.required:
            if not brake["fresh"]:
                blockers.append("brake_state_stale")
            if brake["engaged"] is not True:
                blockers.append("hardware_brake_not_engaged")
            if brake["sample_count"] < self.config.minimum_samples:
                blockers.append("brake_samples_insufficient")

        actuator_raw = _mapping(raw.get("actuators"))
        samples = _velocity_samples(actuator_raw.get("samples"))
        expected = set(self.config.actuators.expected_names)
        ignored = set(self.config.actuators.ignored_names)
        observed = set().union(*(set(sample) for sample in samples)) if samples else set()
        unclassified = observed - expected - ignored
        inventory_complete = bool(samples) and all(
            expected.issubset(sample)
            and not (set(sample) - expected - ignored)
            for sample in samples
        )
        max_abs_velocity = max(
            (
                abs(sample[name])
                for sample in samples
                for name in expected
                if name in sample
            ),
            default=0.0,
        )
        stationary_samples = sum(
            1
            for sample in samples
            if expected.issubset(sample)
            and all(
                abs(sample[name])
                <= self.config.actuators.velocity_threshold
                for name in expected
            )
        )
        actuator_span = _safe_number(
            actuator_raw.get("sample_span_seconds"),
        )
        actuator_fresh = actuator_raw.get("fresh") is True
        if not actuator_fresh:
            blockers.append("actuator_state_stale")
        if not inventory_complete or unclassified:
            blockers.append("actuator_inventory_incomplete")
        actuator_motion = max_abs_velocity > self.config.actuators.velocity_threshold
        if actuator_motion:
            blockers.append("actuator_motion_detected")
        if stationary_samples < self.config.minimum_samples:
            blockers.append("actuator_samples_insufficient")
        if actuator_span < self.config.hold_seconds:
            blockers.append("actuator_hold_window_insufficient")
        actuators = {
            "fresh": actuator_fresh,
            "inventory_complete": inventory_complete and not unclassified,
            "expected_names": sorted(expected),
            "observed_names": sorted(observed),
            "ignored_names": sorted(ignored),
            "unclassified_names": sorted(unclassified),
            "sample_count": len(samples),
            "stationary_samples": stationary_samples,
            "sample_span_seconds": actuator_span,
            "velocity_threshold": self.config.actuators.velocity_threshold,
            "max_abs_velocity": max_abs_velocity,
        }

        motion_raw = _mapping(raw.get("independent_motion"))
        motion_samples = _motion_samples(motion_raw.get("samples"))
        max_linear = max(
            (item["linear_speed"] for item in motion_samples),
            default=0.0,
        )
        max_angular = max(
            (item["angular_speed"] for item in motion_samples),
            default=0.0,
        )
        motion_stationary_samples = sum(
            1
            for item in motion_samples
            if item["linear_speed"]
            <= self.config.independent_motion.linear_velocity_threshold
            and item["angular_speed"]
            <= self.config.independent_motion.angular_velocity_threshold
        )
        motion_span = _safe_number(motion_raw.get("sample_span_seconds"))
        motion_fresh = motion_raw.get("fresh") is True
        if not motion_fresh:
            blockers.append("independent_motion_state_stale")
        independent_motion_detected = (
            max_linear
            > self.config.independent_motion.linear_velocity_threshold
            or max_angular
            > self.config.independent_motion.angular_velocity_threshold
        )
        if independent_motion_detected:
            blockers.append("independent_motion_detected")
        if motion_stationary_samples < self.config.minimum_samples:
            blockers.append("independent_motion_samples_insufficient")
        if motion_span < self.config.hold_seconds:
            blockers.append("independent_motion_hold_window_insufficient")
        independent_motion = {
            "fresh": motion_fresh,
            "sample_count": len(motion_samples),
            "stationary_samples": motion_stationary_samples,
            "sample_span_seconds": motion_span,
            "linear_velocity_threshold": (
                self.config.independent_motion.linear_velocity_threshold
            ),
            "angular_velocity_threshold": (
                self.config.independent_motion.angular_velocity_threshold
            ),
            "max_linear_speed": max_linear,
            "max_angular_speed": max_angular,
        }

        for item in raw.get("observation_errors", ()):
            if isinstance(item, str) and item:
                blockers.append(item)
        blockers = list(dict.fromkeys(blockers))
        return (
            {
                "schema_version": HARDWARE_STOP_EVIDENCE_SCHEMA_VERSION,
                "stop_reasserted": stop_reasserted,
                "stop_acknowledged": stop_acknowledged,
                "watchdog": watchdog,
                "emergency_stop": emergency_stop,
                "driver": driver,
                "brake": brake,
                "actuators": actuators,
                "independent_motion": independent_motion,
                "hold_seconds": min(actuator_span, motion_span),
                "minimum_samples": self.config.minimum_samples,
                "blockers": blockers,
            },
            actuator_motion or independent_motion_detected,
        )

    def _failed_details(self, exception_class: str) -> dict[str, Any]:
        return {
            "schema_version": HARDWARE_STOP_EVIDENCE_SCHEMA_VERSION,
            "stop_reasserted": False,
            "stop_acknowledged": False,
            "watchdog": {
                "fresh": False,
                "healthy": False,
                "stop_asserted": False,
                "sample_count": 0,
            },
            "emergency_stop": {"fresh": False, "active": False, "sample_count": 0},
            "driver": {"fresh": False, "enabled": None, "sample_count": 0},
            "brake": {
                "required": self.config.brake.required,
                "fresh": False,
                "engaged": None,
                "sample_count": 0,
            },
            "actuators": {
                "fresh": False,
                "inventory_complete": False,
                "expected_names": sorted(self.config.actuators.expected_names),
                "observed_names": [],
                "ignored_names": sorted(self.config.actuators.ignored_names),
                "unclassified_names": [],
                "sample_count": 0,
                "stationary_samples": 0,
                "sample_span_seconds": 0.0,
                "velocity_threshold": self.config.actuators.velocity_threshold,
                "max_abs_velocity": 0.0,
            },
            "independent_motion": {
                "fresh": False,
                "sample_count": 0,
                "stationary_samples": 0,
                "sample_span_seconds": 0.0,
                "linear_velocity_threshold": (
                    self.config.independent_motion.linear_velocity_threshold
                ),
                "angular_velocity_threshold": (
                    self.config.independent_motion.angular_velocity_threshold
                ),
                "max_linear_speed": 0.0,
                "max_angular_speed": 0.0,
            },
            "hold_seconds": 0.0,
            "minimum_samples": self.config.minimum_samples,
            "observer_error_class": exception_class,
            "blockers": ["hardware_observer_failed"],
        }


class Ros1HardwareSafetyObserver:
    """Lazy ROS1 collector for vendor-bound hardware safety channels."""

    def __init__(self, config: HardwareSafetyConfig) -> None:
        self.config = config

    def _rospy(self) -> Any:
        rospy = import_module("rospy")
        core = getattr(rospy, "core", None)
        initialized = (
            callable(getattr(core, "is_initialized", None))
            and core.is_initialized()
        )
        if not initialized:
            rospy.init_node(
                "fireclaw_hardware_safety",
                anonymous=True,
                disable_signals=True,
            )
        return rospy

    def preflight(self) -> Mapping[str, Any]:
        """Validate master, types, fields, and inventory without actuation."""

        checks: list[dict[str, str]] = []

        def passed(check_id: str, message: str) -> None:
            checks.append({"id": check_id, "status": "pass", "message": message})

        def failed(check_id: str, exc: Exception | str) -> None:
            label = exc if isinstance(exc, str) else type(exc).__name__
            checks.append({"id": check_id, "status": "fail", "message": str(label)})

        try:
            rospy = self._rospy()
            rospy.get_master().getPid()
            passed("ros.master", "ROS master is reachable")
        except Exception as exc:
            failed("ros.master", exc)
            return {"status": "blocked", "checks": checks}

        try:
            rosservice = import_module("rosservice")
            actual_service_type = rosservice.get_service_type(self.config.stop.name)
            if actual_service_type != self.config.stop.service_type:
                raise ValueError("configured stop service type does not match ROS graph")
            passed("stop.service_type", "hardware stop service type matches")
        except Exception as exc:
            failed("stop.service_type", exc)

        message_loader = None
        rostopic = None
        try:
            message_loader = import_module("roslib.message")
            rostopic = import_module("rostopic")
        except Exception as exc:
            failed("ros.message_introspection", exc)

        topic_contracts: list[tuple[str, str, str]] = [
            (
                "watchdog",
                self.config.watchdog.topic,
                self.config.watchdog.message_type,
            ),
            (
                "emergency_stop",
                self.config.emergency_stop.topic,
                self.config.emergency_stop.message_type,
            ),
            (
                "driver",
                self.config.driver.topic,
                self.config.driver.message_type,
            ),
            (
                "actuators",
                self.config.actuators.topic,
                self.config.actuators.message_type,
            ),
            (
                "independent_motion",
                self.config.independent_motion.topic,
                self.config.independent_motion.message_type,
            ),
        ]
        if self.config.brake.required:
            topic_contracts.append(
                (
                    "brake",
                    str(self.config.brake.topic),
                    str(self.config.brake.message_type),
                )
            )

        if message_loader is not None and rostopic is not None:
            for signal, topic, expected_type in topic_contracts:
                try:
                    resolved = rostopic.get_topic_type(topic, blocking=False)
                    actual_type = resolved[0] if isinstance(resolved, tuple) else None
                    if actual_type != expected_type:
                        raise ValueError(
                            "configured topic type does not match ROS graph"
                        )
                    _message_class(message_loader, expected_type)
                    passed(f"topic.{signal}.type", "topic type matches")
                except Exception as exc:
                    failed(f"topic.{signal}.type", exc)

            try:
                observation = self.collect_hardware_state()
                for signal, _, _ in topic_contracts:
                    try:
                        self._validate_preflight_signal(signal, observation)
                        passed(
                            f"topic.{signal}.sample",
                            "sample contract, freshness, and count are valid",
                        )
                    except Exception as exc:
                        failed(f"topic.{signal}.sample", exc)
            except Exception as exc:
                failed("topic.samples", exc)

        return {
            "status": (
                "ready"
                if all(check["status"] == "pass" for check in checks)
                else "blocked"
            ),
            "checks": checks,
        }

    def _validate_preflight_signal(
        self,
        signal: str,
        observation: Mapping[str, Any],
    ) -> None:
        errors = {
            item
            for item in observation.get("observation_errors", ())
            if isinstance(item, str)
        }
        if f"{signal}_message_invalid" in errors:
            raise ValueError("sample field or value is invalid")
        section = _mapping(observation.get(signal))
        if section.get("fresh") is not True:
            raise ValueError("sample is missing or stale")
        if signal in {"actuators", "independent_motion"}:
            samples = section.get("samples")
            if not isinstance(samples, Sequence) or isinstance(samples, (str, bytes)):
                raise ValueError("sample array is invalid")
            if len(samples) < self.config.minimum_samples:
                raise ValueError("sample count is below minimum_samples")
            if _safe_number(section.get("sample_span_seconds")) < self.config.hold_seconds:
                raise ValueError("sample span is below hold_seconds")
            if signal == "actuators":
                expected = set(self.config.actuators.expected_names)
                ignored = set(self.config.actuators.ignored_names)
                normalized = _velocity_samples(samples)
                if not normalized or any(
                    not expected.issubset(sample)
                    or set(sample) - expected - ignored
                    for sample in normalized
                ):
                    raise ValueError(
                        "JointState actuator inventory does not match Profile"
                    )
            else:
                _motion_samples(samples)
            return
        if _safe_count(section.get("sample_count")) < self.config.minimum_samples:
            raise ValueError("sample count is below minimum_samples")

    def collect_hardware_state(self) -> Mapping[str, Any]:
        return self._collect_hardware_observation(invoke_stop=False)

    def collect_hardware_stop_observation(
        self,
        *,
        reason: str | None = None,
    ) -> Mapping[str, Any]:
        del reason  # The ROS stop service contract does not accept free text.
        return self._collect_hardware_observation(invoke_stop=True)

    def _collect_hardware_observation(
        self,
        *,
        invoke_stop: bool,
    ) -> Mapping[str, Any]:
        rospy = self._rospy()
        message_loader = import_module("roslib.message")
        message_classes = {
            "watchdog": _message_class(
                message_loader,
                self.config.watchdog.message_type,
            ),
            "emergency_stop": _message_class(
                message_loader,
                self.config.emergency_stop.message_type,
            ),
            "driver": _message_class(
                message_loader,
                self.config.driver.message_type,
            ),
            "actuators": _message_class(
                message_loader,
                self.config.actuators.message_type,
            ),
            "independent_motion": _message_class(
                message_loader,
                self.config.independent_motion.message_type,
            ),
        }
        if self.config.brake.required:
            message_classes["brake"] = _message_class(
                message_loader,
                str(self.config.brake.message_type),
            )

        stop_reasserted = False
        stop_acknowledged = False
        observation_errors: list[str] = []
        if invoke_stop:
            try:
                rospy.wait_for_service(
                    self.config.stop.name,
                    timeout=self.config.observation_timeout_seconds,
                )
                service_module = import_module("std_srvs.srv")
                if self.config.stop.service_type == "std_srvs/Trigger":
                    service_class = service_module.Trigger
                    response = rospy.ServiceProxy(
                        self.config.stop.name,
                        service_class,
                    )()
                else:
                    service_class = service_module.SetBool
                    response = rospy.ServiceProxy(
                        self.config.stop.name,
                        service_class,
                    )(True)
                stop_reasserted = True
                stop_acknowledged = getattr(response, "success", None) is True
                if not stop_acknowledged:
                    observation_errors.append("hardware_stop_service_rejected")
            except Exception:
                observation_errors.append("hardware_stop_service_failed")

        lock = RLock()
        signals: dict[str, list[tuple[float, Any]]] = {
            "watchdog": [],
            "emergency_stop": [],
            "driver": [],
            "brake": [],
            "actuators": [],
            "independent_motion": [],
        }

        def append_signal(name: str, value: Any) -> None:
            with lock:
                signals[name].append((monotonic(), value))

        def guarded(name: str, callback: Any) -> Any:
            def receive(message: Any) -> None:
                try:
                    append_signal(name, callback(message))
                except Exception:
                    with lock:
                        observation_errors.append(f"{name}_message_invalid")

            return receive

        subscriptions: list[Any] = []
        try:
            subscriptions.append(
                rospy.Subscriber(
                    self.config.watchdog.topic,
                    message_classes["watchdog"],
                    guarded(
                        "watchdog",
                        lambda message: {
                            "healthy": _strict_bool(
                                _field(message, self.config.watchdog.healthy_field)
                            ),
                            "stop_asserted": _strict_bool(
                                _field(
                                    message,
                                    self.config.watchdog.stop_asserted_field,
                                )
                            ),
                        },
                    ),
                    queue_size=10,
                )
            )
            subscriptions.append(
                rospy.Subscriber(
                    self.config.emergency_stop.topic,
                    message_classes["emergency_stop"],
                    guarded(
                        "emergency_stop",
                        lambda message: _strict_bool(
                            _field(message, self.config.emergency_stop.field)
                        ),
                    ),
                    queue_size=10,
                )
            )
            subscriptions.append(
                rospy.Subscriber(
                    self.config.driver.topic,
                    message_classes["driver"],
                    guarded(
                        "driver",
                        lambda message: _strict_bool(
                            _field(message, self.config.driver.field)
                        ),
                    ),
                    queue_size=10,
                )
            )
            if self.config.brake.required:
                subscriptions.append(
                    rospy.Subscriber(
                        self.config.brake.topic,
                        message_classes["brake"],
                        guarded(
                            "brake",
                            lambda message: _strict_bool(
                                _field(
                                    message,
                                    str(self.config.brake.engaged_field),
                                )
                            ),
                        ),
                        queue_size=10,
                    )
                )
            subscriptions.append(
                rospy.Subscriber(
                    self.config.actuators.topic,
                    message_classes["actuators"],
                    guarded("actuators", _joint_velocities),
                    queue_size=50,
                )
            )
            subscriptions.append(
                rospy.Subscriber(
                    self.config.independent_motion.topic,
                    message_classes["independent_motion"],
                    guarded("independent_motion", _odometry_speed),
                    queue_size=50,
                )
            )

            deadline = monotonic() + self.config.observation_timeout_seconds
            while monotonic() < deadline:
                with lock:
                    enough = (
                        len(signals["watchdog"])
                        >= self.config.minimum_samples
                        and len(signals["emergency_stop"])
                        >= self.config.minimum_samples
                        and len(signals["driver"])
                        >= self.config.minimum_samples
                        and (
                            not self.config.brake.required
                            or len(signals["brake"])
                            >= self.config.minimum_samples
                        )
                        and len(signals["actuators"])
                        >= self.config.minimum_samples
                        and len(signals["independent_motion"])
                        >= self.config.minimum_samples
                        and _sample_span(signals["actuators"])
                        >= self.config.hold_seconds
                        and _sample_span(signals["independent_motion"])
                        >= self.config.hold_seconds
                    )
                if enough:
                    break
                is_shutdown = getattr(rospy, "is_shutdown", None)
                if callable(is_shutdown) and is_shutdown():
                    observation_errors.append("ros_shutdown_during_observation")
                    break
                sleep(0.01)
        finally:
            for subscription in subscriptions:
                try:
                    subscription.unregister()
                except Exception:
                    pass

        now = monotonic()
        with lock:
            snapshot = {key: list(value) for key, value in signals.items()}
            errors = list(dict.fromkeys(observation_errors))
        return {
            "stop_reasserted": stop_reasserted,
            "stop_acknowledged": stop_acknowledged,
            "watchdog": _latest_mapping_signal(
                snapshot["watchdog"],
                now=now,
                max_age=self.config.max_signal_age_seconds,
            ),
            "emergency_stop": _latest_boolean_signal(
                snapshot["emergency_stop"],
                value_key="active",
                now=now,
                max_age=self.config.max_signal_age_seconds,
            ),
            "driver": _latest_boolean_signal(
                snapshot["driver"],
                value_key="enabled",
                now=now,
                max_age=self.config.max_signal_age_seconds,
            ),
            "brake": _latest_boolean_signal(
                snapshot["brake"],
                value_key="engaged",
                now=now,
                max_age=self.config.max_signal_age_seconds,
            ),
            "actuators": {
                "fresh": _fresh(snapshot["actuators"], now, self.config.max_signal_age_seconds),
                "samples": [value for _, value in snapshot["actuators"]],
                "sample_span_seconds": _sample_span(snapshot["actuators"]),
            },
            "independent_motion": {
                "fresh": _fresh(
                    snapshot["independent_motion"],
                    now,
                    self.config.max_signal_age_seconds,
                ),
                "samples": [value for _, value in snapshot["independent_motion"]],
                "sample_span_seconds": _sample_span(
                    snapshot["independent_motion"]
                ),
            },
            "observation_errors": errors,
        }


def _required_object(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object")
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _reject_unknown(raw: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(str(key) for key in raw if key not in allowed)
    if unknown:
        raise ValueError(f"{label} contains unknown fields: {', '.join(unknown)}")


def _required_string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_string(raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string when provided")
    return value.strip()


def _required_bool(raw: Mapping[str, Any], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be boolean")
    return value


def _number(
    raw: Mapping[str, Any],
    key: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    normalized = float(value)
    if not isfinite(normalized) or not minimum <= normalized <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return normalized


def _integer(
    raw: Mapping[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def _string_tuple(
    raw: Mapping[str, Any],
    key: str,
    *,
    required: bool,
) -> tuple[str, ...]:
    value = raw.get(key)
    if value is None and not required:
        return ()
    if not value and not required and isinstance(value, Sequence):
        return ()
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or not value
    ):
        raise ValueError(f"{key} must be a non-empty string array")
    result = tuple(
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    )
    if len(result) != len(value) or len(result) != len(set(result)):
        raise ValueError(f"{key} must contain unique non-empty strings")
    return result


def _safe_count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _safe_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    normalized = float(value)
    return normalized if isfinite(normalized) and normalized >= 0 else 0.0


def _velocity_samples(value: Any) -> list[dict[str, float]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("actuator samples must be an array")
    result: list[dict[str, float]] = []
    for sample in value:
        if not isinstance(sample, Mapping):
            raise ValueError("actuator sample must be an object")
        normalized: dict[str, float] = {}
        valid = True
        for name, velocity in sample.items():
            if (
                not isinstance(name, str)
                or not name.strip()
                or isinstance(velocity, bool)
                or not isinstance(velocity, (int, float))
                or not isfinite(float(velocity))
            ):
                valid = False
                break
            normalized[name.strip()] = float(velocity)
        if not valid:
            raise ValueError("actuator sample contains an invalid value")
        result.append(normalized)
    return result


def _motion_samples(value: Any) -> list[dict[str, float]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("independent motion samples must be an array")
    result: list[dict[str, float]] = []
    for sample in value:
        if not isinstance(sample, Mapping):
            raise ValueError("independent motion sample must be an object")
        linear = _strict_nonnegative_number(
            sample.get("linear_speed"),
            "independent linear speed",
        )
        angular = _strict_nonnegative_number(
            sample.get("angular_speed"),
            "independent angular speed",
        )
        result.append({"linear_speed": linear, "angular_speed": angular})
    return result


def _strict_nonnegative_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite non-negative number")
    normalized = float(value)
    if not isfinite(normalized) or normalized < 0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return normalized


def _message_class(loader: Any, message_type: str) -> Any:
    resolved = loader.get_message_class(message_type)
    if resolved is None:
        raise ValueError(f"ROS message type is unavailable: {message_type}")
    return resolved


def _field(message: Any, path: str) -> Any:
    current = message
    for part in path.split("."):
        if not part:
            raise ValueError("ROS field path contains an empty segment")
        if isinstance(current, Mapping):
            if part not in current:
                raise ValueError("ROS field path is unavailable")
            current = current[part]
        else:
            current = getattr(current, part)
    return current


def _strict_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise TypeError("hardware safety field must be boolean")
    return value


def _joint_velocities(message: Any) -> dict[str, float]:
    names = list(getattr(message, "name"))
    velocities = list(getattr(message, "velocity"))
    if len(names) != len(velocities):
        raise ValueError("JointState name/velocity arrays have different lengths")
    result: dict[str, float] = {}
    for name, velocity in zip(names, velocities):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("JointState contains an invalid actuator name")
        if (
            isinstance(velocity, bool)
            or not isinstance(velocity, (int, float))
            or not isfinite(float(velocity))
        ):
            raise ValueError("JointState contains an invalid velocity")
        if name.strip() in result:
            raise ValueError("JointState contains a duplicate actuator name")
        result[name.strip()] = float(velocity)
    return result


def _odometry_speed(message: Any) -> dict[str, float]:
    twist = message.twist.twist
    linear = twist.linear
    angular = twist.angular
    linear_speed = sqrt(
        float(linear.x) ** 2 + float(linear.y) ** 2 + float(linear.z) ** 2
    )
    angular_speed = sqrt(
        float(angular.x) ** 2
        + float(angular.y) ** 2
        + float(angular.z) ** 2
    )
    if not isfinite(linear_speed) or not isfinite(angular_speed):
        raise ValueError("Odometry contains a non-finite velocity")
    return {"linear_speed": linear_speed, "angular_speed": angular_speed}


def _fresh(samples: Sequence[tuple[float, Any]], now: float, max_age: float) -> bool:
    return bool(samples) and now - samples[-1][0] <= max_age


def _sample_span(samples: Sequence[tuple[float, Any]]) -> float:
    if len(samples) < 2:
        return 0.0
    return max(0.0, samples[-1][0] - samples[0][0])


def _latest_mapping_signal(
    samples: Sequence[tuple[float, Any]],
    *,
    now: float,
    max_age: float,
) -> dict[str, Any]:
    value = _mapping(samples[-1][1]) if samples else {}
    return {
        "fresh": _fresh(samples, now, max_age),
        "healthy": value.get("healthy") is True,
        "stop_asserted": value.get("stop_asserted") is True,
        "sample_count": len(samples),
    }


def _latest_boolean_signal(
    samples: Sequence[tuple[float, Any]],
    *,
    value_key: str,
    now: float,
    max_age: float,
) -> dict[str, Any]:
    value = samples[-1][1] if samples else None
    return {
        "fresh": _fresh(samples, now, max_age),
        value_key: value if isinstance(value, bool) else None,
        "sample_count": len(samples),
    }
