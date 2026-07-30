from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import logging
import time
from typing import Any, Callable, Protocol
from uuid import uuid4

from fireclaw_core.agent.bounded_loop import (
    AgentLoopLimits,
    AgentLoopResult,
    AgentLoopTransition,
    AgentLoopTurn,
    BoundedAgentLoop,
)
from fireclaw_core.agent.tool_runtime import AgentToolRuntime
from fireclaw_core.approval.execution_authorization import (
    execution_action_hash,
)
from fireclaw_core.agent.loop_checkpoint import (
    AgentLoopCheckpoint,
    AgentLoopCheckpointStore,
    AgentLoopPendingOperation,
)
from fireclaw_core.agent.robot_registry import RobotRegistry
from fireclaw_core.mission.active_observation import (
    MissionObservationRequest,
    UNRESOLVED_BELIEF_STATUSES,
)
from fireclaw_core.mission.graph_proposal import (
    MissionGraphCompilationError,
    MissionGraphCompiler,
)
from fireclaw_core.mission.mission_plan_validator import MissionPlanValidator
from fireclaw_core.mission.mission_planner import (
    MissionPlannerContext,
    MissionPlanningResult,
)
from fireclaw_core.mission.planning_context import (
    MissionPlanningContextAssembler,
    MissionPlanningContextAssemblyError,
    MissionPlanningContextEnvelope,
    MissionPlanningContextManifest,
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
    "execute_agent_tool",
    "propose_plan",
    "request_observation",
    "request_clarification",
    "escalate",
})


@dataclass(frozen=True)
class MissionDeliberationLimits:
    max_iterations: int = 4
    timeout_seconds: float = 5.0
    max_observations: int = 3
    max_agent_tool_executions: int = 3

    def __post_init__(self) -> None:
        if self.max_iterations <= 0:
            raise ValueError("max_iterations must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_observations < 0:
            raise ValueError("max_observations must be non-negative")
        if self.max_agent_tool_executions < 0:
            raise ValueError(
                "max_agent_tool_executions must be non-negative"
            )


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
    authoritative: bool = True

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "kind": self.kind,
            "iteration": self.iteration,
            "data": dict(self.data),
            "authoritative": self.authoritative,
        }
        if self.subject_id is not None:
            result["subject_id"] = self.subject_id
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MissionStateObservation:
        data = value.get("data")
        if not isinstance(data, dict):
            raise ValueError("Mission observation data must be an object")
        return cls(
            kind=str(value.get("kind") or ""),
            data=dict(data),
            iteration=int(value.get("iteration") or 0),
            subject_id=(
                value["subject_id"]
                if isinstance(value.get("subject_id"), str)
                else None
            ),
            authoritative=bool(value.get("authoritative", True)),
        )


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
    context_envelope: MissionPlanningContextEnvelope | None = None


