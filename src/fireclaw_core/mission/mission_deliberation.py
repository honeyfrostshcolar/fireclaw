from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import logging
import time
from typing import Any, Callable, Protocol
from uuid import uuid4

from fireclaw_core.agent.robot_registry import RobotRegistry
from fireclaw_core.mission.graph_proposal import (
    MissionGraphCompilationError,
    MissionGraphCompiler,
)
from fireclaw_core.mission.mission_plan_validator import MissionPlanValidator
from fireclaw_core.mission.mission_planner import (
    MissionPlannerContext,
    MissionPlanningResult,
)
from fireclaw_core.mission.mission_state import (
    MissionStateSnapshot,
    MissionStateSnapshotValidator,
)
from fireclaw_core.mission.task_graph import (
    MissionTaskGraph,
    task_graph_from_mission_plan,
)


logger = logging.getLogger(__name__)

VALID_READ_KINDS = frozenset({
    "snapshot_metadata",
    "fleet_state",
    "robot_state",
    "task_state",
    "environment_facts",
    "environment_beliefs",
    "resource_reservations",
})
VALID_DELIBERATION_OPERATIONS = frozenset({
    "inspect_state",
    "propose_plan",
    "request_clarification",
    "escalate",
})


@dataclass(frozen=True)
class MissionDeliberationLimits:
    max_iterations: int = 4
    timeout_seconds: float = 5.0
    max_observations: int = 3

    def __post_init__(self) -> None:
        if self.max_iterations <= 0:
            raise ValueError("max_iterations must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_observations < 0:
            raise ValueError("max_observations must be non-negative")


@dataclass(frozen=True)
class MissionStateReadRequest:
    kind: str
    subject_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind}
        if self.subject_id is not None:
            result["subject_id"] = self.subject_id
        return result


@dataclass(frozen=True)
class MissionStateObservation:
    kind: str
    data: dict[str, Any]
    iteration: int
    subject_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "kind": self.kind,
            "iteration": self.iteration,
            "data": dict(self.data),
        }
        if self.subject_id is not None:
            result["subject_id"] = self.subject_id
        return result


@dataclass(frozen=True)
class MissionDeliberationRequest:
    mission_id: str
    command: str
    state_snapshot: MissionStateSnapshot
    planner_context: MissionPlannerContext
    iteration: int
    observations: tuple[MissionStateObservation, ...] = ()
    validation_errors: tuple[str, ...] = ()
    last_planning_result: MissionPlanningResult | None = None
    plan_revision: int = 1
    supersedes_plan_id: str | None = None
    invalidation_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MissionDeliberationDecision:
    operation: str
    message: str
    planning_result: MissionPlanningResult | None = None
    read_request: MissionStateReadRequest | None = None
    reason_code: str | None = None

    @classmethod
    def inspect(
        cls,
        kind: str,
        *,
        subject_id: str | None = None,
        message: str = "Inspect current mission state.",
    ) -> MissionDeliberationDecision:
        return cls(
            operation="inspect_state",
            message=message,
            read_request=MissionStateReadRequest(kind=kind, subject_id=subject_id),
        )

    @classmethod
    def propose(
        cls,
        planning_result: MissionPlanningResult,
    ) -> MissionDeliberationDecision:
        return cls(
            operation="propose_plan",
            message=planning_result.message,
            planning_result=planning_result,
        )

    @classmethod
    def clarify(
        cls,
        message: str,
        *,
        planning_result: MissionPlanningResult | None = None,
        reason_code: str = "operator_input_required",
    ) -> MissionDeliberationDecision:
        return cls(
            operation="request_clarification",
            message=message,
            planning_result=planning_result,
            reason_code=reason_code,
        )

    @classmethod
    def escalate(
        cls,
        message: str,
        *,
        planning_result: MissionPlanningResult | None = None,
        reason_code: str = "operator_escalation_required",
    ) -> MissionDeliberationDecision:
        return cls(
            operation="escalate",
            message=message,
            planning_result=planning_result,
            reason_code=reason_code,
        )


