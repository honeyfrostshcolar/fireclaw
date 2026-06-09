from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from fireclaw_core.approval_store import JsonlApprovalStore


@dataclass(frozen=True)
class ApprovalRuntimeToken:
    """Immutable record of an approval runtime token.

    Only the hash is stored; the raw token is shown to the operator once
    and never persisted.
    """

    token_hash: str        # SHA-256 hex of the raw token
    request_id: str        # associated approval request
    mission_id: str
    action: str
    risk_level: str
    created_at: str        # ISO timestamp
    expires_at: str        # ISO timestamp
    resolved: bool = False
    resolved_at: str | None = None
    resolution: str | None = None  # "approved", "denied", "expired"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ApprovalRuntime:
    """Manages approval token lifecycle and pending work projection."""

    def __init__(self, store: JsonlApprovalStore, *, token_ttl_seconds: int = 300) -> None:
        self._store = store
        self._token_ttl = token_ttl_seconds
        self._tokens: Dict[str, ApprovalRuntimeToken] = {}  # keyed by token_hash

    def create_token(self, request_id: str) -> Tuple[str, ApprovalRuntimeToken]:
        """Create a token for an approval request.

        Returns (raw_token, token_record).  The raw_token is shown to the
        operator; only the hash is stored.
        """
        request = self._store.get(request_id)
        if request is None:
            raise ValueError(f"Approval request not found: {request_id}")

        raw_token = secrets.token_hex(32)  # 32 bytes -> 64 hex chars
        token_hash = hashlib.sha256(raw_token.encode("ascii")).hexdigest()

        now = time.time()
        created_at = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        expires_at = datetime.fromtimestamp(now + self._token_ttl, tz=timezone.utc).isoformat()

        record = ApprovalRuntimeToken(
            token_hash=token_hash,
            request_id=request.request_id,
            mission_id=request.mission_id,
            action=request.action,
            risk_level=request.risk_level,
            created_at=created_at,
            expires_at=expires_at,
        )
        self._tokens[token_hash] = record
        return raw_token, record

    def resolve_token(self, raw_token: str) -> ApprovalRuntimeToken | None:
        """Resolve a raw token to its record.

        Returns None if expired or not found.
        """
        token_hash = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
        record = self._tokens.get(token_hash)
        if record is None:
            return None
        if record.resolved:
            return record
        # Check expiry
        expires_dt = datetime.fromisoformat(record.expires_at)
        if datetime.now(timezone.utc) >= expires_dt:
            return None
        return record

    def pending_projection(self) -> List[Dict[str, Any]]:
        """Return pending approval projections without leaking raw tokens.

        Each entry contains: request_id, mission_id, action, risk_level,
        requested_by, created_at, expires_at, time_remaining_seconds.
        """
        now = datetime.now(timezone.utc)
        results: List[Dict[str, Any]] = []
        for record in self._tokens.values():
            if record.resolved:
                continue
            expires_dt = datetime.fromisoformat(record.expires_at)
            if now >= expires_dt:
                continue
            remaining = (expires_dt - now).total_seconds()
            # Look up requested_by from the store
            request = self._store.get(record.request_id)
            requested_by = request.requested_by if request else "unknown"
            results.append({
                "request_id": record.request_id,
                "mission_id": record.mission_id,
                "action": record.action,
                "risk_level": record.risk_level,
                "requested_by": requested_by,
                "created_at": record.created_at,
                "expires_at": record.expires_at,
                "time_remaining_seconds": round(remaining, 1),
            })
        return results

    def expire_stale(self) -> int:
        """Mark expired tokens as resolved(expired). Returns count expired."""
        now = datetime.now(timezone.utc)
        expired: List[Tuple[str, str]] = []  # (token_hash, resolved_at)
        for token_hash, record in self._tokens.items():
            if record.resolved:
                continue
            expires_dt = datetime.fromisoformat(record.expires_at)
            if now >= expires_dt:
                expired.append((token_hash, now.isoformat()))

        for token_hash, resolved_at in expired:
            record = self._tokens[token_hash]
            self._tokens[token_hash] = ApprovalRuntimeToken(
                token_hash=record.token_hash,
                request_id=record.request_id,
                mission_id=record.mission_id,
                action=record.action,
                risk_level=record.risk_level,
                created_at=record.created_at,
                expires_at=record.expires_at,
                resolved=True,
                resolved_at=resolved_at,
                resolution="expired",
            )
        return len(expired)
