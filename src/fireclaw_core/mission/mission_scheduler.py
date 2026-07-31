from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.belief_contract import MissionBeliefGate
from fireclaw_core.mission.completion_contract import (
    ExecutionEvidenceValidator,
)
from fireclaw_core.mission.execution_event import MissionExecutionEvent
from fireclaw_core.mission.mission_planner import MissionPlan, MissionSubtask
from fireclaw_core.mission.mission_registry import TERMINAL_SUBTASK_STATUSES
from fireclaw_core.mission.recovery_orchestrator import (
    CompletionRecoveryOrchestrator,
)
from fireclaw_core.mission.revision_dispatcher import (
    ACTIVE_NODE_STATUSES,
    SUCCEEDED_NODE_STATUSES,
    VALID_NODE_RUNTIME_STATUSES,
    JsonlMissionDispatchStore,
    MissionDispatchCheckpoint,
    MissionNodeExecution,
    RevisionDispatchDecision,
    RevisionDispatchReconciler,
    checkpoint_for_graph,
    execution_spec_hash,
)
from fireclaw_core.mission.task_graph import (
    MissionTaskGraph,
    MissionTaskGraphValidator,
    MissionTaskNode,
    task_graph_from_mission_plan,
)
from fireclaw_core.agent.robot_registry import RobotRegistry
from fireclaw_core.task.terminal_outcome import (
    normalize_robot_task_terminal_status,
    robot_task_status_from_trace,
)


# Failure status categories
_BAD_STATUSES = {
    "blocked",
    "escalated",
    "failed",
    "timed_out",
    "lost",
    # Persisted compatibility values.
    "block",
    "denied",
}


@dataclass(frozen=True)
class MissionFailurePolicy:
    """Per-subtask failure decisions.

    Decision values: "retry", "reassign", "skip", "escalate", "abort".
    """
    on_failed: str = "reassign"
    on_denied: str = "abort"
    on_lost: str = "abort"
    on_block: str = "escalate"
    on_escalated: str = "escalate"
    on_timed_out: str = "retry"
    max_retries: int = 1
    max_reassigns: int = 1

    def decision_for(self, status: str) -> str:
        """Return the decision for a given terminal failure status."""
        mapping = {
            "failed": self.on_failed,
            "denied": self.on_denied,
            "lost": self.on_lost,
            "block": self.on_block,
            "blocked": self.on_block,
            "escalated": self.on_escalated,
            "timed_out": self.on_timed_out,
        }
        return mapping.get(status, "abort")


@dataclass(frozen=True)
class MissionSchedulerConfig:
    failure_policy: MissionFailurePolicy = field(default_factory=MissionFailurePolicy)
    poll_interval_seconds: float = 0.1
    group_timeout_seconds: float = 300.0
    revision_cancel_timeout_seconds: float = 5.0


@dataclass(frozen=True)
class _SubmittedAttempt:
    logical_subtask_key: str
    subtask: MissionSubtask
    task_id: str
    node_id: str
    plan_id: str


