# Planner Memory Retrieval Isolation

## 2026-07-24 16:40 +08 - Design And TDD Plan Approved

### Task Goal

Prevent Planner-facing memory from crossing mission, runtime, sensitivity, or
requester boundaries. Historical corrections may influence a later mission
only after explicit `approve_knowledge()` promotion. Plugin-supplied memory
must pass the same authority checks. Memory retrieval fails closed while
mission planning remains available.

### User Decisions

- Follow the Superpowers workflow.
- Selected approach B: create an independent
  `PlannerMemoryContextBuilder` plus explicit low-level
  `MemoryRetrievalScope`.
- Historical corrections require reusable-knowledge approval before
  cross-mission use.
- Plugin filter, rerank, and enrichment output is untrusted and must resolve to
  authoritative current records or approved reusable knowledge IDs.
- When lifecycle or another memory source is unavailable, omit that source,
  record a warning/audit diagnostic, and continue planning.
- Do not create a Python 3.11+ environment or install pytest. Use the existing
  Python 3.10 interpreter and dependency-free focused RED/GREEN tests.
- Do not commit unless the user separately and explicitly authorizes a commit.

### Current Progress

- Brainstorming questions completed.
- Architecture, data flow, error behavior, testing strategy, safety invariants,
  and research impact were presented and approved.
- Design spec written and self-reviewed:
  `docs/superpowers/specs/2026-07-24-planner-memory-retrieval-isolation-design.md`.
- Fine-grained writing-plans output written and self-reviewed:
  `docs/superpowers/plans/2026-07-24-planner-memory-retrieval-isolation.md`.
- No production code has been modified for this subproject.

### OpenClaw/Emem Analogue And Existing FireClaw Sources Inspected

The broader emem comparison is recorded in
`memory/2026-07-24/emem-gap-audit.md`. This subproject reuses FireClaw's
already stronger authority boundaries rather than importing a chat-oriented
memory API.

CodeGraph and focused source inspection covered:

- `src/fireclaw_core/mission/mission_agent.py`
  - `MissionAgent.__init__`
  - `_retrieve_planner_context`
  - `plan_and_submit`
  - `_with_validator_decision`
- `src/fireclaw_core/memory/memory_retrieval.py`
  - `RetrievedMemory`
  - `MemoryRetriever.retrieve`
  - `_rank_fusion`
- `src/fireclaw_core/memory/memory_index.py`
  - `SqliteMemoryIndex.search`
  - `_FILTER_COLUMN_MAP`
  - `_normalize_filters`
- `src/fireclaw_core/mission/mission_memory.py`
  - `MissionMemoryRecord`
  - `MissionMemoryStore.search`
  - `_search_via_index`
  - JSONL authority reads
- `src/fireclaw_core/memory/embodied_memory.py`
  - `EmbodiedMemoryEvent`
  - `to_mission_record`
  - runtime/sensitivity metadata storage
- `src/fireclaw_core/memory/mission_memory_facade.py`
  - `MemoryAccessContext`
  - `get_current_context`
  - `_event_payload`
  - `_envelope`
- `src/fireclaw_core/memory/mission_memory_tools.py`
  - public read-only `facade` property
  - current-context tool routing
- `src/fireclaw_core/memory/memory_lifecycle.py`
  - `ReusableKnowledgeRecord`
  - `approve_knowledge`
  - `revoke_knowledge`
  - `list_knowledge`
- `src/fireclaw_core/plugin/plugin_runtime.py`
  - existing provider/memory hook APIs
  - callback exceptions currently logged and swallowed
- `src/fireclaw_core/mission/mission_planning_audit.py`
  - `GuardDecision`
  - `MissionPlanningAuditRecord`
  - `append_guard_decision`
- `src/fireclaw_core/mission/mission_runtime.py`
  - memory/facade/lifecycle/Agent construction path

### Important Findings

1. `MissionAgent._retrieve_planner_context()` currently calls
   `MemoryRetriever.retrieve(command, limit=...)` without scope.
2. Its correction fallback searches every correction without `mission_id`.
3. Provider `enrich_context` output is appended after the current memory hooks,
   so raw plugin dictionaries can reach `MissionPlannerContext`.
4. `SqliteMemoryIndex` stores runtime and sensitivity, but its exact-filter map
   currently omits `sensitivity`.
5. Embodied runtime and sensitivity are authoritative in
   `MissionMemoryRecord.content["_embodied"]`.
6. `MissionMemoryTools.facade` allows a manually constructed Agent to build the
   same strict context boundary without accessing a private attribute.
7. `PluginRuntime` swallows callback exceptions. A backward-compatible
   diagnostic hook API is needed for content-free planning audit warnings.
8. Planning audit decisions can be appended after planner output and before
   validator decisions by replacing the frozen `MissionPlanningResult`.

### Planned Files

Create:

- `src/fireclaw_core/memory/planner_memory_context.py`
- `tests/test_planner_memory_context.py`

Modify:

- `src/fireclaw_core/memory/memory_index.py`
- `src/fireclaw_core/memory/memory_retrieval.py`
- `src/fireclaw_core/memory/memory_eval.py`
- `src/fireclaw_core/plugin/plugin_runtime.py`
- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/mission/mission_runtime.py`
- focused memory/plugin/Agent/runtime tests listed in the implementation plan

### Verification Constraints

- Available interpreter:
  `/home/lpp/miniconda3/envs/py310/bin/python3.10`.
- Pytest is absent.
- New RED/GREEN tests must be dependency-free plain functions while remaining
  pytest-compatible.
- Existing pytest-dependent modules will be migrated and compiled, but the
  work must not claim the full formal suite ran.
- Final static checks: `compileall`, unscoped `.retrieve(` scan, and
  `git diff --check`.

## 2026-07-24 22:30 +08 - Task 4 Complete: PlannerMemoryContextBuilder

### What was done

- Created `src/fireclaw_core/memory/planner_memory_context.py` with
  `PlannerMemoryContextBuilder`, `PlannerMemoryContextRequest`,
  `MemoryContextWarning`, `PlannerMemoryContextResult`.
- Added 20 new tests to `tests/test_planner_memory_context.py` (27 total).
- Updated `src/fireclaw_core/memory/memory_index.py` with `authority_token`,
  `finalize_spatial`, `commit` params on `upsert()`, plus stubs for
  `rtree_available`, `sync_spatial_authority_token`,
  `finalize_spatial_projection`, `load_records_by_ids`,
  `query_spatial_candidates`.
- Committed as `21e65bb` feat(memory): add planner context admission boundary.

### Key findings

- Restricted records are excluded from FTS5 text indexing by
  `EmbodiedMemoryIndexingPolicy.text_excluded_sensitivities`. The retriever
  cannot find them via text search. Scope grant is still correctly reported.
- `embodied_memory.py` from previous tasks passes `authority_token` to
  `upsert()` but `memory_index.py` wasn't updated. Fixed in this task.

### Current Conclusion

Tasks 1-4 are complete. The `PlannerMemoryContextBuilder` admission boundary
is implemented and tested (27 tests pass). The builder orchestrates memory
admission from MemoryRetriever (indexed), MissionMemoryFacade (current
context), and MissionMemoryStore (corrections). Each dependency failure is
caught independently. `memory_index.py` was also updated with stub methods
needed by EmbodiedMemoryStore and MissionMemoryFacade from previous tasks.

### Next Recommended Step

Continue with Task 5 (reusable knowledge gating) or Task 6 (mission agent
integration) from the implementation plan.
