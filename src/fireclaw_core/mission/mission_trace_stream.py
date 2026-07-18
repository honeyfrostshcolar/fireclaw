from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generator

from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_registry import TERMINAL_SUBTASK_STATUSES

# Mission-level terminal statuses
_TERMINAL_MISSION_STATUSES = {"succeeded", "failed", "cancelled", "aborted", "escalated"}


@dataclass(frozen=True)
class MissionEvent:
    type: str
    mission_id: str
    robot_id: str | None = None
    task_id: str | None = None
    status: str | None = None
    previous_status: str | None = None
    timestamp: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "mission_id": self.mission_id,
            "robot_id": self.robot_id,
            "task_id": self.task_id,
            "status": self.status,
            "previous_status": self.previous_status,
            "timestamp": self.timestamp,
            "details": self.details,
        }


@dataclass
class MissionTraceStream:
    """Polling-based mission event stream.

    Polls mission_trace() and yields MissionEvent objects for detected changes.
    Stops when the mission reaches terminal status or timeout.
    """

    mission_agent: MissionAgent
    poll_interval_seconds: float = 0.1

    def stream(
        self,
        mission_id: str,
        *,
        timeout_seconds: float = 300.0,
    ) -> Generator[MissionEvent, None, None]:
        """Yield mission events until mission is terminal or timeout."""
        deadline = time.monotonic() + timeout_seconds
        # Track seen subtask statuses: (robot_id, task_id) -> status
        seen: dict[tuple[str, str], str] = {}
        last_mission_status: str | None = None

        while time.monotonic() < deadline:
            trace = self.mission_agent.mission_trace(mission_id)
            mission_status = trace.get("status", "unknown")
            now = datetime.now(timezone.utc).isoformat()

            # Check for new or changed subtasks
            for subtask in trace.get("subtasks", []):
                if not isinstance(subtask, dict):
                    continue
                robot_id = subtask.get("robot_id", "")
                task_id = subtask.get("task_id", "")
                status = subtask.get("status", "unknown")
                key = (robot_id, task_id)

                if key not in seen:
                    # New subtask
                    seen[key] = status
                    yield MissionEvent(
                        type="subtask.submitted",
                        mission_id=mission_id,
                        robot_id=robot_id,
                        task_id=task_id,
                        status=status,
                        timestamp=now,
                    )
                elif seen[key] != status:
                    # Status changed
                    previous = seen[key]
                    seen[key] = status
                    yield MissionEvent(
                        type="subtask.status_changed",
                        mission_id=mission_id,
                        robot_id=robot_id,
                        task_id=task_id,
                        status=status,
                        previous_status=previous,
                        timestamp=now,
                    )

            # Check for mission status transition
            if last_mission_status is not None and mission_status != last_mission_status:
                event_type = f"mission.{mission_status}"
                yield MissionEvent(
                    type=event_type,
                    mission_id=mission_id,
                    status=mission_status,
                    previous_status=last_mission_status,
                    timestamp=now,
                )
            last_mission_status = mission_status

            # Stop if mission is terminal
            if mission_status in _TERMINAL_MISSION_STATUSES:
                return

            time.sleep(self.poll_interval_seconds)

        # Timeout
        yield MissionEvent(
            type="mission.timeout",
            mission_id=mission_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            details={"timeout_seconds": timeout_seconds},
        )
