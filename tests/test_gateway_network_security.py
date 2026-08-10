from __future__ import annotations

from email.message import Message
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
import http.client

import pytest

from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.gateway.config import load_config
from fireclaw_core.gateway.network_security import (
    ConnectionBudget,
    GatewayAuthRateLimiter,
    GatewayNetworkPolicy,
    GatewayRequestBodyError,
    GatewayRequestGuard,
    gateway_network_policy_from_config,
    read_json_object_body,
)


class _ReadTrap:
    def read(self, size: int = -1) -> bytes:
        raise AssertionError(f"request body must not be read (size={size})")


class _Handler:
    def __init__(self, headers: Message, body: bytes = b"") -> None:
        self.headers = headers
        self.rfile = BytesIO(body)
        self.connection = None
        self.close_connection = False


def _headers(**values: str) -> Message:
    headers = Message()
    for name, value in values.items():
        headers.add_header(name.replace("_", "-"), value)
    return headers


def _robot_gateway(
    tmp_path: Path,
    *,
    api_token: str | None = None,
    network: GatewayNetworkPolicy | None = None,
) -> FireClawGateway:
    return FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            api_token=api_token,
            network=network or GatewayNetworkPolicy(),
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            runtime_state_path=str(tmp_path / "runtime.sqlite3"),
        )
    )


