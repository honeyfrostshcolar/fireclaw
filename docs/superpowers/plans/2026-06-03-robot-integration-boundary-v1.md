# Robot Integration Boundary v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a FireClaw-level robot action runtime so skill execution can emit auditable robot action lifecycle events without binding FireClaw core to ROS1.

**Architecture:** Introduce `RobotActionRuntime` and `RobotAdapterActionBackend` in a new focused module. Default in-process robot skills will call the runtime, which wraps existing `RobotAdapter` methods and emits `action.requested`, `action.started`, terminal action events, and cancellation events through the existing Gateway/EventLedger sink.

**Tech Stack:** Python 3.11 dataclasses/protocols, pytest, existing FireClaw `RobotAdapter`, `PlanExecutor`, `FireClawAgent`, and Gateway EventLedger.

---

### Task 1: Add Robot Action Runtime Core

**Files:**
- Create: `src/fireclaw_core/action_runtime.py`
- Test: `tests/test_action_runtime.py`

- [x] **Step 1: Write the failing runtime lifecycle test**

Create `tests/test_action_runtime.py` with:

```python
from fireclaw_core.action_runtime import RobotActionRuntime, RobotAdapterActionBackend
from fireclaw_core.robot import DryRunRobotAdapter


def test_robot_action_runtime_emits_lifecycle_events_for_adapter_action():
    events = []
    robot = DryRunRobotAdapter(robot_id="robot-1")
    runtime = RobotActionRuntime(
        backend=RobotAdapterActionBackend(robot),
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
        task_id="task-1",
    )

    result = runtime.run(
        skill_name="navigate_to_floor",
        action_type="navigate_to_floor",
        inputs={"floor": 2},
        dry_run=True,
        risk_level="low",
        timeout_seconds=None,
    )

    assert result.ok is True
    assert result.status == "succeeded"
    assert result.data["action_id"].startswith("action-")
    assert result.data["task_id"] == "task-1"
    assert [event_type for event_type, _payload in events] == [
        "action.requested",
        "action.started",
        "action.succeeded",
    ]
    assert events[0][1]["skill_name"] == "navigate_to_floor"
    assert events[0][1]["action_type"] == "navigate_to_floor"
    assert events[0][1]["inputs"] == {"floor": 2}
    assert events[0][1]["task_id"] == "task-1"
    assert events[2][1]["status"] == "succeeded"
```

- [x] **Step 2: Run the runtime test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_action_runtime.py::test_robot_action_runtime_emits_lifecycle_events_for_adapter_action -q
```

Expected: fail because `fireclaw_core.action_runtime` does not exist.

- [x] **Step 3: Implement minimal runtime and backend**

Create `src/fireclaw_core/action_runtime.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol
from uuid import uuid4

from fireclaw_core.robot import RobotActionResult, RobotAdapter


ActionEventSink = Callable[[str, dict[str, Any]], None]
CancellationCheck = Callable[[], bool]


class RobotActionBackend(Protocol):
    def execute(self, action_type: str, inputs: dict[str, Any]) -> RobotActionResult:
        ...


@dataclass
class RobotAdapterActionBackend:
    robot: RobotAdapter

    def execute(self, action_type: str, inputs: dict[str, Any]) -> RobotActionResult:
        if action_type == "navigate_to_floor":
            return self.robot.navigate_to_floor(int(inputs["floor"]))
        if action_type == "search_for_victims":
            return self.robot.search_for_victims(int(inputs["floor"]))
        if action_type == "assess_victim":
            return self.robot.assess_victim(int(inputs["floor"]))
        if action_type == "report_status":
            return self.robot.report_status(int(inputs["floor"]))
        if action_type == "return_to_safe_zone":
            return self.robot.return_to_safe_zone()
        timestamp = datetime.now(timezone.utc).isoformat()
        return RobotActionResult(
            ok=False,
            status="failed",
            robot_id=getattr(self.robot, "robot_id", "unknown"),
            mode=getattr(self.robot, "mode", "unknown"),
            action=action_type,
            dry_run=getattr(self.robot, "dry_run", True),
            data={},
            timestamp=timestamp,
            error=f"Unsupported robot action type: {action_type}",
        )


