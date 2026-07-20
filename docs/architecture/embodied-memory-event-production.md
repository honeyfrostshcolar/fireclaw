# Embodied Memory Event Production Contract

## Purpose

This contract governs live runtime writes into `EmbodiedMemoryStore`. It separates
the subsystem that asserts a record from the origin described by `source_type`,
and prevents generated artifacts from being represented as physical evidence.

`EmbodiedMemoryStore.record_event()` remains available for legacy import,
rebuild, and controlled migration. New runtime integrations must use
`EmbodiedMemoryProducer`.

## Provenance

Every runtime-produced event persists `MemoryEventProvenance` under
`_embodied.provenance`:

| Field | Requirement |
|---|---|
| `producer_type` | Registered subsystem class allowed to assert the event type |
| `producer_id` | Stable component or instance identifier |
| `evidence_kind` | Evidence semantics listed below |
| `source_id` | Required opaque operator identity for `operator_assertion` |
| `method_id` | Required planner/model/consolidator version for generated artifacts |
| `sensor_id` | Required physical or simulated sensor identifier for `sensor_evidence` |

`source_type` remains the domain-specific origin, such as `thermal_camera`,
`operator`, `mission_planner`, or `robot_state`. It does not grant authority to
write an event type; `producer_type` does.

## Evidence Kinds

| Evidence kind | Meaning | Additional requirements |
|---|---|---|
| `operator_assertion` | Operator command, correction, or approval assertion | Opaque `source_id` |
| `cognitive_artifact` | Planner/LLM/deterministic reasoning output | `method_id`; never treated as sensor fact |
| `runtime_evidence` | State transition or result emitted by executing code | Runtime producer identity |
| `sensor_evidence` | Sensor or perception observation | `sensor_id` and `confidence`; spatial observations use explicit `frame_id` and uncertainty |
| `derived_summary` | Gist or lesson derived from retained evidence | `method_id`, `confidence`, and non-empty `derived_from` |

## Producer Authority

| Producer | Allowed event types |
|---|---|
| `operator_gateway` | `command`, `correction` |
| `mission_agent` | `mission`, `command`, `plan`, `subtask`, `outcome`, `correction` |
| `safety_gate` | `safety_decision` |
| `approval_runtime` | `safety_decision`, `correction`, `entity_resolution` |
| `entity_resolver` | `entity_mention`, `entity_resolution` |
| `skill_runtime` | `skill_invocation`, `outcome` |
| `robot_adapter` | `observation`, `body_state`, `outcome` |
| `sensor_adapter` | `observation`, `body_state` |
| `perception` | `observation` |
| `simulator` | `observation`, `body_state`, `outcome` |
| `replay` | Raw non-consolidated event types |
| `memory_consolidator` | `gist`, `lesson` |

Authority and evidence-kind checks are both required. For example, a
`mission_agent` may write a `plan` as `cognitive_artifact`, but it cannot write
an `observation` or label a plan as `sensor_evidence`.

## Entity Memory

`EntityMemoryService` adds persistent object identity without turning a mutable
entity row into incident evidence. The JSONL authority contains two immutable
event types:

- `entity_mention`: a method-attributed cognitive artifact derived from one
  existing Observation in the same mission and runtime mode;
- `entity_resolution`: a deterministic resolver result or an operator assertion
  that confirms, contradicts, merges, rejects, resolves, or splits entities.

Current `FireClawEntity` values are projections over those events. Each entity
retains all mention IDs, source Observation IDs, aliases, robot-namespaced
tracking identities, confidence range, attributes, and location history. New
locations append an assertion instead of overwriting prior coordinates. The
projection is cached in process memory and may be persisted in SQLite as a
rebuildable, source-tokened projection; JSONL mention/resolution events remain
the authority.

Entity kinds initially cover victims, responders, exits, rooms/zones, fire and
smoke sources, hazardous material, obstacles, robots, and equipment. A mention
is `candidate` by default and becomes `corroborated` only when its source
Observations have at least two distinct evidence identities. Only an operator
resolution may mark it `confirmed`.

Automatic identity attachment is limited to an exact namespaced tracker key of
the same entity kind and owning robot. A missing robot owner disables exact
tracker attachment. Same-name entities with compatible time and spatial
uncertainty produce a merge proposal but remain separate. Cross-robot proposals
require disjoint robot evidence, an explicit `spatial_frame_scope="mission"`,
matching frame/floor, bounded time gap, and uncertainty-region overlap. They are
bounded and deterministic, expose exact evidence IDs, and are marked
`candidate` or `ambiguous`; they never auto-merge victims or hazards. An
operator must confirm a proposal through an explicit resolution event, and the
current state must be revalidated before any physical action. Different kinds
cannot be merged. Split operations must name mention events already owned by
the selected entities.

