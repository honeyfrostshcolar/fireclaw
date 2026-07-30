from __future__ import annotations

import json
import io
import shutil
import ssl
import subprocess
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib import request
from urllib.error import URLError

import pytest

from fireclaw_core.agent.robot_registry import RobotRegistryEntry
from fireclaw_core.agent.robot_registry import RobotRegistry
from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.gateway.transport import (
    GatewayResponseTooLarge,
    GatewayHttpTransport,
    GatewayTlsClientConfig,
    GatewayTlsServerConfig,
    create_gateway_http_server,
    read_bounded_gateway_response,
    validate_gateway_tls_server_config,
)
from fireclaw_core.mission.mission_gateway_client import MissionGatewayClient
from fireclaw_core.mission.mission_gateway import (
    MissionGateway,
    MissionGatewayConfig,
)
from fireclaw_core.subagent.subagent_client import RobotSubagentClient


@dataclass(frozen=True)
class _CertificateBundle:
    ca_cert: Path
    server_cert: Path
    server_key: Path
    client_cert: Path
    client_key: Path


@pytest.fixture()
def certificate_bundle(tmp_path: Path) -> _CertificateBundle:
    if shutil.which("openssl") is None:
        pytest.skip("openssl is required for TLS integration tests")

    ca_key = tmp_path / "ca.key"
    ca_cert = tmp_path / "ca.crt"
    server_key = tmp_path / "server.key"
    server_csr = tmp_path / "server.csr"
    server_cert = tmp_path / "server.crt"
    client_key = tmp_path / "client.key"
    client_csr = tmp_path / "client.csr"
    client_cert = tmp_path / "client.crt"
    server_ext = tmp_path / "server.ext"
    client_ext = tmp_path / "client.ext"
    server_ext.write_text(
        "subjectAltName=DNS:localhost\n"
        "extendedKeyUsage=serverAuth\n",
        encoding="utf-8",
    )
    client_ext.write_text("extendedKeyUsage=clientAuth\n", encoding="utf-8")

    _openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=FireClaw Test CA",
        "-keyout",
        str(ca_key),
        "-out",
        str(ca_cert),
    )
    _openssl(
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        "/CN=localhost",
        "-keyout",
        str(server_key),
        "-out",
        str(server_csr),
    )
    _sign_certificate(
        csr=server_csr,
        output=server_cert,
        ca_cert=ca_cert,
        ca_key=ca_key,
        extensions=server_ext,
    )
    _openssl(
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        "/CN=mission-gateway-test-client",
        "-keyout",
        str(client_key),
        "-out",
        str(client_csr),
    )
    _sign_certificate(
        csr=client_csr,
        output=client_cert,
        ca_cert=ca_cert,
        ca_key=ca_key,
        extensions=client_ext,
    )
    return _CertificateBundle(
        ca_cert=ca_cert,
        server_cert=server_cert,
        server_key=server_key,
        client_cert=client_cert,
        client_key=client_key,
    )


