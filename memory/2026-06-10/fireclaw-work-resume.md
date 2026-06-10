# FireClaw Work Resume - 2026-06-10

## 当前进展：

- OpenClaw parity v1 已实现并完成一次后续接入修正。
- Runtime hardening 计划（`docs/superpowers/plans/2026-06-10-openclaw-parity-runtime-hardening.md`）全部 7 个任务已完成。
- 最新默认全量测试结果：`.venv/bin/python -m pytest -q` -> `823 passed, 6 skipped in 99.56s`。
- 6.10 的工作记录已从 `memory/2026-06-09/fireclaw-work-resume.md` 迁移到本文件。

## 已完成：

- 审核用户实现后的 OpenClaw parity v1 状态。
- 接入 `MemoryRetriever` 到 `MissionAgent` planner context。
- 接入 `ApprovalRuntime` 到 `MissionGateway` approval endpoint。
- 让 `RobotSubagentClient.get_task_trace()` 在观测到 terminal trace 时更新已有 `JsonlSubagentRegistry` run record。
- 修掉 `tests/test_mission_gateway_client.py` 的 SSE reader thread timeout warning。
- 把 stale unchecked OpenClaw parity plan 改成 v1 completion record。
- 同步架构 roadmap 中 PluginRuntime / MemoryRetriever 相关过时表述。

## 当前问题：

- `TaskRegistry` 已实现但仍不是 mission/task lifecycle 的统一 source of truth。
- Subagent completion routing 仍需要事件驱动地回写 parent mission/subtask 状态，并处理 orphan recovery。
- `PluginRuntime` 仍是 descriptor hook name validation / aggregation，尚未安全加载和执行 callable hooks。
- `ApprovalRuntime` token 仍是进程内状态，尚未持久化，也没有 external operator relay。
- `MissionGatewayClient` 有 JSON endpoint typed client，但还没有 typed SSE iterator / reconnect abstraction。
- ROS2 仍是 protocol boundary 和实施计划，不是 native `rclpy` adapter。
- 真实消防机器人硬件 smoke proof 仍未完成。

## 下一步：

1. 将 `TaskRegistry` / `SubagentRegistry` 做成 mission trace 和 recovery 的 lifecycle projection。
2. 给 `MissionGatewayClient` 增加 client-side SSE iterator 和 reconnect/cursor handling。
3. 设计 executable plugin hook loading，先明确 permission boundary 和 sandbox policy。
4. 给 approval runtime token 增加持久化和外部 operator relay。
5. 根据硬件可用性，选择 ROS2 native adapter 或真实 ROS1 hardware smoke proof。

## 需要运行的命令：

```bash
.venv/bin/python -m pytest -q
```

## Update 2026-06-10 00:08 CST — Fixed Current Test Failures

### Task Goal

Fix the two failing tests from the previous reconciliation and restore the full test suite to green.

### Root Causes

1. `tests/test_mission_gateway.py::test_post_body_too_large`
   - `MissionGateway._read_json()` rejected an oversized request from `Content-Length` before consuming the request body.
   - `urllib` was still sending the 1 MB+ body when the server closed the connection, producing `BrokenPipeError` / `URLError` instead of the expected HTTP 400 path.

2. `tests/test_ros1_smoke.py::test_ros1_action_timeout`
   - The test passed a raw `dict` as a real ROS1 Fibonacci action goal, which actionlib cannot serialize.
   - After converting to `FibonacciGoal`, the focused test exposed hidden order coupling: `rospy.init_node()` had only been called by an earlier topic smoke test.
   - Once initialized independently, the transport returned `status="failed"` for action result timeout while the smoke test and task-state model expected a distinct `timeout` status.

### Files Modified

- `src/fireclaw_core/mission_gateway.py`
  - Oversized request bodies are now consumed before raising `_BadRequestError`, so clients can receive the intended HTTP error response.
- `tests/test_ros1_smoke.py`
  - Added `_ensure_rospy_node()`.
  - Smoke tests now initialize `rospy` independently instead of relying on test order.
  - Timeout test now uses `actionlib_tutorials.msg.FibonacciGoal(order=100)`.
- `tests/test_ros1_transport.py`
  - Added `test_ros1_transport_reports_action_result_timeout`.