Proposal payloads also report shared tracker namespaces when both robots expose
one. If both sides expose incompatible namespaces, the pair is rejected; if a
namespace is absent on one side, the alias/geometry/time evidence remains
explicitly marked as weaker rather than being treated as tracker identity.

Relations preserve eMEM's graph shape with stricter attribution:

```text
entity_mention --observed_in--> observation
entity_mention --co_observed_with--> entity_mention
entity_resolution --corrects/caused_by--> entity_mention
```

Co-observation is created only for mentions attributed to the same exact
Observation. FireClaw does not copy eMEM's batch-centroid fallback, assumed
batch co-occurrence, semantic-name automatic merge, coordinate overwrite, or
maximum-confidence update.

### Entity Memory Agent Tools

`EntityMemoryTools` exposes four read-only functions: entity query, entity
detail, supporting Observation lookup, and exact co-observation lookup. The
mission-level `MissionMemoryTools` additionally exposes a bounded identity-
proposal report. The caller binds `mission_id`; the LLM cannot select another
mission in tool arguments. Results are bounded and carry `advisory_only=true`
because stale or contradictory memory must not replace current sensing or
SafetyGate decisions.

The robot-local LLM may perform at most one entity-memory tool round before it
must return an action plan. A round cannot mix memory queries with action skill
calls. The second provider call receives action tools only, and the resulting
plan still passes `RobotAgentPolicy`, normal skill validation, and SafetyGate.

The Robot Gateway exposes the same facade through `GET /entity-memory/tools`
and `POST /entity-memory/tools/call`; both require `state.read` scope. This is a
query boundary only and does not expose entity resolution or robot actuation.

### Structured Entity Extraction

`EntityExtractionPipeline` automatically processes each Observation persisted
by `RobotMemoryRecorder`. The default extractor accepts only an explicit
`payload.entities` array:

```json
{
  "entities": [
    {
      "name": "victim-track-7",
      "entity_kind": "victim",
      "confidence": 0.87,
      "source_track_namespace": "thermal-camera-1",
      "source_track_id": "7",
      "pose": {
        "frame_id": "building-map",
        "x": 12.4,
        "y": 6.8,
        "floor": "2",
        "uncertainty_radius_m": 1.5
      },
      "attributes": {"detector_class": "person"}
    }
  ]
}
```

Every item requires an explicit name, FireClaw entity kind, and confidence.
Entity pose is optional but must be independently supplied in the entity item;
the extractor never treats the observing robot's pose as the object's pose.
Tracking requires both a namespace and ID. Invalid items are isolated and
reported while the original Observation remains persisted.

Each valid item receives a deterministic extraction key and mention event ID,
so replaying the same extractor over the same immutable Observation cannot add
another entity mention. The extractor protocol is replaceable, but natural
language/LLM extraction is not enabled by default. Any future model extractor
must preserve per-Observation attribution and pass through the same mention
validation and conservative resolver.

## Robot-To-Mission Evidence Reconciliation

FireClaw uses the existing hierarchical Gateway transport rather than an A2A
protocol. Each Robot Gateway remains local-first and exports immutable embodied
records through `GET /memory/replication`. The Mission Gateway explicitly pulls
and reconciles them through `POST /missions/{id}/memory/sync`.

Every envelope contains stable source Store/robot identity, absolute source
sequence, source record ID, mission and runtime identity, event/relation kind,
the original immutable record, and a canonical SHA-256 checksum.

The destination keeps original event IDs so entity mention payload references,
`derived_from`, and graph relation endpoints remain valid. Replication identity
is `(source_store_id, source_record_id)` and is stored in a separate append-only
reconciliation log. Identical retransmission is a duplicate. A changed checksum
for the same origin or a destination ID collision with different content is
quarantined and never overwrites evidence.

Relations are imported only after both endpoint events exist. Out-of-order
relations remain pending and are retried after later event imports; no
placeholder event or fabricated graph edge is created.

Cursors are scoped by `(source_store_id, mission_id, runtime_mode)`. One robot
Store can interleave multiple missions, so a global robot cursor would skip old
records for a mission synchronized later. Batch limits are bounded, backward or
non-progressing cursors are rejected, and runtime modes cannot cross streams.

This first path is an explicit pull operation, not a background scheduler.
Production deployment still requires TLS/mTLS, device credentials, signed
envelopes, sensitivity-aware replication, and retry scheduling. Cross-robot
entity merge remains conservative follow-up work.

## Integration Rules

