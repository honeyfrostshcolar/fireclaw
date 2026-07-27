# Memory Operational Correctness P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or
> `superpowers:executing-plans` task-by-task. Do not run tasks concurrently:
> consolidation, runtime assembly, and replication touch shared contracts.

**Goal:** Make FireClaw consolidation operational and non-overlapping, hydrate
fresh working memory after restart, and fail closed for unauthorized or
over-broad cross-robot evidence replication.

**Architecture:** Add an append-only consolidation boundary/watermark journal
and file lease in front of the existing deterministic artifact jobs. Runtime
terminal transitions enqueue closed boundaries for a lifecycle-managed worker.
Normal runtime hydrates a bounded fresh projection from authority. Replication
v2 binds explicit peer policy and HMAC authentication to the existing
cursor/checksum reconciliation flow.

**Tech Stack:** Python 3.10-compatible dataclasses and typing, append-only JSONL,
`fcntl.flock`, `threading`, `hmac`/`hashlib`, existing FireClaw
MissionAgent/Gateway/Store APIs, dependency-free pytest-compatible test
functions.

**Design:** See
`docs/superpowers/specs/2026-07-24-memory-operational-correctness-p0-design.md`.

---

## Scope And File Map

Create:

- `src/fireclaw_core/memory/consolidation_state.py`
- `src/fireclaw_core/memory/consolidation_coordinator.py`
- `src/fireclaw_core/memory/replication_security.py`
- `tests/test_memory_consolidation_coordinator.py`
- `tests/test_working_memory_hydration.py`
- `tests/test_memory_replication_security.py`
- `memory/2026-07-24/memory-operational-correctness-p0.md`

Modify:

- `src/fireclaw_core/memory/consolidation.py`
- `src/fireclaw_core/memory/consolidation_jobs.py`
- `src/fireclaw_core/memory/working_memory.py`
- `src/fireclaw_core/memory/reconciliation.py`
- `src/fireclaw_core/mission/mission_registry.py`
- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/mission/mission_runtime.py`
- `src/fireclaw_core/mission/mission_gateway.py`
- `src/fireclaw_core/subagent/subagent_client.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/gateway/serve.py`
- `src/fireclaw_core/gateway/method_scopes.py`
- `docs/architecture/embodied-memory-event-production.md`
- `tests/test_mission_registry.py`
- `tests/test_mission_agent.py`
- `tests/test_mission_runtime.py`
- `tests/test_gateway.py`
- `tests/test_mission_gateway.py`

Do not modify dense retrieval, Planner memory isolation, Entity matching,
R*Tree result semantics, lifecycle approval formats, or raw evidence retention.
Do not commit unless the user explicitly asks.

## Focused Test Runner

The user previously chose not to create a Python 3.11 environment or install
pytest. Keep new focused tests free of pytest imports and run them with the
existing Python 3.10 environment:

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 - <<'PY'
import inspect
import runpy
import tempfile
from pathlib import Path

files = (
    "tests/test_memory_consolidation_coordinator.py",
    "tests/test_working_memory_hydration.py",
    "tests/test_memory_replication_security.py",
)

for filename in files:
    namespace = runpy.run_path(filename)
    for name, function in sorted(namespace.items()):
        if not name.startswith("test_") or not callable(function):
            continue
        parameters = tuple(inspect.signature(function).parameters)
        if not parameters:
            function()
        elif parameters == ("tmp_path",):
            with tempfile.TemporaryDirectory() as directory:
                function(Path(directory))
        else:
            raise RuntimeError(
                f"Unsupported test signature: {filename}:{name}"
                f"{inspect.signature(function)}"
            )
        print(f"PASS {filename}:{name}")
PY
```

Expected GREEN result: one `PASS` line per new focused test and exit code `0`.

---

## Task 1: Add Durable Consolidation Boundaries And Watermarks

**Files:**

- Create: `src/fireclaw_core/memory/consolidation_state.py`
- Create: `tests/test_memory_consolidation_coordinator.py`

- [ ] **Step 1: Write failing boundary identity and state replay tests**

Add tests for:

```python
def test_boundary_rejects_invalid_or_empty_scope() -> None: ...
def test_boundary_id_is_stable_for_same_terminal_event() -> None: ...
def test_state_store_replays_latest_boundary_status_and_watermark(tmp_path: Path) -> None: ...
def test_completed_watermark_never_moves_backwards(tmp_path: Path) -> None: ...
def test_state_store_ignores_truncated_final_jsonl_line(tmp_path: Path) -> None: ...
```

