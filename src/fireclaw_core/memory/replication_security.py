"""Replication policy, request signing, and batch authentication."""
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol


REPLICATION_PROTOCOL_VERSION = 2
REPLICATION_AUTH_HEADER = "X-FireClaw-Replication-Auth"

# Lazy import to avoid circular dependency at module level.
_MEMORY_RUNTIME_MODES: frozenset[str] | None = None
_MEMORY_SENSITIVITY_LEVELS: frozenset[str] | None = None


def _get_runtime_modes() -> frozenset[str]:
    global _MEMORY_RUNTIME_MODES
    if _MEMORY_RUNTIME_MODES is None:
        from fireclaw_core.memory.embodied_memory import MEMORY_RUNTIME_MODES
        _MEMORY_RUNTIME_MODES = MEMORY_RUNTIME_MODES
    return _MEMORY_RUNTIME_MODES


def _get_sensitivity_levels() -> frozenset[str]:
    global _MEMORY_SENSITIVITY_LEVELS
    if _MEMORY_SENSITIVITY_LEVELS is None:
        from fireclaw_core.memory.embodied_memory import MEMORY_SENSITIVITY_LEVELS
        _MEMORY_SENSITIVITY_LEVELS = MEMORY_SENSITIVITY_LEVELS
    return _MEMORY_SENSITIVITY_LEVELS


@dataclass(frozen=True)
class ReplicationPeerPolicy:
    """Server-bound peer policy controlling exportable evidence."""

    policy_id: str
    peer_id: str
    allowed_robot_ids: frozenset[str]
    allowed_runtime_modes: frozenset[str]
    allowed_sensitivities: frozenset[str]

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("policy_id must not be empty")
        if not self.peer_id.strip():
            raise ValueError("peer_id must not be empty")
        if not self.allowed_robot_ids:
            raise ValueError("allowed_robot_ids must not be empty")
        if not self.allowed_runtime_modes:
            raise ValueError("allowed_runtime_modes must not be empty")
        if not self.allowed_sensitivities:
            raise ValueError("allowed_sensitivities must not be empty")
        valid_runtimes = _get_runtime_modes()
        for mode in self.allowed_runtime_modes:
            if mode not in valid_runtimes:
                raise ValueError(
                    f"Unknown runtime mode: {mode}. "
                    f"Must be one of: {sorted(valid_runtimes)}"
                )
        valid_sensitivities = _get_sensitivity_levels()
        for level in self.allowed_sensitivities:
            if level not in valid_sensitivities:
                raise ValueError(
                    f"Unknown sensitivity level: {level}. "
                    f"Must be one of: {sorted(valid_sensitivities)}"
                )

    def is_event_exportable(
        self,
        *,
        robot_id: str | None,
        runtime_mode: str,
        sensitivity: str,
    ) -> bool:
        if runtime_mode not in self.allowed_runtime_modes:
            return False
        if robot_id is not None and robot_id not in self.allowed_robot_ids:
            return False
        if sensitivity not in self.allowed_sensitivities:
            return False
        return True


