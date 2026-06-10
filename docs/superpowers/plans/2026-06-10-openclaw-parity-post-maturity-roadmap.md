# OpenClaw Parity Post-Maturity Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move FireClaw from ROS1-first OpenClaw parity maturity into a stronger control-plane platform by closing the remaining production-path wiring, lifecycle source-of-truth, plugin/provider/memory runtime, deployment, and real-hardware proof gaps.

**Architecture:** Keep ROS1 as the active robot integration target. Do not start ROS2 implementation in this plan. Prioritize main-path correctness over new surface area: every already-built projection, hook, stream, and relay component must be exercised through `MissionAgent` / `MissionGateway` production paths before broader OpenClaw platform parity work.

**Tech Stack:** Python 3.11, pytest, stdlib JSONL stores, existing FireClaw mission/gateway/runtime modules, ROS1 smoke tests gated by `FIRECLAW_RUN_ROS1_SMOKE=1`.

---

## Current Baseline

Verified on 2026-06-10:

```text
.venv/bin/python -m pytest -q
886 passed, 6 skipped in 105.77s
```

Current working tree still contains uncommitted review fixes and docs:

```text
M docs/superpowers/plans/2026-06-10-openclaw-parity-runtime-hardening.md
M memory/2026-06-10/fireclaw-work-resume.md
M src/fireclaw_core/lifecycle_reconciler.py
M src/fireclaw_core/plugin_policy.py
M src/fireclaw_core/plugin_runtime.py
M tests/test_lifecycle_reconciler.py
M tests/test_plugin_policy.py
?? docs/superpowers/plans/2026-06-10-openclaw-parity-maturity-roadmap.md
```

OpenClaw references inspected for this planning pass:

- `openclaw-main/src/tasks/task-registry.store.ts`
- `openclaw-main/src/tasks/task-flow-registry.store.ts`
- `openclaw-main/src/acp/session-lineage-meta.ts`
- `openclaw-main/src/plugins/plugin-control-plane-context.ts`
- `openclaw-main/extensions/memory-core/src/memory/qmd-manager.ts`
- `openclaw-main/src/acp/translator.ts`
- `openclaw-main/src/agents/acp-spawn.ts`
- `openclaw-main/src/agents/model-fallback.ts`

## Current Capability Assessment

FireClaw can now cover the intended ROS1-first v1 robotics loop:

```text
operator command
-> mission planning
-> memory/correction context
-> plugin provider/memory hooks
-> approval request/token/pending projection/relay boundary
-> mission scheduler
-> robot subagent dispatch
-> robot-local task/action runtime
-> ROS1 topic/service/action transport
-> event replay/SSE/client iterator
-> memory and lifecycle projections
```

This is strong OpenClaw-inspired v1 parity for FireClaw's robotics path. It is not full OpenClaw platform parity.

## Remaining Important Gaps

1. **P0 production-path lifecycle wiring:** `MissionEventAggregator` can write terminal robot events into `SubagentRegistry`, but `MissionAgent.mission_events()` constructs it without `self.subagent_registry`. Also, terminal robot events are not yet projected back into `TaskRegistry` records in the same path.
2. **P1 task/session source-of-truth:** FireClaw has `TaskRegistry`, `SubagentRegistry`, and `LifecycleReconciler`, but lacks OpenClaw-like observers, task-flow records, session lineage ownership checks, and an explicit maintenance runner.
3. **P1 plugin control plane:** FireClaw has descriptors, hook execution, policy checks, and audit records. It lacks OpenClaw-like discovery context, inventory/policy/activation fingerprints, persistent audit export, and trusted runtime activation boundaries.
4. **P1 provider/model runtime:** `LLMMissionPlanner` talks directly to one `ModelProvider` and `model_id`. FireClaw lacks a provider runtime that can resolve catalog entries, normalize provider errors, support fallback, and expose health/status comparable to OpenClaw model runtime pieces.
5. **P1 memory lifecycle:** `MemoryRetriever.status()` and `memory_eval.py` exist, but there is no automatic transcript indexing policy, evaluation command, provider lifecycle dashboard, or regression threshold integrated into doctor/CI.
6. **P2 approval relay adapters:** `ApprovalRelay` exists as a Protocol plus in-memory implementation. Real deployment adapters such as console/stdout, webhook, or operator UI delivery are still deployment-specific and unimplemented.
7. **P2 deployment cleanup:** `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md` still contains stale descriptions for lifecycle, memory eval, and relay maturity. Fleet onboarding is a report, not an interactive wizard or migration flow.
8. **P2 hardware proof:** ROS1 transport and smoke runbook exist, but no real firefighting robot artifact has been produced. ROS2 remains intentionally out of scope.

