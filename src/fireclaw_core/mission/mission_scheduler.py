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

        for group_index in sorted_group_indices:
            group_subtasks = groups[group_index]
            subtask_results: list[dict[str, Any]] = []
            for subtask in group_subtasks:
                result = self.mission_agent.submit_subtask(
                    subtask.robot_id,
                    subtask.command,
                    session_id=mission_id,
                    dedupe_key=f"{mission_id}-{subtask.robot_id}-{subtask.floor}",
                    operator=operator,
                    mission={"mission_id": mission_id, "execution_group": subtask.execution_group},
                    mission_subtask=subtask,
                )
                subtask_results.append(result)

            # Poll + failure handling loop for this group
            group_terminal = self._poll_group_terminal(mission_id, group_subtasks)

            # Evaluate failures and execute decisions
            actions = self._evaluate_group_failures(
                group_terminal, group_subtasks, plan,
                retry_counts, reassign_counts, failure_decisions,
            )

            # Execute retry/reassign actions
            while actions:
                for action in actions:
                    if action["action"] == "retry":
                        subtask = action["subtask"]
                        result = self.mission_agent.submit_subtask(
                            subtask.robot_id,
                            subtask.command,
                            session_id=mission_id,
                            dedupe_key=f"{mission_id}-{subtask.robot_id}-{subtask.floor}-retry{retry_counts[subtask.robot_id]}",
                            operator=operator,
                            mission={"mission_id": mission_id, "execution_group": subtask.execution_group},
                            mission_subtask=subtask,
                        )
                        subtask_results.append(result)
                    elif action["action"] == "reassign":
                        subtask = action["subtask"]
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
                            dedupe_key=f"{mission_id}-{new_robot}-{subtask.floor}-reassign{reassign_counts[subtask.robot_id]}",
                            operator=operator,
                            mission={"mission_id": mission_id, "execution_group": subtask.execution_group},
                            mission_subtask=reassigned_subtask,
                        )
                        subtask_results.append(result)

                # Poll again for the retried/reassigned subtasks
                group_terminal = self._poll_group_terminal(mission_id, group_subtasks)
                actions = self._evaluate_group_failures(
                    group_terminal, group_subtasks, plan,
                    retry_counts, reassign_counts, failure_decisions,
                )

            group_result: dict[str, Any] = {
                "group_index": group_index,
                "subtask_results": subtask_results,
                "terminal_states": group_terminal,
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
        group_subtasks: list[MissionSubtask],
        plan: MissionPlan,
        retry_counts: dict[str, int],
        reassign_counts: dict[str, int],
        failure_decisions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Evaluate failed subtasks and return actions to take."""
        actions: list[dict[str, Any]] = []
        policy = self.config.failure_policy
        subtask_by_robot = {s.robot_id: s for s in group_subtasks}

        for terminal in group_terminal:
            status = terminal.get("status")
            if status not in _BAD_STATUSES:
                continue

            robot_id = terminal.get("robot_id", "")
            decision = policy.decision_for(status)

            if decision == "abort":
                failure_decisions.append({
                    "robot_id": robot_id,
                    "status": status,
                    "decision": "abort",
                })
                return []  # Abort immediately

            elif decision == "retry":
                if retry_counts[robot_id] < policy.max_retries:
                    retry_counts[robot_id] += 1
                    subtask = subtask_by_robot.get(robot_id)
                    if subtask:
                        failure_decisions.append({
                            "robot_id": robot_id,
                            "status": status,
                            "decision": "retry",
                            "attempt": retry_counts[robot_id],
                        })
                        actions.append({"action": "retry", "subtask": subtask})
                else:
                    # Max retries exceeded — skip
                    failure_decisions.append({
                        "robot_id": robot_id,
                        "status": status,
                        "decision": "skipped",
                        "reason": "max_retries_exceeded",
                    })

            elif decision == "reassign":
                if reassign_counts[robot_id] < policy.max_reassigns:
                    subtask = subtask_by_robot.get(robot_id)
                    if subtask:
                        new_robot = self._find_reassign_robot(subtask, plan)
                        if new_robot:
                            reassign_counts[robot_id] += 1
                            failure_decisions.append({
                                "robot_id": robot_id,
                                "status": status,
                                "decision": "reassign",
                                "new_robot": new_robot,
                                "attempt": reassign_counts[robot_id],
                            })
                            actions.append({"action": "reassign", "subtask": subtask, "new_robot": new_robot})
                        else:
                            failure_decisions.append({
                                "robot_id": robot_id,
                                "status": status,
                                "decision": "skipped",
                                "reason": "no_alternative_robot",
                            })
                else:
                    failure_decisions.append({
                        "robot_id": robot_id,
                        "status": status,
                        "decision": "skipped",
                        "reason": "max_reassigns_exceeded",
                    })

            elif decision == "escalate":
                failure_decisions.append({
                    "robot_id": robot_id,
                    "status": status,
                    "decision": "escalated",
                })

            elif decision == "skip":
                failure_decisions.append({
                    "robot_id": robot_id,
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
        group_subtasks: list[MissionSubtask],
    ) -> list[dict[str, Any]]:
        """Poll until all subtasks in group reach terminal state or timeout."""
        deadline = time.monotonic() + self.config.group_timeout_seconds
        robot_ids = {s.robot_id for s in group_subtasks}

        while time.monotonic() < deadline:
            trace = self.mission_agent.mission_trace(mission_id)
            terminal = [
                s for s in trace.get("subtasks", [])
                if s.get("robot_id") in robot_ids and s.get("status") in TERMINAL_SUBTASK_STATUSES
            ]
            if len(terminal) >= len(group_subtasks):
                return terminal
            time.sleep(self.config.poll_interval_seconds)

        # Timeout — return whatever state we have
        trace = self.mission_agent.mission_trace(mission_id)
        return [s for s in trace.get("subtasks", []) if s.get("robot_id") in robot_ids]
