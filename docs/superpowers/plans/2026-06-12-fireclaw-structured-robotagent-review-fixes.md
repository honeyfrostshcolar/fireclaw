# FireClaw Structured RobotAgent Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Fix the review gaps found after the structured RobotAgent and standalone server work: ROS1 unknown-state safety, operator CLI exposure, structured task source-of-truth, and request-time protocol validation.

**Architecture:** Preserve the existing natural-language command path while making the structured-task path reliable for ROS1/Gazebo. Treat unknown robot state as explicit uncertainty instead of fake low/empty values, expose `serve`/`mission` through the CLI entry point, generate `StructuredRobotTask` from `MissionSubtask` data instead of re-parsing command text, and reject malformed structured task payloads before launching robot worker threads.

**Tech Stack:** Python 3.11+, dataclasses, stdlib `argparse`/`http.server`/`urllib.request`, pytest.

**Review Record:** `memory/2026-06-12/fireclaw-structured-robotagent-review.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/fireclaw_core/robot.py` | Modify | Represent ROS1 unknown battery, sensors, and reachable floors as `None`. |
| `src/fireclaw_core/safety.py` | Modify | Handle `available_sensors=None` and carry warnings through block/confirmation decisions. |
| `tests/test_ros1_adapter_state.py` | Create | Regression tests for ROS1 adapter unknown-state reporting. |
| `tests/test_safety_unknown_state.py` | Modify | Add unknown sensor behavior coverage. |
| `src/fireclaw_core/gateway.py` | Modify | Validate structured task payloads synchronously and return HTTP 400 for invalid payloads. |
| `tests/test_gateway_structured_task.py` | Modify | Cover invalid structured task rejection. |
| `src/fireclaw_core/mission_agent.py` | Modify | Allow `submit_subtask()` to receive a `MissionSubtask` and use it as structured-task source. |
| `src/fireclaw_core/mission_scheduler.py` | Modify | Pass `MissionSubtask` objects into `MissionAgent.submit_subtask()` for initial, retry, and reassign dispatch. |
| `tests/test_mission_agent_structured_task.py` | Modify | Prove structured task generation does not depend on command re-parsing. |
| `tests/test_mission_scheduler.py` | Modify | Prove scheduler dispatch preserves structured task metadata from `MissionSubtask`. |
| `src/fireclaw_core/interactive.py` | Create | Minimal interactive mission client for `fireclaw mission`. |
| `src/fireclaw_core/mission_cli.py` | Modify | Add `serve` and `mission` subcommands. |
| `src/fireclaw_core/__main__.py` | Modify | Route `python -m fireclaw_core` to `mission_cli.main()`. |
| `pyproject.toml` | Modify | Add `fireclaw = "fireclaw_core.mission_cli:main"` console script. |
| `tests/test_interactive.py` | Create | Unit tests for built-in interactive command parsing and event display. |
| `tests/test_mission_cli.py` | Modify | Cover `serve` and `mission` subcommand registration without starting a real blocking server. |
| `memory/2026-06-12/fireclaw-structured-robotagent-review.md` | Modify | Record implementation outcome and verification commands. |

---

### Task 1: Fix ROS1 Unknown-State Semantics

**Files:**
- Modify: `src/fireclaw_core/robot.py`
- Modify: `src/fireclaw_core/safety.py`
- Create: `tests/test_ros1_adapter_state.py`
- Modify: `tests/test_safety_unknown_state.py`

- [x] **Step 1: Write failing ROS1 adapter state tests**

Create `tests/test_ros1_adapter_state.py`:

```python
from __future__ import annotations

from pathlib import Path

from fireclaw_core.robot import Ros1RobotAdapter
from fireclaw_core.ros1_config import load_ros1_adapter_config


def _write_config(path: Path) -> None:
    path.write_text(
        """{
  "robot_id": "robot-ros1",
  "skill_remap": {
    "navigate_to_floor": {
      "interface": "action",
      "name": "/move_base",
      "type": "move_base_msgs/MoveBaseAction",
      "action": "navigate_to_floor",
      "payload": {"target_pose": {"header": {"frame_id": "map"}}}
    }
  }
}
""",
        encoding="utf-8",
    )


def test_ros1_robot_adapter_reports_unknown_state_as_none(tmp_path: Path):
    config_path = tmp_path / "ros1.json"
    _write_config(config_path)
    robot = Ros1RobotAdapter(config=load_ros1_adapter_config(config_path), transport=None)

    state = robot.get_robot_state()
    environment = robot.get_environment_state()

    assert state.supports_real_execution is True
    assert state.battery_percent is None
    assert state.available_sensors is None
    assert environment.reachable_floors is None
```

- [x] **Step 2: Add failing SafetyGate sensor unknown test**

Append to `tests/test_safety_unknown_state.py`:

```python
def test_unknown_sensors_require_confirmation_for_real_robot_skill_with_sensor_requirement():
    registry = SkillRegistry(skills={})
    registry.register(Skill(
        name="search_for_victims",
        description="search",
        handler=lambda inputs: None,
        dry_run_only=False,
        allow_real_robot=True,
        required_sensors=["thermal_camera"],
    ))
    planning = PlanningResult(
        status="planned",
        message="planned",
        intent="search",
        target_floor=2,
        plan=Plan("search", [PlanStep("search_for_victims", {"floor": 2})]),
    )

    decision = SafetyGate().evaluate(
        planning,
        registry,
        dry_run=False,
        robot_state=RobotState("robot-1", "ros1", False, True, None, 1, None, True),
        environment_state=EnvironmentState(reachable_floors=[2]),
    )

    assert decision.status == "require_confirmation"
    assert "Robot robot-1 available sensors are unknown." in decision.reasons
```

- [x] **Step 3: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_adapter_state.py tests/test_safety_unknown_state.py::test_unknown_sensors_require_confirmation_for_real_robot_skill_with_sensor_requirement -q
```

Expected:

- `test_ros1_robot_adapter_reports_unknown_state_as_none` fails because ROS1 state currently returns `0.0`, `[]`, and `[]`.
- `test_unknown_sensors_require_confirmation_for_real_robot_skill_with_sensor_requirement` fails with `TypeError` or a sensor block because `SafetyGate` does not handle `available_sensors=None`.

- [x] **Step 4: Update RobotState and ROS1 adapter state**

In `src/fireclaw_core/robot.py`, change `RobotState.available_sensors`:

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
```

Update `Ros1RobotAdapter.get_robot_state()`:

```python
    def get_robot_state(self) -> RobotState:
        return RobotState(
            robot_id=self.robot_id,
            mode=self.mode,
            dry_run=self.dry_run,
            online=not self.emergency_stopped,
            battery_percent=None,
            current_floor=self.current_floor,
            available_sensors=None,
            supports_real_execution=True,
        )
```

Update `Ros1RobotAdapter.get_environment_state()`:

```python
    def get_environment_state(self) -> EnvironmentState:
        return EnvironmentState(reachable_floors=None, hazards=None, victims_by_floor=None)
```

- [x] **Step 5: Update SafetyGate sensor unknown handling**

In `src/fireclaw_core/safety.py`, replace sensor inference with this block:

```python
        sensors_unknown = False
        if available_sensors is not None:
            sensors = available_sensors
        elif robot_state is not None and robot_state.available_sensors is None:
            sensors = set()
            sensors_unknown = True
        elif robot_state is not None:
            sensors = set(robot_state.available_sensors)
        else:
            sensors = set()
```

Replace the `missing_sensors` check with:

```python
        missing_sensors: list[str] = []
        sensor_confirmations: list[str] = []
        sensor_warnings: list[str] = []
        for step in planning_result.plan.steps:
            skill = registry.get(step.skill_name)
            if skill is None:
                continue
            for sensor in skill.required_sensors:
                if sensors_unknown:
                    message = f"Robot {robot_state.robot_id if robot_state else 'unknown'} available sensors are unknown."
                    if dry_run:
                        sensor_warnings.append(message)
                    else:
                        sensor_confirmations.append(message)
                    break
                if sensor not in sensors:
                    missing_sensors.append(
                        f"Skill {step.skill_name} requires unavailable sensor: {sensor}"
                    )
        state_warnings.extend(sensor_warnings)
        state_confirmations.extend(sensor_confirmations)
        if missing_sensors:
            return SafetyDecision(status="block", reasons=missing_sensors, warnings=state_warnings)
```

Ensure later confirmation handling still starts with:

```python
        confirmation_reasons: list[str] = list(state_confirmations)
```

- [x] **Step 6: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_adapter_state.py tests/test_safety_unknown_state.py tests/test_safety.py tests/test_robot.py -q
```

Expected: all pass.

- [x] **Step 7: Commit**

```bash
git add src/fireclaw_core/robot.py src/fireclaw_core/safety.py tests/test_ros1_adapter_state.py tests/test_safety_unknown_state.py
git commit -m "fix: represent ROS1 unknown state explicitly"
```

---

### Task 2: Validate Structured Task Payloads at Gateway Boundary

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Modify: `tests/test_gateway_structured_task.py`

- [x] **Step 1: Write failing HTTP 400 test**

Append to `tests/test_gateway_structured_task.py`:

```python
from urllib.error import HTTPError


def test_gateway_rejects_invalid_structured_task_payload(tmp_path):
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
        try:
            _json_request(
                gateway.base_url,
                "POST",
                "/tasks",
                {
                    "command": "去2楼搜索受困人员",
                    "structured_task": {
                        "task_id": "bad-structured-task",
                        "task_type": "search",
                        "target": {"floor": 2},
                        "required_skills": [],
                    },
                },
            )
        except HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 400
            assert body["status"] == "error"
            assert "required_skills must not be empty" in body["message"]
        else:
            raise AssertionError("Expected HTTP 400 for invalid structured_task")
    finally:
        gateway.stop()
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_structured_task.py::test_gateway_rejects_invalid_structured_task_payload -q
```

Expected: fail because the current Gateway accepts the request and returns `accepted`.

- [x] **Step 3: Add synchronous structured task validation**

In `src/fireclaw_core/gateway.py`, add imports near the top:

```python
from fireclaw_core.task_contract import StructuredRobotTask, validate_structured_robot_task
```

In `_handle_post()` for `parsed.path == "/tasks"`, replace the current `structured_task` block with:

```python
                structured_task = payload.get("structured_task")
                if structured_task is not None:
                    if not isinstance(structured_task, dict):
                        self._write_error(handler, HTTPStatus.BAD_REQUEST, "Field 'structured_task' must be an object when provided.")
                        return
                    structured_task_errors = validate_structured_robot_task(
                        StructuredRobotTask.from_dict(structured_task)
                    )
                    if structured_task_errors:
                        self._write_error(
                            handler,
                            HTTPStatus.BAD_REQUEST,
                            "; ".join(structured_task_errors),
                        )
                        return
```

In `_execute_agent_task()`, remove the local import and use the module-level import:

```python
        if structured_task is not None:
            task_object = StructuredRobotTask.from_dict(structured_task)
            result = agent.run_structured_task(task_object)
        else:
            result = agent.run(command)
```

- [x] **Step 4: Run Gateway focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_structured_task.py tests/test_gateway.py -q
```

Expected: all pass.

- [x] **Step 5: Commit**

```bash
git add src/fireclaw_core/gateway.py tests/test_gateway_structured_task.py
git commit -m "fix: validate structured task payloads at gateway boundary"
```

---

### Task 3: Use MissionSubtask as Structured Task Source of Truth

**Files:**
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `src/fireclaw_core/mission_scheduler.py`
- Modify: `tests/test_mission_agent_structured_task.py`
- Modify: `tests/test_mission_scheduler.py`

