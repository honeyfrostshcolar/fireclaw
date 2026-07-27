# Planner Memory Retrieval Isolation Design

**Date:** 2026-07-24

**Status:** Approved

## 1. Purpose

FireClaw has two memory access paths with different isolation guarantees:

- `MissionMemoryFacade` binds reads to a server-created
  `MemoryAccessContext(mission_id, runtime_mode, requester_id, scopes)` and
  filters restricted evidence.
- `MissionAgent._retrieve_planner_context()` also calls the older
  `MemoryRetriever` and `MissionMemoryStore.search()` APIs without equivalent
  mission, runtime, sensitivity, or requester constraints.

When multiple missions share one SQLite memory index, the older path can place
records from another mission or runtime into `MissionPlannerContext`. Redaction
does not solve this problem because masking secrets is not authorization or
mission isolation.

This design makes one component responsible for all memory admitted into the
Planner. Current-mission evidence remains available, while cross-mission
knowledge becomes approval-gated and auditable.

## 2. Goals

1. Require explicit scope for every decision-facing `MemoryRetriever` call.
2. Isolate current operational memory by mission, runtime, sensitivity, and
   requester permission.
3. Prevent raw historical corrections from automatically affecting another
   mission.
4. Allow cross-mission learning only through approved, non-revoked reusable
   knowledge applicable to the current runtime.
5. Apply the same authority checks to plugin-provided memory.
6. Preserve planning availability when memory sources are absent or degraded.
7. Record omissions and degraded behavior without putting restricted content in
   logs or audit metadata.
8. Establish a stable boundary that later independent dense retrieval can use
   without redesigning access control.

## 3. Non-Goals

- Adding HNSW or another ANN index.
- Changing lexical or embedding ranking quality.
- Adding a new user-facing semantic memory tool.
- Changing R*Tree spatial retrieval.
- Changing consolidation, working-memory hydration, Entity resolution, or
  replication.
- Automatically promoting historical records to reusable knowledge.
- Allowing memory to authorize a robot action.

These are separate Superpowers spec and implementation cycles.

## 4. Selected Approach

The selected approach is an independent Planner memory-context builder backed
by an explicit low-level retrieval scope.

Alternatives rejected:

- Adding filters only inside `MissionAgent` leaves correction, plugin, and
  future dense-retrieval policy spread across call sites.
- Moving all Planner retrieval into `MissionMemoryFacade` combines ranking,
  plugin extension, approved cross-mission knowledge, and operational facade
  responsibilities in an already large module.

The builder centralizes admission policy without making storage or ranking
authoritative.

## 5. Architecture

### 5.1 New Module

Create:

```text
src/fireclaw_core/memory/planner_memory_context.py
```

The module owns request validation, source orchestration, canonicalization,
admission checks, quotas, diagnostics, and audit-decision construction.

It does not persist operational evidence or reusable knowledge.

### 5.2 Request

```python
@dataclass(frozen=True)
class PlannerMemoryContextRequest:
    command: str
    mission_id: str
    runtime_mode: str | None
    requester_id: str
    scopes: frozenset[str]
    max_memories: int = 5
    max_corrections: int = 3
```

Validation requirements:

- `command`, `mission_id`, and `requester_id` are non-empty strings.
- `runtime_mode`, when present, belongs to `MEMORY_RUNTIME_MODES`.
- `max_memories` and `max_corrections` are non-negative and bounded by the
  existing Planner context limits.
- Scope values are non-empty strings.

Invalid requests are programming errors and raise `ValueError`.

### 5.3 Result

```python
@dataclass(frozen=True)
class PlannerMemoryContextResult:
    memories: tuple[dict[str, Any], ...]
    corrections: tuple[dict[str, Any], ...]
    warnings: tuple[MemoryContextWarning, ...]
    omitted_counts: dict[str, int]
    restricted_access_granted: bool
    reusable_knowledge_available: bool
```

`MemoryContextWarning` contains only:

- stable reason code;
- source name;
- rejected or omitted count;
- optional stable record or knowledge ID;
- exception class for dependency failures.

It never contains record content, exception messages that may contain content,
or restricted metadata.

### 5.4 Low-Level Retrieval Scope

Add to `memory_retrieval.py`:

```python
@dataclass(frozen=True)
class MemoryRetrievalScope:
    mission_ids: tuple[str, ...]
    runtime_modes: tuple[str, ...]
    allowed_sensitivities: tuple[str, ...]
```

`MemoryRetriever.retrieve()` requires an explicit scope. It has no global
wildcard and no default scope.

Offline evaluation must also name the missions, runtimes, and sensitivities
that its fixture permits. Diagnostic use is not an implicit authorization
bypass.

The index supplies candidates, but hydrated records are checked again before
they are returned.

## 6. Dependencies

`PlannerMemoryContextBuilder` may receive:

- `MemoryRetriever` for relevant current-mission indexed records;
- `MissionMemoryStore` for current-mission corrections and record hydration;
- `MissionMemoryFacade` for fresh mission context under
  `MemoryAccessContext`;