class _JsonHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps(
            {
                "status": "ok",
                "robot_state": {"robot_id": "robot-1"},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class _MemoryResponse(io.BytesIO):
    def __init__(self, body: bytes, *, content_length: str | None = None):
        super().__init__(body)
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length


def test_gateway_response_reader_enforces_declared_and_streamed_limits() -> None:
    with pytest.raises(GatewayResponseTooLarge):
        read_bounded_gateway_response(
            _MemoryResponse(b"", content_length="65"),
            max_bytes=64,
        )
    with pytest.raises(GatewayResponseTooLarge):
        read_bounded_gateway_response(
            _MemoryResponse(b"x" * 65),
            max_bytes=64,
        )


def test_gateway_transport_disables_proxy_and_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers: list[object] = []

    class _Opener:
        def open(self, request_value, *, timeout):
            return _MemoryResponse(b"{}")

    def _build_opener(*values):
        handlers.extend(values)
        return _Opener()

    monkeypatch.setattr(request, "build_opener", _build_opener)
    transport = GatewayHttpTransport()

    transport.open(
        request.Request("http://127.0.0.1:8765/state"),
        timeout=1,
    )

    proxy_handler = next(
        item for item in handlers if isinstance(item, request.ProxyHandler)
    )
    redirect_handler = next(
        item
        for item in handlers
        if isinstance(item, request.HTTPRedirectHandler)
    )
    assert proxy_handler.proxies == {}
    assert (
        redirect_handler.redirect_request(
            request.Request("http://127.0.0.1/a"),
            None,
            302,
            "Found",
            {},
            "http://127.0.0.1/b",
        )
        is None
    )


def test_remote_plaintext_client_is_rejected_before_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def _unexpected_urlopen(*args: object, **kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("network call must not happen")

    monkeypatch.setattr(request, "urlopen", _unexpected_urlopen)
    transport = GatewayHttpTransport()

    with pytest.raises(ValueError, match="require HTTPS"):
        transport.open(
            request.Request(
                "http://192.0.2.10:8765/state",
                headers={"Authorization": "Bearer must-not-leak"},
            ),
            timeout=1,
        )

    assert called is False


def test_non_loopback_server_requires_tls_even_with_application_auth() -> None:
    with pytest.raises(ValueError, match="requires TLS"):
        validate_gateway_tls_server_config(
            "0.0.0.0",
            GatewayTlsServerConfig(),
        )


def test_tls_configuration_failure_does_not_fall_back_to_http(
    tmp_path: Path,
) -> None:
    cert_file = tmp_path / "server.crt"
    cert_file.write_text("not a certificate", encoding="utf-8")

    with pytest.raises(ValueError, match="key_file"):
        create_gateway_http_server(
            ("127.0.0.1", 0),
            _JsonHandler,
            tls=GatewayTlsServerConfig(
                enabled=True,
                cert_file=str(cert_file),
            ),
        )


def test_partial_client_mtls_configuration_fails_before_request(
    tmp_path: Path,
) -> None:
    cert_file = tmp_path / "client.crt"
    cert_file.write_text("not used", encoding="utf-8")

    with pytest.raises(ValueError, match="both cert_file and key_file"):
        MissionGatewayClient(
            "https://robot-1.example:8765",
            tls=GatewayTlsClientConfig(cert_file=str(cert_file)),
        )


def test_verified_https_is_shared_by_mission_and_robot_clients(
    certificate_bundle: _CertificateBundle,
) -> None:
    server, thread = _start_test_server(certificate_bundle)
    host, port = server.server_address
    base_url = f"https://localhost:{port}"
    tls = GatewayTlsClientConfig(ca_file=str(certificate_bundle.ca_cert))
    try:
        mission_result = MissionGatewayClient(
            base_url,
            tls=tls,
        ).get_fleet_state()
        robot_result = RobotSubagentClient(tls=tls).get_state(
            RobotRegistryEntry(robot_id="robot-1", base_url=base_url)
        )
    finally:
        _stop_test_server(server, thread)

    assert host == "127.0.0.1"
    assert mission_result["status"] == "ok"
    assert robot_result["robot_state"]["robot_id"] == "robot-1"


def test_robot_gateway_listener_uses_verified_https(
    tmp_path: Path,
    certificate_bundle: _CertificateBundle,
) -> None:
    tls_server = _server_tls(certificate_bundle)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
            tls=tls_server,
        )
    )
    gateway.start()
    try:
        result = RobotSubagentClient(
            tls=GatewayTlsClientConfig(
                ca_file=str(certificate_bundle.ca_cert),
            )
        ).get_state(
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url=gateway.base_url.replace("127.0.0.1", "localhost"),
            )
        )
    finally:
        gateway.stop()

    assert gateway.base_url.startswith("https://")
    assert result["robot_state"]["robot_id"] == "robot-1"


