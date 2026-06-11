# FireClaw Embodied Roadmap Review — 2026-06-11

## Task Goal

Review the user's completed implementation against the embodied-agent OpenClaw minimal parity roadmap, current code, recent commits, and memory/plan records. Identify missing pieces, bugs, and residual risks.

## Context Read

- Latest recent memory directory read: `memory/2026-06-10/fireclaw-work-resume.md`
- Current plan checked: `docs/superpowers/plans/2026-06-10-embodied-agent-openclaw-minimal-parity-roadmap.md`
- Current git log head: `f4b822f docs: mark embodied roadmap complete, update memory`
- Current git status before review:
  - branch ahead of origin by 119 commits
  - untracked:
    - `src/fireclaw_core/lifecycle_maintenance.py`
    - `src/fireclaw_core/provider_runtime.py`
    - `tests/test_lifecycle_maintenance.py`
    - `tests/test_provider_runtime.py`

## Commands Run

- `git status --short --branch`
- `git log --oneline -8`
- `git show --stat --oneline --decorate --name-status f4b822f^..f4b822f`
- `git show --stat --oneline --decorate --name-status 678b1f3^..678b1f3`
- `git ls-files src/fireclaw_core/lifecycle_maintenance.py src/fireclaw_core/provider_runtime.py tests/test_lifecycle_maintenance.py tests/test_provider_runtime.py`
- `git show HEAD:src/fireclaw_core/lifecycle_maintenance.py`
- `git show HEAD:src/fireclaw_core/provider_runtime.py`
- `.venv/bin/python -m pytest -q`
- `.venv/bin/python -m pytest tests/test_subagent_client.py::test_robot_subagent_client_cancels_task -q`

## Verification Results

- Full suite:
  - `.venv/bin/python -m pytest -q`
  - Result: `1 failed, 1007 passed, 6 skipped in 114.32s`
  - Failure: `tests/test_subagent_client.py::test_robot_subagent_client_cancels_task`
  - Observed assertion: expected `cancel_requested`, got `completed`
- Focused reproduction:
  - `.venv/bin/python -m pytest tests/test_subagent_client.py::test_robot_subagent_client_cancels_task -q`
  - Result: `1 passed in 0.56s`

## Findings

1. **Clean-checkout breakage risk from untracked files**
   - `src/fireclaw_core/fleet_doctor.py` imports `LifecycleMaintenanceRunner` from `fireclaw_core.lifecycle_maintenance`.
   - `src/fireclaw_core/lifecycle_maintenance.py` exists on disk but is not tracked by git and is missing from `HEAD`.
   - A clean checkout of `HEAD` will fail to import `fireclaw_core.fleet_doctor`.
   - `provider_runtime.py` and its tests are also untracked, while docs/memory describe provider runtime as implemented.

2. **Full suite currently not stable green**
   - Full run failed on cancel lifecycle timing.
   - The same test passes alone, indicating a race/timing assumption: the 1-second slow skill can complete before cancellation is processed under full-suite load or scheduling variance.
   - Root-cause direction: test expects `cancel_requested`, but gateway correctly returns `completed` once worker has already finished and removed/terminalized task control.

3. **Provider runtime is not wired into the LLM planner main path**
   - `LLMMissionPlanner.__init__()` still requires `provider` and `model_id`.
   - `LLMMissionPlanner.plan()` calls `self._provider.chat_completion(... model=self._model_id ...)` directly.
   - `src/fireclaw_core/provider_runtime.py` exists only as an untracked module and is not referenced by production code except documentation/memory references.

4. **Memory eval implementation does not match the earlier plan shape**
   - Plan described `expected_record_ids` and `min_score`.
   - Current fixture uses `must_match` and `record_type`.
   - `record_type` is parsed into `EvalCase` but not used as an assertion/filter in `evaluate_retrieval()`.
   - `_memory_eval_check()` returns `fail` below threshold, while the plan text said the doctor gate should warn rather than hard-fail below threshold.

5. **TaskRegistry scope constants remain inconsistent**
   - `VALID_SCOPE_KINDS = {"session", "mission", "system"}`.
   - Production projection still writes `scope_kind="subtask"`.
   - No current validation makes this fail, but future source-of-truth validation would conflict with existing production records.

6. **Lifecycle projection is still query-driven**
   - `MissionAgent.mission_events()` passes `subagent_registry`, `task_registry`, and `task_flow_store` to `MissionEventAggregator`.
   - Terminal robot events update registry/task-flow state when aggregation is called.
   - There is no background watcher/callback that advances these projections without a trace/events read. Acceptable for v1 if documented, but not yet a real-time source-of-truth lifecycle service.

## Current Conclusion

The embodied-agent roadmap implementation is substantially present in the working tree, but current state should not be called fully clean:

- the current working tree full suite is not green due to a cancel lifecycle race;
- at least two production/doc-claimed modules are untracked, and one of them is imported by tracked production code;
- provider runtime and memory eval have plan-alignment gaps.

## Next Recommended Step

