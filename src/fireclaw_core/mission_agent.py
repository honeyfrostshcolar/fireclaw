from __future__ import annotations

from typing import Any, Protocol
from uuid import uuid4
from datetime import datetime, timezone

from fireclaw_core.mission_registry import JsonlMissionRegistry
from fireclaw_core.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.subagent_client import RobotSubagentClient


class SubagentClient(Protocol):
    def submit_task(self, entry: RobotRegistryEntry, **kwargs: Any) -> dict[str, Any]:
        ...

    def get_task_trace(self, entry: RobotRegistryEntry, task_id: str) -> dict[str, Any]:
        ...


class MissionAgent:
    def __init__(
        self,
        *,
        registry: RobotRegistry,
        subagent_client: SubagentClient | None = None,
        mission_registry: JsonlMissionRegistry | None = None,
    ) -> None:
        self.registry = registry
        self.subagent_client = subagent_client or RobotSubagentClient()
        self.mission_registry = mission_registry

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