@dataclass(frozen=True)
class MissionDeliberationDecision:
    operation: str
    message: str
    planning_result: MissionPlanningResult | None = None
    read_request: MissionStateReadRequest | None = None
    observation_request: MissionObservationRequest | None = None
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    tool_effect: str | None = None
    reason_code: str | None = None
    context_manifest: MissionPlanningContextManifest | None = None

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
    def execute_tool(
        cls,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        tool_effect: str,
        message: str = "Execute an admitted Agent Tool.",
    ) -> MissionDeliberationDecision:
        return cls(
            operation="execute_agent_tool",
            message=message,
            tool_name=tool_name,
            tool_arguments=dict(arguments),
            tool_effect=tool_effect,
        )

    @classmethod
    def observe(
        cls,
        observation_request: MissionObservationRequest,
        *,
        message: str = "Request an active robot observation.",
    ) -> MissionDeliberationDecision:
        return cls(
            operation="request_observation",
            message=message,
            observation_request=observation_request,
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
    observation_request: MissionObservationRequest | None = None
    tool_name: str | None = None
    tool_arguments_hash: str | None = None
    validation_errors: tuple[str, ...] = ()
    context_id: str | None = None
    context_manifest: dict[str, Any] | None = None

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
        if self.observation_request is not None:
            result["observation_request"] = (
                self.observation_request.to_dict()
            )
        if self.tool_name is not None:
            result["tool_name"] = self.tool_name
        if self.tool_arguments_hash is not None:
            result["tool_arguments_hash"] = self.tool_arguments_hash
        if self.context_id is not None:
            result["context_id"] = self.context_id
        if self.context_manifest is not None:
            result["context_manifest"] = dict(self.context_manifest)
        return result

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any],
    ) -> MissionDeliberationAttempt:
        read_request = value.get("read_request")
        observation_request = value.get("observation_request")
        manifest = value.get("context_manifest")
        return cls(
            iteration=int(value.get("iteration") or 0),
            operation=str(value.get("operation") or ""),
            outcome=str(value.get("outcome") or ""),
            started_at=str(value.get("started_at") or ""),
            completed_at=str(value.get("completed_at") or ""),
            duration_ms=float(value.get("duration_ms") or 0.0),
            reason_code=(
                value["reason_code"]
                if isinstance(value.get("reason_code"), str)
                else None
            ),
            read_request=(
                MissionStateReadRequest(
                    kind=str(read_request.get("kind") or ""),
                    subject_id=(
                        read_request["subject_id"]
                        if isinstance(
                            read_request.get("subject_id"),
                            str,
                        )
                        else None
                    ),
                )
                if isinstance(read_request, dict)
                else None
            ),
            observation_request=(
                MissionObservationRequest.from_dict(observation_request)
                if isinstance(observation_request, dict)
                else None
            ),
            tool_name=(
                value["tool_name"]
                if isinstance(value.get("tool_name"), str)
                else None
            ),
            tool_arguments_hash=(
                value["tool_arguments_hash"]
                if isinstance(value.get("tool_arguments_hash"), str)
                else None
            ),
            validation_errors=tuple(
                str(item)
                for item in value.get("validation_errors", [])
                if isinstance(item, str)
            ),
            context_id=(
                value["context_id"]
                if isinstance(value.get("context_id"), str)
                else None
            ),
            context_manifest=(
                dict(manifest) if isinstance(manifest, dict) else None
            ),
        )


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
    observation_request: MissionObservationRequest | None = None

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
        if self.observation_request is not None:
            result["observation_request"] = (
                self.observation_request.to_dict()
            )
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


@dataclass(frozen=True)
class _MissionLoopObservation:
    """Internal progress marker; authoritative observations stay snapshot-bound."""

    iteration: int
    outcome: str
    reason_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any],
    ) -> _MissionLoopObservation:
        return cls(
            iteration=int(value.get("iteration") or 0),
            outcome=str(value.get("outcome") or ""),
            reason_code=(
                value["reason_code"]
                if isinstance(value.get("reason_code"), str)
                else None
            ),
        )


@dataclass(frozen=True)
class _MissionLoopTerminal:
    status: str
    message: str
    planning_result: MissionPlanningResult
    reason_code: str | None = None
    task_graph: MissionTaskGraph | None = None
    validation_errors: tuple[str, ...] = ()
    observation_request: MissionObservationRequest | None = None


