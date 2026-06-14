from __future__ import annotations

import hashlib
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Literal


SensorFindingStatus = Literal["discovered", "verified", "degraded", "rejected"]
FingerprintComparisonStatus = Literal["fresh", "missing", "stale", "unknown"]


@dataclass(frozen=True)
class DiscoveryFingerprint:
    source: str
    topics_hash: str
    nodes_hash: str | None = None
    created_at: str | None = None
    confirmed_by: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "source": self.source,
            "topics_hash": self.topics_hash,
        }
        if self.nodes_hash is not None:
            payload["nodes_hash"] = self.nodes_hash
        if self.created_at is not None:
            payload["created_at"] = self.created_at
        if self.confirmed_by is not None:
            payload["confirmed_by"] = self.confirmed_by
        return payload


@dataclass(frozen=True)
class FingerprintComparison:
    status: FingerprintComparisonStatus
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"status": self.status}
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


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
    confirmed: bool = False
    confirmation_stale: bool = False

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "sensor": self.sensor,
            "topic": self.topic,
            "message_type": self.message_type,
            "status": self.status,
            "confidence": self.confidence,
            "source": self.source,
            "confirmed": self.confirmed,
            "confirmation_stale": self.confirmation_stale,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True)
class SensorDiscoveryReport:
    findings: tuple[SensorFinding, ...]
    source: str = "ros1"
    runtime_fingerprint: DiscoveryFingerprint | None = None
    profile_fingerprint: DiscoveryFingerprint | None = None
    fingerprint_comparison: FingerprintComparison | None = None

    def verified_sensors(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for finding in self.findings:
            if finding.status == "verified" and finding.sensor not in seen:
                seen.add(finding.sensor)
                result.append(finding.sensor)
        return result

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "source": self.source,
            "verified_sensors": self.verified_sensors(),
            "findings": [finding.to_dict() for finding in self.findings],
        }
        if self.runtime_fingerprint is not None:
            payload["runtime_fingerprint"] = self.runtime_fingerprint.to_dict()
        if self.profile_fingerprint is not None:
            payload["profile_fingerprint"] = self.profile_fingerprint.to_dict()
        if self.fingerprint_comparison is not None:
            payload["profile_fingerprint_status"] = self.fingerprint_comparison.status
            if self.fingerprint_comparison.reason is not None:
                payload["profile_fingerprint_reason"] = self.fingerprint_comparison.reason
        return payload


DEFAULT_SENSOR_MAPPING_RULES: tuple[SensorMappingRule, ...] = (
    SensorMappingRule("/scan", "sensor_msgs/LaserScan", "lidar", confidence=0.99),
    SensorMappingRule("/imu", "sensor_msgs/Imu", "imu", confidence=0.99),
    SensorMappingRule("/camera/image_raw", "sensor_msgs/Image", "rgb_camera", confidence=0.9),
    SensorMappingRule("/rgb/image_raw", "sensor_msgs/Image", "rgb_camera", confidence=0.9),
    SensorMappingRule("/camera/color/image_raw", "sensor_msgs/Image", "rgb_camera", confidence=0.9),
    SensorMappingRule("/thermal/image_raw", "sensor_msgs/Image", "thermal_camera", confidence=0.85),
    SensorMappingRule("/gas_sensor", "std_msgs/Float32", "gas_detector", confidence=0.9),
)


def fingerprint_topic_types(
    topic_types: dict[str, str],
    *,
    source: str = "ros1",
) -> DiscoveryFingerprint:
    lines = [
        f"{topic}\t{message_type}"
        for topic, message_type in sorted(topic_types.items())
    ]
    digest = hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()
    return DiscoveryFingerprint(source=source, topics_hash=f"sha256:{digest}")


def compare_fingerprints(
    runtime: DiscoveryFingerprint | None,
    profile: DiscoveryFingerprint | None,
) -> FingerprintComparison:
    if runtime is None:
        return FingerprintComparison(status="unknown", reason="runtime_fingerprint_unavailable")
    if profile is None:
        return FingerprintComparison(status="missing", reason="profile_fingerprint_missing")
    if runtime.source != profile.source:
        return FingerprintComparison(status="stale", reason="source_mismatch")
    if runtime.topics_hash != profile.topics_hash:
        return FingerprintComparison(status="stale", reason="topics_hash_mismatch")
    if runtime.nodes_hash is not None and profile.nodes_hash is not None and runtime.nodes_hash != profile.nodes_hash:
        return FingerprintComparison(status="stale", reason="nodes_hash_mismatch")
    return FingerprintComparison(status="fresh")


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
