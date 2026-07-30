# FireClaw

FireClaw 是一个面向消防机器人的 Python embodied-agent 框架，参考 OpenClaw
的 Agent、Plugin、Skill、Tool、Memory、Gateway 和本地持久化边界，并针对
真实机器人增加物理安全、状态不确定性、多机器人调度、执行证据和可审计恢复
机制。本文中 Skill 表示可协调多个 Tool 的能力说明/工作流，Tool 表示原子
可调用操作，Plugin 表示安装与生命周期单元，Runtime/Algorithm 表示实际算法
实现。完整规范见
[`docs/architecture/plugin-skill-tool-terminology.md`](docs/architecture/plugin-skill-tool-terminology.md)。

FireClaw 的核心研究对象是机器人本地 `RobotAgent`：每台机器人运行一个常驻
`FireClawGateway + FireClawAgent`，负责本机安全门控、Tool 执行、ROS/仿真
适配、事件流和任务记忆。中央指挥端的 `MissionAgent/MissionGateway` 是
`MissionCoordinator`，负责理解消防员命令、选择在线 Robot Agent 并下发
`StructuredRobotTask`，但不直接控制 ROS topic、service、action 或硬件执行器。

本文统一使用三类术语：`Mission Coordinator` 表示中央任务协调器，
`Robot Agent` 表示常驻机器人智能体，`Delegated Worker Subagent`
仅表示未来按需派生的临时认知工作智能体。Robot Agent 不是由中央动态生成的
subagent。完整规范见
[`docs/architecture/fireclaw-agent-terminology.md`](docs/architecture/fireclaw-agent-terminology.md)。

当前仓库已经不再只是最初的单机器人 dry-run demo。现有主循环覆盖：

```text
operator command
-> frozen mission-state snapshot
-> multi-source world-state belief projection
-> advisory RAG context + approved task-assumption rules
-> deterministic context assembly, budgeting, and provenance manifest
-> bounded LLM or deterministic planner
-> optional host-validated active observation and a new state snapshot
-> semantic task graph
-> deterministic graph compiler and validators
-> mission scheduler and robot-local gateways
-> completion-evidence validation
-> checkpoint / retry / reassign / LLM plan revision
-> audit memory
```

## Current Capabilities

| Area | Current behavior |
|---|---|
| Mission planning | Shared `BoundedAgentLoop` lifecycle controls drive a mission-specific multi-round planner with frozen-snapshot queries, host-validated active-observation requests, structured graph proposals, clarification, and escalation |
| Planning context | Central and robot-local LLM planners share model-aware token budgeting, provenance-preserving semantic compaction, trust-tiered context envelopes, persisted inclusion/omission manifests, and deterministic token/retention evaluation |
| Agent Harness | Every central and robot-local LLM turn crosses one `ProviderAgentHarness` boundary for context fitting, tool projection validation, cancellation, provider errors, tool-call validation, and turn diagnostics |
| Deployment Tool modes | `simulation` and `real` profiles project different non-physical Agent Tools through one host-enforced policy; simulation process access stays inside a constrained Docker workspace, while real mutations require exact backend authorization and process/host-admin/credential/hardware effects are denied |
| Robot Agent deliberation | Shared `BoundedAgentLoop` lifecycle controls drive a robot-specific one-operation-per-turn loop; every physical Tool result is returned to the next model turn |
| Plugin Host | One OpenClaw-shaped `FireClawPluginHost` owns plugin identity, contribution ownership, conflicts, atomic activation/rollback, diagnostics, and disposal; legacy registries are compatibility projections |
| Capability policy | One ordered, auditable pipeline projects planning tools and rechecks every physical action against actor identity, task delegation, plugin ownership, robot profile, live state, `SafetyGate`, and exact execution authorization |
| Physical Tool contributions | Legacy `PhysicalSkillPlugin` definitions contribute LLM Tool schemas, task-target bindings, Adapter actions, safety metadata, resources, evidence, and operator projection through the shared host without per-Tool Agent branches |
| Active observation | After inspecting an unresolved belief, the planner may request evidence; the host selects a safe capable robot, runs a typed observation task, builds `state:N+1`, and resumes planning |
| Task graph | Typed targets, dependencies, completion goals, robot capabilities, resources, timeouts, risks, and recovery policies |
| Graph compilation | The LLM describes task semantics; deterministic code allocates robots and injects completion, safety, and approved task-assumption constraints |
| Task common sense | RAG may suggest and cite dependencies, while a versioned approved rule registry supplies enforceable requirements and fills omissions for covered task types |
| Current state | Versioned immutable snapshots bind the planner, plan record, and executable graph to the same world state |
| Evidence fusion | Raw observations are preserved while planner-facing beliefs are marked `confirmed`, `uncertain`, `conflicted`, or `stale` |
| Dispatch belief gate | Semantic nodes bind inspected `belief_id` values to compiled confidence/freshness requirements and revalidate them before every initial, retried, reassigned, resumed, or revised physical dispatch |
| Execution recovery | Typed execution events can invalidate a plan and return trusted evidence to the LLM for a bounded revision |
| Revision dispatch | Completed nodes are preserved, obsolete work is fenced/cancelled, and replacement nodes are checkpointed and dispatched |
| Completion contracts | A robot's nominal `succeeded` result is accepted only when compiled evidence requirements are satisfied |
| Restart recovery | Mission dispatch checkpoints persist node state and recovery intent; restart recovery does not blindly replay completed physical actions |
| Safety boundary | Planner code cannot directly invoke robot Tools or algorithms; compiled plans still pass deterministic validation and robot-local safety gates |
| Auditability | Planner turns, observations, validation errors, graph revisions, execution evidence, checkpoints, and outcomes are persisted |

### Current Spatial Scope

当前 FireClaw 运行范围是**单楼层二维地图**：

- 机器人只在当前 ROS map/frame 内活动；
- 当前导航能力是 `navigate_to_point(x, y, yaw=0.0, frame_id="map")`；
- 中央任务目标优先使用 `MissionTarget.pose`、`area_id` 或 `entity_id`；
- 当前 Planner、Robot Agent profile 和 ROS1 示例不生成跨楼层动作；
- `navigate_to_floor`、`target.floor`、`current_floor` 和
  `reachable_floors` 仅作为历史数据与未来多楼层扩展的兼容接口保留。

兼容字段存在不代表当前系统已经支持电梯、楼梯、跨层定位、地图切换或
跨楼层路径规划。启用这些能力前需要单独设计状态机、安全规则和实机验证。
正式契约见
[`docs/architecture/fireclaw-spatial-scope.md`](docs/architecture/fireclaw-spatial-scope.md)。

## Deployment Tool Modes

两种模式中的 LLM 都不会直接取得 Python、shell、ROS 或机器人进程权限。模型只
返回结构化 Tool Call，可信宿主再执行工具投影、参数校验、`before_tool_call`
hook、审批、安全门、沙箱执行和审计。

- `simulation`：主控和 Robot Agent 可获得 `computer_list_files`、
  `computer_read_file`、`computer_write_file`、`computer_exec`。文件访问限制在
  角色专属 workspace；进程只在 Docker 内运行，默认无网络、无特权、无宿主
  shell 回退。每次执行使用命名容器，超时、取消和异常都会强制清理；输出在
  读取过程中限制字节数，镜像标签必须绑定不可变 Docker image ID。
- `real`：进程、宿主管理、凭据访问和直接硬件类 Agent Tool 不暴露；受限写入
  需要对最终工具名和参数哈希的精确授权。物理动作不进入通用 Agent Tool
  runtime，仍通过 Physical Skill、capability policy、`SafetyGate` 和执行授权。
- 通用计算机工具的结果只能作为 advisory 上下文，不能伪装成传感器事实或覆盖
  当前状态快照。
- 使用 `ros1` adapter 的 Robot Agent 还可获得 `ros_topic_list`、
  `ros_topic_info`、`ros_topic_sample`、`ros_topic_rate`、`tf_lookup`、
  `move_base_status` 和 `navigation_diagnostics`。这些工具只读、限时、限样本、
  限输出字节，并受 topic/frame/action 白名单约束；Mission Agent 不直接获得
  这些本地 ROS 工具。

配置、威胁边界和调用流程见
[`docs/architecture/deployment-tool-policy.md`](docs/architecture/deployment-tool-policy.md)
和
[`docs/architecture/docker-sandbox-lifecycle-security.md`](docs/architecture/docker-sandbox-lifecycle-security.md)
、
[`docs/architecture/ros-diagnostic-tools.md`](docs/architecture/ros-diagnostic-tools.md)。

The framework remains research and integration software, not a certified
firefighting control system. Real ROS1 transport exists as a configuration-driven
adapter boundary, but every robot, sensor, emergency-stop path, confidence policy,
and physical Tool/Runtime still requires site-specific validation.

Known planning gaps:

- the task-assumption registry currently covers only the first
  `navigation + area_id` rule; broad firefighting task coverage still requires
  a reviewed domain ontology and a candidate-to-approval workflow;
- belief confidence, freshness, and source-reliability defaults still require
  calibration from simulator and robot logs;
- active observation currently supports a bounded synchronous request/result
  loop; general push-based sensor streams and durable continuation across a
  coordinator restart are not yet implemented;
- multi-source evidence has structural provenance but not cryptographic
  attestation or learned correlation handling;
- exact token accounting requires a locally available Hugging Face tokenizer;
  models without one use the conservative CJK-aware fallback and record
  `exact_model_tokenizer: false`;
- structured semantic compaction preserves selected task facts and source
  references, but it is not yet an adaptive learned or LLM-generated summary;
- fallback model chains do not yet negotiate a common minimum context window
  across every candidate before the first provider attempt.

## OpenClaw Reference

The local upstream reference is stored in `openclaw/`. It is architectural
source material, not a FireClaw runtime dependency and not code to copy
wholesale. Before changing an OpenClaw-analogous FireClaw module, inspect the
relevant source under `openclaw/` and follow its scoped `AGENTS.md` files.

