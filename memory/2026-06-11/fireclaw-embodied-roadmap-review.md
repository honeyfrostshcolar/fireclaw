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

## Update 2026-06-11 11:46 CST — Field Readiness Implementation Review

### Task Goal

Review the user's implementation of the 2026-06-11 field-readiness roadmap against the plan, recent commits, current code, and tests. Identify missing pieces or problems before claiming the roadmap is fully complete.

### Context Read

- Current git head range since previous review:
  - `72ad2a6 feat: assemble deployable mission runtime from CLI paths`
  - `b54587f feat: assemble deployable mission runtime from CLI paths`
  - `071ae23 feat: expose lifecycle maintenance from mission CLI`
  - `a740cf5 test: add embodied mission end-to-end scenario gate`
  - `7934726 feat: add memory indexing and eval CLI gate`
  - `323caf7 feat: create ROS1 proof bundle artifacts`
  - `067f805 docs: reframe FireClaw embodied-agent field readiness roadmap`
- Current git status:
  - branch ahead of origin by 127 commits
  - untracked: `docs/superpowers/plans/2026-06-11-embodied-agent-field-readiness-roadmap.md`
- CodeGraph status:
  - healthy, 152 files indexed, 3280 nodes, 8235 edges.

### Commands Run

- `git show --stat --oneline 9033a92..HEAD`
- `git status --short --branch`
- `rg -n "\\[ \\]|TODO|FIXME|still|not yet|未完成|待|823 passed|1008 passed|session lineage.*next|provider_runtime.py does not exist|ROS2 adapter 实现|full OpenClaw platform parity" docs/superpowers/plans/2026-06-11-embodied-agent-field-readiness-roadmap.md docs README.md memory/2026-06-11/fireclaw-embodied-roadmap-review.md`
- `.venv/bin/python -m pytest tests/test_mission_runtime.py tests/test_mission_cli.py tests/test_embodied_mission_e2e.py tests/test_memory_cli.py tests/test_ros1_proof_bundle.py -q`
- `.venv/bin/python -m pytest -q`
- `.venv/bin/python -m pytest tests/test_gateway.py::test_gateway_cancels_active_task_between_skills -q`
- Manual memory CLI behavior reproduction:
  - index JSONL with record `old`;
  - replace JSONL with record `new`;
  - run `memory_cli index` again on the same SQLite file;
  - eval query for `old`.

### Verification Results

- Roadmap-focused tests:
  - `.venv/bin/python -m pytest tests/test_mission_runtime.py tests/test_mission_cli.py tests/test_embodied_mission_e2e.py tests/test_memory_cli.py tests/test_ros1_proof_bundle.py -q`
  - Result: `28 passed in 9.06s`
- Full suite:
  - `.venv/bin/python -m pytest -q`
  - Result: `1 failed, 1019 passed, 6 skipped in 119.20s`
  - Failure: `tests/test_gateway.py::test_gateway_cancels_active_task_between_skills`
  - Assertion: `elapsed < 1.5`; observed `1.5332807240192778`
- Focused rerun:
  - `.venv/bin/python -m pytest tests/test_gateway.py::test_gateway_cancels_active_task_between_skills -q`
  - Result: `1 passed in 2.43s`
  - Conclusion: this is a full-suite-load timing flaky; cancel behavior reached `cancelled`, but the hard elapsed threshold is unstable.
- Memory CLI stale-index reproduction:
  - After second indexing run with JSONL no longer containing `old`, `memory_cli eval` still found `old` in SQLite and exited `0`.
  - Conclusion: `memory_cli index` currently performs additive `upsert()` indexing, not a rebuild/replace gate.

### Findings

1. Full suite is not stable green.
   - `tests/test_gateway.py:722` uses a hard `elapsed < 1.5` timing assertion.
   - Under full-suite load the task is cancelled correctly, but elapsed time exceeded the threshold by about 33 ms.
   - This blocks any "stable full suite green" claim until the test is made deterministic or the behavior is asserted via lifecycle events rather than wall-clock timing.

2. `memory_cli index` leaves stale records in an existing SQLite index.
   - `src/fireclaw_core/memory_cli.py:44-48` iterates records and calls `SqliteMemoryIndex.upsert()`.
   - `SqliteMemoryIndex` already has `rebuild(records)` for clearing previous contents.
   - For a demo/CI indexing gate, rerunning index after memory pruning can produce false retrieval hits from deleted memories.

