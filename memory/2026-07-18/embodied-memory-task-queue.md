# FireClaw Embodied Memory Task Queue

## 2026-07-18T00:00:00+08:00

### Task Goal

Resume the unfinished embodied-memory work from the 2026-07-16 and 2026-07-17
records and complete the tasks sequentially. The user explicitly deferred all
pytest/environment work; Python 3.10 is available at
`/home/lpp/miniconda3/envs/py310/bin/python3.10` when needed.

### Environment Decision

- Do not create a Python 3.11 environment in this phase.
- Do not install or run pytest in this phase.
- The workspace is not a usable Git repository.
- `openclaw/`, `.codegraph/`, and exposed CodeGraph tools are unavailable,
  so the required OpenClaw analogue review remains blocked and is documented as
  a deviation rather than silently skipped.

### Ordered Task Queue

1. Event-production contract: completed in this update.
2. Wire `MissionAgent`: command, plan, subtask, outcome, correction.
3. Wire safety and approval decisions.
4. Wire skill invocation lifecycle.
5. Wire robot/sensor observations and body state.
6. Generate relations across command -> plan -> subtask -> skill -> observation
   -> safety decision -> outcome.
7. Add Working Memory after live producers exist.
8. Add Episode/Gist/Lesson consolidation with raw-evidence provenance.
9. Add HNSW/R-tree only after event volume and evaluation justify them.
10. Add retrieval and incident-reconstruction evaluation.

### Task 1 Result

Files modified:

- `src/fireclaw_core/memory/embodied_memory.py`
- `docs/architecture/embodied-memory-event-production.md`
- `docs/superpowers/specs/2026-07-14-fireclaw-embodied-memory-v1-design.md`

Implemented:

- typed `MemoryEventProvenance` persisted in embodied metadata schema v2;
- explicit evidence kinds separating operator assertions, cognitive artifacts,
  runtime evidence, sensor evidence, and derived summaries;
- producer-to-event and producer-to-evidence authority matrices;
- `EmbodiedMemoryProductionPolicy` checks before persistence;
- `EmbodiedMemoryProducer` as the mandatory writer for new live integrations;
- sensor confidence/sensor identity requirements;
- method identity and `derived_from` requirements for generated summaries;
- backward compatibility for schema-v1 records without provenance.

### Current Conclusion

Task 1 is implemented. The next task is to wire `MissionAgent` through the new
producer contract. Existing low-level records remain readable and rebuildable.

### Next Recommended Step

Inspect `MissionAgent` command/planning/execution paths, identify stable mission
and runtime-mode inputs, then inject an optional `EmbodiedMemoryProducer` without
breaking the existing `MissionMemoryStore` path.

## 2026-07-18T09:38:56+08:00

### Task 2 Result

Task 2, `MissionAgent` embodied-memory integration, is implemented.

Files modified:

- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/mission/mission_runtime.py`
- `src/fireclaw_core/mission/mission_cli.py`
- `src/fireclaw_core/gateway/serve.py`

Implemented behavior:

- optional policy-enforced `mission_agent` producer with mandatory explicit
  `embodied_runtime_mode`;
- operator command events with opaque operator source identity;
- planner output events marked `cognitive_artifact` with planner class identity;
- subtask events before normal and primitive-fallback dispatch;
- dispatch, scheduler, mission, primitive-fallback, and cancellation outcomes;
- operator correction events;
- `derived_from` links in metadata from command to plan and from subtask/plan to
  outcomes where the local event id is available;
- fallback to legacy `MissionMemoryStore` if a policy-checked embodied write
  fails;
- runtime/CLI opt-in through `embodied_runtime_mode` and
  `--embodied-runtime-mode` without silently choosing real versus simulation.

Static compilation command:

`/home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q src/fireclaw_core/memory src/fireclaw_core/mission src/fireclaw_core/gateway/serve.py`

Result: exit code 0. Pytest was not run per user instruction.

### Next Step

Task 3: record `SafetyGate` decisions and approval request/decision events with
policy inputs and operator provenance.

## 2026-07-18T09:40:40+08:00

### Task 3 Result

Task 3, safety and approval memory integration, is implemented.

Implemented behavior:

- `SafetyGate` accepts an optional `safety_gate` producer and explicit runtime
  mode while preserving all existing call signatures;
- each evaluated allow/block/clarify/require-confirmation decision records its
  reasons, warnings, planning context, operator-confirmed flag, and bounded
  robot/environment state summary;
- `FireClawAgent` supplies mission/subtask context to all normal, structured,
  and confirmation safety evaluations;
- approval requests record `require_confirmation/pending` runtime evidence;
- approve/deny decisions record operator assertions with operator provenance;
- mission runtime builds separate `mission_agent` and `approval_runtime`
  producers over the same evidence/index store.

Static compilation passed under Python 3.10. Pytest was not run per user
instruction.

### Next Step

Task 4: record skill invocation start, progress/attempt, result, timeout,
cancellation, and failure at the common `PlanExecutor` boundary.

## 2026-07-18T09:44:19+08:00

### Task 4 Result

Task 4, skill lifecycle memory integration, is implemented at `PlanExecutor`,
the common execution boundary for built-in, robot-backed, workspace, and
subprocess skills.

Lifecycle mapping:

- `skill.started` -> `start`
- ordinary `skill.attempted` -> `progress`
- cancelled attempt -> `cancellation`
- timed-out attempt -> `timeout`
- `skill.succeeded` -> `result`
- `skill.failed` -> `failure`

All are persisted as `skill_invocation` runtime evidence. Inputs and outputs
pass through the existing secret-redaction helper before persistence.

### Task 5 Result

Task 5, robot/sensor event ingestion, is implemented for the currently exposed
adapter data contract.

Files added/modified:

- added `src/fireclaw_core/memory/robot_memory.py`;
- updated `FireClawAgent` to record each acquired state snapshot;
- updated robot `GatewayConfig`, TOML config loading, CLI flags, and gateway
  producer construction.

Implemented behavior:

- robot body state is `restricted` `runtime_evidence` from `robot_adapter`;
- environment state is a `restricted` observation from `robot_adapter`;
- sensor discovery/health findings are sensor evidence only when both sensor
  identity and numeric confidence are present;
- `record_sensor_observation()` is the typed path for thermal, smoke, vision,
  pose, and other measurement backends, requiring explicit confidence and
  accepting explicit `SpatialMemoryContext` with frame/uncertainty;
- missing sensor confidence or pose is never invented;
- robot gateway activation requires separate embodied JSONL/index paths and an
  explicit `real`, `simulation`, or `replay` runtime mode.

Current source limitation: existing robot adapters expose aggregate state and
sensor-discovery health, but not a common stream of raw thermal/smoke/perception
measurements. The ingestion API is ready; each concrete measurement backend
must call it when such data exists.

### Validation

Command:

`/home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q src/fireclaw_core`

Result: exit code 0. Pytest and runtime behavior tests were intentionally not
run per user instruction. Source inspection also corrected a transient patch
placement error in the emergency-stop constructor before this record was made.

### Remaining Ordered Queue

6. Generate explicit graph relations across command, plan, subtask, skill,
   observation, safety decision, and outcome event ids.
7. Add Working Memory over real event producers.
8. Add Episode/Gist/Lesson consolidation with raw-evidence provenance.
9. Add HNSW/R-tree only after volume/evaluation justifies them.
10. Add retrieval and incident-reconstruction evaluation.

### Next Recommended Step

Introduce an execution-scoped event-chain context that carries parent event ids
across mission dispatch and robot gateway boundaries. Do not infer graph edges
by querying the latest event globally because concurrent robots can interleave.

## 2026-07-18T10:30:00+08:00

### Task 6 Result

Task 6, explicit event graph production, is implemented with eMEM-style
directed relations and FireClaw safety-evidence adaptations.

Implemented:

- `EmbodiedMemoryProducer.add_relation()` writes policy-attributed relations
  while preserving the store's same-mission/same-runtime endpoint checks;
- MissionAgent creates `plan caused_by command`, `subtask subtask_of plan`, and
  `outcome caused_by subtask/plan` edges, including primitive fallback paths;
- `MemoryLineage` carries command/plan/subtask event IDs as a typed structured
  task field across the mission-control/robot-gateway boundary;
- robot body, environment, and qualified sensor events create `supports` edges
  to the exact SafetyGate decision they informed;
- the accepted safety-decision event ID is passed explicitly into PlanExecutor;
  each later skill lifecycle event creates a `follows` edge to the prior event;
- no relation is inferred from a globally latest record, so concurrent robot
  tasks cannot accidentally cross-link;
- cross-store event IDs remain external lineage only. FireClaw does not create
  dangling relations when mission and robot gateways use separate stores.

eMEM analogue inspected from the local `emem-main` reference:

- `emem-main/emem/types.py`: `EdgeType`, `Edge`, `Observation`, `Episode`, `Gist`;
- `emem-main/emem/store.py`: directed edge persistence and source/target/type
  indexing.

FireClaw adaptation: eMEM's directed graph skeleton is retained, while robot
state and sensor observations explicitly support auditable safety decisions and
real/simulation/replay isolation remains mandatory.

Files modified for this task include:

- `src/fireclaw_core/memory/embodied_memory.py`
- `src/fireclaw_core/task/task_contract.py`
- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/mission/mission_scheduler.py`
- `src/fireclaw_core/memory/robot_memory.py`
- `src/fireclaw_core/safety/safety.py`
- `src/fireclaw_core/execution/executor.py`
- `src/fireclaw_core/agent/agent.py`
- embodied-memory architecture/design documentation.

Static compilation passed with Python 3.10. Pytest was not run per user
instruction.

### Remaining Ordered Queue

7. Add Working Memory over the live event producers.
8. Add Episode/Gist/Lesson consolidation with raw-evidence provenance.
9. Add HNSW/R-tree only after volume/evaluation justifies them.
10. Add retrieval and incident-reconstruction evaluation.

### Next Recommended Step

Implement task-scoped Working Memory as a bounded, non-authoritative projection
over current mission, safety, observation, and skill events. It must not become
a second evidence store or allow simulation state to enter real-robot context.

## 2026-07-18T10:50:00+08:00

### Task 7 Result

Task 7, FireClaw Working Memory, is implemented.

eMEM analogue inspected:

- `emem-main/emem/working_memory.py`: bounded recent deque, active episode,
  position tracking, batch/time flush;
- `emem-main/emem/config.py`: working-memory size and flush thresholds;
- `emem-main/emem/memory.py`: facade integration and consolidation callback.

FireClaw intentionally changes the persistence order. eMEM buffers observations
before batch persistence; FireClaw first appends policy-validated evidence to
JSONL and only then projects it into memory, preventing a crash from erasing a
safety decision or sensor record.

Implemented:

- added `src/fireclaw_core/memory/working_memory.py` with a thread-safe bounded
  deque, event-size cap, event-type freshness limits, and explicit clock-skew
  handling;
- snapshots require `runtime_mode` and `mission_id`, support robot/subtask/event
  filters, and default to excluding restricted and stale records;
- simulation/real/replay events cannot cross snapshot boundaries;
- producer integration occurs only after successful authoritative persistence;
- all MissionAgent/approval and robot safety/skill/adapter/sensor producers in a
  runtime share one projection instance;
- MissionAgent exposes the projection and uses only fresh, non-restricted
  outcome/correction/observation/safety records as bounded planner context;
- SafetyGate still uses directly acquired current state rather than trusting
  cached working memory for physical authorization.

Static compilation passed with Python 3.10. Pytest was not run per user
instruction.

### Remaining Ordered Queue

8. Add Episode/Gist/Lesson consolidation with raw-evidence provenance.
9. Add HNSW/R-tree only after volume/evaluation justifies them.
10. Add retrieval and incident-reconstruction evaluation.

### Next Recommended Step

Implement Episode/Gist/Lesson types and a deterministic consolidation boundary.
Consolidation must preserve all raw evidence, require non-empty `derived_from`,
separate real/simulation/replay, and never summarize away contradictory safety
or operator-correction records.

## 2026-07-18T11:15:00+08:00

### Task 8 Result

Task 8, Episode/Gist/Lesson consolidation, is implemented in
`src/fireclaw_core/memory/consolidation.py`.

