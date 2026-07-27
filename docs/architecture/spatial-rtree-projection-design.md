# FireClaw SQLite R*Tree Spatial Projection Design

## 1. Status

This document defines the implemented SQLite R*Tree candidate retrieval
contract for FireClaw mission memory. The indexed backend is active for
`MissionMemoryFacade.query_nearest()` when its integrity checks pass; otherwise
that query runs the deterministic linear fallback.

The existing deterministic linear scan remains the semantic authority for:

- exact `frame_id` and optional `floor` matching;
- 2D versus 3D fail-closed behaviour;
- conservative uncertainty distance;
- multiple Gist geometries;
- Entity current projection location;
- restricted-evidence filtering and omission counts;
- final ordering and result limits.

R*Tree may only reduce the number of records passed to this exact logic. It
must not change which records are returned.

## 2. Goals And Non-Goals

Goals:

1. Accelerate bounded spatial reads over Observation, Gist, and Entity memory.
2. Preserve exact equality with the current linear-scan result contract.
3. Keep JSONL events as authority and SQLite as a disposable projection.
4. Support multiple spatial geometries for one Gist.
5. Keep missing vertical position distinct from `z=0`.
6. Rebuild safely after schema changes, stale projection, or corruption.
7. Fall back to linear scan whenever indexed correctness cannot be established.

Non-goals:

- semantic/vector retrieval;
- coordinate-frame transformation;
- polygon geometry or route planning;
- authorization decisions inside the index;
- changing Entity identity resolution;
- removing the deterministic linear path.

## 3. Existing Boundaries Reused

The design extends existing FireClaw boundaries rather than introducing a
second spatial subsystem:

- `MissionMemoryStore` JSONL remains authoritative;
- `SqliteMemoryIndex` owns all SQLite schema and projection operations;
- `EmbodiedMemoryStore.append_event()` continues to persist evidence before
  updating derived SQLite state;
- `EntityMemoryService` remains authoritative for current Entity projection;
- `MissionMemoryFacade.query_nearest()` remains responsible for exact matching,
  access filtering, ranking, evidence, freshness, and advisory metadata;
- `_spatial_match()`, `_pose_spatial_match()`, and
  `_geometry_spatial_match()` remain the exact equivalence oracle.

The eMEM analogue uses an in-process `Rtree` index, bounding-box candidate
lookup, and Python Euclidean-distance filtering. FireClaw reuses the two-stage
query shape but uses SQLite R*Tree so the index shares the existing persistence,
transaction, rebuild, and lifecycle boundary.

## 4. Conceptual Model

The projection separates identity and geometry:

```text
spatial_projection
    says who/what/mission/frame/floor a geometry belongs to

spatial_rtree_2d and spatial_rtree_3d
    say where that geometry may exist
```

The tables join through one generated integer `spatial_id`:

```text
spatial_projection.spatial_id = spatial_rtree_*.spatial_id
```

One source can own several rows. A Gist with two conservative geometries has
two `spatial_projection` rows and two R*Tree entries, both pointing to the same
Gist `source_id`.

## 5. SQLite Schema

### 5.1 Projection Metadata

`spatial_projection_meta` records whether one mission/runtime projection was
built from the expected sources:

```sql
CREATE TABLE IF NOT EXISTS spatial_projection_meta (
    mission_id             TEXT NOT NULL,
    runtime_mode           TEXT NOT NULL,
    schema_version         TEXT NOT NULL,
    authority_token        TEXT NOT NULL,
    event_source_token     TEXT NOT NULL,
    entity_source_token    TEXT NOT NULL,
    projection_row_count   INTEGER NOT NULL,
    rtree_2d_row_count     INTEGER NOT NULL,
    rtree_3d_row_count     INTEGER NOT NULL,
    rebuilt_at             TEXT NOT NULL,
    PRIMARY KEY (mission_id, runtime_mode)
);
```

Initial `schema_version`:

```text
fireclaw-spatial-projection-v1
```

The event token follows the current Entity token pattern and is calculated over
immutable Observation/Gist source rows:

```text
spatial-events-v1:<count>:<max_rowid>
```

The Entity token is the token already used by `entity_projection_meta`. When a
query selects Entity memory, Entity projection must be current before the
spatial token is accepted.

`authority_token` is the current JSONL evidence-store snapshot token. It closes
the crash window between appending authority evidence and updating SQLite: if
JSONL changed without a successful index update, the token differs and the
facade refuses indexed candidates. A non-spatial event or relation does not
change spatial rows, but after its SQLite update succeeds it advances this
token for all existing mission/runtime projections.

