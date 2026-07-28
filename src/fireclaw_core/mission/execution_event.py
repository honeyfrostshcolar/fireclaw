from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


INVALIDATING_EXECUTION_EVENTS = frozenset({
    "route_blocked",
    "robot_unavailable",
    "evidence_expired",
    "resource_conflict",
    "partial_success",
    "sensor_degraded",
    "hazard_changed",
    "completion_evidence_rejected",
    "belief_requirement_failed",
})
RETRYABLE_EXECUTION_EVENTS = frozenset({
    "transient_failure",
    "communication_timeout",
})
ESCALATING_EXECUTION_EVENTS = frozenset({
    "safety_blocked",
    "emergency_stop",
    "operator_cancelled",
    "authority_denied",
})
NON_INVALIDATING_EXECUTION_EVENTS = frozenset({
    "task_succeeded",
    "progress_update",
})
VALID_EXECUTION_EVENT_TYPES = frozenset({
    *INVALIDATING_EXECUTION_EVENTS,
    *RETRYABLE_EXECUTION_EVENTS,
    *ESCALATING_EXECUTION_EVENTS,
    *NON_INVALIDATING_EXECUTION_EVENTS,
})
AUTHORITATIVE_EXECUTION_EVENT_SOURCES = frozenset({
    "execution_monitor",
    "robot_gateway",
    "map_fusion",
    "resource_manager",
    "safety_gate",
})
EXECUTION_EVENT_SOURCE_POLICY = {
    "route_blocked": frozenset({
        "execution_monitor",
        "robot_gateway",
        "map_fusion",
    }),
    "robot_unavailable": frozenset({"execution_monitor", "robot_gateway"}),
    "evidence_expired": frozenset({"execution_monitor", "map_fusion"}),
    "resource_conflict": frozenset({
        "execution_monitor",
        "resource_manager",
    }),
    "partial_success": frozenset({"execution_monitor", "robot_gateway"}),
    "sensor_degraded": frozenset({"execution_monitor", "robot_gateway"}),
    "hazard_changed": frozenset({
        "execution_monitor",
        "robot_gateway",
        "map_fusion",
    }),
    "completion_evidence_rejected": frozenset({"execution_monitor"}),
    "belief_requirement_failed": frozenset({"safety_gate"}),
    "transient_failure": frozenset({"execution_monitor", "robot_gateway"}),
    "communication_timeout": frozenset({"execution_monitor"}),
    "safety_blocked": frozenset({"safety_gate"}),
    "emergency_stop": frozenset({"safety_gate", "robot_gateway"}),
    "operator_cancelled": frozenset({"execution_monitor"}),
    "authority_denied": frozenset({"safety_gate"}),
    "task_succeeded": frozenset({"execution_monitor", "robot_gateway"}),
    "progress_update": frozenset({"execution_monitor", "robot_gateway"}),
}


@dataclass(frozen=True)
class MissionExecutionEvent:
    """Lightweight contract shared by robot gateways and mission control."""

    event_id: str
    mission_id: str
    event_type: str
    evidence_id: str
    source_type: str
    observed_at: str
    robot_id: str | None = None
    task_id: str | None = None
    node_id: str | None = None
    resource_id: str | None = None
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("event_id", self.event_id),
            ("mission_id", self.mission_id),
            ("event_type", self.event_type),
            ("evidence_id", self.evidence_id),
            ("source_type", self.source_type),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Mission execution event {name} must not be empty."
                )
        if self.event_type not in VALID_EXECUTION_EVENT_TYPES:
            raise ValueError(
                f"Unsupported mission execution event type: {self.event_type}"
            )
        if self.source_type not in AUTHORITATIVE_EXECUTION_EVENT_SOURCES:
            raise ValueError(
                f"Untrusted mission execution event source: {self.source_type}"
            )
        allowed_sources = EXECUTION_EVENT_SOURCE_POLICY[self.event_type]
        if self.source_type not in allowed_sources:
            raise ValueError(
                "Mission execution event source is not authoritative for "
                f"{self.event_type}: {self.source_type}"
            )
        if not _valid_aware_timestamp(self.observed_at):
            raise ValueError(
                "Mission execution event observed_at must be timezone-aware "
                "ISO-8601."
            )
        for name, value in (
            ("robot_id", self.robot_id),
            ("task_id", self.task_id),
            ("node_id", self.node_id),
            ("resource_id", self.resource_id),
        ):
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(
                    f"Mission execution event {name} must be a non-empty string."
                )
        if self.details is not None and not isinstance(self.details, dict):
            raise ValueError("Mission execution event details must be an object.")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "event_id": self.event_id,
            "mission_id": self.mission_id,
            "event_type": self.event_type,
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "observed_at": self.observed_at,
        }
        for name, value in (
            ("robot_id", self.robot_id),
            ("task_id", self.task_id),
            ("node_id", self.node_id),
            ("resource_id", self.resource_id),
        ):
            if value is not None:
                result[name] = value
        if self.details:
            result["details"] = dict(self.details)
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MissionExecutionEvent:
        if not isinstance(value, dict):
            raise ValueError("Mission execution event must be an object.")
        return cls(
            event_id=value.get("event_id"),
            mission_id=value.get("mission_id"),
            event_type=value.get("event_type"),
            evidence_id=value.get("evidence_id"),
            source_type=value.get("source_type"),
            observed_at=value.get("observed_at"),
            robot_id=value.get("robot_id"),
            task_id=value.get("task_id"),
            node_id=value.get("node_id"),
            resource_id=value.get("resource_id"),
            details=value.get("details"),
        )


def _valid_aware_timestamp(value: str) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None
