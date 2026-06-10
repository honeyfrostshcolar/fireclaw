# OpenClaw Parity Runtime Hardening Implementation Plan

> **Status: COMPLETED** — All 7 tasks implemented and verified. 829 passed, 6 skipped (2026-06-10).
> Follow-up hardening: memory hooks wired into planner retrieval, TaskRegistry/SubagentRegistry projection in submit_subtask(), subagent_registry auto-wiring into RobotSubagentClient.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Harden the remaining OpenClaw parity v1 gaps for FireClaw by adding executable plugin hook boundaries, making task/subagent lifecycle projection usable as runtime recovery state, and persisting approval runtime tokens with an operator relay path.

**Architecture:** Keep ROS1 as the active robot integration target and leave ROS2 untouched. Add narrow runtime interfaces around the existing v1 stores instead of replacing the working mission/gateway path: plugins may enrich/filter/require but must not bypass safety gates; registries become durable lifecycle projections; approvals get persistent token state and relay-ready pending work records.

**Tech Stack:** Python 3.11, pytest, stdlib JSONL stores, existing FireClaw MissionAgent/MissionGateway/TaskRegistry/SubagentRegistry/PluginRuntime/ApprovalRuntime modules, ROS1 smoke tests remain marker-gated.

---

## Execution Status

Updated: 2026-06-10

- Completed: Task 1, Task 3, Task 5, Task 6, and Task 7.
- Partially completed: Task 2 and Task 4.
- Remaining from this plan:
  - Apply `PluginRuntime.run_memory_hooks()` to retrieved planner memories.
  - Project subtasks into `TaskRegistry` on the default `use_scheduler=True` path.
  - Ensure `MissionAgent(subagent_registry=...)` creates subagent lineage without requiring callers to manually construct `RobotSubagentClient(registry=...)`.

Latest verified suite from implementation record:

```text
823 passed, 6 skipped
```

---

## Scope

In scope:

- Executable but constrained plugin hooks for provider context, memory result post-processing, and approval reason/scope enrichment.
- TaskRegistry/SubagentRegistry lifecycle projection that mission trace/recovery can consume.
- Persistent ApprovalRuntime token storage and a relay-ready operator pending-work projection.
- Tests and docs/memory updates.

Out of scope:

- Native ROS2 adapter or ROS2 smoke tests.
- Full Web UI.
- Arbitrary third-party plugin code execution without explicit Python callable registration.
- Hardware ROS1 proof; existing ROS1 smoke remains default-skipped unless explicitly enabled.

---

## File Structure

### Plugin Runtime Hooks

- Modify: `src/fireclaw_core/plugin_runtime.py`
  - Add callable registration and execution for known hook names.
  - Keep descriptor validation separate from callable execution.
  - Add result schemas for provider/memory/tool approval hook effects.
- Modify: `src/fireclaw_core/mission_agent.py`
  - Apply provider context hooks before calling planner.
  - Apply memory hooks after retrieval and before redaction leaves planner context.
- Modify: `src/fireclaw_core/mission_gateway.py`
  - Apply tool approval hooks during approval request handling.
- Test: `tests/test_plugin_runtime.py`
- Test: `tests/test_mission_agent.py`
- Test: `tests/test_mission_gateway.py`

### Lifecycle Projection

- Modify: `src/fireclaw_core/task_registry.py`
  - Add projection helpers for mission/subtask/task transitions.
- Modify: `src/fireclaw_core/subagent_registry.py`
  - Add idempotent terminal update helpers and orphan detection.
- Modify: `src/fireclaw_core/mission_agent.py`
  - Accept optional `task_registry` and `subagent_registry`.
  - Project mission/subtask submit and terminal trace observations.
- Modify: `src/fireclaw_core/mission_event_aggregator.py`
  - Route terminal robot events into subagent registry when configured.
- Test: `tests/test_task_registry.py`
- Test: `tests/test_subagent_registry.py`
- Test: `tests/test_mission_agent.py`
- Test: `tests/test_mission_event_aggregator.py`

### Persistent Approval Runtime

- Modify: `src/fireclaw_core/approval_runtime.py`
  - Add JSONL-backed token store.
  - Keep raw token one-time only; persist token hash and public metadata.
- Modify: `src/fireclaw_core/approval_store.py`
  - Add optional relay channel / requester context fields if needed without breaking old records.
