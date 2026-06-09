from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

from fireclaw_core.approval_store import JsonlApprovalStore
from fireclaw_core.approval_runtime import ApprovalRuntime, ApprovalRuntimeToken


def _create_request(store: JsonlApprovalStore, *, mission_id: str = "mission-1") -> str:
    """Helper: create an approval request and return its request_id."""
    request = store.create(
        mission_id=mission_id,
        action="enter_building",
        risk_level="high",
        command="去二楼救人",
        requested_by="operator-1",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return request.request_id


# ---- Test 1: create_token returns raw token and record ----

def test_create_token_returns_raw_and_record(tmp_path):
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    runtime = ApprovalRuntime(store)

    request_id = _create_request(store)
    raw_token, record = runtime.create_token(request_id)

    # Raw token must be 64 hex chars (32 bytes)
    assert len(raw_token) == 64
    assert all(c in "0123456789abcdef" for c in raw_token)

    # Record fields
    assert record.request_id == request_id
    assert record.mission_id == "mission-1"
    assert record.action == "enter_building"
    assert record.risk_level == "high"
    assert record.resolved is False
    assert record.resolved_at is None
    assert record.resolution is None

    # Hash matches
    expected_hash = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
    assert record.token_hash == expected_hash


# ---- Test 2: resolve_token with valid token ----

def test_resolve_token_valid(tmp_path):
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    runtime = ApprovalRuntime(store)

    request_id = _create_request(store)
    raw_token, original_record = runtime.create_token(request_id)

    resolved = runtime.resolve_token(raw_token)
    assert resolved is not None
    assert resolved.token_hash == original_record.token_hash
    assert resolved.request_id == request_id
    assert resolved.mission_id == "mission-1"
    assert resolved.resolved is False


# ---- Test 3: resolve_token returns None for expired token ----

def test_resolve_token_expired(tmp_path):
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    runtime = ApprovalRuntime(store, token_ttl_seconds=1)

    request_id = _create_request(store)
    raw_token, _ = runtime.create_token(request_id)

    # Wait for expiry
    time.sleep(1.1)

    resolved = runtime.resolve_token(raw_token)
    assert resolved is None


# ---- Test 4: resolve_token returns None for unknown token ----

def test_resolve_token_not_found(tmp_path):
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    runtime = ApprovalRuntime(store)

    resolved = runtime.resolve_token("00" * 32)
    assert resolved is None


# ---- Test 5: pending_projection does not leak raw tokens ----

def test_pending_projection_no_secrets(tmp_path):
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    runtime = ApprovalRuntime(store)

    request_id = _create_request(store)
    raw_token, _ = runtime.create_token(request_id)

    projection = runtime.pending_projection()
    assert len(projection) == 1

    entry = projection[0]
    assert entry["request_id"] == request_id
    assert entry["mission_id"] == "mission-1"
    assert entry["action"] == "enter_building"
    assert entry["risk_level"] == "high"
    assert entry["requested_by"] == "operator-1"
    assert "expires_at" in entry
    assert "time_remaining_seconds" in entry
    assert entry["time_remaining_seconds"] > 0

    # Raw token must NOT appear anywhere in the projection
    projection_str = str(projection)
    assert raw_token not in projection_str
    assert "token_hash" not in entry


# ---- Test 6: expire_stale marks expired tokens ----

def test_expire_stale(tmp_path):
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    runtime = ApprovalRuntime(store, token_ttl_seconds=1)

    request_id = _create_request(store)
    raw_token, record = runtime.create_token(request_id)

    time.sleep(1.1)

    expired_count = runtime.expire_stale()
    assert expired_count == 1

    # Token should now be resolved as expired
    resolved = runtime.resolve_token(raw_token)
    assert resolved is not None
    assert resolved.resolved is True
    assert resolved.resolution == "expired"
    assert resolved.resolved_at is not None


# ---- Test 7: multiple tokens for different requests are independent ----

def test_multiple_tokens_for_different_requests(tmp_path):
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    runtime = ApprovalRuntime(store)

    rid1 = _create_request(store, mission_id="mission-1")
    rid2 = _create_request(store, mission_id="mission-2")

    raw1, rec1 = runtime.create_token(rid1)
    raw2, rec2 = runtime.create_token(rid2)

    assert raw1 != raw2
    assert rec1.token_hash != rec2.token_hash
    assert rec1.mission_id == "mission-1"
    assert rec2.mission_id == "mission-2"

    # Resolving one does not return the other
    resolved1 = runtime.resolve_token(raw1)
    assert resolved1 is not None
    assert resolved1.request_id == rid1

    resolved2 = runtime.resolve_token(raw2)
    assert resolved2 is not None
    assert resolved2.request_id == rid2

    # Projection returns both
    projection = runtime.pending_projection()
    assert len(projection) == 2


# ---- Test 8: token hash is deterministic ----

def test_token_hash_is_deterministic(tmp_path):
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    runtime = ApprovalRuntime(store)

    request_id = _create_request(store)
    raw_token, record = runtime.create_token(request_id)

    # Hashing the same raw token always produces the same hash
    hash1 = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
    hash2 = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
    assert hash1 == hash2
    assert record.token_hash == hash1
