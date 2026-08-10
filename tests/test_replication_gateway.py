"""Authenticated replication gateway integration tests."""
from __future__ import annotations

import json
import io
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.memory.embodied_memory import (
    EmbodiedMemoryEvent,
    EmbodiedMemoryProducer,
    EmbodiedMemoryRelation,
    EmbodiedMemoryStore,
    MemoryEventProvenance,
)
from fireclaw_core.memory.reconciliation import (
    EmbodiedMemoryReconciler,
    EmbodiedMemoryReplicationExporter,
    ReplicationBatch,
)
from fireclaw_core.memory.replication_security import (
    REPLICATION_AUTH_HEADER,
    InMemoryReplicationKeyProvider,
    ReplicationAuthMetadata,
    ReplicationClientSecurity,
    ReplicationNonceCache,
    ReplicationPeerPolicy,
    ReplicationRequestScope,
    ReplicationServerSecurity,
    ReplicationSigningIdentity,
    decode_auth_header,
    encode_auth_header,
    sign_batch,
    sign_payload,
    sign_request,
    verify_signed_request,
)
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig
from fireclaw_core.mission.mission_registry import JsonlMissionRegistry


def _robot_entry(
    robot_id: str = "robot-1", base_url: str = "https://robot-1.local"
) -> RobotRegistryEntry:
    return RobotRegistryEntry(robot_id=robot_id, base_url=base_url)


def _make_event(
    store: EmbodiedMemoryStore,
    *,
    event_id: str,
    mission_id: str = "mission-1",
    runtime_mode: str = "simulation",
    sensitivity: str = "standard",
    robot_id: str | None = "robot-1",
) -> None:
    event = EmbodiedMemoryEvent(
        event_id=event_id,
        mission_id=mission_id,
        event_type="observation",
        payload={"ts": "2026-07-24T10:00:00+00:00", "text": f"data for {event_id}"},
        runtime_mode=runtime_mode,
        source_type="sensor",
        observed_at="2026-07-24T10:00:00+00:00",
        robot_id=robot_id,
        sensitivity=sensitivity,
        provenance=MemoryEventProvenance(
            producer_type="robot_adapter",
            producer_id=robot_id or "unknown",
            evidence_kind="runtime_evidence",
        ),
    )
    store.append_event(event)


def _make_relation(
    store: EmbodiedMemoryStore,
    *,
    relation_id: str,
    source_record_id: str,
    target_record_id: str,
    mission_id: str = "mission-1",
    runtime_mode: str = "simulation",
) -> None:
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


def _default_policy(
    peer_id: str = "mission-gw-1",
) -> ReplicationPeerPolicy:
    return ReplicationPeerPolicy(
        policy_id="policy-1",
        peer_id=peer_id,
        allowed_robot_ids=frozenset({"robot-1"}),
        allowed_runtime_modes=frozenset({"simulation"}),
        allowed_sensitivities=frozenset({"standard"}),
    )


def _key_provider_for(
    *items: tuple[str, str, bytes],
) -> InMemoryReplicationKeyProvider:
    provider = InMemoryReplicationKeyProvider()
    for peer_id, key_id, secret in items:
        provider.register_key(peer_id=peer_id, key_id=key_id, secret=secret)
    return provider


class _FakeSubagentClient:
    """Returns a signed batch when key_provider is set."""

    def __init__(
        self,
        *,
        store: EmbodiedMemoryStore | None = None,
        robot_id: str = "robot-1",
        peer_policy: ReplicationPeerPolicy | None = None,
        key_provider: InMemoryReplicationKeyProvider | None = None,
        peer_id: str = "robot-1",
        key_id: str = "robot-key",
    ) -> None:
        self._store = store
        self._robot_id = robot_id
        self._peer_policy = peer_policy
        self._key_provider = key_provider
        self._peer_id = peer_id
        self._key_id = key_id

    def get_memory_replication(
        self,
        entry: RobotRegistryEntry,
        *,
        cursor: int,
        limit: int,
        mission_id: str,
        runtime_mode: str,
    ) -> dict[str, Any]:
        if self._store is None:
            return {
                "source_store_id": f"robot:{entry.robot_id}",
                "source_robot_id": entry.robot_id,
                "cursor": cursor,
                "next_cursor": cursor,
                "has_more": False,
                "envelopes": [],
            }
        exporter = EmbodiedMemoryReplicationExporter(
            store=self._store,
            source_store_id=f"robot:{entry.robot_id}",
            source_robot_id=entry.robot_id,
        )
        batch = exporter.export_batch(
            cursor=cursor,
            limit=limit,
            mission_id=mission_id,
            runtime_mode=runtime_mode,
            peer_policy=self._peer_policy,
        )
        result = batch.to_dict()
        if self._key_provider is not None:
            scope = ReplicationRequestScope(
                mission_id=mission_id,
                runtime_mode=runtime_mode,
                cursor=cursor,
                limit=max(1, len(batch.envelopes)),
            )
            auth = sign_payload(
                payload=result,
                scope=scope,
                key_provider=self._key_provider,
                identity=ReplicationSigningIdentity(
                    peer_id=self._peer_id,
                    key_id=self._key_id,
                ),
            )
            result["auth"] = auth.to_dict()
        return result


