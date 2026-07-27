# Planner Memory Retrieval Isolation Review Fixes Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task by task. Do not use
> subagents unless the user separately authorizes delegation. Mark each
> checkbox only after its RED or GREEN evidence has actually been observed.

**Goal:** Repair the authority, facade, diagnostic, and plugin-chain defects
found in the review of the implemented Planner memory isolation boundary.

**Architecture:** Keep the existing `PlannerMemoryContextBuilder` boundary and
the approved design. Treat `MissionMemoryStore` JSONL records as the only
authority for current-mission content: retrieval and facade outputs contribute
candidate IDs, never Planner content. If authority cannot be read, omit the
affected memory while allowing planning to continue with a degraded,
content-free audit decision. Run memory plugins over the combined authorized
current-memory and reusable-knowledge candidates, then reauthorize every
plugin result and preserve current-mission quota priority.

**Tech Stack:** Python 3.10-compatible dataclasses and typing, existing JSONL
mission memory, SQLite retrieval projection, `MissionMemoryFacade`,
`MissionMemoryLifecycleStore`, `PluginRuntime`, dependency-free
pytest-compatible test functions.

**Approved design:**

`docs/superpowers/specs/2026-07-24-planner-memory-retrieval-isolation-design.md`

**Review evidence:**

`memory/2026-07-24/planner-memory-retrieval-isolation.md`, section
`2026-07-24 Review - Current HEAD 864d593`.

---

## Constraints And Non-Goals

- Do not redesign `MemoryRetrievalScope`, storage formats, lifecycle approval
  records, facade schemas, ANN/R*Tree behavior, or mission safety gates.
- Do not trust SQLite/retriever payloads, facade payloads, or plugin payloads
  as authority.
- Do not load a reusable knowledge record's `source_event_id` as Planner
  content.
- Do not include memory content or exception messages in warnings, audit
  records, or plugin failure logs.
- Memory retrieval fails closed; otherwise valid mission planning remains
  available and records `memory_context_degraded`.
- Keep current-mission records ahead of reusable knowledge when applying the
  final quota.
- Use `/home/lpp/miniconda3/envs/py310/bin/python3.10`.
- Do not create a Python 3.11 environment or install pytest.
- Do not commit unless the user separately and explicitly requests it.

## File Map

Modify:

- `src/fireclaw_core/memory/planner_memory_context.py`
- `src/fireclaw_core/plugin/plugin_runtime.py`
- `tests/test_planner_memory_context.py`
- `tests/test_plugin_runtime.py`
- `tests/test_mission_agent.py` only if the existing fail-open assertion does
  not cover the repaired authority failure path
- `memory/2026-07-24/planner-memory-retrieval-isolation.md`

Do not modify unrelated untracked spatial projection, entity schema, example,
or historical memory files already present in the worktree.

---

## Task 1: Make JSONL Authority Fail Closed And Content-Authoritative

**Files:**

- Modify: `tests/test_planner_memory_context.py:721`
- Modify: `src/fireclaw_core/memory/planner_memory_context.py:285-367`
- Verify: `tests/test_mission_agent.py:2594-2661`
- Verify: `tests/test_mission_agent.py:2845-2910`

- [ ] **Step 1: Replace the two authority-loss bypass expectations**

Change:

- `test_authority_lookup_failed_warning_when_list_records_raises`
- `test_authority_lookup_failed_when_mission_memory_is_none`

Both tests must assert:

- `result.memories == ()`;
- no indexed or facade candidate reaches Planner context;
- the result contains an authority/current-memory unavailability warning;
- `result.guard_decision().status == "allow"`;
- `result.guard_decision().reason == "memory_context_degraded"`.

At this task, retain the current warning code in the assertion if needed to
isolate authority behavior. Task 3 migrates the stable code names.

- [ ] **Step 2: Add an empty-authority-set/index-only test**

Add
`test_empty_authority_store_rejects_index_only_candidate`.

Arrange a fake retriever that returns a correctly scoped `RetrievedMemory`
while `MissionMemoryStore.list_records()` succeeds with an empty list.