The stable identity must include mission, runtime, robot, subtask, terminal
event ID, and `through_sequence`.

- [ ] **Step 2: Run Task 1 tests and verify RED**

Expected: import failure for `consolidation_state`.

- [ ] **Step 3: Implement immutable state types**

Add:

```python
CONSOLIDATION_BOUNDARY_STATUSES = frozenset({
    "queued",
    "running",
    "completed",
    "covered_without_episode",
    "failed",
})

@dataclass(frozen=True)
class ConsolidationBoundary: ...

@dataclass(frozen=True)
class ConsolidationBoundaryState: ...

@dataclass(frozen=True)
class ConsolidationWatermark: ...
```

Validate runtime modes, non-negative sequences, strict
`after_sequence < through_sequence`, terminal event ID, trigger reason, and
stable IDs.

- [ ] **Step 4: Implement append-only state replay**

`ConsolidationStateStore` must:

- fsync every transition;
- expose `ensure_boundary`, `transition`, `pending_boundaries`, and `watermark`;
- reject identity conflicts;
- reject completed watermark regression;
- store error code/class but not exception messages;
- tolerate blank/truncated invalid lines during recovery.

- [ ] **Step 5: Run Task 1 tests and verify GREEN**

---

## Task 2: Add Explicit Closed-Source Consolidation

**Files:**

- Modify: `src/fireclaw_core/memory/consolidation.py`
- Modify: `src/fireclaw_core/memory/consolidation_jobs.py`
- Modify: `tests/test_memory_consolidation_coordinator.py`

- [ ] **Step 1: Write failing exact-source tests**

Add:

```python
def test_consolidate_events_rejects_missing_duplicate_or_cross_scope_ids(tmp_path: Path) -> None: ...
def test_consolidate_events_uses_only_named_authority_events(tmp_path: Path) -> None: ...
def test_successive_closed_source_sets_are_disjoint(tmp_path: Path) -> None: ...
def test_retry_repairs_same_job_without_new_episode_or_gist(tmp_path: Path) -> None: ...
```

Assert disjointness over Episode `derived_from`, not only deterministic IDs.

- [ ] **Step 2: Run the new tests and verify RED**

Expected: `FireClawConsolidationEngine` has no `consolidate_events()`.

- [ ] **Step 3: Extract source validation and chunk processing**

Implement:

```python
def consolidate_events(
    self,
    *,
    mission_id: str,
    runtime_mode: str,
    source_event_ids: tuple[str, ...],
) -> ConsolidationResult:
    ...
```

Requirements:

- hydrate all IDs from the authoritative Store;
- preserve authority order;
- reject missing, repeated, relation, derived, cross-mission, or cross-runtime
  inputs;
- reuse existing temporal chunks, deterministic jobs, safety enrichment, and
  artifact verification;
- never infer extra source events by timestamp.

- [ ] **Step 4: Keep manual compatibility explicit**

Retain `consolidate_mission()` as a documented offline/manual wrapper. Refactor
it to call the shared exact-source implementation without changing existing
result shapes.

- [ ] **Step 5: Harden job-state errors**

Change job failures to store stable error code/class separately from any
content-bearing exception message. Preserve backward reading of existing
`error` fields.

- [ ] **Step 6: Run Task 2 tests and verify GREEN**

---

## Task 3: Add Cross-Process Lease And Recovery Coordinator

**Files:**

- Create: `src/fireclaw_core/memory/consolidation_coordinator.py`
- Modify: `src/fireclaw_core/memory/consolidation_state.py`
- Modify: `tests/test_memory_consolidation_coordinator.py`

- [ ] **Step 1: Write failing coordinator tests**

Add:

```python
def test_non_terminal_status_does_not_create_boundary(tmp_path: Path) -> None: ...
def test_terminal_boundary_selects_only_open_sequence_range(tmp_path: Path) -> None: ...
def test_insufficient_closed_range_advances_watermark_without_episode(tmp_path: Path) -> None: ...
def test_delayed_event_is_processed_only_by_later_boundary(tmp_path: Path) -> None: ...
def test_restart_recovers_queued_and_running_boundaries(tmp_path: Path) -> None: ...
def test_lease_contention_leaves_boundary_queued(tmp_path: Path) -> None: ...
def test_failure_before_watermark_retries_same_source_set(tmp_path: Path) -> None: ...
```

- [ ] **Step 2: Run the coordinator tests and verify RED**

- [ ] **Step 3: Implement Linux file lease**

Add a private context manager using `fcntl.flock(fd, LOCK_EX | LOCK_NB)`.
Write content-free owner PID/start time metadata only after lock acquisition.
Always unlock/close in `finally`.