@dataclass(frozen=True)
class ReplicationRequestScope:
    """Canonical scope bound into request/batch signatures."""

    mission_id: str
    runtime_mode: str
    cursor: int
    limit: int

    def __post_init__(self) -> None:
        if not self.mission_id.strip():
            raise ValueError("mission_id must not be empty")
        if not self.runtime_mode.strip():
            raise ValueError("runtime_mode must not be empty")
        if self.cursor < 0:
            raise ValueError("cursor must be non-negative")
        if self.limit < 1:
            raise ValueError("limit must be positive")

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            {
                "mission_id": self.mission_id,
                "runtime_mode": self.runtime_mode,
                "cursor": self.cursor,
                "limit": self.limit,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


@dataclass(frozen=True)
class ReplicationAuthMetadata:
    """Authenticated metadata attached to requests and batches."""

    protocol_version: int
    peer_id: str
    key_id: str
    issued_at: str
    nonce: str
    body_digest: str
    signature: str

    def __post_init__(self) -> None:
        if self.protocol_version != REPLICATION_PROTOCOL_VERSION:
            raise ValueError(
                f"Unsupported replication protocol: {self.protocol_version}"
            )
        if not self.peer_id.strip():
            raise ValueError("peer_id must not be empty")
        if not self.key_id.strip():
            raise ValueError("key_id must not be empty")
        if not self.nonce.strip():
            raise ValueError("nonce must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "peer_id": self.peer_id,
            "key_id": self.key_id,
            "issued_at": self.issued_at,
            "nonce": self.nonce,
            "body_digest": self.body_digest,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ReplicationAuthMetadata:
        if not isinstance(payload, dict):
            raise ValueError("auth metadata must be an object")
        return cls(
            protocol_version=int(payload.get("protocol_version", 0)),
            peer_id=str(payload.get("peer_id") or ""),
            key_id=str(payload.get("key_id") or ""),
            issued_at=str(payload.get("issued_at") or ""),
            nonce=str(payload.get("nonce") or ""),
            body_digest=str(payload.get("body_digest") or ""),
            signature=str(payload.get("signature") or ""),
        )


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of signature verification."""

    verified: bool
    error_code: str | None = None


class ReplicationKeyProvider(Protocol):
    """Protocol for obtaining HMAC signing/verification keys."""

    def signing_key(self, key_id: str) -> bytes: ...
    def verification_key(self, peer_id: str, key_id: str) -> bytes: ...


class InMemoryReplicationKeyProvider:
    """Test-only in-memory key provider."""

    def __init__(self) -> None:
        self._keys: dict[tuple[str, str], bytes] = {}

    def register_key(self, *, peer_id: str, key_id: str, secret: bytes) -> None:
        if not secret:
            raise ValueError("secret must not be empty")
        self._keys[(peer_id, key_id)] = secret

    def signing_key(self, key_id: str) -> bytes:
        for (_, kid), secret in self._keys.items():
            if kid == key_id:
                return secret
        raise KeyError(f"No signing key found for key_id={key_id}")

    def verification_key(self, peer_id: str, key_id: str) -> bytes:
        key = (peer_id, key_id)
        if key not in self._keys:
            raise KeyError(f"No verification key for peer={peer_id} key_id={key_id}")
        return self._keys[key]


class ReplicationNonceCache:
    """Bounded LRU cache for nonce replay prevention."""

    def __init__(self, *, max_entries: int = 4096) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.RLock()

    def consume(self, key: str) -> bool:
        """Return True if key is new (consumed), False if already seen."""
        with self._lock:
            if key in self._entries:
                return False
            self._entries[key] = None
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            return True


@dataclass(frozen=True)
class ReplicationSigningIdentity:
    """Shared signing identity for replication peers."""

    peer_id: str
    key_id: str

    def __post_init__(self) -> None:
        if not self.peer_id.strip() or not self.key_id.strip():
            raise ValueError("replication signing identity must be complete")


@dataclass(frozen=True)
class ReplicationServerSecurity:
    """Robot-side gateway security configuration."""

    identity: ReplicationSigningIdentity
    key_provider: ReplicationKeyProvider
    peer_policies: Mapping[str, ReplicationPeerPolicy]
    nonce_cache: ReplicationNonceCache


@dataclass(frozen=True)
class ReplicationClientSecurity:
    """Mission-side gateway security configuration."""

    identity: ReplicationSigningIdentity
    key_provider: ReplicationKeyProvider
    robot_policies: Mapping[str, ReplicationPeerPolicy]
    nonce_cache: ReplicationNonceCache
    allow_insecure_simulation_http: bool = False


def _canonical_sign_data(
    *,
    protocol_version: int,
    peer_id: str,
    key_id: str,
    issued_at: str,
    nonce: str,
    scope: ReplicationRequestScope,
    body_digest: str,
) -> bytes:
    return json.dumps(
        {
            "protocol_version": protocol_version,
            "peer_id": peer_id,
            "key_id": key_id,
            "issued_at": issued_at,
            "nonce": nonce,
            "scope": {
                "mission_id": scope.mission_id,
                "runtime_mode": scope.runtime_mode,
                "cursor": scope.cursor,
                "limit": scope.limit,
            },
            "body_digest": body_digest,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _compute_body_digest(payload: dict[str, Any] | bytes) -> str:
    if isinstance(payload, bytes):
        raw = payload
    else:
        raw = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _compute_hmac(key: bytes, data: bytes) -> str:
    return _hmac.new(key, data, hashlib.sha256).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sign_request(
    *,
    body: dict[str, Any] | None,
    scope: ReplicationRequestScope,
    key_provider: ReplicationKeyProvider,
    peer_id: str,
    key_id: str,
    issued_at: str | None = None,
    nonce: str | None = None,
) -> ReplicationAuthMetadata:
    """Sign a replication request."""
    actual_issued_at = issued_at or _utc_now_iso()
    actual_nonce = nonce or secrets.token_urlsafe(24)
    body_digest = _compute_body_digest(body or {})
    key = key_provider.signing_key(key_id)
    canonical = _canonical_sign_data(
        protocol_version=REPLICATION_PROTOCOL_VERSION,
        peer_id=peer_id,
        key_id=key_id,
        issued_at=actual_issued_at,
        nonce=actual_nonce,
        scope=scope,
        body_digest=body_digest,
    )
    signature = _compute_hmac(key, canonical)
    return ReplicationAuthMetadata(
        protocol_version=REPLICATION_PROTOCOL_VERSION,
        peer_id=peer_id,
        key_id=key_id,
        issued_at=actual_issued_at,
        nonce=actual_nonce,
        body_digest=body_digest,
        signature=signature,
    )


def _verify_common(
    *,
    body: dict[str, Any] | None,
    auth: ReplicationAuthMetadata,
    scope: ReplicationRequestScope,
    key_provider: ReplicationKeyProvider,
    peer_id: str,
    max_age_seconds: int,
    nonce_cache: ReplicationNonceCache | None,
    body_source: dict[str, Any] | None = None,
) -> VerificationResult:
    """Shared verification logic for requests and batches."""
    if auth.peer_id != peer_id:
        return VerificationResult(verified=False, error_code="peer_mismatch")
    try:
        issued_dt = datetime.fromisoformat(auth.issued_at)
    except (ValueError, TypeError):
        return VerificationResult(verified=False, error_code="invalid_timestamp")
    if issued_dt.tzinfo is None:
        return VerificationResult(verified=False, error_code="invalid_timestamp")
    age = (datetime.now(timezone.utc) - issued_dt).total_seconds()
    if age > max_age_seconds:
        return VerificationResult(verified=False, error_code="timestamp_expired")
    if age < -max_age_seconds:
        return VerificationResult(verified=False, error_code="timestamp_future")
    body_digest = _compute_body_digest(body_source if body_source is not None else (body or {}))
    if body_digest != auth.body_digest:
        return VerificationResult(verified=False, error_code="body_digest_mismatch")
    try:
        key = key_provider.verification_key(peer_id, auth.key_id)
    except KeyError:
        return VerificationResult(verified=False, error_code="key_not_found")
    canonical = _canonical_sign_data(
        protocol_version=auth.protocol_version,
        peer_id=auth.peer_id,
        key_id=auth.key_id,
        issued_at=auth.issued_at,
        nonce=auth.nonce,
        scope=scope,
        body_digest=auth.body_digest,
    )
    expected_sig = _compute_hmac(key, canonical)
    if not _hmac.compare_digest(expected_sig, auth.signature):
        return VerificationResult(verified=False, error_code="signature_invalid")
    # Consume nonce only after signature succeeds
    if nonce_cache is not None:
        nonce_key = f"{auth.peer_id}:{auth.nonce}"
        if not nonce_cache.consume(nonce_key):
            return VerificationResult(verified=False, error_code="nonce_reused")
    return VerificationResult(verified=True)


def verify_signed_request(
    *,
    body: dict[str, Any] | None,
    auth: ReplicationAuthMetadata,
    scope: ReplicationRequestScope,
    key_provider: ReplicationKeyProvider,
    peer_id: str,
    max_age_seconds: int = 300,
    nonce_cache: ReplicationNonceCache | None = None,
) -> VerificationResult:
    """Verify a signed replication request."""
    return _verify_common(
        body=body,
        auth=auth,
        scope=scope,
        key_provider=key_provider,
        peer_id=peer_id,
        max_age_seconds=max_age_seconds,
        nonce_cache=nonce_cache,
    )


def sign_batch(
    *,
    batch: Any,
    scope: ReplicationRequestScope,
    key_provider: ReplicationKeyProvider,
    peer_id: str,
    key_id: str,
    issued_at: str | None = None,
    nonce: str | None = None,
) -> ReplicationAuthMetadata:
    """Sign a replication batch."""
    actual_issued_at = issued_at or _utc_now_iso()
    actual_nonce = nonce or secrets.token_urlsafe(24)
    batch_dict = batch.to_dict()
    body_digest = _compute_body_digest(batch_dict)
    key = key_provider.signing_key(key_id)
    canonical = _canonical_sign_data(
        protocol_version=REPLICATION_PROTOCOL_VERSION,
        peer_id=peer_id,
        key_id=key_id,
        issued_at=actual_issued_at,
        nonce=actual_nonce,
        scope=scope,
        body_digest=body_digest,
    )
    signature = _compute_hmac(key, canonical)
    return ReplicationAuthMetadata(
        protocol_version=REPLICATION_PROTOCOL_VERSION,
        peer_id=peer_id,
        key_id=key_id,
        issued_at=actual_issued_at,
        nonce=actual_nonce,
        body_digest=body_digest,
        signature=signature,
    )


def verify_signed_batch(
    *,
    batch: Any,
    auth: ReplicationAuthMetadata,
    scope: ReplicationRequestScope,
    key_provider: ReplicationKeyProvider,
    peer_id: str,
    max_age_seconds: int = 300,
    nonce_cache: ReplicationNonceCache | None = None,
) -> VerificationResult:
    """Verify a signed replication batch."""
    batch_dict = batch.to_dict()
    return _verify_common(
        body=batch_dict,
        auth=auth,
        scope=scope,
        key_provider=key_provider,
        peer_id=peer_id,
        max_age_seconds=max_age_seconds,
        nonce_cache=nonce_cache,
        body_source=batch_dict,
    )


def sign_payload(
    *,
    payload: dict[str, Any],
    scope: ReplicationRequestScope,
    key_provider: ReplicationKeyProvider,
    identity: ReplicationSigningIdentity,
    issued_at: str | None = None,
    nonce: str | None = None,
) -> ReplicationAuthMetadata:
    """Sign a raw payload dict (used for v2 batch responses)."""
    return sign_request(
        body=payload,
        scope=scope,
        key_provider=key_provider,
        peer_id=identity.peer_id,
        key_id=identity.key_id,
        issued_at=issued_at,
        nonce=nonce,
    )


def verify_signed_payload(
    *,
    payload: dict[str, Any],
    auth: ReplicationAuthMetadata,
    scope: ReplicationRequestScope,
    key_provider: ReplicationKeyProvider,
    peer_id: str,
    max_age_seconds: int = 300,
    nonce_cache: ReplicationNonceCache | None = None,
) -> VerificationResult:
    """Verify a raw signed payload dict."""
    return _verify_common(
        body=payload,
        auth=auth,
        scope=scope,
        key_provider=key_provider,
        peer_id=peer_id,
        max_age_seconds=max_age_seconds,
        nonce_cache=nonce_cache,
        body_source=payload,
    )


def encode_auth_header(auth: ReplicationAuthMetadata) -> str:
    """Encode auth metadata as base64url for transport header."""
    raw = json.dumps(
        auth.to_dict(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_auth_header(value: str) -> ReplicationAuthMetadata:
    """Decode auth metadata from a base64url transport header."""
    if not value:
        raise ValueError("replication auth header is required")
    padding = "=" * (-len(value) % 4)
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(value + padding).decode("ascii")
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("replication auth header is invalid") from exc
    return ReplicationAuthMetadata.from_dict(payload)
