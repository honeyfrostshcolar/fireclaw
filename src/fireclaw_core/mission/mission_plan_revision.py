from __future__ import annotations

from dataclasses import dataclass, replace
from threading import RLock

from fireclaw_core.agent.robot_registry import RobotRegistry
from fireclaw_core.mission.execution_event import (
    ESCALATING_EXECUTION_EVENTS,
    INVALIDATING_EXECUTION_EVENTS,
    NON_INVALIDATING_EXECUTION_EVENTS,
    RETRYABLE_EXECUTION_EVENTS,
    MissionExecutionEvent,
)
from fireclaw_core.mission.mission_deliberation import (
    MissionDeliberationResult,
    MissionDeliberationRuntime,
)
from fireclaw_core.mission.mission_planner import MissionPlannerContext
from fireclaw_core.mission.mission_state import (
    MissionEnvironmentFact,
    MissionStateSnapshot,
    MissionStateSnapshotBuilder,
    MissionStateSnapshotValidator,
)
from fireclaw_core.mission.task_graph import MissionTaskGraph


@dataclass(frozen=True)
class MissionPlanInvalidationDecision:
    action: str
    reason_code: str
    message: str

    @property
    def invalidates_plan(self) -> bool:
        return self.action == "replan"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason_code": self.reason_code,
            "message": self.message,
            "invalidates_plan": self.invalidates_plan,
        }


class MissionPlanInvalidationPolicy:
    """Classify authoritative execution events without model judgment."""

    def evaluate(
        self,
        event: MissionExecutionEvent,
        graph: MissionTaskGraph,
    ) -> MissionPlanInvalidationDecision:
        if not self._affects_graph(event, graph):
            return MissionPlanInvalidationDecision(
                action="retain",
                reason_code="event_does_not_affect_active_plan",
                message="Execution event does not affect the active mission plan.",
            )
        if event.event_type in INVALIDATING_EXECUTION_EVENTS:
            return MissionPlanInvalidationDecision(
                action="replan",
                reason_code=f"plan_invalidated:{event.event_type}",
                message=(
                    "Authoritative execution evidence invalidated the active "
                    f"mission plan: {event.event_type}."
                ),
            )
        if event.event_type in RETRYABLE_EXECUTION_EVENTS:
            return MissionPlanInvalidationDecision(
                action="retry",
                reason_code=f"retry_allowed:{event.event_type}",
                message="Execution event permits bounded retry without replanning.",
            )
        if event.event_type in ESCALATING_EXECUTION_EVENTS:
            return MissionPlanInvalidationDecision(
                action="escalate",
                reason_code=f"replan_forbidden:{event.event_type}",
                message=(
                    "Execution event requires operator or safety handling; "
                    "automatic replanning is forbidden."
                ),
            )
        return MissionPlanInvalidationDecision(
            action="retain",
            reason_code=f"plan_retained:{event.event_type}",
            message="Execution event does not invalidate the active mission plan.",
        )

    @staticmethod
    def _affects_graph(
        event: MissionExecutionEvent,
        graph: MissionTaskGraph,
    ) -> bool:
        if event.robot_id is not None and not any(
            node.robot_id == event.robot_id for node in graph.nodes
        ):
            return False
        if event.node_id is not None and not any(
            node.node_id == event.node_id for node in graph.nodes
        ):
            return False
        if event.resource_id is not None and not any(
            event.resource_id in node.exclusive_resources for node in graph.nodes
        ):
            return False
        return True


@dataclass(frozen=True)
class MissionPlanRevisionResult:
    status: str
    message: str
    event: MissionExecutionEvent
    decision: MissionPlanInvalidationDecision
    current_plan_id: str
    state_snapshot: MissionStateSnapshot | None = None
    deliberation_result: MissionDeliberationResult | None = None
    revised_task_graph: MissionTaskGraph | None = None
    validation_errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "message": self.message,
            "event": self.event.to_dict(),
            "decision": self.decision.to_dict(),
            "current_plan_id": self.current_plan_id,
            "validation_errors": list(self.validation_errors),
        }
        if self.state_snapshot is not None:
            result["state_snapshot"] = self.state_snapshot.to_dict()
        if self.deliberation_result is not None:
            result["deliberation"] = self.deliberation_result.to_dict()
        if self.revised_task_graph is not None:
            result["revised_task_graph"] = self.revised_task_graph.to_dict()
        return result


