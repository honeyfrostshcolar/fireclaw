from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any
import uuid


TERMINAL_APPROVAL_STATUSES = {"approved", "denied", "expired"}


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    mission_id: str
    action: str
    risk_level: str
    command: str
    requested_by: str
    status: str = "pending"
    decided_by: str | None = None
    decided_at: str | None = None
    reason: str | None = None
    created_at: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_APPROVAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JsonlApprovalStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def create(
        self,
        *,
        mission_id: str,
        action: str,
        risk_level: str,
        command: str,
        requested_by: str,
        created_at: str,
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            request_id=uuid.uuid4().hex,
            mission_id=mission_id,
            action=action,
            risk_level=risk_level,
            command=command,
            requested_by=requested_by,
            status="pending",
            created_at=created_at,
        )
        self._append(request.to_dict())
        return request

    def approve(
        self,
        request_id: str,
        *,
        decided_by: str,
        decided_at: str,
    ) -> ApprovalRequest | None:
        current = self.get(request_id)
        if current is None or current.is_terminal:
            return None
        updated = ApprovalRequest(
            request_id=current.request_id,
            mission_id=current.mission_id,
            action=current.action,
            risk_level=current.risk_level,
            command=current.command,
            requested_by=current.requested_by,
            status="approved",
            decided_by=decided_by,
            decided_at=decided_at,
            reason=current.reason,
            created_at=current.created_at,
        )
        self._append(updated.to_dict())
        return updated

    def deny(
        self,
        request_id: str,
        *,
        decided_by: str,
        reason: str | None,
        decided_at: str,
    ) -> ApprovalRequest | None:
        current = self.get(request_id)
        if current is None or current.is_terminal:
            return None
        updated = ApprovalRequest(
            request_id=current.request_id,
            mission_id=current.mission_id,
            action=current.action,
            risk_level=current.risk_level,
            command=current.command,
            requested_by=current.requested_by,
            status="denied",
            decided_by=decided_by,
            decided_at=decided_at,
            reason=reason,
            created_at=current.created_at,
        )
        self._append(updated.to_dict())
        return updated

    def get(self, request_id: str) -> ApprovalRequest | None:
        return self._records_by_request_id().get(request_id)

    def list_requests(
        self,
        *,
        mission_id: str | None = None,
        status: str | None = None,
    ) -> list[ApprovalRequest]:
        records = list(self._records_by_request_id().values())
        if mission_id is not None:
            records = [r for r in records if r.mission_id == mission_id]
        if status is not None:
            records = [r for r in records if r.status == status]
        return records

    def pending_requests(
        self,
        mission_id: str | None = None,
    ) -> list[ApprovalRequest]:
        return self.list_requests(mission_id=mission_id, status="pending")

    def _records_by_request_id(self) -> dict[str, ApprovalRequest]:
        records: dict[str, ApprovalRequest] = {}
        for entry in self._read_entries():
            request_id = entry.get("request_id")
            if not isinstance(request_id, str):
                continue
            records[request_id] = ApprovalRequest(
                request_id=request_id,
                mission_id=str(entry.get("mission_id") or ""),
                action=str(entry.get("action") or ""),
                risk_level=str(entry.get("risk_level") or ""),
                command=str(entry.get("command") or ""),
                requested_by=str(entry.get("requested_by") or ""),
                status=str(entry.get("status") or "unknown"),
                decided_by=_string_or_none(entry.get("decided_by")),
                decided_at=_string_or_none(entry.get("decided_at")),
                reason=_string_or_none(entry.get("reason")),
                created_at=str(entry.get("created_at") or ""),
            )
        return records

    def _read_entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    value = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    entries.append(value)
        return entries

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None