- [x] **Step 1: Write failing MissionAgent source-of-truth test**

Append to `tests/test_mission_agent_structured_task.py`:

```python
from fireclaw_core.mission_planner import MissionSubtask


def test_mission_agent_structured_task_uses_mission_subtask_floor_not_command_text():
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-1",
            base_url="http://robot-1",
            capabilities=["search_for_victims"],
        )
    ])
    client = RecordingClient()
    agent = MissionAgent(registry=registry, subagent_client=client)
    planned_subtask = MissionSubtask(
        robot_id="robot-1",
        command="search target zone alpha",
        floor=2,
        capability_required="search_for_victims",
        execution_group=0,
    )

    result = agent.submit_subtask(
        "robot-1",
        planned_subtask.command,
        session_id="mission-structured",
        operator={"operator_id": "operator-1"},
        mission_subtask=planned_subtask,
    )

    assert result["status"] == "accepted"
    structured_task = client.calls[0][1]["structured_task"]
    assert structured_task["target"] == {"floor": 2}
    assert structured_task["command"] == "search target zone alpha"
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent_structured_task.py::test_mission_agent_structured_task_uses_mission_subtask_floor_not_command_text -q
```

Expected: fail with `TypeError: MissionAgent.submit_subtask() got an unexpected keyword argument 'mission_subtask'`.

- [x] **Step 3: Extend `MissionAgent.submit_subtask()` signature**

In `src/fireclaw_core/mission_agent.py`, update the signature:

```python
    def submit_subtask(
        self,
        robot_id: str,
        command: str,
        *,
        session_id: str | None = None,
        dedupe_key: str | None = None,
        operator: dict[str, Any] | None = None,
        mission: dict[str, Any] | None = None,
        mission_subtask: MissionSubtask | None = None,
    ) -> dict[str, Any]:
```

Replace the existing floor/capability extraction block with:

```python
        structured_task = None
        if mission_subtask is not None:
            structured_task = structured_task_from_mission_subtask(
                mission_id=mission_id,
                subtask=mission_subtask,
                operator_id=(operator or {}).get("operator_id") if isinstance(operator, dict) else None,
            ).to_dict()
        else:
            floor = RuleBasedPlanner()._extract_floor(command)
            capability = _capability_from_entry(entry)
            if floor is not None and capability != "unknown":
                generated_subtask = MissionSubtask(
                    robot_id=robot_id,
                    command=command,
                    floor=floor,
                    capability_required=capability,
                    execution_group=0,
                )
                structured_task = structured_task_from_mission_subtask(
                    mission_id=mission_id,
                    subtask=generated_subtask,
                    operator_id=(operator or {}).get("operator_id") if isinstance(operator, dict) else None,
                ).to_dict()
```

- [x] **Step 4: Pass MissionSubtask from direct non-scheduler path**

In `MissionAgent.plan_and_submit()` direct loop, update the call:

```python
            result = self.submit_subtask(
                subtask.robot_id,
                subtask.command,
                session_id=mission_id,
                dedupe_key=f"{mission_id}-{subtask.robot_id}-{subtask.floor}",
                operator=operator,
                mission={"mission_id": mission_id, "execution_group": subtask.execution_group},
                mission_subtask=subtask,
            )
```

- [x] **Step 5: Pass MissionSubtask from scheduler initial, retry, and reassign calls**

In `src/fireclaw_core/mission_scheduler.py`, update the initial dispatch call:

```python
                result = self.mission_agent.submit_subtask(
                    subtask.robot_id,
                    subtask.command,
                    session_id=mission_id,
                    dedupe_key=f"{mission_id}-{subtask.robot_id}-{subtask.floor}",
                    operator=operator,
                    mission={"mission_id": mission_id, "execution_group": subtask.execution_group},
                    mission_subtask=subtask,
                )
```

Update the retry call:

```python
                        result = self.mission_agent.submit_subtask(
                            subtask.robot_id,
                            subtask.command,
                            session_id=mission_id,
                            dedupe_key=f"{mission_id}-{subtask.robot_id}-{subtask.floor}-retry{retry_counts[subtask.robot_id]}",
                            operator=operator,
                            mission={"mission_id": mission_id, "execution_group": subtask.execution_group},
                            mission_subtask=subtask,
                        )
```

Update the reassign call by creating a replacement subtask for the new robot:

```python
                        reassigned_subtask = MissionSubtask(
                            robot_id=new_robot,
                            command=subtask.command,
                            floor=subtask.floor,
                            capability_required=subtask.capability_required,
                            execution_group=subtask.execution_group,
                        )
                        result = self.mission_agent.submit_subtask(
                            new_robot,
                            subtask.command,
                            session_id=mission_id,
                            dedupe_key=f"{mission_id}-{new_robot}-{subtask.floor}-reassign{reassign_counts[subtask.robot_id]}",
                            operator=operator,
                            mission={"mission_id": mission_id, "execution_group": subtask.execution_group},
                            mission_subtask=reassigned_subtask,
                        )
```

- [x] **Step 6: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent_structured_task.py tests/test_mission_scheduler.py tests/test_mission_agent.py -q
```

Expected: all pass.

- [x] **Step 7: Commit**

```bash
git add src/fireclaw_core/mission_agent.py src/fireclaw_core/mission_scheduler.py tests/test_mission_agent_structured_task.py tests/test_mission_scheduler.py
git commit -m "fix: derive structured tasks from mission subtasks"
```

---

### Task 4: Add Minimal Interactive Mission Client

**Files:**
- Create: `src/fireclaw_core/interactive.py`
- Create: `tests/test_interactive.py`

- [x] **Step 1: Write failing interactive tests**

Create `tests/test_interactive.py`:

```python
from __future__ import annotations

from fireclaw_core.interactive import display_event, parse_builtin_command


def test_parse_builtin_command_help():
    action, arg = parse_builtin_command("help")
    assert action == "help"
    assert arg is None


def test_parse_builtin_command_cancel():
    action, arg = parse_builtin_command("cancel mission-123")
    assert action == "cancel"
    assert arg == "mission-123"


def test_parse_builtin_command_submit():
    action, arg = parse_builtin_command("去二楼救人")
    assert action == "submit"
    assert arg == "去二楼救人"


def test_display_event_completed(capsys):
    display_event({"event_type": "task.completed", "robot_id": "robot-1", "task_id": "task-1"})
    captured = capsys.readouterr()
    assert "completed" in captured.out
    assert "robot-1" in captured.out
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_interactive.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'fireclaw_core.interactive'`.

- [x] **Step 3: Create `interactive.py`**

Create `src/fireclaw_core/interactive.py`:

```python
from __future__ import annotations

import sys
import time
from typing import Any

from fireclaw_core.mission_gateway_client import MissionGatewayClient

TERMINAL_MISSION_STATUSES = {"succeeded", "failed", "cancelled", "completed"}


def parse_builtin_command(input_text: str) -> tuple[str, str | None]:
    text = input_text.strip()
    if not text:
        return "help", None
    if text in {"help", "quit", "exit", "status"}:
        return text, None
    if text.startswith("cancel "):
        mission_id = text[len("cancel "):].strip()
        return "cancel", mission_id
    return "submit", text


def display_event(event: dict[str, Any]) -> None:
    event_type = str(event.get("event_type") or event.get("type") or "unknown")
    robot_id = str(event.get("robot_id") or "")
    task_id = str(event.get("task_id") or "")
    status = str(event.get("status") or event_type)
    if event_type in {"task.completed", "task.failed", "task.cancelled"}:
        print(f"[events] {robot_id}: {task_id} -> {event_type.split('.')[-1]}")
    else:
        print(f"[events] {robot_id}: {status}")