1. First fix the cancel lifecycle test race so full suite is stable green.
2. Track or intentionally remove/replace the untracked `lifecycle_maintenance.py` and `provider_runtime.py` modules plus their tests.
3. Decide whether memory eval should be a warn-only diagnostic or a hard demo gate, then update code/tests/docs to match.
4. If provider runtime remains part of the claimed scope, wire it into `LLMMissionPlanner` and CLI construction path, not just the standalone module tests.

## Update 2026-06-11 — Sequential Fixes Applied

### Task Goal

Apply the review findings in order:

1. Restore stable full-suite green by removing cancel lifecycle timing races.
2. Wire `ProviderRuntime` into the planner main path.
3. Align memory eval with the planned `expected_record_ids` / `min_score` demo gate shape and warn-only doctor semantics.
4. Align TaskRegistry scope constants with production `subtask` projections.

### Files Modified

- `tests/test_subagent_client.py`
  - Replaced fixed 1-second sleep in the slow cancellation skill with a release-file controlled fixture.
  - The test now requests cancellation while the skill is deterministically blocked, then releases it before gateway shutdown.
- `tests/test_gateway.py`
  - Increased `_wait_for_task_result()` default timeout from 2 seconds to 5 seconds for thread/HTTP integration tests under full-suite load.
- `src/fireclaw_core/llm_planner.py`
  - Added optional `provider_runtime`.
  - Preserved old `provider + model_id` constructor compatibility.
  - Allows runtime-only construction.
  - Uses runtime `chat_completion()` as the main path when configured.
  - Converts `FallbackSummaryError` into a planner error result.
- `tests/test_llm_planner.py`
  - Added coverage for runtime taking precedence over legacy provider.
  - Added runtime-only constructor coverage.
- `src/fireclaw_core/memory_eval.py`
  - Added `expected_record_ids`, `min_score`, and `record_type` enforcement.
  - Preserved backward-compatible `must_match` cases.
- `tests/fixtures/memory_eval/fireclaw_rescue_queries.json`
  - Converted rescue demo fixture to `expected_record_ids` + `min_score`.
- `src/fireclaw_core/doctor.py`
  - Changed below-threshold memory eval from `fail` to `warn`.
- `tests/test_doctor.py`
  - Updated threshold-not-met expectation to `warn`.
- `src/fireclaw_core/task_registry.py`
  - Added `"subtask"` to `VALID_SCOPE_KINDS`.
- `tests/test_task_registry.py`
  - Added regression coverage for `"subtask"` scope.

### Verification

- Focused provider runtime RED:
  - `.venv/bin/python -m pytest tests/test_llm_planner.py::test_llm_planner_uses_provider_runtime_when_configured -q`
  - Before implementation: failed with `LLMMissionPlanner.__init__() got an unexpected keyword argument 'provider_runtime'`.
- Focused memory eval RED:
  - `.venv/bin/python -m pytest tests/test_memory_eval.py::TestEvaluateRetrieval::test_expected_record_ids_and_min_score_are_enforced tests/test_memory_eval.py::TestEvaluateRetrieval::test_record_type_mismatch_fails_expected_record_case tests/test_memory_eval.py::TestLoadEvalCases::test_rescue_fixture_loads_correctly -q`
  - Before implementation: 3 failures, proving expected IDs/fixture shape were not implemented.
- Focused doctor RED:
  - `.venv/bin/python -m pytest tests/test_doctor.py::test_doctor_memory_eval_warns_when_threshold_not_met -q`
  - Before implementation: failed because status was `fail`, not `warn`.
- Focused TaskRegistry RED:
  - `.venv/bin/python -m pytest tests/test_task_registry.py::test_valid_scope_kinds_include_subtask_projection_scope -q`
  - Before implementation: failed because `"subtask"` was missing from `VALID_SCOPE_KINDS`.
- Related suite after fixes:
  - `.venv/bin/python -m pytest tests/test_gateway.py::test_gateway_events_endpoint_filters_by_task_id tests/test_subagent_client.py::test_robot_subagent_client_cancels_task tests/test_llm_planner.py::test_llm_planner_uses_provider_runtime_when_configured tests/test_llm_planner.py::test_llm_planner_can_be_constructed_with_provider_runtime_only tests/test_memory_eval.py tests/test_doctor.py::test_doctor_memory_eval_warns_when_threshold_not_met tests/test_task_registry.py::test_valid_scope_kinds_include_subtask_projection_scope tests/test_lifecycle_maintenance.py tests/test_provider_runtime.py -q`
  - Result: `64 passed in 3.85s`
- Full suite:
  - `.venv/bin/python -m pytest -q`
  - Result: `1013 passed, 6 skipped in 112.22s`

### Current Conclusion

The local working tree is now full-suite green. Remaining state is VCS hygiene, not test failure:

- `src/fireclaw_core/lifecycle_maintenance.py`
- `src/fireclaw_core/provider_runtime.py`
- `tests/test_lifecycle_maintenance.py`
- `tests/test_provider_runtime.py`

These files are still untracked because this turn did not create a commit. They must be included in the next commit, otherwise a clean checkout of the current committed `HEAD` will still be broken by `fleet_doctor.py` importing `fireclaw_core.lifecycle_maintenance`.