def test_mission_gateway_listener_uses_verified_https(
    certificate_bundle: _CertificateBundle,
) -> None:
    mission_agent = type(
        "_MissionAgentStub",
        (),
        {"consolidation_coordinator": None},
    )()
    gateway = MissionGateway(
        MissionGatewayConfig(
            host="127.0.0.1",
            port=0,
            tls=_server_tls(certificate_bundle),
        ),
        mission_agent=mission_agent,
        registry=RobotRegistry([]),
    )
    gateway.start()
    try:
        result = MissionGatewayClient(
            gateway.base_url.replace("127.0.0.1", "localhost"),
            tls=GatewayTlsClientConfig(
                ca_file=str(certificate_bundle.ca_cert),
            ),
        ).get_fleet_state()
    finally:
        gateway.stop()

    assert gateway.base_url.startswith("https://")
    assert result == {"entries": []}


def test_unknown_ca_is_rejected(
    certificate_bundle: _CertificateBundle,
) -> None:
    server, thread = _start_test_server(certificate_bundle)
    _, port = server.server_address
    try:
        with pytest.raises(URLError):
            MissionGatewayClient(
                f"https://localhost:{port}",
            ).get_fleet_state()
    finally:
        _stop_test_server(server, thread)


def test_certificate_hostname_mismatch_is_rejected(
    certificate_bundle: _CertificateBundle,
) -> None:
    server, thread = _start_test_server(certificate_bundle)
    _, port = server.server_address
    try:
        with pytest.raises(URLError):
            MissionGatewayClient(
                f"https://127.0.0.1:{port}",
                tls=GatewayTlsClientConfig(
                    ca_file=str(certificate_bundle.ca_cert),
                ),
            ).get_fleet_state()
    finally:
        _stop_test_server(server, thread)


def test_mtls_rejects_missing_client_certificate_and_accepts_trusted_one(
    certificate_bundle: _CertificateBundle,
) -> None:
    server, thread = _start_test_server(
        certificate_bundle,
        require_client_cert=True,
    )
    _, port = server.server_address
    base_url = f"https://localhost:{port}"
    try:
        with pytest.raises((URLError, ConnectionError, ssl.SSLError)):
            MissionGatewayClient(
                base_url,
                tls=GatewayTlsClientConfig(
                    ca_file=str(certificate_bundle.ca_cert),
                ),
            ).get_fleet_state()

        result = MissionGatewayClient(
            base_url,
            tls=GatewayTlsClientConfig(
                ca_file=str(certificate_bundle.ca_cert),
                cert_file=str(certificate_bundle.client_cert),
                key_file=str(certificate_bundle.client_key),
            ),
        ).get_fleet_state()
    finally:
        _stop_test_server(server, thread)

    assert result["status"] == "ok"


def _start_test_server(
    bundle: _CertificateBundle,
    *,
    require_client_cert: bool = False,
):
    server = create_gateway_http_server(
        ("127.0.0.1", 0),
        _JsonHandler,
        tls=_server_tls(
            bundle,
            require_client_cert=require_client_cert,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _server_tls(
    bundle: _CertificateBundle,
    *,
    require_client_cert: bool = False,
) -> GatewayTlsServerConfig:
    return GatewayTlsServerConfig(
        enabled=True,
        cert_file=str(bundle.server_cert),
        key_file=str(bundle.server_key),
        ca_file=str(bundle.ca_cert) if require_client_cert else None,
        require_client_cert=require_client_cert,
    )


def _stop_test_server(server, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _sign_certificate(
    *,
    csr: Path,
    output: Path,
    ca_cert: Path,
    ca_key: Path,
    extensions: Path,
) -> None:
    _openssl(
        "x509",
        "-req",
        "-days",
        "1",
        "-in",
        str(csr),
        "-CA",
        str(ca_cert),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-out",
        str(output),
        "-extfile",
        str(extensions),
    )


def _openssl(*args: str) -> None:
    subprocess.run(
        ["openssl", *args],
        check=True,
        capture_output=True,
        text=True,
    )
