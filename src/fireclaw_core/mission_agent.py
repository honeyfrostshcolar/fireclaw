from __future__ import annotations

from typing import Any, Protocol
from uuid import uuid4
from datetime import datetime, timezone

from fireclaw_core.control import ControlPolicy, OperatorContext
from fireclaw_core.mission_planner import MissionPlannerContext, MissionPlanningResult
from fireclaw_core.mission_registry import JsonlMissionRegistry
from fireclaw_core.mission_registry import TERMINAL_SUBTASK_STATUSES
from fireclaw_core.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.subagent_client import RobotSubagentClient


class SubagentClient(Protocol):
    def submit_task(self, entry: RobotRegistryEntry, **kwargs: Any) -> dict[str, Any]:
        ...

    def get_task_trace(self, entry: RobotRegistryEntry, task_id: str) -> dict[str, Any]:
        ...

    def cancel_task(
        self,
        entry: RobotRegistryEntry,
        task_id: str,
        *,
        operator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def check_presence(self, entry: RobotRegistryEntry) -> dict[str, Any]:
        ...


class MissionAgent:
    def __init__(
        self,
        *,
        registry: RobotRegistry,
        subagent_client: SubagentClient | None = None,
        mission_registry: JsonlMissionRegistry | None = None,
        planner: Any | None = None,
        control_policy: ControlPolicy | None = None,
        operator: OperatorContext | None = None,
    ) -> None:
        self.registry = registry
        self.subagent_client = subagent_client or RobotSubagentClient()
        self.mission_registry = mission_registry
        self.planner = planner
        self.control_policy = control_policy
        self.operator = operator

    def _authorize(self, action: str) -> dict[str, Any] | None:
        """Check mission-level authorization. Returns deny dict if denied, None if allowed."""
        if self.control_policy is None or self.operator is None:
            return None
        decision = self.control_policy.evaluate(self.operator, action)
        if decision.status == "deny":
            return {
                "status": "denied",
                "message": f"Operator {self.operator.operator_id} lacks required scope: {action}",
                "decision": decision.to_dict(),
            }
        return None

    def check_fleet_presence(self) -> dict[str, dict[str, Any]]:
        """Check presence of all enabled robots. Updates registry with last_seen_at."""
        results = {}
        for entry in self.registry.enabled_entries():
            result = self.subagent_client.check_presence(entry)
            results[entry.robot_id] = result
            if result["online"]:
                self.registry.update_presence(entry.robot_id, result["last_seen_at"])
        return results

    def submit_subtask(
        self,
        robot_id: str,
        command: str,
        *,
        session_id: str | None = None,
        dedupe_key: str | None = None,
        operator: dict[str, Any] | None = None,
        mission: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        deny = self._authorize("mission.submit")
        if deny is not None:
            return {**deny, "robot_id": robot_id, "subtasks": []}
        entry = self.registry.get(robot_id)
        if entry is None:
            return {
                "status": "not_found",
                "robot_id": robot_id,
                "message": "Robot subagent is not registered.",
                "subtasks": [],
            }
        if not entry.enabled:
            return {
                "status": "disabled",
                "robot_id": robot_id,
                "message": "Robot subagent is disabled.",
                "subtasks": [],
            }
        mission_id = _mission_id(session_id)
        created_at = datetime.now(timezone.utc).isoformat()
        if self.mission_registry is not None and self.mission_registry.get_mission(mission_id) is None:
            self.mission_registry.create_mission(
                mission_id=mission_id,
                session_id=session_id,
                command=command,
                created_at=created_at,
            )
        subagent_result = self.subagent_client.submit_task(
            entry,
            command=command,
            session_id=session_id,
            dedupe_key=dedupe_key,
            operator=operator,
            mission=mission or {"mission_id": mission_id},
        )
        task_id = subagent_result.get("task_id")
        status = str(subagent_result.get("status") or "unknown")
        if self.mission_registry is not None and isinstance(task_id, str):
            self.mission_registry.record_subtask(
                mission_id=mission_id,
                robot_id=robot_id,
                task_id=task_id,
                command=command,
                status=status,
                created_at=created_at,
            )
        subtask = {
            "robot_id": robot_id,
            "task_id": task_id,
            "status": status,
            "command": command,
        }
        return {
            "status": status,
            "mission_id": mission_id,
            "robot_id": robot_id,
            "task_id": task_id,
            "subtasks": [subtask],
            "subagent_result": subagent_result,
        }

    def mission_trace(self, mission_id: str) -> dict[str, Any]:
        deny = self._authorize("mission.read")
        if deny is not None:
            return {**deny, "mission_id": mission_id, "subtasks": []}
        if self.mission_registry is None:
            return {"mission_id": mission_id, "status": "not_configured", "subtasks": []}
        mission = self.mission_registry.get_mission(mission_id)
        if mission is None:
            return {"mission_id": mission_id, "status": "not_found", "subtasks": []}
        robot_traces: dict[tuple[str, str], dict[str, Any]] = {}
        for subtask in mission.subtasks:
            entry = self.registry.get(subtask.robot_id)
            if entry is None:
                continue
            robot_trace = self.subagent_client.get_task_trace(entry, subtask.task_id)
            robot_traces[(subtask.robot_id, subtask.task_id)] = robot_trace
            status = _status_from_robot_trace(robot_trace)
            if status is not None and status != subtask.status:
                self.mission_registry.update_subtask(
                    mission_id=mission_id,
                    robot_id=subtask.robot_id,
                    task_id=subtask.task_id,
                    status=status,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                    result=robot_trace.get("result") if isinstance(robot_trace.get("result"), dict) else None,
                )
        trace = self.mission_registry.mission_trace(mission_id)
        enriched_subtasks = []
        for subtask in trace.get("subtasks", []):
            if not isinstance(subtask, dict):
                continue
            key = (str(subtask.get("robot_id") or ""), str(subtask.get("task_id") or ""))
            enriched = dict(subtask)
            if key in robot_traces:
                enriched["robot_trace"] = robot_traces[key]
            enriched_subtasks.append(enriched)
        trace["subtasks"] = enriched_subtasks
        return trace

    def plan_and_submit(
        self,
        command: str,
        *,
        session_id: str | None = None,
        operator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        deny = self._authorize("mission.plan")
        if deny is not None:
            return {**deny, "subtask_results": []}
        if self.planner is None:
            return {
                "status": "no_planner",
                "message": "No mission planner configured.",
                "subtask_results": [],
            }
        # Check fleet presence before planning
        presence = self.check_fleet_presence()
        online_robot_ids = {rid for rid, info in presence.items() if info.get("online")}
        context = MissionPlannerContext(
            available_robots=[e for e in self.registry.enabled_entries() if e.robot_id in online_robot_ids],
        )
        planning_result = self.planner.plan(command, context=context)
        if planning_result.status != "planned" or planning_result.plan is None:
            return {
                "status": planning_result.status,
                "message": planning_result.message,
                "subtask_results": [],
            }
        mission_id = _mission_id(session_id)
        created_at = datetime.now(timezone.utc).isoformat()
        if self.mission_registry is not None and self.mission_registry.get_mission(mission_id) is None:
            self.mission_registry.create_mission(
                mission_id=mission_id,
                session_id=session_id,
                command=command,
                created_at=created_at,
            )
        subtask_results: list[dict[str, Any]] = []
        for subtask in planning_result.plan.subtasks:
            # Skip offline robots
            if subtask.robot_id not in online_robot_ids:
                continue
            result = self.submit_subtask(
                subtask.robot_id,
                subtask.command,
                session_id=mission_id,
                dedupe_key=f"{mission_id}-{subtask.robot_id}-{subtask.floor}",
                operator=operator,
                mission={"mission_id": mission_id, "execution_group": subtask.execution_group},
            )
            subtask_results.append(result)
        return {
            "status": planning_result.status,
            "message": planning_result.message,
            "mission_id": mission_id,
            "intent": planning_result.intent,
            "plan": planning_result.plan.to_dict(),
            "subtask_results": subtask_results,
        }

    def cancel_mission(
        self,
        mission_id: str,
        *,
        operator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        deny = self._authorize("mission.cancel")
        if deny is not None:
            return {**deny, "mission_id": mission_id, "subtasks": []}
        if self.mission_registry is None:
            return {"mission_id": mission_id, "status": "not_configured", "subtasks": []}
        mission = self.mission_registry.get_mission(mission_id)
        if mission is None:
            return {"mission_id": mission_id, "status": "not_found", "subtasks": []}
        cancelled_subtasks = []
        skipped_subtasks = []
        now = datetime.now(timezone.utc).isoformat()
        for subtask in mission.subtasks:
            if subtask.status in TERMINAL_SUBTASK_STATUSES:
                skipped_subtasks.append(
                    {
                        "robot_id": subtask.robot_id,
                        "task_id": subtask.task_id,
                        "status": subtask.status,
                    }
                )
                continue
            entry = self.registry.get(subtask.robot_id)
            if entry is None:
                skipped_subtasks.append(
                    {
                        "robot_id": subtask.robot_id,
                        "task_id": subtask.task_id,
                        "status": "not_found",
                    }
                )
                continue
            cancel_result = self.subagent_client.cancel_task(entry, subtask.task_id, operator=operator)
            status = str(cancel_result.get("status") or "cancel_requested")
            self.mission_registry.update_subtask(
                mission_id=mission_id,
                robot_id=subtask.robot_id,
                task_id=subtask.task_id,
                status=status,
                updated_at=now,
                result=cancel_result,
            )
            cancelled_subtasks.append(
                {
                    "robot_id": subtask.robot_id,
                    "task_id": subtask.task_id,
                    "status": status,
                    "cancel_result": cancel_result,
                }
            )
        if cancelled_subtasks:
            status = "cancel_requested"
        elif skipped_subtasks:
            status = "already_terminal"
        else:
            status = "empty"
        return {
            "mission_id": mission_id,
            "status": status,
            "cancelled_subtask_count": len(cancelled_subtasks),
            "skipped_subtask_count": len(skipped_subtasks),
            "subtasks": cancelled_subtasks,
            "skipped_subtasks": skipped_subtasks,
        }


def _mission_id(session_id: str | None) -> str:
    if isinstance(session_id, str) and session_id.strip():
        return session_id.strip()
    return f"mission-{uuid4().hex}"


def _status_from_robot_trace(trace: dict[str, Any]) -> str | None:
    result = trace.get("result")
    if isinstance(result, dict):
        status = result.get("status")
        if isinstance(status, str) and status:
            return status
    status = trace.get("status")
    if isinstance(status, str) and status not in {"unknown", "running", "cancel_requested"}:
        return status
    return None
