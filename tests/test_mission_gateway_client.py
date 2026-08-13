"""Tests for MissionGatewayClient typed client and SSE cursor replay.

Tests cover:
1. MissionGatewayClient typed client methods
2. SSE id: field in StreamEvent.to_sse_format()
3. EventBus.get_recent_events() cursor query
4. Cursor replay in gateway SSE endpoints
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from queue import Empty as QueueEmpty, Queue
from typing import Any
from urllib import request
from urllib.error import HTTPError

import pytest

from fireclaw_core.mission.mission_gateway_client import (
    MissionGatewayClient,
    _iter_sse_events,
)
from fireclaw_core.monitoring.stream_events import EventBus, StreamEvent


# ---------------------------------------------------------------------------
# Tests: EventBus.get_recent_events()
# ---------------------------------------------------------------------------


class TestEventBusGetRecentEvents:
    def test_returns_events_after_sequence(self) -> None:
        bus = EventBus()
        events: list[StreamEvent] = []
        bus.subscribe(lambda e: events.append(e))
        for i in range(5):
            bus.publish(StreamEvent(event_type=f"ev.{i}", source="test"))
        recent = bus.get_recent_events(after_sequence=2)
        assert len(recent) == 3
        assert all(e.sequence > 2 for e in recent)

    def test_returns_empty_when_no_events_after_cursor(self) -> None:
        bus = EventBus()
        bus.publish(StreamEvent(event_type="ev.1", source="test"))
        recent = bus.get_recent_events(after_sequence=1)
        assert recent == []

    def test_returns_all_when_cursor_is_zero(self) -> None:
        bus = EventBus()
        for i in range(3):
            bus.publish(StreamEvent(event_type=f"ev.{i}", source="test"))
        recent = bus.get_recent_events(after_sequence=0)
        assert len(recent) == 3

    def test_respects_limit_parameter(self) -> None:
        bus = EventBus()
        for i in range(10):
            bus.publish(StreamEvent(event_type=f"ev.{i}", source="test"))
        recent = bus.get_recent_events(after_sequence=0, limit=5)
        assert len(recent) == 5
        # Should return the last 5
        assert recent[0].sequence == 6
        assert recent[-1].sequence == 10

    def test_returns_empty_on_fresh_bus(self) -> None:
        bus = EventBus()
        recent = bus.get_recent_events(after_sequence=0)
        assert recent == []


# ---------------------------------------------------------------------------
# Tests: StreamEvent.to_sse_format() includes id: field
# ---------------------------------------------------------------------------


class TestSSEEventId:
    def test_sse_format_includes_id_field(self) -> None:
        event = StreamEvent(
            event_type="task.completed",
            source="gateway",
            sequence=42,
        )
        sse = event.to_sse_format()
        lines = sse.split("\n")
        # Should have: event: <type>, id: <sequence>, data: <json>, empty line
        assert lines[0] == "event: task.completed"
        assert lines[1] == "id: 42"
        assert lines[2].startswith("data: ")
        assert lines[3] == ""

    def test_sse_format_id_is_sequence_number(self) -> None:
        event = StreamEvent(
            event_type="heartbeat",
            source="robot",
            sequence=999,
        )
        sse = event.to_sse_format()
        assert "id: 999\n" in sse

    def test_sse_format_with_zero_sequence(self) -> None:
        event = StreamEvent(
            event_type="test",
            source="test",
            sequence=0,
        )
        sse = event.to_sse_format()
        assert "id: 0\n" in sse

    def test_sse_parser_rejects_oversized_single_event(self) -> None:
        import io

        response = io.BytesIO(
            b"data: {\"value\":\"" + (b"x" * 64) + b"\"}\n\n"
        )

        with pytest.raises(ValueError, match="exceeds size limit"):
            list(_iter_sse_events(response, max_event_bytes=32))


# ---------------------------------------------------------------------------
# Helpers: lightweight HTTP test server for MissionGatewayClient
# ---------------------------------------------------------------------------


def _make_echo_handler(expected_responses: dict[str, dict[str, Any]], captured_requests: list):
    """Create a handler that returns canned JSON responses and captures requests."""

    class EchoHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def _handle(self, method: str) -> None:
            from urllib.parse import urlparse

            parsed = urlparse(self.path)
            path = parsed.path
            content_length = int(self.headers.get("Content-Length", "0"))
            body = None
            if content_length > 0:
                body = json.loads(self.rfile.read(content_length))
            captured_requests.append({
                "method": method,
                "path": path,
                "headers": dict(self.headers),
                "body": body,
            })
            key = f"{method} {path}"
            if key in expected_responses:
                resp = expected_responses[key]
            else:
                resp = {"status": 404, "body": {"error": "not found"}}
            status = resp.get("status", 200)
            body_bytes = json.dumps(resp.get("body", {})).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)

        def log_message(self, format: str, *args: object) -> None:
            return

    return EchoHandler


def _start_echo_server(responses: dict[str, dict[str, Any]]) -> tuple[HTTPServer, str, list]:
    captured: list = []
    handler_class = _make_echo_handler(responses, captured)
    server = HTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}", captured


# ---------------------------------------------------------------------------
# Tests: MissionGatewayClient
# ---------------------------------------------------------------------------


class TestMissionGatewayClient:
    def test_submit_mission(self) -> None:
        responses = {
            "POST /missions": {
                "status": 202,
                "body": {"status": "planned", "mission_id": "m-1"},
            },
        }
        server, base_url, captured = _start_echo_server(responses)
        try:
            client = MissionGatewayClient(base_url)
            result = client.submit_mission("去二楼搜索")
            assert result["status"] == "planned"
            assert result["mission_id"] == "m-1"
            # Verify the request was sent correctly
            req = captured[-1]
            assert req["body"]["command"] == "去二楼搜索"
        finally:
            server.shutdown()

    def test_get_mission_trace(self) -> None:
        responses = {
            "GET /missions/m-1/trace": {
                "status": 200,
                "body": {"mission_id": "m-1", "subtasks": []},
            },
        }
        server, base_url, _ = _start_echo_server(responses)
        try:
            client = MissionGatewayClient(base_url)
            result = client.get_mission_trace("m-1")
            assert result["mission_id"] == "m-1"
        finally:
            server.shutdown()

    def test_get_mission_events(self) -> None:
        responses = {
            "GET /missions/m-1/events": {
                "status": 200,
                "body": {"mission_id": "m-1", "events": []},
            },
        }
        server, base_url, _ = _start_echo_server(responses)
        try:
            client = MissionGatewayClient(base_url)
            result = client.get_mission_events("m-1")
            assert result["mission_id"] == "m-1"
        finally:
            server.shutdown()

    def test_cancel_mission(self) -> None:
        responses = {
            "POST /missions/m-1/cancel": {
                "status": 200,
                "body": {"status": "cancel_requested", "mission_id": "m-1"},
            },
        }
        server, base_url, _ = _start_echo_server(responses)
        try:
            client = MissionGatewayClient(base_url)
            result = client.cancel_mission("m-1")
            assert result["status"] == "cancel_requested"
        finally:
            server.shutdown()

    def test_request_approval(self) -> None:
        responses = {
            "POST /missions/m-1/approvals": {
                "status": 200,
                "body": {"status": "pending"},
            },
        }
        server, base_url, captured = _start_echo_server(responses)
        try:
            client = MissionGatewayClient(base_url)
            result = client.request_approval("m-1", action="request", risk_level="high")
            assert result["status"] == "pending"
            req = captured[-1]
            assert req["body"]["action"] == "request"
            assert req["body"]["risk_level"] == "high"
        finally:
            server.shutdown()

    def test_get_fleet_state(self) -> None:
        responses = {
            "GET /fleet/state": {
                "status": 200,
                "body": {"entries": [{"robot_id": "robot-1"}]},
            },
        }
        server, base_url, _ = _start_echo_server(responses)
        try:
            client = MissionGatewayClient(base_url)
            result = client.get_fleet_state()
            assert len(result["entries"]) == 1
            assert result["entries"][0]["robot_id"] == "robot-1"
        finally:
            server.shutdown()

    def test_get_health(self) -> None:
        responses = {
            "GET /health": {
                "status": 200,
                "body": {
                    "schema_version": 1,
                    "status": "ok",
                    "service": "mission_gateway",
                },
            },
        }
        server, base_url, _ = _start_echo_server(responses)
        try:
            client = MissionGatewayClient(base_url)
            result = client.get_health()
            assert result["service"] == "mission_gateway"
        finally:
            server.shutdown()

    def test_get_fleet_doctor(self) -> None:
        responses = {
            "GET /fleet/doctor": {
                "status": 200,
                "body": {"status": "healthy", "findings": []},
            },
        }
        server, base_url, _ = _start_echo_server(responses)
        try:
            client = MissionGatewayClient(base_url)
            result = client.get_fleet_doctor()
            assert result["status"] == "healthy"
        finally:
            server.shutdown()

    def test_sends_auth_headers(self) -> None:
        """Verify only the authenticated Bearer credential is sent."""
        responses = {
            "GET /fleet/state": {
                "status": 200,
                "body": {"entries": []},
            },
        }
        server, base_url, captured = _start_echo_server(responses)
        try:
            client = MissionGatewayClient(base_url, api_token="test-token-123")
            client.get_fleet_state()
            req = captured[-1]
            assert req["headers"].get("Authorization") == "Bearer test-token-123"
            assert "X-Operator-Scopes" not in req["headers"]
            assert "X-Operator-Id" not in req["headers"]
        finally:
            server.shutdown()

    def test_no_auth_headers_when_no_token(self) -> None:
        """Verify no Authorization header when api_token is None."""
        responses = {
            "GET /fleet/state": {
                "status": 200,
                "body": {"entries": []},
            },
        }
        server, base_url, captured = _start_echo_server(responses)
        try:
            client = MissionGatewayClient(base_url)
            client.get_fleet_state()
            req = captured[-1]
            assert "Authorization" not in req["headers"]
            assert "X-Operator-Scopes" not in req["headers"]
            assert "X-Operator-Id" not in req["headers"]
        finally:
            server.shutdown()

    def test_submit_mission_passes_extra_kwargs(self) -> None:
        """Verify extra kwargs are passed in the POST body."""
        responses = {
            "POST /missions": {
                "status": 202,
                "body": {"status": "planned", "mission_id": "m-2"},
            },
        }
        server, base_url, captured = _start_echo_server(responses)
        try:
            client = MissionGatewayClient(base_url)
            result = client.submit_mission(
                "搜索",
                session_id="sess-1",
                use_scheduler=False,
            )
            req = captured[-1]
            assert req["body"]["command"] == "搜索"
            assert req["body"]["session_id"] == "sess-1"
            assert req["body"]["use_scheduler"] is False
        finally:
            server.shutdown()


# ---------------------------------------------------------------------------
# Tests: SSE cursor replay in FireClawGateway
# ---------------------------------------------------------------------------


def test_client_approval_pending_and_resolve_token_requests() -> None:
    sent: list[tuple[str, dict]] = []

    class FakeClient(MissionGatewayClient):
        def _post(self, path: str, body: dict) -> dict:
            sent.append((path, body))
            return {"status": "ok"}

    client = FakeClient("http://localhost")

    client.get_pending_approvals("mission-1")
    client.resolve_approval_token("mission-1", "raw-token-abc")

    assert sent == [
        ("/missions/mission-1/approvals", {"action": "pending"}),
        ("/missions/mission-1/approvals", {"action": "resolve_token", "approval_token": "raw-token-abc"}),
    ]


class TestSSECursorReplayGateway:
    def test_sse_includes_event_id(self) -> None:
        """Verify SSE output includes id: field via cursor replay."""
        from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
        import tempfile
        import socket as _socket
        from http.client import HTTPConnection

        tmpdir = tempfile.mkdtemp()
        config = GatewayConfig(
            port=0,
            memory_path=f"{tmpdir}/mem.jsonl",
            event_path=f"{tmpdir}/events.jsonl",
            task_queue_path=f"{tmpdir}/tasks.jsonl",
        )
        gw = FireClawGateway(config)
        gw.start()
        try:
            # Publish events to populate the ring buffer
            gw._event_bus.publish(StreamEvent(
                event_type="test.event",
                source="test",
            ))

            # Connect with after_sequence=0 to trigger replay of all buffered events
            host, port = gw._server.server_address
            conn = HTTPConnection(host, port, timeout=3)
            conn.request("GET", "/events/stream?after_sequence=0")
            resp = conn.getresponse()
            assert resp.status == 200
            # Read line-by-line to handle chunked SSE
            lines = []
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    line = resp.readline()
                    if not line:
                        break
                    lines.append(line.decode("utf-8"))
                    # Stop after we see a complete event (id + data + blank)
                    if any(l.startswith("id: ") for l in lines) and lines[-1].strip() == "":
                        break
                except _socket.timeout:
                    break
            raw = "".join(lines)
            # Should contain id: field
            assert "id: " in raw
            assert "event: test.event" in raw
            conn.close()
        finally:
            gw.stop()

    def test_sse_cursor_replay(self) -> None:
        """Verify after_sequence replays missed events."""
        from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
        import tempfile
        import socket as _socket
        from http.client import HTTPConnection

        tmpdir = tempfile.mkdtemp()
        config = GatewayConfig(
            port=0,
            memory_path=f"{tmpdir}/mem.jsonl",
            event_path=f"{tmpdir}/events.jsonl",
            task_queue_path=f"{tmpdir}/tasks.jsonl",
        )
        gw = FireClawGateway(config)
        gw.start()
        try:
            # Publish 5 events (will get sequences 1-5)
            for i in range(5):
                gw._event_bus.publish(StreamEvent(
                    event_type=f"ev.{i}",
                    source="test",
                ))

            host, port = gw._server.server_address
            conn = HTTPConnection(host, port, timeout=3)
            conn.request("GET", "/events/stream?after_sequence=2")
            resp = conn.getresponse()
            assert resp.status == 200
            # Read line-by-line: expect 3 replayed events (seq 3,4,5)
            event_lines = []
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    line = resp.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8")
                    if decoded.startswith("event: "):
                        event_lines.append(decoded.strip())
                    if len(event_lines) >= 3:
                        break
                except _socket.timeout:
                    break
            # Should contain events with sequence > 2 (ev.2, ev.3, ev.4)
            assert "event: ev.2" in event_lines
            assert "event: ev.3" in event_lines
            assert "event: ev.4" in event_lines
            # Should NOT contain ev.0 or ev.1 (sequence 1 and 2)
            assert "event: ev.0" not in event_lines
            assert "event: ev.1" not in event_lines
            conn.close()
        finally:
            gw.stop()

    def test_sse_cursor_no_duplicate_live(self) -> None:
        """Verify no duplicate events after replay + live stream."""
        from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
        import tempfile
        from http.client import HTTPConnection

        tmpdir = tempfile.mkdtemp()
        config = GatewayConfig(
            port=0,
            memory_path=f"{tmpdir}/mem.jsonl",
            event_path=f"{tmpdir}/events.jsonl",
            task_queue_path=f"{tmpdir}/tasks.jsonl",
        )
        gw = FireClawGateway(config)
        gw.start()
        try:
            # Publish initial events (sequences 1, 2, 3)
            for i in range(3):
                gw._event_bus.publish(StreamEvent(
                    event_type=f"initial.{i}",
                    source="test",
                ))

            host, port = gw._server.server_address
            seen_ids: set[int] = set()
            all_lines: list[str] = []

            def read_sse():
                conn = HTTPConnection(host, port, timeout=4)
                try:
                    conn.request("GET", "/events/stream?after_sequence=1")
                    resp = conn.getresponse()
                    raw = b""
                    while True:
                        try:
                            chunk = resp.read(1)
                        except TimeoutError:
                            break
                        if not chunk:
                            break
                        raw += chunk
                        if raw.endswith(b"\n\n"):
                            line = raw.decode("utf-8")
                            all_lines.append(line)
                            for l in line.split("\n"):
                                if l.startswith("id: "):
                                    seq = int(l[4:])
                                    assert seq not in seen_ids, f"Duplicate sequence {seq}"
                                    seen_ids.add(seq)
                            raw = b""
                finally:
                    conn.close()

            t = threading.Thread(target=read_sse, daemon=True)
            t.start()
            time.sleep(0.5)

            # Publish a new live event
            gw._event_bus.publish(StreamEvent(
                event_type="live.event",
                source="test",
            ))
            time.sleep(1.0)
            t.join(timeout=3)

            # Should have received replayed events (seq 2,3) + live (seq 4)
            assert len(seen_ids) >= 3
        finally:
            gw.stop()


# ---------------------------------------------------------------------------
# Tests: MissionGatewayClient with real gateway
# ---------------------------------------------------------------------------


class TestMissionGatewayClientIntegration:
    def test_client_submit_mission_real(self, tmp_path) -> None:
        """Test submit_mission against a real MissionGateway."""
        from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig
        from fireclaw_core.mission.mission_agent import MissionAgent
        from fireclaw_core.mission.mission_planner import (
            MissionPlan,
            MissionPlannerContext,
            MissionPlanningResult,
            MissionSubtask,
        )
        from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry

        registry = RobotRegistry([
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot-1.local:8765",
                capabilities=("search",),
            ),
        ])

        class FakeClient:
            def submit_task(self, entry, **kwargs):
                return {"status": "accepted", "task_id": f"t-{entry.robot_id}", "robot_id": entry.robot_id}
            def get_task_trace(self, entry, task_id):
                return {"status": "completed"}
            def cancel_task(self, entry, task_id, **kwargs):
                return {"status": "cancel_requested"}
            def get_events(self, entry, task_id=None, limit=100):
                return []
            def check_presence(self, entry):
                return {"robot_id": entry.robot_id, "online": True, "last_seen_at": "2026-01-01T00:00:00Z", "state": {}}

        class FakePlanner:
            def plan(self, command: str, *, context: MissionPlannerContext) -> MissionPlanningResult:
                available_ids = [e.robot_id for e in context.available_robots]
                if not available_ids:
                    return MissionPlanningResult(
                        status="no_robots",
                        message="No online robots available.",
                        intent=None,
                        plan=None,
                    )
                return MissionPlanningResult(
                    status="planned",
                    message="OK",
                    intent="search",
                    plan=MissionPlan(
                        intent="search",
                        command=command,
                        subtasks=[MissionSubtask(
                            robot_id=available_ids[0],
                            command=command,
                            floor=1,
                            capability_required="search",
                            execution_group=0,
                        )],
                    ),
                )

        agent = MissionAgent(
            registry=registry,
            subagent_client=FakeClient(),
            planner=FakePlanner(),
        )
        config = MissionGatewayConfig(port=0)
        gw = MissionGateway(config, mission_agent=agent, registry=registry, subagent_client=FakeClient())
        gw.start()
        try:
            client = MissionGatewayClient(gw.base_url)
            result = client.submit_mission("去二楼搜索", use_scheduler=False)
            assert result["status"] == "planned"
            assert "mission_id" in result
        finally:
            gw.stop()

    def test_client_get_fleet_state_real(self) -> None:
        """Test get_fleet_state against a real MissionGateway."""
        from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig
        from fireclaw_core.mission.mission_agent import MissionAgent
        from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry

        registry = RobotRegistry([
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot-1.local:8765",
                capabilities=("search",),
            ),
        ])

        class FakeClient:
            def check_presence(self, entry):
                return {"robot_id": entry.robot_id, "online": True, "last_seen_at": "2026-01-01T00:00:00Z", "state": {}}

        agent = MissionAgent(
            registry=registry,
            subagent_client=FakeClient(),
        )
        config = MissionGatewayConfig(port=0)
        gw = MissionGateway(config, mission_agent=agent, registry=registry, subagent_client=FakeClient())
        gw.start()
        try:
            client = MissionGatewayClient(gw.base_url)
            result = client.get_fleet_state()
            assert "entries" in result
            assert len(result["entries"]) == 1
            assert result["entries"][0]["robot_id"] == "robot-1"
        finally:
            gw.stop()

    def test_client_stream_mission_events_parses_sse(self) -> None:
        """stream_mission_events() parses SSE event/id/data blocks."""

        class SSEHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for i in range(1, 4):
                    event_data = json.dumps(
                        {"event_type": f"ev.{i}", "source": "test", "sequence": i, "payload": {}}
                    )
                    block = f"event: ev.{i}\nid: {i}\ndata: {event_data}\n\n"
                    self.wfile.write(block.encode())
                    self.wfile.flush()

            def log_message(self, format: str, *args: object) -> None:
                return

        server = HTTPServer(("127.0.0.1", 0), SSEHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        try:
            client = MissionGatewayClient(f"http://{host}:{port}")
            events = list(client.stream_mission_events("mission-1", max_events=3))
            assert len(events) == 3
            assert events[0]["sequence"] == 1
            assert events[1]["sequence"] == 2
            assert events[2]["sequence"] == 3
            assert events[0]["event_type"] == "ev.1"
        finally:
            server.shutdown()

    def test_client_stream_reconnect_with_after_sequence(self) -> None:
        """Verify reconnect by resuming from a given after_sequence."""
        from urllib.parse import urlparse, parse_qs

        class SSEHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                after = int(qs.get("after_sequence", [0])[0])
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for i in range(after + 1, after + 4):
                    event_data = json.dumps(
                        {"event_type": f"ev.{i}", "source": "test", "sequence": i, "payload": {}}
                    )
                    block = f"event: ev.{i}\nid: {i}\ndata: {event_data}\n\n"
                    self.wfile.write(block.encode())
                    self.wfile.flush()

            def log_message(self, format: str, *args: object) -> None:
                return

        server = HTTPServer(("127.0.0.1", 0), SSEHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        try:
            client = MissionGatewayClient(f"http://{host}:{port}")
            events1, last_seq = client.stream_mission_events_with_cursor("m-1", max_events=3)
            assert len(events1) == 3
            assert last_seq == 3
            events2, last_seq2 = client.stream_mission_events_with_cursor(
                "m-1", after_sequence=last_seq, max_events=3
            )
            assert len(events2) == 3
            assert events2[0]["sequence"] == 4
            assert last_seq2 == 6
        finally:
            server.shutdown()

    def test_client_auth_real(self) -> None:
        """Test client sends auth headers to real gateway."""
        from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig
        from fireclaw_core.mission.mission_agent import MissionAgent
        from fireclaw_core.agent.robot_registry import RobotRegistry

        agent = MissionAgent(
            registry=RobotRegistry([]),
            subagent_client=None,
        )
        config = MissionGatewayConfig(port=0, api_token="my-secret")
        gw = MissionGateway(config, mission_agent=agent, registry=RobotRegistry([]))
        gw.start()
        try:
            client = MissionGatewayClient(gw.base_url, api_token="my-secret")
            result = client.get_fleet_state()
            assert "entries" in result

            # Without token should fail
            client_no_auth = MissionGatewayClient(gw.base_url)
            try:
                client_no_auth.get_fleet_state()
                assert False, "Expected HTTPError 401"
            except HTTPError as exc:
                assert exc.code == 401
        finally:
            gw.stop()