Assert:

- the candidate is absent;
- `authority_record_missing` identifies only its record ID;
- no candidate content appears in `repr(result.warnings)` or
  `repr(result.guard_decision().details)`.

This distinguishes a valid empty authority store from an authority read
failure. Both fail closed, but only the missing-record case reports
`authority_record_missing`.

- [ ] **Step 3: Add a same-ID content-tampering test**

Add
`test_index_candidate_same_id_uses_authority_content`.

Arrange:

- one authoritative JSONL `MissionMemoryRecord` with payload
  `{"note": "AUTHORITY"}`;
- one fake retrieved item with the same `record_id` but payload
  `{"note": "FORGED INDEX CONTENT"}`.

Assert exactly one current-mission memory is admitted and its content is the
authoritative payload. Assert the forged text is absent from the complete
result representation.

- [ ] **Step 4: Run only the four authority tests and observe RED**

Use a dependency-free function runner:

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 - <<'PY'
import inspect
import runpy
import tempfile
from pathlib import Path

namespace = runpy.run_path("tests/test_planner_memory_context.py")
names = (
    "test_authority_lookup_failed_warning_when_list_records_raises",
    "test_authority_lookup_failed_when_mission_memory_is_none",
    "test_empty_authority_store_rejects_index_only_candidate",
    "test_index_candidate_same_id_uses_authority_content",
)
for name in names:
    function = namespace[name]
    with tempfile.TemporaryDirectory() as directory:
        function(Path(directory))
    print("PASS", name)
