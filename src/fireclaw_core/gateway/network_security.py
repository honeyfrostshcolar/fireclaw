from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from email.message import Message
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
import ipaddress
import json
import math
import socket
import threading
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit


_WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", ""})
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
_NETWORK_CONFIG_KEYS = frozenset(
    {
        "max_body_bytes",
        "request_header_timeout_seconds",
        "body_read_timeout_seconds",
        "max_connections",
        "max_connections_per_ip",
        "auth_max_failures",
        "auth_window_seconds",
        "auth_lockout_seconds",
        "auth_exempt_loopback",
        "auth_max_tracked_clients",
        "max_sse_connections",
        "max_sse_connections_per_ip",
        "sse_queue_size",
        "sse_write_timeout_seconds",
        "allowed_hosts",
        "allowed_origins",
    }
)


@dataclass(frozen=True)
class GatewayNetworkPolicy:
    max_body_bytes: int = 1 * 1024 * 1024
    request_header_timeout_seconds: float = 10.0
    body_read_timeout_seconds: float = 10.0
    max_connections: int = 128
    max_connections_per_ip: int = 32
    auth_max_failures: int = 10
    auth_window_seconds: float = 60.0
    auth_lockout_seconds: float = 300.0
    auth_exempt_loopback: bool = True
    auth_max_tracked_clients: int = 10_000
    max_sse_connections: int = 16
    max_sse_connections_per_ip: int = 4
    sse_queue_size: int = 256
    sse_write_timeout_seconds: float = 10.0
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("max_body_bytes", self.max_body_bytes),
            ("max_connections", self.max_connections),
            ("max_connections_per_ip", self.max_connections_per_ip),
            ("auth_max_failures", self.auth_max_failures),
            ("auth_max_tracked_clients", self.auth_max_tracked_clients),
            ("max_sse_connections", self.max_sse_connections),
            ("max_sse_connections_per_ip", self.max_sse_connections_per_ip),
            ("sse_queue_size", self.sse_queue_size),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"Gateway network {name} must be a positive integer.")
        for name, value in (
            (
                "request_header_timeout_seconds",
                self.request_header_timeout_seconds,
            ),
            ("body_read_timeout_seconds", self.body_read_timeout_seconds),
            ("auth_window_seconds", self.auth_window_seconds),
            ("auth_lockout_seconds", self.auth_lockout_seconds),
            ("sse_write_timeout_seconds", self.sse_write_timeout_seconds),
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ValueError(f"Gateway network {name} must be positive.")
        if self.max_connections_per_ip > self.max_connections:
            raise ValueError(
                "Gateway max_connections_per_ip cannot exceed max_connections."
            )
        if self.max_sse_connections > self.max_connections:
            raise ValueError(
                "Gateway max_sse_connections cannot exceed max_connections."
            )
        if self.max_sse_connections_per_ip > self.max_connections_per_ip:
            raise ValueError(
                "Gateway max_sse_connections_per_ip cannot exceed "
                "max_connections_per_ip."
            )
        if self.max_sse_connections_per_ip > self.max_sse_connections:
            raise ValueError(
                "Gateway max_sse_connections_per_ip cannot exceed "
                "max_sse_connections."
            )
        hosts = tuple(_normalize_allowed_host(value) for value in self.allowed_hosts)
        origins = tuple(
            _normalize_allowed_origin(value) for value in self.allowed_origins
        )
        object.__setattr__(self, "allowed_hosts", hosts)
        object.__setattr__(self, "allowed_origins", origins)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_body_bytes": self.max_body_bytes,
            "request_header_timeout_seconds": (
                self.request_header_timeout_seconds
            ),
            "body_read_timeout_seconds": self.body_read_timeout_seconds,
            "max_connections": self.max_connections,
            "max_connections_per_ip": self.max_connections_per_ip,
            "auth_max_failures": self.auth_max_failures,
            "auth_window_seconds": self.auth_window_seconds,
            "auth_lockout_seconds": self.auth_lockout_seconds,
            "auth_exempt_loopback": self.auth_exempt_loopback,
            "auth_max_tracked_clients": self.auth_max_tracked_clients,
            "max_sse_connections": self.max_sse_connections,
            "max_sse_connections_per_ip": self.max_sse_connections_per_ip,
            "sse_queue_size": self.sse_queue_size,
            "sse_write_timeout_seconds": self.sse_write_timeout_seconds,
            "allowed_hosts": list(self.allowed_hosts),
            "allowed_origins": list(self.allowed_origins),
        }


