# FireClaw Embodied Memory Task Queue

## 2026-07-20T14:52:28+08:00

### Task Goal

Resume the eMEM-inspired FireClaw embodied-memory work from the latest
2026-07-19 record and complete the exact spatial read path before considering
R*Tree, hybrid RAG, free-text extraction, or live ROS streams.

### Starting State Reviewed

The latest two memory directories reviewed were `2026-07-19` and
`2026-07-18`. The branch was `fireclaw_memory`, tracking
`origin/fireclaw_memory`, with the full embodied-memory stack still uncommitted.
No staged changes were present. Existing work already covered Entity Memory,
structured entity extraction, robot-to-mission reconciliation, reliable
Episode/Gist consolidation, safety-aware cross-robot assessments, the unified
Mission Memory facade/tools, and mission-scoped archive/deletion/reusable
knowledge lifecycle.

The active gap recorded on 2026-07-19 was exact spatial completion:

- nearest-neighbor lookup for Observation, Gist, and Entity records;
- conservative area intersection for multi-area Gists;
- frame/floor-aware Entity spatial reads;
- bounded, permission-aware Agent exposure;
- a correctness baseline before any R*Tree projection.

### Analogue And Source Review

Inspected the existing FireClaw spatial contracts in:

- `src/fireclaw_core/memory/mission_memory_facade.py`;
- `src/fireclaw_core/memory/mission_memory_tools.py`;
- `src/fireclaw_core/memory/entity_memory.py`;
- `src/fireclaw_core/memory/safety_consolidation.py`;
- `src/fireclaw_core/memory/embodied_memory.py`;
- `src/fireclaw_core/memory/memory_index.py`.

Reviewed the relevant eMEM analogues:

- `emem-main/emem/store.py::spatial_query`;
- `emem-main/emem/store.py::spatial_nearest`;
- `emem-main/emem/store.py::search_gists_by_area`;
- `emem-main/emem/tools.py` spatial, Gist, and Entity tool shapes.

FireClaw retains the high-level facade/tool shape but does not copy eMEM's
unscoped coordinates, center-only Gist area matching, mutable/archive behavior,
or text-formatted results. The repository still has no `.codegraph/`, the
configured `codegraph_*` tools were not exposed, and `openclaw-main/` was not
available, so no new OpenClaw structural comparison was possible in this run.
The user was asked whether `codegraph init -i` should be run later; this did not
block the spatial implementation.

### Exact Spatial Completion Result

Modified `src/fireclaw_core/memory/mission_memory_facade.py`:

- introduced one conservative distance model shared by event poses, Entity
  current poses, and Gist `spatial_geometries`;
- area intersection and nearest ranking use
  `distance_to_uncertainty_m`, while retaining `distance_m` and
  `center_distance_m` so callers can distinguish a region boundary from its
  representative center;
- exact `frame_id` and optional floor matching prevent implicit cross-frame or
  cross-floor coordinate comparison;
- 3D queries fail closed for records without vertical position;
- Gist queries now accept `near_x`, `near_y`, optional `near_z`, and bounded
  `radius_m`, including multi-area conservative geometry matching;
- Entity queries now support floor, optional 3D position, bounded radius,
  conservative intersection, and distance ordering rather than recency ordering
  for spatial calls;
- added unified `query_nearest` over Observation, Gist, and Entity records, with
  bounded distance/result count and optional Entity kind/status filters;
- exact restricted-record/entity omission counts are reported without revealing
  hidden contents;
- every result retains evidence IDs, freshness/provenance, advisory-only state,
  and current-state revalidation requirements.

Modified `src/fireclaw_core/memory/mission_memory_tools.py`:

- added the tenth Agent tool, `query_mission_memory_nearest`;
- exposed bounded `memory_types`, `entity_kinds`, and `entity_statuses` arrays;
- extended Gist and Entity schemas with their exact spatial parameters;
- made schema radius maxima follow `MissionMemoryFacadeConfig` rather than a
  disconnected literal;
- kept mission ID, runtime mode, requester identity, and permission scopes
  server-bound and absent from LLM arguments.

Modified `src/fireclaw_core/memory/safety_consolidation.py`:

- Gist vertical bounds now include each source pose's uncertainty radius;
- records explicitly mark that vertical uncertainty was applied;
- the existing horizontal conservative circle remains unchanged.

