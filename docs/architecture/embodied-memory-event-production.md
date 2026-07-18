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
| `approval_runtime` | `safety_decision`, `correction` |
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
- rerunning consolidation with the same ordered source IDs reuses the existing
  Episode/Gist instead of duplicating derived records;
- restricted source data makes the derived Episode/Gist restricted;
- all safety decisions and operator-correction IDs remain explicit in the Gist,
  and conflicting decision statuses are flagged;
- no source event is deleted, demoted, text-stripped, or replaced by a summary.

Lesson creation is a separate explicit operation requiring at least two Gists,
a method identity, and confidence. Lessons are persisted with
`advisory_only=true` and `requires_runtime_revalidation=true`; they cannot serve
as authority for physical action without current SafetyGate validation.

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
