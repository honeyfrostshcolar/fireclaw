# FireClaw Agent Memory Mechanism Review

## 2026-07-16T22:16:14+08:00

### Task Goal

Explain the current FireClaw agent memory mechanism to the user after reading the relevant implementation, tests, and recent design notes.

### Commands Executed

- `find memory -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r | head -2`
- `git status --short --branch`
- `find . -maxdepth 2 -name AGENTS.md -print`
- `find memory/2026-07-16 memory/2026-07-15 -maxdepth 1 -type f | sort`
- `rg --files src tests docs | rg 'memory|embodied|mission'`
- `find . -maxdepth 2 -type d -name '.codegraph' -o -name 'openclaw'`
- `sed`/`nl` reads of memory notes, design spec, memory source files, and focused tests.
- `rg "EmbodiedMemoryStore|MissionMemoryStore|MemoryRetriever|search_text|query_spatial|record_event" -n src tests docs/superpowers/specs docs/superpowers/plans`
- `rg "_record_mission_memory\(" -n src/fireclaw_core/mission/mission_agent.py`

### Files Inspected

- `memory/2026-07-16/project-status-recap.md`
- `memory/2026-07-15/fireclaw-embodied-memory-v1.md`
- `docs/superpowers/specs/2026-07-14-fireclaw-embodied-memory-v1-design.md`
- `src/fireclaw_core/memory/embodied_memory.py`
- `src/fireclaw_core/memory/memory_index.py`
- `src/fireclaw_core/memory/memory_retrieval.py`
- `src/fireclaw_core/memory/memory.py`
- `src/fireclaw_core/mission/mission_memory.py`
- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/mission/mission_runtime.py`
- `tests/test_embodied_memory.py`
- `tests/test_memory_index.py`
- `tests/test_memory_retrieval.py`
- `tests/test_mission_memory.py`

### Current Conclusion

FireClaw currently has three memory-related layers:

1. `MissionMemoryStore`: append-only JSONL mission transcript with optional SQLite FTS indexing.
2. `SqliteMemoryIndex` and `MemoryRetriever`: derived retrieval layer using FTS5, structured filters, optional stored embeddings, and optional rank fusion.
3. `EmbodiedMemoryStore`: newer typed facade for embodied robotics events, spatial context, runtime-mode isolation, temporal/spatial queries, directed relations, sensitivity-aware text indexing, and rebuildable SQLite projection.

The active `MissionAgent` constructor still accepts `MissionMemoryStore` and `memory_retriever`; it does not yet accept or construct `EmbodiedMemoryStore`. Existing agent writes record ordinary `outcome` and `correction` entries through `_record_mission_memory`, while the embodied memory v1 facade is implemented and tested in isolation but not wired into `MissionAgent`, `SafetyGate`, `SkillRuntime`, or robot adapters.

### Important Findings

- JSONL evidence is authoritative; SQLite is derived and can be cleared/rebuilt.
- `EmbodiedMemoryStore.append_event()` writes evidence first, then indexes.
- `EmbodiedMemoryStore.append_relation()` validates endpoints before evidence append and prevents cross-runtime relations.
- `runtime_mode` is mandatory for embodied events and queries; valid values are `real`, `simulation`, and `replay`.
- spatial context requires `frame_id`, finite coordinates, and non-negative uncertainty.
- `body_state`, `correction`, and `restricted` payloads are excluded from text indexing by default but remain structurally queryable.
- `MemoryRetriever` embedding mode reranks lexical candidates; it is not a true full-vector recall path.
- The repository currently has no usable git metadata, no `.codegraph/`, and no `openclaw/`, so OpenClaw-first structural comparison remains unavailable.
- Pytest has still not been run in this environment.

### Next Recommended Step

If continuing implementation, wire `EmbodiedMemoryStore` into the runtime boundaries in this order:

1. `MissionAgent`: command, plan, subtask, outcome.
2. safety gate / approval flow: allow, block, escalate, operator confirmation.
3. skill runtime: invocation, progress, result, timeout, cancellation.
4. robot adapters: observations and body state with frame, uncertainty, and sensor provenance.

Then run focused memory tests and the full suite under Python 3.11+ with pytest installed.

## 2026-07-16T23:39:44+08:00

### User Clarification

The user asked whether the append-only JSONL "authoritative evidence layer" is really authoritative if its records are currently produced by the LLM/planner/operator dialogue or agent-generated summaries rather than by direct robot perception/runtime sources.

### Clarified Conclusion

The append-only JSONL layer should be understood as the authoritative persisted log, not as an automatic guarantee that every record is a ground-truth world fact. It is authoritative only in the sense that later SQLite indexes, retrieval results, and incident reconstruction should be derived from the original JSONL records instead of treating SQLite or generated summaries as primary evidence.

The source of each record still matters and is currently underdeveloped. Existing `MissionAgent` writes are mostly produced by agent runtime code from user commands, planner/subtask results, gateway/subagent return values, and operator corrections. The project does not yet have a complete event-ingestion path from robot adapters, sensors, safety gates, skill runtime progress, or perception outputs into `EmbodiedMemoryStore`.

### Important Gap To Remember

The append-only JSONL source pipeline needs optimization before FireClaw can claim full embodied memory:

- Separate record provenance explicitly: `operator`, `planner`, `llm`, `skill_runtime`, `safety_gate`, `robot_adapter`, `sensor`, `perception`, `simulator`, `replay`.
- Treat LLM/planner records as proposed/derived cognitive artifacts, not direct environment facts.
- Treat sensor/perception/runtime records as embodied evidence, with `runtime_mode`, `frame_id`, timestamps, confidence, and uncertainty.
- Add event writers at actual runtime boundaries instead of only writing coarse `outcome` and `correction` records from `MissionAgent`.
- Preserve raw observations and runtime events before generating `gist` or `lesson` summaries.
- Do not let SQLite, embeddings, or consolidation summaries become the primary evidence source.

### Next Recommended Step

When memory work resumes, first design the upstream event-production contract for JSONL evidence. The key question is not only "where do we store memory", but "which subsystem is allowed to assert which kind of memory record, with what provenance and confidence".

## 2026-07-16T23:51:10+08:00

### User Clarification

The user asked for a natural-language explanation of how `MissionAgent`, `SafetyGate`, `SkillRuntime`, and `RobotAdapter` events should become embodied memories. After discussion, the user confirmed the important takeaway: these runtime event producers are not yet fully wired.

### Clarified Current State

`EmbodiedMemoryStore` currently provides the data contract and persistence/indexing foundation, not a complete live embodied-memory runtime. It can store typed events and relations once callers provide them, but most FireClaw runtime modules do not yet automatically produce those events.

Existing implementation should be understood as:

- `EmbodiedMemoryEvent`: standard event shape for future embodied memory records.
- `SpatialMemoryContext`: spatial metadata contract for location-aware records.
- `EmbodiedMemoryRelation`: graph edge contract for linking observations, decisions, actions, and outcomes.
- `EmbodiedMemoryIndexingPolicy`: policy for keeping sensitive event payloads out of full-text search.
- `EmbodiedMemoryStore`: append JSONL evidence first, then update SQLite projection.

This is not yet a complete emem-style implementation. It does not yet provide:

- a formal Working Memory queue for recent robot observations;
- automatic command/plan/subtask/outcome event emission from `MissionAgent` into `EmbodiedMemoryStore`;
- automatic allow/block/escalate/confirmation event emission from the safety gate;
- automatic skill start/progress/result/timeout/cancel event emission from skill runtime;
- automatic sensor/perception/pose/battery/body-state event emission from robot adapters;
- automatic relation creation between observation -> safety decision -> skill action -> outcome;
- Episode/Gist consolidation;
- HNSW semantic index;
- R-tree spatial index.

### Important Gap To Remember

Do not describe the current embodied-memory code as a complete runtime memory system. It is a foundation layer. The next implementation phase should make real runtime components write records into it.

Recommended order:

1. Define an event-production contract for runtime modules.
2. Add `MissionAgent` writes for command, plan, subtask, and outcome.
3. Add `SafetyGate` writes for allow/block/escalate and operator confirmation.
4. Add `SkillRuntime` writes for invocation lifecycle.
5. Add `RobotAdapter`/sensor writes for observation and body state with provenance, frame, confidence, and uncertainty.
6. Add relation creation across the resulting event chain.
7. Only after raw event capture is reliable, add Working Memory, consolidation, HNSW, and R-tree.