eMEM analogue inspected:

- `emem-main/emem/types.py`: `EpisodeNode`, `GistNode`, tiers, and graph edges;
- `emem-main/emem/consolidation.py`: episode/time-window chunking, gist creation,
  spatial clustering, entity extraction, and tier transitions;
- `emem-main/tests/test_consolidation.py`: source counts, summaries, and archival.

FireClaw implementation:

- adds `episode` to the typed event/evidence/producer authority contract;
- groups raw events by `(robot_id, subtask_id)` before temporal chunking so
  concurrent robots are not merged by timestamp alone;
- caps Episode size and temporal gaps;
- creates `belongs_to` and `summarizes` relations to every exact source ID;
- detects an existing Gist by ordered source IDs for idempotent reruns;
- propagates restricted sensitivity and conservative minimum asserted source
  confidence;
- retains each safety-decision status and every operator-correction ID and flags
  contradictory safety statuses;
- never mutates, archives, drops text from, or deletes raw evidence;
- requires at least two Gists for a Lesson and marks all lessons advisory and
  subject to current runtime revalidation.

Static compilation passed with Python 3.10. Pytest was not run per user
instruction.

### Remaining Ordered Queue

9. Decide HNSW/R-tree adoption only from measured event volume and evaluation.
10. Add retrieval and incident-reconstruction evaluation.

### Next Recommended Step

Do not add HNSW/R-tree speculatively. First implement evaluation that measures
retrieval quality, real/simulation contamination, incident-chain completeness,
latency, and index size on the current SQLite exact/cosine/spatial baseline.

## 2026-07-18T11:35:00+08:00

### Tasks 9 And 10 Result

Task 10, embodied retrieval and incident-reconstruction evaluation, is
implemented in `src/fireclaw_core/memory/embodied_memory_eval.py`.

Metrics implemented:

- per-case and mean precision/recall;
- false retrieval rate and forbidden-record reporting;
- explicit runtime-mode contamination count;
- mean and p95 retrieval latency;
- required incident event-type coverage;
- producer provenance coverage;
- command/plan/subtask, observation/safety, safety/skill, and outcome relation
  transition coverage;
- evidence/index artifact sizes.

Task 9 is resolved as a measured adoption gate, not an unconditional dependency
addition. `assess_index_scaling()` returns `evidence_sufficient=false` and does
not recommend HNSW/R*Tree when any volume or p95 measurement is missing. Initial
review thresholds are:

- HNSW: at least 10,000 embedded records and exact-cosine p95 at least 100 ms;
- R*Tree: at least 50,000 spatial records and current spatial-query p95 at least
  100 ms.

These thresholds trigger a backend equivalence/benchmark study, not automatic
deployment. No HNSW/R*Tree dependency was added because the repository does not
yet contain measured event volume demonstrating a need.

Static compilation passed with Python 3.10. Pytest and runtime tests were not
run per user instruction.

### Queue Status

The ten-item queue from 2026-07-17 is now handled:

1. Event production contract: complete.
2. MissionAgent production: complete.
3. Safety/approval production: complete.
4. Skill lifecycle production: complete.
5. Robot/sensor production: complete within the current adapter data contract.
6. Explicit event graph: complete, with cross-store lineage preserved for later
   reconciliation rather than invalid dangling edges.
7. Working Memory: complete.
8. Episode/Gist/Lesson consolidation: complete.
9. HNSW/R*Tree: measured adoption gate complete; backend intentionally not
   adopted without evidence.
10. Retrieval/incident evaluation: complete.

### Remaining Limitations

- Concrete thermal/smoke/vision streaming backends must call the typed sensor
  ingestion API; current adapters mainly expose aggregate state/diagnostics.
- Cross-store lineage reconciliation/aggregation is not yet a service; external
  mission event IDs are carried safely but not materialized as local edges.
- OpenClaw comparison remains unavailable because `openclaw/`, `.codegraph/`,
  and CodeGraph tools are absent. The local eMEM reference was inspected for
  tasks 6-8.
- Behavioral tests remain deferred by explicit user decision.

## 2026-07-18T15:31:37+08:00

### User Scope Decision

The user reviewed the remaining gap against eMEM and explicitly changed the
near-term scope.

Deferred until a later unified RAG phase:

- true dense + lexical hybrid semantic retrieval;
- HNSW candidate retrieval and RRF integration;
- embedding-provider lifecycle and other RAG-specific retrieval work.

Do not reintroduce these items into the current memory-module queue. The user
plans to implement RAG capabilities together later.

Deferred until the remaining memory architecture is complete:

- live thermal, smoke, vision, localization, and other real sensor data-stream
  integration;
- adapter-specific continuous measurement ingestion experiments.

The typed sensor ingestion boundary remains in place, but concrete streams
should not be implemented yet.

Behavioral tests and environment work also remain deferred under the earlier
user decision.

### New Active Queue

Complete the remaining non-RAG, non-sensor-stream gaps one at a time in this
order:

1. Cross-store lineage reconciliation and a unified mission/robot incident
   graph. Preserve source-store identity, runtime mode, provenance, and
   idempotency; never fabricate local edges to absent records.
2. Firefighting Entity Memory. Model victims/responders, exits, rooms/zones,
   fire/smoke sources, hazardous materials, obstacles, and robot assets with
   temporal/spatial uncertainty, provenance, aliasing, and conservative merge
   rules.
3. Active Episode lifecycle. Add active/completed/abandoned states, explicit
   start/end/abort transitions, parent-child episodes, and task-scoped event
   attachment without weakening append-only evidence.
4. Reliable consolidation jobs. Add job identity, completion/failure markers,
   resumability, idempotent repair, and protection against partially written
   Episode/Gist relation sets.
5. Richer safety-aware consolidation. Add spatial Gist geometry, multi-layer
   synthesis, contradiction retention, and configurable summarizer boundaries;
   generated text must remain a cognitive artifact rather than sensor fact.
6. Unified embodied-memory facade and agent tools. Provide permission-aware
   equivalents of episode summary, current context, gist search, entity query,
   locate, body status, temporal query, and spatial query. Exclude the deferred
   RAG semantic-search implementation.