Updated `docs/architecture/embodied-memory-event-production.md` with the tenth
tool, exact distance semantics, multi-geometry behavior, 3D fail-closed rules,
eMEM analogue review, deterministic linear-scan baseline, and R*Tree equivalence
requirements.

### Safety And Research Conclusion

Engineering correctness: the structured spatial path is now functionally
complete at the facade/tool level without changing the append-only JSONL
authority. It remains a deterministic scan over mission-scoped records and
projected Entities, which is appropriate for correctness before index scaling.

Research validity: exact uncertainty-aware retrieval is necessary safety
infrastructure, but by itself is not a publication-level novelty. A stronger
research claim would require calibrated localization uncertainty, retrieval
accuracy/latency evaluation under smoke and sensor degradation, comparisons
against point-only and R*Tree baselines, and evidence that conservative
retrieval reduces unsafe planning decisions without unacceptable false-positive
load.

Publication-level contribution: this step supports auditable embodied memory
and safety evaluation, but should be framed as enabling infrastructure rather
than the paper's central innovation unless paired with a novel uncertainty-aware
memory/planning method and convincing robot or simulator experiments.

### Validation

Commands run successfully:

- `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q src/fireclaw_core/memory src/fireclaw_core/mission src/fireclaw_core/gateway`;
- import/signature/schema assertions reporting 10 unique mission memory tools,
  spatial types `entity,gist,observation`, configurable result limit, and
  configurable maximum spatial radius;
- `git diff --check`.

Pytest and runtime/behavioral tests were not run because the user's latest
record explicitly deferred them while implementation tasks are being completed.

### Current Conclusion

The exact spatial feature-completion item is implemented. The next active
eMEM-inspired gap is scalable Entity projection plus conservative cross-robot
identity resolution. That work must prioritize avoiding false victim/hazard
merges, preserve ambiguous identity instead of forcing a match, and keep exact
robot/source evidence lineage.

### Next Recommended Step

1. Define an indexed Entity projection boundary without changing append-only
   mention/resolution authority.
2. Add conservative cross-robot identity proposals using tracker namespace,
   mission-frame geometry, time compatibility, type, and evidence independence.
3. Never auto-merge victims or hazards from name/proximity alone; represent
   ambiguous candidate sets and require operator confirmation for consequential
   merges.
4. After implementation tasks are complete, run the deferred focused behavior
   tests for spatial ordering, uncertainty intersection, restricted omissions,
   lifecycle fail-closed behavior, and cross-frame/floor isolation.

## 2026-07-20T15:18:00+08:00

### Entity Projection And Identity Resolution Update

Implemented the next queue item without changing the append-only JSONL
authority:

- `MissionMemoryStore.snapshot_token()` provides an O(1) file-authority token
  for the no-index fallback.
- `SqliteMemoryIndex` now stores `entity_projection_meta` and
  `entity_projection` tables, with mission/runtime/filter/spatial indexes and
  atomic replacement. The source token is `entity-events-v2:<count>:<max_rowid>`
  over indexed `entity_mention` and `entity_resolution` records.
- `EntityMemoryService` first checks an in-process tokened cache, then the
  rebuildable SQLite projection, and only scans authority events on a cache
  miss or malformed projection. A schema-token bump invalidates prior
  projections after the new fields were introduced.
- Historical mentions recover `robot_id`, mission-frame scope, and tracker
  identity from the source Observation. Exact tracker attachment now uses
  `<robot_id>:<namespace>:<track_id>`; an ownerless tracker cannot attach
  exactly, preventing cross-robot collisions.
- Entity projections retain source robot IDs, frame scopes, robot-namespaced
  tracking identities, and operator-rejected merge partners.
- `list_identity_proposals()` performs a deterministic, bounded pair scan over
  same-kind/same-frame/same-floor entities. It requires mission-frame
  assertions, disjoint robot evidence, shared normalized alias, compatible
  time, and uncertainty-aware spatial overlap. When both sides expose tracker
  namespaces they must agree; the report includes shared namespaces or an
  explicit missing-side reason. One-sided vertical coordinates are rejected.
  It emits deterministic proposal IDs, exact evidence IDs,
  `candidate`/`ambiguous` status, truncation metadata, and
  `automatic_merge=false`/operator-confirmation flags.
- `MissionMemoryFacade.query_entity_identity_proposals()` applies restricted
  entity filtering and exposes an advisory report. The new read-only tool is
  `query_mission_entity_identity_proposals`, bringing the mission tool count to
  eleven.
