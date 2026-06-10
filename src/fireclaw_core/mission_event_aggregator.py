from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fireclaw_core.mission_agent import SubagentClient
from fireclaw_core.mission_registry import JsonlMissionRegistry
from fireclaw_core.robot_registry import RobotRegistry
from fireclaw_core.task_flow_registry import JsonlTaskFlowRegistryStore, TaskFlowRecord


class MissionEventAggregator:
    """Collects events from multiple robot subagents and merges them
    into a unified mission-level timeline."""

    def __init__(
        self,
        *,
        registry: RobotRegistry,
        subagent_client: SubagentClient,
        mission_registry: JsonlMissionRegistry,
        subagent_registry: Any | None = None,
        task_registry: Any | None = None,
        task_flow_store: JsonlTaskFlowRegistryStore | None = None,
    ) -> None:
        self.registry = registry
        self.subagent_client = subagent_client
        self.mission_registry = mission_registry
        self.subagent_registry = subagent_registry
        self.task_registry = task_registry
        self._task_flow_store = task_flow_store

    def aggregate(
        self,
        mission_id: str,
        *,
        robot_id: str | None = None,
        event_type: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Collect events from all robot subagents for a mission.

        Returns:
            {
                "mission_id": str,
                "event_count": int,
                "events": list[dict],  # sorted by timestamp ascending
            }
        """
        mission = self.mission_registry.get_mission(mission_id)
        if mission is None:
            return {"mission_id": mission_id, "event_count": 0, "events": []}

        all_events: list[dict[str, Any]] = []
        for subtask in mission.subtasks:
            if robot_id is not None and subtask.robot_id != robot_id:
                continue
            entry = self.registry.get(subtask.robot_id)
            if entry is None:
                continue
            events = self.subagent_client.get_events(entry, task_id=subtask.task_id)
            for event in events:
                enriched = dict(event)
                enriched["robot_id"] = subtask.robot_id
                all_events.append(enriched)

        if event_type is not None:
            all_events = [e for e in all_events if e.get("type") == event_type]

        all_events.sort(key=lambda e: e.get("timestamp", ""))

        all_events = all_events[:limit]

        # Route terminal robot events into SubagentRegistry and TaskRegistry
        if self.subagent_registry is not None or self.task_registry is not None:
            for event in all_events:
                status = _terminal_status_from_event(event)
                task_id = event.get("task_id")
                if status is not None and isinstance(task_id, str):
                    updated_at = str(event.get("timestamp") or datetime.now(timezone.utc).isoformat())
                    if self.subagent_registry is not None:
                        self.subagent_registry.mark_terminal(
                            child_task_id=task_id,
                            status=status,
                            updated_at=updated_at,
                        )
                    if self.task_registry is not None:
                        composite_id = f"{mission_id}:{task_id}"
                        try:
                            self.task_registry.update(
                                composite_id,
                                status=status,
                                ended_at=updated_at,
                            )
                        except KeyError:
                            pass  # no task record exists for this subtask

        # Update task-flow terminal status when all projected subtasks are done
        if self._task_flow_store is not None:
            flow = self._task_flow_store.get(mission_id)
            if flow is not None and flow.status == "running":
                task_events: dict[str, str] = {}
                for evt in all_events:
                    tid = evt.get("task_id") or evt.get("payload", {}).get("task_id")
                    if tid:
                        evt_status = _terminal_status_from_event(evt)
                        if evt_status is not None:
                            task_events[tid] = evt_status
                if flow.task_ids and all(tid in task_events for tid in flow.task_ids):
                    statuses = set(task_events.values())
                    failure_statuses = {"failed", "timed_out", "lost"}
                    if failure_statuses & statuses:
                        new_status = "failed"
                    elif "cancelled" in statuses:
                        new_status = "cancelled"
                    else:
                        new_status = "completed"
                    self._task_flow_store.upsert(TaskFlowRecord(
                        flow_id=flow.flow_id,
                        mission_id=flow.mission_id,
                        command=flow.command,
                        status=new_status,
                        task_ids=flow.task_ids,
                        robot_ids=flow.robot_ids,
                        created_at=flow.created_at,
                        updated_at=datetime.now(timezone.utc).isoformat(),
                    ))

        return {
            "mission_id": mission_id,
            "event_count": len(all_events),
            "events": all_events,
        }


def _terminal_status_from_event(event: dict[str, Any]) -> str | None:
    """Extract terminal status from a robot event, if applicable."""
    event_type = event.get("type") or event.get("event_type")
    if event_type == "task.completed":
        return "completed"
    if event_type == "task.failed":
        return "failed"
    if event_type == "task.cancelled":
        return "cancelled"
    payload = event.get("payload")
    if isinstance(payload, dict):
        status = payload.get("status")
        if status in {"completed", "failed", "cancelled", "timed_out", "lost"}:
            return str(status)
    return None