- `MissionMemoryLifecycleStore` for approved reusable knowledge;
- optional plugin runtime for filter, rerank, and enrichment hooks.

Every dependency is optional. Missing dependencies produce a reduced but valid
result.

`MissionAgent` receives an optional prebuilt builder. Normal runtime assembly
constructs and injects it. For compatibility, a manually constructed
`MissionAgent` creates a strict default builder from its existing dependencies,
including `mission_memory_tools.facade` when memory tools are configured.

## 7. Canonical Planner Memory Shape

Every admitted Planner memory item uses the following envelope:

```json
{
  "memory_scope": "current_mission",
  "record_id": "record-id",
  "source_mission_id": "mission-id",
  "runtime_mode": "real",
  "sensitivity": "standard",
  "content": {},
  "advisory_only": true,
  "can_authorize_action": false,
  "requires_current_state_revalidation": true
}
```

For approved reusable knowledge:

```json
{
  "memory_scope": "reusable_knowledge",
  "knowledge_id": "knowledge-id",
  "source_mission_id": "source-mission-id",
  "runtime_mode": "real",
  "content": {},
  "advisory_only": true,
  "can_authorize_action": false,
  "requires_current_state_revalidation": true
}
```

The builder reconstructs envelopes from authoritative records. Plugin-provided
content is never treated as canonical.

## 8. Admission Rules

### 8.1 Current Mission Memory

A record is admitted only when:

1. its `mission_id` exactly matches the request mission;
2. its runtime exactly matches the request runtime;
3. its sensitivity is explicitly present and permitted by the requester;
4. its authority record is still present;
5. it is not a relation that reveals an omitted restricted endpoint.

Allowed sensitivities:

- ordinary callers: `standard`;
- callers with `admin` or `memory.restricted.read`: `standard` and
  `restricted`.

Records missing runtime or sensitivity metadata are not admitted to the
Planner. This deliberately excludes ambiguous legacy history.

When `request.runtime_mode is None`, no indexed historical record is admitted.
Legacy mode can continue planning without historical memory, but cannot perform
cross-mission or reusable-knowledge retrieval.

### 8.2 Current Mission Corrections

An ordinary correction is admitted only when:

- its mission matches the request mission;
- its runtime matches the request runtime;
- its sensitivity is permitted;
- its authority record is present.

Historical corrections never cross a mission boundary directly.

An operator preference intended for future missions must be approved as:

```text
knowledge_type = operator_preference
```

and is then handled as reusable knowledge.

### 8.3 Approved Reusable Knowledge

Reusable knowledge is admitted only when:

- lifecycle storage is configured and readable;
- the latest record status is `approved`;
- the current runtime occurs in `applicable_runtime_modes`;
- the record is returned by the authoritative lifecycle store;
- it fits the remaining memory quota.

Revoked knowledge is never admitted.

A simulation-derived record may affect a real mission only when its approval
explicitly lists `real` in `applicable_runtime_modes`.

Approval stores a separately supplied, redacted knowledge payload. Planner
retrieval never follows `source_event_ids` back to restricted source text.

### 8.4 Plugins

`filter` and `rerank` hooks may reorder or remove already admitted IDs. Their
output is canonicalized again so they cannot replace content or introduce a
new ID.

`PluginRuntime` adds diagnostic variants of its memory and provider hook
execution methods. They return the same effects as the existing methods plus
content-free failure records containing only plugin ID, hook name, and
exception class. Existing hook methods and their return shapes remain
unchanged. The builder uses the diagnostic variants so a callback exception is
represented by the corresponding stable warning code instead of disappearing
into logs.

`enrich_context` items must identify either:

- a current-mission record that can be reloaded and passes current scope; or
- an approved reusable `knowledge_id` that can be reloaded and applies to the
  current runtime.

Fields such as `operator_approved=true`, `memory_scope`, or `runtime_mode` in a
plugin payload are claims, not authority. Unknown, revoked, mismatched, or
unverifiable items are omitted.

## 9. Data Flow

For one planning request:

1. Validate `PlannerMemoryContextRequest`.
2. Derive allowed sensitivities from requester scopes.
3. Read fresh current context through `MissionMemoryFacade`.
4. Run scoped indexed retrieval for the current mission.
5. Read scoped current-mission corrections.
6. Canonicalize, deduplicate, and rank current-mission items.
7. Read applicable approved reusable knowledge.
8. Fill only the memory quota left after current-mission items.
9. Run plugin filter and rerank hooks, then canonicalize again.
10. Run plugin enrichment, authority-check each item, and canonicalize.
11. Apply final deterministic quotas.
12. Return memories, corrections, warning codes, and omission counts.

Deduplication keys:

- current mission: `("current_mission", record_id)`;
- reusable knowledge: `("reusable_knowledge", knowledge_id)`.

Current-mission evidence always has quota priority. Reusable knowledge fills
unused capacity; it never evicts current mission evidence.

Within a source, preserve the source's relevance or recency ordering. Use
stable ID as the final tie-breaker.