1. Instantiate one `EmbodiedMemoryProducer` per runtime boundary with a stable
   `producer_type` and `producer_id`.
2. Preserve actual occurrence time in `observed_at`; `created_at` is persistence
   time and may differ.
3. Use `derived_from` for summaries and for runtime reports that aggregate
   lower-level evidence. Add graph relations separately for traversal semantics.
4. Do not put credentials, raw operator PII, or private deployment addresses in
   provenance identifiers.
5. A rejected production-policy check must happen before JSONL append.
6. Simulation, replay, and real events remain isolated by `runtime_mode`.

## Runtime Graph Semantics

FireClaw follows eMEM's explicit directed-edge shape, but extends the graph for
robot safety evidence. Runtime code must carry event IDs through the active
call/task context; it must never discover a parent by querying the globally
latest record because concurrent robot tasks can interleave.

The implemented edge directions are:

| Source | Relation | Target |
|---|---|---|
| plan | `caused_by` | operator command |
| subtask | `subtask_of` | mission plan |
| dispatch/mission outcome | `caused_by` | subtask or plan |
| body/environment/sensor observation | `supports` | safety decision |
| later skill lifecycle event | `follows` | prior safety/skill event |

Mission control and robot gateways may use separate append-only stores. A
`MemoryLineage` field therefore carries command/plan/subtask IDs with the
structured task for later reconciliation. Those external IDs are not inserted
as local relation endpoints: local edges still require both endpoint events to
exist in the same store, mission, and runtime mode. This preserves audit
integrity instead of creating dangling or cross-mode edges.

## Working Memory

`EmbodiedWorkingMemory` adapts eMEM's bounded recent-observation queue to a
safety-critical write-through model:

1. The producer validates and appends the event to JSONL first.
2. Only the successfully persisted event is projected into the in-process
   deque.
3. Projection failure never rolls back or hides authoritative evidence.

Snapshots require an explicit `runtime_mode` and `mission_id`, can be narrowed
to a robot/subtask, and default to excluding `restricted` events. Event-type
freshness limits are stricter for body state and observations than for task
context. Future timestamps beyond a small clock-skew allowance are stale rather
than trusted. Replay callers must pass the replay reference timestamp when
historical freshness semantics are required.

Mission planning may consume fresh, non-restricted working-memory records as
short-horizon context. SafetyGate does not authorize physical action from this
cache: it continues to evaluate directly acquired robot/environment state and
sensor policy. The queue is bounded by event count and serialized event size,
is safe to discard, and is never a second evidence store.

## Consolidation

`FireClawConsolidationEngine` retains eMEM's Episode/Gist graph shape while
preserving FireClaw's append-only incident evidence:

- raw events are grouped by `(robot_id, subtask_id)` and bounded temporal gaps;
- every source event points `belongs_to` its derived Episode;
- every Gist points `summarizes` to every exact source event;
- each ordered Episode source set, Gist supporting-evidence set, and
  consolidation profile has a deterministic consolidation job identity;
- job transitions (`pending`, `running`, `failed`, `completed`) and per-artifact
  checkpoints are persisted in a separate append-only
  `<evidence>.consolidation-jobs.jsonl` journal;
- Episode, Gist, and newly repaired relation IDs are deterministic, so a retry
  verifies or fills the same artifacts instead of creating replacements;
- recovery checks actual evidence and graph relations rather than trusting a
  checkpoint alone, covering a crash after an append but before its checkpoint;
- legacy partial Episode/Gist output is adopted when its exact ordered source
  IDs are unambiguous; multiple existing artifacts for the same source set are
  reported as a conflict rather than selected silently;
- restricted source data makes the derived Episode/Gist restricted;
- all safety decisions and operator-correction IDs remain explicit in the Gist,
  and conflicting decision statuses are flagged;
- positioned evidence is partitioned by exact `(frame_id, floor)` and then
  clustered using source uncertainty radii plus a configured margin; each Gist
  retains conservative envelope geometry instead of eMEM's unqualified mean
  position;
- observations are grouped into explicit evidence layers using `layer_name`,
  `observation_kind`, sensor identity, or source type without parsing free text;
- cross-robot agreement/contradiction analysis compares only structured
  SafetyGate decisions, `safety_assertions`, positive hazards, and per-floor
  victim counts inside a bounded time/spatial scope;
- coordinates are compared across robots only when both source payloads declare
  `spatial_frame_scope="mission"`; same-named robot-local `base_link` or `odom`
  frames are not assumed to be aligned;
- structured cross-robot assessments retain exact robot/event IDs, are
  `advisory_only`, and require current-state revalidation;