## Quick Demo

The smallest supported point-target rescue command is:

```text
去坐标 (2.0, 1.5) 救人
```

It produces a five-Tool dry-run rescue plan:

1. `navigate_to_point`
2. `search_for_victims`
3. `assess_victim`
4. `report_status`
5. `return_to_safe_zone`

## Environment

Use Python 3.10 or newer in a project-local virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

## Run Tests

```bash
.venv/bin/python -m pytest -v
```

## Run Doctor

Use the local doctor before moving from mock adapters toward real ROS1 integration:

```bash
.venv/bin/python -m fireclaw_core.devtools.doctor \
  --adapter mock-ros1 \
  --robot-id doctor-demo \
  --memory-path /tmp/fireclaw-doctor-memory.jsonl \
  --event-path /tmp/fireclaw-doctor-events.jsonl
```

Doctor prints a JSON report with `pass`, `warn`, or `fail` checks for adapter mode, writable memory/event paths, workspace skill manifests, emergency-stop hook availability, and the action feedback boundary. `mock-ros1`, `dry-run`, and `simulator` intentionally report `warn` because they do not control live hardware.

For the real ROS1 adapter skeleton, provide a JSON config:

```json
{
  "robot_id": "fireclaw-01",
  "namespace": "/fireclaw/fireclaw-01",
  "endpoints": {
    "navigate_to_point": {
      "interface": "action",
      "name": "/move_base",
      "type": "move_base_msgs/MoveBaseAction",
      "cancel_supported": true,
      "feedback_supported": true
    }
  },
  "emergency_stop": {
    "interface": "service",
    "name": "/fireclaw/fireclaw-01/emergency_stop",
    "type": "std_srvs/Trigger"
  },
  "diagnostics": {
    "enabled": true,
    "topic_allowlist": [
      "/scan",
      "/odom",
      "/tf",
      "/tf_static",
      "/cmd_vel",
      "/move_base",
      "/move_base/*"
    ],
    "frame_allowlist": ["map", "odom", "base_link", "base_footprint"],
    "action_allowlist": ["/move_base"],
    "max_topics": 100,
    "max_samples": 3,
    "max_timeout_seconds": 3.0,
    "max_output_bytes": 32768
  }
}
```

Then run doctor against it:

```bash
.venv/bin/python -m fireclaw_core.devtools.doctor \
  --adapter ros1 \
  --ros1-config /path/to/ros1-adapter.json \
  --memory-path /tmp/fireclaw-doctor-memory.jsonl \
  --event-path /tmp/fireclaw-doctor-events.jsonl
```

The `ros1` adapter loads robot-specific endpoint config without importing ROS at startup. By default, transport is disabled and actions return `not_configured`. Set `transport.enabled: true` only in a reviewed robot-specific config to make the adapter import `rospy`/`actionlib` and call ROS1 topic, service, or action endpoints.

ROS diagnostics are independent of physical transport enablement. When the
Robot Agent loop is enabled, the robot-local trusted backend may execute only
the fixed read commands behind the seven diagnostic Tool contracts. The
Gateway process must run in a sourced ROS1 environment that can reach the
intended ROS master. Disabling `diagnostics.enabled` removes all seven tools
from the Robot Agent projection.

The same config can be written as YAML using `remap`. This is the preferred shape for robot teams because it exposes the action-to-ROS1 binding directly:

```yaml
robot_id: fireclaw-01
namespace: /fireclaw/fireclaw-01

transport:
  enabled: true
  wait_for_server_seconds: 5.0
  wait_for_result_seconds: 30.0

remap:
  navigate_to_point:
    profile: move_base
    name: /move_base
    goal_template:
      target_pose:
        header:
          frame_id: "${frame_id}"
        pose:
          position:
            x: "${x}"
            y: "${y}"
            z: 0.0
          orientation: "${orientation}"

  emergency_stop:
    profile: trigger_service
    name: /fireclaw/emergency_stop

  spray_water:
    interface: service
    name: /fireclaw/fireclaw-01/spray_water
    type: fireclaw_msgs/SprayWater
    request_template:
      target_id: "{{ target_id }}"
      duration_seconds: "{{ duration_seconds }}"

```

Supported profiles are:

- `move_base`: expands to `move_base_msgs/MoveBaseAction`.
- `trigger_service`: expands to `std_srvs/Trigger`.
- `string_topic`: expands to `std_msgs/String`.

Any remap key may be a built-in robot action or a future workspace skill name. Doctor reports workspace skills that have no ROS1 remap and custom remap entries that do not match any loaded skill manifest.

When transport is enabled, FireClaw renders `goal_template` and
`request_template` using skill inputs and optional named `targets`. The current
`navigate_to_point` example binds `x`, `y`, `frame_id`, and an adapter-derived
quaternion directly into a `move_base` goal. The test suite validates this
rendering and ROS1 topic/service/action transport with fake ROS modules; a real
robot still requires validation in a ROS1 runtime.

## Run the Dry-Run Agent

```bash
.venv/bin/python -m fireclaw_core "去坐标 (2.0, 1.5) 救人" --memory-path /tmp/fireclaw-demo-memory.jsonl
```

The command prints a structured JSON result and appends the same run record to the JSONL memory file.

### Standalone Mission Gateway

Start the mission coordinator:

```bash
.venv/bin/python -m fireclaw_core serve --config fireclaw.toml
```

The `[mission]` section in `fireclaw.toml` lists robot profiles; the registry is built automatically from those profiles — no manual `robots.json` needed.

Open the operator console:

```bash
fireclaw mission --server http://127.0.0.1:8766
```

For ROS1/Gazebo work, keep the robot-local `FireClawGateway` responsible for the adapter. The mission coordinator dispatches to registered robot gateways; it does not directly publish ROS topics or actions.

## Run the End-to-End Framework Demo

Use `--demo rescue` to run one local rescue task through the Gateway control plane and the mock ROS1 adapter:

```bash
.venv/bin/python -m fireclaw_core \
  --demo rescue \
  --robot-id demo-ros1 \
  --session-id demo-shift-a \
  --memory-path /tmp/fireclaw-e2e-demo-memory.jsonl \
  --event-path /tmp/fireclaw-e2e-demo-events.jsonl
```

The output is a compact JSON trace summary. It includes the operator identity, control decision, task/action projected state, action lifecycle events, and `mock_ros1` robot state. This is a framework-level demo only: it does not import `rospy`, connect to a ROS master, or control real hardware.

Runtime context can be configured from the CLI:

```bash
.venv/bin/python -m fireclaw_core "去坐标 (2.0, 1.5) 救人" \
  --adapter simulator \
  --robot-id fire-robot-01 \
  --session-id rescue-shift-a \
  --memory-path /tmp/fireclaw-demo-memory.jsonl

.venv/bin/python -m fireclaw_core "运行 thermal_policy" \
  --skills-dir skills \
  --available-sensor thermal_camera \
  --memory-path /tmp/fireclaw-demo-memory.jsonl
```

`--available-sensor` can be repeated. The values are passed into the safety
gate and checked against each legacy executable Tool definition's
`required_sensors`.

`--adapter` selects the robot adapter used by built-in robot Tools:

- `dry-run`: default dependency-free dry-run adapter.
- `simulator`: deterministic single-floor simulator with pose, victim, sensor, and environment state.
- `mock-ros1`: ROS1-shaped test double that records command specs without importing `rospy`.
- `mock-ros2`: legacy alias that currently routes to the mock ROS1 adapter.
- `ros1`: real ROS1 adapter loaded from `--ros1-config`; transport is disabled unless the config sets `transport.enabled: true`.

For safety-gate experiments, `--real-run` sets `dry_run=false`:

```bash
.venv/bin/python -m fireclaw_core "去坐标 (2.0, 1.5) 救人" --real-run
```

This does not create a real robot adapter. It only makes the safety gate evaluate real-robot eligibility. Default built-in dry-run skills are blocked in this mode.

## Run the Operator Console

The raw agent and Gateway interfaces keep JSON for programs, logs, ROS nodes, and future frontend clients.
For human operators, use the operator console. It projects structured task events into Chinese progress text:

```bash
.venv/bin/python -m fireclaw_core.infra.operator_console "去坐标 (2.0, 1.5) 救人" \
  --adapter simulator \
  --robot-id robot-01 \
  --session-id operator-a \
  --memory-path /tmp/fireclaw-operator-memory.jsonl \
  --event-path /tmp/fireclaw-operator-events.jsonl \
  --no-workspace-skills
```

Example output:

```text
已接收任务：去坐标 (2.0, 1.5) 救人。
正在规划救援任务。
安全检查通过。
正在前往 map 坐标系中的目标点 (2.0, 1.5)。
正在搜索当前区域被困人员。
正在评估被困人员状态。
正在向操作员报告现场状态。
正在返回安全区域。
任务完成：FireClaw dry-run rescue plan completed.
```

This mirrors OpenClaw's split between structured Gateway events and human-facing TUI/channel projection.

## Run the Gateway

FireClaw Gateway is a local HTTP control plane around the same agent core. It is intended for a robot-side resident process:

```text
HTTP in
-> FireClawAgent
-> robot-local planner and safety gate
-> SkillExecutor
-> RobotAdapter
-> dry-run / simulator / mock ROS1 / configured ROS1 transport
```

### Profile-Driven Startup

The recommended workflow uses `fireclaw.toml` with a profile path. Identity, adapter, ROS config, and storage paths are derived from the profile automatically:

```bash
# 1. 编辑配置
cp fireclaw.example.toml fireclaw.toml

# 2. 启动 robot-local gateway（safe default: profile-backed dry run, no ROS transport execution）
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml

# 3. 启动 mission gateway（从 profile 自动构建 robot registry）
.venv/bin/python -m fireclaw_core serve --config fireclaw.toml
```

