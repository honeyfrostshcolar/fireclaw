from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol

from fireclaw_core.sensors.discovery import (
    DEFAULT_SENSOR_MAPPING_RULES,
    SensorDiscoveryReport,
    SensorFinding,
    SensorMappingRule,
    match_sensor_rule,
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

    def has_recent_message(self, topic: str, timeout_seconds: float) -> bool:
        return bool(self.topic_status.get(topic, False))


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


@dataclass
class Ros1SensorDiscovery:
    graph_provider: Ros1GraphProvider
    message_probe: Ros1MessageProbe
    extra_rules: tuple[SensorMappingRule, ...] = ()
    timeout_seconds: float = 2.0

    def discover(self) -> SensorDiscoveryReport:
        findings: list[SensorFinding] = []
        rules = self.extra_rules + DEFAULT_SENSOR_MAPPING_RULES
        try:
            topic_types = self.graph_provider.topic_types()
        except Exception as exc:
            return SensorDiscoveryReport(
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
                )
            )

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
            if self.message_probe.has_recent_message(topic, self.timeout_seconds):
                findings.append(
                    SensorFinding(
                        sensor=rule.sensor,
                        topic=topic,
                        message_type=message_type,
                        status="verified",
                        confidence=rule.confidence,
                        source=rule.source,
                    )
                )
            else:
                findings.append(
                    SensorFinding(
                        sensor=rule.sensor,
                        topic=topic,
                        message_type=message_type,
                        status="degraded",
                        confidence=rule.confidence,
                        source=rule.source,
                        reason=f"topic exists but no recent message within {self.timeout_seconds:.1f}s",
                    )
                )
        return SensorDiscoveryReport(findings=tuple(findings), source="ros1")
