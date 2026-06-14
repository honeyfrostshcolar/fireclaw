from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Protocol

from fireclaw_core.sensors.discovery import (
    DEFAULT_SENSOR_MAPPING_RULES,
    DiscoveryFingerprint,
    SensorDiscoveryReport,
    SensorFinding,
    SensorMappingRule,
    compare_fingerprints,
    fingerprint_topic_types,
    match_sensor_rule,
)
from fireclaw_core.sensors.health import (
    SensorObservation,
    evaluate_sensor_health,
)


class Ros1GraphProvider(Protocol):
    def topic_types(self) -> dict[str, str]:
        ...


class Ros1MessageProbe(Protocol):
    def has_recent_message(self, topic: str, timeout_seconds: float) -> bool:
        ...


@dataclass(frozen=True)
class StaticRos1GraphProvider:
    topics: dict[str, str]

    def topic_types(self) -> dict[str, str]:
        return dict(self.topics)


@dataclass(frozen=True)
class StaticRos1MessageProbe:
    topic_status: dict[str, bool]
    observations: dict[str, SensorObservation] | None = None

    def has_recent_message(self, topic: str, timeout_seconds: float) -> bool:
        return bool(self.topic_status.get(topic, False))

    def observe(self, topic: str, sensor: str, timeout_seconds: float) -> SensorObservation:
        if self.observations is not None and topic in self.observations:
            return self.observations[topic]
        return SensorObservation(
            observed=self.has_recent_message(topic, timeout_seconds),
            age_seconds=0.0 if self.has_recent_message(topic, timeout_seconds) else None,
            details={"inspection": "freshness_only"},
        )


@dataclass(frozen=True)
class Ros1CliGraphProvider:
    rostopic_executable: str = "rostopic"

    def topic_types(self) -> dict[str, str]:
        list_result = subprocess.run(
            [self.rostopic_executable, "list"],
            check=True,
            capture_output=True,
            text=True,
        )
        topics = [line.strip() for line in list_result.stdout.splitlines() if line.strip()]
        result: dict[str, str] = {}
        for topic in topics:
            type_result = subprocess.run(
                [self.rostopic_executable, "type", topic],
                check=False,
                capture_output=True,
                text=True,
            )
            message_type = type_result.stdout.strip()
            if type_result.returncode == 0 and message_type:
                result[topic] = message_type
        return result


