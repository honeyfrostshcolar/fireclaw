# Planner Memory Retrieval Isolation Third Review Fixes Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` task by task. Do not use subagents unless the
> user separately authorizes delegation. Update this document's checkboxes
> immediately after every observed RED or GREEN result; do not leave an
> executed checklist unchecked.

**Goal:** Prevent `rerank` from reintroducing candidates removed by `filter`,
make revoked-knowledge rejection diagnostics stable within the lifecycle
query boundary, and leave reproducible execution evidence.

**Architecture:** Keep `PlannerMemoryContextBuilder` as the admission boundary
and retain authoritative reconstruction from current-memory JSONL and the
knowledge lifecycle store. Replace the current build-wide plugin key
allowlist with a separate allowlist for each sequential stage: `filter`
receives the initial admitted candidates, while `rerank` can reference only
the candidates that survived `filter`. Keep enrichment authorization
independent because enrichment is explicitly allowed to add authoritative
records that were not already in the ranked list. Query up to the lifecycle
API's 500-record maximum only for revoked-status diagnostics; the approved
candidate query and final context quotas remain unchanged.

**Tech Stack:** Python 3.10 standard library, existing FireClaw plugin runtime,
JSONL mission memory, reusable-knowledge lifecycle store, and
dependency-free pytest-compatible tests.

**Parent design:**

`docs/superpowers/specs/2026-07-24-planner-memory-retrieval-isolation-design.md`

**Previous repair plans:**

- `docs/superpowers/plans/2026-07-24-planner-memory-retrieval-isolation-review-fixes.md`
- `docs/superpowers/plans/2026-07-24-planner-memory-retrieval-isolation-second-review-fixes.md`

**Third review evidence:**

`memory/2026-07-24/planner-memory-retrieval-isolation.md`, section
`2026-07-24 16:35 +08 - Latest Independent Review`.

---

## Reference And Constraints

The repository guide requests inspection of the OpenClaw analogue before
editing a corresponding FireClaw module. The current worktree has no
`openclaw-main/` directory, and CodeGraph returned no OpenClaw hook runner.
Record that absence; do not invent an upstream design.

Confirmed current behavior:

- authoritative content reconstruction is working;
- plugin input/output deep-copy isolation is working;
- approved knowledge is queried independently from revoked diagnostics;
- final current-memory and reusable-knowledge quotas are working;
- a `rerank` effect can still name a record removed by `filter`, because both
  stages currently authorize against one build-wide key set;
- the revoked diagnostic query currently requests 100 records, although the
  lifecycle API supports 500, so an older revoked ID can be classified as
  `plugin_record_unverified` instead of `knowledge_not_approved`.

Constraints:

- Do not change memory JSONL, knowledge lifecycle, facade, or hook payload
  schemas.
- Do not change `PluginRuntime` deep-copy behavior.
- Do not change the number or ordering of plugin callbacks.
- Preserve the current per-hook behavior in which all callbacks for one hook
  receive the same stage input and the last valid effect determines that
  hook's result.
- Do not let a `filter` or `rerank` effect add a candidate that was absent
  from that stage's input.
- Do not constrain `enrich_context` to filter/rerank stage keys; enrichment
  keeps its separate authoritative record-resolution path.
- Keep the approved reusable-knowledge query at
  `MAX_PLANNER_MEMORIES == 100`.
- Keep final memory quotas and current-memory priority unchanged.
- Never expose revoked content in plugin payloads, planner context, warnings,
  or logs.
- A revoked record older than the 500-record diagnostic window must still
  fail closed as `plugin_record_unverified`.
- Use `/home/lpp/miniconda3/envs/py310/bin/python3.10`.
- Do not create a Python 3.11 environment or install pytest.
- Do not commit unless the user separately and explicitly requests it.

## File Map

Modify:

- `src/fireclaw_core/memory/planner_memory_context.py`
- `tests/test_planner_memory_context.py`
- `memory/2026-07-24/planner-memory-retrieval-isolation.md`
- this plan's checkboxes during execution

Verify without modifying unless a regression requires it:

- `src/fireclaw_core/plugin/plugin_runtime.py`
- `tests/test_plugin_runtime.py`
- `tests/test_mission_agent.py`
- `tests/test_mission_runtime.py`
- `tests/test_mission_gateway.py`

Do not modify unrelated spatial projection, entity schema, example, or
historical memory files.

---

## Task 1: Enforce Stage-Scoped Filter And Rerank Candidate Authority

**Files:**

- Modify: `tests/test_planner_memory_context.py`
- Modify: `src/fireclaw_core/memory/planner_memory_context.py`

- [x] **Step 1: Add a current-memory filter-to-rerank escape test**

Add
`test_rerank_cannot_reintroduce_current_record_removed_by_filter`.

Arrange three authoritative current-memory records: `obs-a`, `obs-b`, and
`obs-c`.

- `filter` returns only `obs-a` and `obs-b`;
- assert the `rerank` callback payload contains only those two records;
- `rerank` returns its received records plus a forged reference to `obs-c`.

Assert:

- `obs-c` is absent from final planner memories;
- authoritative content for `obs-a` and `obs-b` is unchanged;
- the rejected `obs-c` reference emits the content-free
  `plugin_record_unverified` warning;
- no plugin-supplied content reaches context or warnings.

- [x] **Step 2: Add the equivalent reusable-knowledge escape test**

Add
`test_rerank_cannot_reintroduce_knowledge_removed_by_filter`.

Arrange approved reusable records including `knowledge-a` and
`knowledge-filtered`. Have `filter` remove `knowledge-filtered`, then make
`rerank` claim it.

Assert the rerank payload and final context exclude
`knowledge-filtered`, and the forged reintroduction is rejected as
`plugin_record_unverified`.

- [x] **Step 3: Observe focused RED**

Run the two new test functions with the existing dependency-free discovery
runner. Record the exact command, failing assertion, and observed final IDs
in the execution memory.

Expected RED before implementation: the record removed by `filter` can be
restored by `rerank`.

- [x] **Step 4: Replace the build-wide key allowlist with a helper**

In `PlannerMemoryContextBuilder.build()`:

- keep one immutable `canonical_map` for authority-content reconstruction;
- remove the mutable/global `candidate_keys` admission role;
- add or reuse a small helper that derives `(scope, record_id)` keys from a
  concrete stage input.

The helper must ignore malformed plugin items and must not mutate the
candidate list.

- [x] **Step 5: Authorize filter effects against filter input**

Immediately before running `filter`, create:

- `filter_candidates` from the admitted current and approved reusable
  candidates;
- `filter_allowed_keys` from exactly that list.

Pass every valid filter effect through `_reauthorize_plugin_items()` using
`filter_allowed_keys`.

All filter callbacks still receive identical peer-isolated snapshots of
`filter_candidates`; do not turn multiple callbacks into a sequential
pipeline in this task.

- [x] **Step 6: Authorize rerank effects against post-filter input**

After the final filter effect has been applied:

- rebuild `rerank_candidates` from the surviving current and reusable lists;
- derive `rerank_allowed_keys` from exactly `rerank_candidates`;
- use that list as the rerank payload;
- reauthorize every rerank effect against `rerank_allowed_keys`.

Do not reuse `filter_allowed_keys` for rerank.

- [x] **Step 7: Keep rejection classification fail-closed**

Update `_reauthorize_plugin_items()` to accept the stage-local
`allowed_keys`.

For an item whose key is not in the stage allowlist:

- return no record;
- classify a reusable ID known by the diagnostic lifecycle map to be
  non-approved as `knowledge_not_approved`;
- otherwise classify it as `plugin_record_unverified`.

Only after the key passes the stage allowlist may the builder resolve its
canonical record from `canonical_map`. A missing canonical entry must remain
`plugin_record_unverified`.

`plugin_scope_mismatch` remains available to enrichment's separate
scope-resolution path. Do not claim that filter/rerank can convert an
out-of-stage key into an authorized candidate by changing its scope label.

- [x] **Step 8: Preserve enrichment semantics explicitly**

Do not pass filter or rerank allowlists into
`_resolve_enrichment_record()` or `_resolve_enrichment_knowledge()`.
Enrichment may add an authoritative current record or an approved reusable
record by ID, subject to its existing authority, mission, runtime-mode,
status, and quota checks.

- [x] **Step 9: Observe focused GREEN**

Run:

- the two new escape tests;
- the existing sequential filter/rerank test;
- existing multi-filter and multi-rerank tests;
- existing scope mismatch and enrichment authorization tests.

Record exact pass counts. The two removed records must remain absent after
rerank, while existing filter/rerank ordering behavior stays unchanged.

---

## Task 2: Use The Lifecycle Maximum For Revoked Diagnostics

**Files:**

- Modify: `tests/test_planner_memory_context.py`
- Modify: `src/fireclaw_core/memory/planner_memory_context.py`

- [x] **Step 1: Add a query-bound contract test**

Add
`test_revoked_diagnostic_query_uses_lifecycle_max_without_changing_approved_quota`.

Use a lifecycle test double that records `list_knowledge()` arguments.
Assert the builder issues separate calls equivalent to:

```python
list_knowledge(include_revoked=False, limit=100)
list_knowledge(include_revoked=True, limit=500)
```

Also assert the diagnostic result does not add revoked records to approved
candidates and does not change final memory quotas.

- [x] **Step 2: Add an older-revoked classification test**

Add
`test_old_revoked_knowledge_within_diagnostic_window_is_not_approved`.

With the real lifecycle store, arrange:

- one revoked record older than 100 newer approved records;
- an enrichment effect that claims the revoked record by ID.

Assert:

- the revoked record is absent from final memories;
- the warning code is `knowledge_not_approved`;
- the warning does not contain revoked content;
- approved recall and planner-memory availability remain intact.

- [x] **Step 3: Observe focused RED**

Run the two new tests before implementation.

Expected RED:

- the recorded diagnostic query limit is 100 rather than 500;
- the older revoked record is classified as
  `plugin_record_unverified`.

Record both results in the execution memory.

- [x] **Step 4: Introduce an explicit diagnostic bound**