- `src/fireclaw_core/ros1_transport.py`
  - Action result timeout now returns `{"status": "timeout", ...}` instead of `{"status": "failed", ...}`.

### Verification

- `.venv/bin/python -m pytest tests/test_mission_gateway.py::test_post_body_too_large -q`
  - GREEN: `1 passed`
- `.venv/bin/python -m pytest tests/test_ros1_transport.py::test_ros1_transport_reports_action_result_timeout -q`
  - RED first, then GREEN: `1 passed`
- `.venv/bin/python -m pytest tests/test_ros1_smoke.py::test_ros1_action_timeout -q`
  - GREEN: `1 passed, 5 warnings`
- `.venv/bin/python -m pytest tests/test_mission_gateway.py tests/test_ros1_transport.py tests/test_ros1_smoke.py -q`
  - GREEN: `47 passed, 6 warnings`
- `.venv/bin/python -m pytest -q`
  - GREEN: `582 passed, 6 warnings in 58.86s`

### Current Conclusion

The two current test failures are fixed and the full local test suite is green again. Warnings are from ROS/actionlib deprecations in ROS Noetic dependencies, not FireClaw test failures.

## Update 2026-06-10 OpenClaw Parity v1 Audit After User Implementation

### Task Goal

Re-check the repository after the user reported implementing the OpenClaw parity next-roadmap tasks. Compare commits, current code, roadmap/memory records, and test status to identify real remaining issues.

### Commands Executed

- `git status --short --branch`
  - Result: `## master...origin/master [领先 82]`
  - Dirty/untracked state at that audit point:
    - `M memory/2026-06-09/fireclaw-work-resume.md`
    - `?? docs/superpowers/plans/2026-06-09-openclaw-parity-next-roadmap.md`
- `git log --oneline -12`
  - Latest commits included:
    - `772100c docs: update roadmap with OpenClaw parity v1 completion (791 passed)`
    - `9c3db9a feat: add doctor --fix mode, hardware smoke template, and ROS2 adapter plan`
    - `c1a7f01 feat: add approval runtime tokens and pending work projection`
    - `6c6702b fix: deduplicate _cosine_similarity by importing from memory_index`
    - `52937bc feat: add ranked memory retrieval with optional embedding support`
    - `d92a973 feat: implement PluginRuntime v1 with hook validation and aggregation`
    - `c71f028 feat: add SubagentRunRecord and JsonlSubagentRegistry for robot subagent lineage tracking`
    - `7810d20 feat: add OpenClaw-style TaskRegistry v1`
- `.venv/bin/python -m pytest -q`
  - Result: `791 passed, 6 skipped, 1 warning in 99.29s`
  - Warning: `tests/test_mission_gateway_client.py::TestMissionGatewayClientIntegration::test_client_submit_mission_real` reported a `PytestUnhandledThreadExceptionWarning` from an SSE reader thread timing out.

### Current Audit Findings

- Full suite was green, but not warning-clean.
- `TaskRegistry` existed and had unit coverage, but was mostly standalone; `MissionAgent` / `MissionScheduler` still did not write mission task lifecycle into it.
- `JsonlSubagentRegistry` existed and `RobotSubagentClient.submit_task()` could create child run records when an optional registry was injected, but `get_task_trace()` did not update terminal run status and mission event aggregation did not route terminal robot events back into the subagent registry.
- `PluginRuntime` validated and aggregated declared hook names, but did not execute hook callables or integrate real provider/memory/tool-approval runtime behavior into `llm_planner`, `mission_memory`, or approval flow.
- `MemoryRetriever` supported lexical plus embedding rank fusion for FTS candidate sets, but `MissionAgent._retrieve_planner_context()` still called `MissionMemoryStore.search()` directly, so the planner path was not using ranked retrieval v2.
- `ApprovalRuntime` stored tokens only in process memory and was not referenced by `MissionGateway` / `MissionAgent` approval endpoints, so pending projection/token lifecycle was not yet part of the operator control plane.
- `MissionGatewayClient` covered synchronous JSON endpoints, but had no typed live SSE iterator/client-side cursor abstraction despite server cursor replay being implemented.
- ROS2 remained a protocol boundary and written plan, not a native `rclpy` adapter.
- The architecture roadmap correctly listed many of these as partial coverage, but later "重要缺口" text was stale for PluginRuntime and MemoryRetriever because it still said hook/ranking were missing entirely rather than "implemented v1 but not fully wired into runtime".
- The superpowers implementation plan file remained untracked and unchecked even though many tasks were implemented; this would mislead future agents.

