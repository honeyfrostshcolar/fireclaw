# Planner Memory Retrieval Isolation Second Review Fixes Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` task by task. Do not use subagents unless the
> user separately authorizes delegation. Update this document's checkboxes
> immediately after each observed RED or GREEN result.

**Goal:** Close the remaining plugin reference-mutation authority bypass,
restore approved reusable-knowledge recall under revoked-record pressure,
correct availability and plugin-rejection diagnostics, and make the
dependency-free test evidence reproducible.

**Architecture:** Keep `PlannerMemoryContextBuilder` as the Planner admission
boundary and `MissionMemoryStore` JSONL as current-memory authority. Harden
the shared `PluginRuntime` so each callback receives a deep, independent
payload snapshot and every returned effect is detached from plugin-owned
objects. Query approved reusable knowledge separately from revoked diagnostic
records so revoked entries never consume the approved candidate quota.

**Tech Stack:** Python 3.10 standard library (`copy.deepcopy`,
`contextlib.contextmanager`), existing FireClaw plugin runtime, JSONL mission
memory, reusable-knowledge lifecycle store, dependency-free
pytest-compatible tests.

**Parent design:**

`docs/superpowers/specs/2026-07-24-planner-memory-retrieval-isolation-design.md`

**First repair plan:**

`docs/superpowers/plans/2026-07-24-planner-memory-retrieval-isolation-review-fixes.md`

**Second review evidence:**

`memory/2026-07-24/planner-memory-retrieval-isolation.md`, section
`2026-07-24 15:44 +08 - Review Of Review-Fix Implementation`.

---

## Reference And Constraints

The repository guide requests inspection of the OpenClaw analogue before
editing a corresponding FireClaw module. The current worktree has no
`openclaw/` directory, and CodeGraph returned no OpenClaw hook runner.
Record this absence rather than inventing an upstream pattern.

Current FireClaw production hook callers are:

- `PlannerMemoryContextBuilder`: memory `filter`, memory `rerank`, and provider
  `enrich_context`;
- `MissionGateway`: `tool_approval`.

Their current production payloads are dictionaries containing scalar values
and nested lists/dictionaries. The shared runtime, not each individual
caller, owns callback reference isolation.

Constraints:

- Do not change memory storage formats, knowledge approval/revocation formats,
  facade schemas, retrieval scope, safety gates, or final memory quotas.
- Do not solve the plugin bypass only by copying inside the Builder; that
  would leave multiple plugins and tool-approval hooks sharing nested
  references.
- Do not serialize through JSON as the copy mechanism. Hook payloads are typed
  as `dict[str, Any]`; JSON round-tripping would silently change tuples and
  reject otherwise copyable Python values.
- Callback copy failures fail closed for that hook and use the existing
  content-free failure diagnostic.
- Plugin exception messages, payloads, and tracebacks must remain absent from
  logs and audit.
- Approved current-mission content remains canonicalized from JSONL authority.
- Approved reusable knowledge remains canonicalized from lifecycle records.
- Keep current-mission final quota priority over reusable knowledge.
- Use `/home/lpp/miniconda3/envs/py310/bin/python3.10`.
- Do not create a Python 3.11 environment or install pytest.
- Do not commit unless the user separately and explicitly requests it.

## File Map

Modify:

- `src/fireclaw_core/plugin/plugin_runtime.py`
- `src/fireclaw_core/memory/planner_memory_context.py`
- `tests/test_plugin_runtime.py`
- `tests/test_planner_memory_context.py`
- `memory/2026-07-24/planner-memory-retrieval-isolation.md`
- this plan's checkboxes during execution

Verify:

- `src/fireclaw_core/mission/mission_gateway.py`
- `tests/test_mission_gateway.py`
- `tests/test_mission_agent.py`
- `tests/test_mission_runtime.py`

Do not modify unrelated untracked spatial projection, entity schema, example,
or historical memory files.

---

## Task 1: Isolate Every Plugin Callback From Caller And Peer References

**Files:**

- Modify: `tests/test_plugin_runtime.py:1-8`
- Modify: `tests/test_plugin_runtime.py:230-380`
- Modify: `tests/test_plugin_runtime.py:398-533`
- Modify: `src/fireclaw_core/plugin/plugin_runtime.py:1-40`
- Modify: `src/fireclaw_core/plugin/plugin_runtime.py:313-366`

- [ ] **Step 1: Remove the pytest import requirement from PluginRuntime tests**

