from __future__ import annotations

import ipaddress
import os
import secrets
from dataclasses import dataclass
from typing import Literal

from fireclaw_core.gateway.control import OperatorContext, scopes_for_role
from fireclaw_core.gateway.method_scopes import ADMIN_SCOPE, READ_SCOPE


GatewayAuthMethod = Literal["public_health", "loopback", "shared_token"]


@dataclass(frozen=True)
class AuthenticatedGatewayPrincipal:
    """Server-derived identity used for both endpoint and control authorization."""

    principal_id: str
    auth_method: GatewayAuthMethod
    gateway_scopes: frozenset[str]
    role: str
    source_address: str | None = None

    @property
    def operator(self) -> OperatorContext:
        return OperatorContext(
            operator_id=self.principal_id,
            display_name=None,
            role=self.role,
            control_scopes=scopes_for_role(self.role),
            source=f"gateway_auth:{self.auth_method}",
        )


@dataclass(frozen=True)
class GatewayAuthenticationResult:
    allowed: bool
    principal: AuthenticatedGatewayPrincipal | None = None
    reason_code: str | None = None
    message: str | None = None


def resolve_gateway_api_token(
    configured: str | None,
    *,
    env_var: str = "FIRECLAW_GATEWAY_TOKEN",
) -> str | None:
    """Resolve a Gateway secret without logging or normalizing its contents."""

    if configured is not None:
        if not isinstance(configured, str):
            raise ValueError("Gateway api_token must be a string.")
        if configured.strip():
            return configured
    value = os.environ.get(env_var)
    return value if value else None


def validate_gateway_bind(host: str, api_token: str | None) -> None:
    """Refuse unauthenticated listeners outside the trusted loopback boundary."""

    if api_token is not None and (
        not isinstance(api_token, str) or not api_token.strip()
    ):
        raise ValueError("Gateway api_token must be a non-empty string.")
    if is_loopback_host(host):
        return
    if api_token:
        return
    raise ValueError(
        "A non-loopback Gateway bind requires an API token. Configure the "
        "Gateway api_token or FIRECLAW_GATEWAY_TOKEN before listening on "
        f"{host!r}."
    )


def authenticate_gateway_request(
    *,
    authorization_header: str | None,
    client_host: str,
    api_token: str | None,
    allow_public_health: bool = False,
) -> GatewayAuthenticationResult:
    """Authenticate first, then derive all scopes from the trusted auth method."""

    if allow_public_health:
        return GatewayAuthenticationResult(
            allowed=True,
            principal=AuthenticatedGatewayPrincipal(
                principal_id="anonymous-health",
                auth_method="public_health",
                gateway_scopes=frozenset({READ_SCOPE}),
                role="observer",
                source_address=client_host,
            ),
        )

    if api_token is not None:
        supplied = _bearer_token(authorization_header)
        if supplied is None or not secrets.compare_digest(supplied, api_token):
            return GatewayAuthenticationResult(
                allowed=False,
                reason_code="gateway_token_invalid",
                message="A valid Gateway bearer token is required.",
            )
        return GatewayAuthenticationResult(
            allowed=True,
            principal=AuthenticatedGatewayPrincipal(
                principal_id="gateway-shared-token",
                auth_method="shared_token",
                gateway_scopes=frozenset({ADMIN_SCOPE}),
                role="admin",
                source_address=client_host,
            ),
        )

    if not is_loopback_host(client_host):
        return GatewayAuthenticationResult(
            allowed=False,
            reason_code="gateway_loopback_required",
            message="Unauthenticated Gateway access is restricted to loopback.",
        )

    # As in OpenClaw's trusted single-operator model, local OS access is the
    # identity boundary. Request headers and JSON payloads still cannot narrow
    # or elevate this server-owned principal.
    return GatewayAuthenticationResult(
        allowed=True,
        principal=AuthenticatedGatewayPrincipal(
            principal_id="local-loopback-operator",
            auth_method="loopback",
            gateway_scopes=frozenset({ADMIN_SCOPE}),
            role="admin",
            source_address=client_host,
        ),
    )


def _bearer_token(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token:
        return None
    return token


def is_loopback_host(host: str) -> bool:
    normalized = str(host).strip().lower()
    if normalized == "localhost":
        return True
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    if "%" in normalized:
        normalized = normalized.split("%", 1)[0]
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
