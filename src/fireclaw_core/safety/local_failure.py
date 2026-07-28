"""Phase 2: Stronger local failure taxonomy for robot-local task execution.

Replaces ad-hoc status strings with structured failure reasons that carry
retry semantics, recovery hints, and audit-friendly categories.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FailureCategory(str, Enum):
    """High-level failure categories for robot-local execution."""

    # Infrastructure / transport failures
    TRANSPORT = "transport"
    TIMEOUT = "timeout"
    NOT_CONFIGURED = "not_configured"

    # Robot state failures
    ROBOT_OFFLINE = "robot_offline"
    LOW_BATTERY = "low_battery"
    EMERGENCY_STOP = "emergency_stop"

    # Perception / environment failures
    SENSOR_UNAVAILABLE = "sensor_unavailable"
    TARGET_UNREACHABLE = "target_unreachable"
    NO_VICTIM_FOUND = "no_victim_found"

    # Execution failures
    ACTION_FAILED = "action_failed"
    CANCELLED = "cancelled"
    PRECONDITION_FAILED = "precondition_failed"

    # Safety / authorization
    SAFETY_BLOCKED = "safety_blocked"
    AUTHORIZATION_DENIED = "authorization_denied"

    # Unknown
    UNKNOWN = "unknown"


# Which categories are safe to retry automatically
RETRYABLE_CATEGORIES: set[FailureCategory] = {
    FailureCategory.TRANSPORT,
    FailureCategory.TIMEOUT,
    FailureCategory.SENSOR_UNAVAILABLE,
}

# Which categories should never be retried
NON_RETRYABLE_CATEGORIES: set[FailureCategory] = {
    FailureCategory.ROBOT_OFFLINE,
    FailureCategory.LOW_BATTERY,
    FailureCategory.EMERGENCY_STOP,
    FailureCategory.SAFETY_BLOCKED,
    FailureCategory.AUTHORIZATION_DENIED,
    FailureCategory.NOT_CONFIGURED,
    FailureCategory.TARGET_UNREACHABLE,
}


@dataclass(frozen=True)
class LocalFailureReason:
    """Structured failure reason for robot-local task execution."""

    category: FailureCategory
    message: str
    retryable: bool
    action: str | None = None
    robot_id: str | None = None
    details: dict[str, str] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "message": self.message,
            "retryable": self.retryable,
            "action": self.action,
            "robot_id": self.robot_id,
            "details": dict(self.details) if self.details else None,
        }

    @classmethod
    def from_robot_result(
        cls,
        *,
        status: str,
        error: str | None,
        action: str,
        robot_id: str,
    ) -> LocalFailureReason:
        """Infer a LocalFailureReason from a RobotActionResult failure."""
        category = _infer_category(status, error)
        retryable = category in RETRYABLE_CATEGORIES
        return cls(
            category=category,
            message=error or f"{action} failed with status {status}",
            retryable=retryable,
            action=action,
            robot_id=robot_id,
        )


def _infer_category(status: str, error: str | None) -> FailureCategory:
    """Best-effort inference from legacy status/error strings."""
    error_lower = (error or "").lower()
    status_lower = status.lower()

    if status_lower == "not_configured":
        return FailureCategory.NOT_CONFIGURED
    if status_lower == "cancelled":
        return FailureCategory.CANCELLED
    if status_lower == "emergency_stopped":
        return FailureCategory.EMERGENCY_STOP
    if status_lower == "denied":
        return FailureCategory.AUTHORIZATION_DENIED
    if "timeout" in error_lower or "timed out" in error_lower:
        return FailureCategory.TIMEOUT
    if "offline" in error_lower:
        return FailureCategory.ROBOT_OFFLINE
    if "battery" in error_lower:
        return FailureCategory.LOW_BATTERY
    route_blocked_terms = (
        "unreachable",
        "no path",
        "path blocked",
        "route blocked",
        "blocked route",
    )
    if "sensor" in error_lower:
        return (
            FailureCategory.TARGET_UNREACHABLE
            if any(term in error_lower for term in route_blocked_terms)
            else FailureCategory.SENSOR_UNAVAILABLE
        )
    if any(term in error_lower for term in route_blocked_terms):
        return FailureCategory.TARGET_UNREACHABLE
    if "transport" in error_lower or "connection" in error_lower:
        return FailureCategory.TRANSPORT
    return FailureCategory.ACTION_FAILED
