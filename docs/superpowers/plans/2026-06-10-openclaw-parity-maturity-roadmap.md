# OpenClaw Parity Maturity Roadmap Implementation Plan

> **Status: COMPLETED** — All 7 tasks implemented and verified. Follow-up fixes for lifecycle false-orphan matching and unknown plugin callable registration policy were applied after review. Latest verification: `886 passed, 6 skipped` (2026-06-10).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Move FireClaw from OpenClaw parity v1 to a deployable ROS1-first robotics control platform by closing the remaining task/session, streaming, plugin, memory, approval, deployment, and hardware-proof gaps.

**Architecture:** Keep ROS1 as the active robot integration target and leave ROS2 as a protocol boundary for now. Do not replace the working mission/gateway/runtime path; add narrow reconciliation, client, policy, and proof layers around the existing `MissionAgent`, `MissionGateway`, `TaskRegistry`, `SubagentRegistry`, `PluginRuntime`, `MemoryRetriever`, and `Ros1Transport` modules.

**Tech Stack:** Python 3.11, pytest, stdlib JSONL stores, existing FireClaw mission/gateway/runtime modules, ROS1 smoke tests gated by `FIRECLAW_RUN_ROS1_SMOKE=1`.

---

## Current Baseline

FireClaw can already execute the intended v1 loop:

```text
operator command
-> mission planning
-> scheduler/failure policy
-> MissionGateway
-> robot subagent client
-> robot-local Gateway/task queue
-> local safety/skill/action runtime
-> dry-run/simulator/ROS1 transport
-> SSE events, memory, replay, approval projection
```

Latest audited local verification before this plan:

```text
.venv/bin/python -m pytest -q
829 passed, 6 skipped
```

Latest completion verification after implementation and review fixes:

```text
.venv/bin/python -m pytest -q
886 passed, 6 skipped
```

OpenClaw comparison used:

- `openclaw/src/tasks/task-registry.store.ts`
- `openclaw/src/acp/session-lineage-meta.ts`
- `openclaw/src/plugins/plugin-control-plane-context.ts`
- `openclaw/extensions/memory-core/src/memory/manager.ts`
- `openclaw/extensions/memory-core/src/memory/qmd-manager.ts`

## Original High-Value Gaps Addressed

1. `TaskRegistry` / `SubagentRegistry` are durable projections, but not yet a full session/task control-plane source of truth with reconciliation and orphan recovery.
2. Server-side SSE replay exists, but `MissionGatewayClient` has no typed live SSE iterator with reconnect/cursor handling.
3. `PluginRuntime` executes explicitly registered callables, but there is no install/load policy, permission manifest enforcement, or audit trail for third-party plugin hooks.
4. `MemoryRetriever` is wired into planner context, but memory provider lifecycle, transcript indexing policy, and retrieval evaluation are still weak.
5. `ApprovalRuntime` has persistent token hashes and pending projection, but no external operator relay adapter.
6. Deployment diagnostics still contain stale ROS1 wording and lack a fleet onboarding / repair workflow comparable to OpenClaw's operational control-plane polish.
7. ROS1 proof is local tutorial-stack smoke, not a real robot or high-fidelity robot-stack proof.

---

## File Structure

### Task/Session Control Plane

- Modify: `src/fireclaw_core/task_registry.py`
- Modify: `src/fireclaw_core/subagent_registry.py`
- Modify: `src/fireclaw_core/mission_registry.py`
- Modify: `src/fireclaw_core/mission_event_aggregator.py`
- Create: `src/fireclaw_core/lifecycle_reconciler.py`
- Test: `tests/test_lifecycle_reconciler.py`
- Test: `tests/test_mission_event_aggregator.py`

### Client Streaming

- Modify: `src/fireclaw_core/mission_gateway_client.py`
- Test: `tests/test_mission_gateway_client.py`

### Plugin Permission Boundary

