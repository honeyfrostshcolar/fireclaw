"""Secure transport primitives shared by FireClaw Gateway servers and clients."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import request
from urllib.parse import urlparse

from fireclaw_core.gateway.auth import is_loopback_host
from fireclaw_core.gateway.network_security import (
    ConnectionBudget,
    GatewayNetworkPolicy,
)


@dataclass(frozen=True)
class GatewayTlsServerConfig:
    """TLS identity and optional client-certificate policy for a Gateway."""

    enabled: bool = False
    cert_file: str | None = None
    key_file: str | None = None
    ca_file: str | None = None
    require_client_cert: bool = False


@dataclass(frozen=True)
class GatewayTlsClientConfig:
    """Trust roots and optional mTLS identity for a Gateway client."""

    ca_file: str | None = None
    cert_file: str | None = None
    key_file: str | None = None


DEFAULT_GATEWAY_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_GATEWAY_RESPONSE_BYTES = 16 * 1024 * 1024


class GatewayResponseTooLarge(ValueError):
    """A Gateway peer exceeded the admitted response body budget."""


class _NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class GatewayHttpTransport:
    """URL policy and verified HTTPS execution for Gateway clients."""

    def __init__(
        self,
        tls: GatewayTlsClientConfig | None = None,
        *,
        allow_plaintext_loopback: bool = True,
    ) -> None:
        self.tls = tls or GatewayTlsClientConfig()
        self.allow_plaintext_loopback = allow_plaintext_loopback
        _validate_gateway_tls_client_config(self.tls)
        self._ssl_context: ssl.SSLContext | None = (
            build_gateway_client_ssl_context(self.tls)
            if any(
                (
                    self.tls.ca_file,
                    self.tls.cert_file,
                    self.tls.key_file,
                )
            )
            else None
        )

    def open(
        self,
        request_value: request.Request,
        *,
        timeout: float,
    ) -> Any:
        scheme = validate_gateway_url(
            request_value.full_url,
            allow_plaintext_loopback=self.allow_plaintext_loopback,
        )
        handlers: list[Any] = [
            request.ProxyHandler({}),
            _NoRedirectHandler(),
        ]
        if scheme == "https":
            if self._ssl_context is None:
                self._ssl_context = build_gateway_client_ssl_context(self.tls)
            handlers.append(
                request.HTTPSHandler(context=self._ssl_context)
            )
        opener = request.build_opener(*handlers)
        return opener.open(request_value, timeout=timeout)


def validate_gateway_response_limit(max_bytes: int) -> int:
    if max_bytes <= 0:
        raise ValueError("Gateway max_response_bytes must be positive.")
    if max_bytes > MAX_GATEWAY_RESPONSE_BYTES:
        raise ValueError(
            "Gateway max_response_bytes exceeds the hard safety limit."
        )
    return max_bytes


def read_bounded_gateway_response(
    response: Any,
    *,
    max_bytes: int = DEFAULT_GATEWAY_RESPONSE_BYTES,
) -> bytes:
    limit = validate_gateway_response_limit(max_bytes)
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Gateway response Content-Length must be an integer."
            ) from exc
        if declared_length < 0:
            raise ValueError(
                "Gateway response Content-Length must not be negative."
            )
        if declared_length > limit:
            raise GatewayResponseTooLarge(
                f"Gateway response exceeds {limit} bytes."
            )

    chunks: list[bytes] = []
    observed = 0
    while True:
        chunk = response.read(min(64 * 1024, limit + 1 - observed))
        if not chunk:
            break
        chunks.append(chunk)
        observed += len(chunk)
        if observed > limit:
            raise GatewayResponseTooLarge(
                f"Gateway response exceeds {limit} bytes."
            )
    return b"".join(chunks)


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that admits sockets before spawning handler threads."""

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        connection_budget: ConnectionBudget,
        request_header_timeout_seconds: float,
    ) -> None:
        self.connection_budget = connection_budget
        self.request_header_timeout_seconds = request_header_timeout_seconds
        super().__init__(server_address, handler_class)

    def get_request(self) -> tuple[Any, Any]:
        request_value, client_address = super().get_request()
        request_value.settimeout(self.request_header_timeout_seconds)
        return request_value, client_address

    def verify_request(self, request_value: Any, client_address: Any) -> bool:
        del request_value
        return self.connection_budget.acquire(str(client_address[0]))

    def process_request(
        self,
        request_value: Any,
        client_address: Any,
    ) -> None:
        try:
            super().process_request(request_value, client_address)
        except BaseException:
            self.connection_budget.release(str(client_address[0]))
            raise

    def process_request_thread(
        self,
        request_value: Any,
        client_address: Any,
    ) -> None:
        try:
            super().process_request_thread(request_value, client_address)
        finally:
            self.connection_budget.release(str(client_address[0]))


