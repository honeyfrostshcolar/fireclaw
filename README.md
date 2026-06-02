# FireClaw

FireClaw is a Python-first embodied agent framework for firefighting robots, inspired by OpenClaw's agent, skill, and memory architecture.

The first version is intentionally a pure dry-run core. It does not control real hardware, ROS, CUDA workloads, or robot SDKs. Its purpose is to validate the main agent loop:

```text
natural-language command -> planner -> safety gate -> skill executor -> memory
```

## Current Demo

The first supported command is:

```text
去二楼救人
```

It produces a five-step dry-run rescue plan:

1. `navigate_to_floor`
2. `search_for_victims`
3. `assess_victim`
4. `report_status`
5. `return_to_safe_zone`

## Environment

Use the project virtual environment. Do not use the system `python3` if it points to Python 3.8.

```bash
uv venv --python /home/nankai/.local/bin/python3.11 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

## Run Tests

```bash
.venv/bin/python -m pytest -v
```

## Run the Dry-Run Agent

```bash
.venv/bin/python -m fireclaw_core "去二楼救人" --memory-path /tmp/fireclaw-demo-memory.jsonl
```

The command prints a structured JSON result and appends the same run record to the JSONL memory file.

Runtime context can be configured from the CLI:

```bash
.venv/bin/python -m fireclaw_core "去二楼救人" \
  --adapter simulator \
  --robot-id fire-robot-01 \
  --session-id rescue-shift-a \
  --memory-path /tmp/fireclaw-demo-memory.jsonl

.venv/bin/python -m fireclaw_core "运行 thermal_policy" \
  --skills-dir skills \
  --available-sensor thermal_camera \
  --memory-path /tmp/fireclaw-demo-memory.jsonl
```

`--available-sensor` can be repeated. The values are passed into the safety gate and checked against each skill's `required_sensors`.

`--adapter` selects the robot adapter used by built-in robot skills:

- `dry-run`: default dependency-free dry-run adapter.
- `simulator`: deterministic in-process simulator with floor, victim, sensor, and environment state.
- `mock-ros2`: ROS2-shaped test double that records topic-like commands without importing ROS2.

For safety-gate experiments, `--real-run` sets `dry_run=false`:

```bash
.venv/bin/python -m fireclaw_core "去二楼救人" --real-run
```

This does not create a real robot adapter. It only makes the safety gate evaluate real-robot eligibility. Default built-in dry-run skills are blocked in this mode.

## Run the Operator Console

The raw agent and Gateway interfaces keep JSON for programs, logs, ROS2 nodes, and future frontend clients.
For human operators, use the operator console. It projects structured task events into Chinese progress text:

```bash
.venv/bin/python -m fireclaw_core.operator_console "去二楼救人" \
  --adapter simulator \
  --robot-id robot-01 \
  --session-id operator-a \
  --memory-path /tmp/fireclaw-operator-memory.jsonl \
  --event-path /tmp/fireclaw-operator-events.jsonl \
  --no-workspace-skills
```

Example output:

```text
已接收任务：去二楼救人。
正在规划救援任务。
安全检查通过。
正在前往2楼。
正在搜索2楼被困人员。
正在评估被困人员状态。
正在向操作员报告现场状态。
正在返回安全区域。
任务完成：FireClaw dry-run rescue plan completed.
```

This mirrors OpenClaw's split between structured Gateway events and human-facing TUI/channel projection.

## Run the Gateway

FireClaw Gateway v1 is a local HTTP control plane around the same agent core. It is intended for a robot-side resident process:

```text
HTTP in -> FireClawAgent -> SkillExecutor -> RobotAdapter -> simulator / mock ROS2 / future real ROS2
```

Start a local simulator gateway:

```bash
.venv/bin/python -m fireclaw_core.gateway \
  --host 127.0.0.1 \
  --port 8765 \
  --adapter simulator \
  --robot-id robot-01 \
  --memory-path /tmp/fireclaw-gateway-memory.jsonl \
  --event-path /tmp/fireclaw-gateway-events.jsonl
```

Check health and state:

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/state
```

Submit a natural-language task:

```bash
curl -X POST http://127.0.0.1:8765/tasks \
  -H "Content-Type: application/json" \
  -d '{"command": "去二楼救人", "session_id": "operator-a"}'
```

`POST /tasks`, `POST /confirm`, and `POST /cancel` are asynchronous HTTP entrypoints. They return immediately with HTTP `202 Accepted` and a `task_id`:

```json
{
  "status": "accepted",
  "task_id": "task-...",
  "session_id": "operator-a",
  "message": "任务已接收，正在后台执行。"
}
```

Use the `task_id` to inspect the current task trace, final result, and event stream. While the background task is still running, `result` is `null`; after completion, the final agent result is available under `result`:

```bash
curl http://127.0.0.1:8765/tasks/task-REPLACE_WITH_ID
curl http://127.0.0.1:8765/tasks/task-REPLACE_WITH_ID/events
```

Gateway v1 records append-only JSONL events such as:

- `task.received`
- `task.planned`
- `safety.decided`
- `confirmation.pending`
- `confirmation.confirmed`
- `skill.started`
- `skill.attempted`
- `skill.succeeded`
- `skill.failed`
- `task.completed`
- `task.cancelled`

Confirm or cancel the latest pending task in a session:

```bash
curl -X POST http://127.0.0.1:8765/confirm \
  -H "Content-Type: application/json" \
  -d '{"session_id": "operator-a"}'

curl -X POST http://127.0.0.1:8765/cancel \
  -H "Content-Type: application/json" \
  -d '{"session_id": "operator-a"}'
```

The Python API still exposes synchronous `FireClawGateway.run_agent(...)` for local test harnesses and in-process tooling. External systems should prefer the asynchronous HTTP endpoints or `FireClawGateway.submit_agent(...)`.

Inspect skills, recent memory, and recent events:

```bash
curl http://127.0.0.1:8765/skills
curl 'http://127.0.0.1:8765/memory/recent?session_id=operator-a&limit=5'
curl 'http://127.0.0.1:8765/events/recent?session_id=operator-a&limit=20'
```

Gateway v1 binds to localhost by default and has no authentication yet. Do not expose it on a public network.

## Session State

Every task result includes session metadata:

```json
{
  "session": {
    "session_id": "rescue-shift-a",
    "turn_index": 1,
    "resolved_command": "去二楼救人",
    "context_used": false
  }
}
```

Use `--session-id` to keep memory and recall scoped to a conversation:

```bash
.venv/bin/python -m fireclaw_core "之前做过什么" \
  --session-id rescue-shift-a \
  --memory-path /tmp/fireclaw-demo-memory.jsonl
```

FireClaw also supports a minimal deterministic clarification flow within a session. If one turn asks for clarification and the next turn only supplies a floor, such as `二楼`, the agent resolves it to `去二楼救人` before planning while preserving the original command in the result.

## Planner Interface

The default planner is `RuleBasedPlanner`, which keeps the current dry-run demo deterministic.

FireClaw also has a provider-agnostic `LLMToolCallingPlanner` interface. It does not call any model by itself. Instead, it accepts a client with:

```python
class PlanningClient:
    def plan(self, request: dict) -> dict:
        ...
```

The request includes the operator command plus planner context:

- `session_id`
- `turn_index`
- recent session memory records
- skill metadata

The client must return a structured planning response with an intent and skill steps. FireClaw validates that response and falls back to `RuleBasedPlanner` if it is malformed. This lets future OpenAI, local model, or ROS-side planning clients plug into the same agent without changing safety, execution, or memory.

## Planner Tool Schemas

FireClaw serializes skill metadata into provider-agnostic tool schemas before sending planner requests to a future model client:

```json
{
  "type": "function",
  "function": {
    "name": "echo_policy",
    "description": "Example external subprocess skill for algorithm wrappers.",
    "parameters": {
      "type": "object",
      "additionalProperties": true
    }
  },
  "x-fireclaw": {
    "runtime": "subprocess",
    "dry_run_only": true,
    "max_attempts": 1,
    "idempotent": true,
    "required_sensors": [],
    "failure_categories": ["invalid_input", "subprocess_error"],
    "allow_real_robot": false,
    "timeout_seconds": 5.0
  }
}
```

Planner requests include:

- `command`
- `context`
- `tools`
- `response_schema`
- `instructions`

The schema is intentionally model-agnostic. An OpenAI client, local model client, or ROS-side planner can adapt this payload to its own API without changing the FireClaw agent loop.

You can also ask the agent to recall recent tasks from the same memory file:

```bash
.venv/bin/python -m fireclaw_core "之前做过什么" --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Recall commands return recent memory records and do not execute skills or append another memory entry.

FireClaw also supports minimal structured memory retrieval for common operator questions:

```bash
.venv/bin/python -m fireclaw_core "之前二楼救人成功了吗" \
  --session-id rescue-shift-a \
  --memory-path /tmp/fireclaw-demo-memory.jsonl

.venv/bin/python -m fireclaw_core "上次失败原因是什么" \
  --session-id rescue-shift-a \
  --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Retrieval commands filter JSONL memory by session, status, intent, and target floor when those fields are present in the command. They return `status="retrieved"`, do not execute skills, and do not append another memory entry.

You can inspect the currently registered skills:

```bash
.venv/bin/python -m fireclaw_core "你有哪些技能" --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Skill listing commands return skill metadata and do not execute skills or append memory entries.

## Skill Runtime Direction

The core package should stay lightweight. It should not directly depend on CUDA, PyTorch, reinforcement-learning environments, ROS2, or robot SDKs.

Future heavy algorithms should be exposed as skills through explicit runtime adapters, for example:

- `in_process` for lightweight Python dry-run skills;
- `subprocess` for scripts or isolated Python environments;
- `external_conda` for CUDA/RL/deep-learning algorithms with their own dependencies;
- `ros2` for robot-side ROS nodes;
- `http` for local or remote model services.

This keeps the FireClaw agent stable while allowing each robotics algorithm to keep its own dependency stack.

The first external runtime adapter is `SubprocessSkillRunner`. It sends JSON inputs to a command on stdin and expects a JSON object on stdout:

```json
{
  "ok": true,
  "data": {
    "example": "result"
  }
}
```

This is the intended bridge for early CUDA/RL algorithms that live in separate Python or conda environments.

Subprocess skills can also be loaded from JSON manifests under `skills/**/*.skill.json`:

```json
{
  "name": "rl_navigation",
  "description": "Runs an isolated RL navigation policy.",
  "runtime": "subprocess",
  "command": ["/path/to/conda/env/bin/python", "run_policy.py"],
  "timeout_seconds": 30,
  "dry_run_only": true,
  "max_attempts": 1,
  "idempotent": false,
  "required_sensors": ["rgb_camera", "thermal_camera"],
  "failure_categories": ["timeout", "perception_uncertain"],
  "allow_real_robot": false,
  "risk_level": "high",
  "input_schema": {
    "type": "object",
    "properties": {
      "floor": {"type": "integer"}
    },
    "required": ["floor"],
    "additionalProperties": false
  }
}
```

The `command` field must be a list of strings, not a shell command string. This avoids accidental shell parsing and keeps skill execution explicit.

Manifest metadata is part of the skill contract:

- `max_attempts` controls opt-in retry behavior.
- `idempotent` must be `true` when `max_attempts > 1`.
- `required_sensors` lists sensor assumptions the planner or safety gate can later inspect.
- `failure_categories` documents expected failure modes.
- `allow_real_robot=true` is only valid when `dry_run_only=false`.
- `risk_level` must be one of `low`, `medium`, `high`, or `critical`.
- `input_schema` declares the JSON object inputs a planner should provide.

These fields are exposed by skill listing commands and recorded in agent-visible metadata.

Commands run with the manifest directory as the working directory. For example, `skills/examples/echo_policy.skill.json` launches:

```json
{
  "name": "echo_policy",
  "description": "Example external subprocess skill for algorithm wrappers.",
  "runtime": "subprocess",
  "command": ["../../.venv/bin/python", "echo_policy.py"],
  "timeout_seconds": 5,
  "dry_run_only": true,
  "max_attempts": 1,
  "idempotent": true,
  "required_sensors": [],
  "failure_categories": ["invalid_input", "subprocess_error"],
  "allow_real_robot": false,
  "risk_level": "low",
  "input_schema": {
    "type": "object",
    "additionalProperties": true
  }
}
```

The wrapper script reads JSON from stdin and writes JSON to stdout. This pattern lets CUDA/RL skills point to a specific interpreter, such as `/path/to/conda/env/bin/python`, without importing those heavy dependencies into `fireclaw_core`.

Workspace skill loading is enabled by default in the CLI:

```bash
.venv/bin/python -m fireclaw_core "你有哪些技能" --skills-dir skills
```

Use `--no-workspace-skills` to run with built-in skills only.

## Direct Skill Invocation

Registered skills can be invoked directly from natural-language commands:

```bash
.venv/bin/python -m fireclaw_core "运行 echo_policy"
.venv/bin/python -m fireclaw_core "运行 echo_policy 处理 二楼"
```

Direct skill invocation still goes through the planner, safety gate, executor, and memory store. Missing skills are blocked before execution:

```bash
.venv/bin/python -m fireclaw_core "运行 missing_skill"
```

## Rescue Plans With Policy Skills

Rescue commands can include a named policy or algorithm skill. The policy skill is inserted before navigation and receives the target floor plus the original command:

```bash
.venv/bin/python -m fireclaw_core "去二楼救人 使用 echo_policy"
.venv/bin/python -m fireclaw_core "去二楼救人 导航策略用 echo_policy"
```

The named policy must be registered. If it is missing, the safety gate blocks the plan before any robot action runs.

## Robot Adapter Boundary

Built-in robot skills depend on the `RobotAdapter` protocol, not on a concrete robot implementation. Current adapters:

- `DryRunRobotAdapter`: default adapter used by the CLI and tests.
- `SimulatorRobotAdapter`: deterministic simulator that tracks current floor, reachable floors, victims by floor, sensors, online state, and battery level.
- `MockRos2RobotAdapter`: ROS2-shaped test double that records topics and payloads without importing ROS2.

Future ROS2 or real robot adapters should implement the same action methods:

- `navigate_to_floor`
- `search_for_victims`
- `assess_victim`
- `report_status`
- `return_to_safe_zone`

They should also expose state snapshots:

- `get_robot_state()`
- `get_environment_state()`

Robot state includes fields such as `robot_id`, `mode`, `dry_run`, `online`, `battery_percent`, `current_floor`, `available_sensors`, and `supports_real_execution`.

Environment state includes `reachable_floors`, `hazards`, and `victims_by_floor`.

Adapter action results are structured with fields such as `robot_id`, `mode`, `action`, `status`, `dry_run`, `data`, `timestamp`, and `error`. Planner, safety, executor, and memory should not need to change when a real adapter replaces the simulator or dry-run adapter.

Task results and memory records include `robot_state` and `environment_state` snapshots before execution. This is intentionally audit-oriented: later incident review and experiment analysis should be able to reconstruct the state the agent used for its safety decision.

Simulator demo:

```bash
.venv/bin/python -m fireclaw_core "去二楼救人" \
  --adapter simulator \
  --session-id sim-demo \
  --memory-path /tmp/fireclaw-sim-memory.jsonl
