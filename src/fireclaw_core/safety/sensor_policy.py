from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fireclaw_core.sensors.health import SensorHealthStatus


SensorPolicyAction = Literal["allow", "warn", "degrade", "escalate", "block"]


@dataclass(frozen=True)
class SensorPolicyDecision:
    action: SensorPolicyAction
    reason: str | None = None


VICTIM_SEARCH_ALTERNATIVES: dict[str, frozenset[str]] = {
    "rgb_camera": frozenset({"thermal_camera"}),
    "thermal_camera": frozenset({"rgb_camera"}),
}

NAVIGATION_SKILLS = frozenset({
    "navigate_to_point",
    "navigate_to_floor",
    "return_to_safe_zone",
})
VICTIM_SEARCH_SKILLS = frozenset({"search_for_victims", "assess_victim"})


def evaluate_sensor_policy(
    *,
    skill_name: str,
    sensor: str,
    health_status: SensorHealthStatus | str | None,
    mode: str,
    dry_run: bool,
    verified_sensors: set[str],
    health_reason: str | None = None,
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

    if sensor == "lidar" and skill_name in NAVIGATION_SKILLS:
        return SensorPolicyDecision(action="warn" if dry_run else "block", reason=reason)

    if sensor == "imu" and skill_name in NAVIGATION_SKILLS:
        return SensorPolicyDecision(action="warn" if dry_run else "escalate", reason=reason)

    if sensor in {"rgb_camera", "thermal_camera"} and skill_name in VICTIM_SEARCH_SKILLS:
        alternatives = VICTIM_SEARCH_ALTERNATIVES.get(sensor, frozenset())
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
