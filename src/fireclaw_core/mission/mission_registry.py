from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from fireclaw_core.task.terminal_outcome import (
    ROBOT_TASK_TERMINAL_STATUSES,
    normalize_robot_task_terminal_status,
)


TERMINAL_SUBTASK_STATUSES = set(ROBOT_TASK_TERMINAL_STATUSES) | {
    "succeeded",
    "block",
    "denied",
}


@dataclass(frozen=True)
class MissionSubtaskRecord:
    robot_id: str
    task_id: str
    command: str
    status: str
    created_at: str
    updated_at: str
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MissionRecord:
    mission_id: str
    session_id: str | None
    command: str
    status: str
    created_at: str
    updated_at: str
    subtasks: list[MissionSubtaskRecord] = field(default_factory=list)
    final_report: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["subtasks"] = [subtask.to_dict() for subtask in self.subtasks]
        return data


class JsonlMissionRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def create_mission(
        self,
        *,
        mission_id: str,
        session_id: str | None,
        command: str,
        created_at: str,
    ) -> MissionRecord:
        record = MissionRecord(
            mission_id=mission_id,
            session_id=session_id,
            command=command,
            status="created",
            created_at=created_at,
            updated_at=created_at,
        )
        self._append({"type": "mission", "mission": record.to_dict()})
        return record

    def record_subtask(
        self,
        *,
        mission_id: str,
        robot_id: str,
        task_id: str,
        command: str,
        status: str,
        created_at: str,
    ) -> MissionSubtaskRecord:
        status = normalize_robot_task_terminal_status(status) or status
        subtask = MissionSubtaskRecord(
            robot_id=robot_id,
            task_id=task_id,
            command=command,
            status=status,
            created_at=created_at,
            updated_at=created_at,
        )
        self._append({"type": "subtask", "mission_id": mission_id, "subtask": subtask.to_dict()})
        return subtask

    def update_subtask(
        self,
        *,
        mission_id: str,
        robot_id: str,
        task_id: str,
        status: str,
        updated_at: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> MissionSubtaskRecord:
        mission = self.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"Mission not found: {mission_id}")
        current = _find_subtask(mission.subtasks, robot_id, task_id)
        if current is None:
            raise KeyError(f"Mission subtask not found: {mission_id}/{robot_id}/{task_id}")
        status = normalize_robot_task_terminal_status(status) or status
        updated = MissionSubtaskRecord(
            robot_id=current.robot_id,
            task_id=current.task_id,
            command=current.command,
            status=status,
            created_at=current.created_at,
            updated_at=updated_at,
            result=result if result is not None else current.result,
            error=error if error is not None else current.error,
        )
        self._append({"type": "subtask", "mission_id": mission_id, "subtask": updated.to_dict()})
        return updated

    def record_final_report(
        self,
        *,
        mission_id: str,
        report: dict[str, Any],
        updated_at: str,
    ) -> None:
        if self.get_mission(mission_id) is None:
            raise KeyError(f"Mission not found: {mission_id}")
        self._append({
            "type": "mission.report",
            "mission_id": mission_id,
            "report": dict(report),
            "updated_at": updated_at,
        })

    def get_mission(self, mission_id: str) -> MissionRecord | None:
        return self._missions_by_id().get(mission_id)

    def list_missions(self) -> list[MissionRecord]:
        """Return all missions with derived status."""
        missions = self._missions_by_id()
        result: list[MissionRecord] = []
        for mission in missions.values():
            derived_status = _mission_status(mission.subtasks)
            if mission.status != derived_status:
                result.append(MissionRecord(
                    mission_id=mission.mission_id,
                    session_id=mission.session_id,
                    command=mission.command,
                    status=derived_status,
                    created_at=mission.created_at,
                    updated_at=mission.updated_at,
                    subtasks=mission.subtasks,
                    final_report=mission.final_report,
                ))
            else:
                result.append(mission)
        return sorted(result, key=lambda m: (m.created_at, m.mission_id))

    def mission_trace(self, mission_id: str) -> dict[str, Any]:
        mission = self.get_mission(mission_id)
        if mission is None:
            return {"mission_id": mission_id, "status": "not_found", "subtasks": []}
        subtasks = [subtask.to_dict() for subtask in mission.subtasks]
        terminal = [subtask for subtask in mission.subtasks if subtask.status in TERMINAL_SUBTASK_STATUSES]
        result = {
            "mission_id": mission.mission_id,
            "session_id": mission.session_id,
            "command": mission.command,
            "status": _mission_status(mission.subtasks),
            "created_at": mission.created_at,
            "updated_at": mission.updated_at,
            "subtask_count": len(mission.subtasks),
            "completed_subtask_count": len(terminal),
            "subtasks": subtasks,
        }
        if mission.final_report is not None:
            result["final_report"] = dict(mission.final_report)
        return result

    def _missions_by_id(self) -> dict[str, MissionRecord]:
        missions: dict[str, MissionRecord] = {}
        subtasks: dict[str, dict[tuple[str, str], MissionSubtaskRecord]] = {}
        for entry in self._read_entries():
            entry_type = entry.get("type")
            if entry_type == "mission":
                mission_value = entry.get("mission")
                if not isinstance(mission_value, dict):
                    continue
                mission = _mission_from_dict(mission_value)
                existing_subtasks = subtasks.get(mission.mission_id, {})
                missions[mission.mission_id] = MissionRecord(
                    mission_id=mission.mission_id,
                    session_id=mission.session_id,
                    command=mission.command,
                    status=mission.status,
                    created_at=mission.created_at,
                    updated_at=mission.updated_at,
                    subtasks=list(existing_subtasks.values()),
                    final_report=mission.final_report,
                )
            elif entry_type == "subtask":
                mission_id = entry.get("mission_id")
                subtask_value = entry.get("subtask")
                if not isinstance(mission_id, str) or not isinstance(subtask_value, dict):
                    continue
                subtask = _subtask_from_dict(subtask_value)
                mission_subtasks = subtasks.setdefault(mission_id, {})
                mission_subtasks[(subtask.robot_id, subtask.task_id)] = subtask
                mission = missions.get(mission_id)
                if mission is not None:
                    missions[mission_id] = MissionRecord(
                        mission_id=mission.mission_id,
                        session_id=mission.session_id,
                        command=mission.command,
                        status=mission.status,
                        created_at=mission.created_at,
                        updated_at=subtask.updated_at,
                        subtasks=list(mission_subtasks.values()),
                        final_report=mission.final_report,
                    )
            elif entry_type == "mission.report":
                mission_id = entry.get("mission_id")
                report = entry.get("report")
                mission = missions.get(mission_id)
                if isinstance(mission_id, str) and mission is not None and isinstance(report, dict):
                    missions[mission_id] = MissionRecord(
                        mission_id=mission.mission_id,
                        session_id=mission.session_id,
                        command=mission.command,
                        status=mission.status,
                        created_at=mission.created_at,
                        updated_at=str(entry.get("updated_at") or mission.updated_at),
                        subtasks=mission.subtasks,
                        final_report=dict(report),
                    )
        return missions

    def _read_entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                value = json.loads(stripped)
                if isinstance(value, dict):
                    entries.append(value)
        return entries

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _find_subtask(
    subtasks: list[MissionSubtaskRecord],
    robot_id: str,
    task_id: str,
) -> MissionSubtaskRecord | None:
    for subtask in subtasks:
        if subtask.robot_id == robot_id and subtask.task_id == task_id:
            return subtask
    return None