PY
```

Expected RED:

- authority-loss tests expose indexed/facade memories; and/or
- same-ID test returns forged projection content.

Do not edit production code until the failure is observed.

- [ ] **Step 5: Track authority availability separately from authority size**

In `PlannerMemoryContextBuilder.build()`:

- introduce an explicit `authority_available` boolean;
- set it only after `list_records(mission_id=...)` succeeds;
- treat a successful empty list as available with an empty authority map;
- leave it false when the store is absent or raises.

Never use truthiness of `authority_ids` to decide whether authority checks
apply.

- [ ] **Step 6: Canonicalize retriever candidates from the authority map**

For every `RetrievedMemory`:

1. Treat `item.record_id` as the only candidate claim.
2. Reject it with `authority_record_missing` when the ID is not in
   `authority_record_map`.
3. Pass `authority_record_map[item.record_id]` to `_canonical_record()`.
4. Append only that canonical object.
5. Route an authoritative `record_type == "correction"` to `corrections`,
   not `memories`.

Delete the retriever-payload reconstruction through
`MissionMemoryRecord(...)`. Do not inspect `item.content`,
`item.mission_id`, `item.record_type`, or `item.created_at` for Planner
admission.

If `authority_available` is false, do not admit any retriever candidate.

- [ ] **Step 7: Run the authority tests and observe GREEN**

Run the Step 4 command.

Expected: four `PASS` lines and exit code `0`.

- [ ] **Step 8: Verify planning remains fail open**

Invoke the existing tests:

- `test_builder_source_exception_does_not_block_planning`
- `test_memory_degradation_does_not_block_planning`

Expected:

- planning succeeds;
- exactly one memory-context decision is present;
- its status is `allow`;
- its reason is `memory_context_degraded`;
- no memory or exception text appears in audit details.

---

## Task 2: Reauthorize Real Facade Events And Route Corrections Correctly

**Files:**

- Modify: `tests/test_planner_memory_context.py:570-588`
- Modify: `tests/test_planner_memory_context.py:773-828`
- Modify: `src/fireclaw_core/memory/planner_memory_context.py:369-442`

- [ ] **Step 1: Add a real facade-only fresh-event test**

Add `test_real_facade_only_fresh_event_is_admitted_from_authority`.

Use the real `_embodied_store()` and `_facade()` helpers. Record an event with
a current UTC timestamp so it is inside the facade's context window. Construct
the builder with:

- `memory_retriever=None`;
- `mission_memory=store.evidence_store`;
- the real facade.

Assert the event appears exactly once in `result.memories`, has runtime mode
`real`, and has no `runtime_mode_mismatch` warning.

- [ ] **Step 2: Add a facade same-ID tampering test**

Add `test_facade_candidate_same_id_uses_authority_content`.

Use a mock facade that returns an existing authoritative event ID with forged
`payload`, `sensitivity`, `event_type`, and any invented `runtime_mode`.

Assert the final item is reconstructed only from the authoritative
`MissionMemoryRecord`; every forged field is absent.

- [ ] **Step 3: Add a facade correction-routing test**

Add `test_facade_correction_is_routed_to_corrections_once`.

Arrange a current-mission authoritative correction visible through the real
facade and no retriever. Assert:

- the ID appears exactly once in `result.corrections`;
- it does not appear in `result.memories`;
- the later correction-store pass does not duplicate it.

- [ ] **Step 4: Run the three facade tests and observe RED**

Use the Task 1 function runner with only these test names.

Expected RED:

- the real facade event is rejected because `_event_payload()` has no
  `runtime_mode`; and/or
- facade fields are trusted directly;
- facade corrections enter `memories` or disappear through shared dedupe.

- [ ] **Step 5: Treat facade output as candidate IDs only**

For each facade event:

1. Read only a valid non-empty `event_id`.
2. Resolve that ID through `authority_record_map`.
3. Reject a missing ID with `authority_record_missing`.
4. Call `_canonical_record()` on the authoritative record.
5. Ignore all facade-provided content and classification fields.

Do not add `runtime_mode` to `MissionMemoryFacade._event_payload()` merely to
make the old check pass. The stronger boundary is ID reauthorization.

When authority is unavailable, facade output must not enter Planner context.
The facade dependency may still be called for diagnostics, but all returned
IDs must remain inadmissible.

- [ ] **Step 6: Centralize current-record routing**

Add one small local/helper path that:

- accepts a canonical current-mission record;
- routes authoritative corrections to `corrections`;
- routes other records to `memories`;
- updates the shared ID set only after successful canonicalization and routing;
- applies `max_corrections` and defers final memory quota enforcement to the
  existing final quota stage.

Use this same routing path for retriever, facade, correction-store, and
enrichment records where practical. Do not introduce a new public API.

- [ ] **Step 7: Run the facade tests and observe GREEN**

Expected: all three new tests pass, plus
`test_facade_events_canonicalized_and_deduplicated_against_indexed` and
`test_facade_events_checked_against_authority_map` remain green.

---

## Task 3: Restore Stable Warning Codes And Availability Semantics

**Files:**

- Modify: `tests/test_planner_memory_context.py:628-718`
- Modify: `tests/test_planner_memory_context.py:721-828`
- Modify: `tests/test_planner_memory_context.py:1065-1089`
- Modify: `src/fireclaw_core/memory/planner_memory_context.py:240-298`
- Modify: `src/fireclaw_core/memory/planner_memory_context.py:366-466`

- [ ] **Step 1: Add an exact dependency-warning contract test**

Add `test_dependency_failures_use_stable_warning_codes`.

Exercise independent failures for:

- JSONL/current indexed memory authority;
- facade current context;
- correction query;
- reusable knowledge.

Assert the public warning-code set uses only:

- `current_memory_unavailable`;
- `current_context_unavailable`;
- `corrections_unavailable`;
- `reusable_knowledge_unavailable`.

Assert these obsolete internal codes are absent:

- `authority_lookup_failed`;
- `memory_retriever_failed`;
- `facade_query_failed`;
- `corrections_query_failed`.

Keep `source` and `exception_class` content-free so operators can still locate
the dependency.

- [ ] **Step 2: Add lifecycle availability tests**

Replace the configured-only interpretation in:

- `test_reusable_knowledge_available_flag`;
- `test_reusable_knowledge_available_true_with_lifecycle`.

Add
`test_reusable_knowledge_available_false_when_lifecycle_query_fails`.

Define the flag as:

- `True` only when `list_knowledge()` succeeded for this build, including a
  successful empty result;
- `False` when lifecycle is absent, not queried because runtime mode is absent,
  or raises.

- [ ] **Step 3: Add rejection-code tests**

Add focused tests proving:

- a plugin claim for a known current record that is invalid for the request
  produces `plugin_scope_mismatch`;
- a lifecycle/plugin knowledge candidate that exists but is revoked or
  runtime-inapplicable produces `knowledge_not_approved`;
- an unknown ID still produces `plugin_record_unverified`.

The warnings must not reveal record content, title, tags, or exception
messages.

- [ ] **Step 4: Run the new diagnostic tests and observe RED**

Expected RED:

- old dependency code names are present;
- lifecycle failure reports availability as true;
- scope/approval failures collapse into `plugin_record_unverified` or vanish.

- [ ] **Step 5: Map implementation failures to stable public codes**

Use stable public warning codes at the builder boundary:

- authority load or retriever failure -> `current_memory_unavailable`;
- facade failure -> `current_context_unavailable`;
- correction query failure -> `corrections_unavailable`;
- lifecycle failure -> `reusable_knowledge_unavailable`.

Do not expose implementation method names through the code field. Preserve
the content-free `source` field to distinguish `mission_memory`,
`memory_retriever`, `facade`, `corrections`, and `lifecycle`.

Avoid duplicate identical dependency warnings from the same failed operation.
Per-record rejections may still have their own record IDs and counts.

- [ ] **Step 6: Set availability after successful lifecycle access**

Initialize `reusable_knowledge_available = False`. Set it to `True` only
after `list_knowledge()` returns successfully.

- [ ] **Step 7: Distinguish unknown, scoped-out, and unapproved plugin claims**

Keep content canonicalization fail closed while choosing deterministic codes:

- no authoritative current record or knowledge ID -> `plugin_record_unverified`;
- authoritative current record exists but `_canonical_record()` rejects its
  mission/runtime/sensitivity -> `plugin_scope_mismatch`;
- knowledge exists but is not approved, is revoked, or does not apply to the
  request runtime -> `knowledge_not_approved`.

- [ ] **Step 8: Keep a non-admissible knowledge lookup map**

Call `list_knowledge(include_revoked=True)` once, then:

- store returned records in `knowledge_by_id` so a revoked plugin claim can be
  classified deterministically;
- add only records with `status == "approved"` and matching request runtime to
  `reusable_memories` and `canonical_map`;
- never pass revoked/runtime-inapplicable content to hooks, Planner context,
  warnings, audit, or logs.

The lifecycle API already filters by its configured runtime mode. A
request/runtime mismatch can therefore be classified only when the lifecycle
returns the record; otherwise the fail-closed result remains
`plugin_record_unverified`.

- [ ] **Step 9: Run the diagnostic tests and observe GREEN**

Also verify `PlannerMemoryContextResult.guard_decision().details` contains only
counts, sorted warning codes, boolean grants/availability, and no payload.

---

## Task 4: Remove Plugin Exception Messages From Operational Logs

**Files:**

- Modify: `tests/test_plugin_runtime.py:398-476`
- Modify: `src/fireclaw_core/plugin/plugin_runtime.py:313-366`

- [ ] **Step 1: Add a rendered-log redaction test**

Add a test under `TestPluginHookDiagnostics` that:

1. Attaches a temporary `logging.Handler` to
   `fireclaw_core.plugin.plugin_runtime`.
2. Registers a callback that raises
   `RuntimeError("restricted victim name")`.
3. Executes `run_memory_hooks_with_diagnostics()`.
4. Formats every captured `LogRecord`, including exception information if
   present.
5. Removes the handler in `finally`.

Assert:

- `restricted victim name` is absent from rendered logs;
- `record.exc_info is None`;
- plugin ID, hook type/name, and `RuntimeError` remain available;
- the diagnostic report still contains only `exception_class`.

- [ ] **Step 2: Invoke the single test directly and observe RED**

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 - <<'PY'
import runpy

namespace = runpy.run_path("tests/test_plugin_runtime.py")
case = namespace["TestPluginHookDiagnostics"]()
case.test_callback_exception_log_omits_exception_message_and_traceback()
print("PASS plugin log redaction")
PY
```