- Architecture documentation now records the projection boundary and the
  conservative cross-robot proposal contract.

### Validation In This Update

Passed:

- `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q src/fireclaw_core/memory src/fireclaw_core/mission src/fireclaw_core/gateway`;
- temporary-store behavior check: two robots using the same local tracker key
  remain separate, produce one bounded cross-robot candidate, and a new service
  instance reloads the persisted projection;
- schema/import check: eleven unique mission memory tools, including the new
  identity proposal tool;
- `git diff --check`.

Pytest remains intentionally deferred according to the prior user decision.

During the static review a malformed indentation in the projection
deserializer was found and fixed before handoff. The persistence check was
rerun after the fix and confirmed that a fresh service reloads the same entity
ID and robot-namespaced tracker identity from SQLite.

### Remaining Work

1. Inspect the full diff for API compatibility and add focused static checks for
   malformed projection payloads, restricted proposal omission, frame-scope
   isolation, rejection handling, pair/evidence caps, and operator resolution
   invalidation.
2. Update the current queue conclusion after those checks; only then consider
   whether an R*Tree projection is justified by measured volume/latency.

## 2026-07-20T17:38:29+08:00

### Final Static And Boundary Review For This Item

Additional hardening completed:

- cross-robot `record_mention` proposals now use the same conservative contract
  as the report path: mission-frame scope, exact frame/floor, matching vertical
  availability, compatible tracker namespace when present, and uncertainty-
  aware distance;
- an explicit mention `robot_id` must agree with its source Observation owner;
  ownerless tracker keys cannot be used for exact attachment or cross-robot
  proposals;
- contradicted entities are excluded from new identity candidates;
- resolver numeric thresholds reject non-finite values, booleans, and invalid
  pair/proposal caps;
- projection payload deserialization is strict and fails closed on malformed
  identifiers, sequences, attributes, poses, or non-finite confidence values;
- proposal payloads include shared tracker namespaces and explicit weaker-
  evidence reasons when one side lacks a namespace.

### Boundary Validation Results

The following temporary-store checks passed without modifying repository data:

- two robots using the same local tracker key remain separate and yield one
  advisory candidate when all mission-frame evidence agrees;
- different frames, robot-local scope, one-sided `z`, and incompatible tracker
  namespaces produce no candidate;
- pair evaluation caps set `truncated=true` at the configured bound;
- operator `reject_merge` immediately invalidates the candidate after the
  append-only resolution event changes the source token;
- restricted evidence is omitted for ordinary access and visible only with the
  restricted/admin scope, with omission counts but no hidden IDs;
- malformed persisted projections are rejected and rebuilt; a fresh service
  reloads the same Entity and robot-namespaced tracker identity from SQLite;
- mission schema/import checks report eleven unique read-only tools, including
  `query_mission_entity_identity_proposals`.

Commands passed:

- `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q src/fireclaw_core/memory src/fireclaw_core/mission src/fireclaw_core/gateway`;
- `git diff --check`.

Pytest and behavioral test files remain deferred by the user's prior decision;
the temporary boundary script is the active verification for this phase.

### Current Conclusion And Next Step

The scalable Entity projection/cache and conservative cross-robot identity
resolution item is complete at the current deterministic baseline. JSONL remains
the authority, SQLite is rebuildable, no identity is auto-merged, and all
cross-robot claims require operator confirmation plus current-state
revalidation. R*Tree adoption remains measurement-gated. The next queue item is
operational hardening: sensitivity-aware replication, device identity, conflict
inspection, periodic reconciliation, and cross-process consolidation leases.

## 2026-07-20T18:47:00+08:00

### Stabilization Pass Started

Task goal: begin the first-priority stabilization pass for the large
embodied-memory change set before adding new memory capabilities.

User-facing decision: do not continue with R*Tree, embedding/RAG, free-text
entity extraction, or live ROS stream integration until the current memory
stack has focused behavior tests and basic regression checks.

### Work Completed

Added `tests/test_entity_memory.py` with focused pytest coverage for the core
new behavior:

- structured `payload.entities` extraction from an Observation into one
  idempotent `entity_mention`;
- preservation of `observed_in` evidence linkage from the mention back to the
  source Observation;
- robot-namespaced tracker identity such as
  `robot-A:thermal-camera:7`;