class MissionDeliberationPolicy(Protocol):
    def decide(
        self,
        request: MissionDeliberationRequest,
    ) -> MissionDeliberationDecision:
        ...


class PlannerDeliberationPolicy:
    """Compatibility adapter for existing one-shot mission planners."""

    def __init__(self, planner: Any) -> None:
        self.planner = planner

    def decide(
        self,
        request: MissionDeliberationRequest,
    ) -> MissionDeliberationDecision:
        if request.validation_errors:
            prior_result = request.last_planning_result
            return MissionDeliberationDecision.escalate(
                "Mission planner proposal failed deterministic validation.",
                planning_result=(
                    replace(
                        prior_result,
                        status="blocked",
                        message="Mission planner proposal failed deterministic validation.",
                    )
                    if prior_result is not None
                    else MissionPlanningResult(
                        status="blocked",
                        message="Mission planner proposal failed deterministic validation.",
                    )
                ),
                reason_code="invalid_plan_proposal",
            )
        result = self.planner.plan(request.command, context=request.planner_context)
        if result.status == "planned" and (
            result.plan is not None or result.graph_proposal is not None
        ):
            return MissionDeliberationDecision.propose(result)
        if result.status == "clarify":
            return MissionDeliberationDecision.clarify(
                result.message,
                planning_result=result,
                reason_code="planner_clarification",
            )
        return MissionDeliberationDecision.escalate(
            result.message,
            planning_result=result,
            reason_code=f"planner_status:{result.status}",
        )


class MissionSnapshotReader:
    """Read-only query surface over one immutable mission snapshot."""

    def read(
        self,
        snapshot: MissionStateSnapshot,
        request: MissionStateReadRequest,
        *,
        iteration: int,
    ) -> MissionStateObservation:
        if request.kind not in VALID_READ_KINDS:
            raise ValueError(f"Unsupported mission state read kind: {request.kind}")
        if request.kind == "snapshot_metadata":
            data = {
                "snapshot_id": snapshot.snapshot_id,
                "mission_id": snapshot.mission_id,
                "version": snapshot.version,
                "captured_at": snapshot.captured_at,
                "previous_snapshot_id": snapshot.previous_snapshot_id,
                "evidence_ids": list(snapshot.evidence_ids),
                "belief_projection_version": snapshot.belief_projection_version,
                "environment_belief_summary": {
                    status: sum(
                        belief.status == status
                        for belief in snapshot.environment_beliefs
                    )
                    for status in (
                        "confirmed",
                        "uncertain",
                        "conflicted",
                        "stale",
                    )
                },
            }
        elif request.kind == "fleet_state":
            data = {"robots": [robot.to_dict() for robot in snapshot.robots]}
        elif request.kind == "robot_state":
            if not request.subject_id:
                raise ValueError("robot_state read requires subject_id")
            robot = next(
                (
                    item
                    for item in snapshot.robots
                    if item.robot_id == request.subject_id
                ),
                None,
            )
            data = {
                "found": robot is not None,
                "robot": robot.to_dict() if robot is not None else None,
            }
        elif request.kind == "task_state":
            tasks = snapshot.tasks
            if request.subject_id is not None:
                tasks = tuple(
                    task for task in tasks if task.task_id == request.subject_id
                )
            data = {"tasks": [task.to_dict() for task in tasks]}
        elif request.kind == "environment_facts":
            facts = snapshot.environment_facts
            if request.subject_id is not None:
                facts = tuple(
                    fact
                    for fact in facts
                    if fact.fact_id == request.subject_id
                    or fact.kind == request.subject_id
                )
            data = {"environment_facts": [fact.to_dict() for fact in facts]}
        elif request.kind == "environment_beliefs":
            beliefs = snapshot.environment_beliefs
            if request.subject_id is not None:
                beliefs = tuple(
                    belief
                    for belief in beliefs
                    if belief.belief_id == request.subject_id
                    or belief.subject_id == request.subject_id
                    or belief.kind == request.subject_id
                    or belief.status == request.subject_id
                )
            data = {
                "environment_beliefs": [
                    belief.to_dict() for belief in beliefs
                ]
            }
        else:
            reservations = snapshot.resource_reservations
            if request.subject_id is not None:
                reservations = tuple(
                    reservation
                    for reservation in reservations
                    if reservation.resource_id == request.subject_id
                )
            data = {
                "resource_reservations": [
                    reservation.to_dict() for reservation in reservations
                ]
            }
        return MissionStateObservation(
            kind=request.kind,
            subject_id=request.subject_id,
            data=data,
            iteration=iteration,
        )