Expected RED: the formatted traceback contains the restricted exception
message and `LogRecord.exc_info` is populated.

- [ ] **Step 3: Emit structured content-free warning text**

In `_run_hooks_with_diagnostics()`:

- remove `exc_info=True`;
- include `type(exc).__name__` as a normal format argument;
- retain plugin ID, hook type, and hook name;
- never interpolate `str(exc)`, `repr(exc)`, callback payload, or traceback.

Do not change `PluginHookRun`, `PluginHookFailure`, or legacy list-return API
shapes.

- [ ] **Step 4: Run diagnostic and compatibility tests and observe GREEN**

Directly invoke:

- the new log-redaction test;
- `test_memory_hook_diagnostics_report_callback_exception_without_message`;
- `test_existing_memory_hook_api_keeps_list_shape_on_callback_exception`;
- `test_provider_hook_diagnostics_report_callback_exception_without_message`;
- `test_diagnostics_report_success_and_failure_mixed`.

Expected: all pass and test output contains none of the seeded secret strings.

---

## Task 5: Include Approved Reusable Knowledge In Filter And Rerank Hooks

**Files:**

- Modify: `tests/test_planner_memory_context.py:1184-1291`
- Modify: `tests/test_planner_memory_context.py:1575-1618`
- Modify: `tests/test_planner_memory_context.py:1731-1929`
- Modify: `src/fireclaw_core/memory/planner_memory_context.py:444-625`