7. Evidence retention and storage lifecycle. Design hot/cold storage,
   sensitivity-aware access, retention periods, redaction, and audit-safe
   deletion without copying eMEM's text-dropping archive behavior into incident
   evidence.
8. Exact spatial feature completion, including nearest-neighbor and spatial
   Gist queries. R*Tree adoption remains measurement-gated and is not required
   for the initial exact implementation.

### Research And Safety Framing

The active work should not aim for mechanical eMEM parity. The research value
is the adaptation of embodied memory to safety-critical firefighting:

- uncertainty-aware entity identity and state;
- provenance-preserving multi-robot incident graphs;
- contradiction-aware consolidation;
- advisory memory that cannot bypass current SafetyGate validation;
- auditable retention of commands, observations, approvals, actions, and
  outcomes across distributed robot runtimes.

### Next Recommended Step

Start active item 1 by defining the cross-store record envelope and
reconciliation invariants. Reconciliation should copy or project immutable
events into a mission-level evidence store with source-store provenance and
stable deduplication keys, then create graph relations only after both projected
endpoints exist in the destination store.

## 2026-07-18T15:50:54+08:00

### Queue Reprioritization

The user explicitly selected Firefighting Entity Memory as the next task.
Entity Memory now precedes cross-store reconciliation in the active queue.

### Entity Memory V1 Result

Implemented files:

- added `src/fireclaw_core/memory/entity_memory.py`;
- extended `src/fireclaw_core/memory/embodied_memory.py`;
- wired the service in `src/fireclaw_core/gateway/gateway.py`;
- updated `docs/architecture/embodied-memory-event-production.md`.

eMEM analogue inspected:

- `emem-main/emem/types.py`: `EntityNode` and graph edge types;
- `emem-main/emem/store.py`: entity add/update, matching, query, Observation
  lookup, and co-occurrence lookup;
- `emem-main/emem/consolidation.py`: per-Observation extraction attribution,
  merge flow, `OBSERVED_IN`, and `COOCCURS_WITH` generation;
- `emem-main/emem/tools.py`: entity query, locate, recall, and body status.

Implemented FireClaw behavior:

- added `entity_mention` cognitive-artifact and `entity_resolution`
  runtime/operator event types with producer/evidence authority checks;
- added firefighting kinds for victim, responder, exit, room/zone, fire source,
  smoke source, hazardous material, obstacle, robot, and equipment;
- every mention requires an existing Observation in the same mission/runtime,
  non-empty extractor method identity, explicit confidence, and optional
  explicit pose/uncertainty;
- entity state is replayed from append-only mention/resolution evidence rather
  than saved as mutable truth;
- preserves aliases, tracking keys, all source Observation/mention IDs,
  confidence range, attributes, and complete location history;
- exact namespaced tracker identity of the same kind may attach automatically;
  name/time/spatial similarity only creates a merge proposal;
- independent evidence identities can promote a candidate to corroborated, but
  only an operator assertion can confirm it;
- operator actions support confirm, contradict, merge, reject-merge, resolve,
  and split, with same-kind and ownership validation;
- adds exact `observed_in` and `co_observed_with` relations and query APIs for
  entity observations and co-observed entities;
- supports kind/status/name/time and uncertainty-aware spatial entity queries;
- Robot Gateway constructs a resolver and operator-resolution producer whenever
  embodied memory is explicitly enabled.

Rejected eMEM behaviors for firefighting safety:

- no missing-attribution batch centroid;
- no linking an extracted entity to every Observation in a batch;
- no assumed co-occurrence when attribution is absent;
- no automatic merge from semantic name plus proximity;
- no overwrite of prior coordinates;
- no confidence update by taking the maximum value.

Current limitation: extraction is an explicit API boundary. No LLM/RAG extractor
or real sensor stream is connected, consistent with the user's deferred scope.
The current projection scans mission events and can later be replaced by a
rebuildable SQLite entity projection without changing evidence semantics.

Python 3.10 static compilation passed. Pytest and runtime tests were not run per
user instruction.

### Next Active Step

Implement cross-store lineage reconciliation and the unified mission/robot
incident graph, then continue Active Episode lifecycle work.

## 2026-07-18T16:05:32+08:00

### Entity Memory Priority Decision

The user asked which remaining Entity Memory item should be implemented first.
Selected Agent Tool/API exposure because the entity model was already usable as
a library but not available to the robot-local LLM or an authorized caller.

### Entity Memory Agent Tool Result

Implemented:

- added `src/fireclaw_core/memory/entity_tools.py` with mission-bound, read-only
  schemas and dispatch for entity query, entity detail, supporting Observation,
  and exact co-observation lookup;
- tools do not accept `mission_id` from the LLM, cap result counts, reject
  unknown arguments, and mark every result as advisory-only;
- robot-local LLM planning may execute one all-memory query round, then must
  produce a plan using action tools only;
- mixed memory/action calls in one model round are rejected;
- memory-derived plans still pass `RobotAgentPolicy`, skill validation, and
  SafetyGate and cannot directly trigger actuation;
- Robot Gateway exposes `GET /entity-memory/tools` and
  `POST /entity-memory/tools/call`, both protected by `state.read` scope.

The configured CodeGraph tools were not exposed in this agent session, so the
existing local agent/tool/gateway source was inspected directly. The eMEM
analogue files recorded in the preceding Entity Memory entry remain the design
reference.

### Deferred Entity Memory Work

Record for later, not active now:

1. automatic entity extraction from model/perception outputs;
2. real sensor/ROS stream ingestion;
3. rebuildable SQLite entity projection for high-volume query performance.

Hybrid semantic retrieval/RAG and HNSW remain separately deferred by user
decision. Tests and runtime environment validation remain deferred by user
decision.

### Next Active Step

Return to cross-store lineage reconciliation and the unified mission/robot
incident graph. This is required before mission-level tools can reliably query
entities projected from multiple robot-local stores.

## 2026-07-18T16:36:07+08:00

### Automatic Entity Extraction Decision

After clarifying that Agent Tool/API is the read/use boundary rather than the
memory ingestion core, the user continued to the next task. Selected a
conservative structured Observation extractor before any LLM text extractor or
real sensor/ROS stream integration.

