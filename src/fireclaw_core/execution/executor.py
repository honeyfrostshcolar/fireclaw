from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable, Dict

from fireclaw_core.infra.log_redaction import redact_dict
from fireclaw_core.memory.embodied_memory import (
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryProducer,
)
from fireclaw_core.monitoring.monitor import FailurePolicy
from fireclaw_core.planner.planner import Plan
from fireclaw_core.agent.robot import RobotActionResult
from fireclaw_core.execution.skills import SkillRegistry


logger = logging.getLogger(__name__)


ExecutionEventSink = Callable[[str, Dict[str, Any]], None]
CancellationCheck = Callable[[], bool]


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
        self._last_memory_event_id: str | None = None

    def execute(
        self,
        plan: Plan,
        *,
        parent_memory_event_id: str | None = None,
    ) -> ExecutionResult:
        self._last_memory_event_id = parent_memory_event_id
        step_results: list[StepExecutionResult] = []
        for step in plan.steps:
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
                },
            )
            for attempt_number in range(1, max_attempts + 1):
                result = skill.run(
                    step.inputs,
                    cancellation_requested=self._cancellation_requested,
                )
                output = self._result_to_output(result)
                attempt_status = result.status if result.status == "cancelled" else "succeeded" if result.ok else "failed"
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
                if result.status == "cancelled" or self._cancellation_requested():
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
                )
            )
            if self._cancellation_requested():
                return ExecutionResult(status="cancelled", steps=step_results)

        return ExecutionResult(status="succeeded", steps=step_results)

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
