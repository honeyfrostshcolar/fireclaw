# Memory Operational Correctness P0 Design

**Date:** 2026-07-24

**Status:** Planned

## 1. Purpose

FireClaw already has append-only embodied evidence, resumable deterministic
Episode/Gist jobs, bounded working memory, and cursor-based cross-robot
reconciliation. Three operational gaps remain:

1. consolidation is not wired into the normal runtime and repeated whole-store
   scans can derive overlapping Episode/Gist source ranges;
2. working memory starts empty after a process restart even when fresh
   authoritative evidence exists;
3. replication validates record checksums and declared robot IDs, but does not
   cryptographically authenticate the peer or enforce a sensitivity export
   policy.

This P0 makes those mechanisms operational without changing the rule that
memory is advisory and cannot authorize physical action.

## 2. Current Implementation

### 2.1 Consolidation

`FireClawConsolidationEngine.consolidate_mission()`:

- reads every non-derived event in one mission/runtime;
- groups events by `(robot_id, subtask_id)`;
- creates deterministic jobs from exact source event tuples;
- repairs partial Episode, Gist, and relation output on retry.

Exact-tuple idempotence prevents duplication for an identical invocation. It
does not prevent this sequence:

```text
run 1 sources = [A, B]
new event       = C
run 2 sources = [A, B, C]
```

Both derived ranges remain immutable and overlap.

`ConsolidationJobStore` fsyncs JSONL transitions and uses an in-process
`RLock`. It has no durable source watermark, runtime trigger, startup recovery
loop, or cross-process exclusion.

### 2.2 Working Memory

`EmbodiedWorkingMemory.hydrate()` exists, but normal runtime assembly constructs
an empty projection and never calls it. The projection is correctly
write-through: an event is added only after authoritative persistence.

### 2.3 Replication

Replication already provides:

- absolute source cursor and bounded batches;
- mission/runtime checks;
- canonical record SHA-256 checksums;
- duplicate, collision, pending-relation, and conflict handling;
- destination checkpoints scoped by source Store, mission, and runtime.

The robot Gateway currently accepts caller-supplied scope headers after a
single optional bearer-token check. The exporter filters only mission and
runtime, so restricted records may be included. A declared
`source_robot_id` is not a cryptographic identity.

## 3. Analogue Review

The local eMEM implementation was inspected for episode-boundary and periodic
consolidation. FireClaw reuses the useful trigger concept, but does not reuse
eMEM's mutable active episode, unpersisted safety-observation buffer, implicit
coordinate assumptions, or mutable retention tiers.

The repository does not currently contain `openclaw/`, so the requested
OpenClaw analogue inspection cannot be performed for this design. The design
therefore preserves FireClaw's existing append-only Store, runtime assembly,
Gateway, and reconciliation boundaries rather than inventing a replacement
session or transport layer.

## 4. Goals

1. Consolidate only closed, durable source ranges.
2. Guarantee that completed runtime Episode source sets do not overlap.
3. Trigger consolidation after persisted terminal subtask/mission transitions.
4. Recover queued or interrupted consolidation after restart.
5. Prevent concurrent processes from processing the same evidence Store.
6. Keep consolidation failure non-blocking for robot mission execution.
7. Hydrate a bounded, fresh, mission/runtime-isolated working set at startup.
8. Authenticate replication peers and signed batches.
9. Apply sensitivity policy before records leave a robot Store and again before
   destination admission.
10. Fail closed for insecure real-runtime replication while retaining explicit
    simulation/test support.
11. Preserve existing evidence IDs, provenance, current-state revalidation, and
    append-only audit behavior.

## 5. Non-Goals

- independent dense retrieval or RRF;
- a semantic memory Agent tool;
- ANN/HNSW;
- completing remaining R*Tree facade paths;
- new Entity resolution heuristics;
- automatic Lesson promotion;
- deleting or demoting raw evidence;
- treating Gists as sensor facts;
- replacing the existing Gateway transport;
- implementing a PKI or certificate-issuance service;
- background replication scheduling beyond the existing explicit sync API.

## 6. Selected Architecture

### 6.1 Closed Consolidation Boundaries

Add a durable `ConsolidationBoundary`:

```python
@dataclass(frozen=True)
class ConsolidationBoundary:
    boundary_id: str
    mission_id: str
    runtime_mode: str
    robot_id: str | None
    subtask_id: str | None
    after_sequence: int
    through_sequence: int
    terminal_event_id: str
    trigger_reason: str
```

Sequence values are absolute record ordinals in the append-only evidence
Store. They are selection boundaries, not timestamps. A delayed replicated
event is appended at a later sequence and therefore cannot be inserted into a
completed source range.

The scope key is:

```text
(mission_id, runtime_mode, robot_id, subtask_id)
```

For each key, the coordinator stores the largest successfully covered
`through_sequence`. The next boundary starts strictly after that watermark.

`source_event_ids` inside completed Episodes must be disjoint. Cross-robot
`supporting_event_ids` may appear in more than one Gist revision because they
are corroborating evidence, not Episode ownership.