- Modify: `src/fireclaw_core/mission_gateway.py`
  - Use persistent approval runtime for request/pending/resolve flows.
  - Add relay-ready pending projection payload.
- Modify: `src/fireclaw_core/mission_gateway_client.py`
  - Add typed methods for approval pending and token resolution.
- Test: `tests/test_approval_runtime.py`
- Test: `tests/test_mission_gateway.py`
- Test: `tests/test_mission_gateway_client.py`

### Documentation and Memory

- Modify: `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`
- Modify: `docs/superpowers/plans/2026-06-09-openclaw-parity-next-roadmap.md`
- Modify/Create: `memory/2026-06-10/fireclaw-work-resume.md`

---

## Task 1: PluginRuntime Callable Hook Boundary

**Files:**
- Modify: `src/fireclaw_core/plugin_runtime.py`
- Test: `tests/test_plugin_runtime.py`

- [x] **Step 1: Write failing tests for callable hook registration**

Add tests that prove descriptors alone do not execute arbitrary code and that known hook callables must be explicitly registered:

```python
from fireclaw_core.plugin_descriptor import FireClawPluginDescriptor
from fireclaw_core.plugin_runtime import PluginRuntime


def test_provider_hook_callable_must_be_registered_explicitly():
    runtime = PluginRuntime()
    runtime.register_descriptor(FireClawPluginDescriptor(
        plugin_id="fire.context",
        capabilities=("context",),
        preconditions=(),
        risk_level="low",
        provider_hooks=("enrich_context",),
    ))

    effects = runtime.run_provider_hooks(
        "enrich_context",
        {"command": "去二楼搜索", "context": {}},
    )

    assert effects == []


def test_registered_provider_hook_returns_structured_effect():
    runtime = PluginRuntime()
    runtime.register_callable(
        hook_type="provider",
        hook_name="enrich_context",
        plugin_id="fire.context",
        callback=lambda payload: {"extra_context": {"evacuation_route": "east stairs"}},
    )

    effects = runtime.run_provider_hooks(
        "enrich_context",
        {"command": "去二楼搜索", "context": {}},
    )

    assert effects == [
        {
            "plugin_id": "fire.context",
            "hook_name": "enrich_context",
            "effect": {"extra_context": {"evacuation_route": "east stairs"}},
        }
    ]
```

- [x] **Step 2: Run RED test**

Run:

```bash
.venv/bin/python -m pytest tests/test_plugin_runtime.py::test_provider_hook_callable_must_be_registered_explicitly tests/test_plugin_runtime.py::test_registered_provider_hook_returns_structured_effect -q
```

Expected:

```text
FAILED ... AttributeError: 'PluginRuntime' object has no attribute 'run_provider_hooks'
FAILED ... AttributeError: 'PluginRuntime' object has no attribute 'register_callable'
```

- [x] **Step 3: Implement minimal callable registry**

In `src/fireclaw_core/plugin_runtime.py`, add:

```python
from collections.abc import Callable
from dataclasses import dataclass


PluginHookCallback = Callable[[dict[str, Any]], dict[str, Any] | None]


@dataclass(frozen=True)
class PluginHookEffect:
    plugin_id: str
    hook_name: str
    effect: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "hook_name": self.hook_name,
            "effect": dict(self.effect),
        }
```

Extend `PluginRuntime.__init__()`:

```python
self._callables: dict[tuple[str, str], list[tuple[str, PluginHookCallback]]] = {}
```

Add methods:

```python
def register_callable(
    self,
    *,
    hook_type: str,
    hook_name: str,
    plugin_id: str,
    callback: PluginHookCallback,
) -> None:
    self._validate_known_hook(hook_type, hook_name, plugin_id)
    self._callables.setdefault((hook_type, hook_name), []).append((plugin_id, callback))


def run_provider_hooks(self, hook_name: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return self._run_hooks("provider", hook_name, payload)


def run_memory_hooks(self, hook_name: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return self._run_hooks("memory", hook_name, payload)


def run_tool_approval_hooks(self, hook_name: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return self._run_hooks("tool_approval", hook_name, payload)


def _run_hooks(self, hook_type: str, hook_name: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    self._validate_known_hook(hook_type, hook_name, "runtime")
    effects: list[dict[str, Any]] = []
    for plugin_id, callback in self._callables.get((hook_type, hook_name), []):
        result = callback(dict(payload))
        if result is None:
            continue
        if not isinstance(result, dict):
            raise ValueError(
                f"Plugin '{plugin_id}' {hook_type} hook '{hook_name}' must return a dict or None."
            )
        effects.append(PluginHookEffect(plugin_id, hook_name, result).to_dict())
    return effects


def _validate_known_hook(self, hook_type: str, hook_name: str, plugin_id: str) -> None:
    if hook_type == "provider":
        _validate_hook_names((hook_name,), KNOWN_PROVIDER_HOOKS, hook_type, plugin_id)
    elif hook_type == "memory":
        _validate_hook_names((hook_name,), KNOWN_MEMORY_HOOKS, hook_type, plugin_id)
    elif hook_type == "tool_approval":
        _validate_hook_names((hook_name,), KNOWN_TOOL_APPROVAL_HOOKS, hook_type, plugin_id)
    else:
        raise ValueError(f"Unknown hook type: {hook_type}")
```

- [x] **Step 4: Run GREEN test**

Run:

```bash
.venv/bin/python -m pytest tests/test_plugin_runtime.py -q
```

Expected:

```text
... passed
```

---

## Task 2: Wire Plugin Hooks Into Planner, Memory, and Approval

**Files:**
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `src/fireclaw_core/mission_gateway.py`
- Test: `tests/test_mission_agent.py`
- Test: `tests/test_mission_gateway.py`

- [x] **Step 1: Write failing planner context hook test**

Add to `tests/test_mission_agent.py`:

```python
from fireclaw_core.plugin_runtime import PluginRuntime


def test_plan_and_submit_applies_provider_context_hook(tmp_path):
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    runtime = PluginRuntime()
    runtime.register_callable(
        hook_type="provider",
        hook_name="enrich_context",
        plugin_id="fire.context",
        callback=lambda payload: {"retrieved_memories": [
            {
                "record_id": "plugin-memory",
                "mission_id": "plugin",
                "record_type": "lesson",
                "content": {"lesson": "优先检查东侧楼梯"},
                "source": "plugin",
            }
        ]},
    )
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims"),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(status="planned", message="ok", intent="search", plan=plan))
    mission = MissionAgent(registry=registry, subagent_client=client, planner=planner, plugin_runtime=runtime)

    result = mission.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    ctx = planner.calls[0][1]
    assert ctx.retrieved_memories[0]["record_id"] == "plugin-memory"
```

- [x] **Step 2: Write failing approval hook test**

Add to `tests/test_mission_gateway.py`:

```python
from fireclaw_core.plugin_runtime import PluginRuntime


def test_approval_request_applies_tool_approval_hook(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    runtime = PluginRuntime()
    runtime.register_callable(
        hook_type="tool_approval",
        hook_name="add_reason",
        plugin_id="fire.approval",
        callback=lambda payload: {"reason": "High heat area requires supervisor review."},
    )
    agent = MissionAgent(registry=registry, subagent_client=client, approval_store=approval_store)
    gw = MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=client,
        plugin_runtime=runtime,
    )

    result = gw.handle_approval("mission-1", {
        "action": "request",
        "semantic_action": "enter_building",
        "risk_level": "high",
        "command": "enter burning building",
    })

    assert result["status"] == "pending"
    assert result["approval_reasons"] == [
        {
            "plugin_id": "fire.approval",
            "reason": "High heat area requires supervisor review.",
        }
    ]
```

- [x] **Step 3: Run RED tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py::test_plan_and_submit_applies_provider_context_hook tests/test_mission_gateway.py::test_approval_request_applies_tool_approval_hook -q
```

Expected: fail because `MissionAgent` and `MissionGateway` do not yet accept `plugin_runtime`.

- [x] **Step 4: Implement minimal hook wiring**

Status: completed. Provider context hooks, tool approval hooks, and retrieved planner memory filter/rerank hooks are wired into the production paths. The earlier note that memory hooks were not applied is superseded by commit `c6bdef5`.

In `MissionAgent.__init__`, add:

```python
plugin_runtime: Any | None = None,
```

and:

```python
self.plugin_runtime = plugin_runtime
```

Before building `MissionPlannerContext`, add:

```python
if self.plugin_runtime is not None:
    for effect in self.plugin_runtime.run_provider_hooks(
        "enrich_context",
        {"command": command, "retrieved_memories": memories, "operator_corrections": corrections},
    ):
        payload = effect.get("effect", {})
        extra_memories = payload.get("retrieved_memories", [])
        if isinstance(extra_memories, list):
            memories.extend(redact_dict(m) for m in extra_memories if isinstance(m, dict))
        extra_corrections = payload.get("operator_corrections", [])
        if isinstance(extra_corrections, list):
            corrections.extend(redact_dict(c) for c in extra_corrections if isinstance(c, dict))
