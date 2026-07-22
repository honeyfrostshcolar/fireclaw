# emem Reference Download Attempt

**Date:** 2026-07-14
**Status:** Blocked by local network restriction

## Task Goal

Download `https://github.com/automatika-robotics/emem` into `/home/lpp/fireclaw-master` so FireClaw can reference it when designing a memory module.

## Commands Executed

- `find memory -maxdepth 2 -type f 2>/dev/null | sort | tail -50`
- `git status --short`
  - Result: failed because `/home/lpp/fireclaw-master` is not a git repository from the local shell perspective.
- `find . -maxdepth 2 -name AGENTS.md -print`
- `sed -n '1,220p' memory/2026-07-04/fireclaw-mission-planning-guard-audit.md`
- `sed -n '1,260p' memory/2026-07-05/fireclaw-mission-planning-audit-persistence.md`
- `ls -la`
- `git clone https://github.com/automatika-robotics/emem.git emem`
  - Result: `fatal: unable to access 'https://github.com/automatika-robotics/emem.git/': Couldn't connect to server`
- `curl -L --fail --connect-timeout 20 https://github.com/automatika-robotics/emem/archive/refs/heads/main.zip -o /tmp/emem-main.zip`
  - Result: `curl: (7) Couldn't connect to server`

## Observed Result

The target repository appears to be the intended public GitHub project, but this local execution environment cannot connect to GitHub. Both `git clone` and direct zip download failed with connection errors.

## Current Conclusion

The download is blocked by environment/network restrictions, not by a repository naming issue. No `emem/` directory was created under `/home/lpp/fireclaw-master`.

## Next Recommended Step

Use one of these options:

1. In a terminal with GitHub access, run:
   `git clone https://github.com/automatika-robotics/emem.git /home/lpp/fireclaw-master/emem`
2. Download the GitHub zip manually and place it under `/home/lpp/fireclaw-master`, then ask the agent to unpack and inspect it.
3. If network access becomes available in this environment, rerun the clone command above.

## FireClaw Memory Module Relevance

`emem` should be inspected as a reference for embodied-agent memory design, especially persistent memory layout, spatial/temporal indexing, retrieval APIs, and how memory events are linked to observations/actions. FireClaw should still adapt the design for firefighting robotics constraints: auditability, safety-gated execution, operator corrections, robot identity, simulator/real-robot separation, and degraded communication.

## 2026-07-14 Update: Local Copy Inspected

**Timestamp:** 2026-07-14 Asia/Ulaanbaatar.

### Current Progress

The user downloaded the repository under `/home/lpp/fireclaw-master/emem-main`.

Files inspected:

- `emem-main/README.md`
- `emem-main/pyproject.toml`
- `emem-main/emem/types.py`
- `emem-main/emem/store.py`
- `emem-main/emem/memory.py`
- `emem-main/emem/tools.py`
- `emem-main/emem/consolidation.py`
- `emem-main/emem/config.py`
- `emem-main/examples/basic_memory.py`
- FireClaw comparison files:
  - `src/fireclaw_core/mission/mission_memory.py`
  - `src/fireclaw_core/memory/memory_index.py`
  - `src/fireclaw_core/memory/memory_retrieval.py`
  - `src/fireclaw_core/mission/mission_planning_audit.py`

Commands executed:

- `find . -maxdepth 3 -iname '*emem*' -print`
- `find emem-main/emem -maxdepth 3 -type f | sort`
- `sed -n ...` reads for the files above
- `python3` import probe with `sys.path.insert(0, 'emem-main')`

Import probe result:

- `python3` exists.
- Importing `emem` fails because this environment lacks `hnswlib`.
- No dependency installation was attempted.

### eMEM Architecture Notes

eMEM is a compact Python library around `SpatioTemporalMemory`.

Core model:

- Node types: `ObservationNode`, `EpisodeNode`, `GistNode`, `EntityNode`.
- Edge types: `BELONGS_TO`, `FOLLOWS`, `SUBTASK_OF`, `SUMMARIZES`, `OBSERVED_IN`, `COOCCURS_WITH`.
- Tiers: `working`, `short_term`, `long_term`, `archived`.
- Store: SQLite for structured graph records, HNSW for dense vector search, R-tree for spatial search, FTS5 for BM25/hybrid lexical retrieval.
- Consolidation: episode/time-window observations are summarized into `GistNode`; raw observations can later be archived.
- LLM tools: semantic, spatial, temporal, episode summary, current context, gist search, entity query, locate, recall, body status.
- Interoception/body-state observations are modeled as observations with `source_type="interoception"`.

### Comparison With Current FireClaw Memory

Current FireClaw memory is mission/audit oriented:

- `MissionMemoryStore`: append-only JSONL mission transcript records.
- `SqliteMemoryIndex`: optional SQLite FTS5 index with structured filters.
- `MemoryRetriever`: lexical retrieval with optional embedding rank fusion.
- Mission planning audit persistence is separate JSONL.

eMEM adds embodied memory capabilities that FireClaw does not yet have:

- spatial query by robot/world coordinate;
- persistent entity memory;
- episode/gist consolidation;
- graph edges between observations, episodes, entities, and summaries;
- body/interoception observations integrated with world observations.

### Current Conclusion

Do not copy eMEM wholesale. FireClaw should borrow the architectural shape:

1. Add FireClaw-specific embodied memory node/edge schemas.
2. Keep append-only audit/transcript records for incident reconstruction.
3. Add a query/index layer that supports spatial, temporal, lexical, and later vector retrieval.
4. Treat mission, subtask, skill invocation, observation, safety decision, body state, and operator correction as first-class typed records.
5. Add consolidation/gist generation only after raw auditability is preserved.

The strongest near-term design is to make FireClaw memory two-layered:

- immutable evidence layer: JSONL/audit logs, raw mission/skill/safety records;
- retrieval layer: SQLite graph/index tables with FTS5 now, spatial index next, embeddings/HNSW optional later.

### Next Recommended Step

Write a FireClaw embodied memory design spec based on eMEM, scoped to:

- typed memory records and graph edges;
- spatial/temporal query API;
- relation to existing `MissionMemoryStore` and `SqliteMemoryIndex`;
- safety/audit constraints for firefighting robots;
- first implementation slice with tests.