def validate_gateway_url(
    url: str,
    *,
    allow_plaintext_loopback: bool = True,
) -> str:
    """Reject credentials over remote plaintext before a request is sent."""

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("Gateway URL must use http:// or https://.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Gateway URL must not contain user credentials.")
    if parsed.hostname is None:
        raise ValueError("Gateway URL must include a hostname.")
    if scheme == "http" and not (
        allow_plaintext_loopback and is_loopback_host(parsed.hostname)
    ):
        raise ValueError(
            "Remote Gateway connections require HTTPS; plaintext HTTP is "
            "restricted to the local loopback development boundary."
        )
    return scheme


def validate_gateway_tls_server_config(
    host: str,
    config: GatewayTlsServerConfig,
) -> None:
    """Fail closed when a listener could expose Gateway traffic as plaintext."""

    if not config.enabled:
        if any((config.cert_file, config.key_file, config.ca_file)):
            raise ValueError(
                "Gateway TLS files were configured but TLS is disabled."
            )
        if config.require_client_cert:
            raise ValueError("Gateway mTLS requires TLS to be enabled.")
        if not is_loopback_host(host):
            raise ValueError(
                "A non-loopback Gateway bind requires TLS. Configure a "
                "certificate and key before listening on "
                f"{host!r}."
            )
        return

    if not config.cert_file or not config.key_file:
        raise ValueError(
            "Gateway TLS requires both cert_file and key_file."
        )
    _require_file(config.cert_file, label="Gateway TLS certificate")
    _require_file(config.key_file, label="Gateway TLS private key")
    if config.ca_file is not None:
        _require_file(config.ca_file, label="Gateway TLS CA bundle")
    if config.require_client_cert and not config.ca_file:
        raise ValueError(
            "Gateway mTLS requires ca_file to verify client certificates."
        )


def build_gateway_server_ssl_context(
    config: GatewayTlsServerConfig,
) -> ssl.SSLContext:
    if not config.enabled:
        raise ValueError("Cannot build a server SSL context while TLS is disabled.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(
        certfile=str(config.cert_file),
        keyfile=str(config.key_file),
    )
    if config.ca_file is not None:
        context.load_verify_locations(cafile=config.ca_file)
    context.verify_mode = (
        ssl.CERT_REQUIRED if config.require_client_cert else ssl.CERT_NONE
    )
    return context


def build_gateway_client_ssl_context(
    config: GatewayTlsClientConfig,
) -> ssl.SSLContext:
    _validate_gateway_tls_client_config(config)
    context = ssl.create_default_context(cafile=config.ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    if config.cert_file is not None:
        context.load_cert_chain(
            certfile=config.cert_file,
            keyfile=str(config.key_file),
        )
    return context


def create_gateway_http_server(
    address: tuple[str, int],
    handler_class: type[BaseHTTPRequestHandler],
    *,
    tls: GatewayTlsServerConfig,
    network_policy: GatewayNetworkPolicy | None = None,
) -> ThreadingHTTPServer:
    """Create an HTTP(S) server without ever falling back after TLS failure."""

    validate_gateway_tls_server_config(address[0], tls)
    policy = network_policy or GatewayNetworkPolicy()
    server = BoundedThreadingHTTPServer(
        address,
        handler_class,
        connection_budget=ConnectionBudget(
            total_limit=policy.max_connections,
            per_client_limit=policy.max_connections_per_ip,
        ),
        request_header_timeout_seconds=(
            policy.request_header_timeout_seconds
        ),
    )
    if not tls.enabled:
        return server
    try:
        context = build_gateway_server_ssl_context(tls)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    except Exception:
        server.server_close()
        raise
    return server


def gateway_scheme(config: GatewayTlsServerConfig) -> str:
    return "https" if config.enabled else "http"


def _validate_gateway_tls_client_config(
    config: GatewayTlsClientConfig,
) -> None:
    if (config.cert_file is None) != (config.key_file is None):
        raise ValueError(
            "Gateway client mTLS requires both cert_file and key_file."
        )
    if config.ca_file is not None:
        _require_file(config.ca_file, label="Gateway client CA bundle")
    if config.cert_file is not None:
        _require_file(config.cert_file, label="Gateway client certificate")
        _require_file(str(config.key_file), label="Gateway client private key")


def _require_file(value: str, *, label: str) -> None:
    path = Path(value).expanduser()
    if not path.is_file():
        raise ValueError(f"{label} does not exist or is not a file: {value}")