Both long-running Gateways use a fixed canonical runtime root rather than the
directory from which the shell happened to launch them. Configure
`[runtime].root_dir`, pass `--runtime-root`, or set an absolute
`FIRECLAW_HOME`; otherwise a config-backed process uses the config directory
and a config-less process uses `~/.fireclaw`.

Mission and Robot computer Tools receive separate dedicated writable
workspaces. Repository roots, source/plugin/Skill directories, Home credential
directories, system paths, symlink escapes, and paths outside the role's
allowed workspace root are rejected before Docker receives a bind mount. See
[`runtime-workspace-path-security.md`](docs/architecture/runtime-workspace-path-security.md).

In profile-driven mode, the profile chooses the adapter and ROS config; `dry_run` chooses whether the adapter may send transport commands. Robot profiles may include sensor discovery rules, but runtime sensor availability comes from verified adapter state. In ROS1/Gazebo, a topic must exist, match a sensor rule, and publish a recent message before the corresponding sensor enters `RobotState.available_sensors`. Use `--real-run` to override the dry-run default when ROS/Gazebo or robot hardware is ready:

```bash
# Explicit ROS/Gazebo transport execution
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml --real-run
```

For one-off runs, CLI flags still override the config file:

```bash
.venv/bin/python -m fireclaw_core robot-gateway \
  --config fireclaw.toml \
  --adapter simulator \
  --robot-id robot-01 \
  --runtime-state-path data/robot-01/runtime.sqlite3
```

For legacy workflows that need an explicit `robots.json` file, the `robot-profile export` command remains available:

```bash
.venv/bin/python -m fireclaw_core robot-profile export \
  --profile examples/robot_profiles/gazebo_turtlebot3.toml \
  --output data/mission/robots.json
```

Check health and state:

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/state
```

`/state` includes robot state, environment state, task capacity, active task summaries, and a durable task queue summary. By default a Gateway allows only one active execution task for the robot:

```json
{
  "task_capacity": {
    "active_execution_tasks": 1,
    "max_active_execution_tasks": 1,
    "available_execution_slots": 0
  },
  "active_tasks": [
    {
      "task_id": "task-...",
      "session_id": "operator-a",
      "command": "去坐标 (2.0, 1.5) 救人",
      "started_at": "2026-06-02T...",
      "cancel_requested": false
    }
  ],
  "task_queue": {
    "task_count": 3,
    "active_task_count": 1,
    "terminal_task_count": 2,
    "active_tasks": []
  }
}
```

Submit a natural-language task:

```bash
curl -X POST http://127.0.0.1:8765/tasks \
  -H "Content-Type: application/json" \
  -d '{"command": "去坐标 (2.0, 1.5) 救人", "session_id": "operator-a"}'
```

Task submissions may include a `dedupe_key` for network retry safety. If a non-terminal task with the same key already exists, Gateway returns the existing `task_id` instead of starting another robot action:

```bash
curl -X POST http://127.0.0.1:8765/tasks \
  -H "Content-Type: application/json" \
  -d '{"command": "去坐标 (2.0, 1.5) 救人", "session_id": "operator-a", "dedupe_key": "operator-a-20260608-001"}'
```

```json
{
  "status": "duplicate",
  "task_id": "task-...",
  "session_id": "operator-a",
  "dedupe_key": "operator-a-20260608-001",
  "message": "任务已存在，返回现有未完成任务。"
}
```

Task submissions can also include an operator context. FireClaw records this in the task trace before execution starts:

```bash
curl -X POST http://127.0.0.1:8765/tasks \
  -H "Content-Type: application/json" \
  -d '{"command": "去坐标 (2.0, 1.5) 救人", "session_id": "operator-a", "operator": {"operator_id": "op-1", "role": "operator"}}'
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

If the robot already has the maximum number of active execution tasks, `POST /tasks` returns HTTP `409 Conflict`:

```json
{
  "status": "busy",
  "message": "机器人当前已有任务在执行，请等待当前任务结束或取消后再提交。",
  "active_task_id": "task-...",
  "active_tasks": [],
  "capacity": {
    "active_execution_tasks": 1,
    "max_active_execution_tasks": 1,
    "available_execution_slots": 0
  }
}
```

This backpressure is intentional. A single robot should not silently accept multiple concurrent execution tasks that might command navigation, search, manipulation, or future ROS1-backed robot actions at the same time.

Use the `task_id` to inspect the current task trace, projected state, durable queue record, final result, and event stream. While the background task is still running, `result` is `null`; after completion, the final agent result is available under `result`. The trace also includes `state`, a deterministic projection with `task`, `skills`, and `actions` summaries derived from the append-only events:

```bash
curl http://127.0.0.1:8765/tasks/task-REPLACE_WITH_ID
curl http://127.0.0.1:8765/tasks/task-REPLACE_WITH_ID/events
```

Gateway v1 records append-only JSONL events such as:

- `task.received`
- `operator.identified`
- `control.decision`
- `task.planned`
- `safety.decided`
- `confirmation.pending`
- `confirmation.confirmed`
- `skill.started`
- `action.requested`
- `action.started`
- `action.feedback`
- `action.cancel_requested`
- `action.succeeded`
- `action.failed`
- `action.cancelled`
- `skill.attempted`
- `skill.succeeded`
- `skill.failed`
- `task.cancel_requested`
- `task.lost`
- `task.completed`
- `task.cancelled`
- `emergency_stop.requested`
- `emergency_stop.activated`
- `emergency_stop.denied`

Confirm or cancel the latest pending confirmation in a session:

```bash
curl -X POST http://127.0.0.1:8765/confirm \
  -H "Content-Type: application/json" \
  -d '{"session_id": "operator-a"}'

curl -X POST http://127.0.0.1:8765/cancel \
  -H "Content-Type: application/json" \
  -d '{"session_id": "operator-a"}'
```

Cancel an active background task by task id:

```bash
curl -X POST http://127.0.0.1:8765/tasks/task-REPLACE_WITH_ID/cancel
```

Trigger a Gateway-level emergency stop with an operator that has the `emergency.stop` scope:

```bash
curl -X POST http://127.0.0.1:8765/emergency-stop \
  -H "Content-Type: application/json" \
  -d '{"session_id": "operator-a", "reason": "unsafe heat condition", "operator": {"operator_id": "admin-1", "role": "admin"}}'
```

Emergency stop is stronger than normal task cancellation. It closes persistent resource admission before requesting cancellation, records dedicated emergency-stop audit events, and calls the robot adapter's `emergency_stop(...)` hook. The closed admission state survives Gateway restart. Mock adapters only update local state; a real ROS1 profile must explicitly map the hook to the reviewed robot emergency-stop topic, service, action, or SDK call.

Cancellation is cooperative at the task/executor boundary. FireClaw records
`task.cancel_requested` immediately and stops before starting the next Tool.
Legacy executable manifests run only in the bounded Docker process sandbox;
their timeout is enforced by that boundary and cancellation prevents further
Tool dispatch. In-process Tools still return cooperatively, and real ROS1
profiles must map cancellation to the robot action interface where available.

The robot Gateway uses one SQLite WAL database as the authoritative runtime
store for task transitions, events, loop checkpoints, approval requests,
execution authorizations, authorization use, emergency flags, and resource
leases. Configure it with `--runtime-state-path`. When only a custom
`--memory-path` is supplied, implicit event, task, and runtime paths are placed
in the same storage namespace. `--task-queue-path` and `--event-path` remain
append-only, fsync-backed JSONL audit/export mirrors; runtime decisions never
read them after migration.

Task updates use revisions and reject stale or terminal overwrites. Related
task and event mutations commit in one SQLite transaction. On startup, a
previous non-terminal physical operation is reconciled or marked `lost`;
FireClaw never assumes that a database rollback can undo robot motion.

This rule applies to the robot-local physical-action queue. The central mission
scheduler has a separate revision-aware checkpoint recovery path: it restores
the mission graph and node intent, reconciles robot task traces, and continues
only work that is still pending. It does not treat an old in-flight action as
permission to execute that action a second time.

The Python API still exposes synchronous `FireClawGateway.run_agent(...)` for local test harnesses and in-process tooling. External systems should prefer the asynchronous HTTP endpoints or `FireClawGateway.submit_agent(...)`.

Inspect skills, recent memory, and recent events:

```bash
curl http://127.0.0.1:8765/skills
curl 'http://127.0.0.1:8765/memory/recent?session_id=operator-a&limit=5'
curl 'http://127.0.0.1:8765/events/recent?session_id=operator-a&limit=20'
```

Gateway binds to localhost by default. Tokenless access is accepted only from
the loopback interface and becomes the fixed server-owned
`local-loopback-operator`; any non-loopback bind fails before listening unless
a bearer token is configured. A valid shared token becomes the fixed
`gateway-shared-token` principal. `X-Operator-Id`, `X-Operator-Scopes`, and
request-body `operator` fields are never authorization inputs.

Remote listeners additionally require TLS; remote clients reject plaintext
HTTP before sending credentials. HTTPS validates the CA chain and hostname,
and both Gateways support optional mTLS. TLS initialization failures stop
startup instead of falling back to HTTP.

Prefer `FIRECLAW_GATEWAY_TOKEN` for the Mission Gateway and
`FIRECLAW_ROBOT_GATEWAY_TOKEN` for Mission-to-Robot Gateway calls. Even on
loopback, configure a token for real-robot deployments.

Both Gateways apply the same bounded network-admission pipeline before
business dispatch: strict Host/Origin validation, request-header and body-read
timeouts, a pre-thread total/per-IP connection budget, failed-authentication
rate limiting, and an early 1 MiB request-body limit. SSE streams have separate
total/per-IP connection budgets, bounded subscriber queues, and write
timeouts; a slow subscriber is disconnected instead of buffering without
limit. Configure these values in `[network]`. Authenticated `/state` and
`/fleet/doctor` responses expose the current admission-policy snapshot and SSE
usage.