class CountingExporter:
    """Counts export calls; raises if called before verification."""

    def __init__(self) -> None:
        self.calls = 0

    def export_batch(self, **kwargs: Any) -> ReplicationBatch:
        self.calls += 1
        raise AssertionError("export must not run before request verification")


class _JsonResponse:
    """Minimal urllib response adapter for test mocking."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._raw = json.dumps(payload).encode("utf-8")
        self._stream = io.BytesIO(self._raw)
        self.headers = {"Content-Length": str(len(self._raw))}

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def __enter__(self) -> "_JsonResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _gateway_urlopen(gateway: Any):
    """Create a urlopen side effect that routes to the gateway's export service."""
    def open_request(
        request_value: Any,
        timeout: float,
        context: Any | None = None,
    ) -> _JsonResponse:
        _ = context
        parsed = urlparse(request_value.full_url)
        query = parse_qs(parsed.query)
        scope = ReplicationRequestScope(
            mission_id=query["mission_id"][0],
            runtime_mode=query["runtime_mode"][0],
            cursor=int(query["cursor"][0]),
            limit=int(query["limit"][0]),
        )
        auth = decode_auth_header(
            request_value.headers[REPLICATION_AUTH_HEADER.capitalize()]
        )
        return _JsonResponse(
            gateway.export_memory_replication(
                scope=scope,
                request_auth=auth,
            )
        )
    return open_request


def _make_mission_gateway(
    tmp_path: Path,
    *,
    runtime_mode: str = "simulation",
    reconciler: EmbodiedMemoryReconciler | None = None,
    subagent_client: Any | None = None,
    entries: list[RobotRegistryEntry] | None = None,
    replication_security: ReplicationClientSecurity | None = None,
    allow_legacy_unsigned: bool = False,
) -> MissionGateway:
    mission_registry = JsonlMissionRegistry(str(tmp_path / "missions.jsonl"))
    agent = MissionAgent(
        registry=RobotRegistry(entries or [_robot_entry()]),
        subagent_client=subagent_client or _FakeSubagentClient(),
        mission_registry=mission_registry,
    )
    if reconciler is not None and allow_legacy_unsigned:
        reconciler.allow_legacy_unsigned = True
    gw = MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=RobotRegistry(entries or [_robot_entry()]),
        subagent_client=subagent_client or _FakeSubagentClient(),
        memory_reconciler=reconciler,
        replication_security=replication_security,
    )
    return gw


# --- Tests ---


def test_replication_endpoint_requires_exact_mission_and_runtime(
    tmp_path: Path,
) -> None:
    """sync_robot_memory requires a configured reconciler."""
    gw = _make_mission_gateway(tmp_path, reconciler=None)
    result = gw.sync_robot_memory("mission-1")
    assert result["status"] == "not_configured"


def test_replication_endpoint_rejects_unverified_peer_before_export(
    tmp_path: Path,
) -> None:
    """sync must reject unsigned batches when real runtime requires auth."""
    store = EmbodiedMemoryStore(tmp_path / "dest.jsonl")
    reconciler = EmbodiedMemoryReconciler(
        destination=store,
        state_path=tmp_path / "state.jsonl",
        runtime_mode="real",
    )
    client = _FakeSubagentClient()
    gw = _make_mission_gateway(
        tmp_path,
        runtime_mode="real",
        reconciler=reconciler,
        subagent_client=client,
    )
    result = gw.sync_robot_memory("mission-1")
    robot_result = result["robots"][0]
    assert robot_result["status"] == "error"
    assert "signed" in robot_result["error"].lower() or "security" in robot_result["error"].lower()


def test_real_client_builds_signed_exact_scope_request(tmp_path: Path) -> None:
    """Real RobotSubagentClient signs the exact request scope."""
    from fireclaw_core.subagent.subagent_client import RobotSubagentClient

    keys = _key_provider_for(
        ("mission-gw-1", "coordinator-key", b"coordinator-secret-000000000000"),
    )
    client = RobotSubagentClient(
        replication_identity=ReplicationSigningIdentity(
            peer_id="mission-gw-1",
            key_id="coordinator-key",
        ),
        replication_key_provider=keys,
    )
    entry = _robot_entry()
    request_value = client._build_memory_replication_request(
        entry,
        cursor=7,
        limit=20,
        mission_id="mission-1",
        runtime_mode="simulation",
    )
    auth = decode_auth_header(
        request_value.headers[REPLICATION_AUTH_HEADER.capitalize()]
    )
    scope = ReplicationRequestScope("mission-1", "simulation", 7, 20)
    result = verify_signed_request(
        body={},
        auth=auth,
        scope=scope,
        key_provider=keys,
        peer_id="mission-gw-1",
    )
    assert result.verified is True
    assert "mission_id=mission-1" in request_value.full_url
    assert "runtime_mode=simulation" in request_value.full_url


