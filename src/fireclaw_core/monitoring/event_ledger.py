from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


class EventLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(
        self,
        *,
        task_id: str,
        session_id: str,
        type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        event = {
            "event_id": f"evt-{uuid4().hex}",
            "task_id": task_id,
            "session_id": session_id,
            "type": type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        with self.path.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def list_events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                events.append(json.loads(stripped))
        return events

    def events_for_task(self, task_id: str) -> list[dict[str, Any]]:
        return [event for event in self.list_events() if event.get("task_id") == task_id]

    def latest_events(
        self,
        *,
        limit: int = 20,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        events = self.list_events()
        if session_id is not None:
            events = [event for event in events if event.get("session_id") == session_id]
        return list(reversed(events))[:limit]