Do not delete a lock file to release ownership; kernel lock state is
authoritative.

- [ ] **Step 4: Implement exact boundary selection**

Enumerate authoritative records using absolute 1-based sequence positions.
Select only valid embodied events inside the boundary scope/range. Keep
cross-robot supporting evidence selection inside the existing engine and do not
count it as Episode source ownership.

- [ ] **Step 5: Implement coordinator APIs**

Add:

```python
class MemoryConsolidationCoordinator:
    def request_terminal_boundary(...): ...
    def run_pending_once(self, *, max_boundaries: int = 8): ...
    def recover(self): ...
    def start(self): ...
    def stop(self, *, timeout_seconds: float = 5.0): ...
    def status(self): ...
```

Worker requirements:

- one daemon worker per coordinator;
- bounded work per wake-up;
- stop event and bounded join;
- no payload logging;
- failed work stays recoverable;
- mission execution thread performs journal append only.

- [ ] **Step 6: Run Task 3 tests and verify GREEN**

---

## Task 4: Persist Terminal Transitions And Wire Runtime Consolidation

**Files:**

- Modify: `src/fireclaw_core/mission/mission_agent.py`
- Modify: `src/fireclaw_core/mission/mission_runtime.py`
- Modify: `src/fireclaw_core/mission/mission_gateway.py`
- Modify: `src/fireclaw_core/gateway/serve.py`
- Modify: `tests/test_mission_agent.py`
- Modify: `tests/test_mission_runtime.py`
- Modify: `tests/test_mission_gateway.py`

- [ ] **Step 1: Write failing integration tests**

Cover:

```python
def test_terminal_trace_transition_persists_outcome_then_queues_boundary(tmp_path: Path) -> None: ...
def test_repeated_terminal_trace_does_not_queue_duplicate_boundary(tmp_path: Path) -> None: ...
def test_cancel_requested_is_not_terminal_boundary(tmp_path: Path) -> None: ...
def test_runtime_constructs_consolidator_producer_and_coordinator(tmp_path: Path) -> None: ...
def test_gateway_start_recovers_and_stop_joins_memory_worker(tmp_path: Path) -> None: ...
def test_consolidation_failure_does_not_change_mission_result(tmp_path: Path) -> None: ...
```

- [ ] **Step 2: Run selected tests and verify RED**

- [ ] **Step 3: Construct runtime components**

Extend `MissionRuntimePaths` with optional explicit paths for:

- consolidation jobs;
- consolidation state;
- consolidation lock.

When omitted, derive paths next to the evidence Store. Construct a
`memory_consolidator` producer, `FireClawConsolidationEngine`, and
`MemoryConsolidationCoordinator` only when embodied memory is enabled.

Create `JsonlMissionRegistry` once and inject the same instance everywhere.

- [ ] **Step 4: Centralize terminal transition recording**

Add one `MissionAgent` helper that accepts an already persisted registry
transition, appends a terminal embodied `outcome`, and then queues the boundary.
Use it for immediate terminal submit results, terminal `mission_trace()`
updates, cancellation, and final mission terminal status.

Use a deterministic terminal transition ID/key so polling cannot append
duplicate outcome events.

- [ ] **Step 5: Own worker lifecycle**

Mission Gateway `start()`/`serve_forever()` must call coordinator recovery/start
before accepting requests. `stop()` must stop/join the worker after stopping
request intake. Direct non-server use leaves queued requests durable.

- [ ] **Step 6: Run Task 4 tests and verify GREEN**

---

## Task 5: Hydrate Fresh Working Memory At Startup

**Files:**

- Modify: `src/fireclaw_core/memory/working_memory.py`
- Modify: `src/fireclaw_core/mission/mission_registry.py`
- Modify: `src/fireclaw_core/mission/mission_runtime.py`
- Create: `tests/test_working_memory_hydration.py`
- Modify: `tests/test_mission_registry.py`
- Modify: `tests/test_mission_runtime.py`

- [ ] **Step 1: Write failing hydration tests**

Add:

```python
def test_list_missions_reports_derived_statuses(tmp_path: Path) -> None: ...
def test_hydration_selects_only_active_mission_and_runtime(tmp_path: Path) -> None: ...
def test_hydration_excludes_stale_and_future_skewed_events(tmp_path: Path) -> None: ...
def test_hydration_is_deterministic_and_capacity_bounded(tmp_path: Path) -> None: ...
def test_hydration_reserves_fair_capacity_for_active_missions(tmp_path: Path) -> None: ...
def test_hydration_does_not_append_authority_records(tmp_path: Path) -> None: ...
def test_runtime_hydration_failure_starts_with_empty_projection(tmp_path: Path) -> None: ...
```

