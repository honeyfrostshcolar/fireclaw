# Memory Operational Correctness P0 Planning Record

## 2026-07-24 17:48 +0800

### Task Goal

Create a Superpowers-style design and implementation plan for the remaining P0
memory work identified by the post-eMEM audit:

1. operational, non-overlapping consolidation;
2. startup working-memory hydration;
3. authenticated, sensitivity-aware replication before real multi-robot use.

This turn is planning-only. No production code was modified.

### User Decisions And Constraints

- The user asked to start P0 planning using the Superpowers process.
- Do not create a Python 3.11 environment or install pytest.
- Future implementation should use the existing Python 3.10 environment and
  dependency-free pytest-compatible focused tests.
- Do not commit unless explicitly requested.
- Preserve the already completed Planner memory retrieval isolation.

### Files And Symbols Inspected

- `docs/superpowers/specs/2026-07-24-planner-memory-retrieval-isolation-design.md`
- `docs/superpowers/plans/2026-07-24-planner-memory-retrieval-isolation.md`
- `docs/superpowers/specs/2026-07-14-fireclaw-embodied-memory-v1-design.md`
- `docs/architecture/embodied-memory-event-production.md`
- `src/fireclaw_core/memory/consolidation.py`
  - `FireClawConsolidationEngine.consolidate_mission`
  - `_consolidate_chunk_locked`
- `src/fireclaw_core/memory/consolidation_jobs.py`
  - `ConsolidationJob`
  - `ConsolidationJobStore`
- `src/fireclaw_core/memory/working_memory.py`
  - `WorkingMemoryConfig`
  - `EmbodiedWorkingMemory.hydrate`
  - `EmbodiedWorkingMemory.snapshot`
- `src/fireclaw_core/memory/reconciliation.py`
  - `ReplicationEnvelope`
  - `ReplicationBatch`
  - `EmbodiedMemoryReplicationExporter.export_batch`
  - `EmbodiedMemoryReconciler.ingest_batch`
- `src/fireclaw_core/mission/mission_registry.py`
  - `TERMINAL_SUBTASK_STATUSES`
  - `mission_trace`
  - `_mission_status`
- `src/fireclaw_core/mission/mission_agent.py`
  - `_record_embodied_memory`
  - `submit_subtask`
  - `mission_trace`
  - `cancel_mission`
  - terminal status extraction
- `src/fireclaw_core/mission/mission_runtime.py`
  - `MissionRuntimePaths`
  - `build_mission_agent_from_paths`
- `src/fireclaw_core/mission/mission_gateway.py`
  - `sync_robot_memory`
  - Gateway lifecycle
- `src/fireclaw_core/gateway/gateway.py`
  - `GET /memory/replication`
  - bearer/scope authorization
- `src/fireclaw_core/gateway/method_scopes.py`
- `src/fireclaw_core/subagent/subagent_client.py`
  - `get_memory_replication`
- eMEM consolidation tests and episode/time-window trigger behavior through
  CodeGraph.

The local `openclaw-main/` reference is absent, so no OpenClaw analogue could
be inspected for this plan. This limitation is recorded in the design.

### Commands Executed

```text
rg --files docs/superpowers
rg --files .agents .codex
git status --short
CodeGraph exploration of consolidation, hydration, MissionAgent terminal
transitions, runtime assembly, replication, Gateway endpoints, and tests
rg/sed reads for exact documentation and already-located call-site details
git diff --check -- <new design> <new plan>
wc -l <new design> <new plan>
rg -n '^## |^### Task|^- \[ \]' <new design> <new plan>
```

### Current Findings

- Consolidation artifact jobs are already deterministic and resumable for an
  exact source tuple.
- Whole-mission rescans can still create overlapping immutable source ranges
  after adjacent evidence arrives.
- There is no runtime coordinator, durable range watermark, startup recovery
  loop, or cross-process lease.
- Mission registry terminal transitions discovered by `mission_trace()` are
  persisted in the registry but are not currently mirrored as terminal
  embodied-memory events.
- Working-memory hydration exists but is never called by normal runtime
  assembly.
- Replication checks declared IDs, checksums, cursor order, and collisions, but
  the declared robot/Store identity is not cryptographically authenticated.
- Export filtering currently covers mission/runtime only, not sensitivity or
  relation endpoint visibility.
- The current Gateway bearer token and caller-supplied scope header are not a
  sufficient robot/peer identity boundary.

### Selected Design

#### Consolidation

- Use absolute append-only authority sequence boundaries, not timestamps.
- Persist a watermark per
  `(mission_id, runtime_mode, robot_id, subtask_id)`.
- Queue a boundary only after the registry transition and terminal embodied
  `outcome` are both persisted.
- Select exact event IDs inside `(after_sequence, through_sequence]`.
- Keep Episode source sets disjoint; allow repeated cross-robot supporting
  evidence only as explicit Gist revision provenance.
- Advance sparse closed ranges as `covered_without_episode`.
- Use a JSONL state journal plus Linux `fcntl.flock`.
- Keep mission execution non-blocking; a worker performs consolidation.

#### Hydration

- Add public mission listing with derived status.
- Hydrate only fresh events for active missions and the exact runtime.
- Use deterministic fair capacity across active missions.
- Perform no authority writes and fail open to an empty projection.

#### Replication

- Require an exact mission/runtime request and a server-bound peer policy.
- Filter sensitivity before export and omit relations with hidden endpoints.
- Sign requests and v2 batches with injectable HMAC-SHA256 keys.
- Verify whole-batch authentication before parsing/importing records.
- Recheck sensitivity at destination.
- Reject HTTP, missing keys/policies, and unsigned batches in real runtime.
- Allow insecure/legacy simulation behavior only through an explicit switch.
- HTTPS provides confidentiality; HMAC is pairwise authentication and is not
  presented as PKI.

### Documents Created

- `docs/superpowers/specs/2026-07-24-memory-operational-correctness-p0-design.md`
- `docs/superpowers/plans/2026-07-24-memory-operational-correctness-p0.md`

The implementation plan contains eight serial tasks:

1. boundary/watermark state;
2. exact closed-source engine entry point;
3. file lease and recovery coordinator;
4. terminal transition/runtime wiring;
5. startup hydration;
6. replication policy and signed batch primitives;
7. Gateway/client authenticated replication wiring;
8. diagnostics, documentation, and verification.

### Verification Result

```text
git diff --check: PASS
design lines: 480
plan lines: 643
total checklist steps: 49
```

No tests were run because this turn created planning documents only.

### Current Conclusion

The first implementation task should be the consolidation boundary/watermark
state model. It establishes the invariant needed by the engine and coordinator.
Hydration is smaller but should remain Task 5 because runtime construction is
already being changed by Task 4. Replication security follows after local
memory correctness to keep shared contracts serial and reviewable.

### Next Recommended Step

Execute Task 1 from the P0 implementation plan using RED/GREEN focused tests.
Do not start Task 2 until boundary identity, replay, and monotonic-watermark
tests pass.