### Next Recommended Step

Treat the implementation as OpenClaw parity v1, not full parity. The highest-value next fixes were: wire `MemoryRetriever` into planner context, connect `ApprovalRuntime` to MissionGateway approvals, make subagent trace/event observation update `JsonlSubagentRegistry`, and either update/commit or remove the stale unchecked superpowers plan file.

## Update 2026-06-10 OpenClaw Parity v1 Wiring Fixes

### Task Goal

Implement the highest-value audit fixes from the prior section:

- wire `MemoryRetriever` into planner context;
- connect `ApprovalRuntime` to `MissionGateway` approvals;
- make terminal subagent trace observation update `JsonlSubagentRegistry`;
- replace the stale unchecked OpenClaw parity plan with a completion record;
- remove the SSE client test thread timeout warning.

### Files Modified

- `src/fireclaw_core/mission_agent.py`
  - Added optional `memory_retriever`.
  - `_retrieve_planner_context()` now uses ranked retriever results when configured and falls back to the existing `MissionMemoryStore.search()` path otherwise.
- `src/fireclaw_core/subagent_client.py`
  - `get_task_trace()` now updates an existing subagent run registry record when the observed trace status is terminal.
  - It does not create lineage records from trace-only observations when no child mapping exists.
- `src/fireclaw_core/mission_gateway.py`
  - Added optional `approval_runtime`.
  - `POST /missions/{id}/approvals` with `action=request` now returns a one-time `approval_token` and public token metadata when runtime is configured.
  - Added `action=pending` for mission-scoped pending approval projection.
  - Added `action=resolve_token` for resolving a raw approval token without exposing token hashes.
- `tests/test_mission_agent.py`
  - Added regression coverage proving planner context uses ranked retrieved memories when configured.
- `tests/test_subagent_registry.py`
  - Replaced the old read-only trace expectation with terminal-trace registry update coverage plus a no-mapping no-create guard.
- `tests/test_mission_gateway.py`
  - Added approval runtime token/pending projection endpoint coverage.
- `tests/test_mission_gateway_client.py`
  - SSE reader thread now treats socket timeout as a long-stream stop condition, removing the pytest unhandled thread warning.
- `docs/superpowers/plans/2026-06-09-openclaw-parity-next-roadmap.md`
  - Replaced stale unchecked implementation plan with an OpenClaw parity v1 completion record and remaining maturity gaps.
- `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`
  - Updated stale PluginRuntime/MemoryRetriever gap wording to reflect v1 implementation plus remaining runtime/provider/evaluation gaps.
- `memory/2026-06-10/fireclaw-work-resume.md`
  - Created this 6.10 resume record after the user correctly pointed out that new 6.10 work should not be appended to the 6.9 memory file.

### TDD / Verification

- RED:
  - `.venv/bin/python -m pytest tests/test_mission_agent.py::test_plan_and_submit_uses_ranked_memory_retriever_when_configured tests/test_subagent_registry.py::test_subagent_client_get_trace_updates_existing_registry_record_on_terminal_status tests/test_mission_gateway.py::test_request_approval_creates_runtime_token_when_configured -q`
  - Result after fixing a test import typo: 3 expected failures:
    - `MissionAgent.__init__()` missing `memory_retriever`;
    - terminal trace left registry status as `dispatched`;
    - `MissionGateway.__init__()` missing `approval_runtime`.
- GREEN:
  - Same focused command -> `3 passed`.
- Related suites:
  - `.venv/bin/python -m pytest tests/test_mission_agent.py tests/test_subagent_registry.py tests/test_mission_gateway.py tests/test_approval_runtime.py tests/test_memory_retrieval.py -q`
  - Result: `133 passed`.
  - `.venv/bin/python -m pytest tests/test_mission_gateway_client.py -q`
  - Result: `24 passed`.
- Full suite:
  - `.venv/bin/python -m pytest -q`
  - Result: `794 passed, 6 skipped in 98.22s`.

### Current Conclusion