```

In `MissionGateway.__init__`, add:

```python
plugin_runtime: Any | None = None,
```

and:

```python
self.plugin_runtime = plugin_runtime
```

In `handle_approval()` after `result` is pending:

```python
approval_reasons: list[dict[str, str]] = []
if result.get("status") == "pending" and self.plugin_runtime is not None:
    for effect in self.plugin_runtime.run_tool_approval_hooks(
        "add_reason",
        {"mission_id": mission_id, "action": semantic_action, "payload": dict(payload)},
    ):
        reason = effect.get("effect", {}).get("reason")
        if isinstance(reason, str) and reason:
            approval_reasons.append({"plugin_id": str(effect.get("plugin_id", "")), "reason": reason})
if approval_reasons:
    result = {**result, "approval_reasons": approval_reasons}
```

- [x] **Step 5: Run hook integration tests**

Status: completed. Hook integration tests cover the production memory hook wiring added after the initial plan draft; the earlier unchecked note is superseded by commit `c6bdef5`.

Run:

```bash
.venv/bin/python -m pytest tests/test_plugin_runtime.py tests/test_mission_agent.py::test_plan_and_submit_applies_provider_context_hook tests/test_mission_gateway.py::test_approval_request_applies_tool_approval_hook -q
```

Expected: pass.

---

## Task 3: Lifecycle Projection Stores for Mission Runtime

**Files:**
- Modify: `src/fireclaw_core/task_registry.py`
- Modify: `src/fireclaw_core/subagent_registry.py`
- Modify: `src/fireclaw_core/mission_agent.py`
- Test: `tests/test_task_registry.py`
- Test: `tests/test_subagent_registry.py`
- Test: `tests/test_mission_agent.py`

- [x] **Step 1: Write failing TaskRegistry projection test**

Add to `tests/test_task_registry.py`:

```python
def test_task_registry_projects_mission_subtask_lifecycle(tmp_path):
    store = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")

    record = store.project_task_state(
        task_id="mission-1:subtask-1",
        requester_session_id="mission-1",
        owner_id="robot-1",
        command="搜索二楼",
        runtime="robot_gateway",
        scope_kind="mission",
        status="queued",
        delivery_status="pending",
        notify_policy="state_changes",
        created_at="2026-06-10T00:00:00+00:00",
        parent_task_id="mission-1",
        child_session_id="robot-1:task-1",
    )

    assert record.task_id == "mission-1:subtask-1"
    assert store.get("mission-1:subtask-1").status == "queued"

    updated = store.project_task_state(
        task_id="mission-1:subtask-1",
        requester_session_id="mission-1",
        owner_id="robot-1",
        command="搜索二楼",
        runtime="robot_gateway",
        scope_kind="mission",
        status="completed",
        delivery_status="delivered",
        notify_policy="state_changes",
        created_at="2026-06-10T00:00:00+00:00",
        ended_at="2026-06-10T00:00:10+00:00",
        terminal_outcome="succeeded",
    )

    assert updated.status == "completed"
    assert store.get("mission-1:subtask-1").terminal_outcome == "succeeded"
```

- [x] **Step 2: Run RED test**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_registry.py::test_task_registry_projects_mission_subtask_lifecycle -q
```

Expected: fail because `project_task_state` does not exist.

- [x] **Step 3: Implement projection helper**

In `JsonlTaskRegistryStore`, add:

```python
def project_task_state(
    self,
    *,
    task_id: str,
    requester_session_id: str,
    owner_id: str,
    command: str,
    runtime: str,
    scope_kind: str,
    status: str,
    delivery_status: str,
    notify_policy: str,
    created_at: str,
    parent_task_id: str | None = None,
    child_session_id: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    terminal_outcome: str | None = None,
) -> TaskRecord:
    current = self.get(task_id)
    record = TaskRecord(
        task_id=task_id,
        runtime=runtime,
        requester_session_id=requester_session_id,
        owner_id=owner_id,
        scope_kind=scope_kind,
        command=command,
        status=status,
        delivery_status=delivery_status,
        notify_policy=notify_policy,
        created_at=current.created_at if current is not None and current.created_at else created_at,
        parent_task_id=parent_task_id if parent_task_id is not None else (current.parent_task_id if current else None),
        child_session_id=child_session_id if child_session_id is not None else (current.child_session_id if current else None),
        started_at=started_at if started_at is not None else (current.started_at if current else None),
        ended_at=ended_at if ended_at is not None else (current.ended_at if current else None),
        error=error if error is not None else (current.error if current else None),
        result=result if result is not None else (current.result if current else None),
        terminal_outcome=terminal_outcome if terminal_outcome is not None else (current.terminal_outcome if current else None),
        last_event_at=ended_at or started_at or created_at,
    )
    self._append(record.to_dict())
    return record
```

- [x] **Step 4: Add SubagentRegistry terminal helper test**

Add to `tests/test_subagent_registry.py`:

```python
def test_subagent_registry_mark_terminal_is_idempotent(tmp_path):
    store = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")
    record = store.create(
        parent_mission_id="mission-1",
        parent_subtask_id="subtask-1",
        robot_id="robot-1",
        child_task_id="task-1",
        created_at="2026-06-10T00:00:00+00:00",
    )

    first = store.mark_terminal(
        child_task_id="task-1",
        status="completed",
        updated_at="2026-06-10T00:00:10+00:00",
    )
    second = store.mark_terminal(
        child_task_id="task-1",
        status="failed",
        updated_at="2026-06-10T00:00:11+00:00",
    )

    assert first.status == "completed"
    assert second.status == "completed"
    assert store.get_by_run_id(record.run_id).status == "completed"
```

- [x] **Step 5: Implement SubagentRegistry terminal helper**

In `JsonlSubagentRegistry`, add:

```python
def mark_terminal(
    self,
    *,
    child_task_id: str,
    status: str,
    updated_at: str,
    error: str | None = None,
) -> SubagentRunRecord | None:
    current = self.get_by_child_task_id(child_task_id)
    if current is None:
        return None
    if current.is_terminal:
        return current
    return self.update(
        current.run_id,
        status=status,
        delivery_status="delivered",
        updated_at=updated_at,
        error=error,
    )
```

- [x] **Step 6: Run lifecycle store tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_registry.py tests/test_subagent_registry.py -q
```

Expected: pass.

---

## Task 4: Wire Lifecycle Projection Into MissionAgent and Aggregator

**Files:**
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `src/fireclaw_core/mission_event_aggregator.py`
- Test: `tests/test_mission_agent.py`
- Test: `tests/test_mission_event_aggregator.py`

- [x] **Step 1: Write failing MissionAgent projection test**

Add to `tests/test_mission_agent.py`:

```python
from fireclaw_core.task_registry import JsonlTaskRegistryStore
from fireclaw_core.subagent_registry import JsonlSubagentRegistry


def test_plan_and_submit_projects_subtask_lifecycle_records(tmp_path):
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    task_registry = JsonlTaskRegistryStore(tmp_path / "task_registry.jsonl")
    subagent_registry = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims"),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(status="planned", message="ok", intent="search", plan=plan))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        task_registry=task_registry,
        subagent_registry=subagent_registry,
    )

    result = mission.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    projected = task_registry.list_records()
    assert len(projected) == 1
    assert projected[0].requester_session_id == "mission-1"
    assert projected[0].owner_id == "r1"
    assert projected[0].status in {"accepted", "queued"}
```

- [x] **Step 2: Run RED test**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py::test_plan_and_submit_projects_subtask_lifecycle_records -q
```

Expected: fail because `MissionAgent.__init__()` does not accept `task_registry` / `subagent_registry`.

- [x] **Step 3: Implement MissionAgent projection wiring**

Status: completed. Mission and default scheduler subtask submission paths project into `TaskRegistry`, and `MissionAgent(subagent_registry=...)` auto-wires the registry into the default `RobotSubagentClient`. The earlier limitation note is superseded by commit `c6bdef5`.

