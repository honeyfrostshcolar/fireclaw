# Embodied Agent OpenClaw Minimal Parity Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep FireClaw focused on the ROS1-first firefighting embodied-agent loop, and close only the OpenClaw-inspired gaps that are necessary for robot task execution, safety, memory learning, lifecycle observability, and deployment proof.

**Architecture:** FireClaw should not attempt full OpenClaw platform parity. OpenClaw remains the reference for task registries, session lineage, provider fallback, plugin control-plane fingerprints, and approval handoff, but FireClaw should implement only the robotics-facing subset. ROS1 stays the active robot target; ROS2, generic ACP/IDE sessions, third-party plugin marketplace, and full WebSocket UI are out of scope for this plan.

**Tech Stack:** Python 3.11, pytest, stdlib JSONL stores, SQLite FTS5 memory index, FireClaw MissionAgent/MissionGateway/Gateway runtime modules, ROS1 smoke tests gated by `FIRECLAW_RUN_ROS1_SMOKE=1`.

---

## Current Assessment

FireClaw now appears capable of the intended v1 embodied-agent loop:

```text
operator command
-> planner with retrieved memories and operator corrections
-> plugin/provider/memory hooks
-> safety and approval gate
-> mission scheduler
-> robot subagent dispatch
-> robot-local task/action runtime
-> ROS1 topic/service/action transport
-> unified event replay/SSE
-> lifecycle projection and memory recording
```

OpenClaw features that are useful for this embodied-agent goal:

- Task/source-of-truth patterns from `openclaw/src/tasks/task-registry.store.ts`.
- Task-flow grouping/observer patterns from `openclaw/src/tasks/task-flow-registry.store.ts`.
- Session lineage and resume ownership checks from `openclaw/src/acp/session-lineage-meta.ts` and `openclaw/src/agents/acp-spawn.ts`.
- Plugin control-plane fingerprints from `openclaw/src/plugins/plugin-control-plane-context.ts`.
- Provider fallback observability from `openclaw/src/agents/model-fallback.ts`.
- Approval follow-up idempotency from `openclaw/src/agents/bash-tools.exec-approval-followup-state.ts`.

OpenClaw features that are not required for the current embodied-agent target:

- Full ACP/IDE session platform.
- Generic coding-agent task-flow UX.
- Third-party plugin marketplace and arbitrary dynamic code loading.
- Native ROS2 implementation.
- Full WebSocket UI if SSE plus REST control remains enough for the operator workflow.

## Remaining Embodied-Agent Gaps

1. **Deployment truth and diagnostics:** docs still carry platform-parity wording, and `MockRos1RobotAdapter` reports `"Live ROS1 transport is not implemented yet."` when transport is disabled even though live transport exists behind `transport.enabled=True`.
2. **Lifecycle operations:** `LifecycleMaintenanceRunner` exists, but it is not yet exposed through doctor/CLI/gateway operator workflows, so stale task/orphan recovery is not operationally visible.
3. **Mission/session lineage:** FireClaw has mission IDs, task registry, and subagent lineage, but lacks a compact operator/session lineage record and resume ownership guard. This matters for multi-robot or resumed missions; it should be lighter than OpenClaw ACP lineage.
4. **Task-flow experiment record:** FireClaw can trace missions, but lacks a first-class task-flow summary for paper experiments, recovery analysis, and operator-facing mission trees.
5. **Memory quality proof:** retrieval v2 is in the planner path, but memory quality regression should be easy to run before demos/experiments.
6. **ROS1 hardware proof package:** local ROS1 smoke proof exists, but there is no standardized artifact collector for a real robot or high-fidelity simulator run.

---

### Task 1: Truth Pass and Misleading ROS1 Diagnostic Cleanup

**Files:**
- Modify: `src/fireclaw_core/robot.py`
- Modify: `tests/test_robot.py`
- Modify: `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`
- Modify: `memory/2026-06-10/fireclaw-work-resume.md`

- [x] **Step 1: Update the failing expectation first**

Change the existing test that expects `"Live ROS1 transport is not implemented"` to assert that disabled transport is reported as disabled, not unimplemented.

```python
assert result.status == "not_configured"
assert "disabled" in str(result.error).lower()
assert "not implemented" not in str(result.error).lower()
```

Run:

```bash
.venv/bin/python -m pytest tests/test_robot.py -q
```

Expected before implementation: one failure in the ROS1 disabled-transport diagnostic test.

- [x] **Step 2: Replace the misleading runtime message**

In `src/fireclaw_core/robot.py`, change the disabled transport branch to:

```python
error="ROS1 transport is disabled by configuration. Set transport.enabled=true to execute live ROS1 commands.",
```

- [x] **Step 3: Update architecture wording**

In `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`, change recommendations so ROS2 is explicitly out of scope for the ROS1-first embodied-agent roadmap, and mark WebSocket/plugin marketplace/full ACP parity as optional platform work rather than current requirements.

- [x] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot.py tests/test_doctor.py -q
```

Expected: all selected tests pass.

Commit:

```bash
git add src/fireclaw_core/robot.py tests/test_robot.py docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md memory/2026-06-10/fireclaw-work-resume.md
git commit -m "fix: align ROS1 diagnostics with embodied roadmap"
```

### Task 2: Make Lifecycle Maintenance Operationally Visible

**Files:**
- Modify: `src/fireclaw_core/fleet_doctor.py`
- Modify: `src/fireclaw_core/mission_gateway.py`
- Modify: `tests/test_fleet_doctor.py`
- Modify: `tests/test_mission_gateway.py`

- [x] **Step 1: Add fleet doctor lifecycle test**

Add a test that creates one stale active task and one orphaned subagent, runs the fleet doctor report, and asserts the report contains:

```python
assert report["lifecycle"]["status"] == "warn"
assert report["lifecycle"]["stale_tasks"]
assert report["lifecycle"]["orphaned_subagents"]
```

Run:

```bash
.venv/bin/python -m pytest tests/test_fleet_doctor.py::test_fleet_doctor_reports_lifecycle_maintenance_warnings -q
```

Expected before implementation: fail because the report has no lifecycle section.

- [x] **Step 2: Wire `LifecycleMaintenanceRunner` into fleet doctor**

When both task and subagent registry paths are configured, instantiate `LifecycleMaintenanceRunner` and include its `run()` result under:

```python
report["lifecycle"] = {
    "status": maintenance["status"],
    "stale_tasks": maintenance["stale_tasks"],
    "orphaned_subagents": maintenance["orphaned_subagents"],
    "checked_at": maintenance["checked_at"],
}
```

- [x] **Step 3: Add read-only gateway exposure**

Expose lifecycle maintenance through an admin/read endpoint or existing fleet doctor response in `MissionGateway`; do not auto-mutate task state from HTTP. Require the existing fleet/doctor read scope.

- [x] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_fleet_doctor.py tests/test_mission_gateway.py tests/test_lifecycle_maintenance.py -q
```

Expected: all selected tests pass.

Commit:

```bash
git add src/fireclaw_core/fleet_doctor.py src/fireclaw_core/mission_gateway.py tests/test_fleet_doctor.py tests/test_mission_gateway.py
git commit -m "feat: expose lifecycle maintenance in fleet diagnostics"
```

### Task 3: Add Lightweight Mission Session Lineage and Resume Guard

**Files:**
- Create: `src/fireclaw_core/session_lineage.py`
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `src/fireclaw_core/mission_gateway.py`
- Create: `tests/test_session_lineage.py`
- Modify: `tests/test_mission_agent.py`
- Modify: `tests/test_mission_gateway.py`

- [x] **Step 1: Write lineage model tests**

Create tests for a compact FireClaw lineage record:

```python
record = MissionSessionLineage(
    session_id="mission-1",
    kind="mission",
    operator_id="operator-a",
    parent_session_id=None,
    spawned_by=None,
    spawn_depth=0,
)
assert record.to_dict()["session_id"] == "mission-1"
```

Also test ownership:

```python
store.upsert(record)
assert validate_resume_ownership(store, session_id="mission-1", operator_id="operator-a").ok is True
assert validate_resume_ownership(store, session_id="mission-1", operator_id="operator-b").ok is False
```

Run:

```bash
.venv/bin/python -m pytest tests/test_session_lineage.py -q
```

Expected before implementation: import failure.

- [x] **Step 2: Implement JSONL-backed session lineage**

