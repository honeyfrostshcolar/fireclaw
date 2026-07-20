from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_planner import MissionPlan, MissionSubtask
from fireclaw_core.mission.mission_registry import TERMINAL_SUBTASK_STATUSES
from fireclaw_core.agent.robot_registry import RobotRegistry


# Failure status categories
_BAD_STATUSES = {"failed", "block", "denied", "lost"}


@dataclass(frozen=True)
class MissionFailurePolicy:
    """Per-subtask failure decisions.

    Decision values: "retry", "reassign", "skip", "escalate", "abort".
    """
    on_failed: str = "reassign"
    on_denied: str = "abort"
    on_lost: str = "abort"
    on_block: str = "escalate"
    max_retries: int = 1
    max_reassigns: int = 1

    def decision_for(self, status: str) -> str:
        """Return the decision for a given terminal failure status."""
        mapping = {
            "failed": self.on_failed,
            "denied": self.on_denied,
            "lost": self.on_lost,
            "block": self.on_block,
        }
        return mapping.get(status, "abort")


@dataclass(frozen=True)
class MissionSchedulerConfig:
    failure_policy: MissionFailurePolicy = field(default_factory=MissionFailurePolicy)
    poll_interval_seconds: float = 0.1
    group_timeout_seconds: float = 300.0


@dataclass(frozen=True)
class _SubmittedAttempt:
    logical_subtask_key: str
    subtask: MissionSubtask
    task_id: str


@dataclass
class MissionScheduler:
    mission_agent: MissionAgent
    registry: RobotRegistry | None = None
    config: MissionSchedulerConfig = field(default_factory=MissionSchedulerConfig)

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = self.mission_agent.registry

    def schedule(
        self,
        plan: MissionPlan,
        *,
        mission_id: str,
        session_id: str | None = None,
        operator: dict[str, Any] | None = None,
        memory_command_event_id: str | None = None,
        memory_plan_event_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a mission plan by scheduling execution groups in order."""
        groups: dict[int, list[MissionSubtask]] = {}
        for subtask in plan.subtasks:
            groups.setdefault(subtask.execution_group, []).append(subtask)

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
            group_subtasks = groups[group_index]
            subtask_results: list[dict[str, Any]] = []
            current_attempts: list[_SubmittedAttempt] = []
            terminal_states: list[dict[str, Any]] = []
            for subtask_index, subtask in enumerate(group_subtasks):
                result = self.mission_agent.submit_subtask(
                    subtask.robot_id,
                    subtask.command,
                    session_id=mission_id,
                    dedupe_key=f"{mission_id}-{subtask.robot_id}-{subtask.floor}",
                    operator=operator,
                    mission={"mission_id": mission_id, "execution_group": subtask.execution_group},
                    mission_subtask=subtask,
                    **memory_lineage_kwargs,
                )
                subtask_results.append(result)
                attempt = self._submitted_attempt(
                    result=result,
                    logical_subtask_key=f"{group_index}:{subtask_index}",
                    subtask=subtask,
                )
                if attempt is not None:
                    current_attempts.append(attempt)

            # Poll + failure handling loop for this group
            group_terminal = self._poll_group_terminal(mission_id, current_attempts)
            terminal_states.extend(group_terminal)

            # Evaluate failures and execute decisions
            actions = self._evaluate_group_failures(
                group_terminal, current_attempts, plan,
                retry_counts, reassign_counts, failure_decisions,
            )

            # Execute retry/reassign actions
            while actions:
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
                            **memory_lineage_kwargs,
                        )
                        subtask_results.append(result)
                        attempt = self._submitted_attempt(
                            result=result,
                            logical_subtask_key=previous_attempt.logical_subtask_key,
                            subtask=subtask,
                        )
                        if attempt is not None:
                            next_attempts.append(attempt)
                    elif action["action"] == "reassign":
                        subtask = previous_attempt.subtask
                        new_robot = action["new_robot"]
                        reassigned_subtask = MissionSubtask(
                            robot_id=new_robot,
                            command=subtask.command,
                            floor=subtask.floor,
                            capability_required=subtask.capability_required,
                            execution_group=subtask.execution_group,
                        )
                        result = self.mission_agent.submit_subtask(
                            new_robot,
                            subtask.command,
                            session_id=mission_id,
                            dedupe_key=f"{mission_id}-{new_robot}-{subtask.floor}-reassign{reassign_counts[previous_attempt.logical_subtask_key]}",
                            operator=operator,
                            mission={"mission_id": mission_id, "execution_group": subtask.execution_group},
                            mission_subtask=reassigned_subtask,
                            **memory_lineage_kwargs,
                        )
                        subtask_results.append(result)
                        attempt = self._submitted_attempt(
                            result=result,
                            logical_subtask_key=previous_attempt.logical_subtask_key,
                            subtask=reassigned_subtask,
                        )
                        if attempt is not None:
                            next_attempts.append(attempt)

                # Poll again for the retried/reassigned subtasks
                current_attempts = next_attempts
                group_terminal = self._poll_group_terminal(mission_id, current_attempts)
                terminal_states.extend(group_terminal)
                actions = self._evaluate_group_failures(
                    group_terminal, current_attempts, plan,
                    retry_counts, reassign_counts, failure_decisions,
                )

            group_result: dict[str, Any] = {
                "group_index": group_index,
                "subtask_results": subtask_results,
                "terminal_states": terminal_states,
            }
            group_results.append(group_result)

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

    def _poll_group_terminal(
        self,
        mission_id: str,
        attempts: list[_SubmittedAttempt],
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
            trace = self.mission_agent.mission_trace(mission_id)
            terminal = [
                s for s in trace.get("subtasks", [])
                if (s.get("robot_id"), s.get("task_id")) in execution_keys
                and s.get("status") in TERMINAL_SUBTASK_STATUSES
            ]
            if len(terminal) >= len(execution_keys):
                return terminal
            time.sleep(self.config.poll_interval_seconds)

        # Timeout — return whatever state we have
        trace = self.mission_agent.mission_trace(mission_id)
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
    ) -> _SubmittedAttempt | None:
        task_id = result.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return None
        return _SubmittedAttempt(
            logical_subtask_key=logical_subtask_key,
            subtask=subtask,
            task_id=task_id,
        )
