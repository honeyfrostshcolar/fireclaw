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