3. The end-to-end embodied mission scenario gate is weaker than the written plan.
   - `tests/test_embodied_mission_e2e.py` uses `FakeSubagentClient`, not a real robot-local `FireClawGateway` simulator.
   - It verifies mission gateway/agent/store wiring, but does not prove MissionGateway -> RobotSubagentClient -> FireClawGateway HTTP transport -> robot events in one scenario.
   - This is acceptable as an integration test, but not the full proof described by the roadmap.

4. CLI LLM path still does not expose `ProviderRuntime` fallback.
   - `ProviderRuntime` is wired into `LLMMissionPlanner`, but `src/fireclaw_core/mission_cli.py:309-317` constructs `LLMMissionPlanner(provider=..., model_id=...)` directly.
   - The field-readiness gap originally mentioned reproducing rich provider setup from operator CLI; current CLI still supports only a single provider/model invocation.

5. The new roadmap plan document is still untracked and stale.
   - `docs/superpowers/plans/2026-06-11-embodied-agent-field-readiness-roadmap.md` remains untracked.
   - It still says all six gaps are remaining and all checklist items are unchecked, despite commits implementing most of them.
   - This will mislead future agents unless either tracked as an original plan with a completion note or updated to mark completed/superseded status.

### Current Conclusion

The implementation substantially covers the planned field-readiness features, and the focused roadmap tests pass. The main remaining blockers are test stability and two plan-alignment gaps: stale memory index rebuild semantics, and the e2e scenario not using a real robot gateway. ProviderRuntime CLI fallback and stale plan-doc state are important but lower urgency unless the next milestone depends on operator-facing multi-provider fallback.

### Next Recommended Step

1. Fix the full-suite cancel timing flaky first.
2. Change `memory_cli index` to use `SqliteMemoryIndex.rebuild()` and add a stale-record regression test.
3. Decide whether the e2e gate must use a real `FireClawGateway`; if yes, add a second test or strengthen the existing one.
4. Either expose ProviderRuntime fallback in `mission_cli` or explicitly document that CLI fallback config remains future work.
5. Update or commit the 2026-06-11 plan document so its status matches the implemented commits.

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

## Update 2026-06-11 12:12 CST — Follow-up Fix Review

### Task Goal

Re-check the user's fixes for the previous review findings:

1. full-suite cancel timing flaky;
2. `memory_cli index` stale-record behavior;
3. ProviderRuntime wiring in `mission_cli --planner llm`;
4. stale/untracked 2026-06-11 field-readiness plan document.

### Context Read

- Latest commits:
  - `8db1cac fix: remove flaky wall-clock timing assertion from cancel test`
  - `fe89ba9 fix: use rebuild() for memory_cli index to remove stale records`
  - `2fc85d3 feat: wire SimpleProviderRuntime into mission_cli --planner llm`
  - `d63afbb docs: mark plan complete and add post-implementation fix notes`
- Current git status after review:
  - branch ahead of origin by 131 commits
  - modified: `memory/2026-06-11/fireclaw-embodied-roadmap-review.md`

### Commands Run

- `git status --short --branch && git log --oneline -8`
- `git show --stat --patch --find-renames 8db1cac fe89ba9 2fc85d3 d63afbb -- ...`
- `rg -n "\\[ \\]|Remaining Embodied-Agent Gaps|still builds mostly bare|not yet an explicit operator|single command that collects|untracked|823 passed|1008 passed|provider_runtime.py does not exist|ROS2 adapter 实现" ...`
- `.venv/bin/python -m pytest tests/test_gateway.py::test_gateway_cancels_active_task_between_skills tests/test_memory_cli.py tests/test_mission_cli.py::test_mission_cli_plan_mission_with_llm_flag tests/test_mission_cli.py::test_mission_cli_plan_mission_llm_missing_required_flags tests/test_mission_runtime.py tests/test_embodied_mission_e2e.py tests/test_ros1_proof_bundle.py -q`
- Manual stale-index reproduction:
  - index JSONL with `old`;
  - replace JSONL with `new`;
  - rebuild same SQLite index;
  - eval query for `old`.
- Manual CLI planner introspection:
  - `_build_planner(args)` for `planner="llm"` prints `SimpleProviderRuntime` and `True`.
- `.venv/bin/python -m pytest -q`

### Verification Results

- Focused regression set:
  - Result: `10 passed in 3.74s`
