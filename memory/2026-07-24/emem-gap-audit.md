# FireClaw Versus eMEM Implementation Gap Audit

## 2026-07-24T10:23:36+08:00

### Task Goal

Compare the current FireClaw embodied-memory implementation with the local
`emem-main/` reference and identify:

- capabilities FireClaw still needs;
- existing FireClaw implementations that need targeted changes;
- eMEM behaviors that must not be copied into a safety-critical,
  multi-robot firefighting system.

The user explicitly chose to continue development without creating a Python
3.11 environment or running the official pytest suite first.

### Sources Inspected

eMEM:

- `emem-main/emem/memory.py`
- `emem-main/emem/store.py`
- `emem-main/emem/consolidation.py`
- `emem-main/emem/working_memory.py`
- `emem-main/emem/spatial.py`
- `emem-main/emem/embeddings.py`
- `emem-main/emem/tools.py`
- `emem-main/emem/types.py`
- `emem-main/emem/config.py`
- `emem-main/README.md`

FireClaw:

- `src/fireclaw_core/memory/consolidation.py`
- `src/fireclaw_core/memory/consolidation_jobs.py`
- `src/fireclaw_core/memory/working_memory.py`
- `src/fireclaw_core/memory/memory_retrieval.py`
- `src/fireclaw_core/memory/memory_index.py`
- `src/fireclaw_core/memory/entity_extraction.py`
- `src/fireclaw_core/memory/entity_memory.py`
- `src/fireclaw_core/memory/reconciliation.py`
- `src/fireclaw_core/memory/memory_lifecycle.py`
- `src/fireclaw_core/memory/mission_memory_facade.py`
- `src/fireclaw_core/memory/mission_memory_tools.py`
- `src/fireclaw_core/mission/mission_runtime.py`
- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/mission/mission_memory.py`
- recent embodied-memory records and architecture documents

CodeGraph was used first for module context, source, callers, and blast radius.

### High-Level Conclusion

FireClaw already exceeds eMEM in the areas that matter most for firefighting:

- append-only evidence authority and rebuildable projections;
- mission and real/simulated runtime isolation;
- access-controlled restricted evidence;
- explicit provenance and evidence IDs;
- uncertainty-aware frame/floor/2D/3D spatial semantics;
- conservative Entity identity proposals with operator confirmation;
- multi-robot replication envelopes and conflict-aware evidence;
- crash-resumable consolidation jobs;
- mission archive integrity and approved reusable knowledge;
- the invariant that memory is advisory and cannot authorize an action.

The main gaps are not basic storage or spatial indexing. They are:

1. existing consolidation is not integrated into the normal mission runtime;
2. incremental consolidation can create overlapping Episode/Gist derivations;
3. semantic retrieval is not a true independent dense retrieval path;
4. planner retrieval is not explicitly mission/runtime/sensitivity scoped;
5. working memory is not hydrated after process restart;
6. the older spatial facade paths still bypass the new R*Tree candidate layer;
7. relationship traversal and hot/cold retention remain incomplete;
8. replication and consolidation still need cross-process operational hardening.

### Capability Matrix

#### Persistence

eMEM uses SQLite as primary storage plus separately persisted HNSW and an
in-memory R-tree. FireClaw uses JSONL evidence as authority and SQLite as a
rebuildable FTS/Entity/spatial projection with authority tokens.

Decision: retain the FireClaw model. Any future dense vector index must also be
a source-tokened, rebuildable projection. Do not make HNSW authoritative.

#### Working Memory

eMEM buffers unpersisted observations and flushes on size/time thresholds.
FireClaw projects only already-persisted events and applies event-specific
freshness limits. FireClaw's write-through behavior is safer.

Gap: `EmbodiedWorkingMemory.hydrate()` exists, but runtime and gateway assembly
create an empty working memory and never hydrate it from recent persisted
events.

Targeted change: hydrate a bounded, mission/runtime-scoped set of fresh events
at startup. Do not adopt eMEM's unpersisted write buffer for robot evidence.

#### Episodes And Consolidation

eMEM exposes mutable `start_episode()`/`end_episode()` state and automatically
consolidates at episode end. FireClaw derives immutable Episode/Gist events from
source evidence and has deterministic IDs, provenance, sensitivity propagation,
cross-robot assessment, and a crash-resumable job journal.

Gap: `FireClawConsolidationEngine` has no normal runtime caller. It is currently
an isolated engine rather than an operational subsystem.

Targeted change required before wiring:

- current source selection excludes only `episode`, `gist`, and `lesson`;
- chunks are recomputed over all eligible events on every call;
- exact source tuples make retries idempotent, but adding one adjacent event
  changes the tuple and can create a new Episode/Gist overlapping an older one;
- time gaps and maximum count are the only boundaries, despite FireClaw already
  having subtask and terminal task-flow events;
- there is only an in-process lock, not a cross-process lease;
- failed/running job recovery is available at the job level but not scheduled.

Recommended implementation: add a consolidation coordinator with closed,
non-overlapping source ranges, a coverage watermark, explicit eligible event
types, task/subtask terminal boundaries, startup recovery, a cross-process
lease, and non-blocking execution. Trigger it on subtask/mission terminal
transitions and optionally on a bounded time/count threshold.

#### Semantic And Hybrid Retrieval

eMEM independently retrieves HNSW and BM25 candidates and combines them with
RRF across Observation, Gist, and Entity nodes.

FireClaw already has:

- FTS5;
- an embedding provider protocol;
- persisted embedding blobs;
- a full-scan `SqliteMemoryIndex.search_by_embedding()`;
- `MemoryRetriever`;
- retrieval evaluation fixtures.

However, `MemoryRetriever.retrieve()` first runs FTS and returns no results when
there are no lexical hits. Embeddings only rerank the lexical candidate set.
This cannot recall paraphrases with no shared terms and is not true hybrid
retrieval.

More importantly, `MissionAgent._retrieve_planner_context()` invokes
`retrieve(command)` without mission, runtime, sensitivity, or requester scope.
Its correction fallback also searches without `mission_id`. This conflicts
with the newer rule that cross-mission knowledge must be explicitly approved
as reusable knowledge.

Recommended sequence:

1. Add an explicit retrieval access/scope contract.
2. Default operational retrieval to current mission and runtime.
3. Allow cross-mission retrieval only from approved reusable knowledge.
4. Make dense retrieval an independent branch using the existing full-scan
   implementation first, with structured filtering and hydrated records.
5. Fuse lexical and dense ranks using deterministic RRF.
6. Add a rebuildable ANN projection only after scale measurements justify it.
7. Expose semantic embodied retrieval through `MissionMemoryFacade`, retaining
   evidence IDs, omission counts, sensitivity filtering, freshness, advisory
   labeling, and revalidation requirements.

#### Spatial Retrieval

eMEM's R-tree stores points in a single implicit coordinate space. Gists are
matched mainly through center/radius, and missing dimensions can be normalized
to numeric defaults.

FireClaw's new SQLite R*Tree projection is already the better design:

- explicit frame/floor isolation;
- separate 2D and 3D indexes;
- missing z never becomes zero;
- uncertainty-expanded candidate bounds;
- every Gist geometry is indexed independently;
- exact conservative matchers remain the semantic authority;
- stale/corrupt projection falls back to a deterministic scan.

Remaining implementation gap: only unified `query_nearest()` uses the R*Tree.
Spatial `query_spatial()`, `query_gists()`, and `query_entities()` still scan
linearly.

Targeted change: share the existing candidate provider across these facade
paths while preserving their exact output contracts and exact matchers. This is
engineering completion, not the next research contribution.

#### Entity Memory

eMEM uses LLM extraction and automatically merges entities using semantic name
similarity, spatial distance, and optional context similarity. Its fallback can
link an extracted entity to every observation in a batch and use a centroid.

FireClaw deliberately uses structured `payload.entities`, independently
supplied entity poses, robot-namespaced tracker identity, new entities for
ambiguous matches, advisory cross-robot proposals, and operator merge/split/
reject decisions.

Decision: do not copy eMEM automatic merging or batch-centroid fallback.

Useful targeted extensions:

- adapter-specific structured extractors for actual perception outputs;
- appearance/re-identification features as advisory proposal evidence;
- temporal reachability and motion compatibility;
- negative evidence and contradiction features;
- per-kind policies, with stricter victim/hazard rules than static equipment;
- calibrated proposal scores and explicit reason codes;
- periodic bounded proposal generation after replication.

No learned or semantic signal should directly merge victim or hazard identity.

#### Graph Relations

eMEM exposes a small graph with `BELONGS_TO`, `FOLLOWS`, `SUBTASK_OF`,
`SUMMARIZES`, `OBSERVED_IN`, and `COOCCURS_WITH`.

FireClaw already supports these concepts plus `caused_by`, `corrects`, and
`supports`, and uses relations internally for provenance, consolidation, and
Entity co-observation.

Gap: there is no bounded operator/agent evidence traversal API. Relations are
mostly consumed by internal services.

Targeted change: add bounded, access-controlled evidence expansion for
explaining a Gist, Entity, correction, or safety decision. Do not expose an
unbounded generic graph traversal tool to the planner.

#### Retention And Archival

eMEM mutates observations through working/short-term/long-term/archived tiers
and can delete raw text and embeddings after a short threshold.

FireClaw has mission-level `active -> audit_only -> deleted` lifecycle,
integrity-checked archives, write guards, and approval-gated reusable knowledge.
It intentionally retains raw evidence for incident audit.

Decision: do not copy eMEM's one-hour raw-text deletion.

Potential future change: tier only hot projections and query eligibility, not
the evidence authority. Pin commands, safety decisions, operator corrections,
failures, entity resolutions, and incident evidence. Introduce compaction only
after measured storage and latency pressure.

#### eMEM Features That Should Not Be Copied Directly

- implicit single-frame coordinates;
- fake `z=0` for missing altitude;
- concept-to-centroid `locate()` as an actionable robot location;
- automatic Entity merging;
- batch-wide Entity/Observation links when extraction is ambiguous;
- mutable deletion of raw safety evidence;
- formatted text-only tool responses without stable evidence IDs;
- unscoped global semantic retrieval;
- HNSW or R-tree state as primary authority;
- unpersisted working-memory buffering for safety-relevant observations.

### Prioritized Development Plan

#### P0: Correct Existing Runtime Boundaries

1. Scope planner retrieval and corrections by mission/runtime/sensitivity;
   route cross-mission learning through approved reusable knowledge.
2. Fix consolidation source coverage so repeated runs cannot create overlapping
   Episode/Gist records.
3. Add a consolidation coordinator and wire terminal task/mission triggers,
   startup recovery, and cross-process leases.
4. Hydrate working memory from recent persisted evidence at runtime startup.

#### P1: Complete Retrieval

5. Convert FireClaw retrieval from lexical-candidate reranking to independent
   lexical plus dense recall with deterministic RRF and structured filters.
6. Add a safe semantic memory tool through `MissionMemoryFacade`.
7. Reuse the R*Tree candidate provider in the three older spatial facade paths.

#### P2: Robotics-Specific Extensions

8. Add perception-adapter Entity extractors and richer advisory identity
   proposal features.
9. Add bounded evidence graph expansion for explainability.
10. Add sensitivity-aware replication policy, authenticated device identity,
    periodic reconciliation, and conflict inspection.

#### P3: Scale And Research Evaluation

11. Add ANN only when dense full-scan latency crosses a measured threshold.
12. Add hot-projection retention only when storage/query pressure is measured.
13. Evaluate paraphrase recall, uncertainty-aware retrieval equivalence,
    planner task success, unsafe-action/near-miss rate, cross-robot identity
    precision/recall, and revalidation effectiveness.

### Research Interpretation

Copying eMEM's HNSW, DBSCAN, or tier labels is engineering work, not a strong
research contribution. The stronger FireClaw direction is:

- auditable hybrid spatio-semantic retrieval under localization uncertainty;
- safety-constrained memory use with current-state revalidation;
- multi-robot evidence reconciliation without unsafe identity collapse;
- measured effects on planning success and unsafe-action reduction.

The next implementation should therefore make consolidation operational and
non-overlapping before adding ANN. Otherwise the system gains faster retrieval
without first producing stable, trustworthy long-term memory units.

## 2026-07-24 17:34 +0800 — Post-Isolation Re-Audit

### Scope And Current Conclusion

This pass rechecked the current FireClaw implementation after planner-memory
retrieval isolation and stage-scoped plugin authorization were completed.
No production code was changed during this audit.

The previous planner-scope gap is now closed:

- `PlannerMemoryContextBuilder` constructs an authoritative
  `MemoryRetrievalScope` from the current mission/runtime and allowed
  sensitivities.
- Cross-mission planner learning is limited to approved reusable knowledge.
- Plugin retrieval results are filtered and reranked per stage, then canonical
  records are reconstructed from the mission authority store.

The remaining work is therefore not "add isolation again." It is to make
long-term memory production operational, complete scoped hybrid recall, and add
robotics-specific state and evidence handling.

### eMEM Mechanisms Worth Reusing

1. **Automatic consolidation triggers**
   - eMEM consolidates at episode boundaries and periodically.
   - FireClaw should trigger consolidation from persisted subtask/mission
     terminal events and recover incomplete jobs at startup.

2. **Independent lexical and dense retrieval with deterministic fusion**
   - eMEM retrieves lexical and vector candidates independently.
   - FireClaw currently embeds only to rerank lexical hits and returns no result
     when lexical recall is empty.
   - FireClaw should independently recall both branches, then use deterministic
     RRF after mission/runtime/sensitivity scoping.

3. **Spatial-index candidate pruning**
   - The new R*Tree projection is already used by nearest queries.
   - The same candidate-provider pattern should be reused by gist, region, and
     entity queries while retaining exact conservative geometry checks.

4. **Temporal fallback to summaries**
   - When raw records are not query-resident, a query may fall back to
     Episodes/Gists if the response explicitly reports that it is derived and
     preserves source evidence IDs.

5. **Spatially associated robot body state**
   - eMEM's association of internal state with location is useful.
   - FireClaw should model typed channels such as battery, motor temperature,
     communication quality, localization covariance, traction/slip, and
     breathing-air or oxygen supply where the platform supports it.

6. **Bounded relation traversal**
   - FireClaw already has an indexed low-level `neighbors()` primitive.
   - It still needs an access-controlled, depth-bounded facade/tool surface for
     evidence expansion and incident explanation.

7. **Batch perception processing**
   - Batching is useful for throughput and cross-observation context.
   - FireClaw should implement this in perception-specific adapters, not by
     allowing an LLM to assert authoritative entities.

### Required Firefighting-Robotics Adaptations

- Consolidation must use closed, non-overlapping source ranges or watermarks.
  Exact-source idempotence alone does not prevent overlapping Episode/Gist
  derivations when new adjacent events arrive.
- Working memory must hydrate a bounded set of recent persisted records on
  startup. Safety-relevant observations must remain write-through; FireClaw
  must not adopt eMEM's unpersisted observation buffer.
- Dense candidates must be scoped before admission, reconstructed from the
  authority store, and accompanied by degraded/fallback diagnostics. ANN is
  only a rebuildable projection.
- A future semantic memory tool must return structured records, evidence IDs,
  omission counts, scope diagnostics, and revalidation requirements rather
  than formatted text alone.
- Robot state must be retained per typed channel rather than collapsing every
  channel into one latest `body_state` event. Freshness and safety thresholds
  must differ by channel.
- Spatial-interoceptive correlations must include robot ID, synchronized pose,
  frame/floor, localization uncertainty, and evidence IDs. Missing pose must
  never default to the world origin, and correlation must not be presented as
  causality.
- Entity matching may use appearance/re-identification embeddings, temporal
  reachability, context, and negative evidence to create proposals. Victims
  and hazards must never be auto-merged; operator or policy decisions remain
  authoritative.
- Multi-robot replication needs authenticated robot/store identity,
  peer/role/mission sensitivity policy, confidentiality, replay resistance,
  bounded batches, periodic reconciliation, and inspectable conflicts. eMEM's
  local single-agent design does not solve this.
- Retention should govern projection residency (hot/warm/cold query paths), not
  delete immutable safety evidence. Commands, safety decisions, corrections,
  failures, identity decisions, and incident evidence must be pinned.
- Recency must be event-type aware. Robot telemetry and hazard locations decay
  quickly; operator corrections and near-miss lessons decay slowly or not at
  all. Ranking should expose separate relevance, age, confidence, risk,
  reliability, and spatial components.

### eMEM Behaviors Not To Copy

- global or unscoped semantic retrieval;
- mutable active episodes as authority;
- unpersisted buffers for safety evidence;
- implicit single-frame geometry or missing coordinates defaulted to zero;
- free-text centroid lookup used as an actionable robot target;
- automatic entity merging or batch-centroid fallback;
- deletion of raw safety evidence after summarization;
- formatted-string-only tools without stable evidence IDs;
- HNSW/R*Tree indexes treated as authority;
- uniform recency decay across event types;
- LLM summaries or extracted entities treated as sensor facts;
- unrestricted graph traversal.

### Revised Implementation Priority

#### P0 — Operational Correctness

1. Make consolidation operational: persisted terminal-event triggers,
   non-overlapping source watermarks, eligible-event coverage, startup recovery,
   and cross-process leases.
2. Hydrate bounded working memory from current mission/runtime authority records
   at startup.
3. Before real multi-robot deployment, add authenticated replication identity
   and sensitivity-aware export policy.

#### P1 — Complete Retrieval

4. Implement true scoped lexical+dense recall with deterministic RRF and wire
   an embedding provider into the normal runtime assembly.
5. Add a structured, scope-safe semantic memory tool only after item 4.
6. Reuse R*Tree candidate pruning across region, gist, and entity query paths.
7. Expose bounded, provenance-preserving relation expansion.

#### P2 — Robotics-Specific Memory

8. Add typed spatial-interoceptive channels with per-channel freshness and
   safety thresholds.
9. Add perception-adapter entity extraction and richer advisory identity
   evidence.
10. Add projection-level hot/warm/cold retention only after workload
    measurements justify it.

#### P3 — Scale And Research Validation

11. Add ANN only after full-scan dense retrieval exceeds a measured latency
    budget.
12. Evaluate scope leakage, Recall@k/MRR, consolidation coverage/overlap,
    planner success, unsafe or stale-memory acceptance, revalidation rejection,
    entity merge precision/recall, latency, bandwidth, and incident-trace
    completeness.

### Research And Publication Assessment

- **Engineering correctness:** consolidation lifecycle, hydration, scoped hybrid
  recall, complete spatial projection use, and replication policy are necessary
  system work.
- **Research validity:** copying HNSW, DBSCAN, tier labels, or eMEM's tool names
  is not novel. Stronger hypotheses are safety-aware retrieval under pose
  uncertainty, spatial-interoceptive degradation prediction, and cross-robot
  evidence reconciliation without unsafe identity collapse.
- **Publication level:** the current framework alone is not yet a top-tier
  contribution. A credible paper needs explicit baselines (lexical-only,
  dense-only, eMEM-like hybrid, and no-memory/current-state-only), ablations for
  scope/freshness/uncertainty/reconciliation, and simulated plus real-robot
  evidence that the proposed memory mechanisms improve task success while
  reducing unsafe decisions.

### Next Recommended Step

The next major implementation should be consolidation correctness and runtime
coordination. It is the dependency for trustworthy Episodes/Gists and later
semantic tools. If a small low-risk task is preferred first, implement startup
working-memory hydration, then return immediately to consolidation.
