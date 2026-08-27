"""Typed HTTP client for MissionGateway.

Provides a synchronous client for all MissionGateway endpoints using
urllib.request (stdlib). Caller identity and scopes are derived by the server
from the authenticated connection, never from client-provided headers.
"""
from __future__ import annotations

import json
from io import BytesIO
from collections.abc import Iterable, Iterator, Mapping
from typing import Any
from urllib import request
from urllib.error import HTTPError

from fireclaw_core.gateway.transport import (
    DEFAULT_GATEWAY_RESPONSE_BYTES,
    GatewayHttpTransport,
    GatewayTlsClientConfig,
    read_bounded_gateway_response,
    validate_gateway_response_limit,
    validate_gateway_url,
)


class MissionGatewayRequestError(HTTPError):
    """Bounded, typed Mission Gateway HTTP failure.

    The class remains an ``HTTPError`` subclass for compatibility while
    exposing the server's structured error message to operator surfaces.
    """

    def __init__(
        self,
        *,
        url: str,
        status_code: int,
        reason: str,
        headers: Any,
        body: bytes,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_payload = dict(payload or {})
        gateway_message = _gateway_error_message(
            normalized_payload,
            fallback=reason,
        )
        super().__init__(
            url,
            int(status_code),
            gateway_message,
            headers,
            BytesIO(body),
        )
        self.status_code = int(status_code)
        self.http_reason = reason
        self.gateway_message = gateway_message
        self.gateway_code = _gateway_error_code(
            normalized_payload,
            status_code=self.status_code,
        )
        self.payload = normalized_payload
        self.retryable = self.status_code in {408, 425, 429} or (
            self.status_code >= 500
        )


class MissionGatewayStreamUnsupported(RuntimeError):
    """The peer answered a streaming request with a non-SSE response."""

    def __init__(self, *, url: str, content_type: str) -> None:
        self.url = url
        self.content_type = content_type
        super().__init__(
            "Mission Gateway does not expose the requested SSE stream "
            f"(Content-Type: {content_type or 'missing'})."
        )


class MissionGatewayClient:
    """Typed HTTP client for MissionGateway."""

    def __init__(
        self,
        base_url: str,
        *,
        api_token: str | None = None,
        timeout: float = 75.0,
        operator_id: str = "mission-gateway-client",
        scopes: Iterable[str] | None = None,
        tls: GatewayTlsClientConfig | None = None,
        allow_plaintext_loopback: bool = True,
        max_response_bytes: int = DEFAULT_GATEWAY_RESPONSE_BYTES,
        max_sse_event_bytes: int = 256 * 1024,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        validate_gateway_url(
            self._base_url,
            allow_plaintext_loopback=allow_plaintext_loopback,
        )
        self._api_token = api_token
        self._timeout = timeout
        self._max_response_bytes = validate_gateway_response_limit(
            max_response_bytes
        )
        if max_sse_event_bytes <= 0 or max_sse_event_bytes > 1024 * 1024:
            raise ValueError(
                "max_sse_event_bytes must be between 1 and 1048576."
            )
        self._max_sse_event_bytes = max_sse_event_bytes
        self._transport = GatewayHttpTransport(
            tls,
            allow_plaintext_loopback=allow_plaintext_loopback,
        )
        # Retained as source-compatible constructor parameters. Sending these
        # values as authorization headers would reintroduce caller-controlled
        # identity, so the server-authenticated principal always wins.
        _ = operator_id, scopes

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_health(self) -> dict[str, Any]:
        """GET /health for Mission Gateway process readiness."""

        return self._get("/health")

    def get_readiness(self) -> dict[str, Any]:
        """GET /readiness for evidence-backed robot and deployment status."""

        return self._get("/readiness")

    def preview_mission(
        self,
        command: str,
        *,
        target_robot: str | None = None,
    ) -> dict[str, Any]:
        """Create a server-owned immutable preview without dispatching it."""

        body: dict[str, Any] = {"command": command}
        if target_robot is not None:
            body["target_robot"] = target_robot
        return self._post("/plan-mission", body)

    def stream_preview_mission(
        self,
        command: str,
        *,
        target_robot: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Stream one Mission Agent preview run and its final result."""

        body: dict[str, Any] = {"command": command}
        if target_robot is not None:
            body["target_robot"] = target_robot
        return self._stream_post("/plan-mission/stream", body)

    def answer_planning_clarification(
        self,
        planning_session_id: str,
        answer: str,
    ) -> dict[str, Any]:
        """Answer one Gateway-owned Mission Agent planning question."""

        return self._post(
            "/plan-mission/clarification",
            {
                "planning_session_id": planning_session_id,
                "answer": answer,
            },
        )

    def stream_planning_clarification(
        self,
        planning_session_id: str,
        answer: str,
    ) -> Iterator[dict[str, Any]]:
        """Stream planning resumed from one authenticated clarification."""

        return self._stream_post(
            "/plan-mission/clarification/stream",
            {
                "planning_session_id": planning_session_id,
                "answer": answer,
            },
        )

    def confirm_plan(
        self,
        preview: Mapping[str, Any],
        *,
        operator_confirmed: bool,
    ) -> dict[str, Any]:
        """Explicitly consume one preview token and execute its sealed plan."""

        fields = (
            "artifact_id",
            "plan_token",
            "plan_digest",
            "status_version",
            "session_id",
            "robot_ids",
        )
        missing = [field for field in fields if field not in preview]
        if missing:
            raise ValueError(
                "Mission preview is missing confirmation fields: "
                + ", ".join(missing)
            )
        body = {field: preview[field] for field in fields}
        body["operator_confirmed"] = operator_confirmed
        return self._post("/plan-mission/confirm", body)

    def submit_mission(self, command: str, **kwargs: Any) -> dict[str, Any]:
        """Reject the removed direct-dispatch API before any network request."""

        _ = command, kwargs
        raise RuntimeError(
            "Direct mission submission is disabled; call preview_mission(), "
            "show the sealed plan to the operator, then call confirm_plan()."
        )

    def get_mission_trace(self, mission_id: str) -> dict[str, Any]:
        """GET /missions/{id}/trace"""
        return self._get(f"/missions/{mission_id}/trace")

    def get_mission_run(self, mission_id: str) -> dict[str, Any]:
        """GET /missions/{id}/run"""
        return self._get(f"/missions/{mission_id}/run")

    def get_mission_run_status(self, mission_id: str) -> dict[str, Any]:
        """GET a bounded Mission Run status projection without its full report."""

        return self._get(f"/missions/{mission_id}/run?view=status")

    def get_mission_report(self, mission_id: str) -> dict[str, Any]:
        """GET /missions/{id}/report"""
        return self._get(f"/missions/{mission_id}/report")

    def get_mission_events(self, mission_id: str) -> dict[str, Any]:
        """GET /missions/{id}/events"""
        return self._get(f"/missions/{mission_id}/events")

    def get_memory_tool_definitions(self, mission_id: str) -> dict[str, Any]:
        """GET /missions/{id}/memory/tools"""
        return self._get(f"/missions/{mission_id}/memory/tools")

    def call_memory_tool(
        self,
        mission_id: str,
        *,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /missions/{id}/memory/tools/call"""
        return self._post(
            f"/missions/{mission_id}/memory/tools/call",
            {"name": name, "arguments": arguments or {}},
        )

    def get_memory_lifecycle(self, mission_id: str) -> dict[str, Any]:
        """GET /missions/{id}/memory/lifecycle"""
        return self._get(f"/missions/{mission_id}/memory/lifecycle")

    def get_memory_audit(self, mission_id: str, *, limit: int = 200) -> dict[str, Any]:
        """GET /missions/{id}/memory/audit"""
        return self._get(f"/missions/{mission_id}/memory/audit?limit={limit}")

    def archive_memory(self, mission_id: str, *, reason: str) -> dict[str, Any]:
        """POST /missions/{id}/memory/archive"""
        return self._post(f"/missions/{mission_id}/memory/archive", {"reason": reason})

    def delete_memory_audit(
        self,
        mission_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """POST /missions/{id}/memory/delete with an exact-ID confirmation."""
        return self._post(
            f"/missions/{mission_id}/memory/delete",
            {"reason": reason, "confirmation": mission_id},
        )

    def list_reusable_knowledge(
        self,
        *,
        knowledge_type: str | None = None,
        tags: Iterable[str] = (),
        limit: int = 100,
    ) -> dict[str, Any]:
        """GET /memory/knowledge with exact structured filters."""
        from urllib.parse import urlencode

        params: list[tuple[str, str]] = [("limit", str(limit))]
        if knowledge_type is not None:
            params.append(("knowledge_type", knowledge_type))
        params.extend(("tag", tag) for tag in tags)
        return self._get(f"/memory/knowledge?{urlencode(params)}")

    def approve_reusable_knowledge(self, **kwargs: Any) -> dict[str, Any]:
        """POST /memory/knowledge/approve"""
        return self._post("/memory/knowledge/approve", kwargs)

    def revoke_reusable_knowledge(
        self,
        knowledge_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """POST /memory/knowledge/{id}/revoke"""
        return self._post(
            f"/memory/knowledge/{knowledge_id}/revoke",
            {"reason": reason},
        )

    def sync_robot_memory(
        self,
        mission_id: str,
        *,
        robot_id: str | None = None,
        batch_limit: int = 200,
        max_batches: int = 10,
    ) -> dict[str, Any]:
        """POST /missions/{id}/memory/sync"""
        body: dict[str, Any] = {
            "batch_limit": batch_limit,
            "max_batches": max_batches,
        }
        if robot_id is not None:
            body["robot_id"] = robot_id
        return self._post(f"/missions/{mission_id}/memory/sync", body)

    def cancel_mission(self, mission_id: str) -> dict[str, Any]:
        """POST /missions/{id}/cancel"""
        return self._post(f"/missions/{mission_id}/cancel", {})

    def pause_mission(self, mission_id: str) -> dict[str, Any]:
        """POST /missions/{id}/pause"""
        return self._post(f"/missions/{mission_id}/pause", {})

    def resume_mission(self, mission_id: str) -> dict[str, Any]:
        """POST /missions/{id}/resume"""
        return self._post(f"/missions/{mission_id}/resume", {})

    def correct_mission(
        self,
        mission_id: str,
        correction: str,
        *,
        context: str | None = None,
        robot_id: str | None = None,
        subtask_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /missions/{id}/corrections"""
        body: dict[str, Any] = {"correction": correction}
        if context is not None:
            body["context"] = context
        if robot_id is not None:
            body["robot_id"] = robot_id
        if subtask_id is not None:
            body["subtask_id"] = subtask_id
        return self._post(f"/missions/{mission_id}/corrections", body)

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
            with self._transport.open(req, timeout=effective_timeout) as resp:
                for event in _iter_sse_events(
                    resp,
                    max_event_bytes=self._max_sse_event_bytes,
                ):
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
        bounded_max_events = min(
            max_events if max_events is not None else 1_000,
            10_000,
        )
        if bounded_max_events <= 0:
            raise ValueError("max_events must be positive.")
        for event in self.stream_mission_events(
            mission_id,
            after_sequence=after_sequence,
            max_events=bounded_max_events,
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

    def _stream_post(
        self,
        path: str,
        body: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = self._headers()
        headers["Accept"] = "text/event-stream"
        req = request.Request(
            f"{self._base_url}{path}",
            data=data,
            method="POST",
            headers=headers,
        )
        return self._do_stream_request(req)

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self._api_token is not None:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    def _do_request(self, req: request.Request) -> dict[str, Any]:
        try:
            with self._transport.open(req, timeout=self._timeout) as resp:
                raw = read_bounded_gateway_response(
                    resp,
                    max_bytes=self._max_response_bytes,
                )
                value = json.loads(raw.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError(
                        "Mission Gateway response must be a JSON object."
                    )
                return value
        except HTTPError as exc:
            try:
                raw = read_bounded_gateway_response(
                    exc,
                    max_bytes=self._max_response_bytes,
                )
            except (OSError, ValueError):
                raw = b""
            payload = _decode_gateway_error_payload(raw)
            reason = str(
                getattr(exc, "reason", None)
                or getattr(exc, "msg", None)
                or "Mission Gateway request failed."
            )
            raise MissionGatewayRequestError(
                url=str(exc.geturl() or req.full_url),
                status_code=int(exc.code),
                reason=reason,
                headers=exc.headers,
                body=raw,
                payload=payload,
            ) from None

    def _do_stream_request(
        self,
        req: request.Request,
    ) -> Iterator[dict[str, Any]]:
        try:
            with self._transport.open(req, timeout=self._timeout) as resp:
                content_type = str(
                    resp.headers.get("Content-Type") or ""
                ).lower()
                if "text/event-stream" not in content_type:
                    raise MissionGatewayStreamUnsupported(
                        url=req.full_url,
                        content_type=content_type,
                    )
                yield from _iter_sse_events(
                    resp,
                    max_event_bytes=self._max_sse_event_bytes,
                )
        except HTTPError as exc:
            try:
                raw = read_bounded_gateway_response(
                    exc,
                    max_bytes=self._max_response_bytes,
                )
            except (OSError, ValueError):
                raw = b""
            payload = _decode_gateway_error_payload(raw)
            reason = str(
                getattr(exc, "reason", None)
                or getattr(exc, "msg", None)
                or "Mission Gateway request failed."
            )
            raise MissionGatewayRequestError(
                url=str(exc.geturl() or req.full_url),
                status_code=int(exc.code),
                reason=reason,
                headers=exc.headers,
                body=raw,
                payload=payload,
            ) from None


def _decode_gateway_error_payload(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _gateway_error_message(
    payload: Mapping[str, Any],
    *,
    fallback: str,
) -> str:
    nested_error = payload.get("error")
    candidates: list[Any] = [payload.get("message")]
    if isinstance(nested_error, Mapping):
        candidates.append(nested_error.get("message"))
    else:
        candidates.append(nested_error)
    candidates.append(fallback)
    for candidate in candidates:
        normalized = _bounded_gateway_error_text(candidate, limit=2048)
        if normalized is not None:
            return normalized
    return "Mission Gateway request failed."


def _gateway_error_code(
    payload: Mapping[str, Any],
    *,
    status_code: int,
) -> str:
    nested_error = payload.get("error")
    candidates: list[Any] = [payload.get("error_code"), payload.get("code")]
    if isinstance(nested_error, Mapping):
        candidates.append(nested_error.get("code"))
    for candidate in candidates:
        normalized = _bounded_gateway_error_text(candidate, limit=128)
        if normalized is not None:
            return normalized
    return f"http_{status_code}"


def _bounded_gateway_error_text(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized[:limit] if normalized else None


# ---------------------------------------------------------------------------
# Module-level SSE parser
# ---------------------------------------------------------------------------


def _iter_sse_events(
    response: Any,
    *,
    max_event_bytes: int = 256 * 1024,
) -> Iterator[dict[str, Any]]:
    """Parse SSE event blocks from an HTTP response.

    Yields parsed data dicts for each complete event block.
    Handles: event:, id:, data:, comments (:), and blank-line delimiters.
    """
    current_event: dict[str, str] = {}
    data_lines: list[str] = []

    observed_event_bytes = 0
    while True:
        raw_line = response.readline(max_event_bytes + 1)
        if not raw_line:
            break
        observed_event_bytes += len(raw_line)
        if observed_event_bytes > max_event_bytes:
            raise ValueError("Mission Gateway SSE event exceeds size limit.")
        line = raw_line.decode("utf-8").rstrip("\r\n")

        if line == "":
            # Blank line = end of event block
            parsed = _finalize_sse_event(data_lines, current_event)
            if parsed is not None:
                yield parsed
            current_event = {}
            data_lines = []
            observed_event_bytes = 0
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