@dataclass(frozen=True)
class RequestAdmissionDecision:
    allowed: bool
    status: HTTPStatus = HTTPStatus.OK
    message: str = ""
    content_length: int = 0


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int = 0


@dataclass
class _AuthEntry:
    failures: deque[float]
    locked_until: float = 0.0


class GatewayAuthRateLimiter:
    """Bounded per-IP sliding-window limiter for failed authentication."""

    def __init__(
        self,
        policy: GatewayNetworkPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._clock = clock
        self._entries: OrderedDict[str, _AuthEntry] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, client_ip: str) -> RateLimitDecision:
        if self._exempt(client_ip):
            return RateLimitDecision(True, self._policy.auth_max_failures)
        now = self._clock()
        key = _client_key(client_ip)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return RateLimitDecision(True, self._policy.auth_max_failures)
            self._slide(entry, now)
            if entry.locked_until > now:
                retry_after = max(1, int(entry.locked_until - now + 0.999))
                return RateLimitDecision(False, 0, retry_after)
            if entry.locked_until:
                entry.locked_until = 0.0
            remaining = max(
                0,
                self._policy.auth_max_failures - len(entry.failures),
            )
            return RateLimitDecision(True, remaining)

    def record_failure(self, client_ip: str) -> None:
        if self._exempt(client_ip):
            return
        now = self._clock()
        key = _client_key(client_ip)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._evict_if_needed()
                entry = _AuthEntry(failures=deque())
                self._entries[key] = entry
            else:
                self._entries.move_to_end(key)
            self._slide(entry, now)
            entry.failures.append(now)
            if len(entry.failures) >= self._policy.auth_max_failures:
                entry.locked_until = now + self._policy.auth_lockout_seconds

    def reset(self, client_ip: str) -> None:
        if self._exempt(client_ip):
            return
        with self._lock:
            self._entries.pop(_client_key(client_ip), None)

    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def _slide(self, entry: _AuthEntry, now: float) -> None:
        cutoff = now - self._policy.auth_window_seconds
        while entry.failures and entry.failures[0] <= cutoff:
            entry.failures.popleft()

    def _evict_if_needed(self) -> None:
        while len(self._entries) >= self._policy.auth_max_tracked_clients:
            self._entries.popitem(last=False)

    def _exempt(self, client_ip: str) -> bool:
        return self._policy.auth_exempt_loopback and _is_loopback_ip(client_ip)


class ConnectionBudget:
    """Thread-safe total and per-client concurrent connection budget."""

    def __init__(self, *, total_limit: int, per_client_limit: int) -> None:
        self._total_limit = total_limit
        self._per_client_limit = per_client_limit
        self._total = 0
        self._per_client: dict[str, int] = {}
        self._lock = threading.Lock()

    def acquire(self, client_ip: str) -> bool:
        key = _client_key(client_ip)
        with self._lock:
            current = self._per_client.get(key, 0)
            if self._total >= self._total_limit or current >= self._per_client_limit:
                return False
            self._total += 1
            self._per_client[key] = current + 1
            return True

    def release(self, client_ip: str) -> None:
        key = _client_key(client_ip)
        with self._lock:
            current = self._per_client.get(key)
            if current is None:
                return
            self._total = max(0, self._total - 1)
            if current <= 1:
                self._per_client.pop(key, None)
            else:
                self._per_client[key] = current - 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active": self._total,
                "tracked_clients": len(self._per_client),
                "total_limit": self._total_limit,
                "per_client_limit": self._per_client_limit,
            }