---

## Task 1: Fix Production Lifecycle Projection Wiring

**Goal:** Ensure terminal robot lifecycle observations update both subagent and task projections through the normal `MissionAgent.mission_events()` path.

**Files:**
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `src/fireclaw_core/mission_event_aggregator.py`
- Modify: `src/fireclaw_core/task_registry.py` if a helper is needed
- Test: `tests/test_mission_agent.py`
- Test: `tests/test_mission_event_aggregator.py`

- [ ] **Step 1: Write RED test for MissionAgent event aggregation updating SubagentRegistry**

Add a test in `tests/test_mission_agent.py` that creates `MissionAgent(..., subagent_registry=subagents)`, records a mission subtask, returns a `task.completed` robot event from the fake subagent client, calls `mission.mission_events(mission_id)`, and asserts:

```python
assert subagents.get_by_child_task_id("child-1").status == "completed"
```

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py::test_mission_events_updates_subagent_registry_on_terminal_robot_event -q
```

Expected: fail because `MissionAgent.mission_events()` does not pass `subagent_registry` into `MissionEventAggregator`.

- [ ] **Step 2: Pass registry into aggregator**

In `src/fireclaw_core/mission_agent.py`, update the aggregator construction to:

```python
aggregator = MissionEventAggregator(
    registry=self.registry,
    subagent_client=self.subagent_client,
    mission_registry=self.mission_registry,
    subagent_registry=self.subagent_registry,
    task_registry=self.task_registry,
)
```

- [ ] **Step 3: Add TaskRegistry terminal projection**

Extend `MissionEventAggregator.__init__()` with optional `task_registry`. When `_terminal_status_from_event(event)` returns a terminal status, update the projected subtask record using `task_id=f"{mission_id}:{task_id}"` when present, preserving command/owner from the existing record.

- [ ] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py tests/test_mission_event_aggregator.py tests/test_lifecycle_reconciler.py -q
```

Expected: pass.

Commit:

```bash
git add src/fireclaw_core/mission_agent.py src/fireclaw_core/mission_event_aggregator.py tests/test_mission_agent.py tests/test_mission_event_aggregator.py
git commit -m "fix: wire lifecycle projections through mission events"
```

---

## Task 2: Promote Task/Session Registries Toward Source of Truth

**Goal:** Add OpenClaw-like runtime maintenance and lineage checks without replacing the working mission path.

**Files:**
- Modify: `src/fireclaw_core/task_registry.py`
- Modify: `src/fireclaw_core/subagent_registry.py`
- Modify: `src/fireclaw_core/lifecycle_reconciler.py`
- Create: `src/fireclaw_core/lifecycle_maintenance.py`
- Test: `tests/test_task_registry.py`
- Test: `tests/test_subagent_registry.py`
- Test: `tests/test_lifecycle_reconciler.py`
- Test: `tests/test_lifecycle_maintenance.py`

- [ ] **Step 1: Normalize registry schema**

Add `"subtask"` to `VALID_SCOPE_KINDS` or stop using it in projection. Prefer adding it because the mission path already uses `scope_kind="subtask"`.

Run:

```bash
.venv/bin/python -m pytest tests/test_task_registry.py -q
```

- [ ] **Step 2: Add owner/session listing helpers**

Add focused helpers to `JsonlTaskRegistryStore`:

```python
def list_for_owner(self, owner_id: str) -> list[TaskRecord]: ...
def list_for_requester_session(self, requester_session_id: str) -> list[TaskRecord]: ...
def list_active(self) -> list[TaskRecord]: ...
```

Test active ordering and corrupt-line tolerance.

- [ ] **Step 3: Add explicit maintenance runner**

Create `LifecycleMaintenanceRunner` that wraps `LifecycleReconciler.reconcile()` and returns a report with:

```python
{
    "status": "ok" | "warn",
    "orphaned_subagents": [...],
    "stale_tasks": [...],
    "checked_at": "...",
}
```

Do not run a background thread by default. Keep invocation explicit from CLI/doctor/gateway later.

