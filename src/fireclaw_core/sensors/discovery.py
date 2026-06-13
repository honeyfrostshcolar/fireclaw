from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Literal


SensorFindingStatus = Literal["discovered", "verified", "degraded", "rejected"]


@dataclass(frozen=True)
class SensorMappingRule:
    topic_pattern: str
    message_type: str
    sensor: str
    source: str = "default"
    confidence: float = 0.8
    confirmed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "topic_pattern": self.topic_pattern,
            "message_type": self.message_type,
            "sensor": self.sensor,
            "source": self.source,
            "confidence": self.confidence,
            "confirmed": self.confirmed,
        }


@dataclass(frozen=True)
class SensorFinding:
    sensor: str
    topic: str
    message_type: str
    status: SensorFindingStatus
    confidence: float
    source: str
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "sensor": self.sensor,
            "topic": self.topic,
            "message_type": self.message_type,
            "status": self.status,
            "confidence": self.confidence,
            "source": self.source,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True)
class SensorDiscoveryReport:
    findings: tuple[SensorFinding, ...]
    source: str = "ros1"

    def verified_sensors(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for finding in self.findings:
            if finding.status == "verified" and finding.sensor not in seen:
                seen.add(finding.sensor)
                result.append(finding.sensor)
        return result

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "verified_sensors": self.verified_sensors(),
            "findings": [finding.to_dict() for finding in self.findings],
        }


DEFAULT_SENSOR_MAPPING_RULES: tuple[SensorMappingRule, ...] = (
    SensorMappingRule("/scan", "sensor_msgs/LaserScan", "lidar", confidence=0.99),
    SensorMappingRule("/imu", "sensor_msgs/Imu", "imu", confidence=0.99),
    SensorMappingRule("/camera/image_raw", "sensor_msgs/Image", "rgb_camera", confidence=0.9),
    SensorMappingRule("/rgb/image_raw", "sensor_msgs/Image", "rgb_camera", confidence=0.9),
    SensorMappingRule("/camera/color/image_raw", "sensor_msgs/Image", "rgb_camera", confidence=0.9),
    SensorMappingRule("/thermal/image_raw", "sensor_msgs/Image", "thermal_camera", confidence=0.85),
    SensorMappingRule("/gas_sensor", "std_msgs/Float32", "gas_detector", confidence=0.9),
)


def match_sensor_rule(
    topic: str,
    message_type: str,
    rules: tuple[SensorMappingRule, ...],
) -> SensorMappingRule | None:
    for rule in rules:
        if rule.message_type == message_type and fnmatch(topic, rule.topic_pattern):
            return rule
    return None


def verified_sensor_names(report: SensorDiscoveryReport) -> list[str]:
    return report.verified_sensors()
