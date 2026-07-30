from __future__ import annotations

import pytest

from fireclaw_core.gateway.auth import (
    authenticate_gateway_request,
    resolve_gateway_api_token,
    validate_gateway_bind,
)
from fireclaw_core.gateway.method_scopes import ADMIN_SCOPE, READ_SCOPE


def test_shared_token_derives_server_owned_admin_principal() -> None:
    result = authenticate_gateway_request(
        authorization_header="Bearer secret-token",
        client_host="10.0.0.8",
        api_token="secret-token",
    )

    assert result.allowed is True
    assert result.principal is not None
    assert result.principal.principal_id == "gateway-shared-token"
    assert result.principal.auth_method == "shared_token"
    assert result.principal.gateway_scopes == frozenset({ADMIN_SCOPE})
    assert result.principal.operator.operator_id == "gateway-shared-token"
    assert result.principal.operator.role == "admin"
    assert result.principal.operator.source == "gateway_auth:shared_token"


@pytest.mark.parametrize(
    "authorization_header",
    [
        None,
        "",
        "Basic secret-token",
        "Bearer wrong-token",
        "Bearer",
    ],
)
def test_shared_token_rejects_missing_malformed_or_wrong_credentials(
    authorization_header: str | None,
) -> None:
    result = authenticate_gateway_request(
        authorization_header=authorization_header,
        client_host="127.0.0.1",
        api_token="secret-token",
    )

    assert result.allowed is False
    assert result.principal is None
    assert result.reason_code == "gateway_token_invalid"


def test_tokenless_loopback_uses_fixed_server_owned_principal() -> None:
    result = authenticate_gateway_request(
        authorization_header=None,
        client_host="127.0.0.1",
        api_token=None,
    )

    assert result.allowed is True
    assert result.principal is not None
    assert result.principal.principal_id == "local-loopback-operator"
    assert result.principal.auth_method == "loopback"
    assert result.principal.gateway_scopes == frozenset({ADMIN_SCOPE})
    assert result.principal.operator.source == "gateway_auth:loopback"


def test_tokenless_non_loopback_request_is_rejected() -> None:
    result = authenticate_gateway_request(
        authorization_header=None,
        client_host="10.0.0.8",
        api_token=None,
    )

    assert result.allowed is False
    assert result.principal is None
    assert result.reason_code == "gateway_loopback_required"


def test_public_health_has_only_server_owned_read_scope() -> None:
    result = authenticate_gateway_request(
        authorization_header=None,
        client_host="10.0.0.8",
        api_token="secret-token",
        allow_public_health=True,
    )

    assert result.allowed is True
    assert result.principal is not None
    assert result.principal.principal_id == "anonymous-health"
    assert result.principal.gateway_scopes == frozenset({READ_SCOPE})
    assert result.principal.role == "observer"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]", "localhost"])
def test_loopback_bind_can_use_local_os_trust_boundary(host: str) -> None:
    validate_gateway_bind(host, None)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.8", "robot.local"])
def test_non_loopback_bind_requires_shared_token(host: str) -> None:
    with pytest.raises(ValueError, match="requires an API token"):
        validate_gateway_bind(host, None)

    validate_gateway_bind(host, "secret-token")


@pytest.mark.parametrize("api_token", ["", " ", "\t"])
def test_blank_token_is_rejected(api_token: str) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        validate_gateway_bind("127.0.0.1", api_token)


def test_api_token_resolution_prefers_explicit_config(monkeypatch) -> None:
    monkeypatch.setenv("FIRECLAW_GATEWAY_TOKEN", "environment-token")

    assert resolve_gateway_api_token("configured-token") == "configured-token"
    assert resolve_gateway_api_token(None) == "environment-token"


def test_api_token_resolution_supports_robot_gateway_env(monkeypatch) -> None:
    monkeypatch.setenv("FIRECLAW_ROBOT_GATEWAY_TOKEN", "robot-token")

    assert (
        resolve_gateway_api_token(
            None,
            env_var="FIRECLAW_ROBOT_GATEWAY_TOKEN",
        )
        == "robot-token"
    )