In `MissionAgent.__init__`, add optional parameters:

```python
task_registry: Any | None = None,
subagent_registry: Any | None = None,
```

Store:

```python
self.task_registry = task_registry
self.subagent_registry = subagent_registry
```

After each successful `submit_subtask()` result in the mission planning path, project state:

```python
if self.task_registry is not None and result.get("task_id"):
    self.task_registry.project_task_state(
        task_id=f"{mission_id}:{result.get('task_id')}",
        requester_session_id=mission_id,
        owner_id=str(result.get("robot_id") or subtask.robot_id),
        command=subtask.command,
        runtime="robot_gateway",
        scope_kind="mission",
        status=str(result.get("status") or "accepted"),
        delivery_status="delivered" if result.get("status") == "accepted" else "pending",
        notify_policy="state_changes",
        created_at=created_at,
        parent_task_id=mission_id,
        child_session_id=str(result.get("task_id")),
    )
```

- [x] **Step 4: Add aggregator terminal routing test**

Add to `tests/test_mission_event_aggregator.py`:

```python
def test_aggregator_routes_terminal_robot_events_to_subagent_registry(tmp_path):
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")
    subagents.create(
        parent_mission_id="mission-1",
        parent_subtask_id="subtask-1",
        robot_id="robot-1",
        child_task_id="task-1",
        created_at="2026-06-10T00:00:00+00:00",
    )
    client = FakeSubagentClient(events_by_entry={
        ("robot-1", "task-1"): [
            {
                "type": "task.completed",
                "task_id": "task-1",
                "timestamp": "2026-06-10T00:00:10+00:00",
                "payload": {"status": "completed"},
            }
        ]
    })
    aggregator = MissionEventAggregator(
        registry=_robot_registry(),
        subagent_client=client,
        mission_registry=mission_reg,
        subagent_registry=subagents,
    )

    aggregator.aggregate("mission-1")

    assert subagents.get_by_child_task_id("task-1").status == "completed"
```

- [x] **Step 5: Implement aggregator routing**

In `MissionEventAggregator.__init__`, add:

```python
subagent_registry: Any | None = None,
```

Store it. While processing events:

```python
if self.subagent_registry is not None:
    status = _terminal_status_from_event(event)
    task_id = event.get("task_id")
    if status is not None and isinstance(task_id, str):
        self.subagent_registry.mark_terminal(
            child_task_id=task_id,
            status=status,
            updated_at=str(event.get("timestamp") or datetime.now(timezone.utc).isoformat()),
        )
```

Add helper:

```python
def _terminal_status_from_event(event: dict[str, Any]) -> str | None:
    event_type = event.get("type") or event.get("event_type")
    if event_type == "task.completed":
        return "completed"
    if event_type == "task.failed":
        return "failed"
    if event_type == "task.cancelled":
        return "cancelled"
    payload = event.get("payload")
    if isinstance(payload, dict):
        status = payload.get("status")
        if status in {"completed", "failed", "cancelled", "timed_out", "lost"}:
            return str(status)
    return None
```

- [x] **Step 6: Run projection integration tests**

Status: completed. Projection integration tests cover default subtask projection and direct `subagent_registry` auto-wiring; the earlier unchecked note is superseded by commit `c6bdef5`.

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py::test_plan_and_submit_projects_subtask_lifecycle_records tests/test_mission_event_aggregator.py::test_aggregator_routes_terminal_robot_events_to_subagent_registry -q
```

Expected: pass.

---

## Task 5: Persistent Approval Runtime Token Store

**Files:**
- Modify: `src/fireclaw_core/approval_runtime.py`
- Test: `tests/test_approval_runtime.py`

- [x] **Step 1: Write failing persistence test**

Add to `tests/test_approval_runtime.py`:

```python
def test_approval_runtime_persists_token_hash_across_instances(tmp_path):
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    request_id = _create_request(store)
    token_path = tmp_path / "approval_tokens.jsonl"

    runtime = ApprovalRuntime(store, token_store_path=token_path, token_ttl_seconds=300)
    raw_token, record = runtime.create_token(request_id)

    restarted = ApprovalRuntime(store, token_store_path=token_path, token_ttl_seconds=300)
    resolved = restarted.resolve_token(raw_token)

    assert resolved is not None
    assert resolved.request_id == request_id
    assert raw_token not in token_path.read_text(encoding="utf-8")
    assert record.token_hash in token_path.read_text(encoding="utf-8")