Do not expose either Gateway directly on a public network. Process-local
limits reset after restart and see the reverse proxy as the client unless that
proxy enforces its own trusted-client policy, so production still requires
firewalling, proxy-level connection/rate limits, and secret management. See
[`gateway-authentication.md`](docs/architecture/gateway-authentication.md) and
[`gateway-secure-transport.md`](docs/architecture/gateway-secure-transport.md),
and
[`gateway-network-abuse-controls.md`](docs/architecture/gateway-network-abuse-controls.md).

## Mission Coordinator / Robot Agent Contract

FireClaw is a hierarchical embodied-agent architecture rather than a pure
central robot controller. The Mission Coordinator selects registered,
long-running Robot Agents and calls their local FireClaw Gateways:

```text
FireClaw Mission Coordinator
  -> Robot Agent A: https://robot-a:8765
  -> Robot Agent B: http://robot-b:8765
```

The Mission Coordinator can submit subtasks, read state, read task traces, and
request cancellation through the robot-local Gateway API. It does not bypass
the Robot Agent to publish low-level ROS topics or motor commands. Robot Agents
are independently deployed embodied agents; they are not ephemeral OpenClaw
subagents spawned by the coordinator.

Some existing Python identifiers, including `RobotSubagentClient` and
`JsonlSubagentRegistry`, retain their legacy names for compatibility. They
transport tasks to and track runs on persistent Robot Agents; new prose should
not use those identifiers as the architectural role name.

A robot registry is built automatically from profiles listed in `[mission].robot_profiles` in `fireclaw.toml`. Each profile contributes a registry entry with `robot_id`, `base_url`, `capabilities`, and other metadata. No manual `robots.json` is needed for the profile-driven workflow.

Python callers can use the v1 contract directly:

```python
from fireclaw_core.agent.robot_registry import load_robot_registry
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_registry import JsonlMissionRegistry

registry = load_robot_registry("robots.json")
mission = MissionAgent(
    registry=registry,
    mission_registry=JsonlMissionRegistry("memory/fireclaw-missions.jsonl"),
)
result = mission.submit_subtask(
    "robot-1",
    "去坐标 (2.0, 1.5) 搜索受困人员",
    session_id="mission-001",
    dedupe_key="mission-001-robot-1-floor-2",
)
```

The mission registry is append-only JSONL. It records the mission, assigned robot subtasks, subtask task ids, and projected mission status. The Mission Coordinator can aggregate robot-local task traces into a mission trace:

```python
trace = mission.mission_trace("mission-001")
```

The aggregated trace keeps robot-local traces under each subtask. Robot-local Gateway traces remain the source of truth for embodied execution and incident review.

The same v1 contract is available from the CLI. With the profile-driven workflow, the registry is built automatically from `fireclaw.toml` profiles. For legacy workflows, `robot-profile export` remains available:

```bash
# Legacy: export profile to robots.json
.venv/bin/python -m fireclaw_core robot-profile export \
  --profile examples/robot_profiles/gazebo_turtlebot3.toml \
  --output data/mission/robots.json

# Mission CLI commands (use --robot-registry with exported JSON)
.venv/bin/python -m fireclaw_core.mission.mission_cli trace mission-001 \
  --robot-registry robots.json \
  --mission-registry memory/fireclaw-missions.jsonl

.venv/bin/python -m fireclaw_core.mission.mission_cli cancel mission-001 \
  --robot-registry robots.json \
  --mission-registry memory/fireclaw-missions.jsonl
```

### Mission Planning and Plan Revision

FireClaw supports two planner policies:

- `MissionPlanner`: deterministic compatibility planner for simple
  multi-floor commands;
- `LLMMissionPlanner`: bounded tool-calling planner for semantic task graphs,
  snapshot inspection, clarification, escalation, and evidence-grounded plan
  revision.

The LLM does not directly select and execute arbitrary robot tools. The current
planning boundary is:

```text
LLM MissionGraphProposal
-> MissionGraphCompiler
-> MissionTaskGraphValidator
-> MissionScheduler
-> robot-local StructuredRobotTask
```

`MissionGraphProposal` describes task type, target, capability, dependencies,
execution mode, completion goal, and any inspected world beliefs on which the
node depends. `MissionGraphCompiler` owns robot allocation and injects
completion contracts, robot preconditions, belief requirements, exclusive
resources, timeouts, risk, and recovery policy. The LLM cannot lower the
compiled `confirmed` status, `0.8` confidence, or 15-second freshness
requirements.

Task common sense uses a hybrid trust boundary. Retrieved external knowledge
is advisory: it may help the LLM identify an assumption and its
`knowledge_refs`, but it cannot authorize a physical action or claim that a
condition is currently true. `TaskAssumptionRegistry` contains reviewed,
versioned rules that the deterministic compiler can enforce. For example, a
`navigation` node targeting `area_id: west-stair` automatically requires both
`passage_open: true` and `structural_stable: true`, even when the LLM omits
them. The compiler rejects the node if either belief is missing, unconfirmed,
too old, weak, or conflicts with the planner/RAG suggestion. The dispatch gate
then checks the same requirements again against a fresh snapshot immediately
before robot execution.

Before planning, `MissionStateSnapshotBuilder` captures robot presence,
battery, floor, sensors, emergency-stop state, task capacity, task lifecycle,
environment observations, and resource reservations. The planner can perform
bounded read-only queries against this frozen snapshot. It cannot query a live
robot or execute a skill during mission deliberation.

After the planner has inspected an `uncertain`, `conflicted`, or `stale`
belief, the runtime may expose `request_observation`. This is a proposal, not a
sensor tool call. `MissionObservationCompiler` verifies the belief and target,
restricts the request to approved observation capabilities, and
deterministically selects an online, non-stale, emergency-stop-free robot with
the required capability, sensor, reachability, and task capacity. The task
then travels through the existing `StructuredRobotTask` and robot-local safety
boundary.

Only a successful task trace containing a schema-valid structured observation
can become an environment fact. Free text is rejected. The host assigns the
fact source to the executing robot, recomputes beliefs, persists
`state:N+1`, and starts a new bounded deliberation against that snapshot.
FireClaw permits at most two active-observation rounds per initial planning
request by default.

Before every planner decision, `MissionPlanningContextAssembler` builds the
exact dynamic context envelope for that turn. The envelope separates:

- authoritative mission state, inspected observations, validator feedback, and
  invalidation evidence;
- plan continuity, including the previous rejected proposal and superseded
  plan;
- advisory operator corrections, retrieved mission memories, and external RAG
  knowledge.

Authoritative content is never silently truncated. If it does not fit the
configured planning budget, the runtime blocks before calling the policy.
Advisory items are admitted whole in priority order, deduplicated, and limited
by per-section and total budgets. Every turn receives a stable `context_id`;
the persisted manifest records sources, trust levels, included and omitted
references, omission reasons, and budget usage.

The host retains the complete snapshot for deterministic compilation and
validation. The LLM tool schema receives a separate
`tool_exposed_belief_ids` allowlist, so a graph proposal can cite only beliefs
that the planner actually inspected during that deliberation.

Raw `environment_facts` remain available for audit. Planner-facing
`environment_beliefs` fuse observations by `(subject_id, kind)` and expose
source provenance, confidence, freshness, conflicts, and superseded reports.
A planner proposal is rejected until it has inspected any unresolved
`uncertain`, `conflicted`, or `stale` belief.

Immediately before a physical node is sent to a robot,
`MissionBeliefGate` captures a new state snapshot and revalidates every
compiled belief requirement. Missing, changed, uncertain, conflicted, stale,
low-confidence, or over-age beliefs block dispatch and emit a typed
`belief_requirement_failed` event. Gate results are stored in the node
checkpoint. The same gate covers initial dispatch, retry, reassign, revised
graphs, and restart recovery.

During execution, authoritative events such as route blockage, robot loss, or
completion-evidence rejection can invalidate the active graph. FireClaw builds
a new snapshot, returns the typed evidence to the planner, validates the
replacement graph, preserves completed nodes, fences obsolete work, persists a
new checkpoint, and continues the revised plan.

```python
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_planner import MissionPlanner
from fireclaw_core.mission.mission_registry import JsonlMissionRegistry

mission = MissionAgent(
    registry=robot_registry,
    mission_registry=JsonlMissionRegistry("memory/fireclaw-missions.jsonl"),
    planner=MissionPlanner(),
)

result = mission.plan_and_submit(
    "去坐标 (2.0, 1.5) 搜索受困人员",
    session_id="mission-002",
)
```

The deterministic compatibility planner detects intent (`search`, `patrol`,
`firefight`, `recon`, or `transport`), extracts current-map target points,
matches enabled robots by capability, and emits parallel/sequential execution
groups. It can still read legacy floor commands for compatibility. New research
and embodied-agent work should use semantic graph proposals rather than
extending that rule table.

CLI usage:

```bash
.venv/bin/python -m fireclaw_core.mission.mission_cli plan-mission \
  --command "去坐标 (2.0, 1.5) 搜索受困人员" \
  --robot-registry robots.json \
  --mission-registry memory/fireclaw-missions.jsonl
```

Each Robot Agent remains authoritative over local embodied execution. It may block, reject, cancel, ask for confirmation, or emergency-stop based on local state, safety rules, permissions, ROS availability, and hardware constraints.

### Shared Bounded Agent Loop

FireClaw does not make the Robot Agent inherit `MissionAgent`. The two roles
have different state, tools, authority, and deployment lifecycles.
`BoundedAgentLoop` is the shared runtime foundation for iteration and timeout
limits, cancellation, one typed decision per turn, a required observation for
continuation, terminal states, and an auditable attempt trace. Both
`MissionDeliberationRuntime` and `RobotAgentDeliberationRuntime` now execute
through this lifecycle implementation. Their adapters remain different:
Mission owns immutable snapshots, belief inspection, active-observation
requests, graph compilation, and plan validation; Robot owns local context,
task-envelope enforcement, `SafetyGate`, and physical Tool execution.

