from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any

from fireclaw_core.execution.executor import ExecutionResult, StepExecutionResult
from fireclaw_core.mission.execution_event import MissionExecutionEvent
from fireclaw_core.safety.local_failure import (
    RETRYABLE_CATEGORIES,
    FailureCategory,
    LocalFailureReason,
)


class RobotExecutionEventProducer:
    """Project final robot-local failures into a conservative mission event."""

    def produce(
        self,
        *,
        execution_result: ExecutionResult | None,
        mission_id: str,
        robot_id: str | None,
        runtime_task_id: str | None,
        plan_node_id: str | None,
    ) -> MissionExecutionEvent | None:
        if execution_result is None or execution_result.status != "failed":
            return None
        failed_step = _last_failed_step(execution_result)
        if failed_step is None:
            return None

        output = failed_step.output or {}
        resolved_robot_id = _non_empty_string(output.get("robot_id")) or robot_id
        action = (
            _non_empty_string(output.get("action"))
            or failed_step.skill_name
        )
        failure_reason, classification_basis = _failure_reason(
            failed_step,
            action=action,
            robot_id=resolved_robot_id or "unknown",
        )
        event_type = _event_type_for_failure(
            failure_reason.category,
            action=action,
            skill_name=failed_step.skill_name,
            skill_domain=failed_step.skill_domain,
            safety_class=failed_step.safety_class,
        )
        if event_type is None:
            return None

        observed_at = _aware_timestamp(output.get("timestamp"))
        action_id = _non_empty_string(output.get("action_id"))
        identity = {
            "mission_id": mission_id,
            "robot_id": resolved_robot_id,
            "runtime_task_id": runtime_task_id,
            "plan_node_id": plan_node_id,
            "skill_name": failed_step.skill_name,
            "action": action,
            "event_type": event_type,
            "action_id": action_id,
            "observed_at": observed_at,
            "attempt_count": failed_step.attempt_count,
        }
        digest = sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()[:20]
        evidence_id = (
            f"action-result:{action_id}"
            if action_id is not None
            else f"skill-result:{digest}"
        )
        source_type = (
            "robot_gateway"
            if event_type == "emergency_stop"
            else "execution_monitor"
        )
        return MissionExecutionEvent(
            event_id=f"execution-event:{digest}",
            mission_id=mission_id,
            event_type=event_type,
            evidence_id=evidence_id,
            source_type=source_type,
            observed_at=observed_at,
            robot_id=resolved_robot_id,
            task_id=runtime_task_id,
            node_id=plan_node_id,
            details={
                "classification_basis": classification_basis,
                "failure_reason": failure_reason.to_dict(),
                "skill_name": failed_step.skill_name,
                "action": action,
                "action_binding": failed_step.action_binding,
                "skill_domain": failed_step.skill_domain,
                "safety_class": failed_step.safety_class,
                "attempt_count": failed_step.attempt_count,
            },
        )


def _last_failed_step(
    execution_result: ExecutionResult,
) -> StepExecutionResult | None:
    for step in reversed(execution_result.steps):
        if step.status == "failed":
            return step
    return None


def _failure_reason(
    step: StepExecutionResult,
    *,
    action: str,
    robot_id: str,
) -> tuple[LocalFailureReason, str]:
    output = step.output or {}
    declared = output.get("failure_category")
    declared_reason = output.get("failure_reason")
    if declared is None and isinstance(declared_reason, dict):
        declared = declared_reason.get("category")
    try:
        category = (
            declared
            if isinstance(declared, FailureCategory)
            else FailureCategory(str(declared))
        )
    except (TypeError, ValueError):
        return (
            LocalFailureReason.from_robot_result(
                status=str(output.get("status") or step.status),
                error=step.error,
                action=action,
                robot_id=robot_id,
            ),
            "local_failure_inference",
        )
    declared_retryable = (
        declared_reason.get("retryable")
        if isinstance(declared_reason, dict)
        else None
    )
    return (
        LocalFailureReason(
            category=category,
            message=step.error or f"{action} failed",
            retryable=(
                declared_retryable
                if isinstance(declared_retryable, bool)
                else category in RETRYABLE_CATEGORIES
            ),
            action=action,
            robot_id=robot_id,
        ),
        "adapter_failure_category",
    )


def _event_type_for_failure(
    category: FailureCategory,
    *,
    action: str,
    skill_name: str,
    skill_domain: str | None,
    safety_class: str | None,
) -> str | None:
    if category == FailureCategory.TARGET_UNREACHABLE:
        if safety_class == "motion" or skill_domain == "navigation":
            return "route_blocked"
        return "transient_failure"
    if category in {
        FailureCategory.ROBOT_OFFLINE,
        FailureCategory.LOW_BATTERY,
    }:
        return "robot_unavailable"
    if category == FailureCategory.SENSOR_UNAVAILABLE:
        return "sensor_degraded"
    if category == FailureCategory.EMERGENCY_STOP:
        return "emergency_stop"
    if category in {
        FailureCategory.TRANSPORT,
        FailureCategory.TIMEOUT,
        FailureCategory.ACTION_FAILED,
    }:
        return "transient_failure"
    return None


def _aware_timestamp(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if (
            parsed is not None
            and parsed.tzinfo is not None
            and parsed.utcoffset() is not None
        ):
            return value
    return datetime.now(timezone.utc).isoformat()


def _non_empty_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