def run_interactive(server_url: str = "http://127.0.0.1:8766", timeout: float = 30.0) -> None:
    client = MissionGatewayClient(server_url, timeout=timeout)
    print("FireClaw Mission Console")
    print(f"Connected to {server_url}")
    print("Type 'help' for commands, 'quit' to exit.")
    while True:
        try:
            raw = input("fireclaw> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        action, arg = parse_builtin_command(raw)
        if action in {"quit", "exit"}:
            return
        if action == "help":
            print("Commands: help, status, cancel <mission_id>, quit, or a natural-language mission.")
            continue
        if action == "status":
            print(client.get_fleet_state())
            continue
        if action == "cancel" and arg:
            print(client.cancel_mission(arg))
            continue
        if action == "submit" and arg:
            _submit_and_poll(client, arg)


def _submit_and_poll(client: MissionGatewayClient, command: str) -> None:
    result = client.submit_mission(command)
    mission_id = result.get("mission_id")
    print(result)
    if not isinstance(mission_id, str) or not mission_id:
        return
    seen = 0
    while True:
        try:
            events_body = client.get_mission_events(mission_id)
            events = events_body.get("events", [])
            if isinstance(events, list):
                for event in events[seen:]:
                    if isinstance(event, dict):
                        display_event(event)
                    seen += 1
            trace = client.get_mission_trace(mission_id)
            status = trace.get("status") or trace.get("mission_status")
            if status in TERMINAL_MISSION_STATUSES:
                print(f"[done] {status}")
                return
            time.sleep(0.5)
        except Exception as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return
```

- [x] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_interactive.py -q
```

Expected: `4 passed`.

- [x] **Step 5: Commit**

```bash
git add src/fireclaw_core/interactive.py tests/test_interactive.py
git commit -m "feat: add interactive mission client"
```

---

### Task 5: Expose `fireclaw serve` and `fireclaw mission`

**Files:**
- Modify: `src/fireclaw_core/mission_cli.py`
- Modify: `src/fireclaw_core/__main__.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_mission_cli.py`

- [x] **Step 1: Add failing CLI registration tests**

Append to `tests/test_mission_cli.py`:

```python
def test_serve_subcommand_registered(monkeypatch, tmp_path):
    import fireclaw_core.serve as serve_module

    called = {}

    def fake_run_server_blocking(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(serve_module, "run_server_blocking", fake_run_server_blocking)
    result = _run_cli([
        "serve",
        "--host", "127.0.0.1",
        "--port", "0",
        "--data-dir", str(tmp_path / "data"),
    ])

    assert result.returncode == 0
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 0
    assert called["data_dir"] == tmp_path / "data"


def test_mission_subcommand_registered(monkeypatch):
    import fireclaw_core.interactive as interactive_module

    called = {}

    def fake_run_interactive(server_url: str, timeout: float):
        called["server_url"] = server_url
        called["timeout"] = timeout

    monkeypatch.setattr(interactive_module, "run_interactive", fake_run_interactive)
    result = _run_cli(["mission", "--server", "http://localhost:9999", "--timeout", "7"])

    assert result.returncode == 0
    assert called == {"server_url": "http://localhost:9999", "timeout": 7.0}
```

If `_run_cli()` in this file expects a module subprocess rather than direct args, adapt only the helper invocation to the existing local pattern while keeping the assertions above.

- [x] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_cli.py::test_serve_subcommand_registered tests/test_mission_cli.py::test_mission_subcommand_registered -q
```

Expected: fail because `serve` and `mission` subcommands are not registered.

- [x] **Step 3: Add `serve` parser in `mission_cli.py`**

In `src/fireclaw_core/mission_cli.py`, add imports near the top:

```python
from pathlib import Path
```

After existing subparser setup, add:

```python
    serve = subparsers.add_parser("serve", help="Start a persistent MissionGateway server.")
    serve.add_argument("--adapter", default="simulator", help="Robot adapter label for deployment metadata.")
    serve.add_argument("--ros1-config", default=None, help="Path to ROS1 adapter config for robot gateways.")
    serve.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    serve.add_argument("--port", type=int, default=8766, help="Port to bind.")
    serve.add_argument("--data-dir", type=Path, default=Path("data"), help="Persistent data directory.")
    serve.add_argument("--planner", choices=["deterministic", "llm"], default="deterministic", help="Planner backend.")
    serve.add_argument("--provider-base-url", default=None, help="LLM provider base URL.")
    serve.add_argument("--provider-api-key", default=None, help="LLM provider API key.")
    serve.add_argument("--model", default=None, help="LLM model id.")
    serve.add_argument("--llm-trace-path", default=None, help="Path to LLM trace JSONL file.")
```

- [x] **Step 4: Add `mission` parser in `mission_cli.py`**

Add:

```python
    mission = subparsers.add_parser("mission", help="Open the interactive mission console.")
    mission.add_argument("--server", default="http://127.0.0.1:8766", help="MissionGateway base URL.")
    mission.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds.")
```

- [x] **Step 5: Add command handlers**

Before the existing line `parser.error(f"Unknown command: {args.command_name}")`, add:

```python
    if args.command_name == "serve":
        from fireclaw_core.serve import run_server_blocking
        run_server_blocking(
            adapter=args.adapter,
            ros1_config=args.ros1_config,
            host=args.host,
            port=args.port,
            planner_type=args.planner,
            provider_base_url=args.provider_base_url,
            provider_api_key=args.provider_api_key,
            model=args.model,
            llm_trace_path=args.llm_trace_path,
            data_dir=args.data_dir,
        )
        return 0
    if args.command_name == "mission":
        from fireclaw_core.interactive import run_interactive
        run_interactive(server_url=args.server, timeout=args.timeout)
        return 0
```

- [x] **Step 6: Route module entrypoint to mission CLI**

Replace `src/fireclaw_core/__main__.py` with:

```python
from __future__ import annotations

from fireclaw_core.mission_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
```

This intentionally makes `python -m fireclaw_core serve --help` use the new mission CLI. The old local agent path remains available through existing module functions and can later be reintroduced as an `agent` subcommand if needed.

- [x] **Step 7: Add console script**

In `pyproject.toml`, add after `[project.optional-dependencies]`:

```toml
[project.scripts]
fireclaw = "fireclaw_core.mission_cli:main"
```

- [x] **Step 8: Run CLI tests and help probes**

Run:

```bash
.venv/bin/python -m pytest tests/test_interactive.py tests/test_mission_cli.py::test_serve_subcommand_registered tests/test_mission_cli.py::test_mission_subcommand_registered -q
.venv/bin/python -m fireclaw_core serve --help
.venv/bin/python -m fireclaw_core mission --help
```

Expected:

- pytest command passes;
- `serve --help` lists `--data-dir`, `--planner`, `--host`, and `--port`;
- `mission --help` lists `--server` and `--timeout`.

- [x] **Step 9: Commit**

```bash
git add src/fireclaw_core/mission_cli.py src/fireclaw_core/__main__.py pyproject.toml tests/test_mission_cli.py
git commit -m "feat: expose serve and mission CLI commands"
```

---

### Task 6: Update Documentation, Memory, and Run Final Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture/fireclaw-openclaw-alignment.md`
- Modify: `memory/2026-06-12/fireclaw-structured-robotagent-review.md`

- [x] **Step 1: Update README with corrected operator commands**

In `README.md`, add this command block near the existing simulator/Gazebo validation instructions:

```markdown
### Standalone Mission Gateway

Start the mission coordinator:

```bash
fireclaw serve --data-dir data --host 127.0.0.1 --port 8766 --planner deterministic
```

Open the operator console:

```bash
fireclaw mission --server http://127.0.0.1:8766
```

For ROS1/Gazebo work, keep the robot-local `FireClawGateway` responsible for the adapter. The mission coordinator dispatches to registered robot gateways from `data/robots.json`; it does not directly publish ROS topics or actions.
```

- [x] **Step 2: Update architecture doc with unknown-state note**

In `docs/architecture/fireclaw-openclaw-alignment.md`, add:

```markdown
ROS1/Gazebo adapter state must distinguish unknown values from explicit unsafe values. `None` means FireClaw does not yet know the battery, sensors, or reachable floors; `0.0` and `[]` mean the adapter has positively observed zero battery or no reachable floors. SafetyGate uses that distinction to warn or require operator confirmation instead of blocking valid Gazebo bring-up.
```

- [x] **Step 3: Append memory implementation record**

Append to `memory/2026-06-12/fireclaw-structured-robotagent-review.md`:

```markdown
## Update 2026-06-12 — Review Fixes Implemented

### Task Goal

Fix the review findings: ROS1 unknown-state semantics, structured-task Gateway validation, MissionSubtask-based structured task generation, and CLI exposure for `fireclaw serve` / `fireclaw mission`.

### Files Modified

- `src/fireclaw_core/robot.py`
- `src/fireclaw_core/safety.py`
- `src/fireclaw_core/gateway.py`
- `src/fireclaw_core/mission_agent.py`
- `src/fireclaw_core/mission_scheduler.py`
- `src/fireclaw_core/interactive.py`
- `src/fireclaw_core/mission_cli.py`
- `src/fireclaw_core/__main__.py`
- `pyproject.toml`
- related tests and docs

### Verification

- Focused review-fix tests passed.
- CLI help probes for `serve` and `mission` passed.
- Full suite passed.

### Current Conclusion

The structured RobotAgent path now treats ROS1 unknown state explicitly, rejects malformed structured task payloads at the Gateway boundary, preserves MissionSubtask data as the structured-task source of truth, and exposes standalone operator commands.
```

- [x] **Step 4: Run focused verification**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_adapter_state.py tests/test_safety_unknown_state.py tests/test_gateway_structured_task.py tests/test_mission_agent_structured_task.py tests/test_mission_scheduler.py tests/test_interactive.py tests/test_mission_cli.py -q
.venv/bin/python -m fireclaw_core serve --help
.venv/bin/python -m fireclaw_core mission --help
```

Expected:

- pytest command passes;
- help probes exit 0 and show the new subcommands.

- [x] **Step 5: Run embodied verification**

Run:

```bash
.venv/bin/python -m pytest tests/test_embodied_gateway_e2e.py tests/test_embodied_eval.py tests/test_embodied_proof_bundle.py -q
```

Expected: all pass.

- [x] **Step 6: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full suite passes with existing ROS1 smoke skips unless `FIRECLAW_RUN_ROS1_SMOKE=1` is set.

- [x] **Step 7: Commit**

```bash
git add README.md docs/architecture/fireclaw-openclaw-alignment.md memory/2026-06-12/fireclaw-structured-robotagent-review.md
git commit -m "docs: record structured robotagent review fixes"
```

---

## Final Verification

Run these commands before calling the branch complete:

```bash
.venv/bin/python -m pytest tests/test_ros1_adapter_state.py tests/test_safety_unknown_state.py tests/test_gateway_structured_task.py tests/test_mission_agent_structured_task.py tests/test_mission_scheduler.py tests/test_interactive.py tests/test_mission_cli.py -q
.venv/bin/python -m pytest tests/test_embodied_gateway_e2e.py tests/test_embodied_eval.py tests/test_embodied_proof_bundle.py -q
.venv/bin/python -m fireclaw_core serve --help
.venv/bin/python -m fireclaw_core mission --help
.venv/bin/python -m pytest -q
```

Expected:

- focused review-fix tests pass;
- embodied validation tests pass;
- CLI help probes show `serve` and `mission`;
- full suite passes with the known ROS1 smoke skips when ROS1 smoke is not enabled.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-12-fireclaw-structured-robotagent-review-fixes.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.
