from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonlMemoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def list_records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                records.append(json.loads(stripped))
        return records

    def latest_records(self, limit: int = 5, session_id: str | None = None) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        records = self.list_records()
        if session_id is not None:
            records = [
                record
                for record in records
                if record.get("session", {}).get("session_id") == session_id
            ]
        return records[-limit:]

    def search_records(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        intent: str | None = None,
        target_floor: int | None = None,
        command_contains: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        records = self.list_records()
        matches: list[dict[str, Any]] = []
        for record in records:
            if session_id is not None and record.get("session", {}).get("session_id") != session_id:
                continue
            if status is not None and record.get("status") != status:
                continue
            planning = record.get("planning") or {}
            if intent is not None and planning.get("intent") != intent:
                continue
            if target_floor is not None and planning.get("target_floor") != target_floor:
                continue
            if command_contains is not None and command_contains not in str(record.get("command", "")):
                continue
            matches.append(record)
        return list(reversed(matches))[:limit]
