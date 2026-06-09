# Phase 8-10 Completion and Deployment Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Complete the remaining Phase 9 ROS1 proof, Phase 8 realtime event stream integration, Phase 10 memory/plugin runtime, and deployment/documentation cleanup in one coordinated roadmap.

**Architecture:** Treat this as one umbrella plan with four independently testable workstreams. Finish the robot integration proof first, then make live/replay events authoritative, then add retrieval/plugin extension surfaces, and finally reconcile documentation, memory, and the dirty working tree. Keep robot-local authority intact: main mission code calls robot subagents through Gateway/client contracts and never bypasses robot-local safety/ROS adapters.

**Tech Stack:** Python 3.11, pytest, stdlib `http.server`, stdlib `sqlite3`, JSONL stores, ROS1 Noetic (`rospy`, `actionlib`) for smoke tests, existing FireClaw Gateway/MissionAgent/Memory/Skill modules.

---

## Current Baseline

- Full local suite is green: `.venv/bin/python -m pytest -q` -> `582 passed, 6 warnings`.
- Phase 6 is effectively implemented in code: `MissionAgent.plan_and_submit(..., use_scheduler=True)` defaults to scheduler, and `MissionGateway` exists.
- Phase 7 is substantially implemented: `method_scopes.py`, Gateway scope enforcement, and security review docs exist.
- Phase 8 exists as a framework: `StreamEvent`, `EventBus`, `TelemetryTracker`, robot SSE, mission SSE, partial replay inclusion.
- Phase 9 smoke tests pass locally, but transport-level dict-to-real-ROS message conversion is not complete; several smoke tests construct ROS message objects directly.
- Phase 10 is not implemented beyond JSONL record/search memory and existing skill manifests.
- Deployment cleanup is still needed: many files are uncommitted/untracked, and some roadmap/plan/memory text is stale relative to code.

---

## File Structure

### Phase 9: ROS1 Proof Completion

- Modify: `src/fireclaw_core/ros1_transport.py`
  - Add robust ROS message construction from dicts for topic payloads, service requests, and action goals.
  - Preserve support for callers that already pass real ROS message objects.
- Modify: `tests/test_ros1_transport.py`
  - Add fake message classes with `__slots__`/`_slot_types` for recursive conversion.
  - Verify action timeout uses `status="timeout"`.
- Modify: `tests/test_ros1_smoke.py`
  - Route at least one topic/action smoke test through `Ros1Transport.execute()` with dict payload after conversion exists.
  - Keep direct real-message tests only where they prove actionlib behavior that transport cannot expose.
- Modify: `examples/ros1_configs/*.yaml`
  - Ensure examples demonstrate dict payload shape matching real ROS messages.
- Modify: `docs/deployment/ros1-deployment-guide.md`
  - Document dict payload conversion limits and when to use real message objects.

### Phase 8: Realtime Stream and Replay Completion

- Modify: `src/fireclaw_core/stream_events.py`
  - Add conversion helpers from ledger/mission events into `StreamEvent`.
  - Add stable event type constants if this reduces string drift.
- Modify: `src/fireclaw_core/gateway.py`
  - Ensure task/action/cancel/emergency lifecycle events are emitted to both `EventLedger` and `EventBus`.
  - Ensure `/events/stream` streams historical events plus live events using one schema.
- Modify: `src/fireclaw_core/mission_gateway.py`
  - Emit mission-level events for submit, planned, subtask dispatch, cancel, approval, and fleet checks.
  - Ensure `/missions/{id}/events/stream` uses the same schema as replay.
- Modify: `src/fireclaw_core/incident_replay.py`
  - Normalize replay timeline entries to the `StreamEvent` shape or a strict superset.
- Modify: `tests/test_stream_events.py`
  - Cover conversion helpers and telemetry updates from real lifecycle event names.
- Modify: `tests/test_gateway.py`
  - Cover robot-local lifecycle event emission and SSE schema.
- Modify: `tests/test_mission_gateway.py`
  - Cover mission event emission and mission SSE schema.
- Modify: `tests/test_incident_replay.py`
  - Cover replay/live schema alignment.

### Phase 10: Memory Retrieval and Plugin Runtime

