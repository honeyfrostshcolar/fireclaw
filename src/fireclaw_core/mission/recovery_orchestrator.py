from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any

from fireclaw_core.mission.completion_contract import EvidenceValidationResult
from fireclaw_core.mission.execution_event import MissionExecutionEvent
from fireclaw_core.mission.task_graph import MissionTaskNode


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    reason_code: str
    message: str
    suggested_action: str
    event: MissionExecutionEvent | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "action": self.action,
            "reason_code": self.reason_code,
            "message": self.message,
            "suggested_action": self.suggested_action,
        }
        if self.event is not None:
            result["event"] = self.event.to_dict()
        return result


class CompletionRecoveryOrchestrator:
    """Route rejected completion evidence without granting actuation authority."""

    def __init__(self, *, max_local_retries: int = 1) -> None:
        if (
            not isinstance(max_local_retries, int)
            or isinstance(max_local_retries, bool)
            or max_local_retries < 0
        ):
            raise ValueError(
                "max_local_retries must be a non-negative integer"
            )
        self.max_local_retries = max_local_retries

    def decide(
        self,
        *,
        mission_id: str,
        node: MissionTaskNode,
        robot_id: str,
        task_id: str,
        terminal_state: dict[str, Any],
        validation: EvidenceValidationResult,
        recovery_attempt: int = 0,
    ) -> RecoveryDecision:
        failed_kinds = tuple(
            check.requirement.kind
            for check in validation.checks
            if not check.satisfied
        )
        delivery_gap = not _has_structured_execution_result(terminal_state)
        policy = node.recovery_policy

        if delivery_gap and recovery_attempt < self.max_local_retries:
            event = self._event(
                mission_id=mission_id,
                node=node,
                robot_id=robot_id,
                task_id=task_id,
                terminal_state=terminal_state,
                validation=validation,
                failed_kinds=failed_kinds,
                recovery_attempt=recovery_attempt,
                event_type="transient_failure",
                suggested_action="recheck",
            )
            return RecoveryDecision(
                action="recheck",
                reason_code="completion_result_delivery_incomplete",
                message=(
                    "Completion evidence was incomplete; the runtime may "
                    "recheck the same task result once before replanning."
                ),
                suggested_action="recheck",
                event=event,
            )

        if policy in {"escalate", "abort"}:
            return RecoveryDecision(
                action=policy,
                reason_code=f"completion_contract_{policy}",
                message=(
                    "Completion evidence was rejected and the node recovery "
                    f"policy requires {policy}."
                ),
                suggested_action=policy,
            )

        suggested_action = (
            "result_recheck_exhausted"
            if delivery_gap
            else policy
        )
        event = self._event(
            mission_id=mission_id,
            node=node,
            robot_id=robot_id,
            task_id=task_id,
            terminal_state=terminal_state,
            validation=validation,
            failed_kinds=failed_kinds,
            recovery_attempt=recovery_attempt,
            event_type="completion_evidence_rejected",
            suggested_action=suggested_action,
        )
        return RecoveryDecision(
            action="replan",
            reason_code="completion_evidence_requires_plan_revision",
            message=(
                "Completion evidence changed the mission state and must be "
                "returned to the bounded planner for revision."
            ),
            suggested_action=suggested_action,
            event=event,
        )

    @staticmethod
    def _event(
        *,
        mission_id: str,
        node: MissionTaskNode,
        robot_id: str,
        task_id: str,
        terminal_state: dict[str, Any],
        validation: EvidenceValidationResult,
        failed_kinds: tuple[str, ...],
        recovery_attempt: int,
        event_type: str,
        suggested_action: str,
    ) -> MissionExecutionEvent:
        observed_at = _aware_timestamp(terminal_state)
        identity = {
            "mission_id": mission_id,
            "plan_node_id": node.node_id,
            "robot_id": robot_id,
            "task_id": task_id,
            "event_type": event_type,
            "failed_requirement_kinds": list(failed_kinds),
            "recovery_attempt": recovery_attempt,
            "validation": validation.to_dict(),
        }
        digest = sha256(
            json.dumps(
                identity,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:20]
        return MissionExecutionEvent(
            event_id=f"completion-recovery:{digest}",
            mission_id=mission_id,
            event_type=event_type,
            evidence_id=f"completion-validation:{digest}",
            source_type="execution_monitor",
            observed_at=observed_at,
            robot_id=robot_id,
            task_id=task_id,
            node_id=node.node_id,
            details={
                "failed_requirement_kinds": list(failed_kinds),
                "recovery_policy": node.recovery_policy,
                "suggested_recovery_action": suggested_action,
                "recovery_attempt": recovery_attempt,
                "reported_status": terminal_state.get("reported_status"),
            },
        )


def _has_structured_execution_result(terminal_state: dict[str, Any]) -> bool:
    robot_trace = terminal_state.get("robot_trace")
    if not isinstance(robot_trace, dict):
        return False
    result = robot_trace.get("result")
    if not isinstance(result, dict):
        return False
    execution = result.get("execution")
    if not isinstance(execution, dict):
        return False
    steps = execution.get("steps")
    return isinstance(steps, list) and bool(steps)


def _aware_timestamp(terminal_state: dict[str, Any]) -> str:
    robot_trace = terminal_state.get("robot_trace")
    result = (
        robot_trace.get("result")
        if isinstance(robot_trace, dict)
        else None
    )
    candidates = (
        terminal_state.get("updated_at"),
        terminal_state.get("ended_at"),
        result.get("timestamp") if isinstance(result, dict) else None,
    )
    for value in candidates:
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return value
    return datetime.now(timezone.utc).isoformat()