```

## Execution Monitoring

Every executed step records an attempt history. A step output includes:

- `attempt_count`
- `attempts`
- `failure_category`
- `operator_action`

Skills are not retried by default. `Skill.max_attempts` defaults to `1`, which is intentional for robotics safety. A retryable wrapper can opt in:

```python
Skill(
    name="rl_navigation_policy",
    description="Runs a retryable dry-run navigation policy.",
    handler=run_policy,
    runtime="subprocess",
    dry_run_only=True,
    max_attempts=2,
)
```

If all attempts fail, `PlanExecutor` stops the plan and marks the step with `failure_category="recoverable_exhausted"` and `operator_action="escalate"`. Future real robot adapters should connect this escalation state to operator review, emergency-stop handling, and robot state validation before any retry is allowed on physical hardware.

## Safety Metadata Enforcement

The safety gate now inspects skill metadata before execution:

- blocks missing skills;
- blocks skills whose `required_sensors` are not present in the agent's `available_sensors`;
- blocks retryable skills when `max_attempts > 1` but `idempotent=false`;
- blocks execution when the robot state reports `online=false`;
- blocks execution when the robot battery is below 10%;
- blocks rescue plans whose target floor is not in `environment_state.reachable_floors`;
- in dry-run mode, blocks skills with `dry_run_only=false`;
- in non-dry-run mode, blocks every skill unless `allow_real_robot=true` and `dry_run_only=false`;
- requires operator confirmation for non-dry-run execution after hard checks pass;
- requires operator confirmation for skills with `risk_level=high` or `risk_level=critical`.

`FireClawAgent` accepts sensor context:

```python
agent = FireClawAgent(
    workspace_skills_dir="skills",
    available_sensors={"rgb_camera", "thermal_camera"},
)
```

This does not enable real robot execution by itself. It only makes the pre-execution safety decision use the skill contract instead of trusting the planner.

## Operator Confirmation

When a plan passes hard safety checks but requires explicit operator approval, FireClaw returns `status="awaiting_confirmation"` and appends a pending record to memory. It does not execute any skill in that turn.

Example high-risk dry-run skill:

```json
{
  "name": "smoke_entry",
  "description": "High-risk smoke-entry policy.",
  "runtime": "subprocess",
  "command": ["/path/to/python", "smoke_entry.py"],
  "dry_run_only": true,
  "risk_level": "high"
}
```

Run the high-risk skill:

```bash
.venv/bin/python -m fireclaw_core "运行 smoke_entry" \
  --session-id rescue-shift-a \
  --skills-dir skills \
  --memory-path /tmp/fireclaw-demo-memory.jsonl
```

The result includes:

```json
{
  "status": "awaiting_confirmation",
  "safety": {
    "status": "require_confirmation"
  },
  "execution": null,
  "confirmation": {
    "status": "pending"
  }
}
```

Confirm or cancel in the same session:

```bash
.venv/bin/python -m fireclaw_core "确认执行" \
  --session-id rescue-shift-a \
  --skills-dir skills \
  --memory-path /tmp/fireclaw-demo-memory.jsonl

.venv/bin/python -m fireclaw_core "取消" \
  --session-id rescue-shift-a \
  --skills-dir skills \
  --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Confirmation restores the latest unresolved pending plan from JSONL memory and re-runs the safety gate with `operator_confirmed=true`. Cancellation records `status="cancelled"` and does not execute skills.