- Create: `src/fireclaw_core/memory_index.py`
  - Add a lightweight SQLite FTS-backed index over mission memory records.
  - Support filters for mission, robot, floor, capability, outcome, operator, and risk level where records provide those fields.
- Modify: `src/fireclaw_core/mission_memory.py`
  - Add structured retrieval APIs that can use the index when configured and fall back to JSONL keyword search.
- Modify: `src/fireclaw_core/mission_agent.py`
  - Feed relevant retrieved memories and operator corrections into planner context.
  - Keep behavior deterministic when no memory index is configured.
- Modify: `src/fireclaw_core/mission_planner.py`
  - Extend `MissionPlannerContext` with retrieved memory/correction fields.
- Modify: `src/fireclaw_core/llm_planner.py`
  - Include retrieved memories/corrections in the planner prompt/tool context, with redaction where needed.
- Create: `src/fireclaw_core/plugin_descriptor.py`
  - Define FireClaw plugin/skill descriptor contracts: capability, preconditions, risk, sensors, adapter binding, approval scope, provider/memory hooks.
- Modify: `src/fireclaw_core/skill_manifest.py`
  - Add compatibility helpers from current skill manifest metadata to plugin descriptors.
- Create: `tests/test_memory_index.py`
  - Cover FTS indexing, filters, corrupt/missing index handling, and fallback behavior.
- Modify: `tests/test_mission_memory.py`
  - Cover indexed retrieval integration.
- Modify: `tests/test_mission_agent.py`
  - Cover correction/memory retrieval entering planner context.
- Modify: `tests/test_llm_planner.py`
  - Cover retrieved memory in LLM prompt context without asserting model quality.
- Create: `tests/test_plugin_descriptor.py`
  - Cover descriptor validation and skill manifest compatibility.

### Deployment and Documentation Cleanup

- Modify: `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`
  - Update stale status counts and clearly mark Phase 6/7 done, Phase 8/9 partial or complete after implementation, Phase 10 new status.
- Modify: `docs/superpowers/plans/2026-06-09-phase8-realtime-event-stream.md`
  - Update checkboxes/status after Phase 8 work is complete.
- Modify: `docs/superpowers/plans/2026-06-09-phase9-ros1-integration-proof.md`
  - Update checkboxes/status after Phase 9 work is complete.
- Modify/Create: `docs/deployment/fireclaw-deployment-checklist.md`
  - Add token binding, network binding, secret handling, log redaction, ROS mode separation, smoke test profile, retention policy.
- Modify/Create: `memory/2026-06-09/fireclaw-work-resume.md` or `memory/2026-06-10/fireclaw-work-resume.md`
  - Record commands, test results, files modified, and remaining research risks.

---

## Task 1: Phase 9 Audit and Red Tests for ROS Message Conversion

**Files:**
- Modify: `tests/test_ros1_transport.py`
- Inspect: `src/fireclaw_core/ros1_transport.py`
- Inspect: `tests/test_ros1_smoke.py`

- [x] **Step 1: Identify current transport gaps**

Run:

```bash
rg -n "transport.execute|FibonacciGoal|Twist|payload|send_goal|publish" tests/test_ros1_transport.py tests/test_ros1_smoke.py src/fireclaw_core/ros1_transport.py
```

Expected:

- At least one ROS1 smoke action/topic path constructs real message objects directly.
- `Ros1Transport.execute()` still passes raw payloads to publisher/service/action client.

- [x] **Step 2: Add failing unit tests for dict-to-message construction**

Add tests to `tests/test_ros1_transport.py` using fake message classes:

```python
class FakeVector3:
    __slots__ = ("x", "y", "z")
    _slot_types = ("float64", "float64", "float64")

    def __init__(self):
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0


class FakeTwist:
    __slots__ = ("linear", "angular")
    _slot_types = ("geometry_msgs/Vector3", "geometry_msgs/Vector3")

    def __init__(self):
        self.linear = FakeVector3()
        self.angular = FakeVector3()


class FakeFibonacciGoal:
    __slots__ = ("order",)
    _slot_types = ("int32",)

    def __init__(self):
        self.order = 0
```

Test expectations:

```python
def test_ros1_transport_builds_topic_message_from_dict():
    # Patch module.resolve_message_class("geometry_msgs/Twist") -> FakeTwist.
    # Execute topic with {"linear": {"x": 1.0}, "angular": {"z": 0.5}}.
    # Assert publisher received FakeTwist with nested fields set.


def test_ros1_transport_builds_action_goal_from_dict():
    # Patch module.resolve_action_goal_class("actionlib_tutorials/FibonacciAction") -> FakeFibonacciGoal.
    # Execute action with {"order": 5}.
    # Assert fake action client received FakeFibonacciGoal(order=5).
```

- [x] **Step 3: Run red tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_transport.py::test_ros1_transport_builds_topic_message_from_dict tests/test_ros1_transport.py::test_ros1_transport_builds_action_goal_from_dict -q
```

Expected:

- FAIL because conversion helpers do not exist or raw dicts are still sent.

---

## Task 2: Implement ROS Dict-to-Message Conversion

**Files:**
- Modify: `src/fireclaw_core/ros1_transport.py`
- Modify: `tests/test_ros1_transport.py`

- [x] **Step 1: Add resolver methods to `Ros1RuntimeModule`**

Implement methods equivalent to:

```python
def resolve_message_class(self, type_name: str) -> Any:
    return self._resolve_ros_type(type_name, preferred_module="msg")


def resolve_action_goal_class(self, action_type_name: str) -> Any:
    action_cls = self._resolve_ros_type(action_type_name, preferred_module="msg")
    goal_cls_name = action_cls.__name__.removesuffix("Action") + "Goal"
    module_name = action_cls.__module__
    module = import_module(module_name)
    return getattr(module, goal_cls_name)
