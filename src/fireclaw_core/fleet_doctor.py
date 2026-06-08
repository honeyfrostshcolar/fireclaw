from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fireclaw_core.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.subagent_client import RobotSubagentClient


@dataclass(frozen=True)
class FleetDoctorFinding:
    severity: str  # "error", "warning", "info"
    category: str  # "registry", "reachability", "capabilities", "config"
    robot_id: str | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "category": self.category,
            "robot_id": self.robot_id,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class FleetDoctor:
    """Validates fleet registry, robot reachability, and capabilities."""

    registry: RobotRegistry
    subagent_client: RobotSubagentClient = field(default_factory=RobotSubagentClient)

    def diagnose(self) -> list[FleetDoctorFinding]:
        """Run all fleet checks and return findings."""
        findings: list[FleetDoctorFinding] = []
        findings.extend(self._check_registry())
        findings.extend(self._check_reachability())
        findings.extend(self._check_capabilities())
        return findings

    def _check_registry(self) -> list[FleetDoctorFinding]:
        """Validate registry entries have required fields."""
        findings: list[FleetDoctorFinding] = []
        entries = self.registry.list_entries()
        if not entries:
            findings.append(FleetDoctorFinding(
                severity="warning",
                category="registry",
                message="Robot registry is empty.",
            ))
            return findings

        seen_ids: set[str] = set()
        for entry in entries:
            if not entry.robot_id:
                findings.append(FleetDoctorFinding(
                    severity="error",
                    category="registry",
                    robot_id=entry.robot_id,
                    message="Robot entry has empty robot_id.",
                ))
            elif entry.robot_id in seen_ids:
                findings.append(FleetDoctorFinding(
                    severity="error",
                    category="registry",
                    robot_id=entry.robot_id,
                    message=f"Duplicate robot_id: {entry.robot_id}.",
                ))
            else:
                seen_ids.add(entry.robot_id)

            if not entry.base_url:
                findings.append(FleetDoctorFinding(
                    severity="error",
                    category="registry",
                    robot_id=entry.robot_id,
                    message=f"Robot {entry.robot_id} has empty base_url.",
                ))

            if not entry.enabled:
                findings.append(FleetDoctorFinding(
                    severity="info",
                    category="registry",
                    robot_id=entry.robot_id,
                    message=f"Robot {entry.robot_id} is disabled.",
                ))
        return findings

    def _check_reachability(self) -> list[FleetDoctorFinding]:
        """Ping each enabled robot's /state endpoint."""
        findings: list[FleetDoctorFinding] = []
        for entry in self.registry.enabled_entries():
            result = self.subagent_client.check_presence(entry)
            if not result.get("online"):
                findings.append(FleetDoctorFinding(
                    severity="error",
                    category="reachability",
                    robot_id=entry.robot_id,
                    message=f"Robot {entry.robot_id} is unreachable: {result.get('error', 'unknown error')}.",
                    details={"error": result.get("error")},
                ))
            else:
                findings.append(FleetDoctorFinding(
                    severity="info",
                    category="reachability",
                    robot_id=entry.robot_id,
                    message=f"Robot {entry.robot_id} is online.",
                ))
        return findings

    def _check_capabilities(self) -> list[FleetDoctorFinding]:
        """Check that enabled robots have at least one capability."""
        findings: list[FleetDoctorFinding] = []
        for entry in self.registry.enabled_entries():
            if not entry.capabilities:
                findings.append(FleetDoctorFinding(
                    severity="warning",
                    category="capabilities",
                    robot_id=entry.robot_id,
                    message=f"Robot {entry.robot_id} has no declared capabilities.",
                ))
        return findings

    def summary(self, findings: list[FleetDoctorFinding]) -> dict[str, Any]:
        """Summarize findings by severity."""
        errors = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]
        return {
            "status": "healthy" if not errors else "unhealthy",
            "error_count": len(errors),
            "warning_count": len(warnings),
            "total_count": len(findings),
            "findings": [f.to_dict() for f in findings],
        }