- [ ] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_registry.py tests/test_subagent_registry.py tests/test_lifecycle_reconciler.py tests/test_lifecycle_maintenance.py -q
```

Expected: pass.

Commit:

```bash
git add src/fireclaw_core/task_registry.py src/fireclaw_core/lifecycle_maintenance.py tests/test_task_registry.py tests/test_lifecycle_maintenance.py
git commit -m "feat: add lifecycle maintenance runtime"
```

---

## Task 3: Add Plugin Control-Plane Fingerprints and Persistent Audit

**Goal:** Move from hook policy only to an auditable plugin control plane inspired by OpenClaw's discovery/policy/inventory/activation fingerprints.

**Files:**
- Create: `src/fireclaw_core/plugin_control_plane.py`
- Modify: `src/fireclaw_core/plugin_policy.py`
- Modify: `src/fireclaw_core/plugin_runtime.py`
- Test: `tests/test_plugin_control_plane.py`
- Test: `tests/test_plugin_policy.py`
- Test: `tests/test_plugin_runtime.py`

- [ ] **Step 1: Add control-plane context**

Create dataclasses:

```python
@dataclass(frozen=True)
class PluginDiscoveryContext:
    roots: tuple[str, ...]
    load_paths: tuple[str, ...]

@dataclass(frozen=True)
class PluginControlPlaneContext:
    discovery: PluginDiscoveryContext
    policy_fingerprint: str
    inventory_fingerprint: str | None = None
    activation_fingerprint: str | None = None
```

Use stable JSON hashing for fingerprints.

- [ ] **Step 2: Persist audit records**

Add optional `audit_path` to `PluginPolicy`; append every `PluginHookAuditRecord.to_dict()` as JSONL. Existing in-memory `audit_records` remains.

- [ ] **Step 3: Expose runtime inventory**

Add `PluginRuntime.inventory()` returning registered descriptor ids, hook names, and whether a policy is active. Do not import arbitrary third-party Python code in this task.

- [ ] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_plugin_control_plane.py tests/test_plugin_policy.py tests/test_plugin_runtime.py -q
```

Expected: pass.

Commit:

```bash
git add src/fireclaw_core/plugin_control_plane.py src/fireclaw_core/plugin_policy.py src/fireclaw_core/plugin_runtime.py tests/test_plugin_control_plane.py tests/test_plugin_policy.py tests/test_plugin_runtime.py
git commit -m "feat: add plugin control plane fingerprints"
```

---

## Task 4: Add Provider Runtime and Fallback Boundary

**Goal:** Give mission planning a provider control plane instead of binding `LLMMissionPlanner` directly to one provider/model pair.

**Files:**
- Create: `src/fireclaw_core/provider_runtime.py`
- Modify: `src/fireclaw_core/llm_planner.py`
- Modify: `src/fireclaw_core/model_catalog.py`
- Test: `tests/test_provider_runtime.py`
- Test: `tests/test_llm_planner.py`

- [ ] **Step 1: Add provider runtime protocol**

Create `ProviderRuntime` with:

```python
def select_model(self, *, task: str, preferred_model: str | None = None) -> ModelDescriptor: ...
def chat_completion(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int) -> ChatCompletion: ...
def status(self) -> dict[str, Any]: ...
```

- [ ] **Step 2: Add fallback behavior**

Implement a small runtime that tries configured model candidates in order and records the first success. Normalize `ProviderTimeoutError`, `ProviderAPIError`, and `ProviderError` into structured attempts.

- [ ] **Step 3: Wire planner optionally**

Let `LLMMissionPlanner` accept either the existing `(provider, model_id)` pair or a `provider_runtime`. Keep old constructor compatibility.

