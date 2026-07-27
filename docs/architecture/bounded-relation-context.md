# Bounded Relation Context Retrieval

## Purpose

FireClaw can retrieve an auditable context subgraph around one reconciled
Entity or one Episode. The result is advisory evidence for planning; it cannot
authorize robot action.

The implementation follows the existing `MissionMemoryFacade` read boundary
instead of introducing a second graph store. OpenClaw does not currently expose
an equivalent bounded memory-graph traversal API, and eMEM's relation access is
primarily single-record or single-hop. FireClaw therefore reuses its existing
mission/runtime isolation, sensitivity filtering, evidence envelope, and
relation projection while adding robotics-specific fail-closed bounds.

## API

`MissionMemoryFacade.query_related_context()` accepts exactly one seed:

- `entity_id`: expands from the Entity's mention and observation evidence;
- `episode_id`: expands from the Episode event.

Optional controls:

- `direction`: `incoming`, `outgoing`, or `both`;
- `relation_types`: an allowlist from `MEMORY_RELATION_TYPES`;
- `max_depth`: breadth-first traversal depth;
- `max_nodes`: maximum returned event nodes;
- `max_edges`: maximum returned relation edges.

The LLM-facing read-only tool is
`query_mission_memory_related_context`. Mission ID, runtime mode, requester
identity, and permission scopes remain server-bound and are never accepted as
tool arguments.

## Safety And Isolation

- Only records in the access context's mission and runtime mode are considered.
- Restricted records require `memory.restricted.read` or `admin`.
- A hidden record is omitted and is never expanded as a bridge to otherwise
  visible records.
- Cycles are handled with visited node and relation sets.
- Traversal order is deterministic.
- Server configuration caps depth, edges, neighbors per node, and total result
  nodes even when the caller asks for larger values.
- Every result retains evidence IDs, is marked advisory, and requires current
  sensor and safety-gate validation before physical action.

## Result Shape

The response contains:

- `seed` and `seed_record_ids`;
- event `nodes` with shortest discovered depth;
- directed relation `edges` with traversal direction and discovery depth;
- omission and truncation indicators;
- the standard mission-memory safety envelope.

`truncated=true` means at least one configured or requested graph bound stopped
further expansion. Callers must not interpret a truncated subgraph as complete.

