from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Protocol

from fireclaw_core.agent.robot_registry import RobotRegistryEntry


@dataclass(frozen=True)
class GuardDecision:
    layer: str
    status: str
    reason: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "status": self.status,
            "reason": self.reason,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class MissionPlanningAuditRecord:
    command: str
    available_robots: list[dict[str, Any]]
    tool_schema: dict[str, Any] | None
    llm_tool_call: dict[str, Any] | None
    decisions: list[GuardDecision]
    final_status: str
    final_message: str
    created_at: str
    mission_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "available_robots": [dict(robot) for robot in self.available_robots],
            "tool_schema": self.tool_schema,
            "llm_tool_call": self.llm_tool_call,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "final_status": self.final_status,
            "final_message": self.final_message,
            "created_at": self.created_at,
            "mission_id": self.mission_id,
        }


class MissionPlanningAuditSink(Protocol):
    def record(self, record: MissionPlanningAuditRecord) -> None:
        ...


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_available_robot_snapshot(robots: list[RobotRegistryEntry]) -> list[dict[str, Any]]:
    return [
        {
            "robot_id": robot.robot_id,
            "capabilities": list(robot.capabilities),
            "enabled": robot.enabled,
            "zone": robot.zone,
        }
        for robot in robots
    ]


def append_guard_decision(
    record: MissionPlanningAuditRecord,
    decision: GuardDecision,
    *,
    final_status: str,
    final_message: str,
    mission_id: str | None = None,
) -> MissionPlanningAuditRecord:
    return replace(
        record,
        decisions=[*record.decisions, decision],
        final_status=final_status,
        final_message=final_message,
        mission_id=mission_id if mission_id is not None else record.mission_id,
    )


class JsonlMissionPlanningAuditSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record(self, record: MissionPlanningAuditRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def list_records(
        self,
        *,
        mission_id: str | None = None,
        limit: int | None = None,
    ) -> list[MissionPlanningAuditRecord]:
        if limit is not None and limit <= 0:
            return []
        records = self._read_all()
        if mission_id is not None:
            records = [record for record in records if record.mission_id == mission_id]
        if limit is not None:
            records = records[-limit:]
        return records

    def _read_all(self) -> list[MissionPlanningAuditRecord]:
        if not self.path.exists():
            return []
        import logging
        logger = logging.getLogger(__name__)
        records: list[MissionPlanningAuditRecord] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    value = json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    logger.warning("Skipping malformed audit line: %s", stripped[:200])
                    continue
                if isinstance(value, dict):
                    records.append(_audit_record_from_dict(value))
        return records


def _audit_record_from_dict(value: dict[str, Any]) -> MissionPlanningAuditRecord:
    decisions_value = value.get("decisions")
    decisions = [
        _guard_decision_from_dict(item)
        for item in decisions_value
        if isinstance(item, dict)
    ] if isinstance(decisions_value, list) else []
    available_robots = value.get("available_robots")
    return MissionPlanningAuditRecord(
        command=str(value.get("command") or ""),
        available_robots=[dict(item) for item in available_robots if isinstance(item, dict)] if isinstance(available_robots, list) else [],
        tool_schema=value.get("tool_schema") if isinstance(value.get("tool_schema"), dict) else None,
        llm_tool_call=value.get("llm_tool_call") if isinstance(value.get("llm_tool_call"), dict) else None,
        decisions=decisions,
        final_status=str(value.get("final_status") or ""),
        final_message=str(value.get("final_message") or ""),
        created_at=str(value.get("created_at") or ""),
        mission_id=value.get("mission_id") if isinstance(value.get("mission_id"), str) else None,
    )


def _guard_decision_from_dict(value: dict[str, Any]) -> GuardDecision:
    details = value.get("details")
    return GuardDecision(
        layer=str(value.get("layer") or ""),
        status=str(value.get("status") or ""),
        reason=str(value.get("reason") or ""),
        message=str(value.get("message") or ""),
        details=dict(details) if isinstance(details, dict) else {},
    )