@dataclass(frozen=True)
class MissionDeliberationAttempt:
    iteration: int
    operation: str
    outcome: str
    started_at: str
    completed_at: str
    duration_ms: float
    reason_code: str | None = None
    read_request: MissionStateReadRequest | None = None
    validation_errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "iteration": self.iteration,
            "operation": self.operation,
            "outcome": self.outcome,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "validation_errors": list(self.validation_errors),
        }
        if self.reason_code is not None:
            result["reason_code"] = self.reason_code
        if self.read_request is not None:
            result["read_request"] = self.read_request.to_dict()
        return result


@dataclass(frozen=True)
class MissionDeliberationResult:
    run_id: str
    mission_id: str
    snapshot_id: str
    status: str
    message: str
    started_at: str
    completed_at: str
    attempts: tuple[MissionDeliberationAttempt, ...]
    observations: tuple[MissionStateObservation, ...]
    planning_result: MissionPlanningResult | None = None
    task_graph: MissionTaskGraph | None = None
    reason_code: str | None = None
    validation_errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "run_id": self.run_id,
            "mission_id": self.mission_id,
            "snapshot_id": self.snapshot_id,
            "status": self.status,
            "message": self.message,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "observations": [
                observation.to_dict() for observation in self.observations
            ],
            "validation_errors": list(self.validation_errors),
        }
        if self.reason_code is not None:
            result["reason_code"] = self.reason_code
        if self.task_graph is not None:
            result["task_graph"] = self.task_graph.to_dict()
        if self.planning_result is not None:
            result["planning_status"] = self.planning_result.status
            result["planning_intent"] = self.planning_result.intent
            if self.planning_result.graph_proposal is not None:
                result["graph_proposal"] = (
                    self.planning_result.graph_proposal.to_dict()
                )
        return result