`BoundedAgentLoop` and `ProviderAgentHarness` are separate shared layers.
The loop owns iteration, timeout, checkpoint, reconciliation, and terminal
state. The Harness owns one model/tool turn: tokenizer-aware context fitting,
tool schema normalization, provider invocation, cancellation checks, tool-call
count/name/argument validation, and error classification. Mission and Robot
roles build different prompts and parse different domain decisions, but
neither role calls `chat_completion()` directly.

### Unified Plugin Host

`FireClawPluginHost` follows OpenClaw's injected plugin API and registration
transaction shape. A plugin contributes tools, physical capabilities, hooks,
services, context engines, or Agent Harnesses through a plugin-scoped API.
The host records `owner_plugin_id`, rejects cross-plugin contribution
conflicts, commits all contributions atomically, rolls back failed activation,
and removes only owner-scoped state on disposal.

`PluginRuntime`, `PhysicalSkillCatalog`, `SkillRegistry`, and workspace skill
loading remain available as compatibility APIs, but their registered values
are projected from the same host when a shared host is supplied. Detailed
design and OpenClaw analogues are recorded in
[`docs/architecture/plugin-host-agent-harness.md`](docs/architecture/plugin-host-agent-harness.md).

### Capability Policy Pipeline

Installing a Tool contribution does not automatically make it visible to an LLM or
executable on a robot. FireClaw applies one ordered capability policy pipeline
at two boundaries:

```text
planning projection
  actor identity and scopes
  -> delegated task contract
  -> active Plugin Host ownership
  -> Robot Capability Profile
  -> current robot state
  -> tools exposed to the Robot Agent LLM

physical execution admission
  the same checks with fresh robot state
  -> SafetyGate decision
  -> exact, unexpired execution authorization
  -> resource lease acquisition
  -> Tool side effect
```

The planning projection prevents the model from selecting unavailable or
unauthorized Tools. The execution check is intentionally repeated because
the robot may go offline, enter emergency stop, lose a required sensor, or
receive a different authorization after planning. High-risk authorization is
bound to the exact Tool input hash; approving one target or parameter set
does not approve another.

Every stage records its status, reason, and relevant evidence. The planning
context contains the projection manifest, while execution emits
`capability.policy_preflight` and `capability.policy_decided` audit events.
`SafetyGate` remains the authority for physical safety, and the executor still
acquires resource leases immediately before side effects. Detailed design and
the OpenClaw analogue are documented in
[`docs/architecture/capability-policy-pipeline.md`](docs/architecture/capability-policy-pipeline.md).

In robot-local LLM mode, `FireClawGateway` uses
`RobotAgentDeliberationRuntime`:

```text
StructuredRobotTask
-> refresh robot and environment state
-> LLM proposes exactly one operation
-> task-envelope policy validation
-> SafetyGate validation for a physical Tool
-> execute one Tool
-> return the structured result to the next LLM turn
-> complete / blocked / escalated / cancelled / timed out
```

The default robot-local bounds are eight model turns, six physical Tool
executions, two advisory context queries, and 30 seconds. A `complete`
decision is rejected until every `required_skill` has succeeded. A local
`SafetyGate` block terminates the loop; the LLM cannot try another action to
bypass it. Recoverable skill failures remain local observations, and only a
terminal unrecovered failure is projected into a mission invalidation event.

The shared loop also has an append-only durable checkpoint protocol. Mission
planning checkpoints preserve the frozen-snapshot identity, completed reads,
validation feedback, attempt trace, and next model turn. Robot Agent
checkpoints additionally persist a stable `operation_id` before every physical
Tool dispatch. The Gateway writes legacy event names
`robot_agent.skill_dispatch_started` and
`robot_agent.skill_dispatch_finished` with that same ID.

After a process restart:

```text
finished evidence exists -> absorb the recorded result and continue
no started evidence       -> record "not started" and deliberate again
started without finished  -> escalate as outcome unknown
```

The third case is never replayed automatically. The unresolved pending
operation remains recoverable and auditable until the operator or runtime can
provide conclusive evidence. Gateway startup schedules non-terminal structured
tasks only when their Robot Agent loop checkpoint and persisted task contract
match; stale tasks without a valid checkpoint retain the existing `lost`
behavior.

`LLMRobotAgentPlanner` and `RobotAgentRuntime.plan_structured_task` remain as
static-plan compatibility APIs. The Gateway's `llm` Robot Agent mode uses the
deliberation runtime instead.

### Model Provider Runtime v1

FireClaw supports LLM-driven mission planning through an OpenAI-compatible provider abstraction.

**Supported providers:** Any OpenAI-compatible API (DeepSeek, Qwen, GLM, Moonshot, Ollama, etc.)

**CLI usage:**

```bash
# LLM-driven planning
python -m fireclaw_core.mission.mission_cli plan-mission \
  --command "去坐标 (2.0, 1.5) 搜索受困人员" \
  --planner llm \
  --provider-base-url https://api.deepseek.com \
  --provider-api-key sk-xxx \
  --model deepseek-chat \
  --catalog models.json \
  --robot-registry robots.json \
  --mission-registry missions.jsonl \
  --llm-trace-path logs/llm-traces.jsonl

# Deterministic planning (default, backward compatible)
python -m fireclaw_core.mission.mission_cli plan-mission \
  --command "去坐标 (2.0, 1.5) 搜索受困人员" \
  --robot-registry robots.json \
  --mission-registry missions.jsonl
```

**Components:**
- `provider.py` — `ModelProvider` protocol and `OpenAICompatProvider` (httpx-based)
- `model_catalog.py` — `ModelCatalog` for model metadata (context window, capabilities, cost)
- `llm_planner.py` — `LLMMissionPlanner` with tool calling for structured output
- `llm_trace.py` — `LLMTraceStore` for recording full LLM call traces (prompt, response, tokens, latency)

**LLM Trace:** Record every LLM call for debugging and audit. Use `--llm-trace-path` to enable.

### Unified Planner Context Management

The central mission planner and robot-local LLM planner use the same
`ModelAwareContextManager`. A model catalog supplies the real context window,
maximum output allowance, and optional tokenizer:

```json
{
  "models": [
    {
      "id": "local-model",
      "name": "Local Model",
      "provider": "local",
      "context_window": 32768,
      "max_tokens": 4096,
      "supports_tools": true,
      "tokenizer_id": "/srv/fireclaw/models/local-model"
    }
  ]
}
```

`tokenizer_id` must resolve from local files or the local Hugging Face cache;
FireClaw never downloads a tokenizer during planning. When it can be loaded,
the complete messages and tool schemas are counted with that tokenizer.
Otherwise FireClaw uses a conservative CJK-aware estimate and marks the
manifest as inexact.

Each request is divided into three trust levels:

- `authoritative`: current mission snapshot, robot state, environment state,
  sensors, task contract, skill inventory, and safety-critical constraints;
- `continuity`: active mission/task/plan identifiers needed to continue work;
- `advisory`: session history, mission memory, entity memory, and RAG
  knowledge that may help planning but cannot establish current physical fact.

Old advisory records are converted into a deterministic structured summary
with a content digest and source references, while recent records remain
verbatim. If the request still exceeds the model input budget, advisory items
are omitted and recorded in the context manifest. Authoritative content is
never truncated; if it and the tool schemas do not fit, planning is blocked
before a provider call.

The input allowance is:

```text
context_window - output_reserve - safety_margin
```

Both planners persist the selected model, counter type, exact/fallback flag,
input allowance, actual token count, compacted source references, and omitted
items. Robot-local gateways may use a separate catalog with
`--robot-agent-catalog` or `[robot_agent.provider].catalog`.

`fireclaw_core.context.evaluation.evaluate_context_case` provides a
deterministic baseline for comparing unmanaged and managed requests. It
reports raw/managed input tokens, savings, compacted source counts, omitted
advisory items, and exact preservation of authoritative and continuity
sections. This evaluates context policy behavior; plan-quality equivalence
still requires model-backed mission benchmarks.

### Mission Authorization v1

MissionAgent supports mission-level authorization scopes, aligned with OpenClaw's operator scope pattern. When a `ControlPolicy` and `OperatorContext` are configured, MissionAgent checks authorization before executing mission operations.

**Mission scopes:**

| Scope | Operations |
|-------|------------|
| `mission.submit` | `submit_subtask` |
| `mission.cancel` | `cancel_mission` |
| `mission.plan` | `plan_and_submit` |
| `mission.read` | `mission_trace` |

**Role-scope mapping:**

| Role | Mission Scopes |
|------|----------------|
| `observer` | `mission.read` |
| `operator` | `mission.submit`, `mission.cancel`, `mission.plan`, `mission.read` |
| `supervisor` | `mission.submit`, `mission.cancel`, `mission.plan`, `mission.read` |
| `admin` | `mission.submit`, `mission.cancel`, `mission.plan`, `mission.read` |

The `admin` role bypasses all scope checks. When no `ControlPolicy` is configured, authorization is skipped (backward compatible).

**Python API:**

```python
from fireclaw_core.gateway.control import ControlPolicy, OperatorContext, scopes_for_role
from fireclaw_core.mission.mission_agent import MissionAgent

policy = ControlPolicy()
operator = OperatorContext(
    operator_id="mission-operator",
    role="operator",
    control_scopes=scopes_for_role("operator"),
)
mission = MissionAgent(
    registry=robot_registry,
    control_policy=policy,
    operator=operator,
)

# Allowed: operator has mission.submit
result = mission.submit_subtask(
    "robot-1",
    "去坐标 (2.0, 1.5) 搜索",
    session_id="mission-1",
)

# Denied: observer lacks mission.submit
observer = OperatorContext(
    operator_id="observer-1",
    role="observer",
    control_scopes={"state.read", "mission.read"},
)
mission_observer = MissionAgent(
    registry=robot_registry,
    control_policy=policy,
    operator=observer,
)
result = mission_observer.submit_subtask(
    "robot-1",
    "去坐标 (2.0, 1.5) 搜索",
    session_id="mission-1",
)
# result["status"] == "denied"
```