@dataclass
class RobotActionRuntime:
    backend: RobotActionBackend
    event_sink: ActionEventSink | None = None
    task_id: str | None = None

    def run(
        self,
        *,
        skill_name: str,
        action_type: str,
        inputs: dict[str, Any],
        dry_run: bool,
        risk_level: str,
        timeout_seconds: float | None,
        cancellation_requested: CancellationCheck | None = None,
    ) -> RobotActionResult:
        action_id = f"action-{uuid4().hex}"
        payload = {
            "action_id": action_id,
            "task_id": self.task_id,
            "skill_name": skill_name,
            "action_type": action_type,
            "inputs": dict(inputs),
            "dry_run": dry_run,
            "risk_level": risk_level,
            "timeout_seconds": timeout_seconds,
        }
        self._emit("action.requested", {**payload, "status": "requested"})
        if cancellation_requested is not None and cancellation_requested():
            return self._cancelled_result(action_id, action_type, payload)
        self._emit("action.started", {**payload, "status": "started"})
        result = self.backend.execute(action_type, inputs)
        result.data.setdefault("action_id", action_id)
        result.data.setdefault("task_id", self.task_id)
        terminal_type = "action.succeeded" if result.ok else "action.failed"
        self._emit(
            terminal_type,
            {
                **payload,
                "status": result.status,
                "output": {
                    "robot_id": result.robot_id,
                    "mode": result.mode,
                    "action": result.action,
                    "dry_run": result.dry_run,
                    **result.data,
                },
                "error": result.error,
            },
        )
        return result

    def _cancelled_result(
        self,
        action_id: str,
        action_type: str,
        payload: dict[str, Any],
    ) -> RobotActionResult:
        timestamp = datetime.now(timezone.utc).isoformat()
        self._emit("action.cancel_requested", {**payload, "status": "cancel_requested"})
        self._emit("action.cancelled", {**payload, "status": "cancelled"})
        return RobotActionResult(
            ok=False,
            status="cancelled",
            robot_id="unknown",
            mode="action_runtime",
            action=action_type,
            dry_run=bool(payload["dry_run"]),
            data={"action_id": action_id, "task_id": payload["task_id"]},
            timestamp=timestamp,
            error="Robot action cancelled before backend execution.",
        )

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(event_type, payload)
        except Exception:
            return
```

- [x] **Step 4: Run runtime tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_action_runtime.py -q
```

Expected: runtime lifecycle test passes.

### Task 2: Wire Default Robot Skills Through Action Runtime

**Files:**
- Modify: `src/fireclaw_core/skills.py`
- Modify: `src/fireclaw_core/agent.py`
- Test: `tests/test_agent.py`

- [x] **Step 1: Write the failing agent event test**

Add a test to `tests/test_agent.py`:

```python
def test_agent_emits_robot_action_events_for_default_skills(tmp_path):
    events = []
    agent = FireClawAgent(
        robot=DryRunRobotAdapter(robot_id="robot-1"),
        memory=JsonlMemoryStore(tmp_path / "memory.jsonl"),
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
        task_id="task-1",
    )

    result = agent.run("去二楼救人")

    assert result["status"] == "succeeded"
    event_types = [event_type for event_type, _payload in events]
    assert "action.requested" in event_types
    assert "action.started" in event_types
    assert "action.succeeded" in event_types
    requested = [payload for event_type, payload in events if event_type == "action.requested"]
    assert requested[0]["task_id"] == "task-1"
    assert requested[0]["skill_name"] == "navigate_to_floor"
    assert requested[0]["action_type"] == "navigate_to_floor"
```

- [x] **Step 2: Run the agent test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py::test_agent_emits_robot_action_events_for_default_skills -q
```

Expected: fail because `FireClawAgent` does not accept `task_id` and default skills do not emit action events.

- [x] **Step 3: Add optional runtime injection to default skill registry**

Modify `src/fireclaw_core/skills.py` so `create_default_skill_registry(robot, action_runtime=None)` wraps default skills through `RobotActionRuntime` when provided, while preserving existing direct adapter calls when omitted.

- [x] **Step 4: Add task_id and action runtime creation to agent**

Modify `src/fireclaw_core/agent.py`:

- add optional `task_id: str | None = None` to `FireClawAgent.__init__`;
- create `RobotActionRuntime(RobotAdapterActionBackend(self.robot), event_sink=event_sink, task_id=task_id)`;
- pass that runtime to `create_default_skill_registry(...)`.

- [x] **Step 5: Run agent tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -q
```

Expected: all agent tests pass.

### Task 3: Surface Action Events Through Gateway

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Modify: `tests/test_gateway.py`
- Modify: `README.md`
- Modify: `memory/2026-06-03/fireclaw-dry-run-core.md`

- [x] **Step 1: Write the failing Gateway trace test assertion**

Update `tests/test_gateway.py::test_gateway_runs_task_and_returns_recent_memory` to assert task events include action lifecycle events after the first `skill.started`:

```python
assert "action.requested" in [event["type"] for event in events["events"]]
assert "action.started" in [event["type"] for event in events["events"]]
assert "action.succeeded" in [event["type"] for event in events["events"]]
first_action = next(event for event in events["events"] if event["type"] == "action.requested")
assert first_action["payload"]["task_id"] == accepted["task_id"]
assert first_action["payload"]["skill_name"] == "navigate_to_floor"
```

- [x] **Step 2: Run the Gateway test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway.py::test_gateway_runs_task_and_returns_recent_memory -q
```

Expected: fail because Gateway-created agents do not pass `task_id` into `FireClawAgent`.

- [x] **Step 3: Pass task_id into Gateway-created agents**

Modify `src/fireclaw_core/gateway.py` `_create_agent(...)` to pass `task_id=task_id` into `FireClawAgent`.

- [x] **Step 4: Run focused Gateway tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway.py -q
```

Expected: all Gateway tests pass.

- [x] **Step 5: Update docs and memory**

Update `README.md` Gateway event list to include action lifecycle events. Update `memory/2026-06-03/fireclaw-dry-run-core.md` with files changed, commands run, and remaining gaps.

- [x] **Step 6: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full suite passes.
