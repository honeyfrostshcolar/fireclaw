from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Any
from uuid import uuid4


EXECUTION_AUTHORIZATION_VERSION = 1
EXECUTION_AUTHORIZATION_POLICY = "fireclaw.execution-authorization:v1"


def canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def authorized_action(
    skill_name: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    return {
        "skill_name": skill_name,
        "inputs_hash": canonical_json_hash(inputs),
        "action_hash": execution_action_hash(skill_name, inputs),
    }


def execution_action_hash(
    skill_name: str,
    inputs: dict[str, Any],
) -> str:
    return canonical_json_hash(
        {
            "skill_name": skill_name,
            "inputs": inputs,
        }
    )


def execution_scope_hash(
    *,
    command: str,
    structured_task: dict[str, Any] | None,
    actions: list[dict[str, Any]],
) -> str:
    task_payload = dict(structured_task or {})
    task_payload.pop("execution_authorization", None)
    return canonical_json_hash(
        {
            "command": command,
            "structured_task": task_payload or None,
            "actions": [
                {
                    "skill_name": str(action.get("skill_name") or ""),
                    "inputs_hash": str(action.get("inputs_hash") or ""),
                    "action_hash": str(action.get("action_hash") or ""),
                }
                for action in actions
            ],
        }
    )


@dataclass(frozen=True)
class ExecutionAuthorization:
    authorization_id: str
    request_id: str
    issuer_id: str
    key_id: str
    operator_id: str
    mission_id: str | None
    task_id: str | None
    robot_id: str
    scope_hash: str
    authorized_actions: tuple[dict[str, Any], ...]
    issued_at: str
    expires_at: str
    nonce: str
    policy_id: str = EXECUTION_AUTHORIZATION_POLICY
    signature: str = ""
    version: int = EXECUTION_AUTHORIZATION_VERSION

    def __post_init__(self) -> None:
        for name in (
            "authorization_id",
            "request_id",
            "issuer_id",
            "key_id",
            "operator_id",
            "robot_id",
            "scope_hash",
            "issued_at",
            "expires_at",
            "nonce",
            "policy_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.version != EXECUTION_AUTHORIZATION_VERSION:
            raise ValueError(
                f"Unsupported execution authorization version: {self.version}"
            )
        if not self.authorized_actions:
            raise ValueError("authorized_actions must not be empty")
        for action in self.authorized_actions:
            if (
                not isinstance(action, dict)
                or not isinstance(action.get("skill_name"), str)
                or not isinstance(action.get("inputs_hash"), str)
                or not isinstance(action.get("action_hash"), str)
            ):
                raise ValueError(
                    "authorized_actions must contain skill_name, "
                    "inputs_hash, and action_hash"
                )

    def unsigned_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "authorization_id": self.authorization_id,
            "request_id": self.request_id,
            "issuer_id": self.issuer_id,
            "key_id": self.key_id,
            "operator_id": self.operator_id,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "robot_id": self.robot_id,
            "scope_hash": self.scope_hash,
            "authorized_actions": [
                dict(action) for action in self.authorized_actions
            ],
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
            "policy_id": self.policy_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.unsigned_payload(),
            "signature": self.signature,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ExecutionAuthorization":
        actions = payload.get("authorized_actions")
        if not isinstance(actions, list):
            raise ValueError("execution authorization actions must be a list")
        return cls(
            version=int(payload.get("version") or 0),
            authorization_id=str(payload.get("authorization_id") or ""),
            request_id=str(payload.get("request_id") or ""),
            issuer_id=str(payload.get("issuer_id") or ""),
            key_id=str(payload.get("key_id") or ""),
            operator_id=str(payload.get("operator_id") or ""),
            mission_id=_optional_string(payload.get("mission_id")),
            task_id=_optional_string(payload.get("task_id")),
            robot_id=str(payload.get("robot_id") or ""),
            scope_hash=str(payload.get("scope_hash") or ""),
            authorized_actions=tuple(dict(action) for action in actions),
            issued_at=str(payload.get("issued_at") or ""),
            expires_at=str(payload.get("expires_at") or ""),
            nonce=str(payload.get("nonce") or ""),
            policy_id=str(payload.get("policy_id") or ""),
            signature=str(payload.get("signature") or ""),
        )


@dataclass(frozen=True)
class VerifiedExecutionAuthorization:
    authorization_id: str
    request_id: str
    operator_id: str
    scope_hash: str
    authorized_action_hashes: frozenset[str]
    expires_at: str

    def authorizes(self, skill_name: str, inputs: dict[str, Any]) -> bool:
        return (
            execution_action_hash(skill_name, inputs)
            in self.authorized_action_hashes
        )


@dataclass(frozen=True)
class ExecutionAuthorizationVerification:
    verified: bool
    grant: VerifiedExecutionAuthorization | None = None
    error_code: str | None = None


class HmacExecutionAuthorizationAuthority:
    """Issue and verify exact, short-lived physical execution authority."""

    def __init__(
        self,
        *,
        issuer_id: str,
        key_id: str,
        secret: bytes,
    ) -> None:
        if not issuer_id.strip() or not key_id.strip():
            raise ValueError("execution authorization identity must be complete")
        if not secret:
            raise ValueError("execution authorization secret must not be empty")
        self.issuer_id = issuer_id
        self.key_id = key_id
        self._secret = bytes(secret)

    def issue(
        self,
        *,
        request_id: str,
        operator_id: str,
        mission_id: str | None,
        task_id: str | None,
        robot_id: str,
        scope_hash: str,
        authorized_actions: list[dict[str, Any]],
        issued_at: str,
        expires_at: str,
    ) -> ExecutionAuthorization:
        authorization = ExecutionAuthorization(
            authorization_id=f"exec-auth-{uuid4().hex}",
            request_id=request_id,
            issuer_id=self.issuer_id,
            key_id=self.key_id,
            operator_id=operator_id,
            mission_id=mission_id,
            task_id=task_id,
            robot_id=robot_id,
            scope_hash=scope_hash,
            authorized_actions=tuple(
                {
                    "skill_name": str(action["skill_name"]),
                    "inputs_hash": str(action["inputs_hash"]),
                    "action_hash": str(action["action_hash"]),
                }
                for action in authorized_actions
            ),
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=uuid4().hex,
        )
        signature = hmac.new(
            self._secret,
            _canonical_bytes(authorization.unsigned_payload()),
            hashlib.sha256,
        ).hexdigest()
        return replace(authorization, signature=signature)

    def verify(
        self,
        authorization: ExecutionAuthorization,
        *,
        robot_id: str,
        scope_hash: str,
        action_hashes: set[str],
        now: str | None = None,
    ) -> ExecutionAuthorizationVerification:
        if (
            authorization.issuer_id != self.issuer_id
            or authorization.key_id != self.key_id
            or authorization.policy_id != EXECUTION_AUTHORIZATION_POLICY
        ):
            return ExecutionAuthorizationVerification(
                verified=False,
                error_code="authorization_identity_mismatch",
            )
        expected = hmac.new(
            self._secret,
            _canonical_bytes(authorization.unsigned_payload()),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, authorization.signature):
            return ExecutionAuthorizationVerification(
                verified=False,
                error_code="authorization_signature_invalid",
            )
        try:
            issued_at = _aware_datetime(authorization.issued_at)
            expires_at = _aware_datetime(authorization.expires_at)
            current = _aware_datetime(
                now or datetime.now(timezone.utc).isoformat()
            )
        except ValueError:
            return ExecutionAuthorizationVerification(
                verified=False,
                error_code="authorization_timestamp_invalid",
            )
        if current < issued_at or current >= expires_at:
            return ExecutionAuthorizationVerification(
                verified=False,
                error_code="authorization_expired",
            )
        if authorization.robot_id != robot_id:
            return ExecutionAuthorizationVerification(
                verified=False,
                error_code="authorization_robot_mismatch",
            )
        if authorization.scope_hash != scope_hash:
            return ExecutionAuthorizationVerification(
                verified=False,
                error_code="authorization_scope_mismatch",
            )
        authorized_hashes = frozenset(
            str(action["action_hash"])
            for action in authorization.authorized_actions
        )
        if not action_hashes or not action_hashes.issubset(authorized_hashes):
            return ExecutionAuthorizationVerification(
                verified=False,
                error_code="authorization_action_mismatch",
            )
        return ExecutionAuthorizationVerification(
            verified=True,
            grant=VerifiedExecutionAuthorization(
                authorization_id=authorization.authorization_id,
                request_id=authorization.request_id,
                operator_id=authorization.operator_id,
                scope_hash=authorization.scope_hash,
                authorized_action_hashes=authorized_hashes,
                expires_at=authorization.expires_at,
            ),
        )


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
