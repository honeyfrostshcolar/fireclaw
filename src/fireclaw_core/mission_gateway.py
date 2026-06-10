from __future__ import annotations

import json
import logging
import re
from queue import Empty as QueueEmpty, Queue
import threading
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse
from fireclaw_core.approval_relay import ApprovalRelay
from fireclaw_core.approval_runtime import ApprovalRuntime
from fireclaw_core.method_scopes import authorize_method

from fireclaw_core.stream_events import EventBus, StreamEvent, TelemetryTracker

from fireclaw_core.control import OperatorContext, operator_from_payload
from fireclaw_core.fleet_doctor import FleetDoctor
from fireclaw_core.mission_agent import MissionAgent
from fireclaw_core.robot_registry import RobotRegistry
from fireclaw_core.subagent_client import RobotSubagentClient
from fireclaw_core.subagent_registry import JsonlSubagentRegistry
from fireclaw_core.task_registry import JsonlTaskRegistryStore

logger = logging.getLogger(__name__)


class _BadRequestError(Exception):
    """Raised when the request is malformed before JSON parsing."""

MISSION_TRACE_RE = re.compile(r"^/missions/([^/]+)/trace$")
MISSION_EVENTS_RE = re.compile(r"^/missions/([^/]+)/events$")
MISSION_CANCEL_RE = re.compile(r"^/missions/([^/]+)/cancel$")
MISSION_APPROVALS_RE = re.compile(r"^/missions/([^/]+)/approvals$")
MISSION_EVENTS_STREAM_RE = re.compile(r"^/missions/([^/]+)/events/stream$")


@dataclass(frozen=True)
class MissionGatewayConfig:
    host: str = "127.0.0.1"
    port: int = 8766
    api_token: str | None = None