Near existing planner-memory limits, define an internal constant such as:

```python
MAX_KNOWLEDGE_DIAGNOSTIC_RECORDS = 500
```

Use it only for the `include_revoked=True` diagnostic query.

Keep the approved query and all final quotas on their existing constants.
Do not use the diagnostic result as candidate input.

- [x] **Step 5: Preserve failure and privacy behavior**

If the diagnostic query fails:

- retain already fetched approved candidates;
- keep availability based on usable planner-memory sources;
- classify unverifiable plugin claims fail-closed;
- emit only the existing content-free lifecycle diagnostic.

Document in the relevant code comment or test name that records outside the
500-record window remain `plugin_record_unverified`; do not add an unbounded
scan.

- [x] **Step 6: Observe focused GREEN**

Rerun both tests and record exact results.

Expected:

- approved query limit remains 100;
- diagnostic query limit is 500;
- the older revoked ID inside that window is
  `knowledge_not_approved`;
- no revoked record or content is admitted.

---

## Task 3: Run Regression And Security Evidence

**Files:**

- Verify: `tests/test_planner_memory_context.py`
- Verify: `tests/test_plugin_runtime.py`
- Verify: `tests/test_mission_agent.py`
- Verify: `tests/test_mission_runtime.py`
- Verify: `tests/test_mission_gateway.py`
- Update: `memory/2026-07-24/planner-memory-retrieval-isolation.md`
- Update: this plan

- [x] **Step 1: Run all planner-memory tests with discovery**

Use a standard-library runner that imports
`tests/test_planner_memory_context.py`, discovers every top-level callable
whose name starts with `test_`, invokes each test with a fresh `tmp_path`
when required, and fails nonzero on any error.

Do not paste a fixed list of test names. Record the discovered count and
passed count from the actual command.

- [x] **Step 2: Run the full PluginRuntime regression**

Run all discovered PluginRuntime tests using their existing dependency-free
runner. Confirm callback input isolation, peer isolation, effect detachment,
diagnostics, and legacy behavior remain green.

- [x] **Step 3: Run downstream agent/runtime/gateway regressions**

Run:

- all 78 currently discovered `MissionAgent` tests;
- all 5 currently discovered `MissionRuntime` tests;
- both gateway plugin-approval tests.

Treat those counts as expected baselines, but record the actual discovered
counts and do not hide newly discovered tests.

- [x] **Step 4: Repeat the two security reproductions**

Record compact outputs for:

1. `filter` removes `obs-c`, `rerank` claims `obs-c`:
   final IDs exclude `obs-c` and include a content-free rejection warning.
2. one revoked reusable record followed by 100 approved records:
   the revoked claim is rejected as `knowledge_not_approved`.

Also retain prior evidence that plugin nested mutation cannot alter
authority-owned content.

- [x] **Step 5: Run static verification**

Run:

```bash
/home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall \
  src/fireclaw_core/memory/planner_memory_context.py \
  src/fireclaw_core/plugin/plugin_runtime.py \
  tests/test_planner_memory_context.py \
  tests/test_plugin_runtime.py
```

Then inspect:

```bash
git diff --check
git status --short
git diff -- \
  src/fireclaw_core/memory/planner_memory_context.py \
  tests/test_planner_memory_context.py \
  docs/superpowers/plans/2026-07-24-planner-memory-retrieval-isolation-third-review-fixes.md \
  memory/2026-07-24/planner-memory-retrieval-isolation.md
```

Confirm no unrelated files were modified by this plan's execution.

- [x] **Step 6: Update execution evidence immediately**

As each RED/GREEN or regression command finishes:

- tick the corresponding checkbox in this document;
- append the actual local timestamp and exact command/result to
  `memory/2026-07-24/planner-memory-retrieval-isolation.md`;
- never estimate or future-date timestamps;
- record failures and rejected attempts, not only final passing results.

- [x] **Step 7: Perform final contract review**

Confirm:

- [x] rerank cannot reference any candidate removed by filter;
- [x] filter and rerank cannot forge canonical content;
- [x] enrichment still adds only authority-resolved current records or
      approved reusable knowledge;
- [x] revoked diagnostics do not consume approved recall quota;
- [x] approved lookup remains bounded at 100;
- [x] revoked diagnostic lookup is bounded at 500;
- [x] records outside the diagnostic window fail closed;
- [x] final quotas and current-memory priority are unchanged;
- [x] plugin diagnostics contain no payload, content, exception message, or
      traceback;
- [x] all executed checklist items contain observed evidence;
- [x] no unrelated worktree changes were overwritten or reverted.

---

## Completion Criteria

This repair is complete only when:

1. both new tests fail for the intended reasons before implementation;
2. stage-local filter and rerank allowlists make both tests pass;
3. the 500-record diagnostic query makes older revoked classification stable
   without changing approved recall or final quotas;
4. planner, PluginRuntime, MissionAgent, MissionRuntime, and gateway
   regressions pass with actual discovered counts recorded;
5. `compileall` and `git diff --check` pass;
6. this plan's executed checkboxes and the persistent memory record match the
   observed commands and timestamps.
