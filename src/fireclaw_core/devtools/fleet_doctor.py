from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fireclaw_core.lifecycle import LifecycleMaintenanceRunner
from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.subagent.subagent_client import RobotSubagentClient
from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry
from fireclaw_core.task.task_registry import JsonlTaskRegistryStore


@dataclass(frozen=True)
class FleetDoctorFinding:
    severity: str  # "error", "warning", "info"
    category: str  # "registry", "reachability", "capabilities", "onboarding"
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
    ros1_skill_remapping: dict[str, str] = field(default_factory=dict)
    approval_relay_config: dict[str, Any] | None = None
    task_registry: JsonlTaskRegistryStore | None = None
    subagent_registry: JsonlSubagentRegistry | None = None

    def diagnose(self, *, now: str | None = None, stale_threshold_seconds: float = 300.0) -> list[FleetDoctorFinding]:
        """Run all fleet checks and return findings."""
        findings: list[FleetDoctorFinding] = []
        findings.extend(self._check_registry())
        findings.extend(self._check_reachability())
        findings.extend(self._check_capabilities())
        findings.extend(self._check_onboarding())
        # Store lifecycle result for summary() to include
        self._last_lifecycle = self._check_lifecycle(now=now, stale_threshold_seconds=stale_threshold_seconds)
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
            checked_at = datetime.now(timezone.utc).isoformat()
            if not result.get("online"):
                findings.append(FleetDoctorFinding(
                    severity="error",
                    category="reachability",
                    robot_id=entry.robot_id,
                    message=f"Robot {entry.robot_id} is unreachable: {result.get('error', 'unknown error')}.",
                    details={
                        "online": False,
                        "last_seen_at": result.get("last_seen_at"),
                        "checked_at": checked_at,
                        "error": result.get("error"),
                    },
                ))
            else:
                findings.append(FleetDoctorFinding(
                    severity="info",
                    category="reachability",
                    robot_id=entry.robot_id,
                    message=f"Robot {entry.robot_id} is online.",
                    details={
                        "online": True,
                        "last_seen_at": result.get("last_seen_at"),
                        "checked_at": checked_at,
                        "state": result.get("state"),
                    },
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

    def _check_onboarding(self) -> list[FleetDoctorFinding]:
        """Produce an onboarding readiness report for the fleet."""
        findings: list[FleetDoctorFinding] = []
        all_entries = self.registry.list_entries()
        enabled_entries = [e for e in all_entries if e.enabled]

        # 1. Enrolled robots count
        findings.append(FleetDoctorFinding(
            severity="info",
            category="onboarding",
            message=f"{len(all_entries)} enrolled robot(s) in registry.",
            details={"total": len(all_entries)},
        ))

        # 2. Enabled robots count
        findings.append(FleetDoctorFinding(
            severity="info",
            category="onboarding",
            message=f"{len(enabled_entries)} enabled robot(s).",
            details={"count": len(enabled_entries)},
        ))

        # 3. Stale heartbeats
        stale_entries = self.registry.stale_entries()
        if stale_entries:
            stale_ids = [e.robot_id for e in stale_entries]
            findings.append(FleetDoctorFinding(
                severity="warning",
                category="onboarding",
                message=f"{len(stale_entries)} robot(s) have stale or missing heartbeats: {', '.join(stale_ids)}.",
                details={"stale_robot_ids": stale_ids},
            ))

        # 4. Missing ROS1 remaps (only when explicitly configured)
        if self.ros1_skill_remapping:
            all_capabilities: set[str] = set()
            for entry in enabled_entries:
                all_capabilities.update(entry.capabilities)
            missing_remaps = sorted(cap for cap in all_capabilities if cap not in self.ros1_skill_remapping)
            if missing_remaps:
                findings.append(FleetDoctorFinding(
                    severity="warning",
                    category="onboarding",
                    message=f"Capabilities missing ROS1 remaps: {', '.join(missing_remaps)}.",
                    details={"missing_remaps": missing_remaps},
                ))

        # 5. Missing emergency stop capability (fleet-level: warn if no robot has it)
        if enabled_entries and not any("emergency_stop" in e.capabilities for e in enabled_entries):
            findings.append(FleetDoctorFinding(
                severity="warning",
                category="onboarding",
                message="No enabled robot declares emergency_stop capability.",
            ))

        # 6. Unresolved approval relay channel
        if self.approval_relay_config is not None:
            if self.approval_relay_config.get("enabled") and not self.approval_relay_config.get("channel"):
                findings.append(FleetDoctorFinding(
                    severity="warning",
                    category="onboarding",
                    message="Approval relay is enabled but no channel metadata is configured.",
                ))

        return findings

    def _check_lifecycle(
        self,
        *,
        now: str | None = None,
        stale_threshold_seconds: float = 300.0,
    ) -> dict[str, Any] | None:
        """Run lifecycle maintenance when both registries are provided."""
        if self.task_registry is None or self.subagent_registry is None:
            return None
        runner = LifecycleMaintenanceRunner(
            task_registry=self.task_registry,
            subagent_registry=self.subagent_registry,
        )
        return runner.run(now=now, stale_threshold_seconds=stale_threshold_seconds)

    def summary(
        self,
        findings: list[FleetDoctorFinding],
        lifecycle_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Summarize findings by severity.

        Args:
            findings: Findings produced by :meth:`diagnose`.
            lifecycle_report: Optional explicit lifecycle report.  When
                ``None``, falls back to the lifecycle data stored by the
                most recent :meth:`diagnose()` call (``self._last_lifecycle``).
        """
        errors = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]
        result: dict[str, Any] = {
            "status": "healthy" if not errors else "unhealthy",
            "error_count": len(errors),
            "warning_count": len(warnings),
            "total_count": len(findings),
            "findings": [f.to_dict() for f in findings],
        }
        lifecycle = lifecycle_report if lifecycle_report is not None else getattr(self, "_last_lifecycle", None)
        if lifecycle is not None:
            result["lifecycle"] = {
                "status": lifecycle["status"],
                "stale_tasks": lifecycle["stale_tasks"],
                "orphaned_subagents": lifecycle["orphaned_subagents"],
                "checked_at": lifecycle["checked_at"],
            }
        return result
