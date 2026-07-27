"""Replication policy, signing, and admission tests (Task 6 RED)."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fireclaw_core.memory.embodied_memory import (
    EmbodiedMemoryEvent,
    EmbodiedMemoryProducer,
    EmbodiedMemoryRelation,
    EmbodiedMemoryStore,
    MEMORY_RUNTIME_MODES,
    MEMORY_SENSITIVITY_LEVELS,
    MemoryEventProvenance,
)
from fireclaw_core.memory.reconciliation import (
    EmbodiedMemoryReconciler,
    EmbodiedMemoryReplicationExporter,
    ReplicationBatch,
    ReplicationEnvelope,
    _record_checksum,
)
from fireclaw_core.memory.replication_security import (
    InMemoryReplicationKeyProvider,
    ReplicationAuthMetadata,
    ReplicationKeyProvider,
    ReplicationNonceCache,
    ReplicationPeerPolicy,
    ReplicationRequestScope,
    ReplicationSigningIdentity,
    encode_auth_header,
    decode_auth_header,
    sign_batch,
    sign_payload,
    sign_request,
    verify_signed_batch,
    verify_signed_payload,
    verify_signed_request,
)
from fireclaw_core.agent.robot_registry import RobotRegistryEntry


def _robot_entry(robot_id: str = "robot-1") -> RobotRegistryEntry:
    return RobotRegistryEntry(
        robot_id=robot_id,
        base_url=f"https://{robot_id}.local",
    )


def _make_event(
    store: EmbodiedMemoryStore,
    *,
    event_id: str,
    mission_id: str = "mission-1",
    runtime_mode: str = "simulation",
    sensitivity: str = "standard",
    robot_id: str | None = "robot-1",
    observed_at: str = "2026-07-24T10:00:00+00:00",
) -> EmbodiedMemoryEvent:
    event = EmbodiedMemoryEvent(
        event_id=event_id,
        mission_id=mission_id,
        event_type="observation",
        payload={"ts": observed_at, "text": f"data for {event_id}"},
        runtime_mode=runtime_mode,
        source_type="sensor",
        observed_at=observed_at,
        robot_id=robot_id,
        sensitivity=sensitivity,
        provenance=MemoryEventProvenance(
            producer_type="robot_adapter",
            producer_id=robot_id or "robot-1",
            evidence_kind="runtime_evidence",
        ),
    )
    store.append_event(event)
    return event


def _make_relation(
    store: EmbodiedMemoryStore,
    *,
    relation_id: str,
    source_record_id: str,
    target_record_id: str,
    mission_id: str = "mission-1",
    runtime_mode: str = "simulation",
) -> EmbodiedMemoryRelation:
    relation = EmbodiedMemoryRelation(
        relation_id=relation_id,
        mission_id=mission_id,
        relation_type="supports",
        source_record_id=source_record_id,
        target_record_id=target_record_id,
        runtime_mode=runtime_mode,
        created_at="2026-07-24T10:00:00+00:00",
    )
    store.append_relation(relation)
    return relation


def _default_policy(
    peer_id: str = "mission-gateway-1",
    allowed_sensitivities: frozenset[str] = frozenset({"standard"}),
) -> ReplicationPeerPolicy:
    return ReplicationPeerPolicy(
        policy_id="policy-1",
        peer_id=peer_id,
        allowed_robot_ids=frozenset({"robot-1"}),
        allowed_runtime_modes=frozenset({"simulation"}),
        allowed_sensitivities=allowed_sensitivities,
    )


def _default_key_provider() -> InMemoryReplicationKeyProvider:
    provider = InMemoryReplicationKeyProvider()
    provider.register_key(
        peer_id="mission-gateway-1",
        key_id="key-1",
        secret=b"test-secret-key-for-replication-000",
    )
    return provider


# --- Task 6 Tests ---


def test_export_policy_omits_disallowed_sensitivity_and_advances_cursor(
    tmp_path: Path,
) -> None:
    """Exporter must skip restricted events when policy only allows standard,
    and cursor must advance past omitted records."""
    store = EmbodiedMemoryStore(tmp_path / "store.jsonl")
    _make_event(store, event_id="evt-1", sensitivity="standard")
    _make_event(store, event_id="evt-2", sensitivity="restricted")
    _make_event(store, event_id="evt-3", sensitivity="standard")
    exporter = EmbodiedMemoryReplicationExporter(
        store=store,
        source_store_id="store-1",
        source_robot_id="robot-1",
    )
    policy = _default_policy(allowed_sensitivities=frozenset({"standard"}))
    batch = exporter.export_batch(
        cursor=0,
        limit=100,
        mission_id="mission-1",
        runtime_mode="simulation",
        peer_policy=policy,
    )
    exported_ids = {env.source_record_id for env in batch.envelopes}
    assert "evt-1" in exported_ids
    assert "evt-2" not in exported_ids
    assert "evt-3" in exported_ids
    assert batch.next_cursor == 3
    assert batch.cursor == 0


def test_relation_is_omitted_when_endpoint_is_not_exportable(
    tmp_path: Path,
) -> None:
    """A relation whose target is restricted must be omitted when policy
    excludes restricted sensitivity."""
    store = EmbodiedMemoryStore(tmp_path / "store.jsonl")
    _make_event(store, event_id="evt-1", sensitivity="standard")
    _make_event(store, event_id="evt-2", sensitivity="restricted")
    _make_relation(
        store,
        relation_id="rel-1",
        source_record_id="evt-1",
        target_record_id="evt-2",
    )
    exporter = EmbodiedMemoryReplicationExporter(
        store=store,
        source_store_id="store-1",
        source_robot_id="robot-1",
    )
    policy = _default_policy(allowed_sensitivities=frozenset({"standard"}))
    batch = exporter.export_batch(
        cursor=0,
        limit=100,
        mission_id="mission-1",
        runtime_mode="simulation",
        peer_policy=policy,
    )
    exported_ids = {env.source_record_id for env in batch.envelopes}
    assert "evt-1" in exported_ids
    assert "evt-2" not in exported_ids
    assert "rel-1" not in exported_ids


def test_signed_batch_round_trip(tmp_path: Path) -> None:
    """A signed batch must verify successfully with the correct peer/key."""
    store = EmbodiedMemoryStore(tmp_path / "store.jsonl")
    _make_event(store, event_id="evt-1")
    exporter = EmbodiedMemoryReplicationExporter(
        store=store,
        source_store_id="store-1",
        source_robot_id="robot-1",
    )
    policy = _default_policy()
    key_provider = _default_key_provider()
    batch = exporter.export_batch(
        cursor=0,
        limit=100,
        mission_id="mission-1",
        runtime_mode="simulation",
        peer_policy=policy,
    )
    scope = ReplicationRequestScope(
        mission_id="mission-1",
        runtime_mode="simulation",
        cursor=0,
        limit=100,
    )
    auth = sign_batch(
        batch=batch,
        scope=scope,
        key_provider=key_provider,
        peer_id="mission-gateway-1",
        key_id="key-1",
    )
    result = verify_signed_batch(
        batch=batch,
        auth=auth,
        scope=scope,
        key_provider=key_provider,
        peer_id="mission-gateway-1",
        max_age_seconds=300,
    )
    assert result.verified is True
    assert result.error_code is None


def test_tampered_batch_or_scope_fails_verification(tmp_path: Path) -> None:
    """Batch verification must fail when the batch or scope is tampered."""
    store = EmbodiedMemoryStore(tmp_path / "store.jsonl")
    _make_event(store, event_id="evt-1")
    exporter = EmbodiedMemoryReplicationExporter(
        store=store,
        source_store_id="store-1",
        source_robot_id="robot-1",
    )
    policy = _default_policy()
    key_provider = _default_key_provider()
    batch = exporter.export_batch(
        cursor=0,
        limit=100,
        mission_id="mission-1",
        runtime_mode="simulation",
        peer_policy=policy,
    )
    scope = ReplicationRequestScope(
        mission_id="mission-1",
        runtime_mode="simulation",
        cursor=0,
        limit=100,
    )
    auth = sign_batch(
        batch=batch,
        scope=scope,
        key_provider=key_provider,
        peer_id="mission-gateway-1",
        key_id="key-1",
    )
    tampered_scope = ReplicationRequestScope(
        mission_id="mission-OTHER",
        runtime_mode="simulation",
        cursor=0,
        limit=100,
    )
    result = verify_signed_batch(
        batch=batch,
        auth=auth,
        scope=tampered_scope,
        key_provider=key_provider,
        peer_id="mission-gateway-1",
        max_age_seconds=300,
    )
    assert result.verified is False
    assert result.error_code == "signature_invalid"


def test_wrong_peer_robot_store_or_key_is_rejected(tmp_path: Path) -> None:
    """Verification must fail when the peer or key does not match."""
    store = EmbodiedMemoryStore(tmp_path / "store.jsonl")
    _make_event(store, event_id="evt-1")
    exporter = EmbodiedMemoryReplicationExporter(
        store=store,
        source_store_id="store-1",
        source_robot_id="robot-1",
    )
    policy = _default_policy()
    key_provider = _default_key_provider()
    batch = exporter.export_batch(
        cursor=0,
        limit=100,
        mission_id="mission-1",
        runtime_mode="simulation",
        peer_policy=policy,
    )
    scope = ReplicationRequestScope(
        mission_id="mission-1",
        runtime_mode="simulation",
        cursor=0,
        limit=100,
    )
    auth = sign_batch(
        batch=batch,
        scope=scope,
        key_provider=key_provider,
        peer_id="mission-gateway-1",
        key_id="key-1",
    )
    result = verify_signed_batch(
        batch=batch,
        auth=auth,
        scope=scope,
        key_provider=key_provider,
        peer_id="wrong-peer",
        max_age_seconds=300,
    )
    assert result.verified is False
    assert result.error_code == "peer_mismatch"


def test_expired_timestamp_and_reused_nonce_are_rejected(tmp_path: Path) -> None:
    """Verification must reject expired timestamps and reused nonces."""
    store = EmbodiedMemoryStore(tmp_path / "store.jsonl")
    _make_event(store, event_id="evt-1")
    exporter = EmbodiedMemoryReplicationExporter(
        store=store,
        source_store_id="store-1",
        source_robot_id="robot-1",
    )
    policy = _default_policy()
    key_provider = _default_key_provider()
    batch = exporter.export_batch(
        cursor=0,
        limit=100,
        mission_id="mission-1",
        runtime_mode="simulation",
        peer_policy=policy,
    )
    scope = ReplicationRequestScope(
        mission_id="mission-1",
        runtime_mode="simulation",
        cursor=0,
        limit=100,
    )
    expired_at = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    auth = sign_batch(
        batch=batch,
        scope=scope,
        key_provider=key_provider,
        peer_id="mission-gateway-1",
        key_id="key-1",
        issued_at=expired_at,
        nonce="expired-nonce-001",
    )
    result = verify_signed_batch(
        batch=batch,
        auth=auth,
        scope=scope,
        key_provider=key_provider,
        peer_id="mission-gateway-1",
        max_age_seconds=300,
    )
    assert result.verified is False
    assert result.error_code == "timestamp_expired"
    now_at = datetime.now(timezone.utc).isoformat()
    nonce_cache = ReplicationNonceCache(max_entries=32)
    auth1 = sign_batch(
        batch=batch,
        scope=scope,
        key_provider=key_provider,
        peer_id="mission-gateway-1",
        key_id="key-1",
        issued_at=now_at,
        nonce="unique-nonce-001",
    )
    result1 = verify_signed_batch(
        batch=batch,
        auth=auth1,
        scope=scope,
        key_provider=key_provider,
        peer_id="mission-gateway-1",
        max_age_seconds=300,
        nonce_cache=nonce_cache,
    )
    assert result1.verified is True
    auth2 = sign_batch(
        batch=batch,
        scope=scope,
        key_provider=key_provider,
        peer_id="mission-gateway-1",
        key_id="key-1",
        issued_at=now_at,
        nonce="unique-nonce-001",
    )
    result2 = verify_signed_batch(
        batch=batch,
        auth=auth2,
        scope=scope,
        key_provider=key_provider,
        peer_id="mission-gateway-1",
        max_age_seconds=300,
        nonce_cache=nonce_cache,
    )
    assert result2.verified is False
    assert result2.error_code == "nonce_reused"


def test_real_runtime_rejects_unsigned_v1_batch(tmp_path: Path) -> None:
    """Reconciler must reject unsigned v1 batches in real runtime mode."""
    store = EmbodiedMemoryStore(tmp_path / "store.jsonl")
    reconciler = EmbodiedMemoryReconciler(
        destination=store,
        state_path=tmp_path / "state.jsonl",
        runtime_mode="real",
    )
    v1_batch = ReplicationBatch(
        source_store_id="store-1",
        source_robot_id="robot-1",
        cursor=0,
        next_cursor=0,
        has_more=False,
        envelopes=(),
    )
    try:
        reconciler.ingest_batch(
            v1_batch,
            expected_robot_id="robot-1",
            expected_mission_id="mission-1",
            auth=None,
        )
        raise AssertionError("expected ValueError for unsigned v1 batch in real mode")
    except ValueError as exc:
        msg = str(exc).lower()
        assert "unsigned" in msg or "signed" in msg or "auth" in msg


def test_simulation_can_explicitly_allow_legacy_unsigned_batch(
    tmp_path: Path,
) -> None:
    """Reconciler must accept unsigned v1 batches when allow_legacy_unsigned=True
    in simulation mode."""
    store = EmbodiedMemoryStore(tmp_path / "store.jsonl")
    reconciler = EmbodiedMemoryReconciler(
        destination=store,
        state_path=tmp_path / "state.jsonl",
        runtime_mode="simulation",
        allow_legacy_unsigned=True,
    )
    v1_batch = ReplicationBatch(
        source_store_id="store-1",
        source_robot_id="robot-1",
        cursor=0,
        next_cursor=0,
        has_more=False,
        envelopes=(),
    )
    report = reconciler.ingest_batch(
        v1_batch,
        expected_robot_id="robot-1",
        expected_mission_id="mission-1",
        auth=None,
    )
    assert report.imported == 0
    assert report.rejected == 0


# --- Task 1 RED tests: fail-closed replication primitives ---


def test_non_null_auth_without_verification_dependencies_fails_closed(
    tmp_path: Path,
) -> None:
    """Partial auth config (auth present but no key_provider/peer_id) must fail
    in real runtime, not silently skip verification."""
    destination = EmbodiedMemoryStore(tmp_path / "destination.jsonl")
    reconciler = EmbodiedMemoryReconciler(
        destination=destination,
        state_path=tmp_path / "replication-state.jsonl",
        runtime_mode="real",
    )
    batch = ReplicationBatch(
        source_store_id="robot:robot-1",
        source_robot_id="robot-1",
        cursor=0,
        next_cursor=0,
        has_more=False,
        envelopes=(),
    )
    auth = ReplicationAuthMetadata(
        protocol_version=2,
        peer_id="robot-1",
        key_id="key-1",
        issued_at=datetime.now(timezone.utc).isoformat(),
        nonce="forged-nonce",
        body_digest="0" * 64,
        signature="0" * 64,
    )
    try:
        reconciler.ingest_batch(
            batch,
            expected_robot_id="robot-1",
            expected_mission_id="mission-1",
            auth=auth,
            key_provider=None,
            expected_peer_id=None,
        )
        raise AssertionError("expected partial authentication configuration to fail")
    except ValueError as exc:
        msg = str(exc).lower()
        assert "signed" in msg or "legacy" in msg or "key_provider" in msg
    assert destination.list_events() == []


def test_invalid_signature_does_not_consume_nonce(tmp_path: Path) -> None:
    """An invalid signature must not consume the nonce, allowing a subsequent
    valid request with the same nonce to succeed."""
    store = EmbodiedMemoryStore(tmp_path / "source.jsonl")
    _make_event(store, event_id="evt-1")
    exporter = EmbodiedMemoryReplicationExporter(
        store=store,
        source_store_id="robot:robot-1",
        source_robot_id="robot-1",
    )
    batch = exporter.export_batch(
        cursor=0,
        limit=10,
        mission_id="mission-1",
        runtime_mode="simulation",
        peer_policy=_default_policy(peer_id="robot-1"),
    )
    scope = ReplicationRequestScope(
        mission_id="mission-1",
        runtime_mode="simulation",
        cursor=0,
        limit=10,
    )
    keys = _default_key_provider()
    valid = sign_request(
        body={},
        scope=scope,
        key_provider=keys,
        peer_id="mission-gateway-1",
        key_id="key-1",
        nonce="same-nonce",
    )
    invalid = ReplicationAuthMetadata(
        protocol_version=valid.protocol_version,
        peer_id=valid.peer_id,
        key_id=valid.key_id,
        issued_at=valid.issued_at,
        nonce=valid.nonce,
        body_digest=valid.body_digest,
        signature="0" * 64,
    )
    cache = ReplicationNonceCache(max_entries=32)
    first = verify_signed_request(
        body={},
        auth=invalid,
        scope=scope,
        key_provider=keys,
        peer_id="mission-gateway-1",
        nonce_cache=cache,
    )
    second = verify_signed_request(
        body={},
        auth=valid,
        scope=scope,
        key_provider=keys,
        peer_id="mission-gateway-1",
        nonce_cache=cache,
    )
    assert first.error_code == "signature_invalid"
    assert second.verified is True


def test_naive_timestamp_returns_invalid_timestamp(tmp_path: Path) -> None:
    """A timestamp without timezone info must be rejected as invalid."""
    store = EmbodiedMemoryStore(tmp_path / "source.jsonl")
    _make_event(store, event_id="evt-1")
    batch = EmbodiedMemoryReplicationExporter(
        store=store,
        source_store_id="robot:robot-1",
        source_robot_id="robot-1",
    ).export_batch(
        cursor=0,
        limit=10,
        mission_id="mission-1",
        runtime_mode="simulation",
        peer_policy=_default_policy(),
    )
    scope = ReplicationRequestScope("mission-1", "simulation", 0, 10)
    keys = _default_key_provider()
    auth = sign_request(
        body={},
        scope=scope,
        key_provider=keys,
        peer_id="mission-gateway-1",
        key_id="key-1",
        issued_at="2026-07-24T10:00:00",
        nonce="naive-time",
    )
    result = verify_signed_request(
        body={},
        auth=auth,
        scope=scope,
        key_provider=keys,
        peer_id="mission-gateway-1",
    )
    assert result.verified is False
    assert result.error_code == "invalid_timestamp"


def test_default_nonces_are_random() -> None:
    """Two sign_request calls with the same parameters must produce different
    nonces (secrets-based randomness)."""
    keys = _default_key_provider()
    scope = ReplicationRequestScope("mission-1", "simulation", 0, 10)
    first = sign_request(
        body={},
        scope=scope,
        key_provider=keys,
        peer_id="mission-gateway-1",
        key_id="key-1",
    )
    second = sign_request(
        body={},
        scope=scope,
        key_provider=keys,
        peer_id="mission-gateway-1",
        key_id="key-1",
    )
    assert first.nonce != second.nonce
    assert len(first.nonce) >= 32


def test_peer_policy_rejects_unknown_runtime_and_sensitivity() -> None:
    """ReplicationPeerPolicy must reject unknown runtime modes and sensitivity
    levels at construction time."""
    for runtimes, sensitivities in (
        (frozenset({"unknown"}), frozenset({"standard"})),
        (frozenset({"simulation"}), frozenset({"secret"})),
    ):
        try:
            ReplicationPeerPolicy(
                policy_id="policy-1",
                peer_id="peer-1",
                allowed_robot_ids=frozenset({"robot-1"}),
                allowed_runtime_modes=runtimes,
                allowed_sensitivities=sensitivities,
            )
            raise AssertionError("expected invalid policy value to fail")
        except ValueError:
            pass


def test_ingest_signed_payload_verifies_before_parsing(tmp_path: Path) -> None:
    """ingest_signed_payload must verify the raw payload signature before
    parsing the batch. A tampered payload must be rejected."""
    destination = EmbodiedMemoryStore(tmp_path / "destination.jsonl")
    reconciler = EmbodiedMemoryReconciler(
        destination=destination,
        state_path=tmp_path / "state.jsonl",
        runtime_mode="simulation",
    )
    source = EmbodiedMemoryStore(tmp_path / "source.jsonl")
    _make_event(source, event_id="evt-1")
    exporter = EmbodiedMemoryReplicationExporter(
        store=source,
        source_store_id="robot:robot-1",
        source_robot_id="robot-1",
    )
    # Policy allows robot-1 in simulation
    policy = ReplicationPeerPolicy(
        policy_id="policy-1",
        peer_id="robot-1",
        allowed_robot_ids=frozenset({"robot-1"}),
        allowed_runtime_modes=frozenset({"simulation"}),
        allowed_sensitivities=frozenset({"standard"}),
    )
    batch = exporter.export_batch(
        cursor=0,
        limit=10,
        mission_id="mission-1",
        runtime_mode="simulation",
        peer_policy=policy,
    )
    scope = ReplicationRequestScope("mission-1", "simulation", 0, 10)
    # Key provider with robot-1 as peer (robot gateway signs the response)
    keys = InMemoryReplicationKeyProvider()
    keys.register_key(
        peer_id="robot-1",
        key_id="robot-key",
        secret=b"robot-secret-for-testing-00000000",
    )
    body = batch.to_dict()
    auth = sign_payload(
        payload=body,
        scope=scope,
        key_provider=keys,
        identity=ReplicationSigningIdentity(
            peer_id="robot-1",
            key_id="robot-key",
        ),
    )
    cache = ReplicationNonceCache(max_entries=32)
    # Valid payload succeeds
    report = reconciler.ingest_signed_payload(
        {**body, "auth": auth.to_dict()},
        expected_robot_id="robot-1",
        expected_mission_id="mission-1",
        expected_scope=scope,
        key_provider=keys,
        peer_policy=policy,
        nonce_cache=cache,
    )
    assert report.imported == 1

    # Tampered payload (different cursor) fails verification
    tampered = {**body, "cursor": 999, "auth": auth.to_dict()}
    try:
        reconciler.ingest_signed_payload(
            tampered,
            expected_robot_id="robot-1",
            expected_mission_id="mission-1",
            expected_scope=scope,
            key_provider=keys,
            peer_policy=policy,
            nonce_cache=ReplicationNonceCache(max_entries=32),
        )
        raise AssertionError("expected tampered payload to fail")
    except ValueError as exc:
        assert "verification failed" in str(exc).lower()


def test_encode_decode_auth_header_round_trip() -> None:
    """encode_auth_header / decode_auth_header must round-trip correctly."""
    auth = ReplicationAuthMetadata(
        protocol_version=2,
        peer_id="robot-1",
        key_id="key-1",
        issued_at="2026-07-24T10:00:00+00:00",
        nonce="test-nonce-123",
        body_digest="a" * 64,
        signature="b" * 64,
    )
    encoded = encode_auth_header(auth)
    assert isinstance(encoded, str)
    assert len(encoded) > 0
    decoded = decode_auth_header(encoded)
    assert decoded == auth


def test_replication_nonce_cache_is_bounded() -> None:
    """ReplicationNonceCache must evict oldest entries when full."""
    cache = ReplicationNonceCache(max_entries=3)
    assert cache.consume("a") is True
    assert cache.consume("b") is True
    assert cache.consume("c") is True
    assert cache.consume("a") is False  # already seen
    assert cache.consume("d") is True  # evicts oldest
    assert cache.consume("a") is True  # 'a' was evicted


if __name__ == "__main__":
    import inspect
    import sys

    _tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test_fn in _tests:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            try:
                if "tmp_path" in inspect.signature(test_fn).parameters:
                    test_fn(Path(td))
                else:
                    test_fn()
                print(f"PASS {test_fn.__name__}")
            except Exception as exc:
                print(f"FAIL {test_fn.__name__}: {exc}")
                failures += 1
    if failures:
        print(f"\n{failures} FAILED")
        sys.exit(1)
    else:
        print(f"\nAll {len(_tests)} tests passed")