@dataclass
class MissionScheduler:
    mission_agent: MissionAgent
    registry: RobotRegistry | None = None
    config: MissionSchedulerConfig = field(default_factory=MissionSchedulerConfig)
    revision_reconciler: RevisionDispatchReconciler = field(
        default_factory=RevisionDispatchReconciler
    )
    evidence_validator: ExecutionEvidenceValidator = field(
        default_factory=ExecutionEvidenceValidator
    )
    belief_gate: MissionBeliefGate = field(
        default_factory=MissionBeliefGate
    )
    recovery_orchestrator: CompletionRecoveryOrchestrator = field(
        default_factory=CompletionRecoveryOrchestrator
    )
    dispatch_store: JsonlMissionDispatchStore | None = None

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = self.mission_agent.registry
        if (
            self.dispatch_store is None
            and self.mission_agent.mission_registry is not None
        ):
            registry_path = Path(self.mission_agent.mission_registry.path)
            self.dispatch_store = JsonlMissionDispatchStore(
                registry_path.with_name(
                    f"{registry_path.stem}.dispatch.jsonl"
                )
            )

    def schedule(
        self,
        plan: MissionPlan,
        *,
        mission_id: str,
        session_id: str | None = None,
        operator: dict[str, Any] | None = None,
        memory_command_event_id: str | None = None,
        memory_plan_event_id: str | None = None,
        run_control: Any | None = None,
    ) -> dict[str, Any]:
        """Execute a mission plan by scheduling execution groups in order."""
        current_graph = self.mission_agent.active_task_graph(mission_id)
        if current_graph is None:
            current_graph = task_graph_from_mission_plan(
                plan,
                mission_id=mission_id,
                plan_id=f"{mission_id}:plan:1",
            )
        current_nodes = {
            node.node_id: node for node in current_graph.nodes
        }
        node_executions = {
            node.node_id: MissionNodeExecution(
                plan_id=current_graph.plan_id,
                node_id=node.node_id,
                robot_id=node.robot_id,
                status="pending",
                node_spec_hash=execution_spec_hash(node),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            for node in current_graph.nodes
        }
        checkpoint_error = self._persist_checkpoint(
            current_graph,
            node_executions,
            memory_command_event_id=memory_command_event_id,
            memory_plan_event_id=memory_plan_event_id,
        )
        if checkpoint_error is not None:
            return {
                "status": "blocked",
                "message": checkpoint_error,
                "mission_id": mission_id,
                "group_results": [],
                "failure_decisions": [],
            }
        groups: dict[int, list[tuple[str, MissionSubtask]]] = {}
        for node_index, subtask in enumerate(plan.subtasks, start=1):
            groups.setdefault(subtask.execution_group, []).append(
                (subtask.node_id or f"task-{node_index}", subtask)
            )

        sorted_group_indices = sorted(groups.keys())
        group_results: list[dict[str, Any]] = []
        failure_decisions: list[dict[str, Any]] = []
        retry_counts: dict[str, int] = defaultdict(int)
        reassign_counts: dict[str, int] = defaultdict(int)
        memory_lineage_kwargs: dict[str, str] = {}
        if memory_command_event_id is not None:
            memory_lineage_kwargs["memory_command_event_id"] = memory_command_event_id
        if memory_plan_event_id is not None:
            memory_lineage_kwargs["memory_plan_event_id"] = memory_plan_event_id

        for group_index in sorted_group_indices:
            if not _wait_for_run_control(run_control):
                return {
                    "status": "cancelled",
                    "message": "Mission Run was cancelled by operator request.",
                    "mission_id": mission_id,
                    "group_results": group_results,
                    "failure_decisions": failure_decisions,
                }
            group_subtasks = groups[group_index]
            subtask_results: list[dict[str, Any]] = []
            current_attempts: list[_SubmittedAttempt] = []
            terminal_states: list[dict[str, Any]] = []
            belief_records, belief_blocks = self._evaluate_dispatch_beliefs(
                mission_id=mission_id,
                plan_id=current_graph.plan_id,
                nodes=[
                    current_nodes[node_id]
                    for node_id, _ in group_subtasks
                ],
            )
            if belief_blocks:
                terminal_states.extend(belief_blocks)
                subtask_results.extend(belief_blocks)
                for terminal in belief_blocks:
                    node_id = str(terminal["node_id"])
                    node = current_nodes[node_id]
                    node_executions[node_id] = MissionNodeExecution(
                        plan_id=current_graph.plan_id,
                        node_id=node_id,
                        robot_id=node.robot_id,
                        status="blocked",
                        node_spec_hash=execution_spec_hash(node),
                        updated_at=datetime.now(timezone.utc).isoformat(),
                        dispatch_belief_gate=belief_records.get(node_id),
                    )
            else:
                for subtask_index, (node_id, subtask) in enumerate(
                    group_subtasks
                ):
                    result = self.mission_agent.submit_subtask(
                        subtask.robot_id,
                        subtask.command,
                        session_id=mission_id,
                        dedupe_key=(
                            f"{mission_id}-{current_graph.plan_id}-{node_id}"
                        ),
                        operator=operator,
                        mission={
                            "mission_id": mission_id,
                            "execution_group": subtask.execution_group,
                        },
                        mission_subtask=subtask,
                        mission_node_id=node_id,
                        **memory_lineage_kwargs,
                    )
                    subtask_results.append(result)
                    attempt = self._submitted_attempt(
                        result=result,
                        logical_subtask_key=f"{group_index}:{subtask_index}",
                        subtask=subtask,
                        node_id=node_id,
                        plan_id=current_graph.plan_id,
                    )
                    if attempt is not None:
                        current_attempts.append(attempt)
                        node = current_nodes[node_id]
                        node_executions[node_id] = self._execution_from_attempt(
                            attempt,
                            status=str(result.get("status") or "accepted"),
                            node=node,
                            dispatch_belief_gate=belief_records.get(node_id),
                        )

            checkpoint_error = self._persist_checkpoint(
                current_graph,
                node_executions,
                memory_command_event_id=memory_command_event_id,
                memory_plan_event_id=memory_plan_event_id,
            )
            if checkpoint_error is not None:
                return {
                    "status": "blocked",
                    "message": checkpoint_error,
                    "mission_id": mission_id,
                    "group_results": group_results,
                    "failure_decisions": failure_decisions,
                }

            # Poll + failure handling loop for this group
            group_terminal = list(terminal_states)
            polled_terminal = self._poll_group_terminal(
                mission_id,
                current_attempts,
                run_control=run_control,
            )
            group_terminal.extend(polled_terminal)
            terminal_states.extend(polled_terminal)
            if _has_nonterminal_states(
                polled_terminal,
                expected_count=len(current_attempts),
            ):
                timeout_states = _timeout_attempt_states(
                    current_attempts,
                    polled_terminal,
                )
                group_terminal = timeout_states
                terminal_states.extend(timeout_states)
                self._update_execution_states(
                    mission_id,
                    node_executions,
                    current_attempts,
                    timeout_states,
                    current_nodes,
                )
                self._mark_mission_subtasks_terminal(
                    mission_id,
                    timeout_states,
                )
                self._persist_checkpoint(
                    current_graph,
                    node_executions,
                    memory_command_event_id=memory_command_event_id,
                    memory_plan_event_id=memory_plan_event_id,
                )
                group_results.append({
                    "group_index": group_index,
                    "subtask_results": subtask_results,
                    "terminal_states": terminal_states,
                })
                return {
                    "status": "timed_out",
                    "message": "One or more Robot subtasks did not reach a terminal state before the group timeout.",
                    "mission_id": mission_id,
                    "group_results": group_results,
                    "failure_decisions": failure_decisions,
                }
            self._update_execution_states(
                mission_id,
                node_executions,
                current_attempts,
                group_terminal,
                current_nodes,
            )
            refreshed, _ = self._recheck_completion_evidence(
                mission_id,
                node_executions,
                current_attempts,
                group_terminal,
                current_nodes,
            )
            if refreshed:
                terminal_states.extend(refreshed)
                group_terminal = refreshed
            checkpoint_error = self._persist_checkpoint(
                current_graph,
                node_executions,
                memory_command_event_id=memory_command_event_id,
                memory_plan_event_id=memory_plan_event_id,
            )
            if checkpoint_error is not None:
                return {
                    "status": "blocked",
                    "message": checkpoint_error,
                    "mission_id": mission_id,
                    "group_results": group_results,
                    "failure_decisions": failure_decisions,
                }

            revision_results = self._coordinate_plan_invalidations(
                mission_id,
                group_terminal,
            )
            revision_terminal = next(
                (
                    item
                    for item in revision_results
                    if item.get("status")
                    not in {"retained", "retry_allowed"}
                ),
                None,
            )
            if revision_terminal is not None:
                group_results.append({
                    "group_index": group_index,
                    "subtask_results": subtask_results,
                    "terminal_states": terminal_states,
                    "revision_results": revision_results,
                })
                if revision_terminal.get("status") == "revised":
                    continued = self._continue_revised_plan(
                        mission_id=mission_id,
                        current_graph=current_graph,
                        current_executions=node_executions,
                        revision_result=revision_terminal,
                        operator=operator,
                        memory_command_event_id=memory_command_event_id,
                        memory_plan_event_id=memory_plan_event_id,
                    )
                    group_results.extend(
                        continued.pop("group_results", [])
                    )
                    return {
                        **continued,
                        "mission_id": mission_id,
                        "group_results": group_results,
                        "failure_decisions": failure_decisions,
                        "plan_revision": revision_terminal,
                    }
                return {
                    "status": (
                        "replanned"
                        if revision_terminal.get("status") == "revised"
                        else revision_terminal.get("status", "blocked")
                    ),
                    "message": revision_terminal.get(
                        "message",
                        "Mission execution event stopped the active plan.",
                    ),
                    "mission_id": mission_id,
                    "group_results": group_results,
                    "failure_decisions": failure_decisions,
                    "plan_revision": revision_terminal,
                }

            # Evaluate failures and execute decisions
            actions = self._evaluate_group_failures(
                group_terminal, current_attempts, plan,
                retry_counts, reassign_counts, failure_decisions,
            )

            # Execute retry/reassign actions
            while actions:
                recovery_nodes = [
                    current_nodes[action["attempt"].node_id]
                    for action in actions
                ]
                recovery_belief_records, recovery_belief_blocks = (
                    self._evaluate_dispatch_beliefs(
                        mission_id=mission_id,
                        plan_id=current_graph.plan_id,
                        nodes=recovery_nodes,
                    )
                )
                if recovery_belief_blocks:
                    terminal_states.extend(recovery_belief_blocks)
                    subtask_results.extend(recovery_belief_blocks)
                    for terminal in recovery_belief_blocks:
                        node_id = str(terminal["node_id"])
                        node = current_nodes[node_id]
                        previous = node_executions.get(node_id)
                        node_executions[node_id] = MissionNodeExecution(
                            plan_id=current_graph.plan_id,
                            node_id=node_id,
                            robot_id=node.robot_id,
                            status="blocked",
                            node_spec_hash=execution_spec_hash(node),
                            updated_at=datetime.now(timezone.utc).isoformat(),
                            dispatch_belief_gate=(
                                recovery_belief_records.get(node_id)
                            ),
                            recovery_attempt=(
                                previous.recovery_attempt
                                if previous is not None
                                else 0
                            ),
                            recovery_action=(
                                previous.recovery_action
                                if previous is not None
                                else None
                            ),
                        )
                    checkpoint_error = self._persist_checkpoint(
                        current_graph,
                        node_executions,
                        memory_command_event_id=memory_command_event_id,
                        memory_plan_event_id=memory_plan_event_id,
                    )
                    if checkpoint_error is not None:
                        return {
                            "status": "blocked",
                            "message": checkpoint_error,
                            "mission_id": mission_id,
                            "group_results": group_results,
                            "failure_decisions": failure_decisions,
                        }
                    revision_results = self._coordinate_plan_invalidations(
                        mission_id,
                        recovery_belief_blocks,
                    )
                    revision_terminal = next(
                        (
                            item
                            for item in revision_results
                            if item.get("status")
                            not in {"retained", "retry_allowed"}
                        ),
                        None,
                    )
                    group_results.append({
                        "group_index": group_index,
                        "subtask_results": subtask_results,
                        "terminal_states": terminal_states,
                        "revision_results": revision_results,
                    })
                    if (
                        revision_terminal is not None
                        and revision_terminal.get("status") == "revised"
                    ):
                        continued = self._continue_revised_plan(
                            mission_id=mission_id,
                            current_graph=current_graph,
                            current_executions=node_executions,
                            revision_result=revision_terminal,
                            operator=operator,
                            memory_command_event_id=memory_command_event_id,
                            memory_plan_event_id=memory_plan_event_id,
                        )
                        group_results.extend(
                            continued.pop("group_results", [])
                        )
                        return {
                            **continued,
                            "mission_id": mission_id,
                            "group_results": group_results,
                            "failure_decisions": failure_decisions,
                            "plan_revision": revision_terminal,
                        }
                    return {
                        "status": (
                            revision_terminal.get("status", "blocked")
                            if revision_terminal is not None
                            else "blocked"
                        ),
                        "message": (
                            revision_terminal.get(
                                "message",
                                "Dispatch belief gate blocked recovery.",
                            )
                            if revision_terminal is not None
                            else "Dispatch belief gate blocked recovery."
                        ),
                        "mission_id": mission_id,
                        "group_results": group_results,
                        "failure_decisions": failure_decisions,
                        "plan_revision": revision_terminal,
                    }
                next_attempts: list[_SubmittedAttempt] = []
                for action in actions:
                    previous_attempt = action["attempt"]
                    if action["action"] == "retry":
                        subtask = previous_attempt.subtask
                        result = self.mission_agent.submit_subtask(
                            subtask.robot_id,
                            subtask.command,
                            session_id=mission_id,
                            dedupe_key=f"{mission_id}-{subtask.robot_id}-{subtask.floor}-retry{retry_counts[previous_attempt.logical_subtask_key]}",
                            operator=operator,
                            mission={"mission_id": mission_id, "execution_group": subtask.execution_group},
                            mission_subtask=subtask,
                            mission_node_id=previous_attempt.node_id,
                            **memory_lineage_kwargs,
                        )
                        subtask_results.append(result)
                        attempt = self._submitted_attempt(
                            result=result,
                            logical_subtask_key=previous_attempt.logical_subtask_key,
                            subtask=subtask,
                            node_id=previous_attempt.node_id,
                            plan_id=previous_attempt.plan_id,
                        )
                        if attempt is not None:
                            next_attempts.append(attempt)
                            node_executions[attempt.node_id] = (
                                self._execution_from_attempt(
                                    attempt,
                                    status=str(
                                        result.get("status")
                                        or "accepted"
                                    ),
                                    node=current_nodes[attempt.node_id],
                                    dispatch_belief_gate=(
                                        recovery_belief_records.get(
                                            attempt.node_id
                                        )
                                    ),
                                    recovery_attempt=retry_counts[
                                        previous_attempt.logical_subtask_key
                                    ],
                                    recovery_action="retry",
                                )
                            )
                    elif action["action"] == "reassign":
                        subtask = previous_attempt.subtask
                        new_robot = action["new_robot"]
                        reassigned_subtask = MissionSubtask(
                            robot_id=new_robot,
                            command=subtask.command,
                            floor=subtask.floor,
                            capability_required=subtask.capability_required,
                            execution_group=subtask.execution_group,
                            node_id=subtask.node_id,
                            task_type=subtask.task_type,
                            target=dict(subtask.target),
                            completion_goal=subtask.completion_goal,
                            completion_contract=dict(
                                subtask.completion_contract
                            ),
                            belief_requirements=[
                                dict(requirement)
                                for requirement in subtask.belief_requirements
                            ],
                        )
                        result = self.mission_agent.submit_subtask(
                            new_robot,
                            subtask.command,
                            session_id=mission_id,
                            dedupe_key=f"{mission_id}-{new_robot}-{subtask.floor}-reassign{reassign_counts[previous_attempt.logical_subtask_key]}",
                            operator=operator,
                            mission={"mission_id": mission_id, "execution_group": subtask.execution_group},
                            mission_subtask=reassigned_subtask,
                            mission_node_id=previous_attempt.node_id,
                            **memory_lineage_kwargs,
                        )
                        subtask_results.append(result)
                        attempt = self._submitted_attempt(
                            result=result,
                            logical_subtask_key=previous_attempt.logical_subtask_key,
                            subtask=reassigned_subtask,
                            node_id=previous_attempt.node_id,
                            plan_id=previous_attempt.plan_id,
                        )
                        if attempt is not None:
                            next_attempts.append(attempt)
                            node_executions[attempt.node_id] = (
                                self._execution_from_attempt(
                                    attempt,
                                    status=str(
                                        result.get("status")
                                        or "accepted"
                                    ),
                                    node=current_nodes[attempt.node_id],
                                    dispatch_belief_gate=(
                                        recovery_belief_records.get(
                                            attempt.node_id
                                        )
                                    ),
                                    recovery_attempt=reassign_counts[
                                        previous_attempt.logical_subtask_key
                                    ],
                                    recovery_action="reassign",
                                )
                            )

                # Poll again for the retried/reassigned subtasks
                current_attempts = next_attempts
                checkpoint_error = self._persist_checkpoint(
                    current_graph,
                    node_executions,
                    memory_command_event_id=memory_command_event_id,
                    memory_plan_event_id=memory_plan_event_id,
                )
                if checkpoint_error is not None:
                    return {
                        "status": "blocked",
                        "message": checkpoint_error,
                        "mission_id": mission_id,
                        "group_results": group_results,
                        "failure_decisions": failure_decisions,
                    }
                group_terminal = self._poll_group_terminal(
                    mission_id,
                    current_attempts,
                    run_control=run_control,
                )
                terminal_states.extend(group_terminal)
                if _has_nonterminal_states(
                    group_terminal,
                    expected_count=len(current_attempts),
                ):
                    timeout_states = _timeout_attempt_states(
                        current_attempts,
                        group_terminal,
                    )
                    terminal_states.extend(timeout_states)
                    self._update_execution_states(
                        mission_id,
                        node_executions,
                        current_attempts,
                        timeout_states,
                        current_nodes,
                    )
                    self._mark_mission_subtasks_terminal(
                        mission_id,
                        timeout_states,
                    )
                    self._persist_checkpoint(
                        current_graph,
                        node_executions,
                        memory_command_event_id=memory_command_event_id,
                        memory_plan_event_id=memory_plan_event_id,
                    )
                    group_results.append({
                        "group_index": group_index,
                        "subtask_results": subtask_results,
                        "terminal_states": terminal_states,
                    })
                    return {
                        "status": "timed_out",
                        "message": "A retry or reassignment did not reach a terminal Robot state before the group timeout.",
                        "mission_id": mission_id,
                        "group_results": group_results,
                        "failure_decisions": failure_decisions,
                    }
                self._update_execution_states(
                    mission_id,
                    node_executions,
                    current_attempts,
                    group_terminal,
                    current_nodes,
                )
                refreshed, _ = self._recheck_completion_evidence(
                    mission_id,
                    node_executions,
                    current_attempts,
                    group_terminal,
                    current_nodes,
                )
                if refreshed:
                    terminal_states.extend(refreshed)
                    group_terminal = refreshed
                checkpoint_error = self._persist_checkpoint(
                    current_graph,
                    node_executions,
                    memory_command_event_id=memory_command_event_id,
                    memory_plan_event_id=memory_plan_event_id,
                )
                if checkpoint_error is not None:
                    return {
                        "status": "blocked",
                        "message": checkpoint_error,
                        "mission_id": mission_id,
                        "group_results": group_results,
                        "failure_decisions": failure_decisions,
                    }
                actions = self._evaluate_group_failures(
                    group_terminal, current_attempts, plan,
                    retry_counts, reassign_counts, failure_decisions,
                )

            group_result: dict[str, Any] = {
                "group_index": group_index,
                "subtask_results": subtask_results,
                "terminal_states": terminal_states,
            }
            if revision_results:
                group_result["revision_results"] = revision_results
            group_results.append(group_result)

            if _run_control_cancelled(run_control):
                return {
                    "status": "cancelled",
                    "message": "Mission Run was cancelled by operator request.",
                    "mission_id": mission_id,
                    "group_results": group_results,
                    "failure_decisions": failure_decisions,
                }

            # Check for abort decision
            if any(d.get("decision") == "abort" for d in failure_decisions):
                return {
                    "status": "aborted",
                    "message": f"Mission aborted due to failure policy.",
                    "mission_id": mission_id,
                    "group_results": group_results,
                    "failure_decisions": failure_decisions,
                }

        final_status = "succeeded"
        if failure_decisions:
            has_escalated = any(d.get("decision") == "escalated" for d in failure_decisions)
            if has_escalated:
                final_status = "escalated"

        return {
            "status": final_status,
            "mission_id": mission_id,
            "group_results": group_results,
            "failure_decisions": failure_decisions,
        }

    def resume_pending_dispatches(
        self,
        *,
        operator: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Restore every latest checkpoint and continue non-terminal graphs."""
        if self.dispatch_store is None:
            return []
        try:
            checkpoints = (
                self.dispatch_store.recoverable_latest_by_mission()
            )
        except (OSError, ValueError) as exc:
            return [{
                "status": "blocked",
                "message": (
                    "Mission dispatch checkpoints could not be read: "
                    f"{exc}"
                ),
            }]
        candidates = [
            checkpoint
            for checkpoint in checkpoints.values()
            if (
                checkpoint.task_graph is None
                or checkpoint.superseded_executions
                or any(
                    execution.status == "pending"
                    or execution.is_active
                    for execution in checkpoint.node_executions
                )
            )
        ]
        return [
            self.resume_from_checkpoint(
                checkpoint,
                operator=operator,
            )
            for checkpoint in sorted(
                candidates,
                key=lambda item: (item.updated_at, item.mission_id),
            )
        ]

    def resume_from_checkpoint(
        self,
        checkpoint: MissionDispatchCheckpoint,
        *,
        operator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate, reconcile, and continue one persisted mission dispatch."""
        validation_error = self._checkpoint_validation_error(checkpoint)
        if validation_error is not None:
            return {
                "status": "blocked",
                "message": validation_error,
                "mission_id": checkpoint.mission_id,
                "active_plan_id": checkpoint.active_plan_id,
                "recovered": False,
                "group_results": [],
            }
        graph = checkpoint.task_graph
        assert graph is not None
        executions = {
            execution.node_id: execution
            for execution in checkpoint.node_executions
        }
        cancellation_recovery = self._recover_checkpoint_cancellations(
            checkpoint,
            operator=operator,
        )
        if isinstance(cancellation_recovery, str):
            return {
                "status": "blocked",
                "message": cancellation_recovery,
                "mission_id": checkpoint.mission_id,
                "active_plan_id": checkpoint.active_plan_id,
                "recovered": False,
                "group_results": [],
            }
        cancellation_results, cancellation_terminal_states = (
            cancellation_recovery
        )
        if checkpoint.superseded_executions:
            checkpoint_error = self._persist_checkpoint(
                graph,
                executions,
                fenced_task_ids=set(checkpoint.fenced_task_ids),
                invalidation_event_id=checkpoint.invalidation_event_id,
                memory_command_event_id=(
                    checkpoint.memory_command_event_id
                ),
                memory_plan_event_id=checkpoint.memory_plan_event_id,
            )
            if checkpoint_error is not None:
                return {
                    "status": "blocked",
                    "message": checkpoint_error,
                    "mission_id": checkpoint.mission_id,
                    "active_plan_id": checkpoint.active_plan_id,
                    "recovered": False,
                    "group_results": [],
                    "recovery_cancellation_results": cancellation_results,
                    "recovery_cancellation_terminal_states": (
                        cancellation_terminal_states
                    ),
                }
        if not any(
            execution.status == "pending" or execution.is_active
            for execution in executions.values()
        ):
            return {
                "status": (
                    "succeeded"
                    if all(
                        execution.is_succeeded
                        for execution in executions.values()
                    )
                    else "failed"
                ),
                "message": (
                    f"Checkpoint {checkpoint.active_plan_id} is already "
                    "terminal."
                ),
                "mission_id": checkpoint.mission_id,
                "active_plan_id": checkpoint.active_plan_id,
                "recovered": True,
                "resumed": False,
                "group_results": [],
                "node_executions": [
                    executions[node_id].to_dict()
                    for node_id in sorted(executions)
                ],
                "recovery_cancellation_results": cancellation_results,
                "recovery_cancellation_terminal_states": (
                    cancellation_terminal_states
                ),
            }

        graph_errors = self.mission_agent.restore_active_task_graph(graph)
        if graph_errors:
            return {
                "status": "blocked",
                "message": (
                    "Checkpoint task graph failed deterministic validation."
                ),
                "errors": graph_errors,
                "mission_id": checkpoint.mission_id,
                "active_plan_id": checkpoint.active_plan_id,
                "recovered": False,
                "group_results": [],
            }

        reconciled = self._reconcile_checkpoint_executions(
            checkpoint=checkpoint,
            graph=graph,
            executions=executions,
        )
        if isinstance(reconciled, str):
            return {
                "status": "blocked",
                "message": reconciled,
                "mission_id": checkpoint.mission_id,
                "active_plan_id": checkpoint.active_plan_id,
                "recovered": False,
                "group_results": [],
            }
        executions, observed = reconciled
        checkpoint_error = self._persist_checkpoint(
            graph,
            executions,
            fenced_task_ids=set(checkpoint.fenced_task_ids),
            invalidation_event_id=checkpoint.invalidation_event_id,
            memory_command_event_id=checkpoint.memory_command_event_id,
            memory_plan_event_id=checkpoint.memory_plan_event_id,
        )
        if checkpoint_error is not None:
            return {
                "status": "blocked",
                "message": checkpoint_error,
                "mission_id": checkpoint.mission_id,
                "active_plan_id": checkpoint.active_plan_id,
                "recovered": False,
                "group_results": [],
            }

        revision = self._terminal_revision(
            checkpoint.mission_id,
            observed,
        )
        if revision is not None:
            if revision.get("status") != "revised":
                return {
                    "status": revision.get("status", "blocked"),
                    "message": revision.get(
                        "message",
                        "Recovered execution invalidated the active plan.",
                    ),
                    "mission_id": checkpoint.mission_id,
                    "active_plan_id": checkpoint.active_plan_id,
                    "recovered": True,
                    "resumed": True,
                    "group_results": [],
                    "plan_revision": revision,
                    "recovery_cancellation_results": (
                        cancellation_results
                    ),
                    "recovery_cancellation_terminal_states": (
                        cancellation_terminal_states
                    ),
                }
            result = self._continue_revised_plan(
                mission_id=checkpoint.mission_id,
                current_graph=graph,
                current_executions=executions,
                revision_result=revision,
                operator=operator,
                memory_command_event_id=(
                    checkpoint.memory_command_event_id
                ),
                memory_plan_event_id=checkpoint.memory_plan_event_id,
            )
            result["mission_id"] = checkpoint.mission_id
            result["recovered"] = True
            result["resumed"] = True
            result["plan_revision"] = revision
            result["recovery_cancellation_results"] = (
                cancellation_results
            )
            result["recovery_cancellation_terminal_states"] = (
                cancellation_terminal_states
            )
            return result

        result = self._execute_revised_graph(
            graph=graph,
            executions=executions,
            mission_id=checkpoint.mission_id,
            operator=operator,
            memory_command_event_id=checkpoint.memory_command_event_id,
            memory_plan_event_id=checkpoint.memory_plan_event_id,
            fenced_task_ids=set(checkpoint.fenced_task_ids),
        )
        result["mission_id"] = checkpoint.mission_id
        result["recovered"] = True
        result["resumed"] = True
        result["recovery_cancellation_results"] = cancellation_results
        result["recovery_cancellation_terminal_states"] = (
            cancellation_terminal_states
        )
        return result

    def _checkpoint_validation_error(
        self,
        checkpoint: MissionDispatchCheckpoint,
    ) -> str | None:
        graph = checkpoint.task_graph
        if graph is None:
            return (
                "Checkpoint predates restart-safe task graph persistence; "
                "automatic dispatch is blocked."
            )
        graph_errors = MissionTaskGraphValidator().validate(
            graph,
            self.registry,
        )
        if graph_errors:
            return (
                "Checkpoint task graph failed deterministic validation: "
                + "; ".join(graph_errors)
            )
        seen_task_ids: set[str] = set()
        nodes = {node.node_id: node for node in graph.nodes}
        for execution in checkpoint.node_executions:
            if execution.plan_id != checkpoint.active_plan_id:
                return "Checkpoint execution references a different plan."
            node = nodes.get(execution.node_id)
            if node is None:
                return "Checkpoint execution references an unknown graph node."
            if execution.node_spec_hash != execution_spec_hash(node):
                return (
                    f"Checkpoint node {execution.node_id!r} specification "
                    "hash does not match the persisted graph."
                )
            if execution.is_active and execution.task_id is None:
                return (
                    f"Checkpoint node {execution.node_id!r} is active "
                    "without a robot task ID."
                )
            if execution.task_id is not None:
                if execution.task_id in seen_task_ids:
                    return "Checkpoint reuses one robot task ID across nodes."
                seen_task_ids.add(execution.task_id)
                if execution.task_id in checkpoint.fenced_task_ids:
                    return (
                        "Checkpoint active graph references a fenced robot "
                        "task ID."
                    )
        if (
            self.mission_agent.mission_registry is None
            or self.mission_agent.mission_registry.get_mission(
                checkpoint.mission_id
            )
            is None
        ):
            return "Checkpoint mission registry record is unavailable."
        return None

    def _recover_checkpoint_cancellations(
        self,
        checkpoint: MissionDispatchCheckpoint,
        *,
        operator: dict[str, Any] | None,
    ) -> (
        tuple[list[dict[str, Any]], list[dict[str, Any]]]
        | str
    ):
        if not checkpoint.superseded_executions:
            return [], []
        mission_registry = self.mission_agent.mission_registry
        assert mission_registry is not None
        mission = mission_registry.get_mission(checkpoint.mission_id)
        assert mission is not None
        known_tasks = {
            (subtask.robot_id, subtask.task_id)
            for subtask in mission.subtasks
        }
        cancel_executions: list[MissionNodeExecution] = []
        terminal_states: list[dict[str, Any]] = []
        for execution in checkpoint.superseded_executions:
            assert execution.task_id is not None
            key = (execution.robot_id, execution.task_id)
            if key not in known_tasks:
                return (
                    f"Superseded robot task {execution.task_id!r} is absent "
                    "from the mission registry."
                )
            entry = (
                self.registry.get(execution.robot_id)
                if self.registry is not None
                else None
            )
            if entry is None or not entry.enabled:
                return (
                    f"Superseded robot {execution.robot_id!r} is "
                    "unavailable for cancellation recovery."
                )
            try:
                trace = self.mission_agent.subagent_client.get_task_trace(
                    entry,
                    execution.task_id,
                )
            except Exception as exc:
                return (
                    f"Superseded robot task {execution.task_id!r} could not "
                    f"be reconciled after restart: {exc}"
                )
            identity_error = _recovery_trace_identity_error(
                trace,
                execution,
            )
            if identity_error is not None:
                return identity_error
            status = _recovery_status_from_trace(trace)
            if status is None:
                return (
                    f"Superseded robot task {execution.task_id!r} returned "
                    "no authoritative runtime status after restart."
                )
            try:
                mission_registry.update_subtask(
                    mission_id=checkpoint.mission_id,
                    robot_id=execution.robot_id,
                    task_id=execution.task_id,
                    status=status,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                    result=(
                        trace.get("result")
                        if isinstance(trace.get("result"), dict)
                        else None
                    ),
                )
            except KeyError:
                return (
                    f"Superseded robot task {execution.task_id!r} could not "
                    "be projected into the mission registry."
                )
            terminal = {
                "robot_id": execution.robot_id,
                "task_id": execution.task_id,
                "status": status,
                "node_id": execution.node_id,
                "robot_trace": trace,
            }
            if status in TERMINAL_SUBTASK_STATUSES:
                terminal_states.append(terminal)
            else:
                cancel_executions.append(execution)

        decision = RevisionDispatchDecision(
            current_plan_id=(
                checkpoint.supersedes_plan_id or "superseded-plan"
            ),
            revised_plan_id=checkpoint.active_plan_id,
            carried_node_ids=(),
            preserved_node_ids=(),
            pending_node_ids=(),
            cancel_executions=tuple(cancel_executions),
            fenced_task_ids=checkpoint.fenced_task_ids,
        )
        cancellation_results = self._cancel_superseded_executions(
            mission_id=checkpoint.mission_id,
            decision=decision,
            operator=operator,
        )
        if any(
            result.get("status")
            not in {"cancel_requested", "cancelled", "already_terminal"}
            for result in cancellation_results
        ):
            return (
                "Superseded robot execution could not be cancelled during "
                "restart recovery."
            )
        waited = self._wait_for_superseded_terminal(
            mission_id=checkpoint.mission_id,
            decision=decision,
        )
        if waited is None:
            return (
                "Superseded robot execution did not terminate before the "
                "restart recovery deadline."
            )
        terminal_states.extend(waited)
        return cancellation_results, terminal_states

    def _reconcile_checkpoint_executions(
        self,
        *,
        checkpoint: MissionDispatchCheckpoint,
        graph: MissionTaskGraph,
        executions: dict[str, MissionNodeExecution],
    ) -> (
        tuple[
            dict[str, MissionNodeExecution],
            list[dict[str, Any]],
        ]
        | str
    ):
        observed: list[dict[str, Any]] = []
        nodes = {node.node_id: node for node in graph.nodes}
        mission_registry = self.mission_agent.mission_registry
        assert mission_registry is not None
        mission = mission_registry.get_mission(checkpoint.mission_id)
        assert mission is not None
        known_tasks = {
            (subtask.robot_id, subtask.task_id)
            for subtask in mission.subtasks
        }
        for node_id in sorted(executions):
            execution = executions[node_id]
            if execution.task_id is None or not execution.is_active:
                continue
            key = (execution.robot_id, execution.task_id)
            if key not in known_tasks:
                return (
                    f"Checkpoint robot task {execution.task_id!r} is absent "
                    "from the mission registry."
                )
            entry = (
                self.registry.get(execution.robot_id)
                if self.registry is not None
                else None
            )
            if entry is None or not entry.enabled:
                return (
                    f"Checkpoint robot {execution.robot_id!r} is unavailable."
                )
            try:
                trace = self.mission_agent.subagent_client.get_task_trace(
                    entry,
                    execution.task_id,
                )
            except Exception as exc:
                return (
                    f"Robot task {execution.task_id!r} could not be "
                    f"reconciled after restart: {exc}"
                )
            identity_error = _recovery_trace_identity_error(
                trace,
                execution,
            )
            if identity_error is not None:
                return identity_error
            status = _recovery_status_from_trace(trace)
            if status is None:
                return (
                    f"Robot task {execution.task_id!r} returned no "
                    "authoritative runtime status after restart."
                )
            attempt = self._attempt_from_execution(execution, nodes)
            if attempt is None:
                return (
                    f"Checkpoint node {node_id!r} cannot be reconstructed "
                    "as a robot task."
                )
            updated = self._execution_from_attempt(
                attempt,
                status=status,
                node=nodes[node_id],
                recovery_attempt=execution.recovery_attempt,
                recovery_action=execution.recovery_action,
            )
            executions[node_id] = updated
            try:
                mission_registry.update_subtask(
                    mission_id=checkpoint.mission_id,
                    robot_id=execution.robot_id,
                    task_id=execution.task_id,
                    status=status,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                    result=(
                        trace.get("result")
                        if isinstance(trace.get("result"), dict)
                        else None
                    ),
                )
            except KeyError:
                return (
                    f"Robot task {execution.task_id!r} could not be "
                    "projected into the mission registry."
                )
            observed.append({
                "robot_id": execution.robot_id,
                "task_id": execution.task_id,
                "status": status,
                "node_id": node_id,
                "robot_trace": trace,
            })
        return executions, observed

    def _continue_revised_plan(
        self,
        *,
        mission_id: str,
        current_graph: MissionTaskGraph,
        current_executions: dict[str, MissionNodeExecution],
        revision_result: dict[str, Any],
        operator: dict[str, Any] | None,
        memory_command_event_id: str | None,
        memory_plan_event_id: str | None,
    ) -> dict[str, Any]:
        revised_graph = self.mission_agent.active_task_graph(mission_id)
        raw_event = revision_result.get("event")
        if (
            revised_graph is None
            or not isinstance(raw_event, dict)
        ):
            return {
                "status": "blocked",
                "message": (
                    "Revised plan could not be activated for dispatch."
                ),
                "group_results": [],
            }
        try:
            event = MissionExecutionEvent.from_dict(raw_event)
            decision = self.revision_reconciler.reconcile(
                current_graph=current_graph,
                revised_graph=revised_graph,
                executions=current_executions.values(),
                invalidation_event=event,
            )
        except (TypeError, ValueError) as exc:
            return {
                "status": "blocked",
                "message": f"Revised plan reconciliation failed: {exc}",
                "group_results": [],
            }

        revised_executions = self._revised_execution_state(
            revised_graph=revised_graph,
            current_executions=current_executions,
            decision=decision,
        )
        revised_plan_event_id = revision_result.get(
            "revision_memory_event_id"
        )
        effective_plan_event_id = (
            revised_plan_event_id
            if isinstance(revised_plan_event_id, str)
            and revised_plan_event_id
            else memory_plan_event_id
        )
        checkpoint_error = self._persist_checkpoint(
            revised_graph,
            revised_executions,
            fenced_task_ids=decision.fenced_task_ids,
            superseded_executions=decision.cancel_executions,
            invalidation_event_id=event.event_id,
            memory_command_event_id=memory_command_event_id,
            memory_plan_event_id=effective_plan_event_id,
        )
        if checkpoint_error is not None:
            return {
                "status": "blocked",
                "message": checkpoint_error,
                "group_results": [],
                "revision_dispatch": decision.to_dict(),
                "cancellation_results": [],
            }

        cancellation_results = self._cancel_superseded_executions(
            mission_id=mission_id,
            decision=decision,
            operator=operator,
        )
        if any(
            result.get("status")
            not in {"cancel_requested", "cancelled", "already_terminal"}
            for result in cancellation_results
        ):
            return {
                "status": "blocked",
                "message": (
                    "Superseded robot execution could not be cancelled."
                ),
                "group_results": [],
                "revision_dispatch": decision.to_dict(),
                "cancellation_results": cancellation_results,
            }

        cancellation_terminal_states = (
            self._wait_for_superseded_terminal(
                mission_id=mission_id,
                decision=decision,
            )
        )
        if cancellation_terminal_states is None:
            return {
                "status": "blocked",
                "message": (
                    "Superseded robot execution did not terminate before "
                    "the revision cancellation deadline."
                ),
                "group_results": [],
                "revision_dispatch": decision.to_dict(),
                "cancellation_results": cancellation_results,
                "cancellation_terminal_states": [],
            }

        result = self._execute_revised_graph(
            graph=revised_graph,
            executions=revised_executions,
            mission_id=mission_id,
            operator=operator,
            memory_command_event_id=memory_command_event_id,
            memory_plan_event_id=effective_plan_event_id,
            fenced_task_ids=set(decision.fenced_task_ids),
        )
        result["revision_dispatch"] = decision.to_dict()
        result["cancellation_results"] = cancellation_results
        result["cancellation_terminal_states"] = (
            cancellation_terminal_states
        )
        return result

    def _execute_revised_graph(
        self,
        *,
        graph: MissionTaskGraph,
        executions: dict[str, MissionNodeExecution],
        mission_id: str,
        operator: dict[str, Any] | None,
        memory_command_event_id: str | None,
        memory_plan_event_id: str | None,
        fenced_task_ids: set[str],
    ) -> dict[str, Any]:
        nodes = {node.node_id: node for node in graph.nodes}
        group_results: list[dict[str, Any]] = []
        dispatch_round = 0

        while True:
            active_attempts = [
                self._attempt_from_execution(execution, nodes)
                for execution in executions.values()
                if execution.is_active and execution.task_id is not None
            ]
            active_attempts = [
                attempt for attempt in active_attempts
                if attempt is not None
            ]
            if active_attempts:
                terminal = self._poll_group_terminal(
                    mission_id,
                    active_attempts,
                )
                self._update_execution_states(
                    mission_id,
                    executions,
                    active_attempts,
                    terminal,
                    nodes,
                )
                terminal_history = list(terminal)
                refreshed, rechecked_node_ids = (
                    self._recheck_completion_evidence(
                        mission_id,
                        executions,
                        active_attempts,
                        terminal,
                        nodes,
                    )
                )
                if refreshed:
                    terminal_history.extend(refreshed)
                    terminal = refreshed
                checkpoint_error = self._persist_checkpoint(
                    graph,
                    executions,
                    fenced_task_ids=fenced_task_ids,
                    memory_command_event_id=memory_command_event_id,
                    memory_plan_event_id=memory_plan_event_id,
                )
                if checkpoint_error is not None:
                    return {
                        "status": "blocked",
                        "message": checkpoint_error,
                        "group_results": group_results,
                    }
                group_results.append({
                    "group_index": f"revision:{graph.revision}:active",
                    "plan_id": graph.plan_id,
                    "node_ids": [
                        attempt.node_id for attempt in active_attempts
                    ],
                    "subtask_results": [],
                    "terminal_states": terminal_history,
                    "evidence_recheck_node_ids": list(
                        rechecked_node_ids
                    ),
                })
                revision = self._terminal_revision(
                    mission_id,
                    terminal,
                )
                if revision is not None:
                    return self._continue_nested_revision(
                        mission_id=mission_id,
                        current_graph=graph,
                        current_executions=executions,
                        revision_result=revision,
                        operator=operator,
                        memory_command_event_id=memory_command_event_id,
                        memory_plan_event_id=memory_plan_event_id,
                        prior_group_results=group_results,
                    )
                if any(
                    execution.is_active
                    for execution in executions.values()
                ):
                    return {
                        "status": "blocked",
                        "message": (
                            "Active mission-plan execution did not reach a "
                            "terminal state before timeout."
                        ),
                        "group_results": group_results,
                    }
                continue

            succeeded = {
                node_id
                for node_id, execution in executions.items()
                if execution.is_succeeded
            }
            if len(succeeded) == len(nodes):
                return {
                    "status": "succeeded",
                    "message": (
                        f"Mission plan {graph.plan_id} completed."
                    ),
                    "group_results": group_results,
                    "active_plan_id": graph.plan_id,
                    "node_executions": [
                        executions[node_id].to_dict()
                        for node_id in sorted(executions)
                    ],
                }

            ready = [
                node
                for node_id, node in nodes.items()
                if executions[node_id].status == "pending"
                and set(node.depends_on).issubset(succeeded)
            ]
            if not ready:
                failed = [
                    execution.to_dict()
                    for execution in executions.values()
                    if execution.status not in {
                        "pending",
                        *SUCCEEDED_NODE_STATUSES,
                    }
                ]
                return {
                    "status": "failed" if failed else "blocked",
                    "message": (
                        "Mission plan has failed or unsatisfied dependency "
                        "nodes."
                    ),
                    "group_results": group_results,
                    "active_plan_id": graph.plan_id,
                    "failed_nodes": failed,
                    "node_executions": [
                        executions[node_id].to_dict()
                        for node_id in sorted(executions)
                    ],
                }

            belief_records, belief_blocks = self._evaluate_dispatch_beliefs(
                mission_id=mission_id,
                plan_id=graph.plan_id,
                nodes=ready,
            )
            if belief_blocks:
                for terminal in belief_blocks:
                    node_id = str(terminal["node_id"])
                    node = nodes[node_id]
                    previous = executions[node_id]
                    executions[node_id] = MissionNodeExecution(
                        plan_id=graph.plan_id,
                        node_id=node_id,
                        robot_id=node.robot_id,
                        status="blocked",
                        node_spec_hash=execution_spec_hash(node),
                        updated_at=datetime.now(timezone.utc).isoformat(),
                        dispatch_belief_gate=belief_records.get(node_id),
                        recovery_attempt=previous.recovery_attempt,
                        recovery_action=previous.recovery_action,
                    )
                checkpoint_error = self._persist_checkpoint(
                    graph,
                    executions,
                    fenced_task_ids=fenced_task_ids,
                    memory_command_event_id=memory_command_event_id,
                    memory_plan_event_id=memory_plan_event_id,
                )
                group_results.append({
                    "group_index": (
                        f"revision:{graph.revision}:belief-gate"
                    ),
                    "plan_id": graph.plan_id,
                    "node_ids": [
                        terminal["node_id"]
                        for terminal in belief_blocks
                    ],
                    "subtask_results": list(belief_blocks),
                    "terminal_states": list(belief_blocks),
                })
                if checkpoint_error is not None:
                    return {
                        "status": "blocked",
                        "message": checkpoint_error,
                        "group_results": group_results,
                    }
                revision = self._terminal_revision(
                    mission_id,
                    belief_blocks,
                )
                if revision is not None:
                    return self._continue_nested_revision(
                        mission_id=mission_id,
                        current_graph=graph,
                        current_executions=executions,
                        revision_result=revision,
                        operator=operator,
                        memory_command_event_id=memory_command_event_id,
                        memory_plan_event_id=memory_plan_event_id,
                        prior_group_results=group_results,
                    )
                return {
                    "status": "blocked",
                    "message": (
                        "Dispatch belief gate blocked the ready mission nodes."
                    ),
                    "group_results": group_results,
                }

            dispatch_round += 1
            subtask_results: list[dict[str, Any]] = []
            attempts: list[_SubmittedAttempt] = []
            presence_by_robot: dict[str, dict[str, Any] | str] = {}
            for node in ready:
                readiness_error = self._dispatch_readiness_error(
                    node,
                    presence_by_robot,
                )
                if readiness_error is not None:
                    executions[node.node_id] = MissionNodeExecution(
                        plan_id=graph.plan_id,
                        node_id=node.node_id,
                        robot_id=node.robot_id,
                        status="blocked",
                        node_spec_hash=execution_spec_hash(node),
                        updated_at=datetime.now(timezone.utc).isoformat(),
                    )
                    subtask_results.append({
                        "status": "blocked",
                        "robot_id": node.robot_id,
                        "node_id": node.node_id,
                        "message": readiness_error,
                    })
                    continue
                subtask = self._subtask_from_node(
                    node,
                    execution_group=dispatch_round,
                )
                try:
                    recovery_attempt = executions[
                        node.node_id
                    ].recovery_attempt
                    result = self.mission_agent.submit_subtask(
                        node.robot_id,
                        node.command,
                        session_id=mission_id,
                        dedupe_key=(
                            f"{mission_id}-{graph.plan_id}-{node.node_id}"
                        ),
                        operator=operator,
                        mission={
                            "mission_id": mission_id,
                            "plan_id": graph.plan_id,
                            "plan_revision": graph.revision,
                        },
                        mission_subtask=subtask,
                        mission_node_id=node.node_id,
                        memory_command_event_id=memory_command_event_id,
                        memory_plan_event_id=memory_plan_event_id,
                    )
                except Exception as exc:
                    result = {
                        "status": "blocked",
                        "robot_id": node.robot_id,
                        "node_id": node.node_id,
                        "message": f"Robot task dispatch failed: {exc}",
                    }
                subtask_results.append(result)
                attempt = self._submitted_attempt(
                    result=result,
                    logical_subtask_key=(
                        f"{graph.plan_id}:{node.node_id}"
                    ),
                    subtask=subtask,
                    node_id=node.node_id,
                    plan_id=graph.plan_id,
                )
                if attempt is None:
                    executions[node.node_id] = MissionNodeExecution(
                        plan_id=graph.plan_id,
                        node_id=node.node_id,
                        robot_id=node.robot_id,
                        status="failed",
                        node_spec_hash=execution_spec_hash(node),
                        updated_at=datetime.now(timezone.utc).isoformat(),
                    )
                    continue
                attempts.append(attempt)
                executions[node.node_id] = self._execution_from_attempt(
                    attempt,
                    status=str(result.get("status") or "accepted"),
                    node=node,
                    dispatch_belief_gate=belief_records.get(node.node_id),
                    recovery_attempt=recovery_attempt,
                    recovery_action=(
                        executions[node.node_id].recovery_action
                    ),
                )

            checkpoint_error = self._persist_checkpoint(
                graph,
                executions,
                fenced_task_ids=fenced_task_ids,
                memory_command_event_id=memory_command_event_id,
                memory_plan_event_id=memory_plan_event_id,
            )
            if checkpoint_error is not None:
                return {
                    "status": "blocked",
                    "message": checkpoint_error,
                    "group_results": group_results,
                }
            terminal = self._poll_group_terminal(mission_id, attempts)
            self._update_execution_states(
                mission_id,
                executions,
                attempts,
                terminal,
                nodes,
            )
            terminal_history = list(terminal)
            refreshed, rechecked_node_ids = (
                self._recheck_completion_evidence(
                    mission_id,
                    executions,
                    attempts,
                    terminal,
                    nodes,
                )
            )
            if refreshed:
                terminal_history.extend(refreshed)
                terminal = refreshed
            checkpoint_error = self._persist_checkpoint(
                graph,
                executions,
                fenced_task_ids=fenced_task_ids,
                memory_command_event_id=memory_command_event_id,
                memory_plan_event_id=memory_plan_event_id,
            )
            group_results.append({
                "group_index": (
                    f"revision:{graph.revision}:{dispatch_round}"
                ),
                "plan_id": graph.plan_id,
                "node_ids": [node.node_id for node in ready],
                "subtask_results": subtask_results,
                "terminal_states": terminal_history,
                "evidence_recheck_node_ids": list(rechecked_node_ids),
            })
            if checkpoint_error is not None:
                return {
                    "status": "blocked",
                    "message": checkpoint_error,
                    "group_results": group_results,
                }
            revision = self._terminal_revision(mission_id, terminal)
            if revision is not None:
                return self._continue_nested_revision(
                    mission_id=mission_id,
                    current_graph=graph,
                    current_executions=executions,
                    revision_result=revision,
                    operator=operator,
                    memory_command_event_id=memory_command_event_id,
                    memory_plan_event_id=memory_plan_event_id,
                    prior_group_results=group_results,
                )

    def _dispatch_readiness_error(
        self,
        node: MissionTaskNode,
        presence_by_robot: dict[str, dict[str, Any] | str],
    ) -> str | None:
        cached = presence_by_robot.get(node.robot_id)
        if cached is None:
            entry = (
                self.registry.get(node.robot_id)
                if self.registry is not None
                else None
            )
            if entry is None or not entry.enabled:
                cached = "Robot is not registered and enabled."
            else:
                try:
                    presence = (
                        self.mission_agent.subagent_client.check_presence(
                            entry
                        )
                    )
                except Exception as exc:
                    cached = f"Robot presence check failed: {exc}"
                else:
                    cached = presence
            presence_by_robot[node.robot_id] = cached
        if isinstance(cached, str):
            return cached
        if cached.get("online") is not True or cached.get("stale") is True:
            return "Robot is offline or stale at dispatch time."
        state = cached.get("state")
        if isinstance(state, dict):
            emergency_stop = state.get("emergency_stop")
            if (
                isinstance(emergency_stop, dict)
                and emergency_stop.get("active") is True
            ):
                return "Robot emergency stop is active."
        return None

    def _evaluate_dispatch_beliefs(
        self,
        *,
        mission_id: str,
        plan_id: str,
        nodes: list[MissionTaskNode],
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        guarded = [node for node in nodes if node.belief_requirements]
        if not guarded:
            return {}, []
        try:
            snapshot = self.mission_agent.refresh_mission_state_snapshot(
                mission_id
            )
        except Exception as exc:
            observed_at = datetime.now(timezone.utc).isoformat()
            records: dict[str, dict[str, Any]] = {}
            blocked: list[dict[str, Any]] = []
            for node in guarded:
                record = {
                    "allowed": False,
                    "mission_id": mission_id,
                    "plan_id": plan_id,
                    "node_id": node.node_id,
                    "snapshot_id": None,
                    "failed_belief_ids": [
                        requirement.belief_id
                        for requirement in node.belief_requirements
                    ],
                    "checks": [],
                    "reason_code": "dispatch_snapshot_unavailable",
                    "error": str(exc),
                }
                records[node.node_id] = record
                blocked.append(
                    self._belief_gate_terminal(
                        mission_id=mission_id,
                        plan_id=plan_id,
                        node=node,
                        gate_record=record,
                        observed_at=observed_at,
                        snapshot_id="unavailable",
                    )
                )
            return records, blocked

        records = {}
        blocked = []
        for node in guarded:
            result = self.belief_gate.evaluate(
                mission_id=mission_id,
                plan_id=plan_id,
                node=node,
                snapshot=snapshot,
            )
            record = result.to_dict()
            records[node.node_id] = record
            if not result.allowed:
                blocked.append(
                    self._belief_gate_terminal(
                        mission_id=mission_id,
                        plan_id=plan_id,
                        node=node,
                        gate_record=record,
                        observed_at=snapshot.captured_at,
                        snapshot_id=snapshot.snapshot_id,
                    )
                )
        return records, blocked

    @staticmethod
    def _belief_gate_terminal(
        *,
        mission_id: str,
        plan_id: str,
        node: MissionTaskNode,
        gate_record: dict[str, Any],
        observed_at: str,
        snapshot_id: str,
    ) -> dict[str, Any]:
        event_key = (
            f"{mission_id}:{plan_id}:{node.node_id}:{snapshot_id}"
        )
        event = MissionExecutionEvent(
            event_id=f"belief-gate:{event_key}",
            mission_id=mission_id,
            event_type="belief_requirement_failed",
            evidence_id=f"belief-gate-evidence:{event_key}",
            source_type="safety_gate",
            observed_at=observed_at,
            robot_id=node.robot_id,
            node_id=node.node_id,
            details={
                "plan_id": plan_id,
                "belief_gate": gate_record,
            },
        )
        return {
            "status": "blocked",
            "robot_id": node.robot_id,
            "node_id": node.node_id,
            "message": (
                "Compiled world-state assumptions failed immediately before "
                "robot dispatch."
            ),
            "dispatch_belief_gate": gate_record,
            "invalidation_event": event.to_dict(),
        }

    def _continue_nested_revision(
        self,
        *,
        mission_id: str,
        current_graph: MissionTaskGraph,
        current_executions: dict[str, MissionNodeExecution],
        revision_result: dict[str, Any],
        operator: dict[str, Any] | None,
        memory_command_event_id: str | None,
        memory_plan_event_id: str | None,
        prior_group_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if revision_result.get("status") != "revised":
            return {
                "status": revision_result.get("status", "blocked"),
                "message": revision_result.get(
                    "message",
                    "Revised plan execution was stopped.",
                ),
                "group_results": prior_group_results,
                "plan_revision": revision_result,
            }
        continued = self._continue_revised_plan(
            mission_id=mission_id,
            current_graph=current_graph,
            current_executions=current_executions,
            revision_result=revision_result,
            operator=operator,
            memory_command_event_id=memory_command_event_id,
            memory_plan_event_id=memory_plan_event_id,
        )
        nested_groups = continued.pop("group_results", [])
        continued["group_results"] = [
            *prior_group_results,
            *nested_groups,
        ]
        continued["plan_revision"] = revision_result
        return continued

    def _terminal_revision(
        self,
        mission_id: str,
        terminal_states: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        revisions = self._coordinate_plan_invalidations(
            mission_id,
            terminal_states,
        )
        return next(
            (
                item for item in revisions
                if item.get("status")
                not in {"retained", "retry_allowed"}
            ),
            None,
        )

    def _recheck_completion_evidence(
        self,
        mission_id: str,
        executions: dict[str, MissionNodeExecution],
        attempts: list[_SubmittedAttempt],
        terminal_states: list[dict[str, Any]],
        nodes: dict[str, MissionTaskNode],
    ) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
        attempts_by_node = {
            attempt.node_id: attempt for attempt in attempts
        }
        recheck_attempts: list[_SubmittedAttempt] = []
        for terminal in terminal_states:
            recovery = terminal.get("recovery_decision")
            if (
                not isinstance(recovery, dict)
                or recovery.get("action") != "recheck"
            ):
                continue
            node_id = terminal.get("node_id")
            if (
                not isinstance(node_id, str)
                or node_id not in executions
                or node_id not in attempts_by_node
            ):
                continue
            previous = executions[node_id]
            node = nodes[node_id]
            executions[node_id] = MissionNodeExecution(
                plan_id=previous.plan_id,
                node_id=node_id,
                robot_id=previous.robot_id,
                task_id=previous.task_id,
                status="accepted",
                node_spec_hash=execution_spec_hash(node),
                updated_at=datetime.now(timezone.utc).isoformat(),
                completion_evidence=previous.completion_evidence,
                recovery_attempt=previous.recovery_attempt + 1,
                recovery_action="recheck",
            )
            recheck_attempts.append(attempts_by_node[node_id])
        if not recheck_attempts:
            return [], ()
        refreshed = self._poll_group_terminal(
            mission_id,
            recheck_attempts,
        )
        self._update_execution_states(
            mission_id,
            executions,
            recheck_attempts,
            refreshed,
            nodes,
        )
        return (
            refreshed,
            tuple(sorted({
                attempt.node_id for attempt in recheck_attempts
            })),
        )

    def _revised_execution_state(
        self,
        *,
        revised_graph: MissionTaskGraph,
        current_executions: dict[str, MissionNodeExecution],
        decision: RevisionDispatchDecision,
    ) -> dict[str, MissionNodeExecution]:
        carried = set(decision.carried_node_ids)
        preserved = set(decision.preserved_node_ids)
        now = datetime.now(timezone.utc).isoformat()
        result: dict[str, MissionNodeExecution] = {}
        for node in revised_graph.nodes:
            previous = current_executions.get(node.node_id)
            if node.node_id in carried and previous is not None:
                result[node.node_id] = MissionNodeExecution(
                    plan_id=revised_graph.plan_id,
                    node_id=node.node_id,
                    robot_id=previous.robot_id,
                    task_id=previous.task_id,
                    status="carried",
                    node_spec_hash=execution_spec_hash(node),
                    updated_at=now,
                    completion_evidence=previous.completion_evidence,
                    recovery_attempt=previous.recovery_attempt,
                    recovery_action=previous.recovery_action,
                )
            elif node.node_id in preserved and previous is not None:
                result[node.node_id] = MissionNodeExecution(
                    plan_id=revised_graph.plan_id,
                    node_id=node.node_id,
                    robot_id=previous.robot_id,
                    task_id=previous.task_id,
                    status=previous.status,
                    node_spec_hash=execution_spec_hash(node),
                    updated_at=now,
                    completion_evidence=previous.completion_evidence,
                    recovery_attempt=previous.recovery_attempt,
                    recovery_action=previous.recovery_action,
                )
            else:
                result[node.node_id] = MissionNodeExecution(
                    plan_id=revised_graph.plan_id,
                    node_id=node.node_id,
                    robot_id=node.robot_id,
                    status="pending",
                    node_spec_hash=execution_spec_hash(node),
                    updated_at=now,
                )
        return result

    def _cancel_superseded_executions(
        self,
        *,
        mission_id: str,
        decision: RevisionDispatchDecision,
        operator: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for execution in decision.cancel_executions:
            entry = (
                self.registry.get(execution.robot_id)
                if self.registry is not None
                else None
            )
            if entry is None or execution.task_id is None:
                results.append({
                    "status": "not_found",
                    "robot_id": execution.robot_id,
                    "task_id": execution.task_id,
                    "node_id": execution.node_id,
                })
                continue
            try:
                cancel_result = self.mission_agent.subagent_client.cancel_task(
                    entry,
                    execution.task_id,
                    operator=operator,
                )
            except Exception as exc:
                results.append({
                    "status": "error",
                    "robot_id": execution.robot_id,
                    "task_id": execution.task_id,
                    "node_id": execution.node_id,
                    "error": str(exc),
                })
                continue
            status = str(
                cancel_result.get("status") or "cancel_requested"
            )
            results.append({
                "status": status,
                "robot_id": execution.robot_id,
                "task_id": execution.task_id,
                "node_id": execution.node_id,
                "cancel_result": cancel_result,
            })
            mission_registry = self.mission_agent.mission_registry
            if mission_registry is not None:
                try:
                    mission_registry.update_subtask(
                        mission_id=mission_id,
                        robot_id=execution.robot_id,
                        task_id=execution.task_id,
                        status=status,
                        updated_at=datetime.now(timezone.utc).isoformat(),
                        result=cancel_result,
                    )
                except KeyError:
                    pass
        return results

    def _wait_for_superseded_terminal(
        self,
        *,
        mission_id: str,
        decision: RevisionDispatchDecision,
    ) -> list[dict[str, Any]] | None:
        expected = {
            (execution.robot_id, execution.task_id)
            for execution in decision.cancel_executions
            if execution.task_id is not None
        }
        if not expected:
            return []
        deadline = (
            time.monotonic()
            + self.config.revision_cancel_timeout_seconds
        )
        while time.monotonic() < deadline:
            try:
                trace = self.mission_agent.mission_trace(mission_id)
            except Exception:
                return None
            terminal = [
                item
                for item in trace.get("subtasks", [])
                if (
                    str(item.get("robot_id") or ""),
                    str(item.get("task_id") or ""),
                ) in expected
                and item.get("status") in TERMINAL_SUBTASK_STATUSES
            ]
            terminal_keys = {
                (
                    str(item.get("robot_id") or ""),
                    str(item.get("task_id") or ""),
                )
                for item in terminal
            }
            if terminal_keys == expected:
                return terminal
            time.sleep(self.config.poll_interval_seconds)
        return None

    def _persist_checkpoint(
        self,
        graph: MissionTaskGraph,
        executions: dict[str, MissionNodeExecution],
        *,
        fenced_task_ids: set[str] | tuple[str, ...] = (),
        superseded_executions: tuple[MissionNodeExecution, ...] = (),
        invalidation_event_id: str | None = None,
        memory_command_event_id: str | None = None,
        memory_plan_event_id: str | None = None,
    ) -> str | None:
        if self.dispatch_store is None:
            return None
        try:
            self.dispatch_store.append(
                checkpoint_for_graph(
                    graph,
                    executions.values(),
                    fenced_task_ids=fenced_task_ids,
                    superseded_executions=superseded_executions,
                    invalidation_event_id=invalidation_event_id,
                    memory_command_event_id=memory_command_event_id,
                    memory_plan_event_id=memory_plan_event_id,
                )
            )
        except (OSError, TypeError, ValueError) as exc:
            return (
                "Mission dispatch checkpoint could not be persisted: "
                f"{exc}"
            )
        return None

    @staticmethod
    def _subtask_from_node(
        node: MissionTaskNode,
        *,
        execution_group: int,
    ) -> MissionSubtask:
        return MissionSubtask(
            robot_id=node.robot_id,
            command=node.command,
            floor=node.target.floor,
            capability_required=node.capability_required,
            execution_group=execution_group,
            node_id=node.node_id,
            task_type=node.task_type,
            target=node.target.to_dict(),
            completion_goal=node.completion_goal,
            completion_contract={
                "task_type": node.task_type,
                "completion_goal": node.completion_goal,
                "success_evidence": [
                    evidence.to_dict()
                    for evidence in node.success_evidence
                ],
                "timeout_seconds": node.timeout_seconds,
                "recovery_policy": node.recovery_policy,
                "risk_level": node.risk_level,
            },
            belief_requirements=[
                requirement.to_dict()
                for requirement in node.belief_requirements
            ],
        )

    @staticmethod
    def _execution_from_attempt(
        attempt: _SubmittedAttempt,
        *,
        status: str,
        node: MissionTaskNode,
        completion_evidence: dict[str, Any] | None = None,
        dispatch_belief_gate: dict[str, Any] | None = None,
        recovery_attempt: int = 0,
        recovery_action: str | None = None,
    ) -> MissionNodeExecution:
        normalized_status = (
            status
            if status in VALID_NODE_RUNTIME_STATUSES
            else "accepted"
        )
        return MissionNodeExecution(
            plan_id=attempt.plan_id,
            node_id=attempt.node_id,
            robot_id=attempt.subtask.robot_id,
            task_id=attempt.task_id,
            status=normalized_status,
            node_spec_hash=execution_spec_hash(node),
            updated_at=datetime.now(timezone.utc).isoformat(),
            completion_evidence=completion_evidence,
            dispatch_belief_gate=dispatch_belief_gate,
            recovery_attempt=recovery_attempt,
            recovery_action=recovery_action,
        )

    def _update_execution_states(
        self,
        mission_id: str,
        executions: dict[str, MissionNodeExecution],
        attempts: list[_SubmittedAttempt],
        terminal_states: list[dict[str, Any]],
        nodes: dict[str, MissionTaskNode],
    ) -> None:
        attempts_by_key = {
            (attempt.subtask.robot_id, attempt.task_id): attempt
            for attempt in attempts
        }
        for terminal in terminal_states:
            key = (
                str(terminal.get("robot_id") or ""),
                str(terminal.get("task_id") or ""),
            )
            attempt = attempts_by_key.get(key)
            if attempt is None:
                continue
            terminal.setdefault("node_id", attempt.node_id)
            raw_status = terminal.get("status")
            status = (
                normalize_robot_task_terminal_status(raw_status)
                or (
                    str(raw_status)
                    if raw_status in ACTIVE_NODE_STATUSES
                    else "failed"
                )
            )
            terminal["status"] = status
            if status not in VALID_NODE_RUNTIME_STATUSES:
                status = "blocked"
            evidence_result = None
            recovery_decision = None
            previous = executions.get(attempt.node_id)
            recovery_attempt = (
                previous.recovery_attempt
                if previous is not None
                else 0
            )
            if status in SUCCEEDED_NODE_STATUSES:
                evidence_result = self.evidence_validator.validate(
                    requirements=nodes[attempt.node_id].success_evidence,
                    terminal_state=terminal,
                )
                terminal["completion_evidence_validation"] = (
                    evidence_result.to_dict()
                )
                if not evidence_result.satisfied:
                    terminal["reported_status"] = status
                    terminal["status"] = "blocked"
                    terminal["error"] = (
                        "Robot reported success without satisfying the "
                        "compiled completion contract."
                    )
                    status = "blocked"
                    recovery_decision = self.recovery_orchestrator.decide(
                        mission_id=mission_id,
                        node=nodes[attempt.node_id],
                        robot_id=attempt.subtask.robot_id,
                        task_id=attempt.task_id,
                        terminal_state=terminal,
                        validation=evidence_result,
                        recovery_attempt=recovery_attempt,
                    )
                    terminal["recovery_decision"] = (
                        recovery_decision.to_dict()
                    )
                    if recovery_decision.event is not None:
                        terminal["invalidation_event"] = (
                            recovery_decision.event.to_dict()
                        )
            executions[attempt.node_id] = self._execution_from_attempt(
                attempt,
                status=status,
                node=nodes[attempt.node_id],
                completion_evidence=(
                    evidence_result.to_dict()
                    if evidence_result is not None
                    else None
                ),
                dispatch_belief_gate=(
                    previous.dispatch_belief_gate
                    if previous is not None
                    else None
                ),
                recovery_attempt=recovery_attempt,
                recovery_action=(
                    recovery_decision.action
                    if recovery_decision is not None
                    else previous.recovery_action
                    if previous is not None
                    else None
                ),
            )

    @staticmethod
    def _attempt_from_execution(
        execution: MissionNodeExecution,
        nodes: dict[str, MissionTaskNode],
    ) -> _SubmittedAttempt | None:
        node = nodes.get(execution.node_id)
        if node is None or execution.task_id is None:
            return None
        subtask = MissionSubtask(
            robot_id=execution.robot_id,
            command=node.command,
            floor=node.target.floor,
            capability_required=node.capability_required,
            execution_group=0,
            node_id=node.node_id,
            task_type=node.task_type,
            target=node.target.to_dict(),
            completion_goal=node.completion_goal,
            completion_contract={
                "task_type": node.task_type,
                "completion_goal": node.completion_goal,
                "success_evidence": [
                    evidence.to_dict()
                    for evidence in node.success_evidence
                ],
                "timeout_seconds": node.timeout_seconds,
                "recovery_policy": node.recovery_policy,
                "risk_level": node.risk_level,
            },
            belief_requirements=[
                requirement.to_dict()
                for requirement in node.belief_requirements
            ],
        )
        return _SubmittedAttempt(
            logical_subtask_key=(
                f"{execution.plan_id}:{execution.node_id}"
            ),
            subtask=subtask,
            task_id=execution.task_id,
            node_id=execution.node_id,
            plan_id=execution.plan_id,
        )

    def _coordinate_plan_invalidations(
        self,
        mission_id: str,
        terminal_states: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for terminal in terminal_states:
            try:
                raw_event = _invalidation_event_payload(terminal)
                if raw_event is None:
                    continue
                event = MissionExecutionEvent.from_dict(raw_event)
            except (TypeError, ValueError) as exc:
                results.append({
                    "status": "blocked",
                    "message": f"Invalid mission invalidation event: {exc}",
                })
                break
            if event.mission_id != mission_id:
                results.append({
                    "status": "blocked",
                    "message": (
                        "Mission invalidation event does not belong to the "
                        "scheduled mission."
                    ),
                    "event": event.to_dict(),
                })
                break
            result = self.mission_agent.handle_execution_event(event)
            results.append(result)
            if result.get("status") not in {"retained", "retry_allowed"}:
                break
        return results

    def _evaluate_group_failures(
        self,
        group_terminal: list[dict[str, Any]],
        attempts: list[_SubmittedAttempt],
        plan: MissionPlan,
        retry_counts: dict[str, int],
        reassign_counts: dict[str, int],
        failure_decisions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Evaluate failed subtasks and return actions to take."""
        actions: list[dict[str, Any]] = []
        policy = self.config.failure_policy
        attempts_by_execution = {
            (attempt.subtask.robot_id, attempt.task_id): attempt
            for attempt in attempts
        }

        for terminal in group_terminal:
            status = terminal.get("status")
            if status not in _BAD_STATUSES:
                continue

            robot_id = terminal.get("robot_id", "")
            task_id = terminal.get("task_id", "")
            attempt = attempts_by_execution.get((robot_id, task_id))
            if attempt is None:
                continue
            logical_subtask_key = attempt.logical_subtask_key
            decision = policy.decision_for(status)
            recovery = terminal.get("recovery_decision")
            if isinstance(recovery, dict) and recovery.get("action") in {
                "retry",
                "reassign",
                "escalate",
                "abort",
            }:
                decision = str(recovery["action"])

            if decision == "abort":
                failure_decisions.append({
                    "robot_id": robot_id,
                    "task_id": task_id,
                    "status": status,
                    "decision": "abort",
                })
                return []  # Abort immediately

            elif decision == "retry":
                if retry_counts[logical_subtask_key] < policy.max_retries:
                    retry_counts[logical_subtask_key] += 1
                    failure_decisions.append({
                        "robot_id": robot_id,
                        "task_id": task_id,
                        "status": status,
                        "decision": "retry",
                        "attempt": retry_counts[logical_subtask_key],
                    })
                    actions.append({"action": "retry", "attempt": attempt})
                else:
                    # Max retries exceeded — skip
                    failure_decisions.append({
                        "robot_id": robot_id,
                        "task_id": task_id,
                        "status": status,
                        "decision": "skipped",
                        "reason": "max_retries_exceeded",
                    })

            elif decision == "reassign":
                if reassign_counts[logical_subtask_key] < policy.max_reassigns:
                    new_robot = self._find_reassign_robot(attempt.subtask, plan)
                    if new_robot:
                        reassign_counts[logical_subtask_key] += 1
                        failure_decisions.append({
                            "robot_id": robot_id,
                            "task_id": task_id,
                            "status": status,
                            "decision": "reassign",
                            "new_robot": new_robot,
                            "attempt": reassign_counts[logical_subtask_key],
                        })
                        actions.append({"action": "reassign", "attempt": attempt, "new_robot": new_robot})
                    else:
                        failure_decisions.append({
                            "robot_id": robot_id,
                            "task_id": task_id,
                            "status": status,
                            "decision": "skipped",
                            "reason": "no_alternative_robot",
                        })
                else:
                    failure_decisions.append({
                        "robot_id": robot_id,
                        "task_id": task_id,
                        "status": status,
                        "decision": "skipped",
                        "reason": "max_reassigns_exceeded",
                    })

            elif decision == "escalate":
                failure_decisions.append({
                    "robot_id": robot_id,
                    "task_id": task_id,
                    "status": status,
                    "decision": "escalated",
                })

            elif decision == "skip":
                failure_decisions.append({
                    "robot_id": robot_id,
                    "task_id": task_id,
                    "status": status,
                    "decision": "skipped",
                })

        return actions

    def _find_reassign_robot(self, subtask: MissionSubtask, plan: MissionPlan) -> str | None:
        """Find an alternative robot with matching capability that isn't already assigned."""
        if self.registry is None:
            return None
        assigned_robot_ids = {s.robot_id for s in plan.subtasks}
        for entry in self.registry.enabled_entries():
            if entry.robot_id == subtask.robot_id:
                continue
            if entry.robot_id in assigned_robot_ids:
                continue
            if subtask.capability_required in entry.capabilities:
                return entry.robot_id
        return None

    def _mark_mission_subtasks_terminal(
        self,
        mission_id: str,
        states: list[dict[str, Any]],
    ) -> None:
        registry = getattr(self.mission_agent, "mission_registry", None)
        if registry is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        for state in states:
            robot_id = state.get("robot_id")
            task_id = state.get("task_id")
            if not isinstance(robot_id, str) or not isinstance(task_id, str):
                continue
            try:
                registry.update_subtask(
                    mission_id=mission_id,
                    robot_id=robot_id,
                    task_id=task_id,
                    status=str(state.get("status") or "timed_out"),
                    updated_at=now,
                    result=dict(state),
                    error=(
                        str(state["error"])
                        if isinstance(state.get("error"), str)
                        else None
                    ),
                )
            except (KeyError, OSError, ValueError):
                continue

    def _poll_group_terminal(
        self,
        mission_id: str,
        attempts: list[_SubmittedAttempt],
        *,
        run_control: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Poll until the concrete executions from this dispatch round terminate."""
        if not attempts:
            return []
        deadline = time.monotonic() + self.config.group_timeout_seconds
        execution_keys = {
            (attempt.subtask.robot_id, attempt.task_id)
            for attempt in attempts
        }

        while time.monotonic() < deadline:
            if _run_control_cancelled(run_control):
                try:
                    trace = self.mission_agent.mission_trace(mission_id)
                except Exception as exc:
                    return _lost_attempt_states(attempts, exc)
                observed = [
                    s for s in trace.get("subtasks", [])
                    if (s.get("robot_id"), s.get("task_id")) in execution_keys
                ]
                return _cancelled_attempt_states(attempts, observed)
            try:
                trace = self.mission_agent.mission_trace(mission_id)
            except Exception as exc:
                return _lost_attempt_states(attempts, exc)
            observed = [
                s for s in trace.get("subtasks", [])
                if (s.get("robot_id"), s.get("task_id")) in execution_keys
            ]
            terminal = [
                s for s in observed
                if s.get("status") in TERMINAL_SUBTASK_STATUSES
            ]
            if any(
                _contains_invalidation_event(item)
                for item in observed
            ):
                return observed
            if len(terminal) >= len(execution_keys):
                return terminal
            time.sleep(self.config.poll_interval_seconds)

        # Timeout — return whatever state we have
        try:
            trace = self.mission_agent.mission_trace(mission_id)
        except Exception as exc:
            return _lost_attempt_states(attempts, exc)
        return [
            s for s in trace.get("subtasks", [])
            if (s.get("robot_id"), s.get("task_id")) in execution_keys
        ]

    @staticmethod
    def _submitted_attempt(
        *,
        result: dict[str, Any],
        logical_subtask_key: str,
        subtask: MissionSubtask,
        node_id: str,
        plan_id: str,
    ) -> _SubmittedAttempt | None:
        task_id = result.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return None
        return _SubmittedAttempt(
            logical_subtask_key=logical_subtask_key,
            subtask=subtask,
            task_id=task_id,
            node_id=node_id,
            plan_id=plan_id,
        )


def _contains_invalidation_event(terminal: dict[str, Any]) -> bool:
    if "invalidation_event" in terminal:
        return True
    robot_trace = terminal.get("robot_trace")
    if not isinstance(robot_trace, dict):
        return False
    if "invalidation_event" in robot_trace:
        return True
    events = robot_trace.get("events")
    return isinstance(events, list) and any(
        isinstance(item, dict)
        and item.get("type") == "mission.plan_invalidated"
        for item in events
    )


def _lost_attempt_states(
    attempts: list[_SubmittedAttempt],
    error: Exception,
) -> list[dict[str, Any]]:
    return [
        {
            "robot_id": attempt.subtask.robot_id,
            "task_id": attempt.task_id,
            "node_id": attempt.node_id,
            "status": "lost",
            "error": (
                "Robot task trace became unavailable: "
                f"{type(error).__name__}: {error}"
            ),
        }
        for attempt in attempts
    ]


def _run_control_cancelled(run_control: Any | None) -> bool:
    checker = getattr(run_control, "is_cancel_requested", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:
        return True


def _wait_for_run_control(run_control: Any | None) -> bool:
    if run_control is None:
        return True
    waiter = getattr(run_control, "wait_until_resumed", None)
    if callable(waiter):
        try:
            return bool(waiter())
        except Exception:
            return False
    return not _run_control_cancelled(run_control)


def _cancelled_attempt_states(
    attempts: list[_SubmittedAttempt],
    observed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_key = {
        (str(item.get("robot_id") or ""), str(item.get("task_id") or "")): item
        for item in observed
    }
    result: list[dict[str, Any]] = []
    for attempt in attempts:
        key = (attempt.subtask.robot_id, attempt.task_id)
        item = dict(by_key.get(key, {}))
        if item.get("status") not in TERMINAL_SUBTASK_STATUSES:
            item.update(
                {
                    "robot_id": attempt.subtask.robot_id,
                    "task_id": attempt.task_id,
                    "node_id": attempt.node_id,
                    "status": "cancelled",
                    "error": "Mission Run cancelled by operator request.",
                }
            )
        result.append(item)
    return result


def _has_nonterminal_states(
    states: list[dict[str, Any]],
    *,
    expected_count: int,
) -> bool:
    # An invalidation observation intentionally returns active sibling nodes
    # so the revision dispatcher can fence them before replanning.  Those
    # active siblings are not a timeout.
    if any(_contains_invalidation_event(item) for item in states):
        return False
    return len(states) < expected_count or any(
        item.get("status") not in TERMINAL_SUBTASK_STATUSES
        for item in states
    )


def _timeout_attempt_states(
    attempts: list[_SubmittedAttempt],
    observed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_key = {
        (str(item.get("robot_id") or ""), str(item.get("task_id") or "")): item
        for item in observed
    }
    result: list[dict[str, Any]] = []
    for attempt in attempts:
        key = (attempt.subtask.robot_id, attempt.task_id)
        item = dict(by_key.get(key, {}))
        if item.get("status") not in TERMINAL_SUBTASK_STATUSES:
            item.update(
                {
                    "robot_id": attempt.subtask.robot_id,
                    "task_id": attempt.task_id,
                    "node_id": attempt.node_id,
                    "status": "timed_out",
                    "error": "Robot task did not reach a terminal state before the group timeout.",
                }
            )
        result.append(item)
    return result


def _recovery_trace_identity_error(
    trace: dict[str, Any],
    execution: MissionNodeExecution,
) -> str | None:
    if not isinstance(trace, dict):
        return (
            f"Robot task {execution.task_id!r} returned a non-object trace."
        )
    trace_task_id = trace.get("task_id")
    if (
        isinstance(trace_task_id, str)
        and trace_task_id
        and trace_task_id != execution.task_id
    ):
        return (
            f"Robot task trace identity mismatch for {execution.task_id!r}."
        )
    trace_robot_id = trace.get("robot_id")
    if (
        isinstance(trace_robot_id, str)
        and trace_robot_id
        and trace_robot_id != execution.robot_id
    ):
        return (
            f"Robot task trace robot mismatch for {execution.task_id!r}."
        )
    return None


def _recovery_status_from_trace(
    trace: dict[str, Any],
) -> str | None:
    runtime_status = robot_task_status_from_trace(trace)
    if runtime_status is not None:
        return runtime_status

    # Scheduler-only persisted states never come from a Robot Gateway but may
    # appear in an older checkpoint during restart reconciliation.
    for value in (
        trace.get("status"),
        (
            trace.get("queue_record", {}).get("status")
            if isinstance(trace.get("queue_record"), dict)
            else None
        ),
    ):
        if value in VALID_NODE_RUNTIME_STATUSES:
            return str(value)
    return None


def _invalidation_event_payload(
    terminal: dict[str, Any],
) -> dict[str, Any] | None:
    if "invalidation_event" in terminal:
        value = terminal["invalidation_event"]
        if not isinstance(value, dict):
            raise ValueError("invalidation_event must be an object")
        return value
    robot_trace = terminal.get("robot_trace")
    if not isinstance(robot_trace, dict):
        return None
    if "invalidation_event" in robot_trace:
        value = robot_trace["invalidation_event"]
        if not isinstance(value, dict):
            raise ValueError("robot_trace.invalidation_event must be an object")
        return value
    events = robot_trace.get("events")
    if not isinstance(events, list):
        return None
    for item in reversed(events):
        if not isinstance(item, dict) or item.get("type") != "mission.plan_invalidated":
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict):
            raise ValueError(
                "mission.plan_invalidated payload must be an object"
            )
        value = payload.get("invalidation_event")
        if not isinstance(value, dict):
            raise ValueError(
                "mission.plan_invalidated requires invalidation_event"
            )
        return value
    return None