Add a small standard-library context manager:

```python
from contextlib import contextmanager


@contextmanager
def _raises_value_error(match: str):
    try:
        yield
    except ValueError as exc:
        assert match in str(exc)
    else:
        raise AssertionError(f"Expected ValueError containing: {match}")
```

Replace the six `pytest.raises(ValueError, match=...)` blocks with
`_raises_value_error(...)`, then remove `import pytest`.

This is test infrastructure only. Do not change the production exception
contract.

- [ ] **Step 2: Add a nested caller-payload mutation test**

Add
`test_hook_callback_cannot_mutate_nested_original_payload`.

Arrange a callback that mutates:

```python
payload["memories"][0]["content"]["note"] = "PLUGIN FORGED CONTENT"
payload["memories"].append({"record_id": "plugin-added"})
```

and returns `None`.

After `run_memory_hooks_with_diagnostics()`, assert the caller's original
payload is byte-for-byte/equality unchanged and the report has no effects.

- [ ] **Step 3: Add a peer-plugin isolation test**

Register two callbacks for the same hook:

1. the first mutates its nested payload and returns `None`;
2. the second records what it sees and returns a normal effect.

Assert the second callback sees the original nested values and list length,
not the first plugin's mutation.

- [ ] **Step 4: Add returned-effect detachment tests**

Add:

- `test_hook_report_detaches_nested_effect_from_plugin_object`;
- `test_legacy_hook_result_detaches_nested_effect_from_plugin_object`.

The callback returns a dictionary retained by the test. Mutate that original
dictionary after hook execution. Assert neither the diagnostic report nor the
legacy returned list changes.

- [ ] **Step 5: Add copy-failure behavior test**

Create a caller-owned value whose `__deepcopy__()` raises
`RuntimeError("restricted copy detail")`.

Assert for the diagnostic API:

- the callback is not invoked;
- the failure contains only plugin ID, hook name, and `RuntimeError`;
- logs contain neither the exception message nor payload;
- the original payload is unchanged.

Assert the existing legacy API remains fail-open for callback failure and
returns no effect, matching its current callback-exception behavior.

- [ ] **Step 6: Run the five focused runtime tests and observe RED**

Load `tests/test_plugin_runtime.py` directly with `runpy.run_path()`; no pytest
shim should be needed after Step 1. Instantiate the relevant test class and
invoke the five new methods.

Expected RED on current production code:

- nested original payload changes;
- the second plugin observes the first plugin's mutation;
- nested effect content changes when the plugin-owned return object changes.

- [ ] **Step 7: Deep-copy input separately for every callback**

Import `deepcopy` from `copy`.

Inside `_run_hooks_with_diagnostics()`, create the payload snapshot inside
the per-plugin `try` block immediately before callback invocation:

```python
callback_payload = deepcopy(payload)
result = callback(callback_payload)
```

Do not reuse a snapshot between callbacks. Do not fall back to shallow copy if
`deepcopy` fails.

Copy failure follows the existing callback-exception path:

- skip that plugin;
- append `PluginHookFailure` with exception class only;
- log plugin ID, hook type/name, and exception class only;
- continue to later plugins.

- [ ] **Step 8: Detach successful plugin effects**

For dictionary results, deep-copy the returned result before storing it in
`PluginHookEffect`/`PluginHookRun`.

The copy must occur before any effect dictionary is returned to the caller.
If result copying fails, treat it as a content-free plugin failure and do not
store a partial effect.

Keep these contracts unchanged:

- `None` means no effect;
- diagnostic APIs record non-dict results as `TypeError`;
- legacy APIs raise `ValueError` for non-dict results;
- effect dictionaries retain their existing public shape.

- [ ] **Step 9: Run the focused runtime tests and observe GREEN**

Expected: all new isolation tests and all seven existing diagnostic/logging
tests pass without pytest or a shim.

---

## Task 2: Prove Builder Canonical Content Survives In-Place Plugin Mutation

**Files:**

- Modify: `tests/test_planner_memory_context.py:1552-1759`
- Verify: `src/fireclaw_core/memory/planner_memory_context.py:447-633`

- [ ] **Step 1: Add filter in-place mutation reproduction**

Add
`test_filter_in_place_mutation_returning_none_cannot_change_authority_content`.

Use a real authoritative event containing `AUTHORITY CONTENT`. The filter
callback changes nested content to `PLUGIN FORGED CONTENT` and returns
`None`.

Assert:

- final Planner content is exactly the authority content;
- forged content is absent from the complete result;
- no unauthorized record is added;
- no content appears in warnings or audit details.

- [ ] **Step 2: Add rerank and enrichment mutation reproductions**

Add:

- `test_rerank_in_place_mutation_cannot_change_authority_content`;
- `test_enrichment_in_place_mutation_cannot_change_authority_content`.

For enrichment, mutate both current-mission and reusable-knowledge objects
visible in the callback payload. Return `None`.

Assert both classes retain their canonical authority/lifecycle values.

- [ ] **Step 3: Add correction-list isolation test**

Corrections are not currently passed to the filter/rerank payload. Add a
regression assertion that plugin mutation of general memory cannot alter a
canonical correction object through shared nested references.

- [ ] **Step 4: Run the four Builder tests and observe RED**

Expected RED: at least the current filter reproduction returns
`PLUGIN FORGED CONTENT` with no warning.

- [ ] **Step 5: Run after Task 1 and observe GREEN without Builder copy logic**

The shared runtime fix should make these tests green. Do not add redundant
deep-copy code to every Builder hook call unless the runtime tests prove the
shared boundary cannot provide isolation.

Builder responsibilities remain:

- candidate IDs from plugin effects are claims;
- `_reauthorize_plugin_items()` resolves claims through untouched canonical
  maps;
- enrichment resolution reloads/canonicalizes authority records;
- final quotas and correction routing remain unchanged.

---

## Task 3: Preserve Approved Knowledge Quota Under Revoked-Record Pressure

**Files:**

- Modify: `tests/test_planner_memory_context.py:1067-1458`
- Modify: `src/fireclaw_core/memory/planner_memory_context.py:240-254`
- Modify: `src/fireclaw_core/memory/planner_memory_context.py:420-445`

- [ ] **Step 1: Add the real lifecycle crowding test**

Add
`test_revoked_knowledge_does_not_consume_approved_candidate_quota`.

Using the real lifecycle store:

1. create one older approved applicable knowledge record;
2. create and revoke 100 newer knowledge records;
3. build Planner context with `max_memories >= 1`.

Assert:

- the older approved record is present;
- no revoked record is present;
- `reusable_knowledge_available is True`;
- no source event is loaded as Planner content.

- [ ] **Step 2: Run the crowding test and observe RED**

Expected current output:

```text
APPROVED_QUERY_HAS_OLD True
MIXED_QUERY_HAS_OLD False
BUILDER_HAS_OLD False
```

- [ ] **Step 3: Restore an approved-only candidate query**

Use:

```python
approved_knowledge = self._lifecycle.list_knowledge(
    include_revoked=False,
    limit=MAX_PLANNER_MEMORIES,
)
```

Set `reusable_knowledge_available = True` only after this query succeeds.
Build `reusable_memories` and the initial `knowledge_by_id` from this approved
candidate set.

- [ ] **Step 4: Make revoked classification a non-quota diagnostic lookup**

If revoked IDs must remain distinguishable as `knowledge_not_approved`, run a
separate bounded `include_revoked=True` lookup and merge only its ID/status
records into the classification map.

Rules:

- the diagnostic lookup never determines approved candidates or their order;
- revoked entries never consume `MAX_PLANNER_MEMORIES`;
- diagnostic lookup failure does not discard an already successful approved
  candidate query;
- diagnostic-only records never enter `canonical_map`, hook payloads, Planner
  context, warnings, audit, or logs;
- an ID outside the bounded diagnostic lookup remains fail-closed as
  `plugin_record_unverified`.

Use the lifecycle API maximum (`500`) for the diagnostic lookup unless its
current contract changes. Do not add a new public lifecycle API in this
repair.

- [ ] **Step 5: Correct runtime-mode-none availability**

Add
`test_runtime_mode_none_reports_reusable_knowledge_unavailable_without_query`.

Use a lifecycle fake that raises if called. Assert:

- it is not called;
- result memories/corrections are empty;
- `reusable_knowledge_available is False`.

Change the early return to report false because no lifecycle access occurred
for that build.

- [ ] **Step 6: Add diagnostic-lookup failure test**

Use a fake lifecycle where:

- `include_revoked=False` succeeds with approved knowledge;
- `include_revoked=True` raises.

Assert approved knowledge remains available and admitted. The optional
classification failure must not convert a successful approved source into an
empty retrieval result.

- [ ] **Step 7: Run Task 3 tests and observe GREEN**

Also rerun existing tests for:

- approved cross-mission knowledge;
- revoked absence;
- runtime applicability;
- lifecycle failure;
- availability flags;
- current-mission quota priority.

---

## Task 4: Make Plugin Rejection Codes Semantically Accurate

**Files:**

- Modify: `tests/test_planner_memory_context.py:1460-1545`
- Modify: `src/fireclaw_core/memory/planner_memory_context.py:459-489`
- Modify: `src/fireclaw_core/memory/planner_memory_context.py:591-613`

- [ ] **Step 1: Add known-but-not-candidate filter claim test**

Arrange an authoritative, in-scope current record that is not returned by the
retriever/facade and therefore is absent from the filter candidate list. Have
a filter plugin inject its ID.

Assert:

- it is not admitted;
- warning is `plugin_record_unverified`;
- warning is not `plugin_scope_mismatch`.

Filter and rerank can remove/reorder their authorized input but cannot add an
arbitrary in-scope authority record that was not a candidate.

- [ ] **Step 2: Preserve the real scope-mismatch test**

Keep a current authoritative record that fails request runtime or sensitivity.
Have a plugin claim it and assert `plugin_scope_mismatch`.

- [ ] **Step 3: Add a shared claim-classification helper**

Use one private/local helper for filter, rerank, and enrichment rejection
classification:

For current-mission IDs:

1. absent from `authority_record_map` -> `plugin_record_unverified`;
2. present but `_canonical_record()` rejects mission/runtime/sensitivity ->
   `plugin_scope_mismatch`;
3. canonical and allowed, but not in filter/rerank candidate map ->
   `plugin_record_unverified`;
4. enrichment may admit the canonical record through its existing authority
   resolver.

For reusable knowledge IDs:

1. absent from approved and diagnostic maps ->
   `plugin_record_unverified`;
2. known revoked/runtime-inapplicable ->
   `knowledge_not_approved`;
3. approved and applicable but not in filter/rerank candidates ->
   `plugin_record_unverified`;
4. enrichment may admit approved applicable knowledge through its resolver.

Do not include plugin-provided IDs in warning details where the existing
content-free policy intentionally omits unknown claims.

- [ ] **Step 4: Run classification tests and observe GREEN**

Verify exact warning-code sets and ensure no payload/title/tag/exception text
appears in warnings or `guard_decision().details`.

---

## Task 5: Repair Dependency-Free Test Discovery And Evidence

**Files:**

- Modify: `tests/test_planner_memory_context.py:2549-end`
- Verify: `tests/test_plugin_runtime.py`
- Modify: `memory/2026-07-24/planner-memory-retrieval-isolation.md`

- [ ] **Step 1: Replace the stale manual Planner test list**

The file currently defines 75 tests but `_ALL_TESTS` lists 60. Replace the
manual list with deterministic discovery after all functions are defined:

```python
_ALL_TESTS = sorted(
    (
        value
        for name, value in globals().items()
        if name.startswith("test_") and callable(value)
    ),
    key=lambda function: function.__name__,
)
```

Before invoking a function, inspect its signature:

- no parameters -> call directly;
- exactly `tmp_path` -> provide a fresh temporary `Path`;
- otherwise fail with an explicit unsupported-signature error.

Do not silently skip unsupported tests.

- [ ] **Step 2: Prove self-runner discovery matches definitions**

Use `ast` or imported globals to assert:

```text
DEFINED == LISTED
OMITTED == 0
```

Run the file directly and confirm its total equals the number of module-level
`test_*` definitions.

- [ ] **Step 3: Make PluginRuntime diagnostics directly runnable**

After Task 1 removes the pytest import requirement, use `runpy.run_path()` and
invoke every method in `TestPluginHookDiagnostics` with a fresh instance.

Expected: no pytest shim and no `ModuleNotFoundError`.

- [ ] **Step 4: Correct the persistent record**

Append a timestamped correction stating:

- the earlier “32 pre-existing MissionAgent failures” were runner errors;
- the correct signature-aware result was 78/78;
- the old Planner self-runner covered 60/75, while the exhaustive runner
  covered 75/75;
- future counts come from the repaired discovery runner;
- historical future-dated timestamps are not treated as evidence.

Do not rewrite or erase the historical section; append a correction so the
audit trail remains reconstructable.

- [ ] **Step 5: Update this plan's checkboxes during execution**

This is part of the Superpowers process evidence. A task is complete only when
its RED and GREEN boxes are both supported by recorded command output.

