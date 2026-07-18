# FireClaw Embodied Memory Gap Checklist

## 2026-07-17T00:00:03+08:00

### Task Goal

Record the clarified status of FireClaw's embodied-memory implementation so the next session can continue from memory without rediscovering what exists and what is missing.

### User Clarification

The user asked whether the previous emem-inspired memory work fully implemented emem-style memory, and requested a persistent record of what is still missing.

### Current Conclusion

The previous work implemented a FireClaw embodied-memory v1 foundation, not a complete emem-style memory system.

Implemented foundation:

- typed embodied event contract via `EmbodiedMemoryEvent`;
- spatial metadata contract via `SpatialMemoryContext`;
- directed relation contract via `EmbodiedMemoryRelation`;
- text-indexing policy via `EmbodiedMemoryIndexingPolicy`;
- `EmbodiedMemoryStore` facade that writes JSONL evidence first and SQLite projection second;
- SQLite structured metadata fields for `runtime_mode`, `source_type`, `episode_id`, `observed_at`, `frame_id`, `position_x/y/z`, `uncertainty_radius_m`, `confidence`, `sensitivity`;
- SQLite temporal query, spatial radius query, relation storage/query, and rebuild support;
- memory-layer safety constraints: explicit `runtime_mode`, explicit `frame_id`, timezone-aware timestamps, finite coordinates, confidence range, duplicate ID rejection, reserved payload-key rejection, sensitive payload exclusion from default FTS, same-mission/same-runtime relation endpoints.

Not implemented yet:

- complete Working Memory queue for recent robot observations and short-horizon state;
- automatic runtime event writers from `MissionAgent` into `EmbodiedMemoryStore`;
- automatic safety decision writers from `SafetyGate` / approval flow;
- automatic skill lifecycle writers from `SkillRuntime`;
- automatic observation/body-state writers from `RobotAdapter` / sensor/perception adapters;
- automatic event relation chain such as `command -> plan -> subtask -> skill_invocation -> observation -> safety_decision -> outcome`;
- full `EpisodeNode` equivalent;
- full `GistNode` equivalent;
- consolidation engine for Observation -> Episode -> Gist -> Lesson;
- HNSW vector index;
- R-tree / SQLite R*Tree spatial backend;
- full hybrid retrieval orchestration and evaluation loop;
- OpenClaw-first memory analogue comparison, because `openclaw-main/` and `.codegraph/` were unavailable.

### Important Framing

Do not describe the current code as a complete emem implementation. It should be described as:

```text
FireClaw embodied-memory v1 foundation:
typed events + JSONL evidence + SQLite projection + safety/provenance constraints
```

It should not be described as:

```text
complete emem-style Working Memory + Hybrid Store + HNSW + R-tree + Consolidation Engine
```

### Next Recommended Execution Order

1. Design an upstream event-production contract:
   - which subsystem may assert which event type;
   - required provenance fields;
   - confidence/uncertainty requirements;
   - distinction between LLM/planner artifacts and sensor/runtime evidence.
2. Wire `MissionAgent` to `EmbodiedMemoryStore`:
   - command;
   - plan;
   - subtask;
   - outcome;
   - operator correction.
3. Wire safety decisions:
   - allow;
   - block;
   - escalate;
   - operator confirmation / denial.
4. Wire skill runtime lifecycle:
   - invocation start;
   - progress;
   - result;
   - timeout;
   - cancellation;
   - failure mode.
5. Wire robot/sensor adapters:
   - observation;
   - pose;
   - smoke/thermal readings;
   - battery/body state;
   - sensor provenance;
   - `frame_id`, confidence, and uncertainty.
6. Add relation generation across the event chain.
7. Add Working Memory after real event producers exist.
8. Add Episode/Gist/Lesson consolidation only after raw evidence retention and `derived_from` provenance tests exist.
9. Add HNSW and R-tree only when there is enough reliable event volume to justify them.
10. Add retrieval evaluation:
    - retrieval accuracy;
    - false retrieval rate;
    - real/simulation contamination checks;
    - incident reconstruction completeness;
    - latency and index-size measurements.

### Commands To Run Before Further Memory Work

```bash
.venv/bin/python -m pytest \
  tests/test_embodied_memory.py \
  tests/test_memory_index.py \
  tests/test_mission_memory.py \
  tests/test_memory_retrieval.py \
  tests/test_memory_learning_loop.py -q

.venv/bin/python -m pytest -q
```