- Modify: `src/fireclaw_core/plugin_descriptor.py`
- Modify: `src/fireclaw_core/plugin_runtime.py`
- Create: `src/fireclaw_core/plugin_policy.py`
- Test: `tests/test_plugin_runtime.py`
- Test: `tests/test_plugin_policy.py`

### Memory Lifecycle and Evaluation

- Modify: `src/fireclaw_core/memory_retrieval.py`
- Modify: `src/fireclaw_core/memory_index.py`
- Create: `src/fireclaw_core/memory_eval.py`
- Test: `tests/test_memory_retrieval.py`
- Test: `tests/test_memory_eval.py`

### Approval Relay

- Modify: `src/fireclaw_core/approval_runtime.py`
- Modify: `src/fireclaw_core/mission_gateway.py`
- Create: `src/fireclaw_core/approval_relay.py`
- Test: `tests/test_approval_runtime.py`
- Test: `tests/test_approval_relay.py`
- Test: `tests/test_mission_gateway.py`

### Deployment / ROS1 Proof

- Modify: `src/fireclaw_core/doctor.py`
- Modify: `src/fireclaw_core/fleet_doctor.py`
- Create: `docs/deployment/ros1-hardware-smoke-proof.md`
- Test: `tests/test_doctor.py`
- Test: `tests/test_fleet_doctor.py`

---

## Task 1: Lifecycle Reconciler and Orphan Recovery

**Goal:** Make existing registries useful for restart recovery and mission trace repair without replacing the working runtime path.

- [x] **Step 1: Write RED tests for orphan detection**

Add `tests/test_lifecycle_reconciler.py`:

```python
from fireclaw_core.lifecycle_reconciler import LifecycleReconciler
from fireclaw_core.subagent_registry import JsonlSubagentRegistry
from fireclaw_core.task_registry import JsonlTaskRegistryStore


def test_reconciler_marks_subtask_orphan_when_child_task_missing(tmp_path):
    tasks = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")
    subagents.create(
        parent_mission_id="mission-1",
        parent_subtask_id="subtask-1",
        robot_id="robot-1",
        child_task_id="child-1",
        created_at="2026-06-10T00:00:00+00:00",
    )

    report = LifecycleReconciler(task_registry=tasks, subagent_registry=subagents).reconcile(now="2026-06-10T00:01:00+00:00")

    assert report["orphaned_subagents"] == ["child-1"]
    assert subagents.get_by_child_task_id("child-1").status == "orphaned"
```

Run:

```bash
.venv/bin/python -m pytest tests/test_lifecycle_reconciler.py -q
```

Expected: fail because `lifecycle_reconciler.py` does not exist.

- [x] **Step 2: Implement minimal reconciler**

Create `src/fireclaw_core/lifecycle_reconciler.py` with a small class that:

- lists subagent runs;
- checks whether each `child_task_id` exists in `TaskRegistry`;
- marks missing active children as `orphaned`;
- returns a deterministic report.

- [x] **Step 3: Add mission/subtask terminal repair**

Add tests proving terminal robot events update both `SubagentRegistry` and parent task projection, then extend `MissionEventAggregator` or the reconciler to write the parent task status.

- [x] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_lifecycle_reconciler.py tests/test_task_registry.py tests/test_subagent_registry.py tests/test_mission_event_aggregator.py -q
```

Expected: pass.

Commit:

```bash
git add src/fireclaw_core/lifecycle_reconciler.py src/fireclaw_core/mission_event_aggregator.py tests/test_lifecycle_reconciler.py tests/test_mission_event_aggregator.py
git commit -m "feat: add lifecycle reconciliation for task and subagent registries"
```

---

## Task 2: Typed SSE Client Iterator and Reconnect

**Goal:** Match the existing server-side SSE replay with a client API that can resume from `Last-Event-ID` / sequence cursors.

- [x] **Step 1: Write RED client iterator test**

Add to `tests/test_mission_gateway_client.py` a fake HTTP response test for:

```python
events = list(client.stream_mission_events("mission-1", after_sequence=10, max_events=2))
assert events[0]["sequence"] == 11
assert events[1]["sequence"] == 12
```

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_gateway_client.py::test_client_stream_mission_events_parses_sse -q
```

