from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from fireclaw_core.mission_agent import MissionAgent
from fireclaw_core.mission_planner import MissionPlan, MissionSubtask
from fireclaw_core.mission_registry import TERMINAL_SUBTASK_STATUSES


@dataclass(frozen=True)
class MissionSchedulerConfig:
    failure_policy: str = "stop"  # "stop" or "continue"
    poll_interval_seconds: float = 0.1
    group_timeout_seconds: float = 300.0


@dataclass
class MissionScheduler:
    mission_agent: MissionAgent
    config: MissionSchedulerConfig = field(default_factory=MissionSchedulerConfig)

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
                )
                subtask_results.append(result)

            group_terminal = self._poll_group_terminal(mission_id, group_subtasks)

            group_result: dict[str, Any] = {
                "group_index": group_index,
                "subtask_results": subtask_results,
                "terminal_states": group_terminal,
            }
            group_results.append(group_result)

            if self.config.failure_policy == "stop":
                bad_statuses = {"failed", "block", "denied", "lost"}
                if any(s.get("status") in bad_statuses for s in group_terminal):
                    return {
                        "status": "stopped",
                        "message": f"Group {group_index} had failures, stopping.",
                        "mission_id": mission_id,
                        "group_results": group_results,
                    }

        return {
            "status": "succeeded",
            "mission_id": mission_id,
            "group_results": group_results,
        }

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