### 6.2 Boundary Eligibility

A boundary is closed only after:

1. the mission/subtask state transition is persisted;
2. a corresponding terminal `outcome` event is persisted in embodied memory;
3. its event ID and current authority sequence are recorded in the
   consolidation request journal.

Terminal subtask statuses reuse `TERMINAL_SUBTASK_STATUSES`. A mission boundary
is terminal only when the mission registry derives `succeeded`, `failed`, or
`cancelled`.

An accepted dispatch, `cancel_requested`, running trace, timeout warning, or
current end-of-file is not a terminal boundary.

Eligible source records:

- belong to the exact mission/runtime and boundary group;
- have authority sequences in `(after_sequence, through_sequence]`;
- are not `episode`, `gist`, or `lesson`;
- are not relations;
- still pass `EmbodiedMemoryEvent` validation.

An eligible closed range with fewer than `min_events_per_episode` is recorded
as `covered_without_episode`. Its watermark still advances so recovery does not
loop forever.

### 6.3 Consolidation State And Lease

Create a separate append-only state journal next to the evidence Store:

```text
<evidence>.consolidation-state.jsonl
<evidence>.consolidation.lock
```

The journal records:

- queued boundary;
- running attempt;
- completed boundary and new watermark;
- covered-without-Episode boundary;
- failed attempt with stable error code and exception class;
- recovery attempt.

It never stores raw event payloads or exception messages.

Cross-process exclusion uses Linux `fcntl.flock()` on the lock file. FireClaw's
ROS deployment target is Linux. The lock is held only while selecting and
processing a bounded boundary. Kernel release on process exit provides stale
lease recovery. Lock contention leaves the request queued; it does not block
mission execution.

The existing `ConsolidationJobStore` remains the per-artifact checkpoint
journal. The new state journal owns range coverage and runtime scheduling.

### 6.4 Engine API

Add an explicit closed-range entry point:

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

The coordinator selects exact authoritative source IDs. The engine validates
that every ID exists, belongs to the requested mission/runtime, is eligible,
and is unique before grouping/chunking.

`consolidate_mission()` remains available for offline/manual compatibility but
is not used by normal runtime assembly. Its documentation must state that it
does not provide runtime watermark guarantees.

### 6.5 Coordinator Lifecycle

`MemoryConsolidationCoordinator` exposes:

```python
request_terminal_boundary(...)
run_pending_once(max_boundaries=...)
recover()
start()
stop()
status()
```

`request_terminal_boundary()` only persists a bounded request and wakes the
worker. It does not summarize on the mission execution thread.

The worker:

1. acquires the file lease;
2. reloads state from authority;
3. selects the next queued boundary;
4. recomputes exact eligible source IDs;
5. calls `consolidate_events()`;
6. verifies derived artifacts;
7. commits the watermark;
8. releases the lease.

Normal Mission Gateway start/stop owns the worker lifecycle. Direct CLI use may
enqueue work without waiting; the next runtime start recovers it. Tests use
`run_pending_once()` without threads.

### 6.6 Terminal Event Integration

`MissionAgent` receives an optional coordinator.

Create one helper that:

1. receives an already persisted registry transition;
2. appends a terminal `outcome` event with stable status, robot, subtask, and
   source transition metadata;
3. requests a boundary only if that append succeeded.

Use the helper for:

- an immediately terminal submit result;
- a terminal status discovered by `mission_trace()`;
- terminal cancellation results;
- the final derived mission status.

Repeated observation of the same terminal transition generates the same
boundary identity and is idempotent. Mission execution remains successful when
request journaling or consolidation fails; a content-free warning/status is
reported.

### 6.7 Startup Working-Memory Hydration

Add a deterministic selection method rather than hydrating every record:

```python
hydrate_recent(
    events,
    *,
    mission_ids: frozenset[str],
    runtime_mode: str,
    reference_at: str,
) -> WorkingMemoryHydrationReport
```

Selection rules:

- only missions currently `created` or `running`;
- exact configured runtime;
- only events fresh under existing per-type freshness rules;
- future timestamps beyond allowed skew are excluded;
- deterministic ordering by `(observed_at, event_id)`;
- total admitted bytes and events remain bounded by existing working-memory
  limits;
- multiple active missions receive a deterministic fair quota before remaining
  capacity is filled by recency.

Hydration calls `add_persisted()` and never writes evidence or indexes.
Restricted events may be present in the local projection but remain excluded
from snapshots unless the caller has the existing restricted-read permission.

Runtime assembly constructs the mission registry once, obtains active mission
IDs through a new public `list_missions()` API, hydrates after Store creation,
and then injects the same registry and working memory into the Agent/facade.

Hydration failure degrades to an empty projection and emits content-free
diagnostics. It does not prevent the Gateway from starting.

### 6.8 Replication Policy

Add explicit policy types:

```python
@dataclass(frozen=True)
class ReplicationPeerPolicy:
    policy_id: str
    peer_id: str
    allowed_robot_ids: frozenset[str]
    allowed_runtime_modes: frozenset[str]
    allowed_sensitivities: frozenset[str]
    require_https_for_real: bool = True
```

The request must name exactly one `mission_id` and `runtime_mode`. The server
binds `peer_id` from verified credentials, not from a caller-controlled scope
header.

Exporter admission:

- mission and runtime match the request;
- source robot is allowed by policy;
- event sensitivity is explicitly allowed;
- relations are exported only when both endpoint events are exportable under
  the same policy;
- cursor advances over omitted records;
- response reports only bounded omission counts by stable reason code.

The reconciler repeats runtime, mission, robot, and sensitivity admission before
append. Restricted evidence is never accepted merely because it arrived in a
validly signed batch.

### 6.9 Replication Authentication

Use an injectable standard-library HMAC-SHA256 baseline:

```python
class ReplicationKeyProvider(Protocol):
    def signing_key(self, key_id: str) -> bytes: ...
    def verification_key(self, peer_id: str, key_id: str) -> bytes: ...
```

Secrets come from deployment configuration or environment-backed files and
never enter memory records, logs, CLI output, or committed examples.

Requests and batches contain:

- protocol version;
- peer/source identity;
- key ID;
- issued-at timestamp;
- nonce;
- canonical mission/runtime/cursor/limit scope;
- SHA-256 body or batch digest;
- HMAC signature.

Verification uses `hmac.compare_digest()`, bounded clock skew, and a bounded
nonce replay cache. Destination cursor checks remain the durable replay and
ordering guard.

HMAC proves possession of a configured pairwise secret; it is not a substitute
for transport encryption or third-party-verifiable signatures. For
`runtime_mode="real"`:

- the client refuses non-HTTPS robot endpoints unless an explicit
  test/simulation-only override is enabled;
- missing key or peer policy fails closed;
- unsigned legacy batches are rejected.

Simulation tests may explicitly allow HTTP and unsigned fixtures. There is no
silent insecure fallback.

### 6.10 Protocol Compatibility

Bump the replication batch protocol to v2 while keeping record envelopes and
record IDs stable.

- v2 carries policy/auth metadata and omission diagnostics.
- v1 parsing remains available only when the reconciler is configured with
  `allow_legacy_unsigned=True`.
- normal real-runtime assembly never enables that flag.

## 7. Failure Semantics

| Failure | Required behavior |
|---|---|
| terminal event persistence fails | do not queue consolidation |
| request journal append fails | mission continues; warning and later manual recovery |
| lease busy | leave request queued |
| consolidation crashes after artifact append | retry verifies/repairs same deterministic job |
| watermark append fails | retry exact boundary; no new source set |
| hydration fails | start with empty working projection |
| replication signature invalid | reject whole batch before record parsing |
| policy omits an event | advance source cursor and report content-free omission |
| relation endpoint omitted | omit relation; never create placeholder |
| real-runtime transport is HTTP | refuse sync/export |
| peer key missing or unknown | fail closed |

## 8. Observability

Expose content-free status fields:

- pending/running/failed consolidation boundary counts;
- latest watermark per scope;
- last successful consolidation time;
- startup hydration selected/added/oversized/stale counts;
- replication authenticated peer and policy IDs;
- exported/imported/omitted/rejected/conflict counts;
- stable failure codes and exception class.

Never log event payloads, HMAC material, bearer tokens, restricted metadata, or
raw exception messages that may contain evidence content.

## 9. Testing Contract

Focused tests must cover:

- disjoint source sets across successive boundaries;
- delayed replicated evidence enters only a later boundary;
- exact retry after crash and watermark-write failure;
- cross-process lease contention;
- terminal/non-terminal trigger distinctions;
- startup recovery of queued/running work;
- hydration mission/runtime/freshness isolation and fair capacity;
- hydration performs no authority writes;
- restricted export omission and relation endpoint closure;
- request and batch signature tamper rejection;
- wrong robot/store/key/mission/runtime rejection;
- cursor replay rejection;
- real-runtime HTTP and unsigned-batch rejection;
- explicit simulation legacy compatibility;
- mission execution and Gateway startup remain available when maintenance is
  degraded.

## 10. Research Impact

### Engineering Correctness

This P0 closes runtime correctness and deployment-security gaps. It makes
Episode/Gist generation reproducible, restores bounded short-term context after
restart, and prevents unsigned or over-broad evidence replication.

### Research Validity

Watermarks, hydration, HMAC, and export policy are infrastructure rather than a
new embodied-memory method. They are necessary controls for later experiments:
without them, retrieval comparisons are confounded by duplicate summaries,
empty post-restart context, or untrusted cross-robot evidence.

### Publication-Level Contribution

This P0 alone is not a publication-level novelty claim. It enables defensible
evaluation of later contributions such as uncertainty-aware retrieval,
spatial-interoceptive degradation prediction, and conservative cross-robot
evidence reconciliation.