def test_robot_gateway_rejects_request_before_export(tmp_path: Path) -> None:
    """Robot Gateway must verify request before exporting."""
    from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig

    keys = _key_provider_for(
        ("mission-gw-1", "coordinator-key", b"coordinator-secret-000000000000"),
        ("robot-1", "robot-key", b"robot-secret-00000000000000000"),
    )
    policy = _default_policy(peer_id="mission-gw-1")
    exporter = CountingExporter()
    gateway = FireClawGateway(
        GatewayConfig(
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "legacy.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
        ),
        replication_security=ReplicationServerSecurity(
            identity=ReplicationSigningIdentity("robot-1", "robot-key"),
            key_provider=keys,
            peer_policies={"mission-gw-1": policy},
            nonce_cache=ReplicationNonceCache(max_entries=32),
        ),
    )
    gateway.memory_replication_exporter = exporter
    scope = ReplicationRequestScope("mission-1", "simulation", 0, 20)
    forged = ReplicationAuthMetadata(
        protocol_version=2,
        peer_id="mission-gw-1",
        key_id="coordinator-key",
        issued_at="2026-07-24T10:00:00+00:00",
        nonce="forged",
        body_digest="0" * 64,
        signature="0" * 64,
    )
    try:
        gateway.export_memory_replication(scope=scope, request_auth=forged)
        raise AssertionError("expected forged request to fail")
    except ValueError as exc:
        assert "verification failed" in str(exc).lower()
    assert exporter.calls == 0


def test_real_client_robot_gateway_and_reconciler_round_trip(
    tmp_path: Path,
) -> None:
    """Full round-trip: real client -> robot gateway -> reconciler."""
    robot_gateway, client, mission_gateway, destination = (
        _build_real_replication_stack(tmp_path)
    )
    with patch.object(
        client._transport,
        "open",
        side_effect=_gateway_urlopen(robot_gateway),
    ):
        result = mission_gateway.sync_robot_memory(
            "mission-1",
            robot_id="robot-1",
        )
    assert result["status"] == "synced"
    assert [event.event_id for event in destination.list_events()] == ["evt-1"]


def test_real_path_omits_restricted_event_and_hidden_relation(
    tmp_path: Path,
) -> None:
    """Restricted events and hidden relations must not reach destination."""
    robot_gateway, client, mission_gateway, destination = (
        _build_real_replication_stack(
            tmp_path,
            include_restricted_source=True,
        )
    )
    with patch.object(
        client._transport,
        "open",
        side_effect=_gateway_urlopen(robot_gateway),
    ):
        result = mission_gateway.sync_robot_memory(
            "mission-1",
            robot_id="robot-1",
        )
    imported_ids = {event.event_id for event in destination.list_events()}
    assert imported_ids == {"evt-standard"}


def test_real_sync_rejects_http_robot_endpoint(tmp_path: Path) -> None:
    """sync must reject HTTP robot endpoints in real runtime."""
    store = EmbodiedMemoryStore(tmp_path / "dest.jsonl")
    reconciler = EmbodiedMemoryReconciler(
        destination=store,
        state_path=tmp_path / "state.jsonl",
        runtime_mode="real",
    )
    entries = [_robot_entry("robot-1", "http://robot-1.local")]
    gw = _make_mission_gateway(
        tmp_path,
        runtime_mode="real",
        reconciler=reconciler,
        entries=entries,
    )
    result = gw.sync_robot_memory("mission-1")
    robot_result = result["robots"][0]
    assert robot_result["status"] == "error"
    assert "https" in robot_result["error"].lower() or "transport" in robot_result["error"].lower()


def test_simulation_legacy_allows_unsigned(tmp_path: Path) -> None:
    """Simulation with allow_legacy_unsigned accepts unsigned batches."""
    store = EmbodiedMemoryStore(tmp_path / "dest.jsonl")
    reconciler = EmbodiedMemoryReconciler(
        destination=store,
        state_path=tmp_path / "state.jsonl",
        runtime_mode="simulation",
        allow_legacy_unsigned=True,
    )
    client = _FakeSubagentClient()
    gw = _make_mission_gateway(
        tmp_path,
        reconciler=reconciler,
        subagent_client=client,
        allow_legacy_unsigned=True,
    )
    result = gw.sync_robot_memory("mission-1")
    assert result["robots"][0]["status"] == "synced"


