# RAG Memory Adapter

## 2026-07-27T13:10+08:00

### Task Goal

Integrate the existing RAG retrieval interface into the memory retrieval path
without reimplementing vector retrieval in the memory subsystem.

### Context

- User explicitly reminded that vector retrieval was intentionally kept out of
  the memory work because RAG already owns dense/BM25/hybrid retrieval.
- Current `src/fireclaw_core/rag/` provides `DenseRetriever.query()` and
  `BM25Retriever.query()` with a shared `query(query, top_k)` shape returning
  ranked hits with `record` and `score`.
- Current planner memory context only trusts authoritative mission memory:
  retriever candidates contribute IDs, then `PlannerMemoryContextBuilder`
  canonicalizes from `MissionMemoryStore`.

### Files Inspected

- `src/fireclaw_core/rag/dense_retrieval.py`
- `src/fireclaw_core/rag/bm25_retrieval.py`
- `src/fireclaw_core/rag/rag_cli.py`
- `src/fireclaw_core/rag/dense_ranking.py`
- `src/fireclaw_core/rag/reranking.py`
- `src/fireclaw_core/memory/memory_retrieval.py`
- `src/fireclaw_core/memory/planner_memory_context.py`
- `src/fireclaw_core/mission/mission_runtime.py`
- `tests/test_memory_retrieval.py`
- `tests/test_rag_dense_retrieval.py`
- `tests/test_rag_bm25_retrieval.py`

### OpenClaw / CodeGraph Notes

- Used CodeGraph first to inspect memory retrieval and RAG retrieval interfaces.
- No OpenClaw analogue was needed for this narrow bridge; this is an internal
  FireClaw boundary between two already-existing FireClaw subsystems.

### Files Modified

- `src/fireclaw_core/memory/memory_retrieval.py`
  - Added `RagRetriever` protocol with `query(query, top_k)`.
  - Added `RagMemoryRetrieverAdapter`.
  - Adapter maps RAG hits to `RetrievedMemory` only when the hit contains
    FireClaw authority metadata: `record_id`, `mission_id`, `runtime_mode`,
    and `sensitivity`.
  - Adapter fail-closes plain RAG corpus chunks such as manual chunks without
    mission-memory identity.
  - Adapter deduplicates by `record_id` and preserves RAG scores/source labels.

- `tests/test_memory_retrieval.py`
  - Added fake RAG hit/retriever helpers.
  - Added coverage for scoped mapping, out-of-scope filtering, plain corpus
    chunk rejection, and duplicate record handling.

### Verification

Commands executed:

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 \
  tests/test_memory_retrieval.py

PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q \
  src/fireclaw_core/memory/memory_retrieval.py tests/test_memory_retrieval.py

PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest \
  tests/test_memory_retrieval.py
