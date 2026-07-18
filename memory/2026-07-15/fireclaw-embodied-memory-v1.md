# FireClaw Embodied Memory v1

## 2026-07-15T00:11:22+08:00

### Task Goal

Use the locally downloaded `emem-main/` project as architectural reference and implement the first FireClaw-specific embodied memory slice without replacing the existing append-only mission memory path.

### Current Progress

Implementation and design documentation are written. Syntax compilation passed with the available Python 3.10 interpreter. The user explicitly chose to defer pytest execution to a later session.

### Architecture Decision

FireClaw memory now follows a two-layer model:

- authoritative evidence layer: append-only `MissionMemoryStore` JSONL records;
- rebuildable retrieval layer: `SqliteMemoryIndex` with FTS5, embodied metadata, temporal fields, spatial fields, and directed relations.

The implementation does not add `hnswlib`, `Rtree`, sentence-transformers, or LLM consolidation as required dependencies. Those remain optional later research phases.

Safety-relevant invariants implemented in the typed facade:

- every embodied event has explicit `runtime_mode`: `real`, `simulation`, or `replay`;
- embodied text/spatial/temporal/relation queries require a runtime mode;
- spatial records and queries require explicit `frame_id`;
- event and relation IDs cannot be appended twice;
- relation endpoints must already exist and share mission/runtime mode;
- timestamps must be timezone-aware ISO-8601 values;
- `body_state`, `correction`, and `restricted` payloads are structurally indexed but excluded from FTS by default;
- SQLite is derived state and can be rebuilt from JSONL evidence.

### Files Inspected

- `emem-main/README.md`
- `emem-main/pyproject.toml`
- `emem-main/emem/types.py`
- `emem-main/emem/store.py`
- `emem-main/emem/memory.py`
- `emem-main/emem/tools.py`
- `emem-main/emem/consolidation.py`
- `src/fireclaw_core/mission/mission_memory.py`
- `src/fireclaw_core/memory/memory_index.py`
- `src/fireclaw_core/memory/memory_retrieval.py`
- `tests/test_memory_index.py`
- `tests/test_memory_retrieval.py`
- `tests/test_mission_memory.py`
- `tests/test_memory_learning_loop.py`

### Files Added

- `src/fireclaw_core/memory/embodied_memory.py`
  - `SpatialMemoryContext`
  - `EmbodiedMemoryEvent`
  - `EmbodiedMemoryRelation`
  - `EmbodiedMemoryIndexingPolicy`
  - `EmbodiedMemoryStore`
- `tests/test_embodied_memory.py`
- `docs/superpowers/specs/2026-07-14-fireclaw-embodied-memory-v1-design.md`
- `memory/2026-07-15/fireclaw-embodied-memory-v1.md`

### Files Modified

- `src/fireclaw_core/mission/mission_memory.py`
  - extended allowed record types for embodied events and relations;
  - preserved the existing `MissionMemoryRecord` field shape;
  - kept sensitive event types out of the default transcript FTS policy.
- `src/fireclaw_core/memory/memory_index.py`
  - added backward-compatible schema migration for embodied columns;
  - added recursive payload text extraction while skipping reserved metadata;
  - added `index_text=False` structured-only indexing;
  - added spatial and temporal query APIs;
  - added relation upsert and neighbor query APIs;
  - added derived-index `clear()` support.
- `tests/test_mission_memory.py`
  - updated the expected record-type constant.

### Commands Executed

- repository and recent-memory inspection with `rg`, `find`, `sed`, and `nl`;
- environment probes:
  - system `python3` is Python 3.8.10 and has no pytest;
  - `/home/lpp/miniconda3/envs/py310/bin/python3.10` exists and has SQLite FTS5, but no pytest;
  - `.venv` and `uv` are unavailable;
- syntax verification:
  - `/home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q src/fireclaw_core/memory src/fireclaw_core/mission/mission_memory.py tests/test_embodied_memory.py`
  - result: exit code 0.

### Tests Not Run

`pytest` was not run. No available interpreter currently has pytest, the repository target is Python 3.11+, and the user requested that tests be deferred.

### OpenClaw Analogue Limitation

The repository currently has no `openclaw-main/` directory, no `.codegraph/` index was found, and CodeGraph MCP tools were not exposed in the session. Therefore the required OpenClaw-first structural comparison could not be completed. The implementation reuses FireClaw's existing mission-memory/index boundaries and records this deviation explicitly. Recheck OpenClaw session/memory/local-persistence structure when the reference tree and CodeGraph become available.

### Current Conclusion

Engineering implementation for the v1 memory foundation is present, but it is not considered verified until focused and full pytest suites run under the declared Python 3.11+ environment. The current work is infrastructure and a deterministic research baseline, not a publication-level memory method by itself.

### Next Recommended Step

Run the focused suite once a Python 3.11 environment with dev dependencies is available:

```bash
.venv/bin/python -m pytest \
  tests/test_embodied_memory.py \
  tests/test_memory_index.py \
  tests/test_mission_memory.py \
  tests/test_memory_retrieval.py \
  tests/test_memory_learning_loop.py -q
```

Then run the full regression suite:

```bash
.venv/bin/python -m pytest -q
```

After tests pass, integrate event writes at the existing boundaries in this order:

1. `MissionAgent`: command, plan, subtask, outcome;
2. `SafetyGate`: allow/block/escalate and operator confirmation;
3. `SkillRuntime`: invocation, progress, result, timeout, cancellation;
4. robot adapters: observation and body state with frame and uncertainty provenance.

Do not add LLM gist consolidation before raw evidence retention, derived provenance, and unsafe-summary rejection tests are in place.
