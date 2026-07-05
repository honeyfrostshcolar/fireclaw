from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fireclaw_core.sensors.discovery import SensorDiscoveryReport, SensorFinding


class SensorDiscoveryBackend(Protocol):
    def discover(self) -> SensorDiscoveryReport:
        ...


@dataclass(frozen=True)
class StaticDeclaredDiscoveryBackend:
    sensors: tuple[str, ...]
    source: str = "dry_run"
    allow_real_mode: bool = False

    def discover(self) -> SensorDiscoveryReport:
        findings = tuple(
            SensorFinding(
                sensor=sensor,
                topic=f"static://{sensor}",
                message_type="static/declaration",
                status="verified",
                confidence=1.0,
                source=self.source,
                reason="static declared sensor for non-real runtime",
                health_status="healthy",
            )
            for sensor in self.sensors
        )
        return SensorDiscoveryReport(findings=findings, source=self.source)


@dataclass(frozen=True)
class Ros1SensorDiscoveryBackend:
    delegate: SensorDiscoveryBackend

    @property
    def profile_fingerprint(self):
        return getattr(self.delegate, "profile_fingerprint", None)

    def discover(self) -> SensorDiscoveryReport:
        return self.delegate.discover()


def ensure_real_mode_backend_allowed(
    backend: SensorDiscoveryBackend,
    *,
    mode: str,
    dry_run: bool,
) -> None:
    if dry_run or mode in {"dry_run", "simulator", "gazebo"}:
        return
    allow_real_mode = getattr(backend, "allow_real_mode", True)
    if not allow_real_mode:
        raise ValueError("Static sensor discovery backend is not allowed for real mode")


def create_profile_sensor_discovery_backend(profile: object) -> SensorDiscoveryBackend | None:
    """Create the appropriate sensor discovery backend for a robot profile."""
    sensor_discovery = getattr(profile, "sensor_discovery", None)
    if sensor_discovery is None or not sensor_discovery.enabled:
        return None
    adapter = getattr(profile, "adapter", "")
    if adapter == "ros1":
        from fireclaw_core.ros.ros1_sensor_discovery import (
            Ros1CliGraphProvider,
            Ros1CliMessageProbe,
            Ros1SensorDiscovery,
        )

        return Ros1SensorDiscoveryBackend(
            Ros1SensorDiscovery(
                graph_provider=Ros1CliGraphProvider(),
                message_probe=Ros1CliMessageProbe(),
                extra_rules=sensor_discovery.rules,
                timeout_seconds=sensor_discovery.message_timeout_seconds,
                profile_fingerprint=getattr(profile, "discovery_fingerprint", None),
            )
        )
    if adapter in {"simulator", "dry_run", "mock"}:
        return StaticDeclaredDiscoveryBackend(
            sensors=("rgb_camera", "thermal_camera", "lidar"),
            source=adapter,
            allow_real_mode=False,
        )
    return None