class MissionGateway:
    def __init__(
        self,
        config: MissionGatewayConfig,
        *,
        mission_agent: MissionAgent,
        registry: RobotRegistry,
        subagent_client: RobotSubagentClient | None = None,
        approval_runtime: ApprovalRuntime | None = None,
        approval_relay: ApprovalRelay | None = None,
        plugin_runtime: Any | None = None,
        task_registry: JsonlTaskRegistryStore | None = None,
        subagent_registry: JsonlSubagentRegistry | None = None,
    ) -> None:
        self.config = config
        self.mission_agent = mission_agent
        self.registry = registry
        self.subagent_client = subagent_client or RobotSubagentClient()
        self.approval_runtime = approval_runtime
        self.approval_relay = approval_relay
        self.plugin_runtime = plugin_runtime
        self.task_registry = task_registry
        self.subagent_registry = subagent_registry
        self._approval_relays: dict[str, dict[str, Any]] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._event_bus = EventBus()
        self._telemetry = TelemetryTracker()

    @property
    def base_url(self) -> str:
        if self._server is not None:
            host, port = self._server.server_address
            return f"http://{host}:{port}"
        return f"http://{self.config.host}:{self.config.port}"

    def start(self) -> None:
        if self._server is not None:
            return
        handler_class = self._handler_class()
        self._server = ThreadingHTTPServer((self.config.host, self.config.port), handler_class)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        handler_class = self._handler_class()
        self._server = ThreadingHTTPServer((self.config.host, self.config.port), handler_class)
        self._server.serve_forever()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    # ------------------------------------------------------------------
    # Endpoint logic
    # ------------------------------------------------------------------

    def submit_mission(
        self,
        command: str,
        *,
        session_id: str | None = None,
        operator: OperatorContext | None = None,
        use_scheduler: bool = True,
    ) -> dict[str, Any]:
        operator_dict = operator.to_dict() if operator is not None else None
        result = self.mission_agent.plan_and_submit(
            command,
            session_id=session_id,
            operator=operator_dict,
            use_scheduler=use_scheduler,
        )
        mission_id = result.get("mission_id")
        if mission_id:
            self.publish_event(
                "mission.submitted",
                "mission-gateway",
                mission_id=mission_id,
                payload={"command": command, "status": result.get("status")},
            )
            if result.get("status") == "planned":
                self.publish_event(
                    "mission.planned",
                    "mission-gateway",
                    mission_id=mission_id,
                    payload={"intent": result.get("intent")},
                )
            for subtask in result.get("subtask_results", []):
                if subtask.get("status") == "accepted":
                    self.publish_event(
                        "mission.subtask_dispatched",
                        "mission-gateway",
                        mission_id=mission_id,
                        task_id=subtask.get("task_id"),
                        payload={
                            "robot_id": subtask.get("robot_id"),
                            "task_id": subtask.get("task_id"),
                            "status": subtask.get("status"),
                        },
                    )
        return result

    def get_mission_trace(self, mission_id: str) -> dict[str, Any]:
        return self.mission_agent.mission_trace(mission_id)

    def get_mission_events(
        self,
        mission_id: str,
        *,
        robot_id: str | None = None,
        event_type: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        return self.mission_agent.mission_events(
            mission_id,
            robot_id=robot_id,
            event_type=event_type,
            limit=limit,
        )

    def cancel_mission(
        self,
        mission_id: str,
        *,
        operator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self.mission_agent.cancel_mission(mission_id, operator=operator)
        status = result.get("status")
        if status == "cancel_requested":
            self.publish_event(
                "mission.cancel_requested",
                "mission-gateway",
                mission_id=mission_id,
                payload={"status": status},
            )
        elif status == "cancelled":
            self.publish_event(
                "mission.cancelled",
                "mission-gateway",
                mission_id=mission_id,
                payload={"status": status},
            )
        return result

    def handle_approval(
        self,
        mission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        action = payload.get("action")
        if action == "request":
            semantic_action = payload.get("semantic_action", "")
            if not semantic_action:
                semantic_action = _derive_action_from_command(payload.get("command", ""))
            result = self.mission_agent.request_approval(
                mission_id,
                action=semantic_action,
                risk_level=payload.get("risk_level", "low"),
                command=payload.get("command", ""),
            )
            if result.get("status") == "pending":
                self.publish_event(
                    "mission.approval_requested",
                    "mission-gateway",
                    mission_id=mission_id,
                    payload={
                        "request_id": result.get("request", {}).get("request_id"),
                        "action": semantic_action,
                        "risk_level": payload.get("risk_level", "low"),
                    },
                )
            # Apply tool approval hooks from plugin runtime
            approval_reasons: list[dict[str, str]] = []
            if result.get("status") == "pending" and self.plugin_runtime is not None:
                for effect in self.plugin_runtime.run_tool_approval_hooks(
                    "add_reason",
                    {"mission_id": mission_id, "action": semantic_action, "payload": dict(payload)},
                ):
                    reason = effect.get("effect", {}).get("reason")
                    if isinstance(reason, str) and reason:
                        approval_reasons.append({"plugin_id": str(effect.get("plugin_id", "")), "reason": reason})
            if approval_reasons:
                result = {**result, "approval_reasons": approval_reasons}
            if result.get("status") == "pending" and self.approval_runtime is not None:
                request = result.get("request")
                request_id = request.get("request_id") if isinstance(request, dict) else None
                if isinstance(request_id, str) and request_id:
                    relay = payload.get("relay")
                    if isinstance(relay, dict):
                        self._approval_relays[request_id] = {
                            "channel": str(relay.get("channel") or "console"),
                            "operator_id": str(relay.get("operator_id") or "unknown"),
                        }
                    raw_token, token = self.approval_runtime.create_token(request_id)
                    result = {
                        **result,
                        "approval_token": raw_token,
                        "token": _public_token_dict(token.to_dict()),
                    }
                    # Deliver to external relay if configured and relay metadata present
                    if self.approval_relay is not None and isinstance(relay, dict):
                        relay_meta = self._approval_relays.get(request_id, {})
                        try:
                            self.approval_relay.deliver_pending_approval(
                                request_id=request_id,
                                mission_id=mission_id,
                                action=semantic_action,
                                risk_level=payload.get("risk_level", "low"),
                                channel=relay_meta.get("channel", "console"),
                                operator_id=relay_meta.get("operator_id", "unknown"),
                            )
                        except Exception as exc:
                            logger.warning(
                                "Approval relay delivery failed for %s: %s",
                                request_id, exc,
                            )
                            result = {**result, "relay_error": str(exc)}
            return result
        if action == "pending":
            if self.approval_runtime is None:
                return {"status": "not_configured", "pending_approvals": []}
            self.approval_runtime.expire_stale()
            pending = [
                item
                for item in self.approval_runtime.pending_projection()
                if item.get("mission_id") == mission_id
            ]
            for item in pending:
                relay = self._approval_relays.get(str(item.get("request_id")))
                if relay is not None:
                    item["relay"] = dict(relay)
            return {"status": "pending", "pending_approvals": pending}
        if action == "resolve_token":
            if self.approval_runtime is None:
                return {"status": "not_configured"}
            token = payload.get("approval_token")
            if not isinstance(token, str) or not token:
                return {"status": "error", "message": "Field 'approval_token' is required for resolve_token action."}
            record = self.approval_runtime.resolve_token(token)
            if record is None or record.mission_id != mission_id:
                return {"status": "not_found"}
            return {"status": "resolved", "token": _public_token_dict(record.to_dict())}
        if action == "decide":
            request_id = payload.get("request_id", "")
            if not request_id:
                return {"status": "error", "message": "Field 'request_id' is required for decide action."}
            result = self.mission_agent.decide_approval(
                request_id,
                decision=payload.get("decision", ""),
                reason=payload.get("reason"),
            )
            if result.get("status") == "decided":
                self.publish_event(
                    "mission.approval_decided",
                    "mission-gateway",
                    mission_id=mission_id,
                    payload={
                        "request_id": request_id,
                        "decision": payload.get("decision", ""),
                    },
                )
            return result
        return {"status": "error", "message": f"Unknown approval action: {action}"}

    def fleet_state(self) -> dict[str, Any]:
        entries = self.registry.enabled_entries(include_stale=True)
        return {
            "entries": [
                {
                    **{
                        "robot_id": e.robot_id,
                        "base_url": e.base_url,
                        "capabilities": list(e.capabilities),
                        "zone": e.zone,
                        "enabled": e.enabled,
                    },
                    "is_online": self.registry.is_online(e.robot_id),
                    "is_stale": self.registry.is_stale(e.robot_id),
                    "last_seen_at": self.registry.get_last_seen_at(e.robot_id),
                }
                for e in entries
            ],
        }

    def fleet_doctor(self) -> dict[str, Any]:
        doctor = FleetDoctor(
            registry=self.registry,
            subagent_client=self.subagent_client,
            task_registry=self.task_registry,
            subagent_registry=self.subagent_registry,
        )
        findings = doctor.diagnose()
        return doctor.summary(findings)

    def publish_event(
        self,
        event_type: str,
        source: str,
        *,
        mission_id: str | None = None,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Publish an event to the mission-level EventBus (for SSE streaming)."""
        event = StreamEvent(
            event_type=event_type,
            source=source,
            mission_id=mission_id,
            task_id=task_id,
            payload=payload or {},
        )
        self._event_bus.publish(event)
        self._telemetry.record_event(event)

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------

    def _handler_class(self):
        gateway = self

        class MissionRequestHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if not gateway._check_auth(self):
                    return
                parsed = urlparse(self.path)
                scopes = _extract_scopes_from_header(self)
                result = authorize_method(f"GET {parsed.path}", scopes)
                if not result.allowed:
                    gateway._write_error(
                        self, HTTPStatus.FORBIDDEN,
                        f"Missing required scope: {result.missing_scope}",
                    )
                    return
                # SSE long-lived response — handle before normal dispatch
                stream_match = MISSION_EVENTS_STREAM_RE.match(parsed.path)
                if stream_match:
                    mission_id = stream_match.group(1)
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    # Cursor replay: parse Last-Event-ID header or after_sequence query param
                    after_seq: int | None = None
                    last_event_id = self.headers.get("Last-Event-ID")
                    if last_event_id is not None:
                        try:
                            after_seq = int(last_event_id)
                        except ValueError:
                            after_seq = None
                    else:
                        qs = parse_qs(parsed.query)
                        raw = qs.get("after_sequence", [None])[0]
                        if raw is not None:
                            try:
                                after_seq = int(raw)
                            except ValueError:
                                after_seq = None
                    # Track sequences already sent to avoid duplicates
                    sent_sequences: set[int] = set()
                    if after_seq is not None:
                        # Replay recent mission-filtered events from EventBus
                        for evt in gateway._event_bus.get_recent_events(after_sequence=after_seq):
                            if evt.mission_id == mission_id or evt.mission_id is None:
                                try:
                                    self.wfile.write(evt.to_sse_format().encode("utf-8"))
                                    self.wfile.flush()
                                    sent_sequences.add(evt.sequence)
                                except Exception:
                                    return
                    else:
                        # Send historical mission events as initial batch (no cursor)
                        try:
                            history = gateway.get_mission_events(mission_id, limit=200)
                            for evt in history.get("events", []):
                                se = StreamEvent(
                                    event_type=evt.get("type", "unknown"),
                                    source="mission-history",
                                    mission_id=mission_id,
                                    payload=evt.get("payload", evt),
                                )
                                self.wfile.write(se.to_sse_format().encode("utf-8"))
                            self.wfile.flush()
                        except Exception:
                            logging.getLogger(__name__).debug(
                                "Failed to send historical events for mission %s",
                                mission_id, exc_info=True,
                            )
                    # Stream live events (filtered by mission_id)
                    event_queue: Queue[StreamEvent | None] = Queue()
                    def _on_event(event: StreamEvent) -> None:
                        if event.mission_id == mission_id or event.mission_id is None:
                            event_queue.put(event)
                    token = gateway._event_bus.subscribe(_on_event)
                    try:
                        while True:
                            try:
                                event = event_queue.get(timeout=15.0)
                                if event is None:
                                    break
                                if event.sequence in sent_sequences:
                                    continue
                                self.wfile.write(event.to_sse_format().encode("utf-8"))
                                self.wfile.flush()
                            except QueueEmpty:
                                try:
                                    self.wfile.write(b": heartbeat\n\n")
                                    self.wfile.flush()
                                except Exception:
                                    break
                            except Exception:
                                break
                    finally:
                        gateway._event_bus.unsubscribe(token)
                    return
                gateway._handle_get(self)

            def do_POST(self) -> None:
                if not gateway._check_auth(self):
                    return
                parsed = urlparse(self.path)
                scopes = _extract_scopes_from_header(self)
                result = authorize_method(f"POST {parsed.path}", scopes)
                if not result.allowed:
                    gateway._write_error(
                        self, HTTPStatus.FORBIDDEN,
                        f"Missing required scope: {result.missing_scope}",
                    )
                    return
                gateway._handle_post(self)

            def log_message(self, format: str, *args: object) -> None:
                return

        return MissionRequestHandler

    def _check_auth(self, handler: BaseHTTPRequestHandler) -> bool:
        if self.config.api_token is None:
            return True
        auth_header = handler.headers.get("Authorization", "")
        if auth_header == f"Bearer {self.config.api_token}":
            return True
        body = json.dumps({"error": "Unauthorized"}, ensure_ascii=False).encode("utf-8")
        handler.send_response(HTTPStatus.UNAUTHORIZED)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return False

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/fleet/state":
            self._write_json(handler, HTTPStatus.OK, self.fleet_state())
            return
        if path == "/fleet/doctor":
            self._write_json(handler, HTTPStatus.OK, self.fleet_doctor())
            return

        trace_match = MISSION_TRACE_RE.match(path)
        if trace_match:
            mission_id = trace_match.group(1)
            self._write_json(handler, HTTPStatus.OK, self.get_mission_trace(mission_id))
            return

        events_match = MISSION_EVENTS_RE.match(path)
        if events_match:
            mission_id = events_match.group(1)
            self._write_json(
                handler,
                HTTPStatus.OK,
                self.get_mission_events(
                    mission_id,
                    robot_id=_first(query, "robot_id"),
                    event_type=_first(query, "event_type"),
                    limit=_int_query(query, "limit", 200),
                ),
            )
            return

        self._write_error(handler, HTTPStatus.NOT_FOUND, f"Unknown endpoint: {path}")

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        path = parsed.path
        try:
            payload = self._read_json(handler)

            if path == "/missions":
                command = payload.get("command")
                if not isinstance(command, str) or not command.strip():
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, "Field 'command' is required.")
                    return
                result = self.submit_mission(
                    command,
                    session_id=_optional_string(payload, "session_id"),
                    operator=operator_from_payload(payload.get("operator")),
                    use_scheduler=payload.get("use_scheduler", True),
                )
                status = _mission_submit_status(result)
                self._write_json(handler, status, result)
                return

            cancel_match = MISSION_CANCEL_RE.match(path)
            if cancel_match:
                mission_id = cancel_match.group(1)
                result = self.cancel_mission(
                    mission_id,
                    operator=payload.get("operator"),
                )
                status = HTTPStatus.NOT_FOUND if result.get("status") == "not_found" else HTTPStatus.OK
                self._write_json(handler, status, result)
                return

            approvals_match = MISSION_APPROVALS_RE.match(path)
            if approvals_match:
                mission_id = approvals_match.group(1)
                result = self.handle_approval(mission_id, payload)
                is_error = result.get("status") == "error"
                status = HTTPStatus.BAD_REQUEST if is_error else HTTPStatus.OK
                self._write_json(handler, status, result)
                return

            self._write_error(handler, HTTPStatus.NOT_FOUND, f"Unknown endpoint: {path}")
        except _BadRequestError as exc:
            self._write_error(handler, HTTPStatus.BAD_REQUEST, str(exc))
        except json.JSONDecodeError:
            self._write_error(handler, HTTPStatus.BAD_REQUEST, "Request body must be valid JSON.")
        except Exception:
            logger.exception("Unhandled error in POST %s", path)
            self._write_error(handler, HTTPStatus.INTERNAL_SERVER_ERROR, "Internal server error.")

    _MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB

    def _read_json(self, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        try:
            length = int(handler.headers.get("Content-Length", "0"))
        except (ValueError, TypeError):
            raise _BadRequestError("Invalid Content-Length header.")
        if length <= 0:
            return {}
        if length > self._MAX_BODY_BYTES:
            handler.rfile.read(length)
            raise _BadRequestError(
                f"Request body too large ({length} bytes, max {self._MAX_BODY_BYTES})."
            )
        raw = handler.rfile.read(length)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise json.JSONDecodeError("JSON body must be an object.", raw.decode("utf-8"), 0)
        return value

    def _write_json(
        self,
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _write_error(self, handler: BaseHTTPRequestHandler, status: HTTPStatus, message: str) -> None:
        self._write_json(handler, status, {"status": "error", "message": message})


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    return values[0]


def _int_query(query: dict[str, list[str]], key: str, default: int) -> int:
    value = _first(query, key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _derive_action_from_command(command: str) -> str:
    """Derive a short semantic action label from a natural-language command."""
    if not command:
        return "unknown"
    # Take the first meaningful verb phrase, capped at 40 chars
    action = command.strip().split("，")[0].split(",")[0].strip()
    return action[:40] if action else "unknown"


def _public_token_dict(token: dict[str, Any]) -> dict[str, Any]:
    public = dict(token)
    public.pop("token_hash", None)
    return public


def _mission_submit_status(result: dict[str, Any]) -> HTTPStatus:
    """Pick an HTTP status code for a mission submission result."""
    status = result.get("status")
    if status == "planned" and result.get("mission_id"):
        return HTTPStatus.ACCEPTED
    if status in ("no_planner", "no_robots", "unauthorized"):
        return HTTPStatus.BAD_REQUEST
    return HTTPStatus.ACCEPTED if result.get("mission_id") else HTTPStatus.BAD_REQUEST


def _extract_scopes_from_header(handler: BaseHTTPRequestHandler) -> set[str]:
    """Extract operator scopes from X-Operator-Scopes header or default to read-only."""
    scopes_header = handler.headers.get("X-Operator-Scopes", "")
    if scopes_header:
        return {s.strip() for s in scopes_header.split(",") if s.strip()}
    return {"state.read"}