Create `MissionSessionLineage`, `ResumeOwnershipDecision`, and `JsonlSessionLineageStore` with append-only `upsert()`, `get()`, `list_for_operator()`, and corrupt-line tolerance. Keep fields small: `session_id`, `kind`, `operator_id`, `parent_session_id`, `spawned_by`, `spawn_depth`, `created_at`, `updated_at`.

- [x] **Step 3: Record lineage on mission creation**

In `MissionAgent.plan_and_submit()`, when a mission is created, write a lineage record if a session lineage store is configured. Do not block mission execution if lineage writing fails; log a warning.

- [x] **Step 4: Guard explicit resume requests**

In `MissionGateway`, when a request supplies an existing `session_id` and an operator identity, validate ownership before allowing reuse. Return a 403-style response body:

```json
{"status": "denied", "message": "session resume is not owned by this operator"}
```

- [x] **Step 5: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_session_lineage.py tests/test_mission_agent.py tests/test_mission_gateway.py -q
```

Expected: all selected tests pass.

Commit:

```bash
git add src/fireclaw_core/session_lineage.py src/fireclaw_core/mission_agent.py src/fireclaw_core/mission_gateway.py tests/test_session_lineage.py tests/test_mission_agent.py tests/test_mission_gateway.py
git commit -m "feat: add mission session lineage guard"
```

### Task 4: Add Task-Flow Summary for Robotics Experiments

**Files:**
- Create: `src/fireclaw_core/task_flow_registry.py`
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `src/fireclaw_core/mission_event_aggregator.py`
- Create: `tests/test_task_flow_registry.py`
- Modify: `tests/test_mission_agent.py`
- Modify: `tests/test_mission_event_aggregator.py`

- [x] **Step 1: Write task-flow registry tests**

Create tests for one mission flow:

```python
flow = TaskFlowRecord(
    flow_id="mission-1",
    mission_id="mission-1",
    command="去二楼救人",
    status="running",
    task_ids=("mission-1:task-1", "mission-1:task-2"),
    created_at="2026-06-10T00:00:00+08:00",
    updated_at="2026-06-10T00:00:01+08:00",
)
store.upsert(flow)
assert store.get("mission-1").task_ids == ("mission-1:task-1", "mission-1:task-2")
```

Run:

```bash
.venv/bin/python -m pytest tests/test_task_flow_registry.py -q
```

Expected before implementation: import failure.

- [x] **Step 2: Implement a small JSONL task-flow store**

Create `TaskFlowRecord` and `JsonlTaskFlowRegistryStore` with `upsert()`, `get()`, `list_recent()`, and optional observer callback:

```python
on_event({"kind": "upserted", "flow": flow.to_dict(), "previous": previous_dict})
```

- [x] **Step 3: Project mission plans into task-flow records**

When `MissionAgent.plan_and_submit()` creates subtasks, upsert a flow record containing the mission ID, command, planned task IDs, assigned robot IDs, and current status.

- [x] **Step 4: Update terminal status from aggregated events**

When `MissionEventAggregator` observes all projected subtasks terminal, update the flow status to `completed`, `failed`, or `cancelled`.

- [x] **Step 5: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_flow_registry.py tests/test_mission_agent.py tests/test_mission_event_aggregator.py -q
```

Expected: all selected tests pass.

Commit:

```bash
git add src/fireclaw_core/task_flow_registry.py src/fireclaw_core/mission_agent.py src/fireclaw_core/mission_event_aggregator.py tests/test_task_flow_registry.py tests/test_mission_agent.py tests/test_mission_event_aggregator.py
git commit -m "feat: add robotics task-flow registry"
```

### Task 5: Make Memory Retrieval Quality a Demo Gate

**Files:**
- Modify: `src/fireclaw_core/memory_eval.py`
- Modify: `src/fireclaw_core/doctor.py`
- Create: `tests/fixtures/memory_eval/fireclaw_rescue_queries.json`
- Modify: `tests/test_memory_eval.py`
- Modify: `tests/test_doctor.py`

- [x] **Step 1: Add regression fixture**

Create `tests/fixtures/memory_eval/fireclaw_rescue_queries.json`:

```json
[
  {
    "query": "去二楼救人",
    "expected_record_ids": ["successful-rescue-floor-2"],
    "min_score": 0.4
  },
  {
    "query": "上次热成像误报怎么处理",
    "expected_record_ids": ["thermal-false-positive-correction"],
    "min_score": 0.35
  }
]
```