```

- [x] **Step 2: Run RED test**

Run:

```bash
.venv/bin/python -m pytest tests/test_approval_runtime.py::test_approval_runtime_persists_token_hash_across_instances -q
```

Expected: fail because `ApprovalRuntime.__init__()` does not accept `token_store_path`.

- [x] **Step 3: Implement JSONL token persistence**

In `ApprovalRuntime.__init__`, add:

```python
token_store_path: str | Path | None = None,
```

Store:

```python
self._token_store_path = Path(token_store_path) if token_store_path is not None else None
self._tokens = self._load_tokens()
```

Add helpers:

```python
def _load_tokens(self) -> Dict[str, ApprovalRuntimeToken]:
    if self._token_store_path is None or not self._token_store_path.exists():
        return {}
    records: Dict[str, ApprovalRuntimeToken] = {}
    with self._token_store_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and isinstance(data.get("token_hash"), str):
                token = ApprovalRuntimeToken(**data)
                records[token.token_hash] = token
    return records


def _persist_token(self, record: ApprovalRuntimeToken) -> None:
    if self._token_store_path is None:
        return
    self._token_store_path.parent.mkdir(parents=True, exist_ok=True)
    with self._token_store_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
        handle.write("\n")
```

Call `_persist_token(record)` in `create_token()` and after updates in `expire_stale()`.

- [x] **Step 4: Run approval runtime tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_approval_runtime.py -q
```

Expected: pass.

---

## Task 6: Operator Relay Projection and MissionGatewayClient Methods

**Files:**
- Modify: `src/fireclaw_core/mission_gateway.py`
- Modify: `src/fireclaw_core/mission_gateway_client.py`
- Test: `tests/test_mission_gateway.py`
- Test: `tests/test_mission_gateway_client.py`

- [x] **Step 1: Write failing relay projection test**

Add to `tests/test_mission_gateway.py`:

```python
def test_pending_approval_projection_includes_relay_context(tmp_path):
    registry = _make_registry()
    client = FakeSubagentClient()
    approval_store = JsonlApprovalStore(str(tmp_path / "approvals.jsonl"))
    runtime = ApprovalRuntime(approval_store, token_store_path=tmp_path / "tokens.jsonl")
    agent = MissionAgent(registry=registry, subagent_client=client, approval_store=approval_store)
    gw = MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=client,
        approval_runtime=runtime,
    )

    request_result = gw.handle_approval("mission-1", {
        "action": "request",
        "semantic_action": "enter_building",
        "risk_level": "high",
        "command": "enter burning building",
        "relay": {"channel": "console", "operator_id": "op-1"},
    })
    pending = gw.handle_approval("mission-1", {"action": "pending"})

    assert request_result["status"] == "pending"
    assert pending["pending_approvals"][0]["relay"]["channel"] == "console"
    assert pending["pending_approvals"][0]["relay"]["operator_id"] == "op-1"
```

- [x] **Step 2: Run RED test**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_gateway.py::test_pending_approval_projection_includes_relay_context -q
```

Expected: fail because pending projection does not preserve relay context.

- [x] **Step 3: Add relay metadata to MissionGateway pending projection**

In `MissionGateway`, add a small in-process relay map:

```python
self._approval_relays: dict[str, dict[str, Any]] = {}
```

When request result is pending:

```python
relay = payload.get("relay")
if isinstance(relay, dict) and isinstance(request_id, str):
    self._approval_relays[request_id] = {
        "channel": str(relay.get("channel") or "console"),
        "operator_id": str(relay.get("operator_id") or "unknown"),
    }
```

When building `pending`:

```python
for item in pending:
    relay = self._approval_relays.get(str(item.get("request_id")))
    if relay is not None:
        item["relay"] = dict(relay)