- Manual stale-index reproduction:
  - old-record eval now fails with `EXIT:2`.
  - Conclusion: `memory_cli index` now behaves as a rebuild/replace gate and no longer preserves deleted JSONL records.
- Manual CLI planner introspection:
  - `_build_planner()` now constructs `LLMMissionPlanner` with `_provider_runtime` of type `SimpleProviderRuntime`.
- Full suite:
  - `.venv/bin/python -m pytest -q`
  - Result: `1021 passed, 6 skipped in 115.07s`

### Findings

1. Code/test blocker findings from the previous review are resolved.
   - The full suite is green.
   - The memory index stale-record false positive is fixed.
   - The LLM CLI path now uses ProviderRuntime infrastructure via `SimpleProviderRuntime`.
   - The 2026-06-11 plan file is now tracked by commit `d63afbb`.

2. Remaining issue is documentation wording inside the newly tracked plan.
   - `docs/superpowers/plans/2026-06-11-embodied-agent-field-readiness-roadmap.md` has a top-level `Status: COMPLETE`.
   - The same file still contains `## Remaining Embodied-Agent Gaps` and old statements that `mission_cli.py` builds mostly bare agents, memory indexing is not an operator command, and ROS1 proof packaging has no command.
   - This is non-code, but it can mislead future agents. Recommended fix: rename the section to `Original Embodied-Agent Gaps Addressed By This Plan` or add a clear "Historical pre-implementation assessment" note before the old list.

3. Test coverage note, not a current failure.
   - `tests/test_mission_cli.py::test_mission_cli_plan_mission_with_llm_flag` checks that an `LLMMissionPlanner` is returned but not that `_provider_runtime` is `SimpleProviderRuntime`.
   - Manual introspection verified the behavior. A small assertion would make the regression test more direct.

### Current Conclusion

The implementation is code-level clean after the follow-up fixes. The only remaining actionable item from this review is a non-blocking documentation wording cleanup in the 2026-06-11 plan file; optionally add a direct assertion for mission CLI ProviderRuntime wiring.

## Update 2026-06-11 12:35 CST — OpenClaw Comparison and Experiment-Readiness Replan

### Task Goal

Re-check whether current FireClaw can complete the originally intended firefighting embodied-agent function, compare it against OpenClaw only for embodied-agent-relevant capabilities, and create the next roadmap for remaining important gaps.

### Context Read

- Recent memory:
  - `memory/2026-06-11/fireclaw-embodied-roadmap-review.md`
  - `memory/2026-06-10/fireclaw-work-resume.md` was already incorporated by earlier updates.
- Current git head:
  - `d63afbb docs: mark plan complete and add post-implementation fix notes`
- Current git status at start:
  - branch ahead of origin by 131 commits
  - modified: `memory/2026-06-11/fireclaw-embodied-roadmap-review.md`
- Current plan checked:
  - `docs/superpowers/plans/2026-06-11-embodied-agent-field-readiness-roadmap.md`
- Architecture docs checked:
  - `docs/architecture/fireclaw-openclaw-alignment.md`
  - `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`

### OpenClaw References Inspected

Used CodeGraph against `/home/nankai/fireclaw/openclaw-main`.

Relevant OpenClaw patterns checked:

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

### FireClaw Code Context Checked

Used CodeGraph and file reads for:

- `src/fireclaw_core/mission_agent.py`
- `src/fireclaw_core/mission_gateway.py`
- `src/fireclaw_core/gateway.py`
- `src/fireclaw_core/subagent_client.py`
- `src/fireclaw_core/mission_runtime.py`
- `src/fireclaw_core/memory_retrieval.py`
- `src/fireclaw_core/mission_event_aggregator.py`
- `src/fireclaw_core/approval_runtime.py`
- `src/fireclaw_core/plugin_runtime.py`
- `src/fireclaw_core/provider_runtime.py`
- `src/fireclaw_core/ros1_transport.py`
- `src/fireclaw_core/ros1_proof_bundle.py`
- `tests/test_embodied_mission_e2e.py`

### Verification

- `.venv/bin/python -m pytest -q`
  - Result: `1021 passed, 6 skipped in 139.01s`

### Current Assessment

FireClaw now covers the code-level ROS1-first embodied-agent loop required for the user's scope:

```text
operator command
-> planner with memory/correction context
-> safety and approval gates
-> mission scheduler
-> robot subagent dispatch
-> robot-local gateway task execution
-> ROS1/simulator adapter boundary
-> unified event/replay streams
-> task registry, subagent registry, task-flow, session lineage
-> memory recording and retrieval evaluation
```

OpenClaw features already adapted where useful:

- task registry and task-flow registry;
- session lineage / resume ownership guard;
- provider runtime and fallback boundary;
- plugin control-plane fingerprints and executable hook boundaries;
- approval handoff/idempotency concepts;
- memory retrieval/evaluation patterns.

OpenClaw features not required for the current embodied-agent goal:

- full ACP/IDE session platform;
- generic coding-agent UX;
- full Web UI / WebSocket dashboard;
- third-party plugin marketplace;
- native ROS2 adapter while ROS1 remains the active target.

### Remaining Important Gaps

1. **Real gateway-to-gateway e2e proof**
   - `tests/test_embodied_mission_e2e.py` still uses `FakeSubagentClient`.
   - It verifies mission agent/gateway/store wiring but does not prove `MissionGateway -> RobotSubagentClient -> FireClawGateway HTTP -> robot events`.

2. **Scenario-level experiment harness**
   - There is no repeatable rescue scenario benchmark that emits metrics suitable for demo/paper reporting.

3. **Memory learning proof**
   - Retrieval/eval exists, but there is no closed-loop test proving previous outcomes or operator corrections reach planner context across repeated missions.

4. **Experiment proof bundle**
   - ROS1 proof bundle exists, but there is no higher-level embodied proof bundle packaging mission trace, event replay, task-flow, lineage, memory eval, doctor output, and redacted notes.

5. **Documentation truth cleanup**
   - `docs/superpowers/plans/2026-06-11-embodied-agent-field-readiness-roadmap.md` still includes historical "Remaining Embodied-Agent Gaps" wording even though it is marked complete.
   - Some architecture docs still list outdated priorities such as deployable runtime factory after it has been implemented.

6. **Real robot / high-fidelity simulation execution**
   - Code can support a ROS1 proof workflow, but no artifact from an actual firefighting robot or high-fidelity ROS1 sim run exists yet.
   - This is partly external to code and should not be conflated with missing OpenClaw platform parity.

### New Plan Created

- `docs/superpowers/plans/2026-06-11-embodied-agent-experiment-readiness-roadmap.md`

Plan tasks:

1. Clean up stale roadmap and architecture wording.
2. Add real MissionGateway -> RobotSubagentClient -> FireClawGateway simulator e2e proof.
3. Add scenario-level rescue experiment harness.
4. Add memory learning closed-loop proof.
5. Add embodied experiment proof bundle.
6. Final verification and research-readiness claim notes.

### Current Conclusion

FireClaw should not chase more OpenClaw platform parity before the next milestone. The important remaining work is proving the embodied-agent system through a real gateway-to-gateway simulator chain, repeatable rescue scenarios, memory learning evidence, and artifact packaging. Real ROS1 hardware or high-fidelity simulation proof remains the main external validation gap.

## Update 2026-06-11 — Task 6: Final Verification and Research-Readiness Notes

### Task Goal

Run the full test suite, document what can and cannot be claimed about FireClaw's embodied-agent readiness, and commit.

### Commands Run

- `.venv/bin/python -m pytest -q`

### Verification Results

- Full suite: `1033 passed, 6 skipped in 133.66s`
- All tests green, ROS1 smoke skipped (expected without `FIRECLAW_RUN_ROS1_SMOKE=1`).

### Files Modified

- `README.md` — added "Embodied-Agent Experiment Readiness" section after operator authorization docs.
- `docs/architecture/fireclaw-openclaw-alignment.md` — added "Experiment Readiness Claims" section at the end.
- `memory/2026-06-11/fireclaw-embodied-roadmap-review.md` — this update.

### Claims Documented

**Can claim:** code-level and simulator-level embodied-agent readiness when gateway-to-gateway e2e test, scenario eval harness, memory learning closed-loop proof, and embodied proof bundle all pass.

**Cannot claim:** real firefighting robot validation until a ROS1 hardware or high-fidelity simulation run produces a proof bundle with doctor output, smoke artifacts, mission trace, event replay, and operator notes.

### Current Conclusion

The 6-task experiment-readiness roadmap is fully implemented. Documentation now explicitly distinguishes simulator/code readiness from real robot validation. The full test suite is green at 1033 passed, 6 skipped.