def test_sync_reports_content_free_auth_and_policy_failures(
    tmp_path: Path,
) -> None:
    """Error messages must not leak secrets or evidence payloads."""
    store = EmbodiedMemoryStore(tmp_path / "dest.jsonl")
    reconciler = EmbodiedMemoryReconciler(
        destination=store,
        state_path=tmp_path / "state.jsonl",
        runtime_mode="simulation",
    )
    gw = _make_mission_gateway(
        tmp_path,
        reconciler=reconciler,
    )
    result = gw.sync_robot_memory("mission-1")
    robot_result = result["robots"][0]
    error = robot_result.get("error", "")
    if error:
        assert "secret" not in error.lower()
        assert "signature" not in error.lower()
        assert "key" not in error.lower()


def _build_real_replication_stack(
    tmp_path: Path,
    *,
    include_restricted_source: bool = False,
) -> tuple[Any, Any, MissionGateway, EmbodiedMemoryStore]:
    """Build a full replication stack with real classes."""
    keys = _key_provider_for(
        ("mission-gw-1", "coordinator-key", b"coordinator-secret-000000000000"),
        ("robot-1", "robot-key", b"robot-secret-00000000000000000"),
    )
    export_policy = ReplicationPeerPolicy(
        policy_id="policy-1",
        peer_id="mission-gw-1",
        allowed_robot_ids=frozenset({"robot-1"}),
        allowed_runtime_modes=frozenset({"simulation"}),
        allowed_sensitivities=frozenset({"standard"}),
    )
    import_policy = ReplicationPeerPolicy(
        policy_id="policy-1",
        peer_id="robot-1",
        allowed_robot_ids=frozenset({"robot-1"}),
        allowed_runtime_modes=frozenset({"simulation"}),
        allowed_sensitivities=frozenset({"standard"}),
    )
    from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig

    robot_gateway = FireClawGateway(
        GatewayConfig(
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "robot-legacy.jsonl"),
            event_path=str(tmp_path / "robot-events.jsonl"),
            task_queue_path=str(tmp_path / "robot-tasks.jsonl"),
            embodied_memory_path=str(tmp_path / "robot-memory.jsonl"),
            embodied_runtime_mode="simulation",
        ),
        replication_security=ReplicationServerSecurity(
            identity=ReplicationSigningIdentity("robot-1", "robot-key"),
            key_provider=keys,
            peer_policies={"mission-gw-1": export_policy},
            nonce_cache=ReplicationNonceCache(max_entries=32),
        ),
    )
    assert robot_gateway.embodied_memory is not None
    _make_event(
        robot_gateway.embodied_memory,
        event_id=(
            "evt-standard"
            if include_restricted_source
            else "evt-1"
        ),
        sensitivity="standard",
    )
    if include_restricted_source:
        _make_event(
            robot_gateway.embodied_memory,
            event_id="evt-restricted",
            sensitivity="restricted",
        )
        _make_relation(
            robot_gateway.embodied_memory,
            relation_id="rel-hidden",
            source_record_id="evt-standard",
            target_record_id="evt-restricted",
        )

    from fireclaw_core.subagent.subagent_client import RobotSubagentClient

    client = RobotSubagentClient(
        replication_identity=ReplicationSigningIdentity(
            "mission-gw-1",
            "coordinator-key",
        ),
        replication_key_provider=keys,
    )
    entry = _robot_entry()
    registry = RobotRegistry([entry])
    destination = EmbodiedMemoryStore(tmp_path / "destination.jsonl")
    reconciler = EmbodiedMemoryReconciler(
        destination=destination,
        state_path=tmp_path / "reconciliation.jsonl",
        runtime_mode="simulation",
    )
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=JsonlMissionRegistry(tmp_path / "missions.jsonl"),
    )
    mission_gateway = MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=client,
        memory_reconciler=reconciler,
        replication_security=ReplicationClientSecurity(
            identity=ReplicationSigningIdentity(
                "mission-gw-1",
                "coordinator-key",
            ),
            key_provider=keys,
            robot_policies={"robot-1": import_policy},
            nonce_cache=ReplicationNonceCache(max_entries=32),
        ),
    )
    return robot_gateway, client, mission_gateway, destination


if __name__ == "__main__":
    import sys

    _tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test_fn in _tests:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            try:
                test_fn(Path(td))
                print(f"PASS {test_fn.__name__}")
            except Exception as exc:
                print(f"FAIL {test_fn.__name__}: {exc}")
                failures += 1
    if failures:
        print(f"\n{failures} FAILED")
        sys.exit(1)
    else:
        print(f"\nAll {len(_tests)} tests passed")