- [ ] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_provider_runtime.py tests/test_llm_planner.py tests/test_mission_agent.py -q
```

Expected: pass.

Commit:

```bash
git add src/fireclaw_core/provider_runtime.py src/fireclaw_core/llm_planner.py src/fireclaw_core/model_catalog.py tests/test_provider_runtime.py tests/test_llm_planner.py
git commit -m "feat: add provider runtime fallback boundary"
```

---

## Task 5: Make Memory Lifecycle Evaluatable in Deployment

**Goal:** Turn memory retrieval evaluation from a library helper into an operational check.

**Files:**
- Modify: `src/fireclaw_core/memory_eval.py`
- Modify: `src/fireclaw_core/memory_retrieval.py`
- Modify: `src/fireclaw_core/doctor.py`
- Create or modify: `src/fireclaw_core/memory_cli.py` if no suitable CLI exists
- Test: `tests/test_memory_eval.py`
- Test: `tests/test_doctor.py`

- [ ] **Step 1: Add thresholded evaluation result**

Extend `EvalReport` with `meets_threshold(threshold: float) -> bool`.

- [ ] **Step 2: Add doctor integration**

When `memory_index_path` and an eval fixture path are provided, run `evaluate_retrieval()` and report `pass` only when `hit_rate >= threshold`.

- [ ] **Step 3: Add transcript indexing policy stub**

Document and expose a config field for which mission transcript records should be indexed. Do not automatically index private logs without explicit config.

- [ ] **Step 4: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_eval.py tests/test_memory_retrieval.py tests/test_doctor.py -q
```

Expected: pass.

Commit:

```bash
git add src/fireclaw_core/memory_eval.py src/fireclaw_core/memory_retrieval.py src/fireclaw_core/doctor.py tests/test_memory_eval.py tests/test_doctor.py
git commit -m "feat: add deployment memory retrieval evaluation"
```

---

## Task 6: Add Concrete Approval Relay Adapters

**Goal:** Keep the relay Protocol but provide deployable adapters that do not require custom application code.

**Files:**
- Modify: `src/fireclaw_core/approval_relay.py`
- Modify: `src/fireclaw_core/mission_gateway.py` if config factory is needed
- Test: `tests/test_approval_relay.py`
- Test: `tests/test_mission_gateway.py`

- [ ] **Step 1: Add stdout/console relay**

Implement `ConsoleApprovalRelay` that writes a redacted JSON line containing request id, mission id, action, risk, channel, and operator id.

- [ ] **Step 2: Add webhook relay with fail-safe timeout**

Implement `WebhookApprovalRelay` using stdlib `urllib.request` with configurable timeout. Never include raw approval tokens unless explicitly configured.

- [ ] **Step 3: Verify idempotency and failure behavior**

Tests must prove duplicate delivery is suppressed and webhook errors return `RelayDeliveryRecord(success=False, error=...)` without blocking approval creation.

Run:

```bash
.venv/bin/python -m pytest tests/test_approval_relay.py tests/test_mission_gateway.py -q
```

Expected: pass.

Commit:

```bash
git add src/fireclaw_core/approval_relay.py tests/test_approval_relay.py tests/test_mission_gateway.py
git commit -m "feat: add deployable approval relay adapters"
```

---

## Task 7: Deployment and Documentation Truth Pass

**Goal:** Make docs, doctor output, memory, and plan checkboxes match the actual code state.

**Files:**
- Modify: `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`
- Modify: `docs/superpowers/plans/2026-06-10-openclaw-parity-maturity-roadmap.md`
- Modify: `memory/2026-06-10/fireclaw-work-resume.md`
- Test: default full suite

- [ ] **Step 1: Update architecture roadmap**

Change stale lines that say lifecycle reconciliation, memory evaluation, or relay are missing entirely. Use precise wording:

```text
v1 implemented; remaining gap is production automation / external adapter / deployment proof.
```

- [ ] **Step 2: Record real hardware status honestly**

Keep ROS1 hardware proof as `runbook/schema complete, execution pending hardware`.

- [ ] **Step 3: Verify**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all default tests pass; ROS1 smoke remains skipped by default unless `FIRECLAW_RUN_ROS1_SMOKE=1`.

Commit:

```bash
git add docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md docs/superpowers/plans/2026-06-10-openclaw-parity-post-maturity-roadmap.md memory/2026-06-10/fireclaw-work-resume.md
git commit -m "docs: update OpenClaw parity post-maturity roadmap"
```

---

## Out of Scope

- Native ROS2 `rclpy` adapter implementation.
- Real robot execution without explicit operator approval, emergency stop validation, and deployment-specific configuration.
- Arbitrary untrusted plugin sandboxing that imports third-party code without a trusted activation boundary.
- Web UI.

## Execution Order

1. Task 1 first. It fixes a production-path wiring bug.
2. Task 2 next. It gives lifecycle recovery an explicit runtime boundary.
3. Tasks 3 and 4 can proceed independently after Task 1.
4. Tasks 5 and 6 can proceed independently after Task 3 or Task 4.
5. Task 7 last, after code behavior and verification are stable.