- Gist summarization uses a replaceable summarizer with an explicit `method_id`;
  summary provenance states `derived_summary` and `not_sensor_fact`, and
  different summarizer versions may coexist over the same source evidence;
- an Episode remains scoped to one robot execution, while a Gist may list
  additional cross-robot `supporting_event_ids`; every supporting event is part
  of the Gist's `derived_from` provenance and receives a `summarizes` edge;
- newly synchronized supporting evidence creates a new immutable Gist revision
  under the same Episode instead of mutating an earlier assessment;
- no source event is deleted, demoted, text-stripped, or replaced by a summary.

Sensor/perception producers may provide explicit assertions in this optional
shape:

```json
{
  "spatial_frame_scope": "mission",
  "safety_assertions": [
    {
      "subject": "zone-2-east",
      "predicate": "smoke_state",
      "value": "dense",
      "scope": {"floor": "2", "zone": "east"}
    }
  ]
}
```

Malformed assertions are ignored by consolidation and remain available as raw
evidence. Absence of a hazard is never inferred from an omitted assertion.

Lesson creation is a separate explicit operation requiring at least two Gists,
a method identity, and confidence. Lessons are persisted with
`advisory_only=true` and `requires_runtime_revalidation=true`; they cannot serve
as authority for physical action without current SafetyGate validation.

## Unified Mission Memory Facade And Agent Tools

`MissionMemoryFacade` is the single mission-level read boundary over the
append-only embodied Store, the recent Working Memory projection, Episode/Gist
artifacts, reconciled Entity Memory, and registered robot state. The main Agent
does not receive a SQLite connection or choose a Store path. Every call receives
a server-created `MemoryAccessContext` containing `mission_id`, `runtime_mode`,
requester identity, and granted scopes; none of these isolation fields appear in
the LLM tool arguments.

`MissionMemoryTools` exposes eleven read-only operations:

- current mission context;
- Episode summary with Gist revisions;
- structured and exact spatial Gist filtering;
- exact temporal query;
- frame-explicit spatial query;
- unified nearest Observation/Gist/Entity query;
- Entity query;
- bounded cross-robot Entity identity proposal report;
- exact entity location;
- robot registry/body-state status;
- operator-approved reusable firefighting knowledge using exact type/tag filters.

The Gist query is deliberately structured-only while semantic RAG remains
deferred. Entity location accepts an exact entity ID or normalized name and
never calculates a location from free text. Spatial reads require a `frame_id`,
bound the radius, include source uncertainty, and also inspect the conservative
`spatial_geometries` envelopes on multi-area Gists.

Exact spatial reads use the same geometry contract for area and nearest-neighbor
queries. A positioned event or Entity is a point plus
`uncertainty_radius_m`. A Gist may contain several conservative geometries, each
scoped to an exact `(frame_id, floor)`; the closest matching geometry is used
without fabricating a centroid across floors, frames, or disconnected areas.
When all source positions have `z`, Gist vertical bounds also include their
positional uncertainty. A three-dimensional query excludes records whose
vertical position is unknown.

Results retain `distance_m` as center distance and add
`distance_to_uncertainty_m`, the minimum possible distance to the conservative
uncertainty region. Intersection and nearest ranking use the latter. Both
values, the matched geometry, source evidence IDs, freshness, and revalidation
flags are returned so a planner cannot mistake the center of a large Gist area
for an observed target position. `query_mission_memory_nearest` can restrict the
scan to Observation, Gist, or Entity records and can filter Entity kinds and
statuses, allowing queries such as nearest exit, hazard, or victim candidate
without semantic retrieval.

The current implementation performs a deterministic exact scan over the active,
mission/runtime-isolated records and projected Entities. Query distance and
result count are bounded. This is intentionally the correctness baseline before
an R*Tree projection: any future spatial index must preserve uncertainty-region
intersection, multi-geometry Gists, sensitivity omission, and deterministic
ordering exactly.

All responses carry source evidence IDs and a common safety envelope. Memory is
always `advisory_only`; `can_authorize_action` is always false; stale, derived,
or explicitly flagged claims require current-state revalidation and must still
pass live sensors and SafetyGate. Query limits are bounded. Restricted records
are omitted unless the server grants `memory.restricted.read` or `admin`, and
the response reports how many records were omitted without exposing them.

Mission Gateway publishes the schemas at
`GET /missions/{id}/memory/tools` and executes a call at
`POST /missions/{id}/memory/tools/call`, both under `state.read`. The URL binds
the mission; request bodies contain only `{name, arguments}`. The Gateway also
binds requester/scopes from trusted headers before dispatch. The Mission Agent
uses the same facade for its planning context, so internal planning and external
tool calls follow one freshness, sensitivity, provenance, and safety policy.

