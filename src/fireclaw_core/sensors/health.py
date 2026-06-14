from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal


SensorHealthStatus = Literal["healthy", "degraded", "stale", "invalid", "unknown"]


@dataclass(frozen=True)
class SensorObservation:
    observed: bool
    age_seconds: float | None = None
    payload_size: int | None = None
    numeric_value: float | None = None
    finite_range_count: int | None = None
    frame_id: str | None = None
    timestamp_age_seconds: float | None = None
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SensorHealthPolicy:
    sensor: str
    max_age_seconds: float = 2.0
    require_payload: bool = False
    require_numeric_value: bool = False
    min_numeric_value: float | None = None
    max_numeric_value: float | None = None
    require_finite_ranges: bool = False
    require_frame_id: bool = False


@dataclass(frozen=True)
class SensorHealthResult:
    sensor: str
    status: SensorHealthStatus
    reason: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "sensor": self.sensor,
            "status": self.status,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.details:
            payload["details"] = dict(self.details)
        return payload


DEFAULT_SENSOR_HEALTH_POLICIES: dict[str, SensorHealthPolicy] = {
    "rgb_camera": SensorHealthPolicy(
        sensor="rgb_camera",
        require_payload=True,
        require_frame_id=True,
    ),
    "thermal_camera": SensorHealthPolicy(
        sensor="thermal_camera",
        require_payload=True,
        require_frame_id=True,
    ),
    "gas_detector": SensorHealthPolicy(
        sensor="gas_detector",
        require_numeric_value=True,
        min_numeric_value=0.0,
    ),
    "lidar": SensorHealthPolicy(
        sensor="lidar",
        require_finite_ranges=True,
        require_frame_id=True,
    ),
    "imu": SensorHealthPolicy(
        sensor="imu",
        require_frame_id=True,
    ),
}


def evaluate_sensor_health(
    sensor: str,
    observation: SensorObservation,
    *,
    policy: SensorHealthPolicy | None = None,
) -> SensorHealthResult:
    active_policy = policy or DEFAULT_SENSOR_HEALTH_POLICIES.get(sensor, SensorHealthPolicy(sensor=sensor))
    if not observation.observed:
        return SensorHealthResult(sensor=sensor, status="stale", reason="no recent observation")
    if observation.age_seconds is None:
        return SensorHealthResult(sensor=sensor, status="unknown", reason="observation age is unknown")
    if observation.age_seconds > active_policy.max_age_seconds:
        return SensorHealthResult(
            sensor=sensor,
            status="stale",
            reason=f"observation is stale: {observation.age_seconds:.1f}s > {active_policy.max_age_seconds:.1f}s",
        )
    if active_policy.require_payload and (observation.payload_size is None or observation.payload_size <= 0):
        return SensorHealthResult(sensor=sensor, status="invalid", reason="payload is empty")
    if active_policy.require_numeric_value:
        if observation.numeric_value is None:
            return SensorHealthResult(sensor=sensor, status="invalid", reason="numeric value is missing")
        if not math.isfinite(observation.numeric_value):
            return SensorHealthResult(sensor=sensor, status="invalid", reason="numeric value is not finite")
        if active_policy.min_numeric_value is not None and observation.numeric_value < active_policy.min_numeric_value:
            return SensorHealthResult(
                sensor=sensor,
                status="invalid",
                reason=f"numeric value below minimum: {observation.numeric_value} < {active_policy.min_numeric_value}",
            )
        if active_policy.max_numeric_value is not None and observation.numeric_value > active_policy.max_numeric_value:
            return SensorHealthResult(
                sensor=sensor,
                status="invalid",
                reason=f"numeric value above maximum: {observation.numeric_value} > {active_policy.max_numeric_value}",
            )
    if active_policy.require_finite_ranges and (observation.finite_range_count is None or observation.finite_range_count <= 0):
        return SensorHealthResult(sensor=sensor, status="invalid", reason="no finite ranges")
    if active_policy.require_frame_id and not observation.frame_id:
        return SensorHealthResult(sensor=sensor, status="degraded", reason="frame_id is missing")
    if observation.timestamp_age_seconds is not None and observation.timestamp_age_seconds > active_policy.max_age_seconds:
        return SensorHealthResult(
            sensor=sensor,
            status="stale",
            reason=f"message timestamp is stale: {observation.timestamp_age_seconds:.1f}s > {active_policy.max_age_seconds:.1f}s",
        )
    return SensorHealthResult(sensor=sensor, status="healthy")
