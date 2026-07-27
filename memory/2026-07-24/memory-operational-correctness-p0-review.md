# P0 Operational Correctness Review

## Review timestamp

- 2026-07-24 19:41:03 +08

## Task goal

Review the user's completed P0 implementation against
`memory-operational-correctness-p0-planning.md` and determine whether the
acceptance criteria are satisfied in the real FireClaw runtime, rather than
only in isolated tests.

## Scope inspected

- `src/fireclaw_core/memory/consolidation.py`
- `src/fireclaw_core/memory/consolidation_coordinator.py`
- `src/fireclaw_core/memory/consolidation_state.py`
- `src/fireclaw_core/memory/reconciliation.py`
- `src/fireclaw_core/memory/replication_security.py`
- `src/fireclaw_core/memory/working_memory.py`
- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/mission/mission_gateway.py`
- `src/fireclaw_core/mission/mission_registry.py`
- `src/fireclaw_core/mission/mission_runtime.py`
- `src/fireclaw_core/subagent/subagent_client.py`
- `src/fireclaw_core/gateway/gateway.py`
- The four newly added P0 test files and relevant existing mission/planner tests

## OpenClaw analogue / architecture note

The implementation was reviewed against the existing planning record, which
had already documented the OpenClaw/emem analogues. This pass focused on
FireClaw-specific operational correctness: durable consolidation, startup
hydration, authenticated robot-to-coordinator replication, restricted evidence
filtering, and fail-safe behavior.

## Commands and verification performed

- `python -m compileall` over the relevant source and test modules: passed.
- `git diff --check`: passed before this review record was added.
- A lightweight test-function runner executed all tests in:
  - `tests/test_memory_consolidation_coordinator.py`: 20 passed.
  - `tests/test_working_memory_hydration.py`: 7 passed.
  - `tests/test_memory_replication_security.py`: 8 passed.
  - `tests/test_replication_gateway.py`: 7 passed.
  - Focused P0 total: 42 passed.
- Existing planner-memory-context direct runner: 87 passed.
- A lightweight runner executed:
  - `tests/test_mission_agent.py`: 78 passed.
  - `tests/test_mission_runtime.py`: 5 passed.
- The mission gateway suite could not be fully executed in the current
  sandbox because binding a listening socket raised
  `PermissionError: Operation not permitted`.
- Several test files cited in the implementation record have no `__main__`
  runner. Running them as `python tests/<file>.py` exits successfully without
  executing their test functions; those commands are not valid evidence of
  their claimed test counts.

## Focused reproduction results

```text
runtime_hydrated_count=0
hydrated_ids_capacity_2=['e0','e1'] selected=2
unverified_auth_imported=1
failed_boundary_pending_count=0
```

Additional replication security reproductions:

```text
invalid signature first -> signature_invalid
valid request with the same nonce -> nonce_reused
naive timestamp -> TypeError: can't subtract offset-naive and offset-aware datetimes
```

## Findings

### Critical: replication security is not wired into the real robot path

- `RobotSubagentClient.get_memory_replication()` sends a plain GET and a
  caller-controlled `X-Operator-Scopes` value. It does not use
  `sign_request()`.
- The robot Gateway `GET /memory/replication` endpoint still exports an
  unsigned batch and does not pass a `ReplicationPeerPolicy`.
- `sign_request()`, `verify_signed_request()`, and `sign_batch()` have no
  production callers; their callers are tests.
- `MemoryReconciler.ingest_batch()` only verifies authentication when both
  `key_provider` and `expected_peer_id` are non-null. Passing any non-null
  `ReplicationAuthMetadata` while omitting either dependency bypasses
  verification and imports the batch. The focused reproduction imported one
  event with a bogus digest and signature.
- `MissionGateway` obtains these dependencies with `getattr`, but normal
  runtime assembly never configures them.
- Peer sensitivity policy is applied only by the test fake. The real robot
  export endpoint can still return restricted evidence.
- `tests/test_replication_gateway.py` uses `_FakeSubagentClient`, which performs
  signing and policy filtering itself. It therefore does not validate the real
  client/Gateway path.

Conclusion: acceptance criteria for authenticated replication, fail-closed
behavior, and restricted evidence controls are not satisfied.

### High: startup hydration exists but is never invoked

- `build_mission_runtime()` constructs an empty `EmbodiedWorkingMemory`.
- No runtime assembly path calls `hydrate_recent()`.
- The mission registry is constructed only near the return statement, so it is
  not available to select active missions during hydration.
- A runtime built over an active mission plus a fresh authority event still
  had `hydrated_count=0`.
- `hydrate_recent()` sorts events oldest-first and selects
  `mission_events[:quota]`. Under capacity pressure it retains the oldest
  events and drops the newest; the reproduction selected `e0,e1` and dropped
  `e2`.

Conclusion: startup reconstruction and recency behavior do not satisfy the
planned contract.

### High: failed consolidation boundaries are not recoverable

- Coordinator failures transition a boundary to `failed`.
- `pending_boundaries()` returns only `queued` and `running`.
- `recover()` only processes `pending_boundaries()`, so failed work is never
  requeued or retried after restart.
- The focused reproduction returned zero pending boundaries after failure.
- Failure metadata records `Exception` / `unknown`, not the actual caught
  exception type.
- The test named `test_failure_before_watermark_retries_same_source_set` does
  not inject a failure; it tests a successful run followed by an idempotent
  duplicate request.

Conclusion: the durability requirement that failed work remains recoverable is
not satisfied.

### High: terminal consolidation integration covers only trace polling

- `_record_terminal_outcome()` is called only when `mission_trace()` observes
  a terminal task.
- There is no equivalent trigger for an immediately terminal submit result,
  terminal cancellation, or final mission terminal status.
- No mission-level boundary is requested, so command, plan, mission outcome,
  and declarations without a task ID are not consolidated.
- The stored event ID is random. The deterministic terminal ID appears only in
  payload, so concurrent repeated trace transitions can append duplicate
  terminal evidence.
- A future `(robot_id=None, subtask_id=None)` mission boundary would currently
  act as a wildcard in source selection and could overlap already consolidated
  subtask evidence.

Conclusion: the terminal trigger and scope contract is incomplete.

### Medium: coordinator concurrency and lifecycle invariants are incomplete

- Boundary watermark calculation and duplicate scanning happen outside the
  cross-process lease. Concurrent requests for one scope can enqueue
  overlapping ranges.
- Request journal appends are protected by an in-process lock only.
- Enqueuing a boundary does not wake the background worker, so work may wait
  for the polling interval.
- `stop()` clears its thread reference even when the join times out, allowing a
  later `start()` to create a second worker.
- `MissionGateway.stop()` stops the coordinator before closing HTTP intake.
- A source set large enough overall can split into groups/chunks smaller than
  the episode minimum. No artifact is created, but the coordinator still marks
  the boundary completed instead of explicitly covered-without-episode.

### Medium: replication verification has denial-of-service edge cases

- A nonce is inserted into the replay cache before digest, key, and signature
  verification. An invalid message can reserve a chosen nonce and reject the
  later valid message as replayed.
- A naive ISO timestamp raises an uncaught `TypeError` during aware/naive
  subtraction instead of producing a fail-closed validation result.
- Nonces are deterministically derived from peer, mission, and timestamp and
  should instead use a cryptographically random value.
- Peer policy construction checks for non-empty sets but does not validate
  values against the supported runtime and sensitivity constants.

## Engineering correctness conclusion

The focused new tests pass, compilation passes, and the patch is structurally
coherent, but P0 cannot be accepted as complete. The isolated tests substitute
fake integration behavior and miss several real runtime paths. The most
important acceptance claims in the previous implementation record are
therefore overstated.

## Research validity impact

These are not presentation-only defects. They weaken any claim that FireClaw
provides auditable, durable, policy-controlled embodied memory:

- unauthenticated or over-broad replication invalidates provenance and safety
  claims;
- missing hydration means a restarted agent does not reconstruct operational
  context;
- unrecoverable failed boundaries break durability;
- incomplete terminal coverage biases which embodied experiences become
  long-term memory.

Until these paths are fixed and tested through real client/server/runtime
assembly, they should not be presented as experimentally validated mechanisms.

## Recommended repair order

1. Wire request signing, response signing, peer policy, key provider, and
   expected peer identity through the real robot Gateway, subagent client, and
   mission runtime; make every partially configured auth state fail closed.
2. Invoke startup hydration from runtime construction and select newest
   capacity-bounded evidence fairly across active missions.
3. Define failed-boundary retry/requeue semantics, preserve actual error
   diagnostics, and add a genuine injected-failure restart test.
4. Cover all terminal paths and define non-overlapping mission-level versus
   subtask-level consolidation scopes with deterministic evidence IDs.
5. Close the coordinator concurrency/lifecycle gaps and harden nonce/timestamp
   verification.
6. Replace fake-only gateway coverage with tests that exercise the actual
   `RobotSubagentClient` and robot Gateway handler.

## User preference and current decision

- The user asked for inspection only after completing the implementation.
- No production files were modified during this review.
- The user previously chose to continue development without creating a Python
  3.11+ environment or requiring a formal full-suite run.

