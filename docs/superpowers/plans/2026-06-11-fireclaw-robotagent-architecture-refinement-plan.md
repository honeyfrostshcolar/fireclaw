# FireClaw RobotAgent Architecture Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refine FireClaw around persistent robot-local RobotAgents by adding a structured robot task protocol, structured execution path, unknown-state safety semantics, and proof-gate updates without breaking the existing command-based path.

**Architecture:** Keep existing `MissionAgent`, `FireClawGateway`, `FireClawAgent`, and `RobotSubagentClient` names for compatibility. Add `task_contract.py` as the typed protocol boundary, thread `structured_task` through the MissionCoordinator-to-RobotAgent HTTP path, and let robot-local safety/execution consume structured tasks directly. Update ROS1/simulator validation after the compatibility path is green.

**Tech Stack:** Python 3.11+, dataclasses, stdlib `http.server`, existing FireClaw JSON stores, pytest.

**Spec:** `docs/superpowers/specs/2026-06-11-fireclaw-robotagent-architecture-refinement-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/fireclaw_core/task_contract.py` | Create | Structured robot task dataclasses, validation, conversion to `PlanningResult`. |
| `tests/test_task_contract.py` | Create | Contract validation, mission-subtask conversion, planning conversion tests. |
| `src/fireclaw_core/subagent_client.py` | Modify | Include optional `structured_task` in `POST /tasks` payload. |
| `tests/test_subagent_client.py` | Modify | Verify structured task payload is sent and registry behavior remains unchanged. |
| `src/fireclaw_core/gateway.py` | Modify | Accept `structured_task` in `/tasks`, persist it in task result/event payloads, call structured agent path. |
| `tests/test_gateway_structured_task.py` | Create | Robot Gateway structured-task HTTP behavior. |
| `src/fireclaw_core/agent.py` | Modify | Add `run_structured_task()` and shared execution helper. |
| `tests/test_robot_agent_structured_task.py` | Create | Structured task execution without natural-language planner. |
| `src/fireclaw_core/mission_agent.py` | Modify | Generate and submit structured tasks for mission subtasks. |
| `tests/test_mission_agent_structured_task.py` | Create | MissionAgent sends structured task while preserving `command`. |
| `src/fireclaw_core/mission_plan_validator.py` | Create | Deterministic mission plan validation before execution. |
| `tests/test_mission_plan_validator.py` | Create | Registry/capability/floor/execution group validation. |
| `src/fireclaw_core/robot.py` | Modify | Allow unknown robot/environment state fields. |
| `src/fireclaw_core/safety.py` | Modify | Add warnings and unknown-state handling. |
| `tests/test_safety_unknown_state.py` | Create | Unknown vs explicit-empty safety behavior. |
| `src/fireclaw_core/embodied_eval.py` | Modify | Record structured task metadata in simulator proof artifacts. |
| `tests/test_embodied_eval.py` | Modify | Verify structured task proof metadata. |
| `docs/architecture/fireclaw-openclaw-alignment.md` | Modify | Clarify RobotAgent/MissionCoordinator terminology. |
| `README.md` | Modify | State the refined architecture and structured-task path. |

---

### Task 1: Add Structured Robot Task Contract

**Files:**
- Create: `src/fireclaw_core/task_contract.py`
- Create: `tests/test_task_contract.py`

- [ ] **Step 1: Write failing contract tests**

```python
# tests/test_task_contract.py
from __future__ import annotations

from fireclaw_core.mission_planner import MissionSubtask
from fireclaw_core.task_contract import (
    StructuredRobotTask,
    planning_result_from_structured_task,
    structured_task_from_mission_subtask,
    validate_structured_robot_task,
)


def test_structured_task_from_mission_subtask_maps_floor_and_skill():
    subtask = MissionSubtask(
        robot_id="robot-1",
        command="去2楼搜索受困人员",
        floor=2,
        capability_required="search_for_victims",
        execution_group=0,
    )

    task = structured_task_from_mission_subtask(
        mission_id="mission-1",
        subtask=subtask,
        operator_id="operator-1",
        task_id="task-1",
    )

    assert task.task_id == "task-1"
    assert task.mission_id == "mission-1"
    assert task.robot_id == "robot-1"
    assert task.task_type == "search"
    assert task.target == {"floor": 2}
    assert task.required_skills == ["navigate_to_floor", "search_for_victims", "report_status"]
    assert task.command == "去2楼搜索受困人员"


def test_validate_structured_robot_task_rejects_missing_required_skills():
    task = StructuredRobotTask(
        task_id="task-1",
        task_type="search",
        target={"floor": 2},
        required_skills=[],
    )

    assert "required_skills must not be empty" in validate_structured_robot_task(task)


def test_planning_result_from_structured_task_uses_required_skill_order():
    task = StructuredRobotTask(
        task_id="task-1",
        task_type="rescue_search",
        target={"floor": 2},
        required_skills=["navigate_to_floor", "search_for_victims", "report_status"],
    )

    result = planning_result_from_structured_task(task)

    assert result.status == "planned"
    assert result.intent == "rescue_search"
    assert result.target_floor == 2
    assert [step.skill_name for step in result.plan.steps] == [
        "navigate_to_floor",
        "search_for_victims",
        "report_status",
    ]
    assert result.plan.steps[0].inputs == {"floor": 2}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_contract.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'fireclaw_core.task_contract'`.