---

## Task 6: Full Regression And Security Reproduction

**Files:**

- Verify all files above
- Modify: `memory/2026-07-24/planner-memory-retrieval-isolation.md`

- [ ] **Step 1: Run all Planner context tests through the repaired self-runner**

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 \
  tests/test_planner_memory_context.py
```

Expected: all discovered tests pass, zero omitted, zero failed.

- [ ] **Step 2: Run all PluginRuntime tests without pytest**

Use a dependency-free class/method runner that:

- discovers every `Test*` class;
- instantiates a fresh class per method;
- invokes every `test_*` method;
- supplies a temporary `tmp_path` only when declared;
- fails on unsupported signatures.

Expected: no pytest import/shim and zero failures.

- [ ] **Step 3: Run MissionAgent, runtime, and gateway regressions**

Use the same signature-aware runner for:

- `tests/test_mission_agent.py`;
- `tests/test_mission_runtime.py`;
- the plugin/tool-approval-related tests in `tests/test_mission_gateway.py`.

Expected:

- MissionAgent remains 78/78 unless new tests intentionally change the count;
- runtime remains 5/5 unless new tests intentionally change the count;
- deep-copying tool-approval payloads does not change approval decisions or
  public result shapes.

- [ ] **Step 4: Run static checks**

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q \
  src/fireclaw_core/memory \
  src/fireclaw_core/mission \
  src/fireclaw_core/plugin \
  tests/test_planner_memory_context.py \
  tests/test_plugin_runtime.py \
  tests/test_mission_agent.py \
  tests/test_mission_runtime.py \
  tests/test_mission_gateway.py

rg -n '\.retrieve\(' src tests

git diff --check
```

Expected:

- `compileall` exits `0`;
- all production and normal test retrieval calls use `scope=`;
- only the intentional explicit-scope failure test is unscoped;
- diff whitespace is clean.

- [ ] **Step 5: Re-run four security/validity reproductions**

1. Authority unavailable plus index-only candidate:
   no Planner memory; degraded planning remains allowed.
2. Same-ID retriever/facade tampering:
   only JSONL authority content reaches Planner.
3. Plugin nested in-place mutation returning `None`:
   authority/lifecycle content remains unchanged; peer plugin sees original
   payload.
4. One approved plus 100 newer revoked records:
   approved knowledge remains retrievable; no revoked record enters Planner.

- [ ] **Step 6: Inspect the final diff**

Confirm:

- no unrelated spatial/entity/example files changed;
- no storage or facade schema changed;
- every callback receives an independent payload snapshot;
- returned effects do not retain plugin-owned nested references;
- no plugin mutation can alter canonical maps;
- revoked records cannot consume approved candidate quota;
- availability and rejection codes match actual behavior;
- logs/audit remain content-free;
- memory degradation does not authorize actions or block valid planning.

- [ ] **Step 7: Update engineering and research conclusions**

Record:

- exact RED and GREEN outputs;
- exact test counts from discovery runners;
- files modified;
- any plan deviation and reason;
- remaining limitation that formal Python 3.11/pytest was not run by user
  choice.

Research-validity acceptance requires:

- plugin-enabled and plugin-disabled experiments use identical authoritative
  source content unless an authorized filter/rerank ID effect changes
  selection/order;
- approved knowledge recall is invariant to the number of revoked records
  within the tested lifecycle history;
- availability and warning metrics correspond to actual dependency access.

---

## Final Acceptance Checklist

- [ ] Nested plugin mutation cannot alter caller payload or canonical Planner
  content.
- [ ] One plugin cannot mutate what a peer plugin observes.
- [ ] Returned effects are detached from plugin-owned nested objects.
- [ ] Copy failures are content-free and skip only the affected plugin.
- [ ] Filter, rerank, enrichment, and tool-approval public shapes remain
  compatible.
- [ ] Approved reusable knowledge quota excludes revoked records.
- [ ] Runtime-mode-none availability is false when lifecycle is not queried.
- [ ] Scope, unverified, and unapproved warning codes are semantically exact.
- [ ] Planner self-runner discovers every module-level test.
- [ ] PluginRuntime tests run without pytest or a shim.
- [ ] MissionAgent/runtime/gateway regressions pass.
- [ ] All four security/validity reproductions pass.
- [ ] `compileall`, retrieval-scope scan, and `git diff --check` pass.
- [ ] Persistent memory contains actual RED/GREEN evidence and corrected test
  claims.