```

Observed:

- Direct file execution exited 0 but is a no-op for pytest-style tests.
- `compileall`: PASS.
- `pytest`: not available in `/home/lpp/miniconda3/envs/py310`.

Lightweight direct runner over new adapter tests:

```text
test_deduplicates_rag_hits_by_record_id PASS
test_fails_closed_for_plain_rag_corpus_chunks PASS
test_filters_hits_outside_scope PASS
test_maps_scoped_rag_hits_to_retrieved_memory PASS
```

Static check:

```text
git diff --check: PASS
```

### Current Conclusion

The memory layer can now consume an existing RAG retriever as a candidate
source without adding a memory-owned vector index. The design keeps RAG
responsible for retrieval algorithms and keeps memory responsible for authority,
scope, redaction, and revalidation.

### Next Recommended Step

Design runtime configuration for choosing a concrete RAG backend:

- BM25-only local index path;
- dense index path plus provider/model path;
- optional hybrid/RRF/reranker wrapper;
- explicit choice of whether RAG is retrieving mission-memory records or
  external reusable knowledge/manual chunks.

Do not silently load ordinary manual RAG chunks as current mission memory; they
need a separate reusable-knowledge path or explicit grounding contract.

## 2026-07-27T15:05+08:00

### User Decision

User approved the EMEM-inspired direction and asked for direct implementation:

- keep mission memory as an authoritative typed store;
- reuse RAG for BM25, dense, hybrid, and reranking;
- do not add another memory-owned vector implementation;
- keep external fire knowledge in a distinct `source_kind`, even though both
  kinds may use the same retrieval pipeline.

### Implementation Completed

- Added `src/fireclaw_core/rag/runtime_retrieval.py`.
  - `RagRuntimeConfig` selects `bm25`, `dense`, `hybrid`, or
    `hybrid_rerank`.
  - `HybridRagRetriever` performs RRF over the existing dense and BM25
    retrievers.
  - `RerankingRagRetriever` applies the existing RAG reranker provider.
  - Provider configuration supports explicit BGE-M3/BGE-reranker model paths
    and deterministic fake providers for tests.
- Added `src/fireclaw_core/memory/rag_indexing.py`.
  - Projects authoritative mission records into RAG records with
    `source_kind=mission_memory`, `node_type`, `record_id`, `indexable`, and
    `clean_text`.
  - Calls the existing RAG BM25/dense index builders; it contains no vector
    implementation.
- Updated `RagMemoryRetrieverAdapter`.
  - Requires the expected `source_kind`.
  - Still validates mission ID, runtime mode, sensitivity, and record identity.
  - External knowledge is rejected from the mission-memory authority path even
    if it carries memory-like fields.
- Updated `MissionRuntimePaths` and `build_mission_agent_from_paths`.
  - `memory_rag` can replace planner text candidate retrieval while
    `memory_index` remains available for spatial, temporal, relation, and
    entity projections.
  - The planner still canonicalizes every returned ID from
    `MissionMemoryStore`.
- Updated mission CLI runtime options for all four RAG backends and provider
  model paths.
- Added focused tests for projection/indexing, hybrid fusion, reranking,
  namespace rejection, and runtime wiring.

### Verification

Passed:

```text
compileall over changed production and test modules
git diff --check
BM25/dense/hybrid/hybrid_rerank smoke test over one projected mission record
MissionRuntimePaths -> RagMemoryRetrieverAdapter -> authoritative record smoke
CLI RagRuntimeConfig construction smoke
12 focused adapter/index/runtime tests via a lightweight runner
```

Full pytest execution remains unavailable because neither the system Python nor
`/home/lpp/miniconda3/envs/py310` has `pytest` installed.

### Current Conclusion

Mission memory can now use the existing RAG subsystem as its retrieval backend.
RAG owns index construction, dense embeddings, BM25, fusion, and reranking.
Memory owns record authority, source namespace, mission/runtime/sensitivity
scope, redaction, and final canonicalization.

RAG indexes are derived artifacts. Call `build_memory_rag_indexes` after the
authority corpus changes (typically before runtime startup). Current mission
events remain available through the authoritative facade even before an
offline RAG rebuild, but the derived RAG candidate index does not update itself
incrementally.

## 2026-07-27T23:44:43+08:00

### Task Goal

Implement the next lifecycle layer requested by the user:

- trigger a derived RAG rebuild after successful terminal consolidation;
- validate authority/index versions;
- atomically switch to a complete immutable generation;
- retain and continue serving the previous valid generation on failure.

### Architecture Decision

- Reused FireClaw's existing `ConsolidationBoundary` and
  `MemoryConsolidationCoordinator` as the task/subtask terminal boundary.
- A successful `subtask_terminal` or `mission_terminal` consolidation invokes
  a post-boundary hook in the existing background worker.
- Did not make RAG authoritative for live robot state. Immediate observations
  still flow through authoritative memory, working memory, and structured
  SQLite projections. Managed RAG remains a derived semantic candidate layer.
- Checked CodeGraph for an OpenClaw memory-index generation/swap analogue. No
  directly reusable OpenClaw implementation surfaced in the indexed source, so
  this implementation extends the existing FireClaw consolidation and RAG
  boundaries instead of introducing a separate scheduler.

### Files Added

- `src/fireclaw_core/memory/rag_index_lifecycle.py`
  - `MemoryRagIndexLifecycleManager`.
  - SHA-256 authority source version.
  - Build signature covering schema, backend, namespace, and embedding model.
  - Immutable directories under `generations/<generation_id>/`.
  - Temporary `.building-*` directory and source JSONL snapshot.
  - BM25/dense load validation before publication.
  - Authority hash recheck after build.
  - Cross-process `index-build.lock`.
  - Atomic `current.json` publication using write, `fsync`, `os.replace`, and
    directory `fsync`.
  - Persistent `last-attempt.json`.
  - Failed builds preserve `current.json` and report
    `failed_using_previous`.
- `tests/test_memory_rag_index_lifecycle.py`
  - successful publication and hot reload;
  - source-staleness reporting;
  - failed build fallback;
  - runtime reload failure fallback;
  - authority mutation during build rejection;
  - unchanged source reuse;
  - generation metadata mismatch rebuild.

### Files Modified

- `src/fireclaw_core/rag/runtime_retrieval.py`
  - `RagRuntimeConfig.generation_root`.
  - `ReloadingRagRetriever` checks `current.json` on query/status.
  - New generation loads fully before replacing the in-memory retriever.
  - Pointer/manifest source version and generation identity are cross-checked.
  - Managed paths must stay inside the selected immutable generation.
  - Reload failure retains the last good in-memory retriever.
- `src/fireclaw_core/memory/consolidation_coordinator.py`
  - optional `post_boundary_hook`;
  - called only after a boundary is `completed` or
    `covered_without_episode`;
  - hook failure does not roll back successful memory consolidation.
- `src/fireclaw_core/mission/mission_runtime.py`
  - performs startup refresh/validation for managed RAG;
  - shares the configured embedding provider between build and retrieval;
  - wires terminal consolidation to managed refresh;
  - requires `embodied_runtime_mode` for automatic managed refresh.
- `src/fireclaw_core/mission/mission_cli.py`
  - added `--memory-rag-generation-root`;
  - exposed the same RAG options on the persistent `serve` command;
  - supports flat `fireclaw.toml` keys with the same option names converted to
    underscores.
- `src/fireclaw_core/gateway/serve.py`
  - accepts and forwards `RagRuntimeConfig`, making lifecycle refresh usable in
    the long-running MissionGateway.
- Updated focused consolidation, runtime, and RAG tests.

### Runtime Contract

Managed lifecycle is enabled with:

```text
--embodied-runtime-mode simulation
--memory-rag-backend bm25
--memory-rag-generation-root <data-dir>/memory-rag-generations
```

For dense/hybrid backends, the existing embedding provider/model options are
also required. `hybrid_rerank` additionally requires the existing reranker
provider/model options.

Refresh policy:

```text
runtime startup -> validate/reuse or build
subtask terminal consolidation -> refresh (hash-deduplicated)
mission terminal consolidation -> refresh (hash-deduplicated)
build/load failure -> keep previous current generation
authority changed during build -> reject candidate generation
```

### Verification

Passed:

```text
67 focused test functions/methods via dependency-free runner
managed BM25/dense/hybrid/hybrid_rerank build and query smoke
MissionRuntimePaths managed generation startup/retrieval smoke
serve --help option wiring smoke
compileall over changed source and focused tests
git diff --check
```

Formal pytest remains unavailable:

```text
/home/lpp/miniconda3/envs/py310/bin/python3.10: No module named pytest
```

### Current Conclusion

The requested lifecycle path is implemented. A terminal boundary now causes a
background, versioned refresh; only a complete and loadable generation becomes
current; readers hot-reload after the atomic pointer switch; and both build
failures and reload failures continue using the previous valid generation.

No commit was made. Unrelated pre-existing untracked files were not modified.