class MissionPlanRevisionCoordinator:
    """Build an evidence-bound snapshot and request one bounded plan revision."""

    def __init__(
        self,
        *,
        registry: RobotRegistry,
        state_snapshot_builder: MissionStateSnapshotBuilder,
        deliberation_runtime: MissionDeliberationRuntime,
        invalidation_policy: MissionPlanInvalidationPolicy | None = None,
        max_plan_revisions: int = 3,
    ) -> None:
        if max_plan_revisions < 1:
            raise ValueError("max_plan_revisions must be at least 1")
        self.registry = registry
        self.state_snapshot_builder = state_snapshot_builder
        self.deliberation_runtime = deliberation_runtime
        self.invalidation_policy = (
            invalidation_policy or MissionPlanInvalidationPolicy()
        )
        self.max_plan_revisions = max_plan_revisions
        self._lock = RLock()
        self._results_by_event: dict[
            tuple[str, str],
            MissionPlanRevisionResult,
        ] = {}

    def coordinate(
        self,
        *,
        event: MissionExecutionEvent,
        current_graph: MissionTaskGraph,
        presence: dict[str, dict[str, Any]],
        planner_context: MissionPlannerContext,
        snapshot_version: int,
        previous_snapshot_id: str | None,
    ) -> MissionPlanRevisionResult:
        key = (event.mission_id, event.event_id)
        with self._lock:
            cached = self._results_by_event.get(key)
            if cached is not None:
                return cached
            result = self._coordinate_once(
                event=event,
                current_graph=current_graph,
                presence=presence,
                planner_context=planner_context,
                snapshot_version=snapshot_version,
                previous_snapshot_id=previous_snapshot_id,
            )
            self._results_by_event[key] = result
            return result

    def _coordinate_once(
        self,
        *,
        event: MissionExecutionEvent,
        current_graph: MissionTaskGraph,
        presence: dict[str, dict[str, Any]],
        planner_context: MissionPlannerContext,
        snapshot_version: int,
        previous_snapshot_id: str | None,
    ) -> MissionPlanRevisionResult:
        if event.mission_id != current_graph.mission_id:
            decision = MissionPlanInvalidationDecision(
                action="escalate",
                reason_code="mission_identity_mismatch",
                message="Execution event mission does not match the active plan.",
            )
            return self._result(
                status="blocked",
                event=event,
                current_graph=current_graph,
                decision=decision,
            )

        decision = self.invalidation_policy.evaluate(event, current_graph)
        if decision.action == "retain":
            return self._result(
                status="retained",
                event=event,
                current_graph=current_graph,
                decision=decision,
            )
        if decision.action == "retry":
            return self._result(
                status="retry_allowed",
                event=event,
                current_graph=current_graph,
                decision=decision,
            )
        if decision.action == "escalate":
            return self._result(
                status="escalated",
                event=event,
                current_graph=current_graph,
                decision=decision,
            )
        if current_graph.revision >= self.max_plan_revisions:
            exhausted = MissionPlanInvalidationDecision(
                action="escalate",
                reason_code="plan_revision_budget_exhausted",
                message="Mission plan revision budget was exhausted.",
            )
            return self._result(
                status="escalated",
                event=event,
                current_graph=current_graph,
                decision=exhausted,
            )
        if (
            getattr(
                self.deliberation_runtime.policy,
                "supports_plan_revision",
                False,
            )
            is not True
        ):
            unsupported = MissionPlanInvalidationDecision(
                action="escalate",
                reason_code="revision_policy_not_capable",
                message=(
                    "Configured mission planning policy does not declare "
                    "evidence-grounded plan revision support."
                ),
            )
            return self._result(
                status="escalated",
                event=event,
                current_graph=current_graph,
                decision=unsupported,
            )
        if snapshot_version < 1:
            return self._result(
                status="blocked",
                event=event,
                current_graph=current_graph,
                decision=decision,
                validation_errors=("snapshot_version must be at least 1",),
            )

        try:
            snapshot = self.state_snapshot_builder.build(
                mission_id=event.mission_id,
                presence=presence,
                version=snapshot_version,
                previous_snapshot_id=previous_snapshot_id,
            )
            snapshot = self._with_event_evidence(snapshot, event)
        except Exception as exc:
            return self._result(
                status="blocked",
                event=event,
                current_graph=current_graph,
                decision=decision,
                validation_errors=(
                    f"Mission revision snapshot could not be built: {exc}",
                ),
            )
        snapshot_errors = tuple(
            MissionStateSnapshotValidator().validate(snapshot)
        )
        if snapshot_errors:
            return self._result(
                status="blocked",
                event=event,
                current_graph=current_graph,
                decision=decision,
                state_snapshot=snapshot,
                validation_errors=snapshot_errors,
            )

        online_robot_ids = {
            robot_id
            for robot_id, value in presence.items()
            if value.get("online") is True
        }
        revision_context = replace(
            planner_context,
            available_robots=[
                entry
                for entry in self.registry.enabled_entries()
                if entry.robot_id in online_robot_ids
            ],
            state_snapshot=snapshot.to_dict(),
        )
        deliberation = self.deliberation_runtime.deliberate(
            mission_id=event.mission_id,
            command=current_graph.command,
            state_snapshot=snapshot,
            planner_context=revision_context,
            plan_revision=current_graph.revision + 1,
            supersedes_plan_id=current_graph.plan_id,
            invalidation_evidence_ids=(event.evidence_id,),
        )
        if deliberation.status == "proposed" and deliberation.task_graph is not None:
            return self._result(
                status="revised",
                event=event,
                current_graph=current_graph,
                decision=decision,
                state_snapshot=snapshot,
                deliberation_result=deliberation,
                revised_task_graph=deliberation.task_graph,
            )
        return self._result(
            status=deliberation.status,
            event=event,
            current_graph=current_graph,
            decision=decision,
            state_snapshot=snapshot,
            deliberation_result=deliberation,
            validation_errors=deliberation.validation_errors,
        )

    def _with_event_evidence(
        self,
        snapshot: MissionStateSnapshot,
        event: MissionExecutionEvent,
    ) -> MissionStateSnapshot:
        subject = (
            event.resource_id
            or event.node_id
            or event.task_id
            or event.robot_id
            or True
        )
        fact = MissionEnvironmentFact(
            fact_id=f"plan-invalidation:{event.event_id}",
            kind=event.event_type,
            value=subject,
            source=event.source_type,
            observed_at=event.observed_at,
            evidence_ids=(event.evidence_id,),
            confidence=1.0,
            subject_id=str(subject),
        )
        event_facts = [fact]
        if event.event_type == "completion_evidence_rejected":
            details = event.details or {}
            failed_kinds = details.get("failed_requirement_kinds")
            if isinstance(failed_kinds, list):
                for index, kind in enumerate(failed_kinds[:32]):
                    if not isinstance(kind, str) or not kind.strip():
                        continue
                    event_facts.append(
                        MissionEnvironmentFact(
                            fact_id=(
                                f"plan-invalidation:{event.event_id}:"
                                f"requirement:{index}"
                            ),
                            kind="completion_requirement_failed",
                            value=kind,
                            source=event.source_type,
                            observed_at=event.observed_at,
                            evidence_ids=(event.evidence_id,),
                            confidence=1.0,
                        )
                    )
            suggested = details.get("suggested_recovery_action")
            if isinstance(suggested, str) and suggested in {
                "recheck",
                "result_recheck_exhausted",
                "retry",
                "retry_exhausted",
                "reassign",
                "replan",
                "escalate",
                "abort",
            }:
                event_facts.append(
                    MissionEnvironmentFact(
                        fact_id=(
                            f"plan-invalidation:{event.event_id}:"
                            "suggested-recovery"
                        ),
                        kind="suggested_recovery_action",
                        value=suggested,
                        source=event.source_type,
                        observed_at=event.observed_at,
                        evidence_ids=(event.evidence_id,),
                        confidence=1.0,
                    )
                )
        event_fact_ids = {item.fact_id for item in event_facts}
        facts = tuple(
            item
            for item in snapshot.environment_facts
            if item.fact_id not in event_fact_ids
        ) + tuple(event_facts)
        return self.state_snapshot_builder.with_environment_facts(
            snapshot,
            facts,
            extra_evidence_ids=(event.evidence_id,),
        )

    @staticmethod
    def _result(
        *,
        status: str,
        event: MissionExecutionEvent,
        current_graph: MissionTaskGraph,
        decision: MissionPlanInvalidationDecision,
        state_snapshot: MissionStateSnapshot | None = None,
        deliberation_result: MissionDeliberationResult | None = None,
        revised_task_graph: MissionTaskGraph | None = None,
        validation_errors: tuple[str, ...] = (),
    ) -> MissionPlanRevisionResult:
        return MissionPlanRevisionResult(
            status=status,
            message=(
                deliberation_result.message
                if deliberation_result is not None
                else decision.message
            ),
            event=event,
            decision=decision,
            current_plan_id=current_graph.plan_id,
            state_snapshot=state_snapshot,
            deliberation_result=deliberation_result,
            revised_task_graph=revised_task_graph,
            validation_errors=validation_errors,
        )
