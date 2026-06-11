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

## Update 2026-06-11 — OpenClaw Comparison and Field-Readiness Replan

### Task Goal

Re-check FireClaw after commit `9033a92`, compare it against OpenClaw only for embodied-agent-relevant capabilities, decide whether the intended ROS1-first firefighting robot agent loop is covered, and create the next focused implementation plan.

### Context Read

- Current git head: `9033a92 fix: stabilize embodied roadmap runtime checks`
- Current git status at start:
  - `master...origin/master [ahead 120]`
  - clean before creating the new plan/memory updates
- Recent memory:
  - `memory/2026-06-11/fireclaw-embodied-roadmap-review.md`
  - `memory/2026-06-10/fireclaw-work-resume.md`
- Current roadmap:
  - `docs/superpowers/plans/2026-06-10-embodied-agent-openclaw-minimal-parity-roadmap.md`
- Architecture docs:
  - `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`
  - `docs/architecture/fireclaw-openclaw-alignment.md`

### OpenClaw References Inspected

Used CodeGraph against `/home/nankai/fireclaw/openclaw-main`.

Relevant OpenClaw patterns:

- `src/tasks/task-registry.store.ts`
- `src/tasks/task-flow-registry.store.ts`
- `src/acp/session-lineage-meta.ts`
- `src/agents/acp-spawn.ts`
- `src/plugins/plugin-control-plane-context.ts`
- `src/agents/model-fallback.ts`
- `src/agents/bash-tools.exec-approval-followup-state.ts`
- `extensions/memory-core/src/memory/search-manager.ts`
- `extensions/memory-core/src/memory/qmd-manager.ts`
- `src/acp/translator.ts`

### Current Assessment

FireClaw now covers the intended ROS1-first embodied-agent v1 loop:

```text
operator command
-> planner with retrieved memories and operator corrections
-> provider/runtime fallback boundary
-> plugin provider/memory/tool-approval hooks
-> safety and approval gate
-> mission scheduler
-> robot subagent dispatch
-> robot-local task/action runtime
-> ROS1 topic/service/action transport
-> unified event replay/SSE
-> lifecycle projection, task-flow summary, session lineage, memory recording
```

This is enough for a code-level embodied-agent v1. It is not yet enough for a strong field/simulation experiment claim, because several capabilities exist as library/test wiring but are not fully exposed as repeatable operator/deployment workflows.

### Key Findings

1. **Deployment runtime assembly gap**
   - `MissionAgent` supports memory retriever, task registry, subagent registry, session lineage, task-flow store, approval store, plugin runtime, and provider runtime.
   - `mission_cli.py` still builds mostly bare agents and does not expose paths for all these runtime stores.
   - Result: tests can exercise the rich runtime, but a real operator CLI run cannot yet reproduce the same stateful behavior without custom Python construction.

2. **Lifecycle automation gap**
   - `LifecycleMaintenanceRunner` is visible through fleet doctor.
   - There is no explicit CLI/daemon-style command for pre/post-run lifecycle reconciliation and cross-process recovery reporting.

3. **End-to-end embodied scenario gate gap**
   - Unit/integration tests cover individual pieces.
   - There is no single scenario gate proving `operator command -> mission -> robot gateway -> events -> task-flow -> lineage -> memory` in one workflow.

4. **Memory indexing automation gap**
   - `MemoryRetriever`, `SqliteMemoryIndex`, and `memory_eval.py` exist and are wired into doctor.
   - There is no operator-facing indexing/eval CLI that can be used as a demo/CI gate.

5. **ROS1 proof packaging gap**
   - ROS1 smoke artifacts and runbooks exist.
   - There is no single proof bundle command that collects doctor output, smoke artifacts, and redacted evidence for a hardware/high-fidelity sim run.

6. **Documentation truth gap**
   - Some docs still contain stale next-step wording, such as treating session lineage/resume guard as remaining work even though it is implemented.

### New Plan Created

- `docs/superpowers/plans/2026-06-11-embodied-agent-field-readiness-roadmap.md`

Plan tasks:

1. Add a deployable mission runtime factory and CLI path wiring.
2. Add lifecycle maintenance CLI and explicit recovery report.
3. Add an end-to-end embodied mission scenario gate.
4. Add memory indexing and eval CLI gate.
5. Add ROS1 proof bundle command.
6. Run documentation truth pass and scope cleanup.

### Scope Decision

Required for embodied-agent field readiness:

- deployable runtime assembly;
- explicit lifecycle maintenance command;
- end-to-end embodied mission gate;
- memory index/eval CLI gate;
- ROS1 proof bundle;
- real robot/high-fidelity simulation execution.

Not required now:

- ROS2 native adapter;
- full OpenClaw ACP/IDE platform;
- third-party plugin marketplace;
- arbitrary untrusted plugin loading;
- full WebSocket/operator Web UI;
- generic coding-agent task-flow UX.

### Verification

- `.venv/bin/python -m pytest -q`
  - Result: `1013 passed, 6 skipped in 113.63s`

### Next Recommended Step

Start with Task 1 in `docs/superpowers/plans/2026-06-11-embodied-agent-field-readiness-roadmap.md`. It closes the biggest practical gap: making the rich MissionAgent runtime available through deployable CLI paths instead of only direct Python construction and tests.

## Required For Embodied-Agent Field Readiness

- deployment runtime assembly (Task 1 ✅)
- lifecycle maintenance command (Task 2 ✅)
- e2e embodied scenario gate (Task 3 ✅)
- memory index/eval CLI gate (Task 4 ✅)
- ROS1 proof bundle (Task 5 ✅)
- real robot/high-fidelity simulation execution

## Optional Platform Work

- ROS2 native adapter
- WebSocket/full Web UI
- full OpenClaw ACP/IDE parity
- marketplace/dynamic plugin sandboxing