This mirrors eMEM's high-level `SpatioTemporalMemory` plus `MemoryTools` split
and reviews its `Store.spatial_query`, `Store.spatial_nearest`, and
`Store.search_gists_by_area` shapes, but replaces unscoped coordinates, Gist
center-only area matching, free semantic location, formatted text-only returns,
and direct Store exposure with typed evidence and firefighting safety controls.
The local OpenClaw reference and configured CodeGraph service were unavailable
during this implementation, so its session/tool analogue still requires a
future upstream comparison.

## Mission-Scoped Memory Lifecycle

FireClaw separates three kinds of persistence instead of treating every past
incident as lifelong Agent context:

1. `Mission Memory` is operational evidence for exactly one `mission_id`. A new
   incident starts with an empty mission scope, even when older local archives
   exist.
2. `Audit Memory` is a sealed post-incident record. It is readable only through
   audit APIs and can never be returned by decision-facing Agent tools.
3. `Reusable Knowledge` contains only explicitly approved procedures, safety
   rules, skills, failure modes, capability constraints, or operator
   preferences. It may cross missions but remains advisory and must be checked
   against current sensors and SafetyGate.

`MissionMemoryLifecycleStore` persists `active -> audit_only -> deleted`
transitions in `memory-lifecycle.jsonl`. A mission may be archived only after a
terminal mission status. Archiving first creates a per-mission local audit
bundle with a canonical `SHA-256` content digest and sensitivity counts, then
removes that mission from the hot JSONL store and rebuildable SQLite projection.
All lifecycle artifacts remain under the configured local data directory, and
there is no automatic time-based expiry; audit evidence remains until an
authorized person explicitly deletes it after review.
If a crash occurs after the transition but before hot-store cleanup, retry
verifies the archive and completes cleanup. Once `audit_only`, new evidence,
robot synchronization writes, and all decision-facing memory tools fail closed.

Audit reads require `memory.audit.read`. Secret-shaped values are redacted on
output, and `restricted` records additionally require `memory.restricted.read`
or `admin`; omission counts are returned. Archiving requires
`memory.lifecycle.manage`. Deletion requires `memory.delete`, an explicit
reason, and a confirmation string exactly matching `mission_id`. Deletion
removes the audit bundle while preserving a content-free tombstone containing
who deleted it, when, why, the prior record count, and the former bundle digest.
The deleted evidence is not recoverable through FireClaw.

Reusable knowledge is never promoted automatically from an Episode, Gist, or
Lesson. `memory.knowledge.approve` is required to approve or revoke it, and all
source event IDs must exist in the active or audit evidence for one source
mission. Approved records are stored in `reusable-knowledge.jsonl`; revoked
records remain in that append-only approval history but are excluded from Agent
queries. Knowledge is runtime-scoped by default, so simulation-approved
knowledge cannot enter real-robot context unless its approval explicitly lists
`real` as an applicable runtime. This preserves evidence lineage without allowing site-specific
environment memory to silently contaminate a later fireground.

Lifecycle and knowledge endpoints are:

- `GET /missions/{id}/memory/lifecycle`;
- `GET /missions/{id}/memory/audit`;
- `POST /missions/{id}/memory/archive`;
- `POST /missions/{id}/memory/delete`;
- `GET /memory/knowledge`;
- `POST /memory/knowledge/approve`;
- `POST /memory/knowledge/{id}/revoke`.

## Evaluation And Index Scaling

`embodied_memory_eval.py` evaluates the current SQLite baseline before changing
index architecture:

- retrieval mean precision/recall and false-retrieval rate;
- runtime-mode contamination count, which must remain zero;
- mean and p95 query latency;
- required event-type coverage for incident reconstruction;
- provenance coverage and critical graph-transition coverage;
- evidence/index artifact sizes.

The index-scaling assessment refuses to recommend a backend when volume or p95
measurements are missing. Initial review thresholds require both meaningful
scale and measured latency pressure: at least 10,000 embedded records plus
100 ms p95 for HNSW consideration, or at least 50,000 spatial records plus
100 ms p95 for R*Tree consideration. Crossing a threshold starts an engineering
evaluation; it does not waive JSONL authority, runtime isolation, exact rebuild,
or result-equivalence requirements.

## OpenClaw Analogue Status

The required OpenClaw-first comparison could not be completed because this
workspace has no `openclaw-main/`, no `.codegraph/`, and no exposed CodeGraph
tools. This contract therefore extends the existing FireClaw embodied-memory
boundary and must be reviewed against OpenClaw session/local-persistence patterns
when that reference becomes available.