## 10. Failure Behavior

Memory is fail-closed; planning is fail-open.

- Index failure removes indexed current-mission results.
- Facade failure removes fresh context.
- Correction-store failure removes corrections.
- Lifecycle failure removes cross-mission reusable knowledge.
- Plugin failure removes only that hook's effect.

In every case, the builder returns remaining authorized context and warnings.
`MissionAgent` continues planning.

Dependency failures use stable warning codes:

- `current_memory_unavailable`
- `current_context_unavailable`
- `corrections_unavailable`
- `reusable_knowledge_unavailable`
- `plugin_filter_failed`
- `plugin_rerank_failed`
- `plugin_enrichment_failed`

Admission failures use:

- `authority_record_missing`
- `mission_scope_mismatch`
- `plugin_record_unverified`
- `plugin_scope_mismatch`
- `knowledge_not_approved`
- `runtime_mode_mismatch`
- `restricted_scope_denied`
- `legacy_metadata_missing`

No warning includes rejected content.

## 11. Planning Audit

The builder produces one `GuardDecision` summary:

```text
layer = memory_context
status = allow
reason = memory_context_validated | memory_context_degraded
```

Details contain:

- accepted count by source;
- omitted count by reason;
- warning codes;
- whether reusable knowledge was available;
- whether restricted access was granted.

Details do not contain memory content.

`MissionAgent` appends this decision to every available
`MissionPlanningAuditRecord` before validator decisions are appended. Memory
degradation therefore remains visible in the persistent planning audit without
blocking the plan.

If the planner returns no audit record or no audit sink is configured, the
warning is emitted through structured logging only.

## 12. Compatibility

- Existing storage formats remain unchanged.
- Existing `MissionMemoryFacade` tool contracts remain unchanged.
- Existing reusable-knowledge approval and revocation formats remain
  unchanged.
- `MemoryRetriever.retrieve()` callers must be migrated to explicit scope.
- Tests and offline evaluation must declare their fixture scope.
- `MissionAgent._retrieve_planner_context()` becomes a compatibility wrapper
  that delegates to `PlannerMemoryContextBuilder`.
- Existing plugin hook names remain available, but their results are subject to
  authority validation.

No fallback performs unscoped global retrieval.

## 13. Testing Strategy

Implementation follows test-driven development. Each behavior is written as a
failing test, observed to fail for the intended reason, then implemented
minimally and rerun.

The user chose not to create a Python 3.11 environment. Focused RED/GREEN runs
will therefore use the existing Python 3.10 interpreter and a lightweight
test-function runner. Test files remain compatible with the repository's
declared pytest workflow for later official execution.

Required tests:

1. Admit a matching current-mission/runtime standard record.
2. Reject another mission's record.
3. Reject simulation memory in a real request.
4. Reject restricted memory without scope.
5. Admit restricted memory with the required scope.
6. Reject legacy history missing runtime or sensitivity.
7. Reject a correction from another mission.
8. Admit an approved operator preference across missions.
9. Reject revoked knowledge.
10. Reject simulation knowledge for real unless approval explicitly includes
    real.
11. Preserve current mission context when lifecycle storage fails.
12. Reject plugin-forged approval.
13. Prevent plugin filter/rerank output from replacing canonical content.
14. Report deterministic omission counts and warning codes.
15. Append a non-blocking memory-context decision to planning audit.
16. Verify `MissionAgent` does not issue unscoped retrieval.
17. Verify an unavailable builder source never blocks planning.
18. Convert plugin callback exceptions into content-free warning and audit
    diagnostics without changing the existing hook API.

## 14. Security And Safety Invariants

1. Redaction is never treated as authorization.
2. Plugin claims are never treated as authority.
3. Missing scope metadata fails closed.
4. Simulation evidence does not enter real planning implicitly.
5. Cross-mission reuse requires an approved, applicable, non-revoked knowledge
   record.
6. Memory remains advisory and cannot authorize action.
7. Current robot and environment state must still be revalidated before action.
8. Memory failures cannot prevent an otherwise valid emergency plan from being
   produced.

## 15. Research Impact

This change is primarily engineering and safety infrastructure, not a
standalone research contribution.

It is nevertheless required for valid future experiments. Without enforced
mission/runtime boundaries, improvements attributed to semantic or
spatio-semantic memory could instead result from unintended access to records
from another mission or simulation. The isolation report also provides the
audit variables needed to measure retrieval availability, omissions, and
degraded-memory planning behavior.

## 16. Acceptance Criteria

The design is implemented when:

- no decision-facing Planner retrieval call is unscoped;
- current mission/runtime/sensitivity filtering is enforced twice;
- raw historical corrections cannot cross missions;
- only approved applicable reusable knowledge crosses missions;
- plugin additions are authority-verified;
- memory degradation does not block planning;
- memory-context audit decisions contain counts and codes but no content;
- focused RED/GREEN tests cover all required isolation cases;
- existing storage and public mission-memory tool formats remain compatible.