### Structured Extraction Result

Implemented:

- added `src/fireclaw_core/memory/entity_extraction.py`;
- defined a replaceable `ObservationEntityExtractor` protocol and a default
  `StructuredObservationEntityExtractor`;
- the default contract reads only explicit `Observation.payload.entities[]`;
- every item requires `name`, a valid FireClaw `entity_kind`, and explicit
  numeric `confidence`;
- entity-specific pose is optional but the extractor never substitutes the
  observing robot/Observation pose for object location;
- namespaced tracking requires both namespace and track ID;
- malformed items are isolated as extraction issues without rolling back the
  already persisted Observation;
- deterministic extraction keys and mention event IDs prevent repeated replay
  from adding duplicate entity mentions;
- `RobotMemoryRecorder` now invokes extraction after an Observation append;
- Robot Gateway constructs and injects the extraction pipeline whenever
  embodied memory is enabled.

The implementation follows eMEM's Observation-attributed extraction boundary,
but rejects eMEM's missing-index batch linkage and centroid fallback. It does
not infer individuals from aggregate fields such as `victims_by_floor` and does
not parse free-form hazard strings into asserted entities.

### Deferred After This Step

1. optional LLM/natural-language extractor with strict structured output;
2. real perception, sensor, and ROS stream producers for the canonical
   `payload.entities[]` contract;
3. rebuildable SQLite entity query projection at measured scale;
4. cross-store mission/robot entity reconciliation.

Tests remain deferred by user decision. Static compilation is the only active
verification requirement.

## 2026-07-18T17:32:33+08:00

### Cross-Store Scope Clarification

The user confirmed FireClaw is hierarchical: MissionAgent on the command tablet
discovers registered Robot Gateways and assigns bounded tasks to robot-local
subagents. No peer A2A protocol is required. The active task is therefore
robot-to-mission evidence replication and reconciliation over the existing
Gateway HTTP path.

### Reconciliation Implementation Result

Implemented:

- added `src/fireclaw_core/memory/reconciliation.py`;
- added replication envelopes with source Store/robot identity, absolute
  sequence, mission/runtime isolation, original record, and SHA-256 checksum;
- added bounded cursor export over robot embodied-memory records;
- added idempotent mission-store import keyed by source Store and record ID;
- identical retransmissions are duplicates; changed origin checksums and
  destination ID collisions are persisted as conflicts without overwrite;
- relations remain pending until both event endpoints exist, then retry after
  subsequent batches;
- cursors persist per source Store, mission, and runtime mode so one mission
  cannot skip another mission's interleaved records;
- added Robot Gateway `GET /memory/replication` protected by `state.read`;
- added `RobotSubagentClient.get_memory_replication()`;
- added Mission Gateway `POST /missions/{id}/memory/sync` protected by
  `task.submit`, supporting one robot or all registered robots;
- Mission Gateway assembly creates a reconciler over the mission embodied Store
  with `memory-reconciliation.jsonl` state;
- fixed `MissionMemoryStore` allowed types to include `entity_mention` and
  `entity_resolution`, which otherwise failed at runtime.

Original robot event IDs are preserved so EntityMention Observation references
and relation endpoints remain intact. Source origin, conflicts, pending edges,
and checkpoints are kept in the separate append-only reconciliation log.

### Known Gaps

1. synchronization is explicit, not a periodic/background pull scheduler;
2. transport still needs TLS/mTLS, per-device credentials, signed envelopes,
   and replacement of the prototype client's broad admin scope;
3. sensitivity-aware replication/redaction is not yet enforced;
4. conflict and pending-state operator inspection endpoints are not yet added;
5. cross-robot entity identity resolution is not automatic;
6. tests remain deferred by user decision.

### Next Recommended Step

Add mission-level querying over the reconciled store and conservative
cross-robot Entity Memory projection. Before real deployment, prioritize device
authentication and sensitivity-aware replication over automatic scheduling.

## 2026-07-18T20:54:42+08:00

### Active Episode Lifecycle Audit

The user challenged whether a new runtime `Active Episode` abstraction would
solve any real grouping problem. Code-path inspection confirms that it would
currently duplicate stronger task lineage already present in FireClaw.

Verified behavior:

- each Robot Gateway submission creates a fresh local execution ID in the form
  `task-<uuid>`;
- that local execution ID is propagated into robot embodied events as
  `subtask_id`, including robot snapshots, safety decisions, and skill events;
- MissionAgent records the returned robot execution ID in Mission Registry and
  uses it on dispatch/outcome memory records;
- retries and reassignments create new Robot Gateway execution IDs, while the
  generated `StructuredRobotTask.task_id` remains a logical task identity of
  the form `mission:robot:floor:execution_group`;
- `FireClawConsolidationEngine` first groups raw evidence by
  `(robot_id, subtask_id)` and only then uses time gaps to subdivide one
  execution's evidence;
- the EventLedger already represents the active lifecycle through
  `task.received`, planning/execution events, and terminal task events;
- there is cancellation but no true pause/resume of the same robot execution.
  Mission session resume ownership is a different control-plane concept.

Conclusion: do not implement a separate runtime `Active Episode` lifecycle at
this stage. Keep `Episode` as a post-hoc derived memory summary, and treat the
Robot Gateway task ID as the execution-attempt identity. If future requirements
introduce true pause/resume or multiple phases inside one execution, add an
explicit `attempt_id` or `phase_id` rather than overloading `episode_id`.

### Gaps Found During Audit

1. The names of the two identities are ambiguous: `StructuredRobotTask.task_id`
   is logical, while Robot Gateway `task_id` is the concrete execution ID.
   Future cleanup should expose them explicitly as `logical_task_id` and
   `execution_task_id`/`attempt_id` in memory payloads.
2. MissionAgent's pre-dispatch `subtask` embodied event does not yet carry the
   returned Robot Gateway execution ID because that ID does not exist until
   after dispatch. Its event ID is preserved in `MemoryLineage`, and the later
   dispatch outcome links back to it, so evidence is still traceable.