@dataclass(frozen=True)
class _MissionLoopDecision:
    operation: str
    started_at: str
    started: float
    decision: MissionDeliberationDecision | None = None
    context_manifest: MissionPlanningContextManifest | None = None
    validation_errors: tuple[str, ...] = ()
    terminal: _MissionLoopTerminal | None = None


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
        context_assembler: MissionPlanningContextAssembler | None = None,
        agent_tool_runtime: AgentToolRuntime | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        checkpoint_store: AgentLoopCheckpointStore | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.limits = limits or MissionDeliberationLimits()
        self.snapshot_reader = snapshot_reader or MissionSnapshotReader()
        self.graph_compiler = graph_compiler or MissionGraphCompiler(registry)
        self.context_assembler = (
            context_assembler or MissionPlanningContextAssembler()
        )
        self.agent_tool_runtime = (
            agent_tool_runtime
            or getattr(policy, "agent_tool_runtime", None)
        )
        self.cancellation_requested = cancellation_requested
        self.checkpoint_store = checkpoint_store
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
        command_hash = hashlib.sha256(command.encode("utf-8")).hexdigest()
        checkpoint_key = (
            f"mission:{mission_id}:snapshot:{state_snapshot.snapshot_id}:"
            f"revision:{plan_revision}:command:{command_hash[:16]}"
        )
        run_id = (
            checkpoint_key
            if self.checkpoint_store is not None
            else f"deliberation-{uuid4().hex}"
        )
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
            started_at = self._timestamp()
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

        resume_checkpoint: AgentLoopCheckpoint | None = None
        adapter_state: dict[str, Any] = {}
        if self.checkpoint_store is not None:
            latest = self.checkpoint_store.latest(checkpoint_key)
            if latest is not None and latest.is_recoverable:
                adapter_state = dict(latest.adapter_state or {})
                if (
                    adapter_state.get("mission_id") != mission_id
                    or adapter_state.get("snapshot_id")
                    != state_snapshot.snapshot_id
                    or adapter_state.get("command_hash") != command_hash
                    or adapter_state.get("plan_revision") != plan_revision
                ):
                    raise ValueError(
                        "Mission checkpoint does not match the frozen "
                        "snapshot, command, or plan revision"
                    )
                resume_checkpoint = latest
                attempts = [
                    MissionDeliberationAttempt.from_dict(item)
                    for item in adapter_state.get("mission_attempts", [])
                    if isinstance(item, dict)
                ]
                observations = [
                    MissionStateObservation.from_dict(item)
                    for item in adapter_state.get(
                        "mission_observations",
                        [],
                    )
                    if isinstance(item, dict)
                ]
                validation_errors = tuple(
                    str(item)
                    for item in adapter_state.get(
                        "validation_errors",
                        [],
                    )
                    if isinstance(item, str)
                )

        seen_reads: set[tuple[str, str | None]] = {
            (
                str(item[0]),
                item[1] if isinstance(item[1], str) else None,
            )
            for item in adapter_state.get("seen_reads", [])
            if isinstance(item, list) and len(item) == 2
        }
        agent_tool_execution_count = int(
            adapter_state.get("agent_tool_execution_count") or 0
        )
        seen_agent_tool_calls = {
            str(item)
            for item in adapter_state.get("seen_agent_tool_calls", [])
            if isinstance(item, str) and item
        }
        last_planning_result = _planning_result_from_checkpoint(
            adapter_state.get("last_planning_result")
        )
        controls: dict[int, _MissionLoopDecision] = {}

        def current_adapter_state() -> dict[str, Any]:
            return {
                "mission_id": mission_id,
                "snapshot_id": state_snapshot.snapshot_id,
                "command_hash": command_hash,
                "plan_revision": plan_revision,
                "supersedes_plan_id": supersedes_plan_id,
                "invalidation_evidence_ids": list(
                    invalidation_evidence_ids
                ),
                "mission_attempts": [
                    attempt.to_dict() for attempt in attempts
                ],
                "mission_observations": [
                    observation.to_dict()
                    for observation in observations
                ],
                "validation_errors": list(validation_errors),
                "seen_reads": [
                    [kind, subject_id]
                    for kind, subject_id in sorted(
                        seen_reads,
                        key=lambda item: (
                            item[0],
                            item[1] or "",
                        ),
                    )
                ],
                "last_planning_result": (
                    _planning_result_to_checkpoint(
                        last_planning_result
                    )
                    if last_planning_result is not None
                    else None
                ),
                "agent_tool_execution_count": agent_tool_execution_count,
                "seen_agent_tool_calls": sorted(seen_agent_tool_calls),
            }

        def decide(
            turn: AgentLoopTurn[_MissionLoopObservation],
        ) -> _MissionLoopDecision:
            attempt_started = self._monotonic()
            attempt_started_at = self._timestamp()
            try:
                context_assembly = self.context_assembler.assemble(
                    mission_id=mission_id,
                    command=command,
                    state_snapshot=state_snapshot,
                    planner_context=planner_context,
                    iteration=turn.iteration,
                    observations=observations,
                    validation_errors=validation_errors,
                    last_planning_result=last_planning_result,
                    plan_revision=plan_revision,
                    supersedes_plan_id=supersedes_plan_id,
                    invalidation_evidence_ids=(
                        invalidation_evidence_ids
                    ),
                )
            except MissionPlanningContextAssemblyError as exc:
                context_errors = (str(exc),)
                control = _MissionLoopDecision(
                    operation="assemble_context",
                    started_at=attempt_started_at,
                    started=attempt_started,
                    validation_errors=context_errors,
                    terminal=_MissionLoopTerminal(
                        status="blocked",
                        message=(
                            "Mission planning context failed deterministic "
                            "assembly."
                        ),
                        reason_code="context_budget_exceeded",
                        validation_errors=context_errors,
                        planning_result=MissionPlanningResult(
                            status="blocked",
                            message=(
                                "Mission planning context failed "
                                "deterministic assembly."
                            ),
                        ),
                    ),
                )
                controls[turn.iteration] = control
                return control
            context_manifest = context_assembly.envelope.manifest
            request = MissionDeliberationRequest(
                mission_id=mission_id,
                command=command,
                state_snapshot=state_snapshot,
                planner_context=context_assembly.planner_context,
                iteration=turn.iteration,
                observations=tuple(observations),
                validation_errors=validation_errors,
                last_planning_result=last_planning_result,
                plan_revision=plan_revision,
                supersedes_plan_id=supersedes_plan_id,
                invalidation_evidence_ids=invalidation_evidence_ids,
                context_envelope=context_assembly.envelope,
            )
            try:
                decision = self.policy.decide(request)
            except Exception:
                logger.exception("Mission deliberation policy failed")
                control = _MissionLoopDecision(
                    operation="policy_error",
                    started_at=attempt_started_at,
                    started=attempt_started,
                    context_manifest=context_manifest,
                    terminal=_MissionLoopTerminal(
                        status="escalated",
                        message="Mission deliberation policy failed.",
                        reason_code="policy_error",
                        planning_result=MissionPlanningResult(
                            status="error",
                            message="Mission deliberation policy failed.",
                        ),
                    ),
                )
                controls[turn.iteration] = control
                return control

            if not isinstance(decision, MissionDeliberationDecision):
                errors = (
                    "Mission deliberation policy returned an invalid decision type.",
                )
                control = _MissionLoopDecision(
                    operation="invalid_decision",
                    started_at=attempt_started_at,
                    started=attempt_started,
                    context_manifest=context_manifest,
                    validation_errors=errors,
                )
                controls[turn.iteration] = control
                return control

            if decision.context_manifest is not None:
                context_manifest = decision.context_manifest
            control = _MissionLoopDecision(
                operation=decision.operation,
                started_at=attempt_started_at,
                started=attempt_started,
                decision=decision,
                context_manifest=context_manifest,
            )
            controls[turn.iteration] = control
            return control

        def execute(
            control: _MissionLoopDecision,
            turn: AgentLoopTurn[_MissionLoopObservation],
        ) -> AgentLoopTransition[
            _MissionLoopObservation,
            _MissionLoopTerminal,
        ]:
            nonlocal last_planning_result, validation_errors
            nonlocal agent_tool_execution_count

            def record(
                *,
                outcome: str,
                reason_code: str | None = None,
                read_request: MissionStateReadRequest | None = None,
                observation_request: MissionObservationRequest | None = None,
                tool_name: str | None = None,
                tool_arguments_hash: str | None = None,
                errors: tuple[str, ...] = (),
            ) -> None:
                attempts.append(
                    self._attempt(
                        iteration=turn.iteration,
                        operation=control.operation,
                        outcome=outcome,
                        started_at=control.started_at,
                        started=control.started,
                        reason_code=reason_code,
                        read_request=read_request,
                        observation_request=observation_request,
                        tool_name=tool_name,
                        tool_arguments_hash=tool_arguments_hash,
                        validation_errors=errors,
                        context_manifest=control.context_manifest,
                    )
                )

            def continuing(
                *,
                outcome: str,
                reason_code: str | None = None,
            ) -> AgentLoopTransition[
                _MissionLoopObservation,
                _MissionLoopTerminal,
            ]:
                return AgentLoopTransition.continuing(
                    operation=control.operation,
                    message="Mission deliberation will continue.",
                    observation=_MissionLoopObservation(
                        iteration=turn.iteration,
                        outcome=outcome,
                        reason_code=reason_code,
                    ),
                    reason_code=reason_code,
                )

            def terminal(
                value: _MissionLoopTerminal,
            ) -> AgentLoopTransition[
                _MissionLoopObservation,
                _MissionLoopTerminal,
            ]:
                shared_status = {
                    "blocked": "blocked",
                    "escalated": "escalated",
                    "cancelled": "cancelled",
                    "timed_out": "timed_out",
                }.get(value.status, "completed")
                return AgentLoopTransition(
                    status=shared_status,  # type: ignore[arg-type]
                    operation=control.operation,
                    message=value.message,
                    reason_code=value.reason_code,
                    result=value,
                )

            if control.terminal is not None:
                record(
                    outcome=(
                        "failed"
                        if control.terminal.reason_code == "policy_error"
                        else "rejected"
                    ),
                    reason_code=control.terminal.reason_code,
                    errors=control.validation_errors,
                )
                return terminal(control.terminal)

            if control.decision is None:
                record(
                    outcome="rejected",
                    reason_code="invalid_runtime_decision",
                    errors=control.validation_errors,
                )
                validation_errors = control.validation_errors
                return continuing(
                    outcome="rejected",
                    reason_code="invalid_runtime_decision",
                )

            decision = control.decision
            decision_errors = self._decision_errors(decision)
            if decision_errors:
                if decision.planning_result is not None:
                    last_planning_result = decision.planning_result
                errors = tuple(decision_errors)
                record(
                    outcome="rejected",
                    reason_code="invalid_runtime_decision",
                    errors=errors,
                )
                validation_errors = errors
                return continuing(
                    outcome="rejected",
                    reason_code="invalid_runtime_decision",
                )

            if decision.operation == "inspect_state":
                if len(observations) >= self.limits.max_observations:
                    record(
                        outcome="rejected",
                        reason_code="observation_limit",
                        read_request=decision.read_request,
                    )
                    return terminal(_MissionLoopTerminal(
                        status="blocked",
                        message="Mission deliberation exceeded its observation limit.",
                        reason_code="observation_limit",
                        planning_result=MissionPlanningResult(
                            status="blocked",
                            message="Mission deliberation exceeded its observation limit.",
                        ),
                    ))
                assert decision.read_request is not None
                read_key = (
                    decision.read_request.kind,
                    decision.read_request.subject_id,
                )
                if read_key in seen_reads:
                    record(
                        outcome="rejected",
                        reason_code="repeated_state_read",
                        read_request=decision.read_request,
                    )
                    return terminal(_MissionLoopTerminal(
                        status="blocked",
                        message="Mission deliberation repeated a state read without progress.",
                        reason_code="repeated_state_read",
                        planning_result=MissionPlanningResult(
                            status="blocked",
                            message="Mission deliberation repeated a state read without progress.",
                        ),
                    ))
                try:
                    observation = self.snapshot_reader.read(
                        state_snapshot,
                        decision.read_request,
                        iteration=turn.iteration,
                    )
                except ValueError as exc:
                    errors = (str(exc),)
                    record(
                        outcome="rejected",
                        reason_code="invalid_state_read",
                        read_request=decision.read_request,
                        errors=errors,
                    )
                    validation_errors = errors
                    return continuing(
                        outcome="rejected",
                        reason_code="invalid_state_read",
                    )
                seen_reads.add(read_key)
                observations.append(observation)
                record(
                    outcome="observed",
                    read_request=decision.read_request,
                )
                validation_errors = ()
                return continuing(outcome="observed")

            if decision.operation == "execute_agent_tool":
                assert decision.tool_name is not None
                arguments = dict(decision.tool_arguments or {})
                action_hash = execution_action_hash(
                    decision.tool_name,
                    arguments,
                )
                if self.agent_tool_runtime is None:
                    errors = ("Mission Agent Tool runtime is unavailable.",)
                    record(
                        outcome="rejected",
                        reason_code="agent_tool_runtime_unavailable",
                        tool_name=decision.tool_name,
                        tool_arguments_hash=action_hash,
                        errors=errors,
                    )
                    validation_errors = errors
                    return continuing(
                        outcome="rejected",
                        reason_code="agent_tool_runtime_unavailable",
                    )
                if (
                    agent_tool_execution_count
                    >= self.limits.max_agent_tool_executions
                ):
                    record(
                        outcome="rejected",
                        reason_code="agent_tool_execution_limit",
                        tool_name=decision.tool_name,
                        tool_arguments_hash=action_hash,
                    )
                    return terminal(_MissionLoopTerminal(
                        status="blocked",
                        message=(
                            "Mission deliberation exceeded its Agent Tool "
                            "execution limit."
                        ),
                        reason_code="agent_tool_execution_limit",
                        planning_result=MissionPlanningResult(
                            status="blocked",
                            message=(
                                "Mission deliberation exceeded its Agent "
                                "Tool execution limit."
                            ),
                        ),
                    ))
                if action_hash in seen_agent_tool_calls:
                    record(
                        outcome="rejected",
                        reason_code="repeated_agent_tool_call",
                        tool_name=decision.tool_name,
                        tool_arguments_hash=action_hash,
                    )
                    return terminal(_MissionLoopTerminal(
                        status="blocked",
                        message=(
                            "Mission deliberation repeated the same Agent "
                            "Tool call without progress."
                        ),
                        reason_code="repeated_agent_tool_call",
                        planning_result=MissionPlanningResult(
                            status="blocked",
                            message=(
                                "Mission deliberation repeated the same "
                                "Agent Tool call without progress."
                            ),
                        ),
                    ))
                result = self.agent_tool_runtime.execute(
                    decision.tool_name,
                    arguments,
                    context={
                        "mission_id": mission_id,
                        "snapshot_id": state_snapshot.snapshot_id,
                        "iteration": turn.iteration,
                    },
                )
                agent_tool_execution_count += 1
                seen_agent_tool_calls.add(action_hash)
                observations.append(
                    MissionStateObservation(
                        kind=f"agent_tool:{decision.tool_name}",
                        data=result.to_dict(),
                        iteration=turn.iteration,
                        authoritative=False,
                    )
                )
                record(
                    outcome=result.status,
                    reason_code=result.error_code,
                    tool_name=decision.tool_name,
                    tool_arguments_hash=action_hash,
                )
                validation_errors = ()
                if result.status == "approval_required":
                    return terminal(_MissionLoopTerminal(
                        status="escalated",
                        message=(
                            "Mission Agent Tool requires exact backend "
                            "authorization before execution."
                        ),
                        reason_code="agent_tool_approval_required",
                        planning_result=MissionPlanningResult(
                            status="blocked",
                            message=(
                                "Mission Agent Tool requires exact backend "
                                "authorization before execution."
                            ),
                        ),
                    ))
                return continuing(
                    outcome=result.status,
                    reason_code=result.error_code,
                )

            if decision.operation == "request_observation":
                assert decision.observation_request is not None
                observation_request = decision.observation_request
                belief = next(
                    (
                        item
                        for item in state_snapshot.environment_beliefs
                        if item.belief_id == observation_request.belief_id
                    ),
                    None,
                )
                request_errors: list[str] = []
                if belief is None:
                    request_errors.append(
                        "Active observation request references a belief "
                        "absent from the frozen snapshot."
                    )
                elif belief.status not in UNRESOLVED_BELIEF_STATUSES:
                    request_errors.append(
                        "Active observation request must target an "
                        "unresolved belief."
                    )
                if not _observations_contain_belief(
                    observations,
                    observation_request.belief_id,
                ):
                    request_errors.append(
                        "Active observation request must first inspect its "
                        "belief in the frozen snapshot."
                    )
                errors = tuple(request_errors)
                reason_code = (
                    "active_observation_requested"
                    if not request_errors
                    else "invalid_observation_request"
                )
                record(
                    outcome="requested" if not request_errors else "rejected",
                    reason_code=reason_code,
                    observation_request=observation_request,
                    errors=errors,
                )
                if request_errors:
                    validation_errors = errors
                    return continuing(
                        outcome="rejected",
                        reason_code=reason_code,
                    )
                return terminal(_MissionLoopTerminal(
                    status="observation_required",
                    message=decision.message,
                    reason_code="active_observation_requested",
                    planning_result=MissionPlanningResult(
                        status="observe",
                        message=decision.message,
                    ),
                    observation_request=observation_request,
                ))

            if decision.operation == "propose_plan":
                assert decision.planning_result is not None
                (
                    accepted_planning_result,
                    task_graph,
                    proposal_errors,
                ) = self._validate_plan_proposal(
                    decision,
                    mission_id=mission_id,
                    state_snapshot=state_snapshot,
                    observations=observations,
                    plan_revision=plan_revision,
                    supersedes_plan_id=supersedes_plan_id,
                    invalidation_evidence_ids=invalidation_evidence_ids,
                )
                errors = tuple(proposal_errors)
                record(
                    outcome="accepted" if not proposal_errors else "rejected",
                    reason_code=(
                        None if not proposal_errors else "invalid_plan_proposal"
                    ),
                    errors=errors,
                )
                if proposal_errors:
                    validation_errors = errors
                    last_planning_result = decision.planning_result
                    return continuing(
                        outcome="rejected",
                        reason_code="invalid_plan_proposal",
                    )
                assert task_graph is not None
                return terminal(_MissionLoopTerminal(
                    status="proposed",
                    message=decision.message,
                    reason_code="validated_plan_proposal",
                    planning_result=accepted_planning_result,
                    task_graph=task_graph,
                ))

            record(
                outcome="terminal",
                reason_code=decision.reason_code,
            )
            if decision.operation == "request_clarification":
                planning_result = decision.planning_result or MissionPlanningResult(
                    status="clarify",
                    message=decision.message,
                )
                return terminal(_MissionLoopTerminal(
                    status="clarification_required",
                    message=decision.message,
                    reason_code=decision.reason_code,
                    planning_result=planning_result,
                    validation_errors=validation_errors,
                ))
            planning_result = decision.planning_result or MissionPlanningResult(
                status="escalated",
                message=decision.message,
            )
            return terminal(_MissionLoopTerminal(
                status=(
                    "blocked"
                    if planning_result.status == "blocked"
                    else "escalated"
                ),
                message=decision.message,
                reason_code=decision.reason_code,
                planning_result=planning_result,
                validation_errors=validation_errors,
            ))

        def reconcile_pending(
            pending: AgentLoopPendingOperation,
            turn: AgentLoopTurn[_MissionLoopObservation],
        ) -> AgentLoopTransition[
            _MissionLoopObservation,
            _MissionLoopTerminal,
        ]:
            message = (
                "A side-effecting Mission Agent Tool may have run before "
                "restart; automatic replay is prohibited."
            )
            return AgentLoopTransition(
                status="escalated",
                operation=pending.operation,
                message=message,
                reason_code="agent_tool_effect_outcome_unknown",
                result=_MissionLoopTerminal(
                    status="escalated",
                    message=message,
                    reason_code="agent_tool_effect_outcome_unknown",
                    planning_result=MissionPlanningResult(
                        status="blocked",
                        message=message,
                    ),
                ),
            )

        loop_result = BoundedAgentLoop[
            _MissionLoopDecision,
            _MissionLoopObservation,
            _MissionLoopTerminal,
        ](
            limits=AgentLoopLimits(
                max_iterations=self.limits.max_iterations,
                timeout_seconds=self.limits.timeout_seconds,
            ),
            cancellation_requested=self.cancellation_requested,
            checkpoint_store=self.checkpoint_store,
            checkpoint_role="mission_coordinator",
            monotonic=self._monotonic,
            now=self._now,
        ).run(
            run_id=run_id,
            decide=decide,
            execute=execute,
            checkpoint_key=checkpoint_key,
            resume_checkpoint=resume_checkpoint,
            observation_from_checkpoint=(
                lambda value: _MissionLoopObservation.from_dict(value)
            ),
            adapter_state_provider=current_adapter_state,
            requires_reconciliation=(
                lambda control: (
                    control.decision is not None
                    and control.decision.operation == "execute_agent_tool"
                    and control.decision.tool_effect != "read"
                )
            ),
            reconcile_pending=reconcile_pending,
        )
        self._append_unexecuted_loop_attempts(
            loop_result=loop_result,
            controls=controls,
            attempts=attempts,
        )
        final = self._mission_terminal_from_loop(
            loop_result,
            validation_errors=validation_errors,
        )
        return self._result(
            run_id=run_id,
            mission_id=mission_id,
            snapshot_id=state_snapshot.snapshot_id,
            status=final.status,
            message=final.message,
            started_at=loop_result.started_at,
            completed_at=loop_result.completed_at,
            attempts=attempts,
            observations=observations,
            reason_code=final.reason_code,
            validation_errors=final.validation_errors,
            planning_result=final.planning_result,
            task_graph=final.task_graph,
            observation_request=final.observation_request,
        )

    def _validate_plan_proposal(
        self,
        decision: MissionDeliberationDecision,
        *,
        mission_id: str,
        state_snapshot: MissionStateSnapshot,
        observations: list[MissionStateObservation],
        plan_revision: int,
        supersedes_plan_id: str | None,
        invalidation_evidence_ids: tuple[str, ...],
    ) -> tuple[
        MissionPlanningResult,
        MissionTaskGraph | None,
        list[str],
    ]:
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
            if decision.planning_result.graph_proposal is not None:
                assumed_belief_ids = sorted({
                    assumption.belief_id
                    for node in decision.planning_result.graph_proposal.nodes
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
        return accepted_planning_result, task_graph, proposal_errors

    def _append_unexecuted_loop_attempts(
        self,
        *,
        loop_result: AgentLoopResult[
            _MissionLoopObservation,
            _MissionLoopTerminal,
        ],
        controls: dict[int, _MissionLoopDecision],
        attempts: list[MissionDeliberationAttempt],
    ) -> None:
        recorded_iterations = {attempt.iteration for attempt in attempts}
        for loop_attempt in loop_result.attempts:
            if loop_attempt.iteration in recorded_iterations:
                continue
            control = controls.get(loop_attempt.iteration)
            decision = control.decision if control is not None else None
            context_manifest = (
                control.context_manifest if control is not None else None
            )
            reason_code = (
                "deliberation_timeout"
                if loop_attempt.reason_code == "loop_timeout"
                else loop_attempt.reason_code
            )
            attempts.append(MissionDeliberationAttempt(
                iteration=loop_attempt.iteration,
                operation=(
                    control.operation
                    if control is not None
                    else loop_attempt.operation
                ),
                outcome=loop_attempt.outcome,
                started_at=loop_attempt.started_at,
                completed_at=loop_result.completed_at,
                duration_ms=loop_attempt.duration_ms,
                reason_code=reason_code,
                read_request=(
                    decision.read_request if decision is not None else None
                ),
                observation_request=(
                    decision.observation_request
                    if decision is not None
                    else None
                ),
                validation_errors=(
                    control.validation_errors
                    if control is not None
                    else ()
                ),
                context_id=(
                    context_manifest.context_id
                    if context_manifest is not None
                    else None
                ),
                context_manifest=(
                    context_manifest.to_dict()
                    if context_manifest is not None
                    else None
                ),
            ))

    @staticmethod
    def _mission_terminal_from_loop(
        loop_result: AgentLoopResult[
            _MissionLoopObservation,
            _MissionLoopTerminal,
        ],
        *,
        validation_errors: tuple[str, ...],
    ) -> _MissionLoopTerminal:
        if loop_result.result is not None:
            return loop_result.result
        if loop_result.status == "cancelled":
            message = "Mission deliberation was cancelled."
            return _MissionLoopTerminal(
                status="cancelled",
                message=message,
                reason_code="cancelled",
                planning_result=MissionPlanningResult(
                    status="cancelled",
                    message=message,
                ),
            )
        if loop_result.status == "timed_out":
            message = "Mission deliberation exceeded its time limit."
            return _MissionLoopTerminal(
                status="timed_out",
                message=message,
                reason_code="deliberation_timeout",
                planning_result=MissionPlanningResult(
                    status="timed_out",
                    message=message,
                ),
            )
        if loop_result.reason_code == "iteration_limit":
            message = "Mission deliberation exceeded its iteration limit."
            return _MissionLoopTerminal(
                status="blocked",
                message=message,
                reason_code="iteration_limit",
                validation_errors=validation_errors,
                planning_result=MissionPlanningResult(
                    status="blocked",
                    message=message,
                ),
            )
        message = "Mission deliberation runtime failed closed."
        return _MissionLoopTerminal(
            status="escalated",
            message=message,
            reason_code=loop_result.reason_code,
            validation_errors=validation_errors,
            planning_result=MissionPlanningResult(
                status="error",
                message=message,
            ),
        )

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
        if decision.operation == "request_observation":
            if not isinstance(
                decision.observation_request,
                MissionObservationRequest,
            ):
                errors.append(
                    "request_observation requires an observation_request."
                )
        elif decision.observation_request is not None:
            errors.append(
                f"{decision.operation} must not include an "
                "observation_request."
            )
        if decision.operation == "execute_agent_tool":
            if (
                not isinstance(decision.tool_name, str)
                or not decision.tool_name.strip()
            ):
                errors.append(
                    "execute_agent_tool requires a non-empty tool_name."
                )
            if not isinstance(decision.tool_arguments, dict):
                errors.append(
                    "execute_agent_tool requires object tool_arguments."
                )
            if (
                not isinstance(decision.tool_effect, str)
                or not decision.tool_effect.strip()
            ):
                errors.append(
                    "execute_agent_tool requires a host-projected tool_effect."
                )
        elif (
            decision.tool_name is not None
            or decision.tool_arguments is not None
            or decision.tool_effect is not None
        ):
            errors.append(
                f"{decision.operation} must not include Agent Tool fields."
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
        observation_request: MissionObservationRequest | None = None,
        tool_name: str | None = None,
        tool_arguments_hash: str | None = None,
        validation_errors: tuple[str, ...] = (),
        context_manifest: MissionPlanningContextManifest | None = None,
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
            observation_request=observation_request,
            tool_name=tool_name,
            tool_arguments_hash=tool_arguments_hash,
            validation_errors=validation_errors,
            context_id=(
                context_manifest.context_id
                if context_manifest is not None
                else None
            ),
            context_manifest=(
                context_manifest.to_dict()
                if context_manifest is not None
                else None
            ),
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
        observation_request: MissionObservationRequest | None = None,
        completed_at: str | None = None,
    ) -> MissionDeliberationResult:
        return MissionDeliberationResult(
            run_id=run_id,
            mission_id=mission_id,
            snapshot_id=snapshot_id,
            status=status,
            message=message,
            started_at=started_at,
            completed_at=completed_at or self._timestamp(),
            attempts=tuple(attempts),
            observations=tuple(observations),
            planning_result=planning_result,
            task_graph=task_graph,
            reason_code=reason_code,
            validation_errors=validation_errors,
            observation_request=observation_request,
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


def _planning_result_to_checkpoint(
    result: MissionPlanningResult,
) -> dict[str, Any]:
    return {
        "status": result.status,
        "message": result.message,
        "intent": result.intent,
    }


def _planning_result_from_checkpoint(
    value: Any,
) -> MissionPlanningResult | None:
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    message = value.get("message")
    if not isinstance(status, str) or not isinstance(message, str):
        raise ValueError(
            "Mission checkpoint last_planning_result is invalid"
        )
    return MissionPlanningResult(
        status=status,
        message=message,
        intent=(
            value["intent"]
            if isinstance(value.get("intent"), str)
            else None
        ),
    )
