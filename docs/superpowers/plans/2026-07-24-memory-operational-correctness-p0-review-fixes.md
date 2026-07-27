# Memory Operational Correctness P0 Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the P0 acceptance gaps found in the 2026-07-24 review so replication is authenticated and policy-bound on the real path, startup working memory is reconstructed correctly, consolidation work is recoverable and non-overlapping, and every terminal path queues deterministic evidence.

**Architecture:** Preserve the current FireClaw memory modules and strengthen their boundaries instead of replacing them. Authentication is verified before replication payload parsing, runtime assembly owns startup hydration, consolidation state owns atomic range reservation and recovery, and terminal events use stable IDs plus explicit mission/subtask scope semantics. Every behavior change follows RED-GREEN-REFACTOR and is verified through real production classes rather than fake-only behavior.

**Tech Stack:** Python 3.10 standard library, dataclasses, HMAC-SHA256, JSONL authority journals, `fcntl.flock`, `unittest.mock`, existing dependency-free test functions.

---

## Scope And File Map

This remains one P0 repair plan because the four subsystems form one acceptance
boundary: terminal transitions close consolidation ranges, runtime startup
recovers those ranges and working context, and replication supplies later
evidence under the same authority and safety rules. Each task is independently
testable and should be committed separately.

**Replication security and transport**

- Modify `src/fireclaw_core/memory/replication_security.py`: validated policies,
  random nonces, bounded replay cache, auth header codec, raw-payload signing
  and verification.
- Modify `src/fireclaw_core/memory/reconciliation.py`: v2 batch metadata,
  verify-before-parse entry point, destination policy admission, explicit
  legacy-only batch import.
- Modify `src/fireclaw_core/subagent/subagent_client.py`: sign exact replication
  requests and require exact mission/runtime.
- Modify `src/fireclaw_core/gateway/gateway.py`: verify peer request before
  export, bind server policy, return a signed v2 batch.
- Modify `src/fireclaw_core/mission/mission_gateway.py`: inject explicit
  replication configuration and pass raw responses to the reconciler.
- Modify `src/fireclaw_core/gateway/serve.py`: pass deployment-provided
  replication configuration without default secrets.

**Startup working memory**

- Modify `src/fireclaw_core/memory/working_memory.py`: newest-first,
  deterministic round-robin selection.
- Modify `src/fireclaw_core/mission/mission_runtime.py`: construct one mission
  registry, hydrate once, inject the same instances.
- Modify `src/fireclaw_core/mission/mission_agent.py`: expose the content-free
  hydration report.

**Consolidation correctness**

- Modify `src/fireclaw_core/memory/consolidation_state.py`: atomic range
  reservation, explicit scope kind, strict watermark monotonicity, recoverable
  failure state.
- Modify `src/fireclaw_core/memory/consolidation_coordinator.py`: real failure
  metadata, restart retry, worker wakeup, safe stop, accurate artifact outcome.
- Modify `src/fireclaw_core/mission/mission_agent.py`: deterministic terminal
  events and complete terminal trigger coverage.
- Modify `src/fireclaw_core/mission/mission_gateway.py`: stop request intake
  before stopping the coordinator.

**Tests and documentation**

- Modify `tests/test_memory_replication_security.py`
- Replace fake-only assertions in `tests/test_replication_gateway.py`
- Modify `tests/test_working_memory_hydration.py`
- Modify `tests/test_mission_runtime.py`
- Modify `tests/test_memory_consolidation_coordinator.py`
- Modify `tests/test_mission_agent.py`
- Modify `tests/test_mission_gateway.py`
- Modify `docs/architecture/embodied-memory-event-production.md`
- Update `memory/2026-07-24/memory-operational-correctness-p0.md`

## Fixed Design Decisions

1. A v2 replication response is a raw batch dictionary plus top-level `auth`.
   The batch dictionary contains `protocol_version`, `policy_id`, and bounded
   `omission_counts`; record envelopes remain schema v1.
2. `EmbodiedMemoryReconciler.ingest_signed_payload()` verifies the raw batch
   dictionary before calling `ReplicationBatch.from_dict()`. The existing
   `ingest_batch()` remains only for explicitly enabled unsigned simulation
   compatibility and always rejects real-runtime use.
3. Robot Gateway policies are keyed by authenticated coordinator peer ID.
   Mission Gateway admission policies are keyed by expected robot ID. Both
   policies name the same deployment policy ID and independently constrain
   runtime, robot, and sensitivity.
4. Replication auth travels in
   `X-FireClaw-Replication-Auth` as base64url-encoded canonical JSON. Operator
   scopes do not establish peer identity.
5. A consolidation boundary has explicit `scope_kind`:
   `subtask` accepts only exact `(robot_id, subtask_id)` evidence; `mission`
   accepts only evidence whose `subtask_id is None`. Neither is a wildcard.
6. The state Store atomically reserves the next range using the maximum of the
   completed watermark and all already reserved boundaries for that scope.
7. A failed boundary remains failed during the current worker run and is
   requeued by `recover()` on the next startup. It is never silently discarded.
8. Terminal embodied events use the deterministic terminal transition ID as
   the actual authority event ID. The boundary closes at that event's authority
   sequence, not at a later `len(events)` snapshot.
9. Hydration selects the newest fresh event from each active mission in
   deterministic rounds. Selected events are inserted oldest-to-newest so
   snapshots preserve chronological order.

## Dependency-Free Focused Test Command

Use this command shape for every named test. It executes the function and
supplies a fresh `tmp_path`; it does not rely on a test file's `__main__`.

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 - \
  tests/test_working_memory_hydration.py \
  test_runtime_builder_hydrates_active_mission_memory <<'PY'
from pathlib import Path
import runpy
import sys
import tempfile

module_path, test_name = sys.argv[1:3]
namespace = runpy.run_path(module_path)
test = namespace[test_name]
with tempfile.TemporaryDirectory() as directory:
    test(Path(directory))
print(f"PASS {module_path}::{test_name}")
PY
```

For functions without `tmp_path`, call `test()` in the same runner. When a
task contains several tests, load the module once and invoke each named
function with a new temporary directory.

---

### Task 1: Harden Replication Primitives And Make Reconciliation Fail Closed

**Files:**

- Modify: `src/fireclaw_core/memory/replication_security.py`
- Modify: `src/fireclaw_core/memory/reconciliation.py`
- Modify: `tests/test_memory_replication_security.py`

- [ ] **Step 1: Add RED tests for the reproduced security failures**

Append tests that prove partial auth configuration cannot bypass verification,
invalid messages cannot consume a nonce, naive timestamps fail as validation,
nonces are random, policies reject unknown values, and raw payload
verification precedes envelope parsing:

```python
def test_non_null_auth_without_verification_dependencies_fails_closed(
    tmp_path: Path,
) -> None:
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
        protocol_version=2,
        policy_id="policy-1",
        omission_counts={},
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
        assert "legacy" in str(exc).lower() or "signed payload" in str(exc).lower()
    assert destination.list_events() == []


