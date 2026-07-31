"""Canonical Robot Agent task terminal outcomes.

The Robot Agent result payload may retain a legacy/raw status for compatibility,
but Gateway, task trace, Mission Registry, and Mission Scheduler must project
one canonical terminal outcome.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, cast


RobotTaskTerminalStatus = Literal[
    "completed",
    "blocked",
    "escalated",
    "failed",
    "timed_out",
    "cancelled",
    "lost",
]

ROBOT_TASK_TERMINAL_STATUSES = frozenset({
    "completed",
    "blocked",
    "escalated",
    "failed",
    "timed_out",
    "cancelled",
    "lost",
})
ROBOT_TASK_SUCCESS_STATUSES = frozenset({"completed"})
ROBOT_TASK_INTERVENTION_STATUSES = frozenset({"blocked", "escalated"})
ROBOT_TASK_FAILURE_STATUSES = frozenset({
    "blocked",
    "escalated",
    "failed",
    "timed_out",
    "lost",
})
ROBOT_TASK_ACTIVE_STATUSES = frozenset({
    "accepted",
    "queued",
    "received",
    "planned",
    "running",
    "cancel_requested",
})

ROBOT_TASK_TERMINAL_EVENT_BY_STATUS = {
    "completed": "task.completed",
    "blocked": "task.blocked",
    "escalated": "task.escalated",
    "failed": "task.failed",
    "timed_out": "task.timed_out",
    "cancelled": "task.cancelled",
    "lost": "task.lost",
}
ROBOT_TASK_STATUS_BY_TERMINAL_EVENT = {
    event_type: status
    for status, event_type in ROBOT_TASK_TERMINAL_EVENT_BY_STATUS.items()
}
ROBOT_TASK_TERMINAL_EVENT_TYPES = frozenset(
    ROBOT_TASK_STATUS_BY_TERMINAL_EVENT
)

_LEGACY_STATUS_ALIASES = {
    "succeeded": "completed",
    "success": "completed",
    "done": "completed",
    "block": "blocked",
    "denied": "blocked",
    "clarify": "escalated",
    "approval_required": "escalated",
    "awaiting_confirmation": "escalated",
    "error": "failed",
    "aborted": "failed",
    "timeout": "timed_out",
    "orphaned": "lost",
}


@dataclass(frozen=True)
class RobotTaskTerminalOutcome:
    status: RobotTaskTerminalStatus
    event_type: str
    raw_status: str | None
    successful: bool
    requires_intervention: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_robot_task_terminal_status(
    value: Any,
) -> RobotTaskTerminalStatus | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip().lower()
    candidate = _LEGACY_STATUS_ALIASES.get(candidate, candidate)
    if candidate not in ROBOT_TASK_TERMINAL_STATUSES:
        return None
    return cast(RobotTaskTerminalStatus, candidate)


def build_robot_task_terminal_outcome(
    raw_status: Any,
    *,
    cancellation_requested: bool = False,
) -> RobotTaskTerminalOutcome:
    normalized = (
        cast(RobotTaskTerminalStatus, "cancelled")
        if cancellation_requested
        else normalize_robot_task_terminal_status(raw_status)
    )
    if normalized is None:
        normalized = cast(RobotTaskTerminalStatus, "failed")
    raw = raw_status.strip() if isinstance(raw_status, str) and raw_status.strip() else None
    return RobotTaskTerminalOutcome(
        status=normalized,
        event_type=ROBOT_TASK_TERMINAL_EVENT_BY_STATUS[normalized],
        raw_status=raw,
        successful=normalized in ROBOT_TASK_SUCCESS_STATUSES,
        requires_intervention=normalized in ROBOT_TASK_INTERVENTION_STATUSES,
    )


def robot_task_terminal_status_from_event(
    event: dict[str, Any],
) -> RobotTaskTerminalStatus | None:
    event_type = event.get("type") or event.get("event_type")
    if not isinstance(event_type, str):
        return None
    mapped = ROBOT_TASK_STATUS_BY_TERMINAL_EVENT.get(event_type)
    if mapped is None:
        return None
    if mapped == "cancelled":
        return cast(RobotTaskTerminalStatus, "cancelled")

    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    explicit = normalize_robot_task_terminal_status(payload.get("status"))
    if explicit is None:
        terminal_outcome = payload.get("terminal_outcome")
        if isinstance(terminal_outcome, dict):
            explicit = normalize_robot_task_terminal_status(
                terminal_outcome.get("status")
            )
    if explicit is None:
        result = payload.get("result")
        if isinstance(result, dict):
            terminal_outcome = result.get("terminal_outcome")
            if isinstance(terminal_outcome, dict):
                explicit = normalize_robot_task_terminal_status(
                    terminal_outcome.get("status")
                )
            if explicit is None:
                explicit = normalize_robot_task_terminal_status(
                    result.get("status")
                )
    if explicit is not None:
        return explicit
    return cast(RobotTaskTerminalStatus, mapped)


def robot_task_terminal_status_from_trace(
    trace: dict[str, Any],
) -> RobotTaskTerminalStatus | None:
    """Resolve one terminal outcome without trusting wrapper event names.

    Explicit normalized outcome data wins over compatibility wrappers. A
    cancellation event remains sticky because it proves an operator/runtime
    cancellation boundary.
    """

    events = trace.get("events")
    if isinstance(events, list):
        for event in reversed(events):
            if (
                isinstance(event, dict)
                and robot_task_terminal_status_from_event(event) == "cancelled"
            ):
                return cast(RobotTaskTerminalStatus, "cancelled")

    for value in (
        trace.get("terminal_outcome"),
        (
            trace.get("result", {}).get("terminal_outcome")
            if isinstance(trace.get("result"), dict)
            else None
        ),
    ):
        if isinstance(value, dict):
            status = normalize_robot_task_terminal_status(value.get("status"))
            if status is not None:
                return status

    result = trace.get("result")
    if isinstance(result, dict):
        status = normalize_robot_task_terminal_status(result.get("status"))
        if status is not None:
            return status

    status = normalize_robot_task_terminal_status(trace.get("status"))
    if status is not None:
        return status

    queue_record = trace.get("queue_record")
    if isinstance(queue_record, dict):
        status = normalize_robot_task_terminal_status(
            queue_record.get("status")
        )
        if status is not None:
            return status

    if isinstance(events, list):
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            status = robot_task_terminal_status_from_event(event)
            if status is not None:
                return status
    return None


def robot_task_status_from_trace(trace: dict[str, Any]) -> str | None:
    """Resolve a canonical terminal outcome or an active runtime status."""

    terminal_status = robot_task_terminal_status_from_trace(trace)
    if terminal_status is not None:
        return terminal_status

    queue_record = trace.get("queue_record")
    candidates = (
        queue_record.get("status")
        if isinstance(queue_record, dict)
        else None,
        trace.get("status"),
        (
            trace.get("result", {}).get("status")
            if isinstance(trace.get("result"), dict)
            else None
        ),
    )
    for value in candidates:
        if (
            isinstance(value, str)
            and value.strip().lower() in ROBOT_TASK_ACTIVE_STATUSES
        ):
            return value.strip().lower()
    return None