Count fields detect incomplete table replacement. A token or count mismatch
invalidates the entire mission/runtime spatial projection; partial indexed
results must never be returned.

Bulk rebuilds insert projection rows with metadata refresh deferred, then call
`finalize_spatial_projection()` once. This avoids recounting all projection and
R*Tree rows after every source record.

### 5.2 Ordinary Spatial Metadata

```sql
CREATE TABLE IF NOT EXISTS spatial_projection (
    spatial_id              INTEGER PRIMARY KEY,
    mission_id              TEXT NOT NULL,
    runtime_mode            TEXT NOT NULL,
    memory_type             TEXT NOT NULL CHECK (
        memory_type IN ('observation', 'gist', 'entity')
    ),
    source_id               TEXT NOT NULL,
    geometry_source         TEXT NOT NULL CHECK (
        geometry_source IN (
            'event_pose',
            'conservative_geometry',
            'entity_current_pose'
        )
    ),
    geometry_index          INTEGER NOT NULL,
    frame_id                TEXT NOT NULL,
    floor                   TEXT,
    center_x                REAL NOT NULL,
    center_y                REAL NOT NULL,
    center_z                REAL,
    uncertainty_radius_m    REAL NOT NULL CHECK (
        uncertainty_radius_m >= 0.0
    ),
    has_z                   INTEGER NOT NULL CHECK (has_z IN (0, 1)),
    min_z                   REAL,
    max_z                   REAL,
    entity_kind             TEXT,
    entity_status           TEXT,
    UNIQUE (
        mission_id,
        runtime_mode,
        memory_type,
        source_id,
        geometry_source,
        geometry_index
    )
);

CREATE INDEX IF NOT EXISTS spatial_projection_scope_idx
ON spatial_projection (
    mission_id,
    runtime_mode,
    frame_id,
    floor,
    memory_type
);

CREATE INDEX IF NOT EXISTS spatial_projection_source_idx
ON spatial_projection (
    mission_id,
    runtime_mode,
    memory_type,
    source_id
);

CREATE INDEX IF NOT EXISTS spatial_projection_entity_filter_idx
ON spatial_projection (
    mission_id,
    runtime_mode,
    entity_kind,
    entity_status
);
```

Field semantics:

| Field | Meaning |
|---|---|
| `spatial_id` | Rebuildable integer used by SQLite R*Tree; not a public memory ID |
| `source_id` | Observation/Gist event ID or projected Entity ID |
| `memory_type` | Result type used by `query_nearest` |
| `geometry_source` | Exact matcher branch represented by this row |
| `geometry_index` | `0` for a single pose; payload array index for Gist geometry |
| `frame_id` / `floor` | Candidate isolation fields; exact logic checks again |
| `center_*` | Exact centre copied from the source geometry |
| `uncertainty_radius_m` | Pose uncertainty or Gist horizontal radius |
| `has_z` | Distinguishes absent z from a real `z=0` |
| `min_z` / `max_z` | Exact vertical envelope when a Gist supplies bounds |
| `entity_kind` / `entity_status` | Optional Entity candidate prefilters; exact Entity state checks again |

The projection does not store evidence, sensitivity authorization, freshness,
or result payloads. Those remain owned by the current source objects and facade.

### 5.3 2D R*Tree

Every valid projection row with x/y enters the 2D index:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS spatial_rtree_2d USING rtree(
    spatial_id,
    min_x,
    max_x,
    min_y,
    max_y
);
```

### 5.4 3D R*Tree

Only rows that the exact matcher can compare in 3D enter this index:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS spatial_rtree_3d USING rtree(
    spatial_id,
    min_x,
    max_x,
    min_y,
    max_y,
    min_z,
    max_z
);
```

Separate 2D and 3D tables avoid inventing `z=0` for records without vertical
information:

```text
2D query -> spatial_rtree_2d
3D query -> spatial_rtree_3d
```

## 6. Geometry Projection Rules

### 6.1 Observation Event Pose

For pose `(x, y, z?)` with uncertainty `u`:

```text
2D bounds:
min_x = x - u, max_x = x + u
min_y = y - u, max_y = y + u

3D bounds, only when z is present:
min_z = z - u, max_z = z + u
```

The box encloses the exact uncertainty sphere/circle. It may generate false
positive candidates at corners, but cannot omit an exact match.

### 6.2 Entity Current Pose