- malformed SQLite `entity_projection` payload rejection and rebuild from the
  JSONL authority;
- `query_nearest` conservative spatial behavior for frame/floor isolation,
  uncertainty-boundary inclusion, point-estimate exclusion outside radius, and
  multi-area Gist geometry matching;
- advisory cross-robot identity proposal generation with
  `automatic_merge=false`;
- operator `reject_merge` invalidating the previous identity candidate.

No production code was changed in this stabilization step.

### Validation

Passed:

- `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q src/fireclaw_core/memory src/fireclaw_core/mission src/fireclaw_core/gateway tests/test_entity_memory.py`;
- `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q src tests`;
- temporary standard-library behavior script covering the same Entity Memory,
  projection rebuild, nearest spatial query, and identity proposal/rejection
  paths as the new pytest file;
- `git diff --check`.

Could not run pytest in this environment because `/home/lpp/miniconda3/envs/py310`
and system `python3` do not have `pytest` installed, and no `python3.11` or
`python3.12` executable was available on `PATH`.

### Current Conclusion

The first stabilization increment is now in place as focused pytest coverage,
and the covered behavior passed via an equivalent no-pytest runtime script.
The next stabilization step should run pytest in an environment with the dev
dependency installed, then broaden tests to mission lifecycle/sync and gateway
tool exposure before splitting commits.

## 2026-07-20T19:06:00+08:00

### Diff Split / Commit Organization

Task goal: organize the large embodied-memory diff into understandable commits.

Attempted to stage the first commit with `git add`, but the current execution
environment cannot write to `.git`:

```text
fatal: cannot create '/home/lpp/fireclaw-master/.git/index.lock': read-only file system
```

No files were staged and no commit was created. The working tree remains
unchanged. The following split is the recommended commit order once `.git` is
writable locally.

### Recommended Commit 1

Commit message:

```text
memory: add entity event projection primitives
```

Files:

- `src/fireclaw_core/memory/embodied_memory.py`
- `src/fireclaw_core/memory/memory_index.py`
- `src/fireclaw_core/mission/mission_memory.py`

Purpose:

- register `entity_mention` / `entity_resolution` event types and
  `co_observed_with` relations;
- add producer/evidence-kind permissions for entity resolver and operator
  resolutions;
- expose `EmbodiedMemoryProducer.store`;
- add SQLite entity projection tables/token/load/replace operations;
- add JSONL authority snapshot token, write lock/guard hook, and mission purge
  support needed by projection/lifecycle.

### Recommended Commit 2

Commit message:

```text
memory: add structured entity memory
```

Files:

- `src/fireclaw_core/memory/entity_memory.py`
- `src/fireclaw_core/memory/entity_extraction.py`
- `src/fireclaw_core/memory/entity_tools.py`
- `src/fireclaw_core/memory/robot_memory.py`

Purpose:

- add structured Observation `payload.entities` extraction;
- persist idempotent `entity_mention` events;
- project current `FireClawEntity` state from mention/resolution events;
- namespace tracker identity by robot owner;
- add read-only entity memory tools;
- run extraction after persisted robot Observation records.

### Recommended Commit 3

Commit message:

```text
memory: add mission memory facade and spatial tools
```

Files:

- `src/fireclaw_core/memory/mission_memory_facade.py`
- `src/fireclaw_core/memory/mission_memory_tools.py`
- `src/fireclaw_core/memory/memory_lifecycle.py`
- `tests/test_entity_memory.py`

Purpose:

- add the permission-aware mission memory read facade;
- expose bounded read-only mission memory tools, including
  `query_mission_memory_nearest`,
  `query_mission_entity_identity_proposals`, and
  `query_reusable_firefighting_knowledge`;
- implement exact frame/floor/uncertainty-aware spatial reads over
  Observation/Gist/Entity records;
- add mission archive/delete/reusable-knowledge lifecycle store used by the
  facade envelope;
- add focused pytest coverage for structured extraction, projection rebuild,
  conservative nearest queries, and identity proposal rejection.

### Recommended Commit 4

Commit message:

```text
memory: make consolidation resumable and safety-aware
```

Files:

- `src/fireclaw_core/memory/consolidation.py`
- `src/fireclaw_core/memory/consolidation_jobs.py`
- `src/fireclaw_core/memory/safety_consolidation.py`

Purpose:

- add crash-resumable consolidation job journal;
- add deterministic Gist summarization with evidence layers and conservative
  spatial geometries;
- include cross-robot claim agreement/contradiction assessment in derived
  summaries;
- prevent duplicate or conflicting Episode/Gist artifacts for the same source
  and consolidation profile.

### Recommended Commit 5

Commit message:

```text
gateway: expose mission memory tools and replication
```

Files:

- `src/fireclaw_core/agent/robot_agent.py`
- `src/fireclaw_core/gateway/control.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/gateway/method_scopes.py`
- `src/fireclaw_core/gateway/serve.py`
- `src/fireclaw_core/memory/reconciliation.py`
- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/mission/mission_gateway.py`
- `src/fireclaw_core/mission/mission_gateway_client.py`
- `src/fireclaw_core/mission/mission_runtime.py`
- `src/fireclaw_core/mission/mission_scheduler.py`
- `src/fireclaw_core/subagent/subagent_client.py`

Purpose:

- instantiate Entity Memory, mission memory facade/tools, lifecycle, and
  reconciliation services in gateway/runtime builders;
- expose robot-local `/entity-memory/tools`,
  `/entity-memory/tools/call`, and `/memory/replication`;
- expose mission gateway memory tool, sync, lifecycle, audit, archive/delete,
  and reusable-knowledge endpoints;
- allow robot-local LLM planner one read-only memory-tool round before action
  planning, without mixing memory reads and action calls;
- add memory-related authorization scopes and subagent replication client calls.

### Recommended Commit 6

Commit message:

```text
docs: record embodied memory architecture progress
```

Files:

- `docs/architecture/embodied-memory-event-production.md`
- `memory/2026-07-18/embodied-memory-task-queue.md`
- `memory/2026-07-19/embodied-memory-task-queue.md`
- `memory/2026-07-20/embodied-memory-task-queue.md`

Purpose:

- document Entity Memory, exact spatial retrieval, mission memory lifecycle,
  reusable knowledge, reconciliation, consolidation, and the current research
  interpretation;
- preserve the execution history and validation results for continuation.

### Commands To Run Locally

Run these from `/home/lpp/fireclaw-master` when `.git` is writable:

```bash
git add src/fireclaw_core/memory/embodied_memory.py src/fireclaw_core/memory/memory_index.py src/fireclaw_core/mission/mission_memory.py
git commit -m "memory: add entity event projection primitives"

git add src/fireclaw_core/memory/entity_memory.py src/fireclaw_core/memory/entity_extraction.py src/fireclaw_core/memory/entity_tools.py src/fireclaw_core/memory/robot_memory.py
git commit -m "memory: add structured entity memory"

git add src/fireclaw_core/memory/mission_memory_facade.py src/fireclaw_core/memory/mission_memory_tools.py src/fireclaw_core/memory/memory_lifecycle.py tests/test_entity_memory.py
git commit -m "memory: add mission memory facade and spatial tools"

git add src/fireclaw_core/memory/consolidation.py src/fireclaw_core/memory/consolidation_jobs.py src/fireclaw_core/memory/safety_consolidation.py
git commit -m "memory: make consolidation resumable and safety-aware"

git add src/fireclaw_core/agent/robot_agent.py src/fireclaw_core/gateway/control.py src/fireclaw_core/gateway/gateway.py src/fireclaw_core/gateway/method_scopes.py src/fireclaw_core/gateway/serve.py src/fireclaw_core/memory/reconciliation.py src/fireclaw_core/mission/mission_agent.py src/fireclaw_core/mission/mission_gateway.py src/fireclaw_core/mission/mission_gateway_client.py src/fireclaw_core/mission/mission_runtime.py src/fireclaw_core/mission/mission_scheduler.py src/fireclaw_core/subagent/subagent_client.py
git commit -m "gateway: expose mission memory tools and replication"

git add docs/architecture/embodied-memory-event-production.md memory/2026-07-18/embodied-memory-task-queue.md memory/2026-07-19/embodied-memory-task-queue.md memory/2026-07-20/embodied-memory-task-queue.md
git commit -m "docs: record embodied memory architecture progress"
```

Before pushing, run:

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q src tests
git diff --check
PYTHONPATH=src python -m pytest tests/test_entity_memory.py tests/test_embodied_memory.py tests/test_mission_memory.py
```

The pytest command still requires an environment with `pytest` installed.