- [ ] **Step 2: Run hydration tests and verify RED**

- [ ] **Step 3: Add public mission listing**

Add `JsonlMissionRegistry.list_missions()` returning immutable
`MissionRecord`s with status derived by `_mission_status()`. Do not expose the
private replay dictionary.

- [ ] **Step 4: Implement deterministic hydration report**

Add:

```python
@dataclass(frozen=True)
class WorkingMemoryHydrationReport:
    considered: int
    selected: int
    added: int
    stale: int
    wrong_scope: int
    oversized: int
```

Implement `hydrate_recent()` using existing freshness and size contracts.
Apply a fair per-mission quota, then fill remaining capacity by recency.

- [ ] **Step 5: Wire hydration after Store and registry construction**

Hydrate only `created`/`running` missions in the configured embodied runtime.
Capture content-free diagnostics. Do not block runtime startup on failure.

- [ ] **Step 6: Run Task 5 tests and verify GREEN**

---

## Task 6: Add Replication Policy And Signed Batch Primitives

**Files:**

- Create: `src/fireclaw_core/memory/replication_security.py`
- Modify: `src/fireclaw_core/memory/reconciliation.py`
- Create: `tests/test_memory_replication_security.py`

- [ ] **Step 1: Write failing policy/authentication tests**

Add:

```python
def test_export_policy_omits_disallowed_sensitivity_and_advances_cursor(tmp_path: Path) -> None: ...
def test_relation_is_omitted_when_endpoint_is_not_exportable(tmp_path: Path) -> None: ...
def test_signed_batch_round_trip(tmp_path: Path) -> None: ...
def test_tampered_batch_or_scope_fails_verification(tmp_path: Path) -> None: ...
def test_wrong_peer_robot_store_or_key_is_rejected(tmp_path: Path) -> None: ...
def test_expired_timestamp_and_reused_nonce_are_rejected(tmp_path: Path) -> None: ...
def test_real_runtime_rejects_unsigned_v1_batch(tmp_path: Path) -> None: ...
def test_simulation_can_explicitly_allow_legacy_unsigned_batch(tmp_path: Path) -> None: ...
```

- [ ] **Step 2: Run security tests and verify RED**

- [ ] **Step 3: Implement policy and key-provider contracts**

Add validated `ReplicationPeerPolicy`, `ReplicationRequestScope`,
`ReplicationAuthMetadata`, `ReplicationKeyProvider`, and an in-memory test key
provider. Production configuration must inject keys; do not add default
secrets.

- [ ] **Step 4: Implement canonical signing and verification**

Canonicalize protocol version, identity, mission/runtime, cursor range,
omission counts, envelope checksums, issued-at, and nonce. Sign with
HMAC-SHA256 and compare with `hmac.compare_digest()`.

Verification must complete before `ReplicationBatch.from_dict()` hydrates
individual authority records.

- [ ] **Step 5: Add policy-aware exporter**

Require exact mission/runtime and a server-bound peer policy. Filter sensitivity
before envelope creation. Precompute exportable event IDs so relations with
omitted endpoints are omitted. Preserve absolute cursor progress and add
content-free omission counts.

- [ ] **Step 6: Add destination admission**

Require the verified peer/policy context in `ingest_batch()`. Recheck event
sensitivity, robot/store binding, mission, and runtime before append. Never
advance a checkpoint for a batch that fails whole-batch authentication.

- [ ] **Step 7: Run Task 6 tests and verify GREEN**

---

## Task 7: Wire Authenticated Replication Through Gateways

**Files:**

- Modify: `src/fireclaw_core/subagent/subagent_client.py`
- Modify: `src/fireclaw_core/gateway/gateway.py`
- Modify: `src/fireclaw_core/gateway/method_scopes.py`
- Modify: `src/fireclaw_core/mission/mission_gateway.py`
- Modify: `src/fireclaw_core/gateway/serve.py`
- Modify: `tests/test_gateway.py`
- Modify: `tests/test_mission_gateway.py`

- [ ] **Step 1: Write failing Gateway/client tests**

Cover:

```python
def test_replication_endpoint_requires_exact_mission_and_runtime(tmp_path: Path) -> None: ...
def test_replication_endpoint_rejects_unverified_peer_before_export(tmp_path: Path) -> None: ...
def test_client_signs_request_and_verifies_signed_batch(tmp_path: Path) -> None: ...
def test_real_sync_rejects_http_robot_endpoint(tmp_path: Path) -> None: ...
def test_real_sync_rejects_missing_peer_policy_or_key(tmp_path: Path) -> None: ...
def test_simulation_http_requires_explicit_override(tmp_path: Path) -> None: ...
def test_sync_reports_content_free_auth_and_policy_failures(tmp_path: Path) -> None: ...
```