OpenClaw parity v1 is now better wired into the FireClaw runtime path: ranked retrieval can feed planner context, approval runtime tokens are reachable through MissionGateway, and subagent terminal trace observation updates run lineage. Remaining gaps are still platform maturity work: task/session registry as source of truth, event-driven subagent completion routing, executable plugin hooks with permission boundaries, persistent approval token storage, client-side SSE iterator/reconnect abstraction, ROS2 implementation, and real robot hardware proof.

## Update 2026-06-10 Runtime Hardening Plan Execution

### Task Goal

Execute `docs/superpowers/plans/2026-06-10-openclaw-parity-runtime-hardening.md` — harden remaining OpenClaw parity v1 gaps.

### Tasks Completed

**Task 1: PluginRuntime Callable Hook Boundary**
- Added `PluginHookCallback` type alias and `PluginHookEffect` frozen dataclass
- Added `register_callable()`, `run_provider_hooks()`, `run_memory_hooks()`, `run_tool_approval_hooks()`
- Callback exception isolation: one failing callback does not block remaining hooks
- 10 new tests (21 total in plugin_runtime)

**Task 2: Wire Plugin Hooks Into Planner, Memory, and Approval**
- MissionAgent accepts `plugin_runtime`, applies `enrich_context` hooks before planner
- MissionGateway accepts `plugin_runtime`, applies `add_reason` hooks during approval
- Plugin-contributed memories/corrections pass through `redact_dict`
- 5 new tests across mission_agent and mission_gateway

**Task 3: Lifecycle Projection Stores**
- `JsonlTaskRegistryStore.project_task_state()` — idempotent create-or-update
- `JsonlSubagentRegistry.mark_terminal()` — idempotent terminal update, skips already-terminal
- 6 new tests

**Task 4: Wire Lifecycle Projection Into Mission Runtime**
- MissionAgent accepts `task_registry` and `subagent_registry`, projects mission + subtask lifecycle
- MissionEventAggregator routes terminal robot events into SubagentRegistry
- 4 new tests

**Task 5: Persistent Approval Runtime Token Store**
- `ApprovalRuntime` accepts `token_store_path` for JSONL persistence
- Only token hash persisted, raw token never written to disk
- Restart resilience: new instance loads existing tokens
- 1 new test

**Task 6: Operator Relay Projection and Client Methods**
- MissionGateway stores relay context (channel + operator_id) per approval request
- Pending projection includes relay metadata
- `MissionGatewayClient.get_pending_approvals()` and `resolve_approval_token()` typed methods
- 2 new tests

**Task 7: Documentation, Memory, and Verification**
- Updated architecture roadmap with runtime hardening status
- Updated memory record
- Full suite verification

### Verification

- `.venv/bin/python -m pytest -q`
  - Result: `823 passed, 6 skipped in 99.56s`

### Test Growth

- Session start: 794 passed
- Session end: 823 passed (+29 tests)

### Files Modified

- `src/fireclaw_core/plugin_runtime.py` — callable hook registry + exception isolation
- `src/fireclaw_core/mission_agent.py` — plugin_runtime, task_registry, subagent_registry wiring
- `src/fireclaw_core/mission_gateway.py` — plugin_runtime, approval relay projection
- `src/fireclaw_core/mission_event_aggregator.py` — subagent_registry terminal routing
- `src/fireclaw_core/task_registry.py` — project_task_state()
- `src/fireclaw_core/subagent_registry.py` — mark_terminal()
- `src/fireclaw_core/approval_runtime.py` — JSONL token persistence
- `src/fireclaw_core/mission_gateway_client.py` — typed approval methods
- `tests/test_plugin_runtime.py` — 10 new tests
- `tests/test_mission_agent.py` — 6 new tests
- `tests/test_mission_gateway.py` — 5 new tests
- `tests/test_mission_gateway_client.py` — 1 new test
- `tests/test_task_registry.py` — 3 new tests
- `tests/test_subagent_registry.py` — 3 new tests
- `tests/test_approval_runtime.py` — 1 new test
- `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md` — updated gap descriptions

### Known Remaining Gaps

- Native ROS2 adapter remains out of scope
- Real robot hardware proof remains out of scope
- Full external operator relay adapters not implemented (relay-ready projection only)
- Arbitrary third-party plugin loading not implemented (explicit callable registration only)
- Cross-process TaskRegistry/SubagentRegistry reconciliation and orphan recovery
- Client-side SSE iterator/reconnect abstraction
