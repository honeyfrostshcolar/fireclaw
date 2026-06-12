from __future__ import annotations

from datetime import datetime
from typing import Any

from fireclaw_core.monitoring.event_ledger import EventLedger
from fireclaw_core.mission.mission_memory import MissionMemoryStore
from fireclaw_core.mission.mission_registry import JsonlMissionRegistry, TERMINAL_SUBTASK_STATUSES


class IncidentReplay:
    """Reconstruct a mission timeline from persistent registry + memory data."""

    def __init__(
        self,
        *,
        mission_registry: JsonlMissionRegistry,
        mission_memory: MissionMemoryStore | None = None,
        event_ledger: EventLedger | None = None,
    ) -> None:
        self._registry = mission_registry
        self._memory = mission_memory
        self._event_ledger = event_ledger

    def replay(self, mission_id: str) -> dict[str, Any]:
        """
        Reconstruct mission timeline from persistent data.

        Returns dict with keys:
          mission_id, command, status, created_at, updated_at,
          timeline (list of events sorted by timestamp),
          summary (aggregated counts and duration).

        Raises KeyError if mission not found.
        """
        mission = self._registry.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"Mission not found: {mission_id}")

        timeline: list[dict[str, Any]] = []

        # Build timeline events from subtasks
        for subtask in mission.subtasks:
            timeline.append({
                "timestamp": subtask.created_at,
                "event_type": "subtask.submitted",
                "source": "mission-registry",
                "mission_id": mission_id,
                "robot_id": subtask.robot_id,
                "task_id": subtask.task_id,
                "status": None,
                "content": None,
                "payload": {},
            })
            if subtask.status != "submitted":
                timeline.append({
                    "timestamp": subtask.updated_at,
                    "event_type": "subtask.status_changed",
                    "source": "mission-registry",
                    "mission_id": mission_id,
                    "robot_id": subtask.robot_id,
                    "task_id": subtask.task_id,
                    "status": subtask.status,
                    "content": None,
                    "payload": {"status": subtask.status},
                })

        # Add memory records if memory store configured
        memory_record_count = 0
        correction_count = 0
        if self._memory is not None:
            records = self._memory.search(mission_id=mission_id, limit=1000)
            memory_record_count = len(records)
            for record in records:
                if record.record_type == "correction":
                    correction_count += 1
                timeline.append({
                    "timestamp": record.created_at,
                    "event_type": record.record_type,
                    "source": "memory",
                    "mission_id": mission_id,
                    "robot_id": record.robot_id,
                    "task_id": record.subtask_id,
                    "status": None,
                    "content": record.content,
                    "payload": {"content": record.content},
                })

        # Add action-level events from event ledger if configured
        action_event_count = 0
        if self._event_ledger is not None:
            task_ids = {subtask.task_id for subtask in mission.subtasks}
            for task_id in task_ids:
                for event in self._event_ledger.events_for_task(task_id):
                    action_event_count += 1
                    timeline.append({
                        "timestamp": event["timestamp"],
                        "event_type": event["type"],
                        "source": "event-ledger",
                        "mission_id": mission_id,
                        "robot_id": None,
                        "task_id": event["task_id"],
                        "status": None,
                        "content": event.get("payload"),
                        "payload": event.get("payload") or {},
                    })

        # Sort timeline by timestamp
        timeline.sort(key=lambda event: event["timestamp"])

        # Compute summary
        completed_count = 0
        failed_count = 0
        cancelled_count = 0
        for subtask in mission.subtasks:
            if subtask.status in {"succeeded", "completed"}:
                completed_count += 1
            elif subtask.status in {"failed", "block", "denied", "lost"}:
                failed_count += 1
            elif subtask.status == "cancelled":
                cancelled_count += 1

        # Compute duration from mission created_at to last event timestamp
        duration_seconds: float | None = None
        if timeline:
            try:
                start = datetime.fromisoformat(mission.created_at.replace("Z", "+00:00"))
                end = datetime.fromisoformat(timeline[-1]["timestamp"].replace("Z", "+00:00"))
                duration_seconds = (end - start).total_seconds()
            except (ValueError, TypeError):
                pass

        return {
            "mission_id": mission.mission_id,
            "command": mission.command,
            "status": mission.status,
            "created_at": mission.created_at,
            "updated_at": mission.updated_at,
            "timeline": timeline,
            "summary": {
                "subtask_count": len(mission.subtasks),
                "completed_count": completed_count,
                "failed_count": failed_count,
                "cancelled_count": cancelled_count,
                "memory_record_count": memory_record_count,
                "correction_count": correction_count,
                "action_event_count": action_event_count,
                "duration_seconds": duration_seconds,
            },
        }
