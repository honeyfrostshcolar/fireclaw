# FireClaw Embodied Memory Task Queue

## 2026-07-19T22:58:30+08:00

### Task Goal

Persist the current status of the eMEM-inspired FireClaw embodied-memory work
so the next session can continue without rediscovering the same comparison.

### Current Progress

FireClaw has moved beyond the initial embodied-memory foundation. The current
branch now contains an eMEM-inspired mission memory stack adapted for
firefighting robots and safety-critical mission isolation.

Completed major pieces:

- Entity Memory for persistent mission entities such as people, exits, fire
  sources, hazardous materials, rooms, and robot-observed objects;
- structured Observation-to-Entity extraction from perception-style payloads,
  while deferring free-text/LLM entity extraction;
- robot-to-mission cross-store lineage reconciliation with exact source
  evidence tracking;
- Episode and Gist consolidation from mission observations;
- crash-resumable consolidation job state to avoid duplicated Episode/Gist
  generation after partial writes;
- richer firefighting safety-aware consolidation, including advisory
  cross-robot agreement/contradiction assessment and current-state
  revalidation flags;
- unified `MissionMemoryFacade` and permission-aware Agent tools over Episode,
  Gist, Entity, spatial, temporal, robot state, and reusable knowledge reads;
- complete mission-scoped lifecycle: active mission memory, local audit bundle,
  SHA-256 integrity digest, hot-store cleanup, SQLite projection rebuild,
  audit deletion tombstone, and approved reusable cross-mission knowledge;
- ninth Agent memory tool, `query_reusable_firefighting_knowledge`, for
  explicitly approved procedures, safety rules, skills, failure modes,
  capability constraints, and operator preferences.

### Remaining eMEM-Inspired Gaps

The next valuable eMEM ideas for FireClaw are:

1. Exact spatial feature completion:
   - nearest-neighbor lookup for observations, entities, and Gists;
   - complete area/spatial Gist queries;
   - conservative geometry handling before considering SQLite R*Tree.
2. Scalable Entity projection and conservative cross-robot identity resolution:
   - avoid incorrectly merging two distinct victims or hazards;
   - avoid over-splitting the same entity when evidence is strong;
   - preserve uncertainty when identity is ambiguous.
3. Operational hardening:
   - periodic reconciliation rather than one-shot synchronization;
   - per-device credentials and identity;
   - sensitivity-aware replication;
   - conflict inspection;
   - cross-process consolidation leases.

### Deferred By User Decision

- Hybrid RAG, embeddings, HNSW, and RRF retrieval;
- free-text/LLM entity extraction;
- real sensor/ROS stream integration;
- pytest or behavioral test work for now.

### Current Conclusion

The best next implementation step is exact spatial feature completion. It is
directly useful for firefighting robots, does not depend on RAG, does not
require LLM extraction, and can run over the structured mission memory already
implemented.

Expected capabilities:

- find the nearest exit, hazard, fire source, victim candidate, or observation
  from a robot pose;
- query evidence inside a frame/floor/area;
- query Gists whose conservative spatial geometry overlaps or is near a
  requested region;
- expose these through the existing `MissionMemoryFacade` and Agent memory
  tools with safety metadata, source IDs, uncertainty, and revalidation flags.

### Next Recommended Step

Resume by implementing exact spatial feature completion in the mission memory
read path. Start from:

- `src/fireclaw_core/memory/mission_memory_facade.py`;
- `src/fireclaw_core/memory/mission_memory_tools.py`;
- `src/fireclaw_core/memory/entity_memory.py`;
- `src/fireclaw_core/memory/safety_consolidation.py`;
- `docs/architecture/embodied-memory-event-production.md`.

Do not start with HNSW/RAG or real sensor streams. Those remain deferred until
the structured spatial and entity memory path is complete.

### Commands Already Run Recently

- `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q src/fireclaw_core/memory src/fireclaw_core/mission src/fireclaw_core/gateway`
- module import/schema checks for memory lifecycle and Agent tools;
- `git diff --check`.

Pytest and runtime/behavioral tests were intentionally not run because the user
asked to finish implementation tasks first and postpone testing.