- [ ] **Step 1: Add a plugin filter test for approved knowledge**

Add
`test_plugin_filter_can_remove_approved_reusable_knowledge`.

Arrange one current-mission memory and one approved reusable knowledge item.
Capture the hook payload and return only the current record ID.

Assert:

- the hook received both canonical candidates;
- the approved knowledge item is absent from the result;
- the current item remains;
- plugin-supplied fields cannot replace canonical content.

- [ ] **Step 2: Add a reusable-knowledge rerank test**

Add
`test_plugin_rerank_orders_approved_reusable_knowledge`.

Arrange two approved knowledge records and no current records. Return their
canonical IDs in reverse order. Assert final reusable knowledge order follows
the authorized rerank effect.

- [ ] **Step 3: Add mixed-list safety tests**

Add tests proving:

- a forged knowledge content replacement is ignored;
- revoked/runtime-inapplicable knowledge cannot be introduced by filter or
  rerank;
- current-mission items still consume final quota before reusable knowledge;
- corrections are not silently moved into the general memory list.

- [ ] **Step 4: Run the new plugin tests and observe RED**

Expected RED: filter/rerank payloads contain only current `memories`, so the
plugin cannot see or affect approved reusable knowledge.

- [ ] **Step 5: Run hooks over a combined authorized candidate list**

Before filter execution, construct a working list from:

1. canonical current-mission memories;
2. canonical approved reusable knowledge.

Pass this combined list as `payload["memories"]` to filter, rerank, and
provider enrichment hooks.

After every plugin effect:

- replace claims through `canonical_map`;
- drop or classify unknown/scoped-out/unapproved claims using Task 3 codes;
- partition canonical current and reusable records without trusting
  plugin-provided `memory_scope`;
- retain the plugin-returned order within each partition.

The final quota remains safety-prioritized:

1. current-mission memory up to `max_memories`;
2. reusable knowledge fills only the remaining slots.

Thus plugins can filter both classes and rank within them, but cannot use
cross-class ordering to evict authorized current-mission context in favor of
historical reusable knowledge.

- [ ] **Step 6: Run the plugin tests and observe GREEN**

Also rerun existing tests for:

- known-ID content replacement;
- forged approval;
- enrichment by authoritative record ID;
- enrichment by approved knowledge ID;
- revoked/runtime-mismatched knowledge rejection;
- sequential filter and rerank behavior;
- final current-before-reusable quota.