Entity rows use `FireClawEntity.current_pose`, not every historical location.
They follow the same bounds as an event pose. `entity_kind` and `entity_status`
are copied only for candidate filtering.

When mention/resolution events rebuild Entity projection, old Entity spatial
rows and new rows are replaced in the same SQLite transaction as
`entity_projection` and `entity_projection_meta`.

### 6.3 Gist Conservative Geometry

Each valid item in `payload.spatial_geometries` becomes a separate row. Its
`geometry_index` is its stable array index in the immutable Gist event.

Horizontal candidate bounds follow the exact matcher, which currently treats
the geometry as a circle:

```text
min_x = center_x - radius_m
max_x = center_x + radius_m
min_y = center_y - radius_m
max_y = center_y + radius_m
```

The payload's rectangular x/y `bounds` must not narrow this box while the exact
matcher uses `center + radius`; narrowing would create false negatives.

For 3D:

- valid `bounds.min_z/max_z` are used directly;
- otherwise a finite `center_z` creates `min_z=max_z=center_z`;
- otherwise the geometry is excluded from the 3D index but remains in 2D.

A Gist may also have an event pose. That pose receives a separate
`geometry_source='event_pose'` row because `_spatial_match()` currently accepts
the best match from both payload geometries and event pose.

### 6.4 Invalid Geometry

Projection rejects a row when required values are missing, boolean, non-finite,
or violate bounds. It records a rebuild diagnostic but never invents values.
The original event remains authoritative and available to linear scan.

## 7. Candidate Query

For a 2D query `(qx, qy)` and radius `r`, the query envelope is:

```text
query_min_x = qx - r
query_max_x = qx + r
query_min_y = qy - r
query_max_y = qy + r
```

Candidate SQL shape:

```sql
SELECT p.spatial_id,
       p.memory_type,
       p.source_id,
       p.geometry_source,
       p.geometry_index
FROM spatial_rtree_2d AS r
JOIN spatial_projection AS p
  ON p.spatial_id = r.spatial_id
WHERE p.mission_id = ?
  AND p.runtime_mode = ?
  AND p.frame_id = ?
  AND (? IS NULL OR p.floor = ?)
  AND p.memory_type IN (...)
  AND r.min_x <= ?
  AND r.max_x >= ?
  AND r.min_y <= ?
  AND r.max_y >= ?;
```

A 3D query uses `spatial_rtree_3d` and adds z intersection predicates.

Candidate SQL must not apply the public result `LIMIT`. Bounding-box false
positives or duplicate Gist rows could otherwise crowd out a valid exact match.
The bounded public limit is applied only after exact filtering and sorting.

Candidate source IDs are deduplicated before loading source objects. The exact
matcher then evaluates the complete source again, including every Gist geometry,
so the current best-match and `matched_spatial_geometry` semantics are retained.

### 7.1 Candidate Hydration Must Also Be Indexed

R*Tree acceleration would be mostly defeated if candidate IDs were followed by
`EmbodiedMemoryStore.list_events()`, because that method scans the complete
JSONL authority file. The indexed path therefore needs a bounded batch loader:

```text
SqliteMemoryIndex.load_records_by_ids(source_ids)
    -> complete MissionMemoryRecord dictionaries from memory_records
    -> EmbodiedMemoryEvent.from_mission_record(...)
```

`memory_records.content_json` already retains the complete event content, and
the row retains `record_id`, `mission_id`, `record_type`, `robot_id`,
`subtask_id`, and `created_at`, so an event can be reconstructed without a full
JSONL scan.

The batch loader must:

- use bound `IN` parameters and deterministic source-ID ordering;
- require every requested event candidate to exist exactly once;
- validate mission, runtime, selected event type, and event schema;
- reject malformed JSON or an event whose reconstructed identity differs;
- return no partial success: any missing/malformed candidate forces the whole
  query to the linear fallback.

JSONL remains authority because the SQLite copy is accepted only while the
projection source token and integrity checks are current. Audit, repair, and
rebuild continue to read JSONL.

Entity candidates do not use event hydration for the Entity payload. They are
resolved from the current token-validated `EntityMemoryService` projection.
Their evidence event IDs may be batch-loaded from `memory_records` for exact
restricted-evidence checks; any missing or malformed evidence also forces
linear fallback.

## 8. Exact Query Pipeline

`MissionMemoryFacade.query_nearest()` will conceptually execute:

```text
1. Validate access and query bounds.
2. Ensure selected Entity projection is current when Entity is requested.
3. Validate R*Tree capability and projection token/counts.
4. Ask R*Tree for candidate source IDs.
5. Batch-load current source events from token-validated `memory_records` and
   current Entities from `EntityMemoryService`, without scanning all JSONL.
6. Apply existing visibility/restricted-evidence filtering.
7. Apply existing _spatial_match/_pose_spatial_match exact logic.
8. Compute restricted omission counts without exposing hidden payloads.
9. Sort with the existing _SpatialMatch.sort_key plus type/ID tie-breakers.
10. Apply the public bounded result limit.
11. Build the existing advisory/evidence/freshness response envelope.
```

The indexed path must preserve the existing public
`retrieval_mode='exact_conservative_spatial_nearest'`. An internal or diagnostic
field may report `candidate_backend='sqlite_rtree'` versus `linear_scan`, but an
agent must not infer different authority from the backend.

## 9. Access-Control Boundary

R*Tree is not an authorization system.

- Candidate lookup may include restricted records.
- Restricted event and Entity evidence filtering remains in
  `MissionMemoryFacade`.
- `restricted_records_omitted` and `restricted_entities_omitted` must be exactly
  equal to the linear path.
- Candidate diagnostics must not expose hidden source IDs, coordinates, types,
  counts beyond the existing omission contract, or payloads.

The index may prefilter mission/runtime/frame/floor because these are query
isolation conditions. It may not make the final sensitivity decision.

## 10. Write And Rebuild Lifecycle

### 10.1 Observation/Gist Append

After JSONL evidence is durably appended, `SqliteMemoryIndex.upsert()` updates
`memory_records` and replaces that event's spatial rows in one SQLite
transaction:

```text
delete old event spatial R*Tree rows
delete old event spatial metadata rows
insert new metadata rows
insert 2D rows
insert eligible 3D rows
update projection metadata/token/counts
commit
```

Events are immutable, so replacement primarily supports deterministic rebuild
and idempotent index repair.

### 10.2 Entity Projection Replacement

`replace_entity_projection()` atomically replaces:

```text
entity_projection
entity_projection_meta
Entity-owned spatial_projection rows
Entity-owned spatial_rtree_2d rows
Entity-owned spatial_rtree_3d rows
spatial_projection_meta Entity token/counts
```

This prevents an Entity payload from reporting a new current pose while the
R*Tree still points to its old location.

### 10.3 Clear, Purge, Archive, And Rebuild

`SqliteMemoryIndex.clear()` deletes ordinary projection rows, both R*Tree
tables, and projection metadata without touching JSONL.

Existing full index rebuild and mission purge paths rebuild event spatial rows
from retained authority. Entity spatial rows remain absent until the current
Entity projection is rebuilt; a query requesting Entity memory must ensure that
projection first.

Spatial IDs are internal and may change after rebuild. Public source IDs and
query results must remain stable.

## 11. Capability And Failure Policy

The runtime performs an actual R*Tree table creation probe. Compile-option text
alone is not sufficient.

On open/rebuild, FireClaw also compares metadata counts with ordinary/R*Tree
row counts and, where supported by the linked SQLite version, requires
`rtreecheck('spatial_rtree_2d')` and `rtreecheck('spatial_rtree_3d')` to return
`ok`. These checks validate index structure; exact source-token validation is
still required for projection freshness.

The indexed path is unavailable when:

- SQLite reports `no such module: rtree`;
- schema initialization fails;
- schema version is unexpected;
- source token or row counts do not match;
- metadata or R*Tree rows are malformed/incomplete;
- candidate records or Entity evidence cannot be batch-hydrated completely;
- an R*Tree query raises `sqlite3.DatabaseError`;
- candidate completeness cannot be established.

In every case:

```text
discard partial indexed candidates
run the complete deterministic linear scan
report/log a bounded diagnostic
allow later projection rebuild
```

The query must never silently return an empty or partial result because the
index is unavailable.

## 12. Correctness Tests

The indexed path is enabled behind fail-closed integrity checks. Focused tests
cover projection shape, 2D/3D behaviour, multiple Gist geometries, Entity pose
replacement, restricted-result equivalence, indexed-versus-linear result
equality, stale-authority fallback, JSONL rebuild recovery, non-spatial event
token synchronization, and R*Tree-unavailable fallback. The following remains
the broader acceptance matrix for future property and deployment testing.

Required deterministic cases:

1. 2D point inside, outside, and exactly on the radius boundary.
2. Position uncertainty pulling an otherwise distant centre into range.
3. Bounding-box corner false positives removed by exact circle/sphere logic.
4. Exact mission/runtime/frame/floor isolation.
5. Query without floor versus query with an explicit floor.
6. 3D query excluding every record without z.
7. Real `z=0` remaining distinguishable from missing z.
8. Gist with multiple separated geometries, including result deduplication and
   the same best `matched_spatial_geometry`.
9. Gist event pose and payload geometry both present.
10. Entity current pose update removing the old spatial location.
11. Entity merge/split/resolution invalidating spatial rows correctly.
12. Entity kind/status candidate filters followed by exact current-state check.
13. Restricted Observation, Gist, and restricted-evidence Entity omission
    counts identical to linear scan.
14. Equal-distance deterministic tie ordering.
15. R*Tree unavailable, stale token, bad row count, and database-error fallback.
16. Dense mixed memory types cannot crowd out requested result types.
17. Many bounding-box false positives cannot crowd out exact matches before
    the public limit.
18. Rebuild produces the same public IDs, order, distances, evidence IDs, and
    advisory metadata as the pre-rebuild index.
19. Indexed candidate hydration does not call the full JSONL `list_events()`
    path during a healthy query.
20. Missing/malformed candidate event or Entity evidence row discards all
    indexed candidates and runs the complete linear fallback.

Property-style equivalence should use a fixed random seed to generate mixed
frames, floors, optional z, uncertainty radii, memory types, and Gist geometry
counts. For every generated query:

```text
indexed_result == linear_result
```

Comparison includes:

- result IDs and order;
- `center_distance_m`;
- `distance_to_uncertainty_m`;
- `spatial_match` source/dimensions;
- matched Gist geometry;
- restricted omission counts;
- evidence IDs and revalidation flags.

## 13. Performance Evaluation

Correctness gates performance. After equivalence passes, benchmark synthetic
mission sizes of at least:

```text
1,000
10,000
100,000 spatial projection rows
```

Report:

- p50/p95/p99 query latency;
- candidate row count and candidate-reduction ratio;
- linear versus indexed exact-match count;
- projection build/rebuild time;
- SQLite file-size increase;
- append/update overhead;
- Entity replacement cost;
- 2D versus 3D query cost;
- multi-area Gist scaling;
- fallback latency.

R*Tree is justified as the default only when it provides material latency or
CPU improvement at expected mission volumes while preserving exact recall.

Preliminary local development measurements on 2026-07-20, using system Python
3.8/SQLite 3.31.1 and synthetic Observation rows, produced:

| Projection rows | Bulk rebuild | First integrity-checked query | Warm candidate query |
|---:|---:|---:|---:|
| 1,000 | 0.40 s | 3.1 ms | 0.07 ms |
| 10,000 | 13.33 s | 25.9 ms | 0.39 ms |

These are engineering smoke measurements, not publication benchmarks. The
10,000-row warm result uses R*Tree-first `CROSS JOIN`; the previous planner
selected `spatial_projection` first and took about 12.7 ms. A reproducible
benchmark harness, 100,000-row run, percentile distributions, disk size, and
end-to-end facade comparison remain required before making performance claims.

## 14. Implementation Status

1. Completed: SQLite R*Tree capability probing and an inspectable availability flag.
2. Completed: schema creation/migration for metadata, ordinary projection, 2D R*Tree,
   and 3D R*Tree.
3. Completed: pure geometry-to-projection helpers with strict finite-value validation.
4. Completed: atomic event spatial-row replacement in `upsert()`.
5. Completed: atomic Entity spatial-row replacement in `replace_entity_projection()`.
6. Completed: candidate-query APIs returning server-internal source IDs only.
7. Completed: strict batch candidate/evidence hydration from `memory_records`.
8. Completed for `query_nearest()`: candidate integration while retaining exact
   matching and fallback.
9. Completed for the focused query contract: deterministic equivalence and
   failure-injection tests.
10. Pending: add a reproducible benchmark harness and collect full baseline data.
11. Completed: indexed candidate retrieval is selected automatically only when
    runtime capability and projection-integrity gates pass.

## 15. Research Interpretation

Engineering correctness requires exact no-loss retrieval, rebuildability,
runtime fallback, and access-control equivalence.

R*Tree itself is standard indexing infrastructure, not a publication-level
contribution. Research value would come from demonstrating that FireClaw's
uncertainty-aware, auditable spatial memory improves safe planning under noisy
localization and multi-robot evidence, with R*Tree preserving that behaviour at
operational scale.
