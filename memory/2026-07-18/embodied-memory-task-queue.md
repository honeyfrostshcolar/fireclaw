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
- `openclaw-main/`, `.codegraph/`, and exposed CodeGraph tools are unavailable,
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
- OpenClaw comparison remains unavailable because `openclaw-main/`, `.codegraph/`,
  and CodeGraph tools are absent. The local eMEM reference was inspected for
  tasks 6-8.
- Behavioral tests remain deferred by explicit user decision.