**CLI:**

```bash
# Submit task via gateway (default operator has all mission scopes)
curl -X POST http://127.0.0.1:8765/tasks \
  -H "Content-Type: application/json" \
  -d '{"command": "去坐标 (2.0, 1.5) 搜索", "session_id": "mission-001"}'
```

### Fleet Presence v1

`MissionAgent` can check which Robot Agents are online before planning. `check_fleet_presence()` pings each enabled robot's `/state` endpoint and updates the registry with `last_seen_at` timestamps.

```python
from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.mission.mission_agent import MissionAgent

registry = RobotRegistry([
    RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("search_for_victims",)),
])
mission = MissionAgent(registry=registry, subagent_client=client)

# Check which robots are online
presence = mission.check_fleet_presence()
# presence["r1"]["online"] == True
# presence["r2"]["online"] == False (if unreachable)

# plan_and_submit automatically filters offline robots
result = mission.plan_and_submit(
    "去坐标 (2.0, 1.5) 搜索受困人员",
    session_id="mission-003",
)
# Only online robots receive subtasks
```

Robot presence is tracked in-memory on `RobotRegistry`. `is_online(robot_id)` and `online_entries()` query the last known state. `plan_and_submit` calls `check_fleet_presence()` automatically and skips offline robots when assigning subtasks.

### Mission Scheduler, Checkpoints, and Recovery

The scheduler executes compiled graph nodes in dependency-compatible groups.
Subtasks in the same group may be submitted in parallel; later groups wait for
their dependencies to reach an accepted terminal state. Node-level execution
state is persisted in revision-aware checkpoints so the runtime can recover
pending dispatches after a coordinator restart.

```python
from fireclaw_core.mission.mission_scheduler import (
    MissionFailurePolicy,
    MissionScheduler,
    MissionSchedulerConfig,
)

scheduler = MissionScheduler(
    mission_agent=mission,
    registry=registry,  # optional, defaults to mission_agent.registry
    config=MissionSchedulerConfig(
        failure_policy=MissionFailurePolicy(
            on_failed="reassign",   # "retry", "reassign", "skip", "escalate", "abort"
            on_denied="abort",
            on_lost="abort",
            on_block="escalate",
            max_retries=1,
            max_reassigns=1,
        ),
        poll_interval_seconds=0.1,
        group_timeout_seconds=300.0,
    ),
)

result = scheduler.schedule(plan, mission_id="mission-004", session_id="mission-004")
# result["status"]: "succeeded", "aborted", or "escalated"
# result["failure_decisions"]: per-subtask failure handling records
```

The execution monitor distinguishes three cases:

1. A physical subtask completed and supplied all compiled evidence: accept the
   node and release dependent work.
2. The result transport is incomplete: re-read the same `task_id` once without
   resubmitting the physical action.
3. The evidence semantically rejects completion or invalidates the route/robot
   assumption: emit a typed event and enter the bounded plan-revision loop.

**Local failure decisions:**

| Decision | Behavior |
|---|---|
| `retry` | Resubmit to the same robot (up to `max_retries` times). |
| `reassign` | Submit to a different robot with matching capability (up to `max_reassigns` times). |
| `skip` | Ignore the failure and continue. |
| `escalate` | Mark as needing operator intervention; mission continues. |
| `abort` | Stop the mission immediately. |

Each failure status (`failed`, `denied`, `lost`, `block`) maps to an independent decision. For example, `on_failed="reassign"` means sensor failures get reassigned to another robot, while `on_denied="abort"` means safety gate denials stop the entire mission.

The scheduler polls `mission_trace()` between groups to determine when all
subtasks reach terminal state. Retry and reassign actions are bounded and
persisted. Revised graphs reconcile old and new nodes: completed compatible
nodes are carried forward, changed or removed active nodes are fenced and
cancelled, and only replacement pending nodes are dispatched.

FireClaw does not treat coordinator restart as permission to replay a physical
action. Recovery validates the checkpoint and current task trace before
continuing. Result rechecks reuse the original task ID; they do not create a
second navigation, search, or suppression command.

### Mission Trace Stream v1

The mission trace stream provides a polling-based generator that yields `MissionEvent` objects as mission subtasks change state. It detects changes between `mission_trace()` snapshots and emits structured events.

```python
from fireclaw_core.mission.mission_trace_stream import MissionTraceStream

stream = MissionTraceStream(mission_agent=mission, poll_interval_seconds=0.1)

for event in stream.stream("mission-004", timeout_seconds=300.0):
    print(event.type, event.robot_id, event.status)
    # "subtask.submitted"   r1  accepted
    # "subtask.status_changed" r1  succeeded  (previous: accepted)
    # "mission.succeeded"   None  succeeded
```

**Event types:**

| Type | When |
|---|---|
| `subtask.submitted` | A new subtask appears in the mission trace. |
| `subtask.status_changed` | A subtask's status changes (e.g., `accepted` → `succeeded`). |
| `mission.succeeded` | All subtasks reached terminal status. |
| `mission.failed` | Mission has a failed subtask. |
| `mission.escalated` | Mission has an escalated subtask. |
| `mission.timeout` | Stream timed out before mission completed. |

The stream stops automatically when the mission reaches a terminal status or the timeout expires. Each event includes `mission_id`, `robot_id`, `task_id`, `status`, `previous_status`, `timestamp`, and `details`.

### Fleet Doctor v1

The fleet doctor validates robot registry entries, checks robot reachability, and reports capability gaps.

```python
from fireclaw_core.devtools.fleet_doctor import FleetDoctor

doctor = FleetDoctor(registry=registry, subagent_client=client)
findings = doctor.diagnose()
summary = doctor.summary(findings)
# summary["status"] == "healthy" or "unhealthy"
# summary["error_count"], summary["warning_count"]
# summary["findings"] — list of severity/category/robot_id/message
```

**Checks:**

| Category | Severity | Condition |
|---|---|---|
| `registry` | error | Empty or duplicate `robot_id`, empty `base_url` |
| `registry` | warning | Registry is empty |
| `registry` | info | Robot is disabled |
| `reachability` | error | Robot `/state` endpoint unreachable |
| `reachability` | info | Robot is online |
| `capabilities` | warning | Enabled robot has no declared capabilities |

### Mission Memory v1

FireClaw records mission-level memory: outcomes, observations, corrections, and lessons.

**Record types:**

| Type | Use |
|------|-----|
| `outcome` | Mission/subtask success/failure with metadata |
| `observation` | Environment facts (floor layout, obstacles, victim locations) |
| `correction` | Operator corrections (e.g., "don't send robot A to floor 3") |
| `lesson` | Reusable knowledge for future missions |

**CLI usage:**

```bash
# List memory records for a mission
.venv/bin/python -m fireclaw_core.mission.mission_cli memory list --mission-id <id> [--type outcome] [--limit 10]

# Add a memory record
.venv/bin/python -m fireclaw_core.mission.mission_cli memory add --mission-id <id> --type observation \
  --content '{"floor": 2, "note": "smoke detected in east wing"}' [--robot-id r1]

# Summary of all memory records
.venv/bin/python -m fireclaw_core.mission.mission_cli memory summary [--mission-id <id>]
```

**Python API:**

```python
from fireclaw_core.mission.mission_memory import MissionMemoryRecord, MissionMemoryStore

store = MissionMemoryStore("mission_memory.jsonl")
store.append(MissionMemoryRecord(
    record_id="mem-1", mission_id="m-1", record_type="lesson",
    content={"note": "floor 3 stairs blocked, use elevator"},
    created_at="2026-06-08T12:00:00Z",
))
records = store.search(mission_id="m-1", record_type="lesson")
```

**Auto-recording:**

`MissionAgent` automatically records outcome records when:
- A subtask is submitted
- A mission plan is executed
- A mission is cancelled

Configure with `MissionMemoryStore`:

```python
agent = MissionAgent(
    registry=registry,
    mission_memory=MissionMemoryStore("mission_memory.jsonl"),
)
```

### Cross-Robot Event Aggregation v1

Aggregates events from multiple Robot Agents into a unified mission-level timeline.

#### CLI Usage

```bash
# Get all events for a mission
python -m fireclaw_core.mission.mission_cli events <mission_id> [--robot-id <id>] [--type <event_type>] [--limit 100]
```

#### Python API

```python
from fireclaw_core.mission.mission_event_aggregator import MissionEventAggregator

aggregator = MissionEventAggregator(
    registry=robot_registry,
    subagent_client=subagent_client,
    mission_registry=mission_registry,
)
result = aggregator.aggregate("mission-1", robot_id="robot-a", event_type="skill.completed")
# result: {"mission_id": "mission-1", "event_count": 5, "events": [...]}
```

#### Gateway Endpoint

Robot Agents expose `GET /events` for fetching local events:
```
GET /events?task_id=<id>&limit=<N>
```

### Approval Workflow v1

High-risk mission operations can require explicit supervisor approval before execution.

#### Risk Levels

| Level | Behavior |
|-------|----------|
| `low` | Auto-approved, no approval needed |
| `medium` | Auto-approved, logged |
| `high` | Requires supervisor approval |
| `critical` | Requires supervisor approval |

#### CLI Usage