Expected: fail because `stream_mission_events()` is missing.

- [x] **Step 2: Implement parser and iterator**

Add:

- `_iter_sse_events(response)` that parses `event:`, `id:`, and `data:` blocks;
- `MissionGatewayClient.stream_mission_events(mission_id, after_sequence=None, last_event_id=None, max_events=None)`;
- reconnect helper that returns the last seen sequence so callers can resume.

- [x] **Step 3: Verify reconnect semantics**

Add a test that a timeout after one event can be resumed with `after_sequence=<last sequence>`.

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_gateway_client.py -q
```

Expected: pass.

Commit:

```bash
git add src/fireclaw_core/mission_gateway_client.py tests/test_mission_gateway_client.py
git commit -m "feat: add typed mission SSE client iterator"
```

---

## Task 3: Plugin Policy and Audit Trail

**Goal:** Keep callable hooks, but require explicit descriptor permissions and record every hook effect for incident audit.

- [x] **Step 1: Write RED policy tests**

Add `tests/test_plugin_policy.py` proving:

- a plugin cannot register `tool_approval:add_reason` unless descriptor declares that hook;
- plugin hook execution records `plugin_id`, `hook_type`, `hook_name`, `allowed`, and `reason`.

Run:

```bash
.venv/bin/python -m pytest tests/test_plugin_policy.py -q
```

Expected: fail because `plugin_policy.py` is missing.

- [x] **Step 2: Implement policy module**

Create `src/fireclaw_core/plugin_policy.py` with:

- `PluginHookPermission`;
- `PluginHookAuditRecord`;
- `PluginPolicy.evaluate_registration(descriptor, hook_type, hook_name)`.

- [x] **Step 3: Wire into `PluginRuntime.register_callable()`**

Reject undeclared hook registrations with `ValueError`, unless explicitly called with a test-only or trusted descriptor bypass.

- [x] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_plugin_runtime.py tests/test_plugin_policy.py tests/test_mission_agent.py tests/test_mission_gateway.py -q
```

Expected: pass.

Commit:

```bash
git add src/fireclaw_core/plugin_policy.py src/fireclaw_core/plugin_runtime.py tests/test_plugin_policy.py tests/test_plugin_runtime.py
git commit -m "feat: enforce plugin hook policy and audit runtime effects"
```

---

## Task 4: Memory Provider Lifecycle and Retrieval Evaluation

**Goal:** Turn retrieval v2 from a feature into an evaluable subsystem with startup checks and regression fixtures.

- [x] **Step 1: Add evaluation fixtures**

Create `tests/fixtures/memory_eval_cases.json` with command/query pairs such as:

```json
[
  {
    "query": "去二楼救人",
    "must_match": ["二楼", "victim", "thermal"],
    "record_type": "outcome"
  }
]
```

- [x] **Step 2: Implement `memory_eval.py`**

Create `src/fireclaw_core/memory_eval.py` with `evaluate_retrieval(retriever, cases, limit=5)` returning hit rate, missing cases, and matched record ids.

- [x] **Step 3: Add provider lifecycle preflight**

Extend `MemoryRetriever` with a cheap `status()` method reporting:

- lexical index available;
- embedding provider configured;
- embedding provider degraded/unavailable;
- last indexing timestamp if available.