def _request(
    gateway: FireClawGateway,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    host, port = gateway._server.server_address  # type: ignore[union-attr]
    connection = http.client.HTTPConnection(host, port, timeout=2)
    connection.request(method, path, headers=headers or {})
    response = connection.getresponse()
    body = response.read()
    response_headers = dict(response.getheaders())
    status = response.status
    connection.close()
    return status, response_headers, body


def test_policy_rejects_inconsistent_connection_limits() -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        GatewayNetworkPolicy(
            max_connections=4,
            max_connections_per_ip=5,
        )
    with pytest.raises(ValueError, match="max_sse_connections"):
        GatewayNetworkPolicy(
            max_sse_connections=2,
            max_sse_connections_per_ip=3,
        )


def test_wildcard_listener_requires_explicit_host_allowlist() -> None:
    with pytest.raises(ValueError, match="allowed_hosts"):
        GatewayRequestGuard(
            GatewayNetworkPolicy(),
            configured_host="0.0.0.0",
            tls_enabled=True,
        )


def test_request_guard_checks_host_origin_and_body_before_dispatch() -> None:
    guard = GatewayRequestGuard(
        GatewayNetworkPolicy(max_body_bytes=8),
        configured_host="127.0.0.1",
        tls_enabled=False,
    )

    accepted = guard.admit(
        method="POST",
        headers=_headers(
            Host="127.0.0.1:8765",
            Origin="http://127.0.0.1:8765",
            Content_Length="8",
        ),
        server_host="127.0.0.1",
        server_port=8765,
    )
    assert accepted.allowed is True

    wrong_host = guard.admit(
        method="GET",
        headers=_headers(Host="attacker.example"),
        server_host="127.0.0.1",
        server_port=8765,
    )
    assert wrong_host.status == HTTPStatus.MISDIRECTED_REQUEST

    wrong_origin = guard.admit(
        method="GET",
        headers=_headers(
            Host="127.0.0.1:8765",
            Origin="https://attacker.example",
        ),
        server_host="127.0.0.1",
        server_port=8765,
    )
    assert wrong_origin.status == HTTPStatus.FORBIDDEN

    oversized = guard.admit(
        method="POST",
        headers=_headers(Host="127.0.0.1:8765", Content_Length="9"),
        server_host="127.0.0.1",
        server_port=8765,
    )
    assert oversized.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE


def test_body_limit_is_enforced_without_reading_declared_body() -> None:
    handler = _Handler(_headers(Content_Length="1024"))
    handler.rfile = _ReadTrap()

    with pytest.raises(GatewayRequestBodyError) as exc_info:
        read_json_object_body(
            handler,  # type: ignore[arg-type]
            GatewayNetworkPolicy(max_body_bytes=16),
        )

    assert exc_info.value.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert handler.close_connection is True


def test_body_reader_rejects_partial_json_body() -> None:
    handler = _Handler(
        _headers(Content_Length="10"),
        body=b"{}",
    )

    with pytest.raises(GatewayRequestBodyError, match="complete request body"):
        read_json_object_body(
            handler,  # type: ignore[arg-type]
            GatewayNetworkPolicy(),
        )

    assert handler.close_connection is True


def test_auth_rate_limiter_locks_and_recovers_client() -> None:
    current_time = 0.0
    policy = GatewayNetworkPolicy(
        auth_max_failures=2,
        auth_window_seconds=10,
        auth_lockout_seconds=30,
        auth_exempt_loopback=False,
    )
    limiter = GatewayAuthRateLimiter(
        policy,
        clock=lambda: current_time,
    )

    limiter.record_failure("192.0.2.10")
    assert limiter.check("192.0.2.10").allowed is True
    limiter.record_failure("192.0.2.10")
    blocked = limiter.check("192.0.2.10")
    assert blocked.allowed is False
    assert blocked.retry_after_seconds == 30

    current_time = 31.0
    assert limiter.check("192.0.2.10").allowed is True


def test_connection_budget_enforces_total_and_per_client_limits() -> None:
    budget = ConnectionBudget(total_limit=2, per_client_limit=1)

    assert budget.acquire("192.0.2.1") is True
    assert budget.acquire("192.0.2.1") is False
    assert budget.acquire("192.0.2.2") is True
    assert budget.acquire("192.0.2.3") is False

    budget.release("192.0.2.1")
    assert budget.acquire("192.0.2.3") is True


def test_network_config_parser_is_typed_and_normalizes_allowlists() -> None:
    policy = gateway_network_policy_from_config(
        {
            "max_body_bytes": 2048,
            "auth_exempt_loopback": False,
            "allowed_hosts": ["Robot.Local."],
            "allowed_origins": ["https://console.example:9443/"],
        }
    )

    assert policy.max_body_bytes == 2048
    assert policy.auth_exempt_loopback is False
    assert policy.allowed_hosts == ("robot.local",)
    assert policy.allowed_origins == ("https://console.example:9443",)

    with pytest.raises(ValueError, match="must be an integer"):
        gateway_network_policy_from_config({"max_body_bytes": "2048"})
    with pytest.raises(ValueError, match="Unknown Gateway network"):
        gateway_network_policy_from_config({"max_conections": 10})
    with pytest.raises(ValueError, match="must be a number"):
        gateway_network_policy_from_config(
            {"body_read_timeout_seconds": True}
        )


def test_config_loader_preserves_shared_network_table(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "fireclaw.toml"
    config_path.write_text(
        "[network]\n"
        "max_body_bytes = 2048\n"
        "allowed_hosts = [\"robot.local\"]\n",
        encoding="utf-8",
    )

    loaded = load_config(config_path)

    assert loaded["network"] == {
        "max_body_bytes": 2048,
        "allowed_hosts": ["robot.local"],
    }


def test_robot_gateway_rejects_oversized_declared_body_immediately(
    tmp_path: Path,
) -> None:
    gateway = _robot_gateway(
        tmp_path,
        network=GatewayNetworkPolicy(max_body_bytes=32),
    )
    gateway.start()
    try:
        host, port = gateway._server.server_address  # type: ignore[union-attr]
        connection = http.client.HTTPConnection(host, port, timeout=2)
        connection.putrequest("POST", "/tasks")
        connection.putheader("Content-Length", "1000000")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        response.read()
        connection.close()
    finally:
        gateway.stop()


def test_robot_gateway_rate_limits_failed_authentication(
    tmp_path: Path,
) -> None:
    gateway = _robot_gateway(
        tmp_path,
        api_token="correct-token",
        network=GatewayNetworkPolicy(
            auth_max_failures=2,
            auth_exempt_loopback=False,
        ),
    )
    gateway.start()
    try:
        headers = {"Authorization": "Bearer wrong-token"}
        assert _request(gateway, "GET", "/state", headers=headers)[0] == 401
        assert _request(gateway, "GET", "/state", headers=headers)[0] == 401
        status, response_headers, _ = _request(
            gateway,
            "GET",
            "/state",
            headers=headers,
        )
        assert status == HTTPStatus.TOO_MANY_REQUESTS
        assert int(response_headers["Retry-After"]) > 0
    finally:
        gateway.stop()


def test_robot_gateway_rejects_cross_origin_request(
    tmp_path: Path,
) -> None:
    gateway = _robot_gateway(tmp_path)
    gateway.start()
    try:
        status, _, _ = _request(
            gateway,
            "GET",
            "/state",
            headers={"Origin": "https://attacker.example"},
        )
        assert status == HTTPStatus.FORBIDDEN
    finally:
        gateway.stop()


def test_robot_gateway_limits_concurrent_sse_connections(
    tmp_path: Path,
) -> None:
    gateway = _robot_gateway(
        tmp_path,
        network=GatewayNetworkPolicy(
            max_sse_connections=1,
            max_sse_connections_per_ip=1,
        ),
    )
    gateway.start()
    first_connection: http.client.HTTPConnection | None = None
    try:
        host, port = gateway._server.server_address  # type: ignore[union-attr]
        first_connection = http.client.HTTPConnection(host, port, timeout=2)
        first_connection.request("GET", "/events/stream")
        first_response = first_connection.getresponse()
        assert first_response.status == HTTPStatus.OK

        status, _, body = _request(gateway, "GET", "/events/stream")
        assert status == HTTPStatus.TOO_MANY_REQUESTS
        assert b"SSE connection limit exceeded" in body
    finally:
        if first_connection is not None:
            first_connection.close()
        gateway.stop()