- [ ] **Step 2: Run selected Gateway tests and verify RED**

- [ ] **Step 3: Bind replication credentials to server configuration**

Add explicit configuration for:

- local peer/key ID;
- key provider or secret-file reference;
- peer policy map;
- allowed timestamp skew and nonce cache size;
- simulation-only insecure HTTP/legacy switch.

Do not source peer identity from `X-Operator-Scopes`.

- [ ] **Step 4: Sign requests and batches**

`RobotSubagentClient.get_memory_replication()` signs the exact method, path,
query, peer, timestamp, nonce, and empty-body digest. Robot Gateway verifies the
request before calling the exporter and returns a signed v2 batch.

- [ ] **Step 5: Enforce transport policy**

In real runtime, refuse non-HTTPS robot base URLs before network I/O.
Simulation HTTP requires an explicit configuration flag. Keep Python's default
TLS certificate validation enabled.

- [ ] **Step 6: Verify before reconcile**

Mission Gateway verifies the signed batch, expected robot/store identity, and
policy ID before constructing/importing records. Continue using exact cursor
checks and bounded `max_batches`.

- [ ] **Step 7: Run Task 7 tests and verify GREEN**

---

## Task 8: Operational Diagnostics, Documentation, And Final Verification

**Files:**

- Modify: `docs/architecture/embodied-memory-event-production.md`
- Modify: `src/fireclaw_core/memory/consolidation_coordinator.py`
- Modify: `src/fireclaw_core/memory/reconciliation.py`
- Modify: `src/fireclaw_core/mission/mission_runtime.py`
- Modify: `tests/test_memory_consolidation_coordinator.py`
- Modify: `tests/test_working_memory_hydration.py`
- Modify: `tests/test_memory_replication_security.py`
- Create: `memory/2026-07-24/memory-operational-correctness-p0.md`

- [ ] **Step 1: Add content-free status reports**

Expose bounded status for consolidation queue/watermarks, hydration counts, and
replication auth/policy outcomes. Verify that status and log records never
contain event payloads, secrets, signatures, or raw exception messages.

- [ ] **Step 2: Update architecture documentation**

Document:

- closed source ranges and source/supporting evidence distinction;
- coordinator lifecycle and crash recovery;
- startup hydration selection;
- replication v2 policy/authentication;
- HTTPS requirement for real runtime;
- explicit remaining limitation that HMAC is pairwise authentication, not PKI.

- [ ] **Step 3: Run the focused Python 3.10 runner**

Use the command in **Focused Test Runner**.

- [ ] **Step 4: Run selected existing compatibility tests**

Use the same dependency-free function runner for selected plain functions in:

```text
tests/test_mission_registry.py
tests/test_mission_runtime.py
tests/test_mission_agent.py
tests/test_gateway.py
tests/test_mission_gateway.py
```

If a selected file imports pytest or needs unsupported fixtures, record that
limitation instead of installing dependencies or creating Python 3.11.

- [ ] **Step 5: Run static checks**

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q \
  src/fireclaw_core/memory \
  src/fireclaw_core/mission \
  src/fireclaw_core/gateway
git diff --check
```

- [ ] **Step 6: Record exact results**

Update `memory/2026-07-24/memory-operational-correctness-p0.md` with:

- commands and timestamps;
- RED/GREEN results;
- files changed;
- failure injection results;
- remaining test limitations;
- runtime/security configuration assumptions;
- next recommended task.

---

## Acceptance Criteria

P0 is complete only when all of the following are true:

1. normal runtime constructs a consolidation coordinator;
2. persisted terminal transitions queue closed boundaries;
3. successive completed Episode source sets are disjoint;
4. queued/running boundaries recover after restart;
5. two processes cannot process one Store concurrently;
6. consolidation failure does not change mission execution outcome;
7. fresh active-mission working memory is restored without authority writes;
8. replication export filters sensitivity and relation closure at the source;
9. signed batches are verified before record hydration/import;
10. real-runtime HTTP, missing keys, unknown peers, and unsigned batches fail
    closed;
11. all new focused Python 3.10 tests pass;
12. `compileall` and `git diff --check` pass;
13. raw evidence, mission/runtime isolation, and Planner memory safety envelopes
    remain unchanged.

