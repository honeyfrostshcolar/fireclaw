"""Typed HTTP client for MissionGateway.

Provides a synchronous client for all MissionGateway endpoints using
urllib.request (stdlib). Supports Bearer token auth and X-Operator-Scopes header.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
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

    def get_pending_approvals(self, mission_id: str) -> dict[str, Any]:
        """POST /missions/{id}/approvals with action=pending"""
        return self._post(f"/missions/{mission_id}/approvals", {"action": "pending"})

    def resolve_approval_token(self, mission_id: str, approval_token: str) -> dict[str, Any]:
        """POST /missions/{id}/approvals with action=resolve_token"""
        return self._post(
            f"/missions/{mission_id}/approvals",
            {"action": "resolve_token", "approval_token": approval_token},
        )

    def get_fleet_state(self) -> dict[str, Any]:
        """GET /fleet/state"""
        return self._get("/fleet/state")

    def get_fleet_doctor(self) -> dict[str, Any]:
        """GET /fleet/doctor"""
        return self._get("/fleet/doctor")

    def stream_mission_events(
        self,
        mission_id: str,
        *,
        after_sequence: int | None = None,
        last_event_id: int | None = None,
        max_events: int | None = None,
        timeout: float | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Stream SSE events from /missions/{id}/events/stream.

        Yields parsed event dicts. Supports cursor replay via after_sequence
        or last_event_id. Stops after max_events or on connection close/timeout.

        For reconnect, pass after_sequence=<last_seen_sequence>.
        """
        path = f"/missions/{mission_id}/events/stream"
        params: list[str] = []
        if after_sequence is not None:
            params.append(f"after_sequence={after_sequence}")
        if params:
            path = f"{path}?{'&'.join(params)}"

        headers = self._headers()
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)

        url = f"{self._base_url}{path}"
        req = request.Request(url, method="GET", headers=headers)
        effective_timeout = timeout if timeout is not None else self._timeout

        count = 0
        try:
            with request.urlopen(req, timeout=effective_timeout) as resp:
                for event in _iter_sse_events(resp):
                    yield event
                    count += 1
                    if max_events is not None and count >= max_events:
                        return
        except Exception:
            # On any error (timeout, connection reset), return what we have.
            # Caller can reconnect with after_sequence=<last_seen_sequence>.
            return

    def stream_mission_events_with_cursor(
        self,
        mission_id: str,
        *,
        after_sequence: int | None = None,
        max_events: int | None = None,
        timeout: float | None = None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Like stream_mission_events, but returns (events, last_sequence).

        The last_sequence can be passed to after_sequence on reconnect.
        """
        events: list[dict[str, Any]] = []
        last_seq = after_sequence
        for event in self.stream_mission_events(
            mission_id,
            after_sequence=after_sequence,
            max_events=max_events,
            timeout=timeout,
        ):
            events.append(event)
            seq = event.get("sequence")
            if isinstance(seq, int):
                last_seq = seq
        return events, last_seq

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


# ---------------------------------------------------------------------------
# Module-level SSE parser
# ---------------------------------------------------------------------------


def _iter_sse_events(response: Any) -> Iterator[dict[str, Any]]:
    """Parse SSE event blocks from an HTTP response.

    Yields parsed data dicts for each complete event block.
    Handles: event:, id:, data:, comments (:), and blank-line delimiters.
    """
    current_event: dict[str, str] = {}
    data_lines: list[str] = []

    for raw_line in response:
        line = raw_line.decode("utf-8").rstrip("\r\n")

        if line == "":
            # Blank line = end of event block
            parsed = _finalize_sse_event(data_lines, current_event)
            if parsed is not None:
                yield parsed
            current_event = {}
            data_lines = []
            continue

        if line.startswith(":"):
            # Comment (e.g., heartbeat) — skip
            continue

        if line.startswith("event:"):
            current_event["event"] = line[6:].strip()
        elif line.startswith("id:"):
            current_event["id"] = line[3:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].removeprefix(" "))
        # Ignore unknown fields

    # Handle final event without trailing blank line
    parsed = _finalize_sse_event(data_lines, current_event)
    if parsed is not None:
        yield parsed


def _finalize_sse_event(
    data_lines: list[str],
    current_event: dict[str, str],
) -> dict[str, Any] | None:
    """Convert accumulated SSE data lines into a parsed event dict."""
    if not data_lines:
        return None
    data_str = "\n".join(data_lines)
    try:
        parsed: dict[str, Any] = json.loads(data_str)
    except (json.JSONDecodeError, ValueError):
        parsed = {"raw": data_str}
    if "event" in current_event:
        parsed.setdefault("event_type", current_event["event"])
    if "id" in current_event:
        try:
            parsed.setdefault("sequence", int(current_event["id"]))
        except (ValueError, TypeError):
            parsed.setdefault("event_id", current_event["id"])
    return parsed