- [ ] **Step 3: Implement the contract module**

```python
# src/fireclaw_core/task_contract.py
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from fireclaw_core.mission_planner import MissionSubtask
from fireclaw_core.planner import Plan, PlanningResult, PlanStep

VALID_PRIORITIES = {"low", "normal", "high", "emergency"}
VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}
FLOOR_SKILLS = {"navigate_to_floor", "search_for_victims", "assess_victim", "report_status"}


@dataclass(frozen=True)
class StructuredRobotTask:
    task_id: str
    task_type: str
    target: dict[str, Any]
    required_skills: list[str]
    constraints: dict[str, Any] = field(default_factory=dict)
    priority: str = "normal"
    risk_level: str = "low"
    operator_id: str | None = None
    mission_id: str | None = None
    robot_id: str | None = None
    command: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StructuredRobotTask":
        return cls(
            task_id=str(payload.get("task_id") or ""),
            task_type=str(payload.get("task_type") or ""),
            target=dict(payload.get("target") or {}),
            required_skills=[str(item) for item in payload.get("required_skills") or []],
            constraints=dict(payload.get("constraints") or {}),
            priority=str(payload.get("priority") or "normal"),
            risk_level=str(payload.get("risk_level") or "low"),
            operator_id=_optional_str(payload.get("operator_id")),
            mission_id=_optional_str(payload.get("mission_id")),
            robot_id=_optional_str(payload.get("robot_id")),
            command=_optional_str(payload.get("command")),
        )


def structured_task_from_mission_subtask(
    *,
    mission_id: str,
    subtask: MissionSubtask,
    operator_id: str | None = None,
    task_id: str | None = None,
) -> StructuredRobotTask:
    task_type = _task_type_from_capability(subtask.capability_required)
    required_skills = _skills_from_capability(subtask.capability_required)
    return StructuredRobotTask(
        task_id=task_id or f"{mission_id}:{subtask.robot_id}:{subtask.floor}:{subtask.execution_group}",
        task_type=task_type,
        target={"floor": subtask.floor},
        required_skills=required_skills,
        constraints={"execution_group": subtask.execution_group},
        priority="normal",
        risk_level="low",
        operator_id=operator_id,
        mission_id=mission_id,
        robot_id=subtask.robot_id,
        command=subtask.command,
    )


def validate_structured_robot_task(task: StructuredRobotTask) -> list[str]:
    errors: list[str] = []
    if not task.task_id:
        errors.append("task_id must not be empty")
    if not task.task_type:
        errors.append("task_type must not be empty")
    if not task.required_skills:
        errors.append("required_skills must not be empty")
    if task.priority not in VALID_PRIORITIES:
        errors.append(f"priority must be one of {sorted(VALID_PRIORITIES)}")
    if task.risk_level not in VALID_RISK_LEVELS:
        errors.append(f"risk_level must be one of {sorted(VALID_RISK_LEVELS)}")
    floor = task.target.get("floor")
    if floor is not None and (not isinstance(floor, int) or floor <= 0):
        errors.append("target.floor must be a positive integer when provided")
    return errors


def planning_result_from_structured_task(task: StructuredRobotTask) -> PlanningResult:
    errors = validate_structured_robot_task(task)
    if errors:
        return PlanningResult(status="clarify", message="; ".join(errors), intent=task.task_type)

    floor = task.target.get("floor")
    target_floor = floor if isinstance(floor, int) else None
    steps: list[PlanStep] = []
    for skill_name in task.required_skills:
        inputs: dict[str, Any] = {}
        if skill_name in FLOOR_SKILLS and target_floor is not None:
            inputs["floor"] = target_floor
        if task.constraints:
            inputs["constraints"] = dict(task.constraints)
        steps.append(PlanStep(skill_name=skill_name, inputs=inputs))
    return PlanningResult(
        status="planned",
        message="Structured robot task converted to executable plan.",
        intent=task.task_type,
        target_floor=target_floor,
        plan=Plan(intent=task.task_type, steps=steps),
    )


def _task_type_from_capability(capability: str) -> str:
    if capability == "search_for_victims":
        return "search"
    return capability


def _skills_from_capability(capability: str) -> list[str]:
    if capability == "search_for_victims":
        return ["navigate_to_floor", "search_for_victims", "report_status"]
    return [capability]


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_contract.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/task_contract.py tests/test_task_contract.py
git commit -m "feat: add structured robot task contract"
```

