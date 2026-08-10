from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Callable, Dict

from fireclaw_core.infra.log_redaction import redact_dict
from fireclaw_core.memory.embodied_memory import (
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryProducer,
)
from fireclaw_core.approval.execution_authorization import (
    VerifiedExecutionAuthorization,
    execution_action_hash,
)
from fireclaw_core.infra.runtime_state import ResourceLeaseConflict
from fireclaw_core.monitoring.monitor import FailurePolicy
from fireclaw_core.planner.planner import Plan, PlanStep
from fireclaw_core.agent.robot import RobotActionResult
from fireclaw_core.execution.skills import Skill, SkillRegistry
from fireclaw_core.policy.capability import CapabilityPolicyDecision


logger = logging.getLogger(__name__)


ExecutionEventSink = Callable[[str, Dict[str, Any]], None]
CancellationCheck = Callable[[], bool]
CapabilityPolicyCheck = Callable[
    [Skill, dict[str, Any]],
    CapabilityPolicyDecision,
]

_PROPAGATED_PHYSICAL_TERMINALS = frozenset({
    "blocked",
    "escalated",
    "timed_out",
    "cancelled",
    "lost",
})


@dataclass(frozen=True)
class StepAttemptResult:
    attempt_number: int
    status: str
    output: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class StepExecutionResult:
    skill_name: str
    inputs: dict[str, Any]
    status: str
    output: dict[str, Any] | None = None
    error: str | None = None
    attempt_count: int = 0
    attempts: list[StepAttemptResult] | None = None
    failure_category: str | None = None
    operator_action: str | None = None
    skill_domain: str | None = None
    safety_class: str | None = None
    action_binding: str | None = None


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    steps: list[StepExecutionResult]