```

- [x] **Step 2: Add recursive message builder**

Implement:

```python
def _build_ros_message(message_cls: Any, payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    msg = message_cls()
    slots = getattr(msg, "__slots__", ())
    slot_types = getattr(msg, "_slot_types", ())
    for field_name, field_type in zip(slots, slot_types):
        if field_name not in payload:
            continue
        value = payload[field_name]
        current = getattr(msg, field_name, None)
        if isinstance(value, dict) and hasattr(current, "__slots__"):
            setattr(msg, field_name, _fill_ros_message(current, value))
        else:
            setattr(msg, field_name, value)
    unknown = set(payload) - set(slots)
    if unknown:
        raise ValueError(f"Unknown ROS message fields for {message_cls.__name__}: {sorted(unknown)}")
    return msg
```

Use a helper `_fill_ros_message(existing_msg, payload)` for nested objects.

- [x] **Step 3: Convert payloads at transport boundary**

Rules:

- Topic:
  - If payload is dict, resolve message class from `endpoint.type`, build message, publish message.
  - If payload is already a ROS message object, publish it unchanged.
- Service:
  - Empty dict still calls `service()`.
  - Non-empty dict may remain service-specific until service request conversion is added; document if v1 only supports empty and direct request objects.
- Action:
  - If payload is dict, resolve action goal class from `endpoint.type`, build goal, send goal.
  - If payload is already a ROS goal object, send it unchanged.

- [x] **Step 4: Run unit tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_transport.py -q
```

Expected:

- PASS.

---

## Task 3: Route ROS Smoke Tests Through Transport Conversion

**Files:**
- Modify: `tests/test_ros1_smoke.py`

- [x] **Step 1: Update smoke tests**

Change at least:

- `test_ros1_topic_publish_to_turtlesim` to call `transport.execute(endpoint, {"linear": ..., "angular": ...}, config)` instead of directly publishing `Twist`.
- `test_ros1_action_fibonacci_goal` to call `transport.execute(endpoint, {"order": 5}, config)` instead of directly sending `FibonacciGoal`.
- Keep `test_ros1_action_cancel` direct if cancellation requires direct client control before transport exposes a cancellable async operation.

- [x] **Step 2: Run ROS smoke tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_smoke.py -q
```

Expected:

- PASS with only ROS dependency deprecation warnings.

- [x] **Step 3: Update ROS docs/examples**

Update `docs/deployment/ros1-deployment-guide.md` and `examples/ros1_configs/*.yaml` to show dict payload shapes that transport can convert.

---

## Task 4: Phase 8 Event Path Audit

**Files:**
- Inspect: `src/fireclaw_core/gateway.py`
- Inspect: `src/fireclaw_core/mission_gateway.py`
- Inspect: `src/fireclaw_core/incident_replay.py`
- Inspect: `src/fireclaw_core/stream_events.py`
- Modify: `memory/2026-06-09/fireclaw-work-resume.md` or current date memory file

- [x] **Step 1: Build event coverage table**

Audit these event types:

```text
task.received
task.running
action.started
action.feedback
action.succeeded
action.failed
task.completed
task.failed
task.cancel_requested
task.cancelled
emergency.stop
mission.submitted
mission.planned
mission.subtask_dispatched
mission.cancel_requested
mission.cancelled
mission.approval_requested
mission.approval_decided
fleet.presence_checked
```

Record whether each reaches:

```text
EventLedger
EventBus
SSE
IncidentReplay
TelemetryTracker
```

- [x] **Step 2: Decide minimum v1 event set**

For this plan, v1 must cover:

- robot task lifecycle;
- action feedback/result/cancel;
- emergency stop;
- mission submit/cancel/approval;
- fleet presence check.

Do not add UI-specific events.

---

## Task 5: Robot-Local Unified Event Emission

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Modify: `src/fireclaw_core/stream_events.py`
- Modify: `tests/test_gateway.py`
- Modify: `tests/test_stream_events.py`

- [x] **Step 1: Add helper tests**

Add tests proving a ledger-style task event can become a `StreamEvent`:

```python
def test_stream_event_from_task_ledger_event():
    event = stream_event_from_ledger_record(
        {
            "type": "task.completed",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "task_id": "task-1",
            "session_id": "mission-1",
            "payload": {"status": "completed"},
        },
        source="robot-alpha",
        robot_id="robot-alpha",
    )
    assert event.event_type == "task.completed"
    assert event.mission_id == "mission-1"
    assert event.robot_id == "robot-alpha"
    assert event.task_id == "task-1"
```

- [x] **Step 2: Add Gateway event publishing helper**

In `FireClawGateway`, add a private helper:

```python
def _publish_stream_event(
    self,
    event_type: str,
    *,
    task_id: str | None = None,
    mission_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    event = StreamEvent(
        event_type=event_type,
        source=self.config.robot_id,
        robot_id=self.config.robot_id,
        task_id=task_id,
        mission_id=mission_id,
        payload=payload or {},
    )
    self._event_bus.publish(event)
    self._telemetry.record_event(event)
```

- [x] **Step 3: Emit lifecycle events**

Call the helper from task submit/run/cancel/emergency paths so the following are emitted:

```text
task.received
task.running
action.feedback
task.completed
task.failed
task.cancel_requested
task.cancelled
emergency.stop
```

- [x] **Step 4: Test robot SSE receives lifecycle events**

Add tests in `tests/test_gateway.py` that:

- start gateway;
- subscribe to `/events/stream`;
- submit/cancel task or trigger emergency stop;
- assert SSE payload has `event_type`, `source`, `robot_id`, `task_id`, `timestamp`, `sequence`, `payload`.

- [x] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_stream_events.py tests/test_gateway.py -q
```

Expected:

- PASS.

---

## Task 6: Mission-Level Unified Event Emission

**Files:**
- Modify: `src/fireclaw_core/mission_gateway.py`
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `tests/test_mission_gateway.py`

- [x] **Step 1: Add mission event tests**

Add tests that prove:

- `POST /missions` emits `mission.submitted` and `mission.planned`;
- `POST /missions/{id}/cancel` emits `mission.cancel_requested` or `mission.cancelled`;
- `POST /missions/{id}/approvals` emits approval events;
- `GET /missions/{id}/events/stream` uses `StreamEvent` schema.

- [x] **Step 2: Add mission publish helper**

Use the existing `MissionGateway.publish_event(...)`, but make sure every mission control endpoint calls it after successful operations.

- [x] **Step 3: Include subtask dispatch metadata**

When mission submit returns subtask results, publish one `mission.subtask_dispatched` event per accepted subtask:

```python
payload = {
    "robot_id": result.get("robot_id"),
    "task_id": result.get("task_id"),
    "status": result.get("status"),
}
```

- [x] **Step 4: Run focused mission tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_gateway.py tests/test_mission_agent.py -q
```

Expected:

- PASS.

---

## Task 7: Replay and Live Stream Schema Alignment

**Files:**
- Modify: `src/fireclaw_core/incident_replay.py`
- Modify: `tests/test_incident_replay.py`
- Modify: `src/fireclaw_core/stream_events.py`

- [x] **Step 1: Add replay schema tests**

Add assertions that replay timeline entries include:

```text
event_type
timestamp
source
mission_id
robot_id
task_id
payload
```

- [x] **Step 2: Normalize replay entries**

Use `StreamEvent.to_dict()` shape for replay events where possible. If replay needs extra fields, place them under `payload` or add a documented `replay_metadata` key.

- [x] **Step 3: Run replay tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_incident_replay.py tests/test_stream_events.py -q
```

Expected:

- PASS.

---

## Task 8: Phase 10 Memory Index v1

**Files:**
- Create: `src/fireclaw_core/memory_index.py`
- Modify: `src/fireclaw_core/mission_memory.py`
- Create: `tests/test_memory_index.py`
- Modify: `tests/test_mission_memory.py`

- [x] **Step 1: Add failing tests for SQLite FTS index**

Test:

- index mission memory records;
- search by text;
- filter by `mission_id`, `robot_id`, `floor`, `capability`, `outcome`, `operator`, `risk_level`;
- tolerate missing index file;
- rebuild index from JSONL records.

- [x] **Step 2: Implement `SqliteMemoryIndex`**

Minimal API:

```python
class SqliteMemoryIndex:
    def __init__(self, path: str | Path) -> None: ...
    def upsert(self, record: dict[str, Any]) -> None: ...
    def search(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]: ...
    def rebuild(self, records: Iterable[dict[str, Any]]) -> int: ...
```

- [x] **Step 3: Integrate optional index into mission memory store**

Do not make SQLite mandatory for existing users. If no index path is configured, keep JSONL keyword search behavior.

- [x] **Step 4: Run memory tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_index.py tests/test_mission_memory.py -q
```

Expected:

- PASS.

---

## Task 9: Operator Corrections Into Planner Context

**Files:**
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `src/fireclaw_core/mission_planner.py`
- Modify: `src/fireclaw_core/llm_planner.py`
- Modify: `tests/test_mission_agent.py`
- Modify: `tests/test_mission_planner.py`
- Modify: `tests/test_llm_planner.py`

- [x] **Step 1: Extend planner context tests**

Add tests that:

- record an operator correction;
- submit a similar command;
- verify retrieved correction appears in `MissionPlannerContext`;
- verify LLM planner prompt includes a concise correction summary.

- [x] **Step 2: Extend `MissionPlannerContext`**

Add fields:

```python
retrieved_memories: list[dict[str, Any]] = field(default_factory=list)
operator_corrections: list[dict[str, Any]] = field(default_factory=list)
```

- [x] **Step 3: Populate context in `MissionAgent.plan_and_submit()`**

Before planner invocation:

- search memory/correction records by command and available metadata;
- cap context size;
- redact secrets before passing to LLM planner.

- [x] **Step 4: Run planner tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py tests/test_mission_planner.py tests/test_llm_planner.py -q
```

Expected:

- PASS.

---

## Task 10: Plugin/Skill Descriptor Runtime v1

**Files:**
- Create: `src/fireclaw_core/plugin_descriptor.py`
- Modify: `src/fireclaw_core/skill_manifest.py`
- Create: `tests/test_plugin_descriptor.py`
- Modify: `tests/test_skill_manifest.py`

- [x] **Step 1: Add descriptor validation tests**

Required descriptor fields:

```text
plugin_id
capabilities
preconditions
risk_level
required_sensors
adapter_bindings
approval_scope
provider_hooks
memory_hooks
```

- [x] **Step 2: Implement descriptor dataclasses**

Minimal classes:

```python
@dataclass(frozen=True)
class FireClawPluginDescriptor:
    plugin_id: str
    capabilities: tuple[str, ...]
    preconditions: tuple[str, ...]
    risk_level: str
    required_sensors: tuple[str, ...]
    adapter_bindings: tuple[str, ...]
    approval_scope: str | None = None
    provider_hooks: tuple[str, ...] = ()
    memory_hooks: tuple[str, ...] = ()
```

- [x] **Step 3: Add conversion from skill manifest metadata**

Add a helper:

```python
def descriptor_from_skill_manifest(skill: SkillManifest) -> FireClawPluginDescriptor:
    ...
```

It should map existing skill metadata without changing runtime behavior.

- [x] **Step 4: Run descriptor tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_plugin_descriptor.py tests/test_skill_manifest.py -q
```

Expected:

- PASS.

---

## Task 11: Deployment and Documentation Cleanup

**Files:**
- Modify: `docs/architecture/fireclaw-openclaw-gap-roadmap-2026-06-09.zh-CN.md`
- Modify: `docs/superpowers/plans/2026-06-09-phase8-realtime-event-stream.md`
- Modify: `docs/superpowers/plans/2026-06-09-phase9-ros1-integration-proof.md`
- Create/Modify: `docs/deployment/fireclaw-deployment-checklist.md`
- Modify: `README.md` if needed
- Modify: current date memory record

- [x] **Step 1: Update roadmap status**

Set status accurately:

- Phase 6: implemented unless new regressions found.
- Phase 7: implemented unless security review gaps remain.
- Phase 8: implemented after Tasks 4-7 pass.
- Phase 9: implemented for ROS1 local smoke proof after Tasks 1-3 pass; real robot hardware proof remains future work.
- Phase 10: implemented v1 after Tasks 8-10 pass; stronger embeddings/provider lifecycle remain future work.

- [x] **Step 2: Update deployment checklist**

Checklist must cover:

- API token configuration;
- operator scopes;
- network binding;
- ROS mode separation: dry-run/simulator/live;
- ROS smoke test command;
- log redaction;
- queue compaction/retention;
- memory index storage;
- emergency stop verification.

- [x] **Step 3: Update memory**

Record:

- files modified;
- commands run;
- observed warnings;
- known gaps;
- next recommended step.

---

## Task 12: Final Verification and Git Hygiene Review

**Files:**
- No production files unless tests reveal issues.

- [x] **Step 1: Run focused suites**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_ros1_transport.py \
  tests/test_ros1_smoke.py \
  tests/test_stream_events.py \
  tests/test_gateway.py \
  tests/test_mission_gateway.py \
  tests/test_incident_replay.py \
  tests/test_memory_index.py \
  tests/test_mission_memory.py \
  tests/test_mission_agent.py \
  tests/test_llm_planner.py \
  tests/test_plugin_descriptor.py \
  tests/test_skill_manifest.py \
  -q
```

Expected:

- PASS.

- [x] **Step 2: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected:

- PASS.

- [x] **Step 3: Review dirty worktree**

Run:

```bash
git status --short --branch
git diff --stat
```

Expected:

- All changed files are intentional.
- No generated secrets, private robot logs, or large artifacts are staged or ready for commit.

- [x] **Step 4: Prepare handoff summary**

Summarize:

- Phase 8 acceptance evidence;
- Phase 9 acceptance evidence;
- Phase 10 acceptance evidence;
- deployment cleanup evidence;
- remaining research-level gaps.

---

## Acceptance Criteria

This umbrella plan is complete only when all of the following are true:

- ROS1 transport can convert dict payloads into real ROS topic/action messages for the covered smoke proof path.
- ROS1 smoke tests prove topic, service, action result, feedback, cancel, and timeout behavior.
- Robot-local and mission-level lifecycle events use one `StreamEvent` schema across live stream and replay.
- Telemetry receives real lifecycle inputs, not only isolated unit-test events.
- Mission memory has a lightweight indexed retrieval path with structured filters.
- Operator corrections can enter planner context.
- A FireClaw plugin/skill descriptor v1 exists and can be derived from existing skill manifest metadata.
- Deployment docs and roadmap/memory records match the actual code state.
- Full test suite passes.

## Explicit Non-Goals

- No real robot hardware deployment in this plan; ROS1 proof may use local ROS master/turtlesim/actionlib tutorial stack.
- No full ROS2 implementation; keep ROS2 as protocol boundary unless a separate plan is approved.
- No vector database dependency; Phase 10 v1 uses stdlib SQLite FTS or falls back to JSONL.
- No operator web UI; this plan only makes backend events/replay/telemetry reliable enough for a future UI.
- No provider plugin marketplace; plugin descriptor v1 is a local contract, not a distribution system.