---

### Task 2: Thread Structured Tasks Through RobotSubagentClient

**Files:**
- Modify: `src/fireclaw_core/subagent_client.py`
- Modify: `tests/test_subagent_client.py`

- [ ] **Step 1: Write failing client payload test**

Append this test to `tests/test_subagent_client.py`:

```python
def test_robot_subagent_client_sends_structured_task_payload(tmp_path):
    from fireclaw_core.gateway import FireClawGateway, GatewayConfig
    from fireclaw_core.subagent_client import RobotSubagentClient
    from fireclaw_core.robot_registry import RobotRegistryEntry
    from fireclaw_core.task_contract import StructuredRobotTask

    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        entry = RobotRegistryEntry(
            robot_id="robot-1",
            base_url=gateway.base_url,
            capabilities=["search_for_victims"],
        )
        client = RobotSubagentClient()
        task = StructuredRobotTask(
            task_id="structured-1",
            task_type="search",
            target={"floor": 2},
            required_skills=["navigate_to_floor", "search_for_victims", "report_status"],
        )

        result = client.submit_task(entry, command="去2楼搜索受困人员", structured_task=task.to_dict())

        assert result["status"] in {"accepted", "completed", "require_confirmation"}
        trace = client.get_task_trace(entry, result["task_id"])
        assert trace["structured_task"]["task_id"] == "structured-1"
    finally:
        gateway.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_subagent_client.py::test_robot_subagent_client_sends_structured_task_payload -q
```

Expected: fail with `TypeError: RobotSubagentClient.submit_task() got an unexpected keyword argument 'structured_task'`.

- [ ] **Step 3: Add optional structured_task argument**

Modify `RobotSubagentClient.submit_task()` signature and payload construction:

```python
    def submit_task(
        self,
        entry: RobotRegistryEntry,
        *,
        command: str,
        session_id: str | None = None,
        dedupe_key: str | None = None,
        operator: dict[str, Any] | None = None,
        mission: dict[str, Any] | None = None,
        structured_task: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"command": command}
        if structured_task is not None:
            payload["structured_task"] = structured_task
```

Keep the existing session, dedupe, operator, mission, and registry code unchanged.

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_subagent_client.py::test_robot_subagent_client_sends_structured_task_payload tests/test_subagent_client.py::test_robot_subagent_client_submits_task_and_reads_trace -q
```

Expected: both pass after Task 3 adds Gateway support. If this task is executed before Task 3, keep the failing assertion as the RED state and continue to Task 3.

- [ ] **Step 5: Commit after Task 3 passes**

```bash
git add src/fireclaw_core/subagent_client.py tests/test_subagent_client.py
git commit -m "feat: send structured robot tasks to robot gateways"
```

---

### Task 3: Add FireClawAgent Structured Execution

**Files:**
- Modify: `src/fireclaw_core/agent.py`
- Create: `tests/test_robot_agent_structured_task.py`

- [ ] **Step 1: Write failing RobotAgent tests**

```python
# tests/test_robot_agent_structured_task.py
from __future__ import annotations

from fireclaw_core.agent import FireClawAgent
from fireclaw_core.robot import DryRunRobotAdapter
from fireclaw_core.task_contract import StructuredRobotTask


def test_robot_agent_runs_structured_task_without_natural_language_planner():
    robot = DryRunRobotAdapter(robot_id="robot-1")
    agent = FireClawAgent(robot=robot, workspace_skills_dir=None)
    task = StructuredRobotTask(
        task_id="task-structured",
        task_type="search",
        target={"floor": 2},
        required_skills=["navigate_to_floor", "search_for_victims", "report_status"],
        mission_id="mission-1",
        robot_id="robot-1",
        command="human readable only",
    )

    result = agent.run_structured_task(task)

    assert result["status"] == "completed"
    assert result["structured_task"]["task_id"] == "task-structured"
    assert [action["action"] for action in robot.actions] == [
        "navigate_to_floor",
        "search_for_victims",
        "report_status",
    ]