---

## Task 6: End-To-End Regression And Research-Validity Check

**Files:**

- Verify all files above
- Modify: `memory/2026-07-24/planner-memory-retrieval-isolation.md`

- [ ] **Step 1: Run all dependency-free Planner context tests**

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 \
  tests/test_planner_memory_context.py
```

Expected: every listed test prints `PASS`; summary reports zero failures.

- [ ] **Step 2: Invoke all module-level MissionAgent and runtime tests**

Use the existing dependency-free dynamic runner recorded in
`memory/2026-07-24/planner-memory-retrieval-isolation.md`. It must actually
call every module-level `test_*` function and supply a temporary `tmp_path`
when required.

Expected:

- zero failed test functions;
- memory source failure does not block planning;
- memory-context audit decision precedes validator decisions;
- audit metadata contains no memory content.

- [ ] **Step 3: Run focused PluginRuntime diagnostic methods**

Invoke every method in `TestPluginHookDiagnostics` directly with a fresh
instance where needed.

Expected:

- all methods pass;
- callback exception messages and tracebacks are absent from output;
- legacy hook APIs retain their existing result shapes.

- [ ] **Step 4: Run syntax and static boundary checks**

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q \
  src/fireclaw_core/memory \
  src/fireclaw_core/mission \
  src/fireclaw_core/plugin \
  tests/test_planner_memory_context.py \
  tests/test_plugin_runtime.py \
  tests/test_mission_agent.py \
  tests/test_mission_runtime.py

rg -n '\.retrieve\(' src tests

git diff --check
```

Expected:

- `compileall` exits `0`;
- every production retrieval call and every normal test call has `scope=`;
- only the intentional “requires explicit scope” test is unscoped;
- `git diff --check` is clean.

- [ ] **Step 5: Re-run the two original security reproductions**

Verify:

1. authority unavailable plus index-only candidate produces no Planner
   memory and a degraded allow decision;
2. matching authority ID plus forged index/facade payload returns only JSONL
   authority content.

Record exact output without including seeded sensitive content in persistent
logs.

- [ ] **Step 6: Inspect the final diff**

Confirm:

- no unrelated spatial/entity/example files changed;
- no storage or public facade schema changed;
- no plugin payload becomes canonical content;
- no source event is loaded for reusable knowledge;
- no memory failure changes action authorization;
- no warning, audit detail, or operational plugin log contains payload or
  exception-message text.

- [ ] **Step 7: Update the persistent execution record**

Append:

- timestamps for each task;
- RED failure observed;
- GREEN command and counts;
- exact files modified;
- any deviation from this plan and why;
- remaining test limitation: formal Python 3.11/pytest suite not run by user
  choice;
- engineering conclusion;
- research-validity conclusion.

Research-validity acceptance requires both:

- retrieval improvements cannot be attributed to stale/forged projection
  content;
- fresh facade context is demonstrably present when available and omitted
  deterministically when authority is unavailable.

---

## Final Acceptance Checklist

- [ ] Authority unavailable or empty never admits index/facade-only memory.
- [ ] Same-ID projection/facade tampering cannot replace JSONL content.
- [ ] Real facade-only fresh events are admitted through authority
  reauthorization.
- [ ] Facade and retriever corrections appear only in `corrections`, once.
- [ ] Stable warning codes match the approved design.
- [ ] Reusable knowledge availability means successful access, not
  configuration.
- [ ] Plugin exception messages and tracebacks never enter logs or audit.
- [ ] Filter/rerank hooks can process approved reusable knowledge.
- [ ] Every plugin effect is reauthorized after every hook.
- [ ] Current mission keeps final quota priority over reusable knowledge.
- [ ] Memory degradation leaves valid planning available.
- [ ] Focused tests, dynamic Agent/runtime tests, `compileall`, retrieval-scope
  scan, and `git diff --check` pass.
- [ ] Persistent memory records actual evidence and remaining limitations.