def test_invalid_signature_does_not_consume_nonce(tmp_path: Path) -> None:
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
    valid = sign_batch(
        batch=batch,
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
    first = verify_signed_batch(
        batch=batch,
        auth=invalid,
        scope=scope,
        key_provider=keys,
        peer_id="mission-gateway-1",
        nonce_cache=cache,
    )
    second = verify_signed_batch(
        batch=batch,
        auth=valid,
        scope=scope,
        key_provider=keys,
        peer_id="mission-gateway-1",
        nonce_cache=cache,
    )
    assert first.error_code == "signature_invalid"
    assert second.verified is True


def test_naive_timestamp_returns_invalid_timestamp(tmp_path: Path) -> None:
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
    auth = sign_batch(
        batch=batch,
        scope=scope,
        key_provider=keys,
        peer_id="mission-gateway-1",
        key_id="key-1",
        issued_at="2026-07-24T10:00:00",
        nonce="naive-time",
    )
    result = verify_signed_batch(
        batch=batch,
        auth=auth,
        scope=scope,
        key_provider=keys,
        peer_id="mission-gateway-1",
    )
    assert result == VerificationResult(False, "invalid_timestamp")


def test_default_nonces_are_random() -> None:
    keys = _default_key_provider()
    scope = ReplicationRequestScope("mission-1", "simulation", 0, 10)
    issued_at = "2026-07-24T10:00:00+00:00"
    first = sign_request(
        body={},
        scope=scope,
        key_provider=keys,
        peer_id="mission-gateway-1",
        key_id="key-1",
        issued_at=issued_at,
    )
    second = sign_request(
        body={},
        scope=scope,
        key_provider=keys,
        peer_id="mission-gateway-1",
        key_id="key-1",
        issued_at=issued_at,
    )
    assert first.nonce != second.nonce
    assert len(first.nonce) >= 32


def test_peer_policy_rejects_unknown_runtime_and_sensitivity() -> None:
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
```

- [ ] **Step 2: Run the six new tests and verify RED**

Expected failures:

- partial auth currently imports;
- the invalid signature consumes the nonce;
- the naive timestamp raises `TypeError`;
- same-time default nonces collide;
- invalid policy values are accepted;
- `ReplicationBatch` lacks the v2 metadata fields.

- [ ] **Step 3: Implement validated v2 primitives**

Add these contracts and use `MEMORY_RUNTIME_MODES` and
`MEMORY_SENSITIVITY_LEVELS` for policy validation:

```python
REPLICATION_AUTH_HEADER = "X-FireClaw-Replication-Auth"


class ReplicationNonceCache:
    def __init__(self, *, max_entries: int = 4096) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.RLock()

    def consume(self, key: str) -> bool:
        with self._lock:
            if key in self._entries:
                return False
            self._entries[key] = None
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            return True


def encode_auth_header(auth: ReplicationAuthMetadata) -> str:
    raw = json.dumps(
        auth.to_dict(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_auth_header(value: str) -> ReplicationAuthMetadata:
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
```

Use `secrets.token_urlsafe(24)` for request and batch defaults. Refactor request
and batch verification through one private function that:

1. validates peer and timezone-aware timestamp;
2. validates body digest;
3. resolves the verification key;
4. compares the HMAC;
5. consumes the nonce only after the signature succeeds.

Add v2 batch fields without changing envelope schema:

```python
@dataclass(frozen=True)
class ReplicationBatch:
    source_store_id: str
    source_robot_id: str
    cursor: int
    next_cursor: int
    has_more: bool
    envelopes: tuple[ReplicationEnvelope, ...]
    protocol_version: int = REPLICATION_SCHEMA_VERSION
    policy_id: str | None = None
    omission_counts: dict[str, int] = field(default_factory=dict)
```

For v2, validate a non-empty `policy_id`, stable omission reason names,
non-negative counts, and a maximum number of omission keys. For v1, require
`policy_id is None` and empty omission counts; accept it only through the
explicit legacy simulation path. The policy-aware exporter must construct v2
batches explicitly.

Add the shared signing identity and general raw-payload primitives alongside
the existing batch wrappers:

```python
@dataclass(frozen=True)
class ReplicationSigningIdentity:
    peer_id: str
    key_id: str

    def __post_init__(self) -> None:
        if not self.peer_id.strip() or not self.key_id.strip():
            raise ValueError("replication signing identity must be complete")


def sign_payload(
    *,
    payload: dict[str, Any],
    scope: ReplicationRequestScope,
    key_provider: ReplicationKeyProvider,
    identity: ReplicationSigningIdentity,
    issued_at: str | None = None,
    nonce: str | None = None,
) -> ReplicationAuthMetadata:
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
    return verify_signed_request(
        body=payload,
        auth=auth,
        scope=scope,
        key_provider=key_provider,
        peer_id=peer_id,
        max_age_seconds=max_age_seconds,
        nonce_cache=nonce_cache,
    )
```

Reuse `ReplicationSigningIdentity` in Task 2. Keep `sign_batch()` and
`verify_signed_batch()` as compatibility wrappers over the raw-payload
functions.

- [ ] **Step 4: Add verify-before-parse reconciliation**

Add the production entry point:

```python
def ingest_signed_payload(
    self,
    payload: dict[str, Any],
    *,
    expected_robot_id: str,
    expected_mission_id: str,
    expected_scope: ReplicationRequestScope,
    key_provider: ReplicationKeyProvider,
    peer_policy: ReplicationPeerPolicy,
    nonce_cache: ReplicationNonceCache,
) -> ReconciliationReport:
    raw_auth = payload.get("auth")
    if not isinstance(raw_auth, dict):
        raise ValueError("signed replication payload requires auth")
    raw_batch = {key: value for key, value in payload.items() if key != "auth"}
    auth = ReplicationAuthMetadata.from_dict(raw_auth)
    verified = verify_signed_payload(
        payload=raw_batch,
        auth=auth,
        scope=expected_scope,
        key_provider=key_provider,
        peer_id=expected_robot_id,
        nonce_cache=nonce_cache,
    )
    if not verified.verified:
        raise ValueError(
            f"replication batch verification failed: {verified.error_code}"
        )
    batch = ReplicationBatch.from_dict(raw_batch)
    return self._ingest_verified_batch(
        batch,
        expected_robot_id=expected_robot_id,
        expected_mission_id=expected_mission_id,
        peer_policy=peer_policy,
    )
```

`_ingest_verified_batch()` must recheck every event's mission, runtime, robot,
and sensitivity before append. It must reject a policy ID mismatch before
reading envelopes. Change public `ingest_batch()` so it succeeds only when all
of these are true: simulation runtime, `allow_legacy_unsigned=True`, and
`auth is None`. Every other call raises
`ValueError("use ingest_signed_payload for authenticated replication")`.

- [ ] **Step 5: Run Task 1 tests and existing replication tests GREEN**

Expected: every Task 1 test passes; existing policy filtering, relation closure,
tamper rejection, real unsigned rejection, and explicit simulation legacy
tests remain green.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/fireclaw_core/memory/replication_security.py \
  src/fireclaw_core/memory/reconciliation.py \
  tests/test_memory_replication_security.py
git commit -m "fix(memory): make replication verification fail closed"
```

Do not run the commit command unless the user explicitly asks for commits.

---

### Task 2: Wire Authentication Through The Real Client And Robot Gateway

**Files:**

- Modify: `src/fireclaw_core/subagent/subagent_client.py`
- Modify: `src/fireclaw_core/gateway/gateway.py`
- Modify: `src/fireclaw_core/mission/mission_gateway.py`
- Modify: `src/fireclaw_core/gateway/serve.py`
- Modify: `tests/test_replication_gateway.py`
- Modify: `tests/test_gateway.py`
- Modify: `tests/test_mission_gateway.py`

- [ ] **Step 1: Replace fake-only integration assertions with RED tests**

Delete `_FakeSubagentClient` signing and filtering behavior from
`tests/test_replication_gateway.py`. Add tests against the real client and a
real robot Gateway service method:

```python
def test_real_client_builds_signed_exact_scope_request(tmp_path: Path) -> None:
    keys = _key_provider_for(
        ("mission-gateway-1", "coordinator-key", b"coordinator-secret")
    )
    client = RobotSubagentClient(
        replication_identity=ReplicationSigningIdentity(
            peer_id="mission-gateway-1",
            key_id="coordinator-key",
        ),
        replication_key_provider=keys,
    )
    entry = RobotRegistryEntry(
        robot_id="robot-1",
        base_url="https://robot-1.local",
    )
    request_value = client._build_memory_replication_request(
        entry,
        cursor=7,
        limit=20,
        mission_id="mission-1",
        runtime_mode="real",
    )
    auth = decode_auth_header(
        request_value.headers[REPLICATION_AUTH_HEADER]
    )
    scope = ReplicationRequestScope("mission-1", "real", 7, 20)
    result = verify_signed_request(
        body={},
        auth=auth,
        scope=scope,
        key_provider=keys,
        peer_id="mission-gateway-1",
    )
    assert result.verified is True
    assert "mission_id=mission-1" in request_value.full_url
    assert "runtime_mode=real" in request_value.full_url


def test_robot_gateway_rejects_request_before_export(tmp_path: Path) -> None:
    keys = _key_provider_for(
        (
            "mission-gateway-1",
            "coordinator-key",
            b"coordinator-secret-000000000000",
        ),
        ("robot-1", "robot-key", b"robot-secret-00000000000000000"),
    )
    policy = ReplicationPeerPolicy(
        policy_id="policy-1",
        peer_id="mission-gateway-1",
        allowed_robot_ids=frozenset({"robot-1"}),
        allowed_runtime_modes=frozenset({"simulation"}),
        allowed_sensitivities=frozenset({"standard"}),
    )
    exporter = CountingExporter()
    gateway = FireClawGateway(
        GatewayConfig(
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "legacy.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
        ),
        replication_security=ReplicationServerSecurity(
            identity=ReplicationSigningIdentity("robot-1", "robot-key"),
            key_provider=keys,
            peer_policies={"mission-gateway-1": policy},
            nonce_cache=ReplicationNonceCache(max_entries=32),
        ),
    )
    gateway.memory_replication_exporter = exporter
    scope = ReplicationRequestScope("mission-1", "simulation", 0, 20)
    forged = ReplicationAuthMetadata(
        protocol_version=2,
        peer_id="mission-gateway-1",
        key_id="coordinator-key",
        issued_at=datetime.now(timezone.utc).isoformat(),
        nonce="forged",
        body_digest="0" * 64,
        signature="0" * 64,
    )
    try:
        gateway.export_memory_replication(scope=scope, request_auth=forged)
        raise AssertionError("expected forged request to fail")
    except ValueError as exc:
        assert "verification failed" in str(exc)
    assert exporter.calls == 0


def test_real_client_robot_gateway_and_reconciler_round_trip(
    tmp_path: Path,
) -> None:
    robot_gateway, client, mission_gateway, destination = (
        _build_real_replication_stack(tmp_path)
    )
    with patch(
        "fireclaw_core.subagent.subagent_client.request.urlopen",
        side_effect=_gateway_urlopen(robot_gateway),
    ):
        result = mission_gateway.sync_robot_memory(
            "mission-1",
            robot_id="robot-1",
        )
    assert result["status"] == "synced"
    assert [event.event_id for event in destination.list_events()] == ["evt-1"]
    assert result["robots"][0]["authenticated_peer_id"] == "robot-1"
    assert result["robots"][0]["policy_id"] == "policy-1"


def test_real_path_omits_restricted_event_and_hidden_relation(
    tmp_path: Path,
) -> None:
    robot_gateway, client, mission_gateway, destination = (
        _build_real_replication_stack(
            tmp_path,
            include_restricted_source=True,
        )
    )
    with patch(
        "fireclaw_core.subagent.subagent_client.request.urlopen",
        side_effect=_gateway_urlopen(robot_gateway),
    ):
        result = mission_gateway.sync_robot_memory(
            "mission-1",
            robot_id="robot-1",
        )
    imported_ids = {event.event_id for event in destination.list_events()}
    assert imported_ids == {"evt-standard"}
    assert result["robots"][0]["omission_counts"] == {
        "relation_endpoint_hidden": 1,
        "sensitivity_denied": 1,
    }
```

Add these concrete helpers in the same test file. The URL opener only adapts
stdlib transport objects; production classes still perform every signature,
policy, export, parse, and reconciliation operation:

```python
class _JsonResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_JsonResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class CountingExporter:
    def __init__(self) -> None:
        self.calls = 0

    def export_batch(self, **kwargs: Any) -> ReplicationBatch:
        self.calls += 1
        raise AssertionError("export must not run before request verification")


def _key_provider_for(
    *items: tuple[str, str, bytes],
) -> InMemoryReplicationKeyProvider:
    provider = InMemoryReplicationKeyProvider()
    for peer_id, key_id, secret in items:
        provider.register_key(
            peer_id=peer_id,
            key_id=key_id,
            secret=secret,
        )
    return provider


def _gateway_urlopen(gateway: FireClawGateway):
    def open_request(request_value, timeout: float) -> _JsonResponse:
        parsed = urlparse(request_value.full_url)
        query = parse_qs(parsed.query)
        scope = ReplicationRequestScope(
            mission_id=query["mission_id"][0],
            runtime_mode=query["runtime_mode"][0],
            cursor=int(query["cursor"][0]),
            limit=int(query["limit"][0]),
        )
        auth = decode_auth_header(
            request_value.get_header(REPLICATION_AUTH_HEADER)
        )
        return _JsonResponse(
            gateway.export_memory_replication(
                scope=scope,
                request_auth=auth,
            )
        )

    return open_request


def _build_real_replication_stack(
    tmp_path: Path,
    *,
    include_restricted_source: bool = False,
) -> tuple[
    FireClawGateway,
    RobotSubagentClient,
    MissionGateway,
    EmbodiedMemoryStore,
]:
    keys = _key_provider_for(
        (
            "mission-gateway-1",
            "coordinator-key",
            b"coordinator-secret-000000000000",
        ),
        ("robot-1", "robot-key", b"robot-secret-00000000000000000"),
    )
    export_policy = ReplicationPeerPolicy(
        policy_id="policy-1",
        peer_id="mission-gateway-1",
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
    robot_gateway = FireClawGateway(
        GatewayConfig(
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "robot-legacy.jsonl"),
            event_path=str(tmp_path / "robot-events.jsonl"),
            task_queue_path=str(tmp_path / "robot-tasks.jsonl"),
            workspace_skills_dir=None,
            embodied_memory_path=str(tmp_path / "robot-memory.jsonl"),
            embodied_runtime_mode="simulation",
        ),
        replication_security=ReplicationServerSecurity(
            identity=ReplicationSigningIdentity("robot-1", "robot-key"),
            key_provider=keys,
            peer_policies={"mission-gateway-1": export_policy},
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

    client = RobotSubagentClient(
        replication_identity=ReplicationSigningIdentity(
            "mission-gateway-1",
            "coordinator-key",
        ),
        replication_key_provider=keys,
    )
    entry = RobotRegistryEntry(
        robot_id="robot-1",
        base_url="https://robot-1.local",
    )
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
                "mission-gateway-1",
                "coordinator-key",
            ),
            key_provider=keys,
            robot_policies={"robot-1": import_policy},
            nonce_cache=ReplicationNonceCache(max_entries=32),
        ),
    )
    return robot_gateway, client, mission_gateway, destination
```

- [ ] **Step 2: Run the four integration tests and verify RED**

Expected: the real client lacks replication identity parameters and request
construction; Gateway lacks the verified export service; Mission Gateway uses
ad-hoc attributes and parses the batch before verification.

- [ ] **Step 3: Add explicit transport security configuration**

Reuse `ReplicationSigningIdentity` from Task 1 and add:

```python
@dataclass(frozen=True)
class ReplicationServerSecurity:
    identity: ReplicationSigningIdentity
    key_provider: ReplicationKeyProvider
    peer_policies: Mapping[str, ReplicationPeerPolicy]
    nonce_cache: ReplicationNonceCache


@dataclass(frozen=True)
class ReplicationClientSecurity:
    identity: ReplicationSigningIdentity
    key_provider: ReplicationKeyProvider
    robot_policies: Mapping[str, ReplicationPeerPolicy]
    nonce_cache: ReplicationNonceCache
    allow_insecure_simulation_http: bool = False
```

Inject `ReplicationServerSecurity | None` into robot `Gateway`. Inject
`ReplicationClientSecurity | None` into `MissionGateway`, and construct its
`RobotSubagentClient` with the same signing identity and key provider. Remove
all `getattr(self, "replication_*", None)` access.

Do not create default identities, policies, keys, or insecure switches.
`gateway/serve.py` may pass a fully constructed object from deployment
configuration; absent configuration means replication returns a content-free
`not_configured` error.

- [ ] **Step 4: Sign the real request and response**

Make `get_memory_replication()` require non-optional mission/runtime:

```python
def get_memory_replication(
    self,
    entry: RobotRegistryEntry,
    *,
    cursor: int,
    limit: int,
    mission_id: str,
    runtime_mode: str,
) -> dict[str, Any]:
    request_value = self._build_memory_replication_request(
        entry,
        cursor=cursor,
        limit=limit,
        mission_id=mission_id,
        runtime_mode=runtime_mode,
    )
    return self._open_json(request_value)
```

`_build_memory_replication_request()` must:

- reject missing replication configuration;
- reject HTTP when runtime is real;
- reject simulation HTTP unless the explicit switch is true;
- sign `{}` against exactly the query scope;
- attach `REPLICATION_AUTH_HEADER`;
- retain bearer authentication only as a separate transport/control-plane
  check.

Add a pure Gateway service used by the HTTP handler:

```python
def export_memory_replication(
    self,
    *,
    scope: ReplicationRequestScope,
    request_auth: ReplicationAuthMetadata,
) -> dict[str, Any]:
    security = self.replication_security
    if security is None or self.memory_replication_exporter is None:
        raise ValueError("authenticated replication is not configured")
    policy = security.peer_policies.get(request_auth.peer_id)
    if policy is None:
        raise ValueError("replication peer policy not found")
    verified = verify_signed_request(
        body={},
        auth=request_auth,
        scope=scope,
        key_provider=security.key_provider,
        peer_id=request_auth.peer_id,
        nonce_cache=security.nonce_cache,
    )
    if not verified.verified:
        raise ValueError(
            f"replication request verification failed: {verified.error_code}"
        )
    batch = self.memory_replication_exporter.export_batch(
        cursor=scope.cursor,
        limit=scope.limit,
        mission_id=scope.mission_id,
        runtime_mode=scope.runtime_mode,
        peer_policy=policy,
    )
    body = batch.to_dict()
    auth = sign_payload(
        payload=body,
        scope=scope,
        key_provider=security.key_provider,
        identity=security.identity,
    )
    return {**body, "auth": auth.to_dict()}
```

The `/memory/replication` handler must require exact mission/runtime, decode
the auth header, call this service, and map validation failures to
`401/403/400` without returning secrets, signatures, or evidence payloads in
the error.

- [ ] **Step 5: Verify raw response in Mission Gateway before parsing**

In `sync_robot_memory()`:

1. resolve the configured policy by `entry.robot_id`;
2. construct the exact request scope using the requested limit, not envelope
   count;
3. call the real client;
4. pass the untouched payload to `ingest_signed_payload()`;
5. update cursor only from the verified reconciliation report;
6. expose only peer ID, policy ID, counts, stable error code, and exception
   class.

Real runtime must fail before network I/O for HTTP, missing security
configuration, missing robot policy, or missing key. Simulation legacy behavior
must require both `allow_insecure_simulation_http=True` and reconciler
`allow_legacy_unsigned=True`.

- [ ] **Step 6: Run Task 2 and compatibility tests GREEN**

Run the four new real-path tests, then the existing Gateway authorization,
Mission Gateway sync, real HTTP rejection, cursor progress, and simulation
legacy tests. Expected: all pass and `_FakeSubagentClient` no longer owns
security behavior.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/fireclaw_core/subagent/subagent_client.py \
  src/fireclaw_core/gateway/gateway.py \
  src/fireclaw_core/mission/mission_gateway.py \
  src/fireclaw_core/gateway/serve.py \
  tests/test_replication_gateway.py \
  tests/test_gateway.py \
  tests/test_mission_gateway.py
git commit -m "fix(gateway): wire authenticated memory replication"
```

Do not run the commit command unless explicitly requested.

---

### Task 3: Hydrate Newest Active-Mission Working Memory At Runtime Startup

**Files:**

- Modify: `src/fireclaw_core/memory/working_memory.py`
- Modify: `src/fireclaw_core/mission/mission_runtime.py`
- Modify: `src/fireclaw_core/mission/mission_agent.py`
- Modify: `tests/test_working_memory_hydration.py`
- Modify: `tests/test_mission_runtime.py`

- [ ] **Step 1: Add RED tests for newest-first fairness and actual runtime wiring**

```python
def test_hydration_capacity_keeps_newest_events(tmp_path: Path) -> None:
    registry = _active_registry(tmp_path, mission_ids=("mission-1",))
    store = EmbodiedMemoryStore(tmp_path / "memory.jsonl")
    for index in range(3):
        _append_event(
            store,
            event_id=f"evt-{index}",
            mission_id="mission-1",
            observed_at=f"2026-07-24T10:00:0{index}+00:00",
        )
    working = EmbodiedWorkingMemory(
        WorkingMemoryConfig(capacity=2, task_context_max_age_seconds=600)
    )
    report = working.hydrate_recent(
        store=store,
        registry=registry,
        runtime_mode="real",
        reference_at="2026-07-24T10:00:10+00:00",
    )
    snapshot = working.snapshot(
        runtime_mode="real",
        mission_id="mission-1",
        reference_at="2026-07-24T10:00:10+00:00",
        include_stale=True,
    )
    assert report.selected == 2
    assert [event.event_id for event in snapshot.events] == ["evt-1", "evt-2"]


def test_hydration_round_robin_does_not_let_one_mission_fill_capacity(
    tmp_path: Path,
) -> None:
    registry = _active_registry(
        tmp_path,
        mission_ids=("mission-a", "mission-b"),
    )
    store = EmbodiedMemoryStore(tmp_path / "memory.jsonl")
    for index in range(4):
        _append_event(
            store,
            event_id=f"a-{index}",
            mission_id="mission-a",
            observed_at=f"2026-07-24T10:00:0{index}+00:00",
        )
    _append_event(
        store,
        event_id="b-0",
        mission_id="mission-b",
        observed_at="2026-07-24T10:00:04+00:00",
    )
    working = EmbodiedWorkingMemory(WorkingMemoryConfig(capacity=3))
    working.hydrate_recent(
        store=store,
        registry=registry,
        runtime_mode="real",
        reference_at="2026-07-24T10:00:05+00:00",
    )
    assert {
        event.mission_id
        for mission_id in ("mission-a", "mission-b")
        for event in working.snapshot(
            runtime_mode="real",
            mission_id=mission_id,
            reference_at="2026-07-24T10:00:05+00:00",
        ).events
    } == {"mission-a", "mission-b"}


def test_runtime_builder_hydrates_active_mission_memory(tmp_path: Path) -> None:
    registry_path = tmp_path / "robots.json"
    _write_robot_registry(
        registry_path,
        [{"robot_id": "r1", "base_url": "http://r1:8765"}],
    )
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission_registry.create_mission(
        mission_id="mission-1",
        session_id=None,
        command="search",
        created_at="2026-07-24T10:00:00+00:00",
    )
    store = EmbodiedMemoryStore(tmp_path / "memory.jsonl")
    store.record_event(
        mission_id="mission-1",
        event_type="command",
        payload={"command": "search"},
        runtime_mode="real",
        source_type="operator",
        observed_at=datetime.now(timezone.utc).isoformat(),
        event_id="startup-event",
    )
    agent = build_mission_agent_from_paths(
        MissionRuntimePaths(
            robot_registry=registry_path,
            mission_registry=tmp_path / "missions.jsonl",
            mission_memory=tmp_path / "memory.jsonl",
            embodied_runtime_mode="real",
        ),
        operator_id="operator-1",
        role="operator",
    )
    assert agent.embodied_working_memory is not None
    assert agent.embodied_working_memory.count == 1
    assert agent.working_memory_hydration_report.added == 1
```

Add these helpers to `tests/test_working_memory_hydration.py` so the first two
tests are self-contained:

```python
def _active_registry(
    tmp_path: Path,
    *,
    mission_ids: tuple[str, ...],
) -> JsonlMissionRegistry:
    registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    for index, mission_id in enumerate(mission_ids):
        registry.create_mission(
            mission_id=mission_id,
            session_id=None,
            command="search",
            created_at=f"2026-07-24T09:00:0{index}+00:00",
        )
    return registry


def _append_event(
    store: EmbodiedMemoryStore,
    *,
    event_id: str,
    mission_id: str,
    observed_at: str,
) -> None:
    store.record_event(
        mission_id=mission_id,
        event_type="command",
        payload={"command": "search"},
        runtime_mode="real",
        source_type="operator",
        observed_at=observed_at,
        event_id=event_id,
    )
```

- [ ] **Step 2: Run the three tests and verify RED**

Expected: the first test selects `evt-0,evt-1`; the runtime builder reports
zero hydrated events.

- [ ] **Step 3: Implement deterministic newest-first round-robin selection**

Replace quota slicing with:

```python
by_mission: dict[str, deque[EmbodiedMemoryEvent]] = {}
for event in fresh:
    by_mission.setdefault(event.mission_id, deque()).append(event)
for events in by_mission.values():
    ordered = sorted(
        events,
        key=lambda event: (event.observed_at, event.event_id),
        reverse=True,
    )
    events.clear()
    events.extend(ordered)

selected_newest_first: list[EmbodiedMemoryEvent] = []
mission_order = sorted(
    by_mission,
    key=lambda mission_id: (
        by_mission[mission_id][0].observed_at,
        by_mission[mission_id][0].event_id,
        mission_id,
    ),
    reverse=True,
)
while len(selected_newest_first) < self.config.capacity:
    admitted_this_round = 0
    for mission_id in mission_order:
        queue = by_mission[mission_id]
        if not queue:
            continue
        selected_newest_first.append(queue.popleft())
        admitted_this_round += 1
        if len(selected_newest_first) == self.config.capacity:
            break
    if admitted_this_round == 0:
        break
selected = sorted(
    selected_newest_first,
    key=lambda event: (event.observed_at, event.event_id),
)
```

Keep freshness, runtime isolation, active mission filtering, size admission,
and no-authority-write behavior unchanged.

- [ ] **Step 4: Wire hydration into normal runtime assembly**

Construct `mission_registry = JsonlMissionRegistry(paths.mission_registry)`
before embodied Store construction. After the Store, working memory, and
registry exist:

```python
hydration_report = WorkingMemoryHydrationReport()
if embodied_store is not None and embodied_working_memory is not None:
    try:
        hydration_report = embodied_working_memory.hydrate_recent(
            store=embodied_store,
            registry=mission_registry,
            runtime_mode=paths.embodied_runtime_mode,
            reference_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        logger.warning(
            "working memory startup hydration failed",
            extra={"exception_class": type(exc).__name__},
        )
```

Inject that same `mission_registry`, `embodied_working_memory`, and
`hydration_report` into `MissionAgent`. Add a read-only
`working_memory_hydration_report` attribute. Do not create a second registry
at the return statement.

- [ ] **Step 5: Run Task 3 tests and existing hydration/runtime tests GREEN**

Expected: newest events survive capacity pressure, both active missions receive
capacity when possible, inactive/runtime/stale isolation remains green, no
authority records are appended, and builder failure still starts empty.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/fireclaw_core/memory/working_memory.py \
  src/fireclaw_core/mission/mission_runtime.py \
  src/fireclaw_core/mission/mission_agent.py \
  tests/test_working_memory_hydration.py \
  tests/test_mission_runtime.py
git commit -m "fix(memory): hydrate newest working context at startup"
```

Do not run the commit command unless explicitly requested.

---

### Task 4: Make Consolidation Range Reservation And Failure Recovery Durable

**Files:**

- Modify: `src/fireclaw_core/memory/consolidation_state.py`
- Modify: `src/fireclaw_core/memory/consolidation_coordinator.py`
- Modify: `tests/test_memory_consolidation_coordinator.py`

- [ ] **Step 1: Replace the false retry test with genuine RED failure tests**

```python
class _FailOnceEngine:
    def __init__(self, delegate: FireClawConsolidationEngine) -> None:
        self.delegate = delegate
        self.config = delegate.config
        self.calls: list[tuple[str, ...]] = []

    def consolidate_events(
        self,
        *,
        mission_id: str,
        runtime_mode: str,
        source_event_ids: tuple[str, ...],
    ) -> ConsolidationResult:
        self.calls.append(source_event_ids)
        if len(self.calls) == 1:
            raise RuntimeError("injected consolidation failure")
        return self.delegate.consolidate_events(
            mission_id=mission_id,
            runtime_mode=runtime_mode,
            source_event_ids=source_event_ids,
        )


def test_failed_boundary_is_requeued_on_restart_with_same_source_set(
    tmp_path: Path,
) -> None:
    coordinator, state_store, engine = _coordinator_with_engine(
        tmp_path,
        engine_wrapper=_FailOnceEngine,
    )
    _append_boundary_source_events(coordinator, count=3)
    _request_subtask_terminal(coordinator, through_sequence=3)
    first = coordinator.run_pending_once()
    boundary = state_store.boundaries()[0]
    assert first.errors == 1
    assert state_store.latest_status(boundary.boundary_id).status == "failed"
    assert state_store.pending_boundaries() == []

    coordinator.recover()
    assert state_store.latest_status(boundary.boundary_id).status == "queued"
    second = coordinator.run_pending_once()
    assert second.processed == 1
    assert engine.calls[0] == engine.calls[1]
    assert state_store.watermark("m1", "real", "subtask", "robot-a", "sub-1").through_sequence == 3


def test_failed_boundary_records_actual_exception_class(tmp_path: Path) -> None:
    coordinator, state_store, _ = _coordinator_with_engine(
        tmp_path,
        engine_wrapper=_FailOnceEngine,
    )
    _append_boundary_source_events(coordinator, count=3)
    _request_subtask_terminal(coordinator, through_sequence=3)
    coordinator.run_pending_once()
    boundary = state_store.boundaries()[0]
    state = state_store.latest_status(boundary.boundary_id)
    assert state.error_code == "consolidation_failed"
    assert state.error_class == "RuntimeError"


def test_two_queued_boundaries_reserve_adjacent_ranges(tmp_path: Path) -> None:
    coordinator, state_store, _ = _coordinator_with_engine(tmp_path)
    _append_boundary_source_events(coordinator, count=6)
    _request_subtask_terminal(
        coordinator,
        terminal_event_id="terminal-1",
        through_sequence=3,
    )
    _request_subtask_terminal(
        coordinator,
        terminal_event_id="terminal-2",
        through_sequence=6,
    )
    boundaries = state_store.boundaries()
    assert [
        (boundary.after_sequence, boundary.through_sequence)
        for boundary in boundaries
    ] == [(0, 3), (3, 6)]


def test_watermark_regression_is_rejected(tmp_path: Path) -> None:
    store = ConsolidationStateStore(tmp_path / "state.jsonl")
    first = _boundary("terminal-1", after_sequence=0, through_sequence=5)
    second = _boundary("terminal-2", after_sequence=5, through_sequence=7)
    store.ensure_boundary(first)
    store.ensure_boundary(second)
    store.transition(first.boundary_id, status="completed", through_sequence=5)
    store.transition(second.boundary_id, status="completed", through_sequence=7)
    try:
        store.transition(first.boundary_id, status="completed", through_sequence=4)
        raise AssertionError("expected watermark regression to fail")
    except ValueError as exc:
        assert "regression" in str(exc)
```

Add the complete fixture helpers immediately above those tests:

```python
def _coordinator_with_engine(
    tmp_path: Path,
    *,
    engine_wrapper=None,
):
    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(
        store,
        producer_type="memory_consolidator",
        producer_id="test-consolidator",
    )
    base_engine = FireClawConsolidationEngine(
        store=store,
        producer=producer,
    )
    engine = (
        engine_wrapper(base_engine)
        if engine_wrapper is not None
        else base_engine
    )
    state_store = ConsolidationStateStore(tmp_path / "state.jsonl")
    coordinator = MemoryConsolidationCoordinator(
        engine=engine,
        state_store=state_store,
        store=store,
        lock_path=tmp_path / "consolidation.lock",
    )
    return coordinator, state_store, engine


def _append_boundary_source_events(
    coordinator: MemoryConsolidationCoordinator,
    *,
    count: int,
) -> None:
    for event in _make_test_events(count=count):
        coordinator._store.append_event(event)


def _request_subtask_terminal(
    coordinator: MemoryConsolidationCoordinator,
    *,
    terminal_event_id: str = "terminal-1",
    through_sequence: int,
) -> None:
    coordinator.request_terminal_boundary(
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        terminal_event_id=terminal_event_id,
        terminal_status="succeeded",
        through_sequence=through_sequence,
    )


def _boundary(
    terminal_event_id: str,
    *,
    after_sequence: int,
    through_sequence: int,
) -> ConsolidationBoundary:
    return ConsolidationBoundary(
        boundary_id="auto",
        mission_id="m1",
        runtime_mode="real",
        scope_kind="subtask",
        robot_id="robot-a",
        subtask_id="sub-1",
        after_sequence=after_sequence,
        through_sequence=through_sequence,
        terminal_event_id=terminal_event_id,
        trigger_reason="subtask_terminal",
    )
```

- [ ] **Step 2: Run the four tests and verify RED**

Expected: the failed boundary is absent from recovery, diagnostics say
`Exception/unknown`, queued ranges overlap, and regression is silently capped.

- [ ] **Step 3: Add atomic state-store reservation**

Add public replay methods and one atomic reservation operation:

```python
def reserve_boundary(
    self,
    *,
    mission_id: str,
    runtime_mode: str,
    scope_kind: str,
    robot_id: str | None,
    subtask_id: str | None,
    terminal_event_id: str,
    trigger_reason: str,
    through_sequence: int,
) -> ConsolidationBoundary | None:
    with self._journal_lease():
        boundaries = self._boundaries_by_id_unlocked()
        if any(
            boundary.terminal_event_id == terminal_event_id
            and boundary.mission_id == mission_id
            and boundary.runtime_mode == runtime_mode
            for boundary in boundaries.values()
        ):
            return None
        scope = (mission_id, runtime_mode, scope_kind, robot_id, subtask_id)
        watermark = self._watermarks_unlocked().get(scope)
        reserved_tail = max(
            (
                boundary.through_sequence
                for boundary in boundaries.values()
                if boundary.scope_key() == scope
            ),
            default=0,
        )
        after_sequence = max(
            watermark.through_sequence if watermark is not None else 0,
            reserved_tail,
        )
        if through_sequence <= after_sequence:
            return None
        boundary = ConsolidationBoundary(
            boundary_id="auto",
            mission_id=mission_id,
            runtime_mode=runtime_mode,
            scope_kind=scope_kind,
            robot_id=robot_id,
            subtask_id=subtask_id,
            after_sequence=after_sequence,
            through_sequence=through_sequence,
            terminal_event_id=terminal_event_id,
            trigger_reason=trigger_reason,
        )
        self._append_unlocked(boundary.to_dict())
        return boundary
```

Use a dedicated journal `flock` so range calculation and append are one
cross-process critical section without holding the long-running consolidation
lease. Make `boundaries()` and `latest_status()` public. Make watermark
regression raise instead of rewriting the requested sequence.

- [ ] **Step 4: Recover failed work and preserve real diagnostics**

Change `run_pending_once()` to catch `Exception as exc` and write:

```python
self._state_store.transition(
    boundary.boundary_id,
    status="failed",
    error_code="consolidation_failed",
    error_class=type(exc).__name__,
)
```

Change `recover()` to requeue both `running` and `failed`. Failed work is not
returned by normal `pending_boundaries()` until recovery, preventing a hot
retry loop while guaranteeing restart recoverability.

After `consolidate_events()`, inspect the actual `ConsolidationResult`. If both
`episode_event_ids` and `gist_event_ids` are empty, transition to
`covered_without_episode`; otherwise transition to `completed`. Both terminal
states advance the exact boundary watermark.

- [ ] **Step 5: Run Task 4 and existing coordinator tests GREEN**

Expected: genuine failure is observed, restart retries the identical source
tuple, adjacent queued ranges do not overlap, watermark regression fails, lease
contention remains queued, and sparse/group-split source ranges report
`covered_without_episode`.

- [ ] **Step 6: Commit Task 4**

```bash
git add src/fireclaw_core/memory/consolidation_state.py \
  src/fireclaw_core/memory/consolidation_coordinator.py \
  tests/test_memory_consolidation_coordinator.py
git commit -m "fix(memory): recover failed consolidation ranges"
```

Do not run the commit command unless explicitly requested.

---

### Task 5: Complete Terminal Triggers And Enforce Non-Overlapping Scopes

**Files:**

- Modify: `src/fireclaw_core/memory/consolidation_state.py`
- Modify: `src/fireclaw_core/memory/consolidation_coordinator.py`
- Modify: `src/fireclaw_core/mission/mission_agent.py`
- Modify: `tests/test_memory_consolidation_coordinator.py`
- Modify: `tests/test_mission_agent.py`

- [ ] **Step 1: Add RED scope and terminal-path tests**

```python
def test_mission_scope_never_selects_subtask_evidence(tmp_path: Path) -> None:
    coordinator, state_store, store = _coordinator_stack(tmp_path)
    store.append_event(_event("mission-command", subtask_id=None))
    store.append_event(_event("subtask-observation", subtask_id="task-1"))
    store.append_event(_event("mission-terminal", subtask_id=None))
    coordinator.request_terminal_boundary(
        mission_id="m1",
        runtime_mode="real",
        scope_kind="mission",
        robot_id=None,
        subtask_id=None,
        terminal_event_id="mission-terminal",
        terminal_status="completed",
        through_sequence=3,
    )
    boundary = state_store.pending_boundaries()[0]
    assert coordinator._select_eligible_sources(boundary) == [
        "mission-command",
        "mission-terminal",
    ]


def test_terminal_outcome_uses_stable_authority_event_id(tmp_path: Path) -> None:
    agent, store, state_store = _terminal_agent(tmp_path)
    agent.mission_trace("m1")
    agent.mission_trace("m1")
    terminal_ids = [
        event.event_id
        for event in store.list_events(mission_id="m1")
        if event.source_type == "terminal_transition"
    ]
    assert terminal_ids.count("m1:robot-1:task-1:terminal:succeeded") == 1
    assert len(state_store.boundaries()) == 2


def test_immediate_terminal_submit_queues_subtask_boundary(
    tmp_path: Path,
) -> None:
    agent, state_store = _agent_with_submit_status(tmp_path, "failed")
    result = agent.submit_subtask("robot-1", "search", mission_id="m1")
    assert result["status"] == "failed"
    assert any(
        boundary.scope_kind == "subtask"
        and boundary.subtask_id == result["task_id"]
        for boundary in state_store.boundaries()
    )


def test_terminal_cancel_queues_subtask_and_mission_boundaries(
    tmp_path: Path,
) -> None:
    agent, state_store = _agent_with_cancel_status(tmp_path, "cancelled")
    result = agent.cancel_mission("m1")
    assert result["status"] == "cancelled"
    scopes = {
        (boundary.scope_kind, boundary.subtask_id)
        for boundary in state_store.boundaries()
    }
    assert ("subtask", "task-1") in scopes
    assert ("mission", None) in scopes


def test_final_trace_queues_one_mission_boundary(tmp_path: Path) -> None:
    agent, state_store = _terminal_agent(tmp_path)
    first = agent.mission_trace("m1")
    second = agent.mission_trace("m1")
    assert first["status"] == "completed"
    assert second["status"] == "completed"
    mission_boundaries = [
        boundary
        for boundary in state_store.boundaries()
        if boundary.scope_kind == "mission"
    ]
    assert len(mission_boundaries) == 1
```

The stable-event test expects two boundaries after two trace calls: one
subtask boundary and one mission boundary, not duplicate terminal boundaries.

Add these concrete helpers in the same test module:

```python
class _TerminalClient:
    def __init__(
        self,
        *,
        submit_status: str = "accepted",
        trace_status: str = "succeeded",
        cancel_status: str = "cancelled",
    ) -> None:
        self.submit_status = submit_status
        self.trace_status = trace_status
        self.cancel_status = cancel_status

    def submit_task(self, entry, **kwargs):
        return {
            "status": self.submit_status,
            "task_id": "task-1",
            "robot_id": entry.robot_id,
        }

    def get_task_trace(self, entry, task_id):
        return {
            "status": self.trace_status,
            "task_id": task_id,
            "robot_id": entry.robot_id,
        }

    def cancel_task(self, entry, task_id, **kwargs):
        return {
            "status": self.cancel_status,
            "task_id": task_id,
            "robot_id": entry.robot_id,
        }

    def get_events(self, entry, task_id=None, limit=100):
        return []

    def check_presence(self, entry):
        return {
            "robot_id": entry.robot_id,
            "online": True,
            "last_seen_at": "2026-07-24T10:00:00+00:00",
            "state": {},
        }


def _coordinator_stack(tmp_path: Path):
    store = EmbodiedMemoryStore(tmp_path / "embodied.jsonl")
    producer = EmbodiedMemoryProducer(
        store,
        producer_type="memory_consolidator",
        producer_id="test-consolidator",
    )
    engine = FireClawConsolidationEngine(
        store=store,
        producer=producer,
    )
    state_store = ConsolidationStateStore(tmp_path / "state.jsonl")
    coordinator = MemoryConsolidationCoordinator(
        engine=engine,
        state_store=state_store,
        store=store,
        lock_path=tmp_path / "consolidation.lock",
    )
    return coordinator, state_store, store


def _event(
    event_id: str,
    *,
    subtask_id: str | None,
) -> EmbodiedMemoryEvent:
    return EmbodiedMemoryEvent(
        event_id=event_id,
        mission_id="m1",
        event_type="outcome" if "terminal" in event_id else "observation",
        payload={"event_id": event_id},
        runtime_mode="real",
        source_type="terminal_transition" if "terminal" in event_id else "test",
        observed_at="2026-07-24T10:00:00+00:00",
        robot_id="robot-1" if subtask_id is not None else None,
        subtask_id=subtask_id,
        sensitivity="standard",
    )


def _terminal_agent_with_client(
    tmp_path: Path,
    client: _TerminalClient,
    *,
    record_subtask: bool,
):
    coordinator, state_store, store = _coordinator_stack(tmp_path)
    robot_registry = RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot-1.local",
            )
        ]
    )
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission_registry.create_mission(
        mission_id="m1",
        session_id=None,
        command="search",
        created_at="2026-07-24T10:00:00+00:00",
    )
    if record_subtask:
        mission_registry.record_subtask(
            mission_id="m1",
            robot_id="robot-1",
            task_id="task-1",
            command="search",
            status="accepted",
            created_at="2026-07-24T10:00:00+00:00",
        )
    agent = MissionAgent(
        registry=robot_registry,
        subagent_client=client,
        mission_registry=mission_registry,
        embodied_memory_producer=EmbodiedMemoryProducer(
            store,
            producer_type="mission_agent",
            producer_id="test-agent",
        ),
        embodied_runtime_mode="real",
        consolidation_coordinator=coordinator,
    )
    return agent, store, state_store


def _terminal_agent(tmp_path: Path):
    return _terminal_agent_with_client(
        tmp_path,
        _TerminalClient(trace_status="succeeded"),
        record_subtask=True,
    )


def _agent_with_submit_status(tmp_path: Path, status: str):
    agent, _, state_store = _terminal_agent_with_client(
        tmp_path,
        _TerminalClient(submit_status=status),
        record_subtask=False,
    )
    return agent, state_store


def _agent_with_cancel_status(tmp_path: Path, status: str):
    agent, _, state_store = _terminal_agent_with_client(
        tmp_path,
        _TerminalClient(cancel_status=status),
        record_subtask=True,
    )
    return agent, state_store
```

- [ ] **Step 2: Run the five tests and verify RED**

Expected: mission scope behaves as a wildcard, stable IDs are payload-only,
immediate submit and terminal cancel do not trigger, and no mission boundary
exists.

- [ ] **Step 3: Add explicit scope-kind validation and selection**

Extend boundary/state serialization with `scope_kind`. For old journal lines,
`from_dict()` derives `"subtask"` when `subtask_id` is present and `"mission"`
otherwise. Validate:

```python
if self.scope_kind == "subtask":
    if not self.robot_id or not self.subtask_id:
        raise ValueError("subtask boundary requires robot_id and subtask_id")
elif self.scope_kind == "mission":
    if self.subtask_id is not None:
        raise ValueError("mission boundary cannot name subtask_id")
else:
    raise ValueError("scope_kind must be mission or subtask")
```

Include `scope_kind` in `scope_key()`, `ConsolidationWatermark`, and
`_stable_boundary_id()` so a migrated mission boundary cannot collide with a
subtask boundary.

Select sources with exact predicates:

```python
if boundary.scope_kind == "subtask":
    if (
        event.robot_id != boundary.robot_id
        or event.subtask_id != boundary.subtask_id
    ):
        continue
else:
    if event.subtask_id is not None:
        continue
```

This makes source ownership disjoint even when mission and subtask watermarks
cover the same absolute authority sequence interval.

- [ ] **Step 4: Make terminal evidence truly idempotent**

Add `event_id: str | None = None` to `_record_embodied_memory()` and pass it to
`EmbodiedMemoryProducer.record_event()`. Replace `_record_terminal_outcome()`
with an idempotent implementation that:

1. looks up the deterministic event ID;
2. appends it only if absent;
3. rejects a same-ID event whose status/scope differs;
4. locates the event's 1-based sequence in `store.list_events(mission_id=...)`;
5. reserves the boundary at exactly that sequence.

Use these IDs:

```python
def _subtask_terminal_event_id(
    mission_id: str,
    robot_id: str,
    subtask_id: str,
    status: str,
) -> str:
    return f"{mission_id}:{robot_id}:{subtask_id}:terminal:{status}"


def _mission_terminal_event_id(mission_id: str, status: str) -> str:
    return f"{mission_id}:mission:terminal:{status}"
```

- [ ] **Step 5: Call the helper from every terminal path**

- After `submit_subtask()` persists a terminal returned status, record the
  subtask transition.
- When `mission_trace()` changes a subtask into a terminal status, record the
  subtask transition.
- When `cancel_mission()` receives a terminal robot result, persist the
  terminal subtask status and record its transition.
- After each of those operations, derive the registry mission status. If it is
  terminal, record one mission-level terminal outcome and boundary.
- Keep `cancel_requested` non-terminal.
- If terminal evidence or boundary journaling fails, preserve the mission
  result and log only stable code and exception class.

- [ ] **Step 6: Run Task 5 and mission compatibility tests GREEN**

Expected: all five new tests pass; repeated trace calls append no duplicate
events; command/plan/mission evidence is consolidated only by mission scope;
subtask source tuples remain disjoint; existing submit, trace, and cancel
results do not change.

- [ ] **Step 7: Commit Task 5**

```bash
git add src/fireclaw_core/memory/consolidation_state.py \
  src/fireclaw_core/memory/consolidation_coordinator.py \
  src/fireclaw_core/mission/mission_agent.py \
  tests/test_memory_consolidation_coordinator.py \
  tests/test_mission_agent.py
git commit -m "fix(memory): cover deterministic terminal scopes"
```

Do not run the commit command unless explicitly requested.

---

### Task 6: Fix Worker Lifecycle, Diagnostics, Documentation, And Final Proof

**Files:**

- Modify: `src/fireclaw_core/memory/consolidation_coordinator.py`
- Modify: `src/fireclaw_core/mission/mission_gateway.py`
- Modify: `tests/test_memory_consolidation_coordinator.py`
- Modify: `tests/test_mission_gateway.py`
- Modify: `docs/architecture/embodied-memory-event-production.md`
- Modify: `memory/2026-07-24/memory-operational-correctness-p0.md`

- [ ] **Step 1: Add RED worker lifecycle tests**

```python
def test_boundary_request_wakes_sleeping_worker(tmp_path: Path) -> None:
    coordinator, state_store, _ = _coordinator_stack(tmp_path)
    coordinator.start()
    try:
        assert _wait_until(lambda: coordinator.status()["worker_waiting"])
        started = time.monotonic()
        _append_boundary_source_events(coordinator, count=3)
        _request_subtask_terminal(coordinator, through_sequence=3)
        assert _wait_until(
            lambda: state_store.pending_boundaries() == [],
            timeout_seconds=2.0,
        )
        assert time.monotonic() - started < 2.0
    finally:
        coordinator.stop()


def test_stop_timeout_does_not_drop_live_thread_reference(
    tmp_path: Path,
) -> None:
    coordinator, _, _ = _coordinator_stack(tmp_path)
    live_thread = _NeverStopsThread()
    coordinator._worker_thread = live_thread
    try:
        coordinator.stop(timeout_seconds=0.01)
        raise AssertionError("expected stop timeout")
    except TimeoutError:
        pass
    assert coordinator._worker_thread is live_thread
    coordinator.start()
    assert coordinator._worker_thread is live_thread


def test_gateway_stops_request_intake_before_coordinator() -> None:
    calls: list[str] = []
    gateway = _gateway_with_lifecycle_spies(calls)
    gateway.stop()
    assert calls == [
        "server.shutdown",
        "server.close",
        "server.join",
        "coordinator.stop",
    ]
```

Add the lifecycle test doubles:

```python
class _NeverStopsThread:
    def join(self, timeout: float | None = None) -> None:
        return None

    def is_alive(self) -> bool:
        return True


def _wait_until(
    predicate,
    *,
    timeout_seconds: float = 2.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _gateway_with_lifecycle_spies(calls: list[str]) -> MissionGateway:
    class _Server:
        def shutdown(self) -> None:
            calls.append("server.shutdown")

        def server_close(self) -> None:
            calls.append("server.close")

    class _Thread:
        def join(self, timeout: float | None = None) -> None:
            calls.append("server.join")

    class _Coordinator:
        def stop(self) -> None:
            calls.append("coordinator.stop")

    gateway = MissionGateway.__new__(MissionGateway)
    gateway._server = _Server()
    gateway._thread = _Thread()
    gateway.mission_agent = SimpleNamespace(
        consolidation_coordinator=_Coordinator()
    )
    return gateway
```

- [ ] **Step 2: Run the three tests and verify RED**

Expected: work can wait 30 seconds, stop drops a live thread reference, and
Gateway stops the coordinator before request intake.

- [ ] **Step 3: Implement independent wake and stop events**

Add `self._wake_event = threading.Event()`. Boundary reservation sets it.
Worker waiting uses:

```python
while not self._stop_event.is_set():
    self._wake_event.clear()
    result = self.run_pending_once()
    if result.processed == 0 and result.lock_busy == 0:
        self._wake_event.wait(timeout=30.0)
    else:
        self._wake_event.wait(timeout=1.0)
```

`stop()` sets both events, joins, and:

```python
thread = self._worker_thread
if thread is None:
    return
thread.join(timeout=timeout_seconds)
if thread.is_alive():
    raise TimeoutError("consolidation coordinator did not stop")
self._worker_thread = None
```

Do not allow `start()` to replace a live thread. Add content-free status fields
for queued/running/failed counts, worker alive/waiting, last success timestamp,
and latest watermark by scope.

- [ ] **Step 4: Correct Mission Gateway shutdown order**

Shutdown and close the server, join its thread, then stop the coordinator.
`serve_forever()` must use `try/finally` so the same ordering applies on an
exception. A boundary already journaled remains recoverable even if worker
shutdown times out.

- [ ] **Step 5: Update architecture documentation**

Document:

- v2 raw-payload verification before parsing;
- authenticated request header and separate bearer/scope authorization;
- source and destination peer policies;
- explicit insecure simulation switch and real HTTPS requirement;
- startup hydration newest-first round-robin;
- mission/subtask scope ownership;
- atomic boundary reservation and failed-boundary startup recovery;
- worker wake/stop behavior;
- HMAC's pairwise-authentication limitation.

Do not claim PKI, transport confidentiality from HMAC, or experimental novelty
for these infrastructure controls.

- [ ] **Step 6: Execute focused P0 tests with a real runner**

Use this exact dependency-free runner. It discovers top-level `test_*`
functions, provides `tmp_path`, fails on unsupported fixtures, and rejects a
zero-test file:

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 - <<'PY'
from inspect import signature
from pathlib import Path
import runpy
import tempfile

files = (
    "tests/test_memory_replication_security.py",
    "tests/test_replication_gateway.py",
    "tests/test_working_memory_hydration.py",
    "tests/test_memory_consolidation_coordinator.py",
    "tests/test_mission_runtime.py",
    "tests/test_mission_agent.py",
)
total = 0
for path in files:
    namespace = runpy.run_path(path)
    tests = [
        (name, value)
        for name, value in namespace.items()
        if name.startswith("test_") and callable(value)
    ]
    if not tests:
        raise AssertionError(f"zero tests discovered in {path}")
    executed = 0
    for name, test in sorted(tests):
        parameters = tuple(signature(test).parameters)
        if parameters == ():
            test()
        elif parameters == ("tmp_path",):
            with tempfile.TemporaryDirectory() as directory:
                test(Path(directory))
        else:
            raise AssertionError(
                f"unsupported fixtures for {path}::{name}: {parameters}"
            )
        executed += 1
        total += 1
        print(f"PASS {path}::{name}")
    print(f"EXECUTED {path}: {executed}")
print(f"TOTAL EXECUTED: {total}")
PY
```

Expected: every discovered supported test prints `PASS`; the runner prints an
explicit executed count greater than zero for every file. A zero-test exit is
a failure.

- [ ] **Step 7: Run static and compatibility verification**

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q \
  src/fireclaw_core/memory \
  src/fireclaw_core/mission \
  src/fireclaw_core/gateway \
  src/fireclaw_core/subagent
git diff --check
```

Run socket-free Mission Gateway unit tests in the sandbox. Record the existing
socket binding restriction instead of treating an unexecuted server test as a
pass.

- [ ] **Step 8: Re-run the four original reproductions**

Expected:

```text
runtime_hydrated_count=1
hydrated_ids_capacity_2=['e1', 'e2']
unverified_auth_imported=0
failed_boundary_pending_after_recover=1
```

Also verify:

```text
invalid signature first -> signature_invalid
valid request with the same nonce -> verified
naive timestamp -> invalid_timestamp
```

- [ ] **Step 9: Update persistent evidence**

Append the exact timestamp, commands, RED/GREEN results, executed test counts,
files changed, sandbox limitations, and remaining deployment assumptions to
`memory/2026-07-24/memory-operational-correctness-p0.md`. Do not repeat the
previous unsupported `261/261` claim.

- [ ] **Step 10: Commit Task 6**

```bash
git add src/fireclaw_core/memory/consolidation_coordinator.py \
  src/fireclaw_core/mission/mission_gateway.py \
  tests/test_memory_consolidation_coordinator.py \
  tests/test_mission_gateway.py \
  docs/architecture/embodied-memory-event-production.md \
  memory/2026-07-24/memory-operational-correctness-p0.md
git commit -m "docs(memory): record P0 operational proof"
```

Do not run the commit command unless explicitly requested.

---

## Acceptance Criteria

P0 repair is complete only when all statements are demonstrated by executed
tests or static verification:

1. Real replication refuses HTTP, missing security config, missing policy,
   unknown peer, unknown key, unsigned response, and partially configured auth.
2. The real `RobotSubagentClient` signs the exact request scope; robot Gateway
   verifies before export and signs the v2 response.
3. Raw batch authentication succeeds before `ReplicationBatch.from_dict()`;
   destination policy rechecks robot, mission, runtime, sensitivity, and policy
   ID.
4. Restricted evidence and relations with hidden endpoints never reach the
   destination; cursor and bounded omission counts remain correct.
5. Invalid signatures do not poison replay state; timestamps are timezone
   aware; production nonce storage is bounded; default nonces are random.
6. Runtime construction hydrates fresh active-mission evidence into the same
   injected working-memory instance without authority writes.
7. Capacity pressure preserves newest evidence and deterministically shares
   available slots across active missions.
8. Failed and interrupted boundaries requeue on startup with the identical
   source set and actual exception class.
9. Concurrent boundary requests reserve adjacent non-overlapping ranges;
   watermark regression is rejected.
10. Mission and subtask scopes have disjoint exact source predicates.
11. Immediate terminal submit, trace-discovered terminal state, terminal
    cancellation, and final mission terminal state all create deterministic,
    idempotent evidence and boundaries.
12. Sparse or split groups with no derived artifact are reported as
    `covered_without_episode`.
13. Boundary requests wake the worker; a timed-out stop retains the live thread
    reference; Gateway stops request intake before maintenance workers.
14. Every reported test count comes from a runner that executed at least one
    test; `compileall` and `git diff --check` pass.

## Research And Publication Boundary

These repairs establish engineering validity for later embodied-memory
experiments. They do not by themselves constitute a new memory algorithm or a
publication-level contribution. After P0 passes, research claims may safely
evaluate memory usefulness, multi-robot evidence sharing, recovery under
degraded communication, and safety outcomes because provenance, scope,
restart, and replication controls are no longer confounds.