def _mission_status(subtasks: list[MissionSubtaskRecord]) -> str:
    if not subtasks:
        return "created"
    normalized = [
        normalize_robot_task_terminal_status(subtask.status)
        for subtask in subtasks
    ]
    if any(status == "escalated" for status in normalized):
        return "escalated"
    if any(
        status in {"blocked", "failed", "timed_out", "lost"}
        for status in normalized
    ):
        return "failed"
    if any(status == "cancelled" for status in normalized):
        return "cancelled"
    if all(status == "completed" for status in normalized):
        return "succeeded"
    return "running"


def _mission_from_dict(value: dict[str, Any]) -> MissionRecord:
    final_report = value.get("final_report")
    return MissionRecord(
        mission_id=str(value.get("mission_id") or ""),
        session_id=value.get("session_id") if isinstance(value.get("session_id"), str) else None,
        command=str(value.get("command") or ""),
        status=str(value.get("status") or "created"),
        created_at=str(value.get("created_at") or ""),
        updated_at=str(value.get("updated_at") or value.get("created_at") or ""),
        final_report=dict(final_report) if isinstance(final_report, dict) else None,
    )


def _subtask_from_dict(value: dict[str, Any]) -> MissionSubtaskRecord:
    result_value = value.get("result")
    raw_status = str(value.get("status") or "unknown")
    return MissionSubtaskRecord(
        robot_id=str(value.get("robot_id") or ""),
        task_id=str(value.get("task_id") or ""),
        command=str(value.get("command") or ""),
        status=normalize_robot_task_terminal_status(raw_status) or raw_status,
        created_at=str(value.get("created_at") or ""),
        updated_at=str(value.get("updated_at") or value.get("created_at") or ""),
        result=result_value if isinstance(result_value, dict) else None,
        error=value.get("error") if isinstance(value.get("error"), str) else None,
    )