```

- [x] **Step 4: Add MissionGatewayClient typed methods tests**

Add to `tests/test_mission_gateway_client.py`:

```python
def test_client_approval_pending_and_resolve_token_requests() -> None:
    sent = []

    class FakeClient(MissionGatewayClient):
        def _post(self, path, body):
            sent.append((path, body))
            return {"status": "ok"}

    client = FakeClient("http://localhost")

    client.get_pending_approvals("mission-1")
    client.resolve_approval_token("mission-1", "raw-token")

    assert sent == [
        ("/missions/mission-1/approvals", {"action": "pending"}),
        ("/missions/mission-1/approvals", {"action": "resolve_token", "approval_token": "raw-token"}),
    ]
```

- [x] **Step 5: Implement client methods**

In `MissionGatewayClient`, add:

```python
def get_pending_approvals(self, mission_id: str) -> dict[str, Any]:
    return self._post(f"/missions/{mission_id}/approvals", {"action": "pending"})


def resolve_approval_token(self, mission_id: str, approval_token: str) -> dict[str, Any]:
    return self._post(
        f"/missions/{mission_id}/approvals",
        {"action": "resolve_token", "approval_token": approval_token},
    )
```

- [x] **Step 6: Run approval gateway/client tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_approval_runtime.py tests/test_mission_gateway.py::test_pending_approval_projection_includes_relay_context tests/test_mission_gateway_client.py::test_client_approval_pending_and_resolve_token_requests -q
```

Expected: pass.

---

## Task 7: Documentation, Memory, and Verification

**Files:**
- Modify: `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`
- Modify: `docs/superpowers/plans/2026-06-09-openclaw-parity-next-roadmap.md`
- Modify: `memory/2026-06-10/fireclaw-work-resume.md`

- [x] **Step 1: Update architecture roadmap**

Update the remaining gap wording:

```markdown
PluginRuntime 已支持受限 callable hook 注册和执行；剩余缺口是第三方插件加载、安全沙箱、权限审计。

TaskRegistry/SubagentRegistry 已可作为 lifecycle projection；剩余缺口是跨进程 reconciliation 和 orphan recovery 自动修复。

ApprovalRuntime 已支持持久化 token hash 和 relay-ready pending projection；剩余缺口是外部 operator channel 的实际发送适配器。
```

- [x] **Step 2: Update memory record**

Append to `memory/2026-06-10/fireclaw-work-resume.md`:

```markdown
## Update 2026-06-10 Runtime Hardening Plan Execution

### Task Goal

Execute `docs/superpowers/plans/2026-06-10-openclaw-parity-runtime-hardening.md`.

### Verification

- `.venv/bin/python -m pytest -q`
  - Result: ...
```

- [x] **Step 3: Run targeted tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_plugin_runtime.py \
  tests/test_mission_agent.py \
  tests/test_mission_gateway.py \
  tests/test_mission_gateway_client.py \
  tests/test_task_registry.py \
  tests/test_subagent_registry.py \
  tests/test_mission_event_aggregator.py \
  tests/test_approval_runtime.py \
  -q
```

Expected: all pass.

- [x] **Step 4: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected:

```text
all tests pass, ROS1 smoke tests skipped by default
```

- [x] **Step 5: Check whitespace and status**

Run:

```bash
git diff --check
git status --short --branch
```

Expected:

```text
git diff --check exits 0
status shows only intended files
```

Do not commit unless the user explicitly asks.

---

## Final Acceptance Criteria

- `PluginRuntime` can execute explicitly registered provider, memory, and tool approval callables and reject unknown hook names.
- `MissionAgent` can apply provider context hook effects before planner invocation.
- `MissionGateway` can apply approval hook effects to pending approval requests.
- `TaskRegistry` exposes idempotent lifecycle projection helpers.
- `SubagentRegistry` exposes idempotent terminal update helpers.
- Mission runtime can project submitted subtasks into `TaskRegistry`.
- Mission event aggregation can route terminal robot events into `SubagentRegistry`.
- `ApprovalRuntime` can persist token hashes across process restart without storing raw tokens.
- Mission approval pending projection can carry relay-ready operator context.
- `MissionGatewayClient` has typed pending approval and token resolution helpers.
- Full test suite passes with ROS1 smoke skipped by default.

## Known Remaining Gaps After This Plan

- Native ROS2 adapter remains out of scope.
- Real robot hardware proof remains out of scope.
- Full external operator relay adapters are not implemented; this plan only adds relay-ready projection.
- Arbitrary third-party plugin loading remains out of scope; this plan adds explicit callable registration and execution boundaries.
