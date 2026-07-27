# SQLite R*Tree Spatial Projection Implementation

## 2026-07-21T00:12:09+08:00

### Task Goal

Implement the previously designed SQLite R*Tree candidate index for FireClaw
mission-memory nearest queries without changing the existing conservative
spatial semantics or safety/advisory boundary.

### Current Progress

Implementation is complete for `MissionMemoryFacade.query_nearest()`. Healthy
queries use `candidate_backend=sqlite_rtree`; unsupported, stale, corrupt, or
incompletely hydrated projections discard all indexed candidates and run the
existing deterministic linear scan.

### Files Added

- `src/fireclaw_core/memory/spatial_projection.py`
- `tests/test_spatial_rtree_projection.py`
- `docs/architecture/spatial-rtree-projection-design.md`

Earlier uncommitted work in the same batch also added:

- `docs/architecture/payload-entities-schema.md`
- `examples/embodied_memory/entity_memory_flow_demo.py`

### Files Modified

- `src/fireclaw_core/memory/memory_index.py`
- `src/fireclaw_core/memory/embodied_memory.py`
- `src/fireclaw_core/memory/entity_memory.py`
- `src/fireclaw_core/memory/mission_memory_facade.py`
- `src/fireclaw_core/mission/mission_memory.py`
- `tests/test_entity_memory.py`
- `docs/architecture/embodied-memory-event-production.md`
- `docs/architecture/spatial-rtree-projection-design.md`

### Implemented Behaviour

- Added ordinary `spatial_projection_meta` and `spatial_projection` tables.
- Added SQLite virtual tables `spatial_rtree_2d` and `spatial_rtree_3d`.
- Observation poses, Gist event poses, every Gist conservative geometry, and
  Entity current poses are projected independently.
- Uncertainty expands R*Tree candidate bounds; the existing exact matcher still
  computes circle/sphere/envelope distance and final ordering.
- Missing z never becomes `z=0`; z-less rows enter only the 2D index.
- Candidate queries isolate mission, runtime, frame, optional floor, memory
  type, and optional Entity kind/status.
- Candidate source records and Entity evidence are batch-hydrated from SQLite,
  avoiding a full JSONL scan on the healthy indexed path.
- JSONL snapshot `authority_token`, event/entity source tokens, row counts, and
  `rtreecheck()` protect projection integrity.
- A JSONL/index crash-window mismatch forces linear fallback until explicit
  JSONL-authoritative `rebuild_index()` repairs SQLite.
- Non-spatial events and relation appends advance existing authority tokens only
  after their SQLite update succeeds.
- Bulk rebuild defers spatial metadata recounting and commits once.
- Integrity validation is cached while metadata and SQLite `data_version` are
  unchanged. Internal writes invalidate the cache.
- Candidate SQL uses R*Tree-first `CROSS JOIN`; ordinary `JOIN` caused SQLite to
  scan `spatial_projection` first.
- Public results retain evidence IDs, restricted omission counts, freshness,
  `advisory_only`, `requires_revalidation`, and `can_authorize_action=False`.

### Verification

Commands run:

```bash
python3 -m compileall -q src tests examples/embodied_memory
git diff --check
PYTHONPATH=src python3 examples/embodied_memory/entity_memory_flow_demo.py
```

Both local Python environments lack pytest:

```text
/usr/bin/python3: No module named pytest
/home/lpp/miniconda3/envs/py310/bin/python3.10: No module named pytest
```

A local lightweight collector executed the actual test functions and assertions
with temporary directories:

```text
PASS tests.test_memory_index: 27 tests
PASS tests.test_embodied_memory: 10 tests
PASS tests.test_entity_memory: 4 tests
PASS tests.test_spatial_rtree_projection: 5 tests
PASS total: 46 tests
```

The demo completed all nine stages and reported:

```text
nearest query: mode=exact_conservative_spatial_nearest
candidate backend: sqlite_rtree
memory_can_authorize_action=False
```

### Preliminary Performance Smoke Check

System Python 3.8 / SQLite 3.31.1, synthetic Observation projections:

| Rows | Bulk rebuild | First checked query | Warm candidate query |
|---:|---:|---:|---:|
| 1,000 | 0.40 s | 3.1 ms | 0.07 ms |
| 10,000 | 13.33 s | 25.9 ms | 0.39 ms |

The first query performs full count and `rtreecheck()` validation. Warm queries
reuse validation while metadata/data version remain unchanged. Before forcing
R*Tree-first join order, the 10,000-row warm query took about 12.7 ms.

These numbers are smoke measurements, not publication-quality benchmarks.

### Current Issues And Known Gaps

- `pytest` is not installed, so the official pytest CLI was not run.
- The repository declares Python >=3.11, but only Python 3.8 and 3.10 are
  locally available; the target deployment interpreter remains unverified.
- R*Tree acceleration is integrated into the unified `query_nearest()` path.
  The older standalone `query_spatial()`, spatial `query_gists()`, and spatial
  `query_entities()` branches still use their existing linear implementations.
- No committed 100,000-row benchmark harness or p50/p95/p99 report exists yet.
- Bulk projection build time can be improved further if expected missions
  routinely contain tens of thousands of geometries.

### Research Interpretation

R*Tree is engineering infrastructure, not a research contribution by itself.
The research claim would need to center on uncertainty-aware, auditable mission
memory and demonstrate safer or more successful planning under localization
noise and multi-robot evidence. Required evidence includes retrieval recall
equivalence, planning/task success, unsafe-action or near-miss rates, latency,
and ablations for uncertainty, Entity resolution, and memory revalidation.

### Next Recommended Step

Review the complete diff and commit this implementation as one scoped spatial
index change, or first split the earlier schema/demo work from the R*Tree code.
After that, add an official Python >=3.11 development environment with pytest
and run the full repository test suite. A benchmark harness is the next R*Tree
task; sensor-algorithm adaptation remains intentionally deferred until concrete
perception outputs are known.

## 2026-07-21T00:22:34+08:00 Session Close

The user asked to stop work for today. No additional code changes were
requested after the implementation and verification described above.

Final clarification recorded for future resumption:

- the original roughly 33 ms at 10,000 rows combined full per-query integrity
  validation (`COUNT` plus `rtreecheck`) with a poor SQLite join order;
- validation caching removed repeated full checks while preserving a full first
  check and invalidation on writes;
- rebuild improved from about 29.7 s to 13.3-13.7 s by deferring projection
  metadata recounting, disabling per-record commit, and finalizing once;
- the remaining roughly 12.7 ms warm query came from SQLite choosing
  `spatial_projection` as the outer table and effectively scanning all scoped
  metadata rows before R*Tree filtering;
- explicit R*Tree-first `CROSS JOIN` changed the plan to query the R*Tree
  bounding box first and then hydrate candidates by integer `spatial_id`,
  reducing the 10,000-row warm candidate query to about 0.39 ms;
- 0.39 ms is a warm candidate-index measurement, not end-to-end agent latency;
  the first integrity-checked query remains about 25.9 ms in this smoke test.

Worktree remains uncommitted. Preserve all current modified and untracked files.
On resume, inspect `git status` and this record before deciding whether to split
and commit the existing batch or continue with Python >=3.11/pytest validation.