class PlanExecutor:
    def __init__(
        self,
        registry: SkillRegistry,
        failure_policy: FailurePolicy | None = None,
        event_sink: ExecutionEventSink | None = None,
        cancellation_requested: CancellationCheck | None = None,
        memory_producer: EmbodiedMemoryProducer | None = None,
        runtime_mode: str | None = None,
        mission_id: str | None = None,
        robot_id: str | None = None,
        subtask_id: str | None = None,
        resource_lease_manager: Any | None = None,
        authorization_use_recorder: Any | None = None,
    ) -> None:
        if memory_producer is not None:
            if memory_producer.producer_type != "skill_runtime":
                raise ValueError("PlanExecutor requires a skill_runtime memory producer")
            if runtime_mode not in MEMORY_RUNTIME_MODES:
                raise ValueError(
                    "PlanExecutor requires runtime_mode to be one of: "
                    f"{sorted(MEMORY_RUNTIME_MODES)}"
                )
            if mission_id is None or not mission_id.strip():
                raise ValueError("PlanExecutor requires mission_id when memory is enabled")
        self._registry = registry
        self._failure_policy = failure_policy or FailurePolicy()
        self._event_sink = event_sink
        self._cancellation_requested = cancellation_requested or (lambda: False)
        self._memory_producer = memory_producer
        self._runtime_mode = runtime_mode
        self._mission_id = mission_id
        self._robot_id = robot_id
        self._subtask_id = subtask_id
        self._resource_lease_manager = resource_lease_manager
        self._authorization_use_recorder = authorization_use_recorder
        self._last_memory_event_id: str | None = None

    def execute(
        self,
        plan: Plan,
        *,
        parent_memory_event_id: str | None = None,
        operation_id: str | None = None,
        execution_authorization: (
            VerifiedExecutionAuthorization | None
        ) = None,
        capability_policy_check: CapabilityPolicyCheck | None = None,
    ) -> ExecutionResult:
        self._last_memory_event_id = parent_memory_event_id
        step_results: list[StepExecutionResult] = []
        for step_index, step in enumerate(plan.steps, start=1):
            if self._cancellation_requested():
                return ExecutionResult(status="cancelled", steps=step_results)
            skill = self._registry.get(step.skill_name)
            if skill is None:
                self._emit(
                    "skill.failed",
                    {
                        "skill_name": step.skill_name,
                        "inputs": step.inputs,
                        "status": "failed",
                        "error": f"Skill not registered: {step.skill_name}",
                    },
                )
                step_results.append(
                    StepExecutionResult(
                        skill_name=step.skill_name,
                        inputs=step.inputs,
                        status="failed",
                        error=f"Skill not registered: {step.skill_name}",
                    )
                )
                return ExecutionResult(status="failed", steps=step_results)

            input_errors = skill.validate_inputs(step.inputs)
            if input_errors:
                error = "; ".join(input_errors)
                self._emit(
                    "skill.failed",
                    {
                        "skill_name": step.skill_name,
                        "inputs": step.inputs,
                        "status": "failed",
                        "error": error,
                    },
                )
                step_results.append(
                    StepExecutionResult(
                        skill_name=step.skill_name,
                        inputs=step.inputs,
                        status="failed",
                        error=error,
                        skill_domain=skill.domain,
                        safety_class=(
                            skill.physical_plugin.safety_class
                            if skill.physical_plugin is not None
                            else None
                        ),
                        action_binding=(
                            skill.physical_plugin.action
                            if skill.physical_plugin is not None
                            else None
                        ),
                    )
                )
                return ExecutionResult(status="failed", steps=step_results)

            if capability_policy_check is not None:
                try:
                    capability_decision = capability_policy_check(
                        skill,
                        dict(step.inputs),
                    )
                except Exception as exc:
                    return self._capability_blocked_result(
                        step_results=step_results,
                        skill=skill,
                        step=step,
                        error=f"Capability policy evaluation failed: {exc}",
                        reason_code="capability_policy_error",
                    )
                self._emit(
                    "capability.policy_decided",
                    capability_decision.to_dict(),
                )
                if capability_decision.status != "allow":
                    return self._capability_blocked_result(
                        step_results=step_results,
                        skill=skill,
                        step=step,
                        error=capability_decision.message,
                        reason_code=capability_decision.reason_code,
                    )

            attempts: list[StepAttemptResult] = []
            max_attempts = max(1, skill.max_attempts)
            result: RobotActionResult | None = None
            output: dict[str, Any] | None = None
            failure_category: str | None = None
            operator_action: str | None = None
            self._emit(
                "skill.started",
                {
                    "skill_name": step.skill_name,
                    "inputs": step.inputs,
                    "max_attempts": max_attempts,
                    "operator_message": skill.operator_message(
                        "started",
                        step.inputs,
                    ),
                },
            )
            for attempt_number in range(1, max_attempts + 1):
                step_operation_id = (
                    operation_id
                    if len(plan.steps) == 1 and operation_id
                    else (
                        f"{operation_id}:step:{step_index}"
                        if operation_id
                        else (
                            f"{self._subtask_id or self._mission_id or 'plan'}:"
                            f"step:{step_index}"
                        )
                    )
                )
                resource_names = (
                    skill.physical_plugin.resource_locks
                    if skill.physical_plugin is not None
                    else ()
                )
                lease_owner = (
                    self._subtask_id
                    or self._mission_id
                    or self._robot_id
                    or "fireclaw-executor"
                )
                leases = ()
                attempt_result: RobotActionResult | None = None
                try:
                    if self._resource_lease_manager is not None and resource_names:
                        leases = self._resource_lease_manager.acquire(
                            robot_id=self._robot_id or "unknown-robot",
                            resource_names=tuple(resource_names),
                            owner_id=lease_owner,
                            operation_id=step_operation_id,
                            ttl_seconds=(
                                max(
                                    1.0,
                                    float(
                                        skill.timeout_seconds
                                        if skill.timeout_seconds
                                        is not None
                                        else 30.0
                                    ),
                                )
                                + 30.0
                            ),
                        )
                        self._emit(
                            "resource.acquired",
                            {
                                "skill_name": step.skill_name,
                                "operation_id": step_operation_id,
                                "resources": [
                                    lease.to_dict() for lease in leases
                                ],
                            },
                        )
                    if execution_authorization is not None:
                        if not execution_authorization.authorizes(
                            step.skill_name,
                            step.inputs,
                        ):
                            return self._resource_blocked_result(
                                step_results=step_results,
                                skill=skill,
                                step=step,
                                error=(
                                    "Execution authorization does not cover "
                                    "the exact skill inputs."
                                ),
                                operation_id=step_operation_id,
                                reason_code="authorization_action_mismatch",
                            )
                        if (
                            self._authorization_use_recorder is not None
                            and not self._authorization_use_recorder(
                                authorization_id=(
                                    execution_authorization.authorization_id
                                ),
                                operation_id=step_operation_id,
                                action_hash=execution_action_hash(
                                    step.skill_name,
                                    step.inputs,
                                ),
                                used_at=datetime.now(
                                    timezone.utc
                                ).isoformat(),
                            )
                        ):
                            return self._resource_blocked_result(
                                step_results=step_results,
                                skill=skill,
                                step=step,
                                error=(
                                    "Execution authorization use was stale "
                                    "or conflicted with another action."
                                ),
                                operation_id=step_operation_id,
                                reason_code="authorization_use_conflict",
                            )
                    result = skill.run(
                        step.inputs,
                        cancellation_requested=self._cancellation_requested,
                    )
                    attempt_result = result
                except ResourceLeaseConflict as exc:
                    return self._resource_blocked_result(
                        step_results=step_results,
                        skill=skill,
                        step=step,
                        error=str(exc),
                        operation_id=step_operation_id,
                        reason_code="resource_lease_conflict",
                    )
                finally:
                    if (
                        leases
                        and self._resource_lease_manager is not None
                    ):
                        release_safe = not (
                            attempt_result is not None
                            and attempt_result.data.get(
                                "resource_release_safe"
                            )
                            is False
                        )
                        if release_safe:
                            released = self._resource_lease_manager.release(
                                robot_id=self._robot_id or "unknown-robot",
                                owner_id=lease_owner,
                                operation_id=step_operation_id,
                                resource_names=tuple(resource_names),
                                generations={
                                    lease.resource_name: lease.generation
                                    for lease in leases
                                },
                            )
                            self._emit(
                                "resource.released",
                                {
                                    "skill_name": step.skill_name,
                                    "operation_id": step_operation_id,
                                    "resource_names": list(resource_names),
                                    "released_count": released,
                                },
                            )
                        else:
                            self._retain_unsafe_action_leases(
                                skill=skill,
                                operation_id=step_operation_id,
                                resource_names=tuple(resource_names),
                                leases=leases,
                            )
                output = self._result_to_output(result)
                attempt_status = (
                    result.status
                    if result.status in _PROPAGATED_PHYSICAL_TERMINALS
                    else "succeeded"
                    if result.ok
                    else "failed"
                )
                self._emit(
                    "skill.attempted",
                    {
                        "skill_name": step.skill_name,
                        "inputs": step.inputs,
                        "attempt_number": attempt_number,
                        "max_attempts": max_attempts,
                        "status": attempt_status,
                        "output": output,
                        "error": result.error,
                    },
                )
                attempts.append(
                    StepAttemptResult(
                        attempt_number=attempt_number,
                        status=attempt_status,
                        output=output,
                        error=result.error,
                    )
                )
                if result.ok:
                    break
                if result.status in _PROPAGATED_PHYSICAL_TERMINALS:
                    failure_category = f"physical_action_{result.status}"
                    operator_action = (
                        "stop_and_escalate"
                        if result.status == "lost"
                        else "inspect_and_recover"
                        if result.status in {"timed_out", "escalated"}
                        else "wait_or_escalate"
                    )
                    self._emit(
                        "skill.failed",
                        {
                            "skill_name": step.skill_name,
                            "inputs": step.inputs,
                            "status": result.status,
                            "output": output,
                            "error": result.error,
                            "attempt_count": len(attempts),
                            "failure_category": failure_category,
                            "operator_action": operator_action,
                        },
                    )
                    step_results.append(
                        StepExecutionResult(
                            skill_name=step.skill_name,
                            inputs=step.inputs,
                            status=result.status,
                            output=output,
                            error=result.error,
                            attempt_count=len(attempts),
                            attempts=attempts,
                            failure_category=failure_category,
                            operator_action=operator_action,
                            skill_domain=skill.domain,
                            safety_class=(
                                skill.physical_plugin.safety_class
                                if skill.physical_plugin is not None
                                else None
                            ),
                            action_binding=(
                                skill.physical_plugin.action
                                if skill.physical_plugin is not None
                                else None
                            ),
                        )
                    )
                    return ExecutionResult(
                        status=result.status,
                        steps=step_results,
                    )
                if self._cancellation_requested():
                    return ExecutionResult(status="cancelled", steps=step_results)
                decision = self._failure_policy.decide(skill=skill, attempt_number=attempt_number)
                if decision.action == "retry":
                    continue
                failure_category = decision.failure_category
                operator_action = decision.operator_action
                break
            if result is None or output is None:
                raise RuntimeError("Skill execution loop did not run.")
            if not result.ok:
                if self._cancellation_requested():
                    return ExecutionResult(status="cancelled", steps=step_results)
                self._emit(
                    "skill.failed",
                    {
                        "skill_name": step.skill_name,
                        "inputs": step.inputs,
                        "status": "failed",
                        "output": output,
                        "error": result.error,
                        "attempt_count": len(attempts),
                        "failure_category": failure_category,
                        "operator_action": operator_action,
                    },
                )
                step_results.append(
                    StepExecutionResult(
                        skill_name=step.skill_name,
                        inputs=step.inputs,
                        status="failed",
                        output=output,
                        error=result.error,
                        attempt_count=len(attempts),
                        attempts=attempts,
                        failure_category=failure_category,
                        operator_action=operator_action,
                        skill_domain=skill.domain,
                        safety_class=(
                            skill.physical_plugin.safety_class
                            if skill.physical_plugin is not None
                            else None
                        ),
                        action_binding=(
                            skill.physical_plugin.action
                            if skill.physical_plugin is not None
                            else None
                        ),
                    )
                )
                return ExecutionResult(status="failed", steps=step_results)

            self._emit(
                "skill.succeeded",
                {
                    "skill_name": step.skill_name,
                    "inputs": step.inputs,
                    "status": "succeeded",
                    "output": output,
                    "attempt_count": len(attempts),
                },
            )
            step_results.append(
                StepExecutionResult(
                    skill_name=step.skill_name,
                    inputs=step.inputs,
                    status="succeeded",
                    output=output,
                    attempt_count=len(attempts),
                    attempts=attempts,
                    skill_domain=skill.domain,
                    safety_class=(
                        skill.physical_plugin.safety_class
                        if skill.physical_plugin is not None
                        else None
                    ),
                    action_binding=(
                        skill.physical_plugin.action
                        if skill.physical_plugin is not None
                        else None
                    ),
                )
            )
            if self._cancellation_requested():
                return ExecutionResult(status="cancelled", steps=step_results)

        return ExecutionResult(status="succeeded", steps=step_results)

    def _capability_blocked_result(
        self,
        *,
        step_results: list[StepExecutionResult],
        skill: Skill,
        step: PlanStep,
        error: str,
        reason_code: str,
    ) -> ExecutionResult:
        self._emit(
            "skill.failed",
            {
                "skill_name": step.skill_name,
                "inputs": step.inputs,
                "status": "failed",
                "error": error,
                "failure_category": reason_code,
            },
        )
        step_results.append(
            StepExecutionResult(
                skill_name=step.skill_name,
                inputs=step.inputs,
                status="failed",
                error=error,
                failure_category=reason_code,
                operator_action="wait_or_escalate",
                skill_domain=skill.domain,
                safety_class=(
                    skill.physical_plugin.safety_class
                    if skill.physical_plugin is not None
                    else None
                ),
                action_binding=(
                    skill.physical_plugin.action
                    if skill.physical_plugin is not None
                    else None
                ),
            )
        )
        return ExecutionResult(status="failed", steps=step_results)

    def _resource_blocked_result(
        self,
        *,
        step_results: list[StepExecutionResult],
        skill: Skill,
        step: PlanStep,
        error: str,
        operation_id: str,
        reason_code: str,
    ) -> ExecutionResult:
        self._emit(
            "skill.failed",
            {
                "skill_name": step.skill_name,
                "inputs": step.inputs,
                "status": "failed",
                "error": error,
                "failure_category": reason_code,
                "operation_id": operation_id,
            },
        )
        step_results.append(
            StepExecutionResult(
                skill_name=step.skill_name,
                inputs=step.inputs,
                status="failed",
                error=error,
                failure_category=reason_code,
                operator_action="wait_or_escalate",
                skill_domain=skill.domain,
                safety_class=(
                    skill.physical_plugin.safety_class
                    if skill.physical_plugin is not None
                    else None
                ),
                action_binding=(
                    skill.physical_plugin.action
                    if skill.physical_plugin is not None
                    else None
                ),
            )
        )
        return ExecutionResult(status="failed", steps=step_results)

    def _result_to_output(self, result: RobotActionResult) -> dict[str, Any]:
        return {
            "status": result.status,
            "robot_id": result.robot_id,
            "mode": result.mode,
            "action": result.action,
            "dry_run": result.dry_run,
            "timestamp": result.timestamp,
            **result.data,
        }

    def _retain_unsafe_action_leases(
        self,
        *,
        skill: Skill,
        operation_id: str,
        resource_names: tuple[str, ...],
        leases: tuple[Any, ...],
    ) -> None:
        reason = "physical_runtime_stop_unconfirmed"
        close_admission = getattr(
            self._resource_lease_manager,
            "close_admission",
            None,
        )
        admission_closed = False
        if callable(close_admission):
            try:
                close_admission(
                    reason=reason,
                    task_id=(
                        self._subtask_id
                        or self._mission_id
                        or operation_id
                    ),
                    closed_at=datetime.now(timezone.utc).isoformat(),
                )
                admission_closed = True
            except Exception as exc:
                self._emit(
                    "resource.fence_failed",
                    {
                        "skill_name": skill.name,
                        "operation_id": operation_id,
                        "reason": reason,
                        "error_type": type(exc).__name__,
                    },
                )
        self._emit(
            "resource.retained",
            {
                "skill_name": skill.name,
                "operation_id": operation_id,
                "resource_names": list(resource_names),
                "reason": reason,
                "admission_closed": admission_closed,
                "leases": [lease.to_dict() for lease in leases],
            },
        )

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self._record_skill_memory(event_type, payload)
        if self._event_sink is not None:
            try:
                self._event_sink(event_type, payload)
            except Exception:
                return

    def _record_skill_memory(self, event_type: str, payload: dict[str, Any]) -> None:
        if (
            self._memory_producer is None
            or self._runtime_mode is None
            or self._mission_id is None
        ):
            return
        phase = _skill_lifecycle_phase(event_type, payload)
        if phase is None:
            return
        memory_payload = redact_dict({
            **payload,
            "lifecycle_phase": phase,
            "runtime_event_type": event_type,
        })
        output = payload.get("output")
        observed_at = output.get("timestamp") if isinstance(output, dict) else None
        output_robot_id = output.get("robot_id") if isinstance(output, dict) else None
        try:
            memory_event = self._memory_producer.record_event(
                mission_id=self._mission_id,
                event_type="skill_invocation",
                evidence_kind="runtime_evidence",
                payload=memory_payload,
                runtime_mode=self._runtime_mode,
                source_type="skill_runtime",
                robot_id=(
                    str(output_robot_id)
                    if isinstance(output_robot_id, str) and output_robot_id
                    else self._robot_id
                ),
                subtask_id=self._subtask_id,
                observed_at=(
                    str(observed_at)
                    if isinstance(observed_at, str) and observed_at
                    else None
                ),
            )
            if self._last_memory_event_id is not None:
                self._memory_producer.add_relation(
                    mission_id=self._mission_id,
                    source_record_id=memory_event.event_id,
                    target_record_id=self._last_memory_event_id,
                    relation_type="follows",
                    runtime_mode=self._runtime_mode,
                )
            self._last_memory_event_id = memory_event.event_id
        except Exception:
            logger.warning("Failed to write embodied skill lifecycle event", exc_info=True)


def _skill_lifecycle_phase(event_type: str, payload: dict[str, Any]) -> str | None:
    if event_type == "skill.started":
        return "start"
    if event_type == "skill.succeeded":
        return "result"
    if event_type == "skill.failed" and payload.get("status") == "timed_out":
        return "timeout"
    if event_type == "skill.failed" and payload.get("status") == "cancelled":
        return "cancellation"
    if event_type == "skill.failed":
        return "failure"
    if event_type != "skill.attempted":
        return None
    if payload.get("status") == "cancelled":
        return "cancellation"
    error = payload.get("error")
    if isinstance(error, str) and "timed out" in error.lower():
        return "timeout"
    return "progress"
