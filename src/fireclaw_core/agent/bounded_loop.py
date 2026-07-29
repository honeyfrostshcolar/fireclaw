"""Shared bounded decision/operation/observation loop for FireClaw agents."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import time
from typing import Any, Generic, Literal, Protocol, TypeVar

from fireclaw_core.agent.loop_checkpoint import (
    AgentLoopCheckpoint,
    AgentLoopCheckpointStore,
    AgentLoopPendingOperation,
)


class AgentLoopDecision(Protocol):
    operation: str


DecisionT = TypeVar("DecisionT", bound=AgentLoopDecision)
ObservationT = TypeVar("ObservationT")
ResultT = TypeVar("ResultT")

AgentLoopStatus = Literal[
    "continue",
    "completed",
    "blocked",
    "escalated",
    "failed",
    "cancelled",
    "timed_out",
]


@dataclass(frozen=True)
class AgentLoopLimits:
    max_iterations: int = 6
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_iterations <= 0:
            raise ValueError("max_iterations must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True)
class AgentLoopTurn(Generic[ObservationT]):
    run_id: str
    iteration: int
    operation_id: str
    observations: tuple[ObservationT, ...]
    remaining_iterations: int
    elapsed_seconds: float


@dataclass(frozen=True)
class AgentLoopTransition(Generic[ObservationT, ResultT]):
    status: AgentLoopStatus
    operation: str
    message: str
    observation: ObservationT | None = None
    result: ResultT | None = None
    reason_code: str | None = None

    @classmethod
    def continuing(
        cls,
        *,
        operation: str,
        message: str,
        observation: ObservationT,
        result: ResultT | None = None,
        reason_code: str | None = None,
    ) -> "AgentLoopTransition[ObservationT, ResultT]":
        return cls(
            status="continue",
            operation=operation,
            message=message,
            observation=observation,
            result=result,
            reason_code=reason_code,
        )


@dataclass(frozen=True)
class AgentLoopAttempt:
    iteration: int
    operation: str
    outcome: str
    started_at: str
    duration_ms: float
    reason_code: str | None = None
    observation: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AgentLoopAttempt:
        return cls(
            iteration=int(value.get("iteration") or 0),
            operation=str(value.get("operation") or ""),
            outcome=str(value.get("outcome") or ""),
            started_at=str(value.get("started_at") or ""),
            duration_ms=float(value.get("duration_ms") or 0.0),
            reason_code=(
                value["reason_code"]
                if isinstance(value.get("reason_code"), str)
                else None
            ),
            observation=value.get("observation"),
        )


@dataclass(frozen=True)
class AgentLoopResult(Generic[ObservationT, ResultT]):
    run_id: str
    status: ExcludeContinueStatus
    message: str
    reason_code: str
    started_at: str
    completed_at: str
    attempts: tuple[AgentLoopAttempt, ...]
    observations: tuple[ObservationT, ...]
    result: ResultT | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "message": self.message,
            "reason_code": self.reason_code,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "observations": [_jsonable(item) for item in self.observations],
            "result": _jsonable(self.result),
        }


ExcludeContinueStatus = Literal[
    "completed",
    "blocked",
    "escalated",
    "failed",
    "cancelled",
    "timed_out",
]


class BoundedAgentLoop(Generic[DecisionT, ObservationT, ResultT]):
    """Run a role-specific policy through a host-enforced bounded loop.

    The shared loop owns lifecycle controls and trace shape. Role adapters own
    context assembly, decision validation, tool policy, and operation execution.
    A continuing transition must include an observation so the next model turn
    has concrete evidence of progress.
    """

    def __init__(
        self,
        *,
        limits: AgentLoopLimits | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        checkpoint_store: AgentLoopCheckpointStore | None = None,
        checkpoint_role: str = "agent",
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.limits = limits or AgentLoopLimits()
        self.cancellation_requested = cancellation_requested
        self.checkpoint_store = checkpoint_store
        self.checkpoint_role = checkpoint_role
        self._monotonic = monotonic
        self._now = now or (lambda: datetime.now(timezone.utc))

    def run(
        self,
        *,
        run_id: str,
        decide: Callable[[AgentLoopTurn[ObservationT]], DecisionT],
        execute: Callable[
            [DecisionT, AgentLoopTurn[ObservationT]],
            AgentLoopTransition[ObservationT, ResultT],
        ],
        checkpoint_key: str | None = None,
        resume_checkpoint: AgentLoopCheckpoint | None = None,
        observation_from_checkpoint: (
            Callable[[Any], ObservationT] | None
        ) = None,
        adapter_state_provider: (
            Callable[[], dict[str, Any]] | None
        ) = None,
        requires_reconciliation: (
            Callable[[DecisionT], bool] | None
        ) = None,
        reconcile_pending: (
            Callable[
                [
                    AgentLoopPendingOperation,
                    AgentLoopTurn[ObservationT],
                ],
                AgentLoopTransition[ObservationT, ResultT],
            ]
            | None
        ) = None,
    ) -> AgentLoopResult[ObservationT, ResultT]:
        if self.checkpoint_store is not None and not checkpoint_key:
            raise ValueError(
                "checkpoint_key is required when checkpoint_store is configured"
            )
        if resume_checkpoint is not None:
            self._validate_resume_checkpoint(
                resume_checkpoint,
                run_id=run_id,
                checkpoint_key=checkpoint_key,
            )
            started_at = resume_checkpoint.started_at
            elapsed = self._resumed_elapsed_seconds(resume_checkpoint)
            started = self._monotonic() - elapsed
            attempts = [
                AgentLoopAttempt.from_dict(item)
                for item in resume_checkpoint.attempts
            ]
            restore = observation_from_checkpoint or (lambda value: value)
            observations = [
                restore(value) for value in resume_checkpoint.observations
            ]
            next_iteration = resume_checkpoint.next_iteration
            pending_operation = resume_checkpoint.pending_operation
        else:
            started_at = self._timestamp()
            started = self._monotonic()
            attempts = []
            observations = []
            next_iteration = 1
            pending_operation = None

        def save_checkpoint(
            *,
            status: str,
            next_turn: int,
            pending: AgentLoopPendingOperation | None = None,
            result: ResultT | None = None,
            message: str | None = None,
            reason_code: str | None = None,
        ) -> None:
            if self.checkpoint_store is None or checkpoint_key is None:
                return
            adapter_state = (
                adapter_state_provider()
                if adapter_state_provider is not None
                else {}
            )
            self.checkpoint_store.append(AgentLoopCheckpoint(
                checkpoint_key=checkpoint_key,
                role=self.checkpoint_role,
                run_id=run_id,
                status=status,
                next_iteration=max(1, next_turn),
                started_at=started_at,
                elapsed_seconds=max(
                    0.0,
                    self._monotonic() - started,
                ),
                attempts=tuple(attempt.to_dict() for attempt in attempts),
                observations=tuple(
                    _jsonable(observation)
                    for observation in observations
                ),
                pending_operation=pending,
                adapter_state=adapter_state,
                result=_jsonable(result),
                message=message,
                reason_code=reason_code,
                updated_at=self._timestamp(),
            ))

        def finish(
            *,
            status: ExcludeContinueStatus,
            message: str,
            reason_code: str,
            result: ResultT | None = None,
            next_turn: int,
            persist: bool = True,
        ) -> AgentLoopResult[ObservationT, ResultT]:
            terminal = self._terminal(
                run_id=run_id,
                status=status,
                message=message,
                reason_code=reason_code,
                started_at=started_at,
                attempts=attempts,
                observations=observations,
                result=result,
            )
            if persist:
                save_checkpoint(
                    status=status,
                    next_turn=next_turn,
                    result=result,
                    message=message,
                    reason_code=reason_code,
                )
            return terminal

        try:
            save_checkpoint(
                status="running",
                next_turn=next_iteration,
                pending=pending_operation,
            )
        except Exception:
            return self._terminal(
                run_id=run_id,
                status="failed",
                message="Agent loop checkpoint could not be persisted.",
                reason_code="checkpoint_error",
                started_at=started_at,
                attempts=attempts,
                observations=observations,
            )

        if pending_operation is not None:
            iteration = pending_operation.iteration
            turn = self._turn(
                run_id=run_id,
                iteration=iteration,
                started=started,
                observations=observations,
            )
            reconcile_started_at = self._timestamp()
            reconcile_started = self._monotonic()
            if reconcile_pending is None:
                attempts.append(self._attempt(
                    iteration=iteration,
                    operation=pending_operation.operation,
                    outcome="blocked",
                    started_at=reconcile_started_at,
                    started=reconcile_started,
                    reason_code="pending_operation_requires_reconciliation",
                ))
                return finish(
                    status="blocked",
                    message=(
                        "Agent loop has an unresolved pending operation and "
                        "cannot replay it automatically."
                    ),
                    reason_code="pending_operation_requires_reconciliation",
                    next_turn=iteration,
                    persist=False,
                )
            try:
                transition = reconcile_pending(pending_operation, turn)
            except Exception:
                attempts.append(self._attempt(
                    iteration=iteration,
                    operation=pending_operation.operation,
                    outcome="failed",
                    started_at=reconcile_started_at,
                    started=reconcile_started,
                    reason_code="reconciliation_error",
                ))
                return finish(
                    status="escalated",
                    message="Pending operation reconciliation failed.",
                    reason_code="reconciliation_error",
                    next_turn=iteration,
                    persist=False,
                )
            no_progress = (
                transition.status == "continue"
                and transition.observation is None
            )
            attempts.append(self._attempt(
                iteration=iteration,
                operation=transition.operation,
                outcome=(
                    "reconciled"
                    if transition.status == "continue"
                    else transition.status
                ),
                started_at=reconcile_started_at,
                started=reconcile_started,
                reason_code=transition.reason_code,
                observation=transition.observation,
            ))
            if transition.observation is not None:
                observations.append(transition.observation)
            if no_progress:
                return finish(
                    status="blocked",
                    message=(
                        "Pending operation reconciliation produced no "
                        "observation."
                    ),
                    reason_code="reconciliation_no_progress",
                    next_turn=iteration,
                    persist=False,
                )
            if transition.status != "continue":
                return finish(
                    status=transition.status,
                    message=transition.message,
                    reason_code=(
                        transition.reason_code or transition.status
                    ),
                    result=transition.result,
                    next_turn=iteration + 1,
                    persist=transition.observation is not None,
                )
            next_iteration = iteration + 1
            try:
                save_checkpoint(
                    status="running",
                    next_turn=next_iteration,
                )
            except Exception:
                return finish(
                    status="escalated",
                    message=(
                        "Reconciled operation could not be committed to the "
                        "agent checkpoint."
                    ),
                    reason_code="checkpoint_error",
                    next_turn=next_iteration,
                    persist=False,
                )

        for iteration in range(
            next_iteration,
            self.limits.max_iterations + 1,
        ):
            if self._cancelled():
                return finish(
                    status="cancelled",
                    message="Agent loop was cancelled.",
                    reason_code="cancelled",
                    next_turn=iteration,
                )
            if self._timed_out(started):
                return finish(
                    status="timed_out",
                    message="Agent loop exceeded its time limit.",
                    reason_code="loop_timeout",
                    next_turn=iteration,
                )
            turn = self._turn(
                run_id=run_id,
                iteration=iteration,
                started=started,
                observations=observations,
            )
            attempt_started_at = self._timestamp()
            attempt_started = self._monotonic()
            try:
                decision = decide(turn)
            except Exception:
                return finish(
                    status="failed",
                    message="Agent decision policy failed.",
                    reason_code="policy_error",
                    next_turn=iteration,
                )
            operation = getattr(decision, "operation", "")
            if not isinstance(operation, str) or not operation:
                attempts.append(
                    self._attempt(
                        iteration=iteration,
                        operation="invalid_decision",
                        outcome="rejected",
                        started_at=attempt_started_at,
                        started=attempt_started,
                        reason_code="invalid_decision",
                    )
                )
                return finish(
                    status="blocked",
                    message="Agent policy returned an invalid decision.",
                    reason_code="invalid_decision",
                    next_turn=iteration + 1,
                )
            if self._cancelled():
                attempts.append(
                    self._attempt(
                        iteration=iteration,
                        operation=operation,
                        outcome="cancelled",
                        started_at=attempt_started_at,
                        started=attempt_started,
                        reason_code="cancelled",
                    )
                )
                return finish(
                    status="cancelled",
                    message="Agent loop was cancelled.",
                    reason_code="cancelled",
                    next_turn=iteration,
                )
            if self._timed_out(started):
                attempts.append(
                    self._attempt(
                        iteration=iteration,
                        operation=operation,
                        outcome="timed_out",
                        started_at=attempt_started_at,
                        started=attempt_started,
                        reason_code="loop_timeout",
                    )
                )
                return finish(
                    status="timed_out",
                    message="Agent loop exceeded its time limit.",
                    reason_code="loop_timeout",
                    next_turn=iteration,
                )
            pending = None
            if (
                requires_reconciliation is not None
                and requires_reconciliation(decision)
            ):
                decision_payload = _jsonable(decision)
                pending = AgentLoopPendingOperation(
                    operation_id=turn.operation_id,
                    iteration=iteration,
                    operation=operation,
                    decision=(
                        decision_payload
                        if isinstance(decision_payload, dict)
                        else {"value": decision_payload}
                    ),
                    prepared_at=self._timestamp(),
                )
                try:
                    save_checkpoint(
                        status="running",
                        next_turn=iteration,
                        pending=pending,
                    )
                except Exception:
                    attempts.append(self._attempt(
                        iteration=iteration,
                        operation=operation,
                        outcome="rejected",
                        started_at=attempt_started_at,
                        started=attempt_started,
                        reason_code="checkpoint_error",
                    ))
                    return finish(
                        status="failed",
                        message=(
                            "Agent operation was not started because its "
                            "checkpoint could not be persisted."
                        ),
                        reason_code="checkpoint_error",
                        next_turn=iteration,
                        persist=False,
                    )
            try:
                transition = execute(decision, turn)
            except Exception:
                attempts.append(
                    self._attempt(
                        iteration=iteration,
                        operation=operation,
                        outcome="failed",
                        started_at=attempt_started_at,
                        started=attempt_started,
                        reason_code="operation_error",
                    )
                )
                return finish(
                    status=(
                        "escalated" if pending is not None else "failed"
                    ),
                    message=(
                        "Agent operation outcome is unknown and requires "
                        "reconciliation."
                        if pending is not None
                        else "Agent operation handler failed."
                    ),
                    reason_code=(
                        "operation_outcome_unknown"
                        if pending is not None
                        else "operation_error"
                    ),
                    next_turn=iteration,
                    persist=pending is None,
                )
            if transition.status == "continue" and transition.observation is None:
                attempts.append(
                    self._attempt(
                        iteration=iteration,
                        operation=operation,
                        outcome="rejected",
                        started_at=attempt_started_at,
                        started=attempt_started,
                        reason_code="no_progress",
                    )
                )
                return finish(
                    status="blocked",
                    message="Agent operation continued without an observation.",
                    reason_code="no_progress",
                    next_turn=iteration + 1,
                    persist=pending is None,
                )

            attempts.append(
                self._attempt(
                    iteration=iteration,
                    operation=transition.operation,
                    outcome=(
                        "observed"
                        if transition.status == "continue"
                        else transition.status
                    ),
                    started_at=attempt_started_at,
                    started=attempt_started,
                    reason_code=transition.reason_code,
                    observation=transition.observation,
                )
            )
            if transition.observation is not None:
                observations.append(transition.observation)
            if transition.status == "continue":
                try:
                    save_checkpoint(
                        status="running",
                        next_turn=iteration + 1,
                    )
                except Exception:
                    return finish(
                        status="escalated",
                        message=(
                            "Agent operation completed but its observation "
                            "could not be committed."
                        ),
                        reason_code="checkpoint_error",
                        next_turn=iteration,
                        persist=False,
                    )
                continue
            return finish(
                status=transition.status,
                message=transition.message,
                reason_code=transition.reason_code or transition.status,
                result=transition.result,
                next_turn=iteration + 1,
            )

        return finish(
            status="blocked",
            message="Agent loop exhausted its iteration limit.",
            reason_code="iteration_limit",
            next_turn=self.limits.max_iterations + 1,
        )

    def _turn(
        self,
        *,
        run_id: str,
        iteration: int,
        started: float,
        observations: list[ObservationT],
    ) -> AgentLoopTurn[ObservationT]:
        return AgentLoopTurn(
            run_id=run_id,
            iteration=iteration,
            operation_id=f"{run_id}:operation:{iteration}",
            observations=tuple(observations),
            remaining_iterations=max(
                0,
                self.limits.max_iterations - iteration,
            ),
            elapsed_seconds=max(0.0, self._monotonic() - started),
        )

    def _validate_resume_checkpoint(
        self,
        checkpoint: AgentLoopCheckpoint,
        *,
        run_id: str,
        checkpoint_key: str | None,
    ) -> None:
        if not checkpoint.is_recoverable:
            raise ValueError("Only running agent loop checkpoints can resume")
        if checkpoint.run_id != run_id:
            raise ValueError("Agent loop checkpoint run_id does not match")
        if checkpoint.role != self.checkpoint_role:
            raise ValueError("Agent loop checkpoint role does not match")
        if (
            checkpoint_key is not None
            and checkpoint.checkpoint_key != checkpoint_key
        ):
            raise ValueError(
                "Agent loop checkpoint key does not match"
            )
        if checkpoint.next_iteration > self.limits.max_iterations + 1:
            raise ValueError(
                "Agent loop checkpoint iteration exceeds configured limit"
            )
        pending = checkpoint.pending_operation
        if (
            pending is not None
            and pending.iteration != checkpoint.next_iteration
        ):
            raise ValueError(
                "Pending operation iteration must match next_iteration"
            )

    def _resumed_elapsed_seconds(
        self,
        checkpoint: AgentLoopCheckpoint,
    ) -> float:
        return min(
            checkpoint.elapsed_seconds,
            self.limits.timeout_seconds,
        )

    def _terminal(
        self,
        *,
        run_id: str,
        status: ExcludeContinueStatus,
        message: str,
        reason_code: str,
        started_at: str,
        attempts: list[AgentLoopAttempt],
        observations: list[ObservationT],
        result: ResultT | None = None,
    ) -> AgentLoopResult[ObservationT, ResultT]:
        return AgentLoopResult(
            run_id=run_id,
            status=status,
            message=message,
            reason_code=reason_code,
            started_at=started_at,
            completed_at=self._timestamp(),
            attempts=tuple(attempts),
            observations=tuple(observations),
            result=result,
        )

    def _attempt(
        self,
        *,
        iteration: int,
        operation: str,
        outcome: str,
        started_at: str,
        started: float,
        reason_code: str | None,
        observation: ObservationT | None = None,
    ) -> AgentLoopAttempt:
        return AgentLoopAttempt(
            iteration=iteration,
            operation=operation,
            outcome=outcome,
            started_at=started_at,
            duration_ms=round(
                max(0.0, self._monotonic() - started) * 1000,
                3,
            ),
            reason_code=reason_code,
            observation=_jsonable(observation),
        )

    def _cancelled(self) -> bool:
        return bool(
            self.cancellation_requested is not None
            and self.cancellation_requested()
        )

    def _timed_out(self, started: float) -> bool:
        return self._monotonic() - started >= self.limits.timeout_seconds

    def _timestamp(self) -> str:
        return self._now().isoformat()


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value