3. `MissionScheduler._poll_group_terminal()` counts every historical terminal
   execution for matching robot IDs. After a retry/reassignment, an earlier
   failed attempt can satisfy the terminal count immediately and can be
   evaluated again. This is a scheduler retry-correlation bug, not a missing
   Episode lifecycle problem; polling should track the concrete task IDs
   returned by the current dispatch round.

No tests were run, per user instruction. This update is an architecture and
code-path audit only.

## 2026-07-18T20:57:48+08:00

### Mission Scheduler Retry Correlation Fix

Fixed the retry/reassignment correlation defect found during the Active Episode
audit in `src/fireclaw_core/mission/mission_scheduler.py`.

Implemented behavior:

- introduced an internal submitted-attempt record containing the stable logical
  subtask key, current `MissionSubtask`, and concrete Robot Gateway `task_id`;
- terminal polling now matches the exact `(robot_id, task_id)` values returned
  by the current dispatch round instead of counting every historical terminal
  task for a robot;
- retry and reassignment preserve the logical subtask key while replacing the
  concrete execution identity with the newly returned task ID;
- retry/reassignment limits are now counted per logical subtask rather than per
  robot, so two different subtasks assigned to one robot do not consume each
  other's retry budget;
- reassigned executions are polled under the new robot identity;
- failure decisions now include the failed concrete `task_id` for auditability;
- group `terminal_states` retain all execution attempts, including the original
  failure and subsequent retry/reassignment outcome.

Verification:

`/home/lpp/miniconda3/envs/py310/bin/python3.10 -m py_compile src/fireclaw_core/mission/mission_scheduler.py`

Result: exit code 0. The test suite was not run, per user instruction.

## 2026-07-18T21:13:59+08:00

### Reliable Consolidation Jobs Result

Implemented resumable, append-only consolidation jobs for Episode/Gist graph
construction.

Files added/modified:

- added `src/fireclaw_core/memory/consolidation_jobs.py`;
- updated `src/fireclaw_core/memory/consolidation.py`;
- updated `src/fireclaw_core/mission/mission_memory.py`;
- updated `docs/architecture/embodied-memory-event-production.md`.

Implemented behavior:

- each exact ordered source-event set receives a SHA-256-derived stable
  `job_id`, Episode ID, Gist ID, and deterministic IDs for newly repaired graph
  relations;
- job state is persisted in a separate fsync-backed append-only JSONL journal
  with `pending`, `running`, `failed`, and `completed` transitions;
- per-artifact checkpoints cover Episode creation, each `belongs_to` relation,
  Gist creation, each `summarizes` relation, and final completion;
- recovery checks the actual evidence and relation stores before writing, so a
  crash after evidence append but before checkpoint append does not duplicate
  the artifact;
- failed or interrupted jobs resume against the same artifact identities;
- existing legacy partial Episode/Gist output is adopted when exact source IDs
  identify it unambiguously;
- multiple existing Episodes or Gists for the same source set are reported as
  conflicts instead of selecting one silently;
- identity/payload conflicts fail closed and are recorded as failed jobs;
- completed jobs are no-op on rerun after their artifacts are verified;
- job query methods expose one job or filtered mission/status job lists;
- `ConsolidationResult` now returns the associated `job_ids`;
- fixed the lower-level mission-memory type allowlist to accept `episode`,
  which the embodied event contract already allowed;
- an in-process lock prevents concurrent consolidation through the same engine
  instance.

The eMEM consolidation implementation was inspected again. It provides Episode
and time-window consolidation but no crash-resumable job protocol; this is a
FireClaw safety/audit adaptation rather than mechanical eMEM parity.

Known boundary: this version does not provide a cross-process/distributed job
lease. Deployment should use one mission-level consolidation worker per store
until a persistent lease/ownership protocol is added.

Verification:

`/home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q src/fireclaw_core/memory src/fireclaw_core/mission/mission_memory.py`

Result: exit code 0. `git diff --check` also passed. Pytest and behavioral fault
injection were not run, per user instruction.

### Next Recommended Step

Continue with richer safety-aware consolidation: preserve spatial geometry,
cross-robot agreements and contradictions, and explicit generated-summary
boundaries without treating summaries as sensor facts.

## 2026-07-18T21:15:41+08:00

### Post-Reliability eMEM Gap Audit

Rechecked the actual `emem-main/emem/` facade, tools, store, consolidation,
working-memory, spatial, embedding, and type surfaces against current FireClaw.

Remaining active gaps, in recommended order:

1. richer safety-aware/spatial consolidation: frame-consistent spatial Gist
   geometry, uncertainty-aware clustering, layer/source synthesis, and explicit
   cross-robot agreement/contradiction retention;
2. unified mission-level memory facade and Agent tools for Episode summary,
   current context, Gist search, temporal/spatial query, locate, body status,
   and reconciled Entity Memory;
3. evidence retention/storage lifecycle replacing eMEM's mutable tier promotion
   and text-dropping archive with auditable hot/cold retention, sensitivity
   policy, redaction, and deletion authorization;
4. exact spatial feature completion, especially nearest-neighbor and spatial
   Gist/area queries without requiring R*Tree;
5. scalable Entity Memory projection and conservative cross-robot entity
   resolution over the reconciled mission store;
6. operational hardening for periodic reconciliation, per-device credentials,
   sensitivity-aware replication, conflict inspection, and a cross-process
   consolidation lease.

Intentionally replaced rather than missing:

- eMEM Active Episode is replaced by FireClaw mission/robot execution IDs and
  the task lifecycle; Episode remains a post-hoc derived record;
- eMEM's pre-persistence Working Memory buffer is replaced by append-first
  evidence plus a discardable bounded projection;
- eMEM's automatic semantic/proximity entity merging is replaced by
  conservative proposals and operator confirmation;
- eMEM's text-dropping archive behavior is rejected for incident evidence.

Explicitly deferred by the user:

- dense/lexical hybrid RAG, embeddings, HNSW, and RRF;
- free-text/LLM entity extraction beyond the structured observation contract;
- concrete live sensor/ROS measurement streams;
- tests and environment validation.

## 2026-07-18T21:28:22+08:00

### Richer Safety-Aware Consolidation Result

Implemented the next active eMEM gap with FireClaw-specific safety constraints.

Files added/modified:

- added `src/fireclaw_core/memory/safety_consolidation.py`;
- upgraded `src/fireclaw_core/memory/consolidation.py` to consolidation protocol
  v2;
- extended `src/fireclaw_core/memory/consolidation_jobs.py` with explicit
  cross-robot supporting evidence;
- updated `docs/architecture/embodied-memory-event-production.md`.

Implemented behavior:

- keeps each Episode scoped to its original `(robot_id, subtask_id)` execution;
- partitions positioned evidence by exact `(frame_id, floor)` and forms
  deterministic connected spatial clusters using each source uncertainty
  radius plus a configurable margin;
- stores conservative Gist geometry as uncertainty envelopes with bounds,
  center, radius, source IDs, robot IDs, and frame scope;
- projects a Gist pose only when exactly one spatial geometry exists, preventing
  mixed frames or disconnected areas from receiving a fabricated centroid;
- groups observations into evidence layers from explicit `layer_name`,
  `observation_kind`, sensor ID, or source type without parsing free text;
- adds structured cross-robot agreement/contradiction analysis for SafetyGate
  decisions, explicit `safety_assertions`, positive hazards, and per-floor
  victim counts;
- cross-robot coordinate comparison requires both records to declare
  `spatial_frame_scope="mission"`; otherwise only matching explicit scopes such
  as floor/zone may be compared;
- records exact values, robot IDs, event IDs, comparison window, and spatial
  margin for every retained agreement/contradiction;
- marks all cross-robot assessments `advisory_only` and
  `requires_current_state_revalidation`;
- introduces a replaceable `GistSummarizer` protocol and a deterministic default
  summarizer, with explicit method and profile identity;
- records `summary_provenance` as `derived_summary`,
  `generated_from_structured_context`, and `not_sensor_fact`;
- separates stable Episode identity from Gist generation profile so different
  summarizer/config versions reuse the Episode and create versioned immutable
  Gists;
- records external robot evidence as `supporting_event_ids`, includes every item
  in Gist `derived_from`, propagates its sensitivity/confidence conservatively,
  and creates exact `summarizes` relations;
- newly synchronized supporting evidence creates a new Gist revision instead of
  mutating an existing assessment;
- extends reliable consolidation job identity and journal state with the exact
  supporting evidence set;
- custom summarizer failures and enrichment failures transition the reliable
  job to `failed` rather than leaving it permanently `running`.

eMEM analogue reviewed:

- `emem-main/emem/consolidation.py`: DBSCAN spatial clustering, common-layer
  detection, multi-layer synthesis, mean-center/radius Gist generation;
- `emem-main/emem/store.py`: spatial Gist and nearest-observation queries;
- `emem-main/emem/types.py`: Gist center/radius/layer fields.

FireClaw intentionally does not copy eMEM's unqualified coordinate averaging or
LLM text-to-fact behavior. Cross-robot claims remain structured derived
assessments with complete evidence lineage and cannot authorize action.

Verification:

`/home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q src/fireclaw_core/memory src/fireclaw_core/mission/mission_memory.py`

Result: exit code 0. `git diff --check` passed. Pytest and behavioral tests were
not run, per user instruction.

### Next Recommended Step

Implement the unified mission-level memory facade and permission-aware Agent
tools for Episode summaries, current context, Gist lookup, temporal/spatial
query, locate, body status, and reconciled Entity Memory.

## 2026-07-18T21:47:18+08:00

### Unified Memory Facade Design Decision

User confirmed the intended role of the unified mission-level memory facade:
without one facade, an Agent would either query SQLite directly or call many
different memory APIs manually, making targeted memory use brittle and unsafe.
The facade should provide one stable read interface over Episode, Gist, Entity,
working memory, robot state, and reconciled mission-level stores.

Important safety requirement: this facade is not only a convenience layer. It is
also where FireClaw should centralize firefighting safety rules for memory
access, including sensitivity filtering, permission checks, bounded query
limits, evidence IDs, mission/runtime binding, freshness/revalidation markers,
and advisory-only handling for cross-robot or stale safety claims. Agent tools
should call this facade instead of exposing raw SQLite or scattered low-level
memory stores to the model.

## 2026-07-18T21:57:19+08:00

### Unified Mission Memory Facade And Agent Tools Result

Implemented the unified mission-level read boundary and permission-aware Agent
tools requested in the preceding design decision.

Files added:

- `src/fireclaw_core/memory/mission_memory_facade.py`;
- `src/fireclaw_core/memory/mission_memory_tools.py`.

Files integrated or documented:

- `src/fireclaw_core/mission/mission_runtime.py`;
- `src/fireclaw_core/mission/mission_agent.py`;
- `src/fireclaw_core/mission/mission_gateway.py`;
- `src/fireclaw_core/mission/mission_gateway_client.py`;
- `src/fireclaw_core/gateway/method_scopes.py`;
- `src/fireclaw_core/gateway/control.py`;
- `docs/architecture/embodied-memory-event-production.md`.

Implemented behavior:

- added `MissionMemoryFacade` over the append-only mission embodied Store,
  Working Memory freshness projection, Episode/Gist artifacts, reconciled
  Entity Memory, and robot registry/body state;
- added server-bound `MemoryAccessContext`; `mission_id`, `runtime_mode`,
  requester identity, and scopes cannot be supplied or changed by LLM tool
  arguments;
- added eight structured, read-only tools for current context, Episode/Gist,
  timeline, map-frame spatial evidence, Entity query/location, and robot status;
- semantic Gist search and free-text location are intentionally absent while
  RAG remains deferred; entity location requires exact ID or normalized name;
- all query limits and spatial radii are bounded, timestamps must be
  timezone-aware, and spatial queries require an explicit frame;
- spatial reads include source uncertainty and inspect the conservative
  `spatial_geometries` envelopes of multi-area Gists that cannot have one pose;
- every result carries exact evidence IDs, confidence/provenance where present,
  freshness, advisory/revalidation flags, and a safety block stating that
  memory cannot authorize action and must pass current sensors and SafetyGate;
- stale, derived, explicitly flagged, or multi-robot result sets require
  current-state revalidation;
- restricted events and entities derived from restricted evidence are omitted
  unless the bound caller has `memory.restricted.read` or `admin`; omission
  counts are reported without disclosing the hidden records;
