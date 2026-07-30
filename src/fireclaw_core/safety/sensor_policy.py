from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fireclaw_core.sensors.health import SensorHealthStatus


SensorPolicyAction = Literal["allow", "warn", "degrade", "escalate", "block"]


@dataclass(frozen=True)
class SensorPolicyDecision:
    action: SensorPolicyAction
    reason: str | None = None


def evaluate_sensor_policy(
    *,
    skill_name: str,
    sensor: str,
    health_status: SensorHealthStatus | str | None,
    mode: str,
    dry_run: bool,
    verified_sensors: set[str],
    health_reason: str | None = None,
    safety_class: str | None = None,
    sensor_alternatives: dict[str, tuple[str, ...]] | None = None,
) -> SensorPolicyDecision:
    if health_status == "healthy":
        return SensorPolicyDecision(action="allow")

    if health_status is None:
        return SensorPolicyDecision(
            action="block",
            reason=f"Skill {skill_name} requires unavailable sensor: {sensor}",
        )

    reason = _format_reason(skill_name, sensor, health_status, health_reason)

    if health_status == "unknown":
        return SensorPolicyDecision(action="warn" if dry_run else "escalate", reason=reason)

    if sensor == "gas_detector":
        return SensorPolicyDecision(action="block", reason=reason)

    if sensor == "lidar" and safety_class == "motion":
        return SensorPolicyDecision(action="warn" if dry_run else "block", reason=reason)

    if sensor == "imu" and safety_class == "motion":
        return SensorPolicyDecision(action="warn" if dry_run else "escalate", reason=reason)

    alternatives_by_sensor = sensor_alternatives or {}
    if sensor in alternatives_by_sensor:
        alternatives = frozenset(alternatives_by_sensor[sensor])
        if alternatives & verified_sensors:
            alternative = sorted(alternatives & verified_sensors)[0]
            return SensorPolicyDecision(
                action="escalate" if not dry_run else "warn",
                reason=f"Skill {skill_name} requires {sensor}, but {alternative} is the only verified victim-search sensor.",
            )
        return SensorPolicyDecision(action="block", reason=reason)

    return SensorPolicyDecision(action="warn" if dry_run else "escalate", reason=reason)


def _format_reason(
    skill_name: str,
    sensor: str,
    health_status: SensorHealthStatus | str,
    health_reason: str | None,
) -> str:
    suffix = f": {health_reason}" if health_reason else ""
    return f"Skill {skill_name} requires {sensor}, but health is {health_status}{suffix}"