class MissionDeliberationRuntime:
    """Bounded, read-only planning loop that can propose but never dispatch."""

    def __init__(
        self,
        *,
        registry: RobotRegistry,
        policy: MissionDeliberationPolicy,
        limits: MissionDeliberationLimits | None = None,
        snapshot_reader: MissionSnapshotReader | None = None,
        graph_compiler: MissionGraphCompiler | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.limits = limits or MissionDeliberationLimits()
        self.snapshot_reader = snapshot_reader or MissionSnapshotReader()
        self.graph_compiler = graph_compiler or MissionGraphCompiler(registry)
        self.cancellation_requested = cancellation_requested
        self._monotonic = monotonic
        self._now = now or (lambda: datetime.now(timezone.utc))

    def deliberate(
        self,
        *,
        mission_id: str,
        command: str,
        state_snapshot: MissionStateSnapshot,
        planner_context: MissionPlannerContext,
        plan_revision: int = 1,
        supersedes_plan_id: str | None = None,
        invalidation_evidence_ids: tuple[str, ...] = (),
    ) -> MissionDeliberationResult:
        run_id = f"deliberation-{uuid4().hex}"
        started_at = self._timestamp()
        started = self._monotonic()
        attempts: list[MissionDeliberationAttempt] = []
        observations: list[MissionStateObservation] = []
        validation_errors = tuple(
            MissionStateSnapshotValidator().validate(state_snapshot)
        )
        if state_snapshot.mission_id != mission_id:
            validation_errors = (
                *validation_errors,
                "Mission state snapshot mission_id does not match deliberation mission_id.",
            )
        if planner_context.state_snapshot != state_snapshot.to_dict():
            validation_errors = (
                *validation_errors,
                "Planner context state_snapshot does not match deliberation state snapshot.",
            )
        if validation_errors:
            return self._result(
                run_id=run_id,
                mission_id=mission_id,
                snapshot_id=state_snapshot.snapshot_id,
                status="blocked",
                message="Mission state snapshot failed deterministic validation.",
                started_at=started_at,
                attempts=attempts,
                observations=observations,
                reason_code="invalid_state_snapshot",
                validation_errors=validation_errors,
                planning_result=MissionPlanningResult(
                    status="blocked",
                    message="Mission state snapshot failed deterministic validation.",
                ),
            )

        seen_reads: set[tuple[str, str | None]] = set()
        last_planning_result: MissionPlanningResult | None = None
        for iteration in range(1, self.limits.max_iterations + 1):
            terminal = self._pre_attempt_terminal(
                run_id=run_id,
                mission_id=mission_id,
                state_snapshot=state_snapshot,
                started_at=started_at,
                started=started,
                attempts=attempts,
                observations=observations,
            )
            if terminal is not None:
                return terminal
            attempt_started = self._monotonic()
            attempt_started_at = self._timestamp()
            request = MissionDeliberationRequest(
                mission_id=mission_id,
                command=command,
                state_snapshot=state_snapshot,
                planner_context=planner_context,
                iteration=iteration,
                observations=tuple(observations),
                validation_errors=validation_errors,
                last_planning_result=last_planning_result,
                plan_revision=plan_revision,
                supersedes_plan_id=supersedes_plan_id,
                invalidation_evidence_ids=invalidation_evidence_ids,
            )
            try:
                decision = self.policy.decide(request)
            except Exception:
                logger.exception("Mission deliberation policy failed")
                attempts.append(
                    self._attempt(
                        iteration=iteration,
                        operation="policy_error",
                        outcome="failed",
                        started_at=attempt_started_at,
                        started=attempt_started,
                        reason_code="policy_error",
                    )
                )
                return self._result(
                    run_id=run_id,
                    mission_id=mission_id,
                    snapshot_id=state_snapshot.snapshot_id,
                    status="escalated",
                    message="Mission deliberation policy failed.",
                    started_at=started_at,
                    attempts=attempts,
                    observations=observations,
                    reason_code="policy_error",
                    planning_result=MissionPlanningResult(
                        status="error",
                        message="Mission deliberation policy failed.",
                    ),
                )

            if not isinstance(decision, MissionDeliberationDecision):
                validation_errors = (
                    "Mission deliberation policy returned an invalid decision type.",
                )
                attempts.append(
                    self._attempt(
                        iteration=iteration,
                        operation="invalid_decision",
                        outcome="rejected",
                        started_at=attempt_started_at,
                        started=attempt_started,
                        reason_code="invalid_runtime_decision",
                        validation_errors=validation_errors,
                    )
                )
                continue

            if self.cancellation_requested is not None and self.cancellation_requested():
                attempts.append(
                    self._attempt(
                        iteration=iteration,
                        operation=decision.operation,
                        outcome="cancelled",
                        started_at=attempt_started_at,
                        started=attempt_started,
                        reason_code="cancelled",
                    )
                )
                return self._result(
                    run_id=run_id,
                    mission_id=mission_id,
                    snapshot_id=state_snapshot.snapshot_id,
                    status="cancelled",
                    message="Mission deliberation was cancelled.",
                    started_at=started_at,
                    attempts=attempts,
                    observations=observations,
                    reason_code="cancelled",
                    planning_result=MissionPlanningResult(
                        status="cancelled",
                        message="Mission deliberation was cancelled.",
                    ),
                )

            if self._timed_out(started):
                attempts.append(
                    self._attempt(
                        iteration=iteration,
                        operation=decision.operation,
                        outcome="timed_out",
                        started_at=attempt_started_at,
                        started=attempt_started,
                        reason_code="deliberation_timeout",
                    )
                )
                return self._result(
                    run_id=run_id,
                    mission_id=mission_id,
                    snapshot_id=state_snapshot.snapshot_id,
                    status="timed_out",
                    message="Mission deliberation exceeded its time limit.",
                    started_at=started_at,
                    attempts=attempts,
                    observations=observations,
                    reason_code="deliberation_timeout",
                    planning_result=MissionPlanningResult(
                        status="timed_out",
                        message="Mission deliberation exceeded its time limit.",
                    ),
                )

            decision_errors = self._decision_errors(decision)
            if decision_errors:
                if decision.planning_result is not None:
                    last_planning_result = decision.planning_result
                attempts.append(
                    self._attempt(
                        iteration=iteration,
                        operation=decision.operation,
                        outcome="rejected",
                        started_at=attempt_started_at,
                        started=attempt_started,
                        reason_code="invalid_runtime_decision",
                        validation_errors=tuple(decision_errors),
                    )
                )
                validation_errors = tuple(decision_errors)
                continue

            if decision.operation == "inspect_state":
                if len(observations) >= self.limits.max_observations:
                    attempts.append(
                        self._attempt(
                            iteration=iteration,
                            operation=decision.operation,
                            outcome="rejected",
                            started_at=attempt_started_at,
                            started=attempt_started,
                            reason_code="observation_limit",
                            read_request=decision.read_request,
                        )
                    )
                    return self._result(
                        run_id=run_id,
                        mission_id=mission_id,
                        snapshot_id=state_snapshot.snapshot_id,
                        status="blocked",
                        message="Mission deliberation exceeded its observation limit.",
                        started_at=started_at,
                        attempts=attempts,
                        observations=observations,
                        reason_code="observation_limit",
                        planning_result=MissionPlanningResult(
                            status="blocked",
                            message="Mission deliberation exceeded its observation limit.",
                        ),
                    )
                assert decision.read_request is not None
                read_key = (
                    decision.read_request.kind,
                    decision.read_request.subject_id,
                )
                if read_key in seen_reads:
                    attempts.append(
                        self._attempt(
                            iteration=iteration,
                            operation=decision.operation,
                            outcome="rejected",
                            started_at=attempt_started_at,
                            started=attempt_started,
                            reason_code="repeated_state_read",
                            read_request=decision.read_request,
                        )
                    )
                    return self._result(
                        run_id=run_id,
                        mission_id=mission_id,
                        snapshot_id=state_snapshot.snapshot_id,
                        status="blocked",
                        message="Mission deliberation repeated a state read without progress.",
                        started_at=started_at,
                        attempts=attempts,
                        observations=observations,
                        reason_code="repeated_state_read",
                        planning_result=MissionPlanningResult(
                            status="blocked",
                            message="Mission deliberation repeated a state read without progress.",
                        ),
                    )
                try:
                    observation = self.snapshot_reader.read(
                        state_snapshot,
                        decision.read_request,
                        iteration=iteration,
                    )
                except ValueError as exc:
                    attempts.append(
                        self._attempt(
                            iteration=iteration,
                            operation=decision.operation,
                            outcome="rejected",
                            started_at=attempt_started_at,
                            started=attempt_started,
                            reason_code="invalid_state_read",
                            read_request=decision.read_request,
                            validation_errors=(str(exc),),
                        )
                    )
                    validation_errors = (str(exc),)
                    continue
                seen_reads.add(read_key)
                observations.append(observation)
                attempts.append(
                    self._attempt(
                        iteration=iteration,
                        operation=decision.operation,
                        outcome="observed",
                        started_at=attempt_started_at,
                        started=attempt_started,
                        read_request=decision.read_request,
                    )
                )
                validation_errors = ()
                continue

            if decision.operation == "propose_plan":
                assert decision.planning_result is not None
                accepted_planning_result = decision.planning_result
                task_graph: MissionTaskGraph | None = None
                try:
                    if decision.planning_result.graph_proposal is not None:
                        compiled = self.graph_compiler.compile(
                            decision.planning_result.graph_proposal,
                            state_snapshot=state_snapshot,
                            mission_id=mission_id,
                            plan_id=f"{mission_id}:plan:{plan_revision}",
                            revision=plan_revision,
                            supersedes_plan_id=supersedes_plan_id,
                            invalidation_evidence_ids=invalidation_evidence_ids,
                        )
                        task_graph = compiled.task_graph
                        accepted_planning_result = replace(
                            decision.planning_result,
                            plan=compiled.plan,
                        )
                    else:
                        assert decision.planning_result.plan is not None
                        task_graph = task_graph_from_mission_plan(
                            decision.planning_result.plan,
                            mission_id=mission_id,
                            plan_id=f"{mission_id}:plan:{plan_revision}",
                            state_snapshot_id=state_snapshot.snapshot_id,
                            revision=plan_revision,
                            supersedes_plan_id=supersedes_plan_id,
                            invalidation_evidence_ids=invalidation_evidence_ids,
                        )
                    assert accepted_planning_result.plan is not None
                    proposal_errors = MissionPlanValidator().validate(
                        accepted_planning_result.plan,
                        self.registry,
                        task_graph=task_graph,
                    )
                    unresolved_belief_ids = sorted(
                        belief.belief_id
                        for belief in state_snapshot.environment_beliefs
                        if belief.status != "confirmed"
                        and not _observations_contain_belief(
                            observations,
                            belief.belief_id,
                        )
                    )
                    if unresolved_belief_ids:
                        proposal_errors.append(
                            "Mission plan proposal must inspect unresolved "
                            "environment beliefs before proposal: "
                            f"{unresolved_belief_ids}."
                        )
                    if (
                        decision.planning_result.graph_proposal is not None
                    ):
                        assumed_belief_ids = sorted({
                            assumption.belief_id
                            for node in (
                                decision.planning_result.graph_proposal.nodes
                            )
                            for assumption in node.belief_assumptions
                        })
                        unobserved_assumption_ids = [
                            belief_id
                            for belief_id in assumed_belief_ids
                            if not _observations_contain_belief(
                                observations,
                                belief_id,
                            )
                        ]
                        if unobserved_assumption_ids:
                            proposal_errors.append(
                                "Mission graph belief assumptions must be "
                                "inspected before proposal: "
                                f"{unobserved_assumption_ids}."
                            )
                    if plan_revision > 1:
                        unknown_evidence_ids = sorted(
                            set(invalidation_evidence_ids)
                            - set(state_snapshot.evidence_ids)
                        )
                        if unknown_evidence_ids:
                            proposal_errors.append(
                                "Mission plan revision cites evidence absent from its "
                                f"state snapshot: {unknown_evidence_ids}."
                            )
                        unobserved_evidence_ids = sorted(
                            evidence_id
                            for evidence_id in invalidation_evidence_ids
                            if not _observations_contain_evidence(
                                observations,
                                evidence_id,
                                required_kind=(
                                    "environment_beliefs"
                                    if state_snapshot.belief_projection_version == 1
                                    else "environment_facts"
                                ),
                            )
                        )
                        if unobserved_evidence_ids:
                            evidence_surface = (
                                "beliefs"
                                if state_snapshot.belief_projection_version == 1
                                else "facts"
                            )
                            proposal_errors.append(
                                "Mission plan revision must inspect environment "
                                f"{evidence_surface} containing its invalidation evidence "
                                f"before proposal: {unobserved_evidence_ids}."
                            )
                except MissionGraphCompilationError as exc:
                    proposal_errors = list(exc.errors)
                except Exception:
                    logger.exception(
                        "Mission deliberation could not project a plan proposal"
                    )
                    proposal_errors = [
                        "Mission plan proposal could not be projected for validation."
                    ]
                attempts.append(
                    self._attempt(
                        iteration=iteration,
                        operation=decision.operation,
                        outcome="accepted" if not proposal_errors else "rejected",
                        started_at=attempt_started_at,
                        started=attempt_started,
                        reason_code=(
                            None if not proposal_errors else "invalid_plan_proposal"
                        ),
                        validation_errors=tuple(proposal_errors),
                    )
                )
                if proposal_errors:
                    validation_errors = tuple(proposal_errors)
                    last_planning_result = decision.planning_result
                    continue
                assert task_graph is not None
                return self._result(
                    run_id=run_id,
                    mission_id=mission_id,
                    snapshot_id=state_snapshot.snapshot_id,
                    status="proposed",
                    message=decision.message,
                    started_at=started_at,
                    attempts=attempts,
                    observations=observations,
                    reason_code="validated_plan_proposal",
                    planning_result=accepted_planning_result,
                    task_graph=task_graph,
                )

            attempts.append(
                self._attempt(
                    iteration=iteration,
                    operation=decision.operation,
                    outcome="terminal",
                    started_at=attempt_started_at,
                    started=attempt_started,
                    reason_code=decision.reason_code,
                )
            )
            if decision.operation == "request_clarification":
                planning_result = decision.planning_result or MissionPlanningResult(
                    status="clarify",
                    message=decision.message,
                )
                return self._result(
                    run_id=run_id,
                    mission_id=mission_id,
                    snapshot_id=state_snapshot.snapshot_id,
                    status="clarification_required",
                    message=decision.message,
                    started_at=started_at,
                    attempts=attempts,
                    observations=observations,
                    reason_code=decision.reason_code,
                    planning_result=planning_result,
                    validation_errors=validation_errors,
                )
            planning_result = decision.planning_result or MissionPlanningResult(
                status="escalated",
                message=decision.message,
            )
            return self._result(
                run_id=run_id,
                mission_id=mission_id,
                snapshot_id=state_snapshot.snapshot_id,
                status=(
                    "blocked"
                    if planning_result.status == "blocked"
                    else "escalated"
                ),
                message=decision.message,
                started_at=started_at,
                attempts=attempts,
                observations=observations,
                reason_code=decision.reason_code,
                planning_result=planning_result,
                validation_errors=validation_errors,
            )

        return self._result(
            run_id=run_id,
            mission_id=mission_id,
            snapshot_id=state_snapshot.snapshot_id,
            status="blocked",
            message="Mission deliberation exceeded its iteration limit.",
            started_at=started_at,
            attempts=attempts,
            observations=observations,
            reason_code="iteration_limit",
            validation_errors=validation_errors,
            planning_result=MissionPlanningResult(
                status="blocked",
                message="Mission deliberation exceeded its iteration limit.",
            ),
        )

    def _pre_attempt_terminal(
        self,
        *,
        run_id: str,
        mission_id: str,
        state_snapshot: MissionStateSnapshot,
        started_at: str,
        started: float,
        attempts: list[MissionDeliberationAttempt],
        observations: list[MissionStateObservation],
    ) -> MissionDeliberationResult | None:
        if self.cancellation_requested is not None and self.cancellation_requested():
            return self._result(
                run_id=run_id,
                mission_id=mission_id,
                snapshot_id=state_snapshot.snapshot_id,
                status="cancelled",
                message="Mission deliberation was cancelled.",
                started_at=started_at,
                attempts=attempts,
                observations=observations,
                reason_code="cancelled",
                planning_result=MissionPlanningResult(
                    status="cancelled",
                    message="Mission deliberation was cancelled.",
                ),
            )
        if self._timed_out(started):
            return self._result(
                run_id=run_id,
                mission_id=mission_id,
                snapshot_id=state_snapshot.snapshot_id,
                status="timed_out",
                message="Mission deliberation exceeded its time limit.",
                started_at=started_at,
                attempts=attempts,
                observations=observations,
                reason_code="deliberation_timeout",
                planning_result=MissionPlanningResult(
                    status="timed_out",
                    message="Mission deliberation exceeded its time limit.",
                ),
            )
        return None

    def _decision_errors(
        self,
        decision: MissionDeliberationDecision,
    ) -> list[str]:
        errors: list[str] = []
        if (
            not isinstance(decision.operation, str)
            or decision.operation not in VALID_DELIBERATION_OPERATIONS
        ):
            errors.append(
                f"Unsupported mission deliberation operation: {decision.operation}"
            )
        if not isinstance(decision.message, str) or not decision.message.strip():
            errors.append("Mission deliberation decision message must not be empty.")
        if decision.operation == "inspect_state":
            if not isinstance(decision.read_request, MissionStateReadRequest):
                errors.append("inspect_state requires a read_request.")
            elif (
                not isinstance(decision.read_request.kind, str)
                or decision.read_request.kind not in VALID_READ_KINDS
            ):
                errors.append(
                    f"Unsupported mission state read kind: {decision.read_request.kind}"
                )
            elif (
                decision.read_request.subject_id is not None
                and not isinstance(decision.read_request.subject_id, str)
            ):
                errors.append("Mission state read subject_id must be a string.")
        elif decision.read_request is not None:
            errors.append(
                f"{decision.operation} must not include a read_request."
            )
        if decision.operation == "propose_plan":
            if (
                decision.planning_result is None
                or decision.planning_result.status != "planned"
                or (
                    decision.planning_result.plan is None
                    and decision.planning_result.graph_proposal is None
                )
            ):
                message = (
                    decision.planning_result.message
                    if decision.planning_result is not None
                    else ""
                )
                errors.append(
                    "propose_plan requires a planned MissionPlanningResult."
                    + (f" Planner error: {message}" if message else "")
                )
        return errors

    def _timed_out(self, started: float) -> bool:
        return self._monotonic() - started >= self.limits.timeout_seconds

    def _attempt(
        self,
        *,
        iteration: int,
        operation: str,
        outcome: str,
        started_at: str,
        started: float,
        reason_code: str | None = None,
        read_request: MissionStateReadRequest | None = None,
        validation_errors: tuple[str, ...] = (),
    ) -> MissionDeliberationAttempt:
        return MissionDeliberationAttempt(
            iteration=iteration,
            operation=operation,
            outcome=outcome,
            started_at=started_at,
            completed_at=self._timestamp(),
            duration_ms=max(0.0, (self._monotonic() - started) * 1000),
            reason_code=reason_code,
            read_request=read_request,
            validation_errors=validation_errors,
        )

    def _result(
        self,
        *,
        run_id: str,
        mission_id: str,
        snapshot_id: str,
        status: str,
        message: str,
        started_at: str,
        attempts: list[MissionDeliberationAttempt],
        observations: list[MissionStateObservation],
        planning_result: MissionPlanningResult,
        reason_code: str | None = None,
        task_graph: MissionTaskGraph | None = None,
        validation_errors: tuple[str, ...] = (),
    ) -> MissionDeliberationResult:
        return MissionDeliberationResult(
            run_id=run_id,
            mission_id=mission_id,
            snapshot_id=snapshot_id,
            status=status,
            message=message,
            started_at=started_at,
            completed_at=self._timestamp(),
            attempts=tuple(attempts),
            observations=tuple(observations),
            planning_result=planning_result,
            task_graph=task_graph,
            reason_code=reason_code,
            validation_errors=validation_errors,
        )

    def _timestamp(self) -> str:
        return self._now().isoformat()


def _observations_contain_evidence(
    observations: list[MissionStateObservation],
    evidence_id: str,
    *,
    required_kind: str,
) -> bool:
    return any(
        observation.kind == required_kind
        and _contains_value(observation.data, evidence_id)
        for observation in observations
    )


def _observations_contain_belief(
    observations: list[MissionStateObservation],
    belief_id: str,
) -> bool:
    return any(
        observation.kind == "environment_beliefs"
        and _contains_value(observation.data, belief_id)
        for observation in observations
    )


def _contains_value(value: Any, expected: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_value(item, expected) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_value(item, expected) for item in value)
    return value == expected