- Mission Agent planning context now consumes the same facade rather than a
  separate safety policy;
- Mission Gateway exposes `GET /missions/{id}/memory/tools` and
  `POST /missions/{id}/memory/tools/call`, both protected by `state.read` and
  mission-bound by the URL;
- `MissionGatewayClient` now supports these endpoints and no longer sends
  `admin` by default; its default scopes are the narrower set needed by its
  existing read, submission, synchronization, and approval methods.

Analogue review:

- inspected `emem-main/emem/memory.py` (`SpatioTemporalMemory`) and
  `emem-main/emem/tools.py` (`MemoryTools`), reusing their high-level
  facade/tool split;
- rejected eMEM's text-only formatted results, free semantic location, and
  direct Store-oriented assumptions in favor of typed evidence and FireClaw
  safety controls;
- the configured CodeGraph tools were not exposed and `openclaw/` is not
  present in this workspace, so the OpenClaw session/tool analogue could not be
  inspected in this run and remains a future upstream review item.

Verification:

- `/home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q src/fireclaw_core/memory src/fireclaw_core/mission src/fireclaw_core/gateway` passed;
- module imports passed with `PYTHONPATH=src`;
- schema import reported 8 tools, max result bound 100, and restricted access
  false by default;
- `git diff --check` passed;
- the first import command omitted `PYTHONPATH=src` and failed with
  `ModuleNotFoundError: No module named 'fireclaw_core'`; rerunning with the
  repository source path fixed it;
- pytest and behavioral/runtime tests were not run, per user instruction.

### Remaining Active eMEM Gaps

Recommended next item is the evidence retention/storage lifecycle: auditable
hot/cold retention, sensitivity-aware redaction, and deletion authorization
without eMEM's mutable tier promotion or destructive text dropping.

After that remain exact spatial scaling/completion, scalable Entity projection
and conservative cross-robot identity resolution, and operational hardening
for synchronization/device credentials/conflict inspection/cross-process
consolidation leases. Hybrid RAG/HNSW, free-text extraction, real sensor/ROS
streams, and tests remain deferred by user decision.

## 2026-07-18T23:34:08+08:00

### Complete Mission-Scoped Memory Lifecycle Result

User clarified the desired retention model and requested the complete version
rather than a reduced first stage. The implemented policy is:

- each incident has isolated operational `Mission Memory`;
- a later incident starts from zero and cannot read an older incident's
  environment evidence;
- after terminal mission completion, an authorized operator seals the mission
  as `Audit Memory`;
- only manually approved, generalized `Reusable Knowledge` may cross missions;
- audit evidence may be deleted after human review, while a content-free
  deletion tombstone remains.

Added `src/fireclaw_core/memory/memory_lifecycle.py` with:

- lifecycle states `active`, `audit_only`, and `deleted`;
- append-only lifecycle transitions recording actor, timestamp, reason,
  mission status, archive identity, record count, and content digest;
- terminal-status enforcement before archive;
- exact per-mission local audit bundles with canonical SHA-256 integrity
  verification and sensitivity counts;
- archive-before-hot-removal ordering, atomic hot JSONL rewrite, and SQLite
  projection rebuild;
- retry recovery when a crash occurs after the audit transition but before hot
  evidence cleanup;
- fail-closed write and Agent-read guards after archive;
- sensitivity-aware audit reads: secret redaction, restricted-scope filtering,
  and omission counts;
- deletion authorization contract requiring a reason and exact mission-ID
  confirmation, followed by irreversible bundle removal and an auditable
  tombstone;
- reusable knowledge types for procedures, safety rules, skills, failure
  modes, capability constraints, and operator preferences;
- exact source-event lineage checks, explicit human approval, append-only
  revocation history, runtime applicability isolation, and exclusion of revoked
  knowledge from Agent reads.

Integrated behavior:

- `MissionMemoryStore` now supports an in-process lifecycle write guard and
  atomic per-mission purge while preserving malformed/unrelated JSONL lines;
- `MissionMemoryFacade` rejects decision-facing reads for non-active missions
  and includes lifecycle state in every response;
- a ninth Agent tool,
  `query_reusable_firefighting_knowledge`, exposes only approved structured
  cross-mission knowledge and keeps all results advisory;
- archived missions receive no tool schemas and tool calls fail closed;
- `MissionRuntimePaths` and the default server now configure
  `memory-lifecycle.jsonl`, `memory-audit/`, and
  `reusable-knowledge.jsonl` under the local data directory;
- Mission Gateway and its client now expose lifecycle, audit, archive, delete,
  knowledge approval/query/revocation APIs;
- role/scopes separate audit read, lifecycle management, knowledge approval,
  and destructive deletion. Operators can review; supervisors can seal and
  approve knowledge; only admins receive `memory.delete` by default.

Design boundary: archive is explicit after terminal status rather than silently
triggered by the first terminal robot report. This leaves time for final robot
memory reconciliation and consolidation before sealing; once sealed, delayed
writes are intentionally rejected. Audit deletion covers the embodied-memory
evidence bundle. Separate mission registry/task control records remain governed
by their own retention policy.

Analogue review:

- eMEM's tier promotion and `drop_text` archive behavior was reviewed in
  `emem-main/emem/consolidation.py`, `store.py`, and `types.py`;
- FireClaw intentionally uses mission isolation, immutable audit bundles, and
  explicit knowledge approval instead of mutable observation tiers;
- CodeGraph tools were not exposed in this run and the local OpenClaw reference
  was unavailable, so no OpenClaw lifecycle analogue could be inspected.

Verification:

- `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q src/fireclaw_core/memory src/fireclaw_core/mission src/fireclaw_core/gateway` passed;
- module import and schema inspection passed and reported 9 Agent memory tools;
- `git diff --check` passed;
- pytest and behavioral tests were not run, per user instruction.

### Next Recommended Step

The next remaining eMEM gap is exact spatial feature completion, especially
nearest-neighbor lookup and complete area/Gist queries. After that remain
scalable Entity projection/identity resolution and operational hardening for
periodic reconciliation, device credentials, sensitivity-aware replication,
conflict inspection, and cross-process consolidation leases.
