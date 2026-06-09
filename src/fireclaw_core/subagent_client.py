from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib import request
from urllib.error import HTTPError

from fireclaw_core.robot_registry import RobotRegistryEntry


class RobotSubagentClient:
    def __init__(self, *, timeout_seconds: float = 5.0, api_token: str | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.api_token = api_token

    def get_state(self, entry: RobotRegistryEntry) -> dict[str, Any]:
        return self._request_json("GET", entry.base_url, "/state")

    def submit_task(
        self,
        entry: RobotRegistryEntry,
        *,
        command: str,
        session_id: str | None = None,
        dedupe_key: str | None = None,
        operator: dict[str, Any] | None = None,
        mission: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"command": command}
        if session_id is not None:
            payload["session_id"] = session_id
        if dedupe_key is not None:
            payload["dedupe_key"] = dedupe_key
        if operator is not None:
            payload["operator"] = operator
        if mission is not None:
            payload["mission"] = mission
        result = self._request_json("POST", entry.base_url, "/tasks", payload)
        result.setdefault("robot_id", entry.robot_id)
        return result

    def get_task_trace(self, entry: RobotRegistryEntry, task_id: str) -> dict[str, Any]:
        result = self._request_json("GET", entry.base_url, f"/tasks/{task_id}")
        result.setdefault("robot_id", entry.robot_id)
        return result

    def cancel_task(
        self,
        entry: RobotRegistryEntry,
        task_id: str,
        *,
        operator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if operator is not None:
            payload["operator"] = operator
        result = self._request_json("POST", entry.base_url, f"/tasks/{task_id}/cancel", payload)
        result.setdefault("robot_id", entry.robot_id)
        return result

    def get_events(
        self,
        entry: RobotRegistryEntry,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        path = f"/events?limit={limit}"
        if task_id:
            path += f"&task_id={task_id}"
        result = self._request_json("GET", entry.base_url, path)
        return result.get("events", [])

    def check_presence(self, entry: RobotRegistryEntry) -> dict[str, Any]:
        try:
            state = self.get_state(entry)
            now = datetime.now(timezone.utc).isoformat()
            return {
                "robot_id": entry.robot_id,
                "online": True,
                "last_seen_at": now,
                "state": state,
            }
        except Exception as exc:
            return {
                "robot_id": entry.robot_id,
                "online": False,
                "error": str(exc),
            }

    def _request_json(
        self,
        method: str,
        base_url: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_token is not None:
            headers["Authorization"] = f"Bearer {self.api_token}"
        req = request.Request(
            f"{base_url.rstrip('/')}{path}",
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return _decode_json_response(response.read())
        except HTTPError as exc:
            body = _decode_json_response(exc.read())
            body.setdefault("status", "error")
            body.setdefault("http_status", exc.code)
            return body


def _decode_json_response(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Robot subagent response must be a JSON object.")
    return value