@dataclass(frozen=True)
class Ros1CliMessageProbe:
    rostopic_executable: str = "rostopic"

    def has_recent_message(self, topic: str, timeout_seconds: float) -> bool:
        try:
            result = subprocess.run(
                [self.rostopic_executable, "echo", "-n", "1", topic],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0 and bool(result.stdout.strip())

    def observe(self, topic: str, sensor: str, timeout_seconds: float) -> SensorObservation:
        try:
            result = subprocess.run(
                [self.rostopic_executable, "echo", "-n", "1", topic],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return SensorObservation(observed=False)
        text = result.stdout.strip()
        return SensorObservation(
            observed=result.returncode == 0 and bool(text),
            age_seconds=0.0 if result.returncode == 0 and text else None,
            payload_size=_extract_payload_size(text, sensor),
            frame_id=_extract_frame_id(text),
            numeric_value=_extract_float_value(text),
            finite_range_count=_count_finite_ranges(text),
        )


def _extract_frame_id(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("frame_id:"):
            value = stripped.split(":", 1)[1].strip().strip("'\"")
            return value or None
    return None


def _extract_float_value(text: str) -> float | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            value = stripped.split(":", 1)[1].strip()
            try:
                return float(value)
            except ValueError:
                return None
    return None


def _extract_payload_size(text: str, sensor: str) -> int:
    if sensor in {"rgb_camera", "thermal_camera"}:
        return _count_array_items(text, "data") or 0
    return len(text.encode("utf-8")) if text else 0


def _count_array_items(text: str, field_name: str) -> int | None:
    values = _extract_array_values(text, field_name)
    if values is None:
        return None
    return len(values)


def _extract_array_values(text: str, field_name: str) -> list[str] | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(f"{field_name}:"):
            continue
        after_colon = stripped.split(":", 1)[1].strip()
        if after_colon.startswith("["):
            return _parse_inline_array(after_colon, lines[index + 1 :])
        if after_colon:
            return [after_colon]
        return _parse_block_array(lines[index + 1 :])
    return None


def _parse_inline_array(first_fragment: str, following_lines: list[str]) -> list[str]:
    fragments = [first_fragment]
    if "]" not in first_fragment:
        for line in following_lines:
            stripped = line.strip()
            fragments.append(stripped)
            if "]" in stripped:
                break
    joined = " ".join(fragments)
    start = joined.find("[")
    end = joined.find("]", start + 1)
    if start == -1 or end == -1:
        return []
    inner = joined[start + 1 : end].strip()
    if not inner:
        return []
    return [item.strip() for item in inner.split(",") if item.strip()]


def _parse_block_array(following_lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in following_lines:
        if line and not line.startswith((" ", "\t", "-")):
            break
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("-"):
            value = stripped[1:].strip()
            if value:
                values.append(value)
    return values


def _count_finite_ranges(text: str) -> int | None:
    values = _extract_array_values(text, "ranges")
    if values is None:
        return None
    count = 0
    for token in values:
        try:
            value = float(token)
        except ValueError:
            continue
        if value == value and value not in {float("inf"), float("-inf")}:
            count += 1
    return count


def _observe_topic(
    probe: Ros1MessageProbe,
    *,
    topic: str,
    sensor: str,
    timeout_seconds: float,
) -> SensorObservation:
    observe = getattr(probe, "observe", None)
    if callable(observe):
        return observe(topic, sensor, timeout_seconds)
    observed = probe.has_recent_message(topic, timeout_seconds)
    return SensorObservation(
        observed=observed,
        age_seconds=0.0 if observed else None,
        details={"inspection": "freshness_only"},
    )


@dataclass
class Ros1SensorDiscovery:
    graph_provider: Ros1GraphProvider
    message_probe: Ros1MessageProbe
    extra_rules: tuple[SensorMappingRule, ...] = ()
    profile_fingerprint: DiscoveryFingerprint | None = None
    timeout_seconds: float = 2.0
    cache_ttl_seconds: float = 5.0
    _cached_report: SensorDiscoveryReport | None = field(default=None, init=False, repr=False)
    _cached_at: float = field(default=0.0, init=False, repr=False)

    def discover(self) -> SensorDiscoveryReport:
        now = time.monotonic()
        if (
            self._cached_report is not None
            and self.cache_ttl_seconds > 0
            and now - self._cached_at <= self.cache_ttl_seconds
        ):
            return self._cached_report

        findings: list[SensorFinding] = []
        rules = self.extra_rules + DEFAULT_SENSOR_MAPPING_RULES
        try:
            topic_types = self.graph_provider.topic_types()
        except Exception as exc:
            return self._remember(
                SensorDiscoveryReport(
                    findings=(
                        SensorFinding(
                            sensor="ros1_graph",
                            topic="*",
                            message_type="unknown",
                            status="degraded",
                            confidence=0.0,
                            source="ros1",
                            reason=f"ROS1 topic discovery failed: {exc}",
                        ),
                    ),
                    runtime_fingerprint=None,
                    profile_fingerprint=self.profile_fingerprint,
                    fingerprint_comparison=compare_fingerprints(None, self.profile_fingerprint),
                )
            )

        runtime_fingerprint = fingerprint_topic_types(topic_types)
        fingerprint_comparison = compare_fingerprints(runtime_fingerprint, self.profile_fingerprint)
        confirmation_stale = fingerprint_comparison.status in {"stale", "missing", "unknown"}

        for topic, message_type in sorted(topic_types.items()):
            rule = match_sensor_rule(topic, message_type, rules)
            if rule is None:
                findings.append(
                    SensorFinding(
                        sensor="unknown",
                        topic=topic,
                        message_type=message_type,
                        status="rejected",
                        confidence=0.0,
                        source="ros1",
                        reason="no sensor mapping rule matched",
                    )
                )
                continue
            observation = _observe_topic(
                self.message_probe,
                topic=topic,
                sensor=rule.sensor,
                timeout_seconds=self.timeout_seconds,
            )
            health = evaluate_sensor_health(rule.sensor, observation)
            finding_status = "verified" if health.status == "healthy" else "degraded"
            reason = None if finding_status == "verified" else health.reason or f"sensor health is {health.status}"
            findings.append(
                SensorFinding(
                    sensor=rule.sensor,
                    topic=topic,
                    message_type=message_type,
                    status=finding_status,
                    confidence=rule.confidence,
                    source=rule.source,
                    reason=reason,
                    confirmed=rule.confirmed,
                    confirmation_stale=rule.confirmed and confirmation_stale,
                    health_status=health.status,
                    health_reason=health.reason,
                )
            )
        return self._remember(
            SensorDiscoveryReport(
                findings=tuple(findings),
                source="ros1",
                runtime_fingerprint=runtime_fingerprint,
                profile_fingerprint=self.profile_fingerprint,
                fingerprint_comparison=fingerprint_comparison,
            )
        )

    def _remember(self, report: SensorDiscoveryReport) -> SensorDiscoveryReport:
        self._cached_report = report
        self._cached_at = time.monotonic()
        return report