def test_robot_agent_rejects_invalid_structured_task():
    agent = FireClawAgent(workspace_skills_dir=None)
    task = StructuredRobotTask(
        task_id="task-invalid",
        task_type="search",
        target={"floor": 2},
        required_skills=[],
    )

    result = agent.run_structured_task(task)

    assert result["status"] in {"clarify", "blocked"}
    assert "required_skills must not be empty" in result["message"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_structured_task.py -q
```

Expected: fail with `AttributeError: 'FireClawAgent' object has no attribute 'run_structured_task'`.

- [ ] **Step 3: Add `run_structured_task()` using existing execution path**

In `src/fireclaw_core/agent.py`, import:

```python
from fireclaw_core.task_contract import StructuredRobotTask, planning_result_from_structured_task
```

Add method to `FireClawAgent`:

```python
    def run_structured_task(self, task: StructuredRobotTask) -> dict[str, Any]:
        planning_result = planning_result_from_structured_task(task)
        robot_state_object = self._get_robot_state()
        environment_state_object = self._get_environment_state()
        robot_state = self._state_snapshot(robot_state_object)
        environment_state = self._state_snapshot(environment_state_object)
        safety_decision = self.safety.evaluate(
            planning_result,
            self.registry,
            dry_run=self.dry_run,
            available_sensors=self.available_sensors,
            robot_state=robot_state_object,
            environment_state=environment_state_object,
        )
        self._emit_event("task.structured_received", task.to_dict())
        self._emit_event("task.planned", self._planning_to_dict(planning_result))
        self._emit_event("safety.decided", asdict(safety_decision))

        execution_result: ExecutionResult | None = None
        if safety_decision.status == "allow" and planning_result.plan is not None:
            execution_result = self.executor.execute(planning_result.plan)

        status = self._resolve_status(safety_decision, execution_result)
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": task.command or task.task_type,
            "structured_task": task.to_dict(),
            "status": status,
            "message": self._resolve_message(planning_result, safety_decision, execution_result),
            "dry_run": self.dry_run,
            "planning": self._planning_to_dict(planning_result),
            "safety": asdict(safety_decision),
            "execution": self._execution_to_dict(execution_result),
            "confirmation": self._confirmation_to_dict(safety_decision),
            "robot_state": robot_state,
            "environment_state": environment_state,
            "memory_error": None,
        }
        self._append_memory_result(result)
        return result
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_structured_task.py tests/test_agent.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/agent.py tests/test_robot_agent_structured_task.py
git commit -m "feat: add robot agent structured task execution"
```

---

### Task 4: Accept Structured Tasks in FireClawGateway

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Create: `tests/test_gateway_structured_task.py`

- [ ] **Step 1: Write failing Gateway HTTP tests**

```python
# tests/test_gateway_structured_task.py
from __future__ import annotations

import json
from urllib import request

from fireclaw_core.gateway import FireClawGateway, GatewayConfig


