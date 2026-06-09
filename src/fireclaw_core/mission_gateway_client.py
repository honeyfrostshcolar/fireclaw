"""Typed HTTP client for MissionGateway.

Provides a synchronous client for all MissionGateway endpoints using
urllib.request (stdlib). Supports Bearer token auth and X-Operator-Scopes header.
"""
from __future__ import annotations

import json
from typing import Any
from urllib import request
from urllib.error import HTTPError


class MissionGatewayClient:
    """Typed HTTP client for MissionGateway."""

    def __init__(
        self,
        base_url: str,
        *,
        api_token: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit_mission(self, command: str, **kwargs: Any) -> dict[str, Any]:
        """POST /missions"""
        body: dict[str, Any] = {"command": command, **kwargs}
        return self._post("/missions", body)

    def get_mission_trace(self, mission_id: str) -> dict[str, Any]:
        """GET /missions/{id}/trace"""
        return self._get(f"/missions/{mission_id}/trace")

    def get_mission_events(self, mission_id: str) -> dict[str, Any]:
        """GET /missions/{id}/events"""
        return self._get(f"/missions/{mission_id}/events")

    def cancel_mission(self, mission_id: str) -> dict[str, Any]:
        """POST /missions/{id}/cancel"""
        return self._post(f"/missions/{mission_id}/cancel", {})

    def request_approval(self, mission_id: str, **kwargs: Any) -> dict[str, Any]:
        """POST /missions/{id}/approvals"""
        return self._post(f"/missions/{mission_id}/approvals", kwargs)

    def get_fleet_state(self) -> dict[str, Any]:
        """GET /fleet/state"""
        return self._get("/fleet/state")

    def get_fleet_doctor(self) -> dict[str, Any]:
        """GET /fleet/doctor"""
        return self._get("/fleet/doctor")

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _get(self, path: str) -> dict[str, Any]:
        req = request.Request(
            f"{self._base_url}{path}",
            method="GET",
            headers=self._headers(),
        )
        return self._do_request(req)

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self._base_url}{path}",
            data=data,
            method="POST",
            headers=self._headers(),
        )
        return self._do_request(req)

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "X-Operator-Scopes": "admin",
        }
        if self._api_token is not None:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    def _do_request(self, req: request.Request) -> dict[str, Any]:
        try:
            with request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError:
            raise
