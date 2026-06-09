from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict

from fireclaw_core.monitor import FailurePolicy
from fireclaw_core.planner import Plan
from fireclaw_core.robot import RobotActionResult
from fireclaw_core.skills import SkillRegistry


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
    ) -> None:
        self._registry = registry
        self._failure_policy = failure_policy or FailurePolicy()
        self._event_sink = event_sink
        self._cancellation_requested = cancellation_requested or (lambda: False)

    def execute(self, plan: Plan) -> ExecutionResult:
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
        if self._event_sink is None:
            return
        try:
            self._event_sink(event_type, payload)
        except Exception:
            return