def _json_request(base_url: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "X-Operator-Scopes": "admin"},
    )
    with request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_gateway_accepts_structured_task_payload(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        accepted = _json_request(
            gateway.base_url,
            "POST",
            "/tasks",
            {
                "command": "去2楼搜索受困人员",
                "structured_task": {
                    "task_id": "structured-1",
                    "task_type": "search",
                    "target": {"floor": 2},
                    "required_skills": ["navigate_to_floor", "search_for_victims", "report_status"],
                },
            },
        )
        trace = _json_request(gateway.base_url, "GET", f"/tasks/{accepted['task_id']}")

        assert trace["structured_task"]["task_id"] == "structured-1"
        assert trace["result"]["structured_task"]["task_type"] == "search"
    finally:
        gateway.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_structured_task.py -q
```

Expected: fail because trace does not include `structured_task`.

- [ ] **Step 3: Add structured_task to `TaskControl` and submit path**

In `src/fireclaw_core/gateway.py`, update `TaskControl`:

```python
@dataclass
class TaskControl:
    task_id: str
    session_id: str
    command: str
    started_at: str
    structured_task: dict[str, Any] | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
```

Update `submit_agent()` signature:

```python
    def submit_agent(
        self,
        command: str,
        session_id: str | None = None,
        operator: OperatorContext | None = None,
        dedupe_key: str | None = None,
        structured_task: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
```

Pass `structured_task` into `TaskControl`, `task_queue.create(... result metadata if available)`, and worker `_execute_agent_task(...)`.

- [ ] **Step 4: Route structured execution inside `_execute_agent_task()`**

Add this import:

```python
from fireclaw_core.task_contract import StructuredRobotTask
```

Where `FireClawAgent` is constructed and called, use:

```python
if structured_task is not None:
    task_object = StructuredRobotTask.from_dict(structured_task)
    result = agent.run_structured_task(task_object)
else:
    result = agent.run(command)
```

Ensure the task trace response includes:

```python
"structured_task": structured_task
```

- [ ] **Step 5: Run Gateway focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_structured_task.py tests/test_gateway.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/gateway.py tests/test_gateway_structured_task.py
git commit -m "feat: accept structured tasks in robot gateway"
```

---

### Task 5: Generate Structured Tasks in MissionAgent

**Files:**
- Modify: `src/fireclaw_core/mission_agent.py`
- Create: `tests/test_mission_agent_structured_task.py`

- [ ] **Step 1: Write failing MissionAgent test**

```python
# tests/test_mission_agent_structured_task.py
from __future__ import annotations

from fireclaw_core.mission_agent import MissionAgent
from fireclaw_core.robot_registry import RobotRegistry, RobotRegistryEntry


class RecordingClient:
    def __init__(self):
        self.calls = []

    def submit_task(self, entry, **kwargs):
        self.calls.append((entry, kwargs))
        return {"status": "accepted", "task_id": "robot-task-1"}

    def get_task_trace(self, entry, task_id):
        return {"status": "completed", "task_id": task_id, "result": {"status": "completed"}}

    def cancel_task(self, entry, task_id, *, operator=None):
        return {"status": "cancelled", "task_id": task_id}

    def get_events(self, entry, task_id=None, limit=100):
        return []

    def check_presence(self, entry):
        return {"online": True}


def test_mission_agent_submit_subtask_sends_structured_task():
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-1",
            base_url="http://robot-1",
            capabilities=["search_for_victims"],
        )
    ])
    client = RecordingClient()
    agent = MissionAgent(registry=registry, subagent_client=client)

    result = agent.submit_subtask(
        "robot-1",
        "去2楼搜索受困人员",
        session_id="session-1",
        operator={"operator_id": "operator-1"},
    )

    assert result["status"] == "accepted"
    structured_task = client.calls[0][1]["structured_task"]
    assert structured_task["mission_id"] == "session-1"
    assert structured_task["robot_id"] == "robot-1"
    assert structured_task["target"] == {"floor": 2}
    assert structured_task["required_skills"] == ["navigate_to_floor", "search_for_victims", "report_status"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent_structured_task.py -q
```

Expected: fail because `structured_task` is not passed.

- [ ] **Step 3: Generate task from command in submit_subtask**

In `src/fireclaw_core/mission_agent.py`, import:

```python
from fireclaw_core.mission_planner import MissionSubtask
from fireclaw_core.planner import RuleBasedPlanner
from fireclaw_core.task_contract import structured_task_from_mission_subtask
```

Before `self.subagent_client.submit_task(...)`, create a temporary mission subtask:

```python
        floor = RuleBasedPlanner()._extract_floor(command)
        structured_task = None
        if floor is not None:
            mission_subtask = MissionSubtask(
                robot_id=robot_id,
                command=command,
                floor=floor,
                capability_required=_capability_from_entry(entry),
                execution_group=0,
            )
            structured_task = structured_task_from_mission_subtask(
                mission_id=mission_id,
                subtask=mission_subtask,
                operator_id=(operator or {}).get("operator_id") if isinstance(operator, dict) else None,
            ).to_dict()
```

Add helper near the bottom of the file:

```python
def _capability_from_entry(entry: RobotRegistryEntry) -> str:
    if "search_for_victims" in entry.capabilities:
        return "search_for_victims"
    return entry.capabilities[0] if entry.capabilities else "unknown"
```

Pass `structured_task=structured_task` into `submit_task()`.

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent_structured_task.py tests/test_mission_agent.py tests/test_mission_scheduler.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/mission_agent.py tests/test_mission_agent_structured_task.py
git commit -m "feat: submit structured tasks from mission agent"
```

---

### Task 6: Add Mission Plan Validator

**Files:**
- Create: `src/fireclaw_core/mission_plan_validator.py`
- Create: `tests/test_mission_plan_validator.py`

- [ ] **Step 1: Write failing validator tests**

```python
# tests/test_mission_plan_validator.py
from __future__ import annotations

from fireclaw_core.mission_plan_validator import MissionPlanValidator
from fireclaw_core.mission_planner import MissionPlan, MissionSubtask
from fireclaw_core.robot_registry import RobotRegistry, RobotRegistryEntry


def test_validator_allows_valid_plan():
    registry = RobotRegistry([
        RobotRegistryEntry("robot-1", "http://robot-1", capabilities=["search_for_victims"])
    ])
    plan = MissionPlan(
        intent="search",
        command="去2楼搜索",
        subtasks=[MissionSubtask("robot-1", "去2楼搜索", 2, "search_for_victims")],
    )

    assert MissionPlanValidator().validate(plan, registry) == []


def test_validator_rejects_missing_robot_and_capability_mismatch():
    registry = RobotRegistry([
        RobotRegistryEntry("robot-1", "http://robot-1", capabilities=["patrol"])
    ])
    plan = MissionPlan(
        intent="search",
        command="去2楼搜索",
        subtasks=[
            MissionSubtask("robot-1", "去2楼搜索", 2, "search_for_victims"),
            MissionSubtask("missing", "去3楼搜索", 3, "search_for_victims"),
        ],
    )

    errors = MissionPlanValidator().validate(plan, registry)

    assert "Robot robot-1 lacks required capability search_for_victims." in errors
    assert "Robot missing is not registered." in errors
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_plan_validator.py -q
```

Expected: fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement validator**

```python
# src/fireclaw_core/mission_plan_validator.py
from __future__ import annotations

from fireclaw_core.mission_planner import MissionPlan
from fireclaw_core.robot_registry import RobotRegistry
from fireclaw_core.task_contract import structured_task_from_mission_subtask, validate_structured_robot_task


class MissionPlanValidator:
    def validate(self, plan: MissionPlan, registry: RobotRegistry) -> list[str]:
        errors: list[str] = []
        if not plan.subtasks:
            errors.append("Mission plan must contain at least one subtask.")
        for subtask in plan.subtasks:
            entry = registry.get(subtask.robot_id)
            if entry is None:
                errors.append(f"Robot {subtask.robot_id} is not registered.")
                continue
            if not entry.enabled:
                errors.append(f"Robot {subtask.robot_id} is disabled.")
            if subtask.capability_required not in entry.capabilities:
                errors.append(
                    f"Robot {subtask.robot_id} lacks required capability {subtask.capability_required}."
                )
            if subtask.floor <= 0:
                errors.append(f"Subtask floor must be positive for robot {subtask.robot_id}.")
            if subtask.execution_group < 0:
                errors.append(f"Execution group must be non-negative for robot {subtask.robot_id}.")
            structured = structured_task_from_mission_subtask(
                mission_id="validation",
                subtask=subtask,
            )
            errors.extend(validate_structured_robot_task(structured))
        return errors
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_plan_validator.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Wire validator into `MissionAgent.plan_mission()`**

Find the method that plans missions in `src/fireclaw_core/mission_agent.py`, instantiate `MissionPlanValidator`, and if errors exist return a non-executable result:

```python
validation_errors = MissionPlanValidator().validate(planning_result.plan, self.registry)
if validation_errors:
    return {
        "status": "blocked",
        "message": "Mission plan failed deterministic validation.",
        "errors": validation_errors,
    }
```

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/mission_plan_validator.py tests/test_mission_plan_validator.py src/fireclaw_core/mission_agent.py
git commit -m "feat: validate mission plans before robot dispatch"
```

---

### Task 7: Add Unknown-State Safety Semantics

**Files:**
- Modify: `src/fireclaw_core/robot.py`
- Modify: `src/fireclaw_core/safety.py`
- Create: `tests/test_safety_unknown_state.py`

- [ ] **Step 1: Write failing unknown-state tests**

```python
# tests/test_safety_unknown_state.py
from __future__ import annotations

from fireclaw_core.planner import Plan, PlanningResult, PlanStep
from fireclaw_core.robot import EnvironmentState, RobotState
from fireclaw_core.safety import SafetyGate
from fireclaw_core.skills import Skill, SkillRegistry


def _registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(Skill(name="navigate_to_floor", description="nav", handler=lambda inputs: None))
    return registry


def _planning() -> PlanningResult:
    return PlanningResult(
        status="planned",
        message="planned",
        intent="search",
        target_floor=2,
        plan=Plan("search", [PlanStep("navigate_to_floor", {"floor": 2})]),
    )


def test_unknown_reachable_floors_warns_in_dry_run():
    decision = SafetyGate().evaluate(
        _planning(),
        _registry(),
        dry_run=True,
        robot_state=RobotState("robot-1", "dry_run", True, True, 100.0, 1, [], False),
        environment_state=EnvironmentState(reachable_floors=None),
    )

    assert decision.status == "allow"
    assert "Reachable floors are unknown." in decision.warnings


def test_explicit_empty_reachable_floors_blocks():
    decision = SafetyGate().evaluate(
        _planning(),
        _registry(),
        dry_run=True,
        robot_state=RobotState("robot-1", "dry_run", True, True, 100.0, 1, [], False),
        environment_state=EnvironmentState(reachable_floors=[]),
    )

    assert decision.status == "block"
    assert "Target floor is not reachable: 2" in decision.reasons


def test_unknown_battery_requires_confirmation_for_real_robot():
    decision = SafetyGate().evaluate(
        _planning(),
        _registry(),
        dry_run=False,
        robot_state=RobotState("robot-1", "ros1", False, True, None, 1, [], True),
        environment_state=EnvironmentState(reachable_floors=[2]),
    )

    assert decision.status == "require_confirmation"
    assert "Robot robot-1 battery state is unknown." in decision.reasons
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_safety_unknown_state.py -q
```

Expected: fail because `EnvironmentState.reachable_floors` does not accept `None` semantics and `SafetyDecision` has no `warnings`.

- [ ] **Step 3: Update state dataclasses**

Modify `src/fireclaw_core/robot.py`:

```python
@dataclass
class RobotState:
    robot_id: str
    mode: str
    dry_run: bool
    online: bool
    battery_percent: float | None
    current_floor: int | None
    available_sensors: list[str] | None
    supports_real_execution: bool


@dataclass
class EnvironmentState:
    reachable_floors: list[int] | None
    hazards: list[str] | None = field(default_factory=list)
    victims_by_floor: dict[int, int] | None = field(default_factory=dict)
```

- [ ] **Step 4: Update SafetyDecision and checks**

Modify `src/fireclaw_core/safety.py`:

```python
@dataclass(frozen=True)
class SafetyDecision:
    status: str
    reasons: list[str]
    warnings: list[str] = field(default_factory=list)
```

Add warning collection in `evaluate()`:

```python
state_blocks, state_warnings, confirmation_reasons = self._evaluate_state(
    planning_result,
    robot_state,
    environment_state,
    dry_run=dry_run,
)
if state_blocks:
    return SafetyDecision(status="block", reasons=state_blocks, warnings=state_warnings)
```

Use this state helper:

```python
    def _evaluate_state(
        self,
        planning_result: PlanningResult,
        robot_state: RobotState | None,
        environment_state: EnvironmentState | None,
        *,
        dry_run: bool,
    ) -> tuple[list[str], list[str], list[str]]:
        blocks: list[str] = []
        warnings: list[str] = []
        confirmations: list[str] = []
        if robot_state is not None:
            if not robot_state.online:
                blocks.append(f"Robot {robot_state.robot_id} is offline.")
            if robot_state.battery_percent is None:
                message = f"Robot {robot_state.robot_id} battery state is unknown."
                if dry_run:
                    warnings.append(message)
                else:
                    confirmations.append(message)
            elif robot_state.battery_percent < 10.0:
                blocks.append(
                    f"Robot {robot_state.robot_id} battery is too low: {robot_state.battery_percent}%."
                )
        if environment_state is not None and planning_result.target_floor is not None:
            if environment_state.reachable_floors is None:
                message = "Reachable floors are unknown."
                if dry_run:
                    warnings.append(message)
                else:
                    confirmations.append(message)
            elif planning_result.target_floor not in environment_state.reachable_floors:
                blocks.append(f"Target floor is not reachable: {planning_result.target_floor}")
        return blocks, warnings, confirmations
```

Merge `confirmation_reasons` with existing risk/real-robot confirmation reasons and preserve warnings in every returned `SafetyDecision`.

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_safety_unknown_state.py tests/test_safety_gate.py tests/test_ros1_adapter_state.py tests/test_ros1_transport.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/robot.py src/fireclaw_core/safety.py tests/test_safety_unknown_state.py
git commit -m "fix: distinguish unknown robot state in safety gate"
```

---

### Task 8: Update Evaluation, Docs, and Baseline Verification

**Files:**
- Modify: `src/fireclaw_core/embodied_eval.py`
- Modify: `tests/test_embodied_eval.py`
- Modify: `README.md`
- Modify: `docs/architecture/fireclaw-openclaw-alignment.md`
- Modify: `memory/2026-06-11/fireclaw-embodied-roadmap-review.md`

- [ ] **Step 1: Add proof metadata test**

In `tests/test_embodied_eval.py`, add an assertion to the simulator scenario test that the output scenario record includes structured task metadata:

```python
def test_embodied_eval_records_structured_task_metadata(tmp_path):
    output_dir = tmp_path / "eval"
    result = run_embodied_eval(
        scenarios_path=Path("tests/fixtures/embodied_eval/rescue_scenarios.json"),
        output_dir=output_dir,
        adapter="simulator",
    )

    assert result["status"] == "pass"
    scenario_lines = (output_dir / "scenarios.jsonl").read_text(encoding="utf-8").splitlines()
    assert scenario_lines
    first = json.loads(scenario_lines[0])
    assert "structured_task" in first
    assert first["structured_task"]["required_skills"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_embodied_eval.py::test_embodied_eval_records_structured_task_metadata -q
```

Expected: fail because scenario output does not include `structured_task`.

- [ ] **Step 3: Record structured task metadata in embodied eval**

In `src/fireclaw_core/embodied_eval.py`, after mission submission/trace collection, extract the first robot subtask structured payload:

```python
structured_task = None
for subtask in trace.get("subtasks", []):
    if isinstance(subtask, dict) and isinstance(subtask.get("structured_task"), dict):
        structured_task = subtask["structured_task"]
        break
```

Include it in the scenario result:

```python
"structured_task": structured_task,
```

- [ ] **Step 4: Update README architecture wording**

Add this concise paragraph near the top of `README.md`:

```markdown
FireClaw 的核心研究对象是机器人本地 `RobotAgent`：每台机器人运行一个常驻 `FireClawGateway + FireClawAgent`，负责本机安全门控、技能执行、ROS/仿真适配、事件流和任务记忆。上位机 `MissionAgent/MissionGateway` 是 `MissionCoordinator`，负责理解消防员命令、选择在线机器人并下发 `StructuredRobotTask`，但不直接控制 ROS topic、service、action 或硬件执行器。
```

- [ ] **Step 5: Update architecture alignment doc**

In `docs/architecture/fireclaw-openclaw-alignment.md`, replace wording that implies software-created robots with:

```markdown
`RobotSubagentClient` 保留 OpenClaw 对齐命名，但在 FireClaw 中它调用的是已经注册并在线的物理/仿真 `RobotAgent`。上位机不会生成机器人；它只选择和调用机器人本地常驻 Agent。
```

- [ ] **Step 6: Run validation commands**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_contract.py tests/test_robot_agent_structured_task.py tests/test_gateway_structured_task.py tests/test_mission_agent_structured_task.py tests/test_mission_plan_validator.py tests/test_safety_unknown_state.py tests/test_embodied_eval.py -q
.venv/bin/python -m pytest -q
```

Expected: focused tests pass, full suite pass with existing ROS1 smoke skips unless `FIRECLAW_RUN_ROS1_SMOKE=1` is set.

- [ ] **Step 7: Update memory**

Append a timestamped section to `memory/2026-06-11/fireclaw-embodied-roadmap-review.md` with:

```markdown
## Update 2026-06-11 — RobotAgent Architecture Refinement Implemented

### Task Goal

Implement the architecture refinement based on external review: structured robot task protocol, robot-local structured execution path, unknown-state safety semantics, and documentation alignment.

### Files Modified

- `src/fireclaw_core/task_contract.py`
- `src/fireclaw_core/subagent_client.py`
- `src/fireclaw_core/gateway.py`
- `src/fireclaw_core/agent.py`
- `src/fireclaw_core/mission_agent.py`
- `src/fireclaw_core/mission_plan_validator.py`
- `src/fireclaw_core/robot.py`
- `src/fireclaw_core/safety.py`
- tests and docs listed in the implementation plan

### Verification

- Focused structured-task and safety tests passed.
- Full suite passed.

### Current Conclusion

FireClaw now preserves the old natural-language command path while adding a formal structured task path from MissionCoordinator to robot-local RobotAgent.
```

- [ ] **Step 8: Commit**

```bash
git add src/fireclaw_core tests README.md docs/architecture/fireclaw-openclaw-alignment.md memory/2026-06-11/fireclaw-embodied-roadmap-review.md
git commit -m "feat: refine robot agent structured task architecture"
```

---

## Final Verification

Run these before calling the branch complete:

```bash
.venv/bin/python -m pytest tests/test_task_contract.py tests/test_robot_agent_structured_task.py tests/test_gateway_structured_task.py tests/test_mission_agent_structured_task.py tests/test_mission_plan_validator.py tests/test_safety_unknown_state.py -q
.venv/bin/python -m pytest tests/test_embodied_gateway_e2e.py tests/test_embodied_eval.py tests/test_embodied_proof_bundle.py -q
.venv/bin/python -m pytest -q
```

Expected:

- structured-task focused tests pass;
- embodied validation tests pass;
- full suite passes with the existing skipped ROS1 smoke tests unless ROS1 smoke is explicitly enabled.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-11-fireclaw-robotagent-architecture-refinement-plan.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.
