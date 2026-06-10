from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib import request
from urllib.error import HTTPError

from fireclaw_core.robot_registry import RobotRegistryEntry
from fireclaw_core.subagent_registry import JsonlSubagentRegistry, TERMINAL_SUBAGENT_STATUSES


class RobotSubagentClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        api_token: str | None = None,
        registry: JsonlSubagentRegistry | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.api_token = api_token
        self.registry = registry

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

        if self.registry is not None:
            task_id = result.get("task_id")
            if isinstance(task_id, str) and task_id:
                mission_id = ""
                subtask_id = None
                if isinstance(mission, dict):
                    mission_id = str(mission.get("mission_id") or "")
                    subtask_id = mission.get("subtask_id")
                    if isinstance(subtask_id, str) and subtask_id:
                        pass
                    else:
                        subtask_id = None
                self.registry.create(
                    parent_mission_id=mission_id,
                    parent_subtask_id=subtask_id,
                    robot_id=entry.robot_id,
                    child_task_id=task_id,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )

        return result

    def get_task_trace(self, entry: RobotRegistryEntry, task_id: str) -> dict[str, Any]:
        result = self._request_json("GET", entry.base_url, f"/tasks/{task_id}")
        result.setdefault("robot_id", entry.robot_id)
        if self.registry is not None:
            status = _status_from_trace(result)
            if status in TERMINAL_SUBAGENT_STATUSES:
                record = self.registry.get_by_child_task_id(task_id)
                if record is not None:
                    self.registry.update(
                        record.run_id,
                        status=status,
                        delivery_status="delivered",
                        updated_at=datetime.now(timezone.utc).isoformat(),
                        error=_error_from_trace(result),
                    )
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

        if self.registry is not None:
            record = self.registry.get_by_child_task_id(task_id)
            if record is not None:
                self.registry.update(
                    record.run_id,
                    status="cancelled",
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )

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
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "X-Operator-Scopes": "admin",
        }
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


def _status_from_trace(trace: dict[str, Any]) -> str | None:
    result = trace.get("result")
    if isinstance(result, dict):
        status = result.get("status")
        if isinstance(status, str) and status:
            return status
    status = trace.get("status")
    return status if isinstance(status, str) and status else None


def _error_from_trace(trace: dict[str, Any]) -> str | None:
    result = trace.get("result")
    if isinstance(result, dict):
        error = result.get("error")
        if isinstance(error, str) and error:
            return error
    error = trace.get("error")
    return error if isinstance(error, str) and error else None