class GatewayRequestGuard:
    """Shared HTTP admission, authentication throttling, and SSE budgets."""

    def __init__(
        self,
        policy: GatewayNetworkPolicy,
        *,
        configured_host: str,
        tls_enabled: bool,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.policy = policy
        self.configured_host = _normalize_hostname(configured_host)
        self.tls_enabled = tls_enabled
        if (
            self.configured_host in _WILDCARD_HOSTS
            and not self.policy.allowed_hosts
        ):
            raise ValueError(
                "Wildcard Gateway binds require network.allowed_hosts."
            )
        self.auth_limiter = GatewayAuthRateLimiter(policy, clock=clock)
        self.sse_budget = ConnectionBudget(
            total_limit=policy.max_sse_connections,
            per_client_limit=policy.max_sse_connections_per_ip,
        )

    def admit(
        self,
        *,
        method: str,
        headers: Message,
        server_host: str,
        server_port: int,
    ) -> RequestAdmissionDecision:
        host_values = headers.get_all("Host", failobj=[])
        if len(host_values) != 1:
            return _reject(HTTPStatus.BAD_REQUEST, "Exactly one Host header is required.")
        try:
            request_host, request_port = _parse_host_header(host_values[0])
        except ValueError as exc:
            return _reject(HTTPStatus.BAD_REQUEST, str(exc))
        allowed_hosts = {
            host
            for host in (
                self.configured_host,
                _normalize_hostname(server_host),
                *self.policy.allowed_hosts,
            )
            if host not in _WILDCARD_HOSTS
        }
        if any(
            host == "localhost" or _is_loopback_ip(host)
            for host in allowed_hosts
        ):
            allowed_hosts.update({"localhost", "127.0.0.1", "::1"})
        if request_host not in allowed_hosts:
            return _reject(HTTPStatus.MISDIRECTED_REQUEST, "Host is not allowed.")
        if request_port is not None and request_port != server_port:
            return _reject(HTTPStatus.MISDIRECTED_REQUEST, "Host port is not allowed.")

        origin_values = headers.get_all("Origin", failobj=[])
        if len(origin_values) > 1:
            return _reject(HTTPStatus.FORBIDDEN, "Multiple Origin headers are forbidden.")
        if origin_values:
            origin = origin_values[0]
            if not self._origin_allowed(
                origin,
                request_host=request_host,
                request_port=request_port or server_port,
            ):
                return _reject(HTTPStatus.FORBIDDEN, "Origin is not allowed.")

        transfer_encoding = headers.get_all("Transfer-Encoding", failobj=[])
        if transfer_encoding:
            return _reject(
                HTTPStatus.BAD_REQUEST,
                "Transfer-Encoding is not supported.",
            )
        content_lengths = headers.get_all("Content-Length", failobj=[])
        if len(content_lengths) > 1:
            return _reject(
                HTTPStatus.BAD_REQUEST,
                "Multiple Content-Length headers are forbidden.",
            )
        length = 0
        if content_lengths:
            raw_length = content_lengths[0]
            if not raw_length or not raw_length.isascii() or not raw_length.isdigit():
                return _reject(HTTPStatus.BAD_REQUEST, "Invalid Content-Length header.")
            try:
                length = int(raw_length)
            except (ValueError, OverflowError):
                return _reject(
                    HTTPStatus.BAD_REQUEST,
                    "Invalid Content-Length header.",
                )
        if length > self.policy.max_body_bytes:
            return _reject(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                (
                    f"Request body too large ({length} bytes, max "
                    f"{self.policy.max_body_bytes})."
                ),
            )
        if method.upper() not in _BODY_METHODS and length > 0:
            return _reject(
                HTTPStatus.BAD_REQUEST,
                "Request bodies are not allowed for this method.",
            )
        return RequestAdmissionDecision(True, content_length=length)

    def check_auth(self, client_ip: str) -> RateLimitDecision:
        return self.auth_limiter.check(client_ip)

    def record_auth_failure(self, client_ip: str) -> None:
        self.auth_limiter.record_failure(client_ip)

    def reset_auth_failures(self, client_ip: str) -> None:
        self.auth_limiter.reset(client_ip)

    def acquire_sse(self, client_ip: str) -> bool:
        return self.sse_budget.acquire(client_ip)

    def release_sse(self, client_ip: str) -> None:
        self.sse_budget.release(client_ip)

    def snapshot(self) -> dict[str, Any]:
        return {
            "policy": self.policy.to_dict(),
            "auth_tracked_clients": self.auth_limiter.size(),
            "sse": self.sse_budget.snapshot(),
        }

    def _origin_allowed(
        self,
        raw_origin: str,
        *,
        request_host: str,
        request_port: int,
    ) -> bool:
        try:
            origin = _normalize_allowed_origin(raw_origin)
        except ValueError:
            return False
        if origin in self.policy.allowed_origins:
            return True
        parsed = urlsplit(origin)
        expected_scheme = "https" if self.tls_enabled else "http"
        origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return (
            parsed.scheme == expected_scheme
            and _normalize_hostname(parsed.hostname or "") == request_host
            and origin_port == request_port
        )


class GatewayRequestBodyError(ValueError):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def read_json_object_body(
    handler: BaseHTTPRequestHandler,
    policy: GatewayNetworkPolicy,
) -> dict[str, Any]:
    decision = _content_length_decision(handler.headers, policy)
    if not decision.allowed:
        handler.close_connection = True
        raise GatewayRequestBodyError(decision.status, decision.message)
    length = decision.content_length
    if length <= 0:
        return {}
    connection = getattr(handler, "connection", None)
    previous_timeout: float | None = None
    if connection is not None and hasattr(connection, "settimeout"):
        try:
            previous_timeout = connection.gettimeout()
        except (AttributeError, OSError):
            previous_timeout = None
        connection.settimeout(policy.body_read_timeout_seconds)
    try:
        raw = handler.rfile.read(length)
    except (TimeoutError, socket.timeout) as exc:
        handler.close_connection = True
        raise GatewayRequestBodyError(
            HTTPStatus.REQUEST_TIMEOUT,
            "Request body read timed out.",
        ) from exc
    finally:
        if connection is not None and hasattr(connection, "settimeout"):
            try:
                connection.settimeout(previous_timeout)
            except OSError:
                pass
    if len(raw) != length:
        handler.close_connection = True
        raise GatewayRequestBodyError(
            HTTPStatus.BAD_REQUEST,
            "Connection closed before the complete request body was received.",
        )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GatewayRequestBodyError(
            HTTPStatus.BAD_REQUEST,
            "Request body must be valid JSON encoded as UTF-8.",
        ) from exc
    if not isinstance(value, dict):
        raise GatewayRequestBodyError(
            HTTPStatus.BAD_REQUEST,
            "JSON body must be an object.",
        )
    return value


def gateway_network_policy_from_config(
    value: Mapping[str, Any] | None,
) -> GatewayNetworkPolicy:
    raw = dict(value or {})
    unknown = sorted(set(raw).difference(_NETWORK_CONFIG_KEYS))
    if unknown:
        raise ValueError(
            "Unknown Gateway network setting(s): " + ", ".join(unknown)
        )
    defaults = GatewayNetworkPolicy()
    return GatewayNetworkPolicy(
        max_body_bytes=_config_int(raw, "max_body_bytes", defaults.max_body_bytes),
        request_header_timeout_seconds=_config_number(
            raw,
            "request_header_timeout_seconds",
            defaults.request_header_timeout_seconds,
        ),
        body_read_timeout_seconds=_config_number(
            raw,
            "body_read_timeout_seconds",
            defaults.body_read_timeout_seconds,
        ),
        max_connections=_config_int(
            raw,
            "max_connections",
            defaults.max_connections,
        ),
        max_connections_per_ip=_config_int(
            raw,
            "max_connections_per_ip",
            defaults.max_connections_per_ip,
        ),
        auth_max_failures=_config_int(
            raw,
            "auth_max_failures",
            defaults.auth_max_failures,
        ),
        auth_window_seconds=_config_number(
            raw,
            "auth_window_seconds",
            defaults.auth_window_seconds,
        ),
        auth_lockout_seconds=_config_number(
            raw,
            "auth_lockout_seconds",
            defaults.auth_lockout_seconds,
        ),
        auth_exempt_loopback=_config_bool(
            raw,
            "auth_exempt_loopback",
            defaults.auth_exempt_loopback,
        ),
        auth_max_tracked_clients=_config_int(
            raw,
            "auth_max_tracked_clients",
            defaults.auth_max_tracked_clients,
        ),
        max_sse_connections=_config_int(
            raw,
            "max_sse_connections",
            defaults.max_sse_connections,
        ),
        max_sse_connections_per_ip=_config_int(
            raw,
            "max_sse_connections_per_ip",
            defaults.max_sse_connections_per_ip,
        ),
        sse_queue_size=_config_int(
            raw,
            "sse_queue_size",
            defaults.sse_queue_size,
        ),
        sse_write_timeout_seconds=_config_number(
            raw,
            "sse_write_timeout_seconds",
            defaults.sse_write_timeout_seconds,
        ),
        allowed_hosts=_config_strings(raw, "allowed_hosts"),
        allowed_origins=_config_strings(raw, "allowed_origins"),
    )


def _content_length_decision(
    headers: Message,
    policy: GatewayNetworkPolicy,
) -> RequestAdmissionDecision:
    transfer_encoding = headers.get_all("Transfer-Encoding", failobj=[])
    if transfer_encoding:
        return _reject(HTTPStatus.BAD_REQUEST, "Transfer-Encoding is not supported.")
    values = headers.get_all("Content-Length", failobj=[])
    if len(values) > 1:
        return _reject(
            HTTPStatus.BAD_REQUEST,
            "Multiple Content-Length headers are forbidden.",
        )
    if not values:
        return RequestAdmissionDecision(True, content_length=0)
    raw = values[0]
    if not raw or not raw.isascii() or not raw.isdigit():
        return _reject(HTTPStatus.BAD_REQUEST, "Invalid Content-Length header.")
    try:
        length = int(raw)
    except (ValueError, OverflowError):
        return _reject(HTTPStatus.BAD_REQUEST, "Invalid Content-Length header.")
    if length > policy.max_body_bytes:
        return _reject(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            (
                f"Request body too large ({length} bytes, max "
                f"{policy.max_body_bytes})."
            ),
        )
    return RequestAdmissionDecision(True, content_length=length)


def _parse_host_header(value: str) -> tuple[str, int | None]:
    raw = value.strip()
    if not raw or any(char in raw for char in "/?#@\\ \t\r\n,"):
        raise ValueError("Host header is invalid.")
    try:
        parsed = urlsplit(f"//{raw}")
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Host header is invalid.") from exc
    if hostname is None:
        raise ValueError("Host header is invalid.")
    return _normalize_hostname(hostname), port


def _normalize_allowed_host(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Gateway network allowed_hosts must contain strings.")
    host, _ = _parse_host_header(value)
    if host in _WILDCARD_HOSTS:
        raise ValueError("Gateway allowed_hosts must not contain wildcard binds.")
    return host


def _normalize_allowed_origin(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Gateway network allowed_origins must contain strings.")
    raw = value.strip()
    if not raw or raw == "null":
        raise ValueError("Gateway allowed origin is invalid.")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Gateway allowed origin is invalid.") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Gateway allowed origin is invalid.")
    hostname = _normalize_hostname(parsed.hostname)
    default_port = 443 if parsed.scheme == "https" else 80
    authority = _format_host(hostname)
    if port is not None and port != default_port:
        authority = f"{authority}:{port}"
    return f"{parsed.scheme}://{authority}"


def _normalize_hostname(value: str) -> str:
    raw = value.strip().lower().rstrip(".")
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    try:
        return ipaddress.ip_address(raw).compressed
    except ValueError:
        return raw


def _format_host(hostname: str) -> str:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return hostname
    return f"[{hostname}]" if address.version == 6 else hostname


def _client_key(client_ip: str) -> str:
    return _normalize_hostname(client_ip) or "__unknown_client__"


def _is_loopback_ip(client_ip: str) -> bool:
    try:
        return ipaddress.ip_address(_normalize_hostname(client_ip)).is_loopback
    except ValueError:
        return False


def _reject(status: HTTPStatus, message: str) -> RequestAdmissionDecision:
    return RequestAdmissionDecision(False, status=status, message=message)


def _config_int(raw: Mapping[str, Any], key: str, default: int) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"network.{key} must be an integer")
    return value


def _config_number(raw: Mapping[str, Any], key: str, default: float) -> float:
    value = raw.get(key, default)
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"network.{key} must be a number")
    return float(value)


def _config_bool(raw: Mapping[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"network.{key} must be a boolean")
    return value


def _config_strings(raw: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = raw.get(key, ())
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) for item in value
    ):
        raise ValueError(f"network.{key} must be an array of strings")
    return tuple(value)