- [x] **Step 2: Test thresholded eval output**

Add a test asserting the eval command/report returns `status="pass"` only when recall and score thresholds are met:

```python
assert report["status"] == "pass"
assert report["summary"]["query_count"] == 2
assert report["summary"]["failed_queries"] == 0
```

- [x] **Step 3: Add doctor gate**

Add a doctor check that runs memory eval when an eval fixture path is configured. The check should be `warn` when quality is below threshold, not a hard crash.

- [x] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_eval.py tests/test_doctor.py -q
```

Expected: all selected tests pass.

Commit:

```bash
git add src/fireclaw_core/memory_eval.py src/fireclaw_core/doctor.py tests/fixtures/memory_eval/fireclaw_rescue_queries.json tests/test_memory_eval.py tests/test_doctor.py
git commit -m "feat: gate demos on memory retrieval quality"
```

### Task 6: Standardize ROS1 Hardware Proof Artifacts

**Files:**
- Create: `src/fireclaw_core/ros1_smoke_artifacts.py`
- Modify: `tests/test_ros1_smoke.py`
- Create: `tests/test_ros1_smoke_artifacts.py`
- Modify: `docs/deployment/ros1-deployment-guide.md`

- [x] **Step 1: Write artifact schema tests**

Create a test that records a smoke run:

```python
artifact = Ros1SmokeArtifact(
    run_id="run-1",
    robot_id="fireclaw-01",
    environment="sim",
    command="去二楼救人",
    checks=("topic_publish", "service_call", "action_goal", "action_cancel"),
    passed=True,
    started_at="2026-06-10T00:00:00+08:00",
    finished_at="2026-06-10T00:00:10+08:00",
)
writer.append(artifact)
assert writer.list_recent(limit=1)[0].run_id == "run-1"
```

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_smoke_artifacts.py -q
```

Expected before implementation: import failure.

- [x] **Step 2: Implement JSONL artifact writer**

Create `Ros1SmokeArtifact` and `JsonlRos1SmokeArtifactStore` with `append()` and `list_recent()`. Store only non-secret metadata, ROS graph names, pass/fail, timing, and error summaries.

- [x] **Step 3: Attach artifact writing to smoke tests**

When `FIRECLAW_ROS1_SMOKE_ARTIFACTS=/path/to/artifacts.jsonl` is set, append one artifact per smoke test module run. Keep default behavior unchanged.

- [x] **Step 4: Document real robot run command**

Add this command to `docs/deployment/ros1-deployment-guide.md`:

```bash
FIRECLAW_RUN_ROS1_SMOKE=1 \
FIRECLAW_ROS1_SMOKE_ARTIFACTS=results/ros1-smoke/fireclaw-$(date +%Y%m%d-%H%M%S).jsonl \
.venv/bin/python -m pytest tests/test_ros1_smoke.py -q
```

- [x] **Step 5: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_smoke_artifacts.py tests/test_ros1_transport.py -q
```

Expected: all selected tests pass.

Commit:

```bash
git add src/fireclaw_core/ros1_smoke_artifacts.py tests/test_ros1_smoke.py tests/test_ros1_smoke_artifacts.py docs/deployment/ros1-deployment-guide.md
git commit -m "feat: record ROS1 smoke proof artifacts"
```

---

## Explicitly Out of Scope

- Native ROS2 `rclpy` adapter.
- Full OpenClaw ACP/IDE session parity.
- Third-party plugin marketplace and arbitrary untrusted plugin loading.
- Full operator web UI. SSE plus REST remains sufficient unless the operator workflow proves otherwise.
- Autonomous destructive robot actions without approval/safety gates.

## Final Verification

**Status: COMPLETED** — all 6 tasks implemented via subagent-driven-development with two-stage review per task.

Run:

```bash
.venv/bin/python -m pytest -q
```

Result: `1008 passed, 6 skipped` ✅

Optional hardware/simulator proof:

```bash
FIRECLAW_RUN_ROS1_SMOKE=1 \
FIRECLAW_ROS1_SMOKE_ARTIFACTS=results/ros1-smoke/fireclaw-$(date +%Y%m%d-%H%M%S).jsonl \
.venv/bin/python -m pytest tests/test_ros1_smoke.py -q
```