```bash
# List approval requests
python -m fireclaw_core.mission.mission_cli approval list [--mission-id <id>] [--status pending]

# Request approval for a high-risk operation
python -m fireclaw_core.mission.mission_cli approval request \
  --mission-id <id> --action mission.submit --risk-level high \
  --command "去三楼搜救"

# Approve or deny
python -m fireclaw_core.mission.mission_cli approval decide <request_id> --decision approve
python -m fireclaw_core.mission.mission_cli approval decide <request_id> --decision deny --reason "Too dangerous"
```

#### Python API

```python
from fireclaw_core.approval.approval_store import JsonlApprovalStore

store = JsonlApprovalStore("mission_approvals.jsonl")
agent = MissionAgent(registry=registry, approval_store=store)

# Request approval
result = agent.request_approval("m-1", action="mission.submit", risk_level="high", command="去三楼搜救")

# Decide
agent.decide_approval(request_id, decision="approve")
```

### Incident Replay v1

Reconstruct mission timelines from persistent data for post-incident analysis.

#### CLI Usage

```bash
python -m fireclaw_core.mission.mission_cli replay <mission_id> [--memory-path <path>]
```

#### Python API

```python
from fireclaw_core.monitoring.incident_replay import IncidentReplay

replay = IncidentReplay(mission_registry=registry, mission_memory=memory)
result = replay.replay("mission-1")
# result: {"mission_id", "command", "status", "timeline": [...], "summary": {...}}
```

#### Timeline Events

| Event Type | Source |
|------------|--------|
| `subtask.submitted` | Mission registry subtask creation |
| `subtask.status_changed` | Mission registry subtask status update |
| `outcome` | Mission memory outcome record |
| `observation` | Mission memory observation record |
| `correction` | Mission memory correction record |
| `lesson` | Mission memory lesson record |

### Executable Tool Typed Contracts (Legacy Skill API)

The current Python `Skill` compatibility class models an executable Tool. It
carries typed `output_schema`, `domain`, `preconditions`, and
`degraded_mode_policy` metadata alongside the existing `input_schema`. It is
not an OpenClaw-style `SKILL.md` workflow.

```python
from fireclaw_core.execution.skills import (
    NAVIGATE_POINT_OUTPUT_SCHEMA,
    create_default_skill_registry,
)

registry = create_default_skill_registry(robot)
nav = registry.get("navigate_to_point")

nav.output_schema  # {"type": "object", "properties": {"x": {...}, "y": {...}, ...}}
nav.domain          # "navigation"
nav.preconditions   # ["robot_online", "target_point_reachable"]
nav.degraded_mode_policy  # "retry"
```

**Domains:** `navigation`, `perception`, `communication`, `safety`, `manipulation`

**Degraded mode policies:** `skip`, `fallback`, `retry`, `abort`, `escalate`

### Physical Tool Runtime (Legacy PhysicalSkillPlugin API)

内置机器人原子物理 Tool 当前通过兼容 API
`define_physical_skill_plugin()` 声明，再由通用
`SkillRegistry.register_plugin()` 绑定到当前 Robot Adapter。这个边界参考
OpenClaw 的 `defineToolPlugin() -> api.registerTool()`：

```text
LLM-visible tool schema
-> Robot Agent task-envelope policy
-> SafetyGate schema/sensor/risk validation
-> SkillRegistry
-> RobotActionRuntime
-> registered Robot Adapter action
-> ROS / simulator / robot SDK / algorithm
```

一个物理 Tool contribution 自行声明：

- `name / description / parameters / output_schema`；
- `action` 和受信任的 action-input builder；
- 从 `StructuredRobotTask.target` 到工具输入的绑定及防篡改字段；
- `required_sensors / safety_class / risk_level / preconditions`；
- `resource_locks / success_evidence / timeout / retry`；
- Robot Agent 补充工具、默认后续技能和操作员进度消息。

Agent loop、SafetyGate、PlanExecutor 和 Operator projector 不再按
`navigate_to_point`、`search_for_victims` 等名字分派。新增物理能力需要新增
Tool 定义和对应 Adapter handler，并由 Plugin 统一注册；只有硬件实现本身
需要接触 ROS/SDK。
LLM 可见参数应限于任务级变量或经过验证的命名 profile。机器人 footprint、
传感器 frame、硬件极限和原始安全参数不应直接开放给模型。

当前代码名 `Skill` 实际是可执行 Tool 定义，LLM-facing schema 由它投影；
真正的 Skill 是指导 Agent 如何组合多个 Tool 的 `SKILL.md` 工作流。
`RobotAdapter` 是算法/硬件实现边界。MCP 只是在能力位于独立进程或远端服务
时可选的传输协议，不是本地物理 Tool 必须经过的层。详细设计见
[`docs/architecture/physical-skill-plugin-runtime.md`](docs/architecture/physical-skill-plugin-runtime.md)。

### Plugin Extension And Skill Workspaces

仓库根目录 [`extensions/`](extensions/README.md) 保存 Plugin 包、Tool
实现、算法 Runtime、ROS workspace、配置、launch 文件和测试。导航、扫描、
覆盖搜索、感知、操作等实现应按一个可部署 Plugin 一个目录组织。

[`skills/`](skills/README.md) 只保存 Agent-facing `SKILL.md` 能力说明和
工作流。Plugin 可以在自己的 `skills/` 子目录附带 Skill。首个导航 Plugin
位于
[`extensions/navigation-move-base/`](extensions/navigation-move-base/README.md)，
其中附带的
[`Navigation Skill`](extensions/navigation-move-base/skills/navigation/SKILL.md)
指导 Agent 使用一个或多个导航 Tool。

两个目录都不是新的 registry。Tool 和其他贡献仍由
`FireClawPluginHost` 统一拥有和激活。现有 `.skill.json` 和
`workspace_skills_dir` 是把可执行 Tool 称为 Skill 的 legacy compatibility
API；新设计不得延续该含义。该兼容加载器只允许在 `simulation` deployment
profile 中注册，并且必须通过配置了镜像的 `ComputerSandbox`。`real` 模式、
未启用沙箱、无镜像、Tool allowlist/denylist 拒绝时都不会注册该 Tool。
详细边界见
[`docs/architecture/legacy-executable-tool-sandbox.md`](docs/architecture/legacy-executable-tool-sandbox.md)。

### Adapter Capabilities v1

Every adapter now declares what it can do via `capabilities()`:

```python
from fireclaw_core.agent.robot import (
    DryRunRobotAdapter,
    validate_simulator_real_separation,
)

adapter = DryRunRobotAdapter(robot_id="r1")
caps = adapter.capabilities()
# caps.supported_actions, caps.is_simulator, caps.supports_real_execution, ...

# Simulator/real-robot separation check
error = validate_simulator_real_separation("simulator", dry_run=False, action="navigate_to_point")
# error: "Simulator adapter must not execute real actions"
```

All adapters (`DryRunRobotAdapter`, `MockRos1RobotAdapter`, `MockRos2RobotAdapter`, `SimulatorRobotAdapter`, `Ros1RobotAdapter`) implement `capabilities()`.

### Local Failure Taxonomy v1

Structured failure reasons replace ad-hoc status strings:

```python
from fireclaw_core.safety.local_failure import FailureCategory, LocalFailureReason

reason = LocalFailureReason.from_robot_result(
    status="failed", error="Connection timed out", action="navigate_to_point", robot_id="r1",
)
reason.category   # FailureCategory.TIMEOUT
reason.retryable   # True
reason.to_dict()   # {"category": "timeout", "message": "...", "retryable": True, ...}
```

**Categories:** `transport`, `timeout`, `not_configured`, `robot_offline`, `low_battery`, `emergency_stop`, `sensor_unavailable`, `target_unreachable`, `action_failed`, `cancelled`, `precondition_failed`, `safety_blocked`, `authorization_denied`, `unknown`

**Retryable:** `transport`, `timeout`, `sensor_unavailable`
**Non-retryable:** `robot_offline`, `low_battery`, `emergency_stop`, `safety_blocked`, `authorization_denied`, `not_configured`, `target_unreachable`

## Session State

Every task result includes session metadata:

```json
{
  "session": {
    "session_id": "rescue-shift-a",
    "turn_index": 1,
    "resolved_command": "去坐标 (2.0, 1.5) 救人",
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

FireClaw also supports a minimal deterministic clarification flow within a
session. If one turn asks for clarification and the next turn only supplies a
point, such as `坐标 (2.0, 1.5)`, the agent resolves it to
`去坐标 (2.0, 1.5) 救人` before planning while preserving the original command
in the result. Floor-only continuation remains available only for legacy
records.

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
    "runtime": "sandboxed_subprocess",
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

FireClaw also supports minimal structured memory retrieval for common operator
questions. The floor-filter example below documents legacy memory compatibility:

```bash
.venv/bin/python -m fireclaw_core "之前二楼救人成功了吗" \
  --session-id rescue-shift-a \
  --memory-path /tmp/fireclaw-demo-memory.jsonl

.venv/bin/python -m fireclaw_core "上次失败原因是什么" \
  --session-id rescue-shift-a \
  --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Retrieval commands filter JSONL memory by session, status, and intent. Legacy
records can additionally be filtered by target floor. They return
`status="retrieved"`, do not execute skills, and do not append another memory
entry.

You can inspect the currently registered skills:

```bash
.venv/bin/python -m fireclaw_core "你有哪些技能" --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Skill listing commands return skill metadata and do not execute skills or append memory entries.

## External Tool Runtime Direction (Legacy Skill API)

The core package should stay lightweight. It should not directly depend on CUDA, PyTorch, reinforcement-learning environments, ROS middleware, or robot SDKs.

Future heavy algorithms should be exposed as atomic Tools through explicit
runtime adapters and grouped by Plugins. Agent workflows that coordinate
those Tools belong in Skills. Plugin adapters may implement runtime patterns
such as:

- `in_process` for lightweight Python dry-run skills;
- `sandboxed_subprocess` for legacy simulation-only executable manifests;
- `external_conda` for CUDA/RL/deep-learning algorithms with their own dependencies;
- `ros2` for robot-side ROS nodes;
- `http` for local or remote model services.

This keeps the FireClaw agent stable while allowing each robotics algorithm to keep its own dependency stack.

The compatibility external runtime adapter is `SubprocessSkillRunner`. It
sends JSON inputs to a command on stdin and expects a JSON object on stdout:

```json
{
  "ok": true,
  "data": {
    "example": "result"
  }
}
```

Despite its legacy name, it has no host-process implementation. It delegates
only to `ComputerSandbox`, which starts the fixed Docker command assembled by
the trusted host.

Legacy subprocess Tools can also be loaded from JSON manifests under
`skills/**/*.skill.json`:

```json
{
  "name": "rl_navigation",
  "description": "Runs an isolated RL navigation policy.",
  "runtime": "subprocess",
  "command": ["{python}", "run_policy.py"],
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

The `command` field must be a list of strings, not a shell command string.
`{python}` resolves to `python3` inside the configured container, not the
FireClaw host interpreter.

Manifest metadata is part of the executable Tool contract:

- `max_attempts` controls opt-in retry behavior.
- `idempotent` must be `true` when `max_attempts > 1`.
- `required_sensors` lists sensor assumptions the planner or safety gate can later inspect.
- `failure_categories` documents expected failure modes.
- `dry_run_only` must be `true`.
- `allow_real_robot` must be `false`; real capabilities require a Plugin Tool
  backed by a trusted robot Adapter.
- `risk_level` must be one of `low`, `medium`, `high`, or `critical`.
- `input_schema` declares the JSON object inputs a planner should provide.

These fields are exposed by skill listing commands and recorded in agent-visible metadata.

Before registration, regular files in the manifest directory are copied into
a content-addressed directory below the sandbox workspace. Symlinks, special
files, oversized files, and oversized directories are rejected. The command
runs with that staged directory as its container working directory. For
example, `skills/examples/echo_policy.skill.json` declares:

```json
{
  "name": "echo_policy",
  "description": "Example external subprocess skill for algorithm wrappers.",
  "runtime": "subprocess",
  "command": ["{python}", "echo_policy.py"],
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

The wrapper script reads JSON from stdin and writes JSON to stdout. It can use
only dependencies present in the configured sandbox image. The container gets
no FireClaw environment variables, ROS socket, host filesystem, devices, or
network unless a reviewed sandbox profile explicitly adds a supported
capability. Current profiles support only `none` or `bridge` networking and
default to `none`.

The compatibility CLI still discovers workspace manifests by default, but
without an explicit simulation deployment profile and process sandbox they
fail closed and appear in `skill_load_errors`. `fireclaw doctor` uses
inspection-only loading and never stages or runs them:

```bash
.venv/bin/python -m fireclaw_core "你有哪些技能" \
  --skills-dir skills \
  --legacy-skill-sandbox-image fireclaw-agent-sandbox:local \
  --legacy-skill-sandbox-image-digest sha256:<docker-image-id> \
  --legacy-skill-sandbox-root data/fireclaw-sandbox/legacy-agent-cli
```

Use `--no-workspace-skills` to run with built-in skills only.

## Direct Executable Tool Invocation (Legacy Skill Command)

Registered executable Tools can be invoked directly from natural-language
commands after the simulation sandbox options above have been configured. The
CLI still uses legacy `skill` wording:

```bash
.venv/bin/python -m fireclaw_core "运行 echo_policy"
.venv/bin/python -m fireclaw_core "运行 echo_policy 处理 二楼"
```

Direct Tool invocation still goes through the planner, safety gate, executor,
and memory store. Missing Tools are blocked before execution:

```bash
.venv/bin/python -m fireclaw_core "运行 missing_skill"
```

## Rescue Plans With Policy Tools

Rescue commands can include a named policy or algorithm Tool. The Tool is
inserted before navigation and receives the target point plus the original
command:

```bash
.venv/bin/python -m fireclaw_core "去坐标 (2.0, 1.5) 救人 使用 echo_policy"
.venv/bin/python -m fireclaw_core "去坐标 (2.0, 1.5) 救人 导航策略用 echo_policy"
```

The named policy must be registered. If it is missing, the safety gate blocks the plan before any robot action runs.

## Robot Adapter Boundary

Built-in robot Tools depend on the `RobotAdapter` protocol, not on a concrete
robot implementation. Current adapters:

- `DryRunRobotAdapter`: default adapter used by the CLI and tests.
- `SimulatorRobotAdapter`: deterministic single-floor simulator that tracks the current map pose, sensors, online state, and battery level; legacy floor fields remain readable.
- `MockRos1RobotAdapter`: ROS1-shaped test double that records `Ros1CommandSpec` values without importing `rospy`.
- `MockRos2RobotAdapter`: legacy ROS2-shaped test double kept for direct compatibility tests.

`Ros1RobotAdapter` implements this boundary through reviewed configuration and
optional ROS1 transport. `RobotAdapter` no longer enumerates every possible
physical action. An adapter advertises action names through
`capabilities().supported_actions`; a trusted physical Tool contribution
(currently named `PhysicalSkillPlugin`) binds its declared `action` to a
same-named adapter callable. Therefore a new robot SDK capability adds an
Adapter handler and Plugin-owned Tool definition without changing the Agent
protocol or action runtime.

They should also expose state snapshots:

- `get_robot_state()`
- `get_environment_state()`

Future real adapters should also implement `emergency_stop(reason=None)`. This method is part of the safety-critical control boundary and should fail safe: stop motion or escalate to the lowest-risk state available for that robot stack.

Robot state includes fields such as `robot_id`, `mode`, `dry_run`, `online`,
`battery_percent`, `available_sensors`, and `supports_real_execution`.
`current_floor` is currently a compatibility field, not an active planning
dimension.

Environment state includes `hazards`; `reachable_floors` and
`victims_by_floor` remain compatibility fields for older records.

Adapter action results are structured with fields such as `robot_id`, `mode`, `action`, `status`, `dry_run`, `data`, `timestamp`, and `error`. Planner, safety, executor, and memory should not need to change when a real adapter replaces the simulator or dry-run adapter.

Robot action backends can also report progress through the FireClaw action feedback boundary. Mock ROS1 navigation emits deterministic `action.feedback` events. When the real `ros1` adapter uses an action endpoint with `transport.enabled: true`, its `actionlib` feedback callback is forwarded into the same `action.feedback` event shape, and `task_trace(...).state.actions[*]` records `feedback_count` plus `last_feedback`.

Gateway task cancellation propagates through the executor into robot action backends. For ROS1 action endpoints, FireClaw polls the active action client while waiting for a result; if cancellation is requested, it calls `cancel_goal()` when the endpoint declares `cancel_supported: true` and records `action.cancel_requested` / `action.cancelled`.

Task results and memory records include `robot_state` and `environment_state` snapshots before execution. This is intentionally audit-oriented: later incident review and experiment analysis should be able to reconstruct the state the agent used for its safety decision.

Simulator demo:

```bash
.venv/bin/python -m fireclaw_core "去坐标 (2.0, 1.5) 救人" \
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
- requires a point target for current single-floor rescue plans; legacy floor
  plans retain their old reachability check;
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

Cancel in the same session:

```bash
.venv/bin/python -m fireclaw_core "取消" \
  --session-id rescue-shift-a \
  --skills-dir skills \
  --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Natural-language confirmation such as `确认执行` is not execution authority.
Without a signed `ExecutionAuthorization`, the local Agent remains in
`awaiting_confirmation`. Cancellation records `status="cancelled"` and does
not execute skills.

## Operator Authorization

High and critical risk tasks create a server-side authorization request before
the pending result is returned:

```text
authorization.requested -> authorization.approved | authorization.denied | authorization.expired
```

Default role behavior:

- `observer`: can read state only.
- `operator`: can submit and cancel tasks, but cannot approve high/critical risk work.
- `supervisor`: can approve high/critical risk work through `safety.override`.
- `admin`: supervisor scopes plus `emergency.stop`.

Example supervisor approval:

```bash
curl -X POST http://127.0.0.1:8765/confirm \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "rescue-shift-a",
    "operator": {
      "operator_id": "supervisor-1",
      "role": "supervisor"
    }
  }'
```

The request binds the original command, structured task, robot identity, exact
skill names and canonical input hashes. An authorized supervisor receives a
short-lived, HMAC-signed `ExecutionAuthorization`; the Gateway resubmits the
original task with that grant and the Robot Agent recomputes the scope before
execution. Changed parameters, another robot, expiry, signature tampering, or
reusing an already consumed operation all fail closed.

If an `operator` without `safety.override` tries to approve a high-risk task, Gateway returns HTTP 403 and records `authorization.denied`. If the pending authorization expires, Gateway returns HTTP 403 with `status="expired"` and records `authorization.expired`.

Task cancellation also checks authorization. A caller without `task.cancel` receives HTTP 403 and the task records `task.cancel_denied`; the active task is not cancelled.

## Embodied-Agent Experiment Readiness

FireClaw can claim **simulator-level embodied-agent experiment readiness** when the focused validation tests, simulator eval CLI, and proof bundle CLI all pass.

FireClaw cannot claim real firefighting robot validation until a ROS1 high-fidelity or hardware proof run produces the required deployment artifacts.

The simulator eval emits a `doctor-report.json` describing the eval run's health, making the proof bundle acceptance chain self-contained without requiring a separate doctor invocation. Real firefighting robot validation still requires a ROS1 hardware or high-fidelity simulation doctor report — the simulator doctor report documents eval-level health only.
部署前可以运行只读安全审计：

```bash
fireclaw security-audit --config fireclaw.toml
```

审计会检查 Gateway 暴露与认证、TLS、运行目录和文件权限、sandbox/Tool
策略、旧版可执行清单以及插件来源。详细说明见
[`docs/architecture/security-audit.md`](docs/architecture/security-audit.md)。