- [x] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_retrieval.py tests/test_memory_eval.py tests/test_mission_agent.py -q
```

Expected: pass.

Commit:

```bash
git add src/fireclaw_core/memory_eval.py src/fireclaw_core/memory_retrieval.py tests/test_memory_eval.py tests/test_memory_retrieval.py tests/fixtures/memory_eval_cases.json
git commit -m "feat: add memory retrieval lifecycle status and evaluation harness"
```

---

## Task 5: External Approval Relay Adapter

**Goal:** Convert relay-ready pending approval records into an actual adapter boundary for console, webhook, or future operator UI delivery.

- [x] **Step 1: Write RED relay tests**

Add `tests/test_approval_relay.py` proving an in-memory relay receives pending approvals and does not receive resolved/expired approvals twice.

- [x] **Step 2: Implement relay protocol**

Create `src/fireclaw_core/approval_relay.py` with:

- `ApprovalRelay` protocol;
- `InMemoryApprovalRelay`;
- `RelayDeliveryRecord`;
- idempotent `deliver_pending_approval()`.

- [x] **Step 3: Wire into `MissionGateway`**

When `action=request` returns pending and relay metadata exists, call the configured relay adapter. Keep failure fail-safe: approval remains pending, relay error is recorded in the response and logs, but the operator decision path is not bypassed.

- [x] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_approval_relay.py tests/test_mission_gateway.py tests/test_approval_runtime.py -q
```

Expected: pass.

Commit:

```bash
git add src/fireclaw_core/approval_relay.py src/fireclaw_core/mission_gateway.py tests/test_approval_relay.py tests/test_mission_gateway.py
git commit -m "feat: add external approval relay boundary"
```

---

## Task 6: Deployment Doctor Cleanup and Fleet Onboarding

**Goal:** Remove stale diagnostics and add a deployer-facing ROS1/fleet readiness path.

- [x] **Step 1: Fix stale ROS1 doctor wording**

Change the `ros1` adapter doctor message from "live ROS1 transport is not implemented yet" to a readiness statement that checks config, emergency stop, action feedback, and cancel support.

- [x] **Step 2: Add onboarding report**

Extend `fleet_doctor.py` with a report section for:

- enrolled robots;
- enabled robots;
- stale heartbeats;
- missing ROS1 remaps;
- missing emergency stop endpoint;
- unresolved approval relay channel.

- [x] **Step 3: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_doctor.py tests/test_fleet_doctor.py -q
```

Expected: pass.

Commit:

```bash
git add src/fireclaw_core/doctor.py src/fireclaw_core/fleet_doctor.py tests/test_doctor.py tests/test_fleet_doctor.py
git commit -m "fix: align deployment doctor with ROS1 transport maturity"
```

---

## Task 7: ROS1 Hardware Smoke Proof Runbook

**Goal:** Keep development tests default-safe while making real robot proof repeatable and auditable.

- [x] **Step 1: Add runbook**

Create `docs/deployment/ros1-hardware-smoke-proof.md` covering:

- required ROS master/network variables;
- robot namespace and topic/service/action mapping;
- dry-run preflight;
- emergency stop validation;
- topic publish proof;
- service request proof;
- action goal/feedback/cancel proof;
- logs/artifacts to keep out of git.

- [x] **Step 2: Add optional artifact schema**

Create `docs/deployment/ros1-hardware-smoke-artifact.schema.json` for recording smoke results without robot secrets.

- [x] **Step 3: Verify docs and tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_transport.py tests/test_ros1_config.py tests/test_doctor.py -q
```

Expected: pass.

Commit:

```bash
git add docs/deployment/ros1-hardware-smoke-proof.md docs/deployment/ros1-hardware-smoke-artifact.schema.json
git commit -m "docs: add ROS1 hardware smoke proof runbook"
```

---

## Final Verification

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected:

```text
all default tests pass; ros1_smoke remains skipped by default
```

Optional ROS1 smoke proof on a configured ROS1 workstation:

```bash
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py -q
```

Expected:

```text
6 passed
```

## Out of Scope

- Native ROS2 `rclpy` transport implementation.
- Operator web UI.
- Direct real hardware execution without explicit operator and deployment safety gates.
- Arbitrary untrusted plugin sandboxing beyond explicit permission checks and audit records.
