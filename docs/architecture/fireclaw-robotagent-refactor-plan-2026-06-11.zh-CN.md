# FireClaw 架构重构方案：以单机器人具身 Agent 为核心

> **历史文档，已被 2026-08-09 Plugin boundary cleanup 取代。**
> 本文的旧 Tool 名、跨楼层示例、workspace loader 和 Adapter 领域动作仅用于
> 追溯设计演化，不能作为当前 API、配置或 Gazebo 验收依据。当前实现以
> manifest-loaded Plugin、Plugin-owned handler 和单楼层绝对 `map` pose 为准。

> 术语说明（2026-07-29）：本文是历史重构计划。文中的 `MainAgent` / 主智能体现统一称为 `Mission Coordinator`（任务协调器），机器人侧 `subagent` / 子智能体现统一称为 `Robot Agent`（机器人智能体）。两者是 FireClaw 内两个常驻、职责不同但协同工作的 Agent 角色；`subagent` 仅保留给未来临时派生的认知工作单元。详见 [FireClaw Agent Terminology](./fireclaw-agent-terminology.md)。
>
> 空间范围说明（2026-07-29）：本文中的跨楼层示例同样属于历史设计。当前运行合同仅支持单楼层二维地图和 `navigate_to_point`；楼层字段只用于兼容旧记录及未来扩展。详见 [FireClaw Spatial Scope](./fireclaw-spatial-scope.md)。
>
> Plugin/Skill/Tool 说明（2026-07-29）：本文中的 `SkillRuntime`、`SkillRegistry`
> 和 `skill` 多数实际指 legacy 可执行 Tool。后续设计必须以
> [Plugin, Skill, Tool, Runtime, and Adapter Terminology](./plugin-skill-tool-terminology.md)
> 为准。

日期：2026-06-11  
适用项目：FireClaw  
文档定位：架构重构设计与实施计划  
当前目标：明确 FireClaw 的核心定位、系统边界、主调用链路与后续代码重构步骤。

---

## 1. 重构背景

FireClaw 的最初目标是仿照 OpenClaw 的 Agent / Skill / Memory / Subagent 思路，构建一个面向救援机器人的具身 Agent 框架。

当前项目已经具备较多模块，包括：

- 单机器人本地 `FireClawAgent`
- 机器人本地 `FireClawGateway`
- 上位机侧 `MissionAgent`
- `SkillRegistry` / `SkillRuntime`
- `SafetyGate`
- `RobotAdapter`
- `MissionPlanner` / `LLMMissionPlanner`
- `TaskQueue` / `EventLedger` / `MissionRegistry`
- ROS1 adapter skeleton
- Memory、Approval、Plugin、Provider、Doctor 等辅助模块

这些模块说明项目已经具备平台雏形，但也带来一个明显问题：

> 当前 FireClaw 容易被理解成一个多机器人调度平台，而不是一个以单机器人具身 Agent 为核心的系统。

经过重新梳理，FireClaw 的核心目标应调整为：

> FireClaw 是一个面向救援机器人的单机具身 Agent 框架。每台机器人运行一个常驻 FireClaw RobotAgent，具备任务理解、技能调用、安全门控、执行反馈和任务记忆能力。上位机运行 MainAgent / MissionCoordinator，用于接收消防员指令、选择在线机器人并下发任务，但不直接控制机器人硬件。

---

## 2. 核心定位

### 2.1 FireClaw 的真正研究对象

FireClaw 的核心不是上位机，也不是多机器人调度系统，而是：

```text
FireClaw RobotAgent = 单台救援机器人上的具身智能体
```

RobotAgent 应具备以下能力：

1. 接收来自上位机或本地命令行的任务；
2. 将任务转化为结构化执行计划；
3. 根据机器人状态、环境状态和任务风险进行安全门控；
4. 调用本机技能；
5. 通过 ROS1 / ROS2 / 仿真 / 机器人 SDK 执行动作；
6. 将执行过程以事件流形式返回给上位机；
7. 记录任务结果、失败原因和操作员修正，形成可检索记忆。

### 2.2 上位机的定位

上位机不应直接控制电机、传感器或 ROS topic。它的定位是：

```text
MainAgent / MissionCoordinator = 任务指挥与协调层
```

主要职责：

1. 接收消防员自然语言任务；
2. 查询在线机器人状态；
3. 查询机器人能力、位置、工作区和当前负载；
4. 将任务分解为结构化子任务；
5. 选择合适的 RobotAgent；
6. 将任务下发给对应机器人；
7. 汇总多个机器人返回的事件、状态和结果；
8. 提供取消、急停、人工确认和任务追踪接口。

### 2.3 不建议的表述

不建议说：

```text
上位机 MainAgent 生成多个机器人 subagent。
```

因为机器人不是临时生成的软件进程，而是真实物理实体。更准确的说法是：

```text
上位机 MainAgent 调用多个已经注册并在线的 RobotAgent。
每个 RobotAgent 都是一个独立的 FireClaw 具身智能体。
```

---

## 3. 目标架构

### 3.1 总体结构

```text
                           上位机 / PC / 指挥端
┌──────────────────────────────────────────────────────────┐
│ MainAgent / MissionCoordinator                            │
│                                                          │
│ 1. 接收消防员自然语言任务                                 │
│ 2. 查询 RobotAgent 在线状态 / 能力 / 位置 / 负载            │
│ 3. 任务分解与机器人选择                                   │
│ 4. 生成结构化 RobotTask                                   │
│ 5. 下发任务到对应机器人                                   │
│ 6. 监听事件流并汇总反馈                                   │
│ 7. 支持取消、急停、人工确认、任务追踪                       │
└──────────────────────────────────────────────────────────┘
                            │
                            │ HTTP / WebSocket / ROS Bridge
                            ▼
                    机器人 1 / 机器人 2 / 机器人 N
┌──────────────────────────────────────────────────────────┐
│ FireClaw RobotAgent                                       │
│                                                          │
│ FireClawGateway                                           │
│   ↓                                                      │
│ FireClawAgent / RobotAgentCore                            │
│   ↓                                                      │
│ Planner / TaskInterpreter                                 │
│   ↓                                                      │
│ SafetyGate                                                │
│   ↓                                                      │
│ SkillRuntime / SkillRegistry                              │
│   ↓                                                      │
│ RobotAdapter                                              │
│   ↓                                                      │
│ ROS1 / ROS2 / Simulator / Robot SDK / Sensors / Actuators  │
└──────────────────────────────────────────────────────────┘
```

### 3.2 主调用链路

```text
消防员自然语言命令
-> 上位机 MainAgent 理解任务
-> MissionPlanner 生成结构化任务
-> RobotSelector 选择目标机器人
-> 下发 StructuredRobotTask
-> 机器人端 FireClawGateway 接收任务
-> RobotAgentCore 执行任务解释与本地规划
-> SafetyGate 做安全检查
-> SkillRuntime 调用技能
-> RobotAdapter 转换为 ROS / 仿真 / SDK 调用
-> EventStream 返回执行进度
-> Memory 记录任务结果和失败原因
```

---

## 4. 关键职责边界

### 4.1 MainAgent / MissionCoordinator

上位机侧只负责全局协调，不负责真实动作执行。

应负责：

- 自然语言任务入口；
- 多机器人状态查询；
- 多机器人能力匹配；
- 任务分解；
- 任务分配；
- 多机器人事件聚合；
- 任务取消；
- 全局急停；
- 人工确认；
- 指挥端 UI / CLI / Web 控制台。

不应负责：

- 直接发布 ROS topic；
- 直接调用底盘、机械臂、传感器；
- 绕过机器人端 SafetyGate；
- 直接执行高风险动作；
- 修改机器人本地技能运行结果。

### 4.2 FireClaw RobotAgent

机器人端是 FireClaw 的核心主体。

应负责：

- 接收结构化任务；
- 保留自然语言调试入口；
- 执行本地任务解释；
- 检查本机状态；
- 检查环境状态；
- 检查技能前置条件；
- 调用本机技能；
- 执行 ROS / SDK 动作；
- 上报事件流；
- 处理取消和急停；
- 写入本机任务记忆。

不应负责：

- 全局多机器人调度；
- 选择其他机器人；
- 跨机器人任务分配；
- 自行绕过上位机授权策略。

### 4.3 SkillRuntime

SkillRuntime 是能力执行层。

应负责：

- 执行 skill；
- 校验输入；
- 校验输出；
- 支持 timeout；
- 支持 cancel；
- 支持 retry；
- 记录 skill-level 事件；
- 输出统一 `SkillResult`。

不应负责：

- 自己决定任务分配；
- 自己绕过安全门；
- 任意访问系统文件；
- 任意访问网络；
- 任意启动高风险进程。

### 4.4 RobotAdapter

RobotAdapter 是机器人硬件和 FireClaw 框架之间的边界。

应负责：

- 将标准 action 转换成 ROS action / service / topic；
- 读取机器人状态；
- 读取传感器状态；
- 支持动作取消；
- 支持动作反馈；
- 将底层错误规范化为 `RobotActionResult`。

不应负责：

- 自然语言理解；
- 高层任务规划；
- 多机器人调度；
- 记忆检索；
- 用户权限判断。

---

## 5. 上位机与机器人之间的任务协议

当前代码中 MissionPlanner 仍然大量使用自然语言 `command` 下发给机器人端。长期看，这会导致：

```text
上位机理解一次自然语言
-> 机器人端再次理解自然语言
-> 再转成 skill
```

这会造成语义误差叠加。建议改成结构化任务协议。

### 5.1 StructuredRobotTask

建议新增数据结构：

```python
@dataclass(frozen=True)
class StructuredRobotTask:
    task_id: str
    task_type: str
    target: dict[str, Any]
    required_skills: list[str]
    constraints: dict[str, Any]
    priority: str = "normal"
    risk_level: str = "low"
    operator_id: str | None = None
    mission_id: str | None = None
```

示例：

```json
{
  "task_id": "task-001",
  "task_type": "rescue_search",
  "target": {
    "floor": 2,
    "zone": "east_corridor"
  },
  "required_skills": [
    "navigate_to_floor",
    "search_for_victims",
    "report_status"
  ],
  "constraints": {
    "max_speed": 0.5,
    "avoid_smoke_area": true,
    "require_operator_confirmation": false
  },
  "priority": "high",
  "risk_level": "medium",
  "operator_id": "operator-001",
  "mission_id": "mission-001"
}
```

### 5.2 RobotTaskResult

建议统一机器人端返回结构：

```python
@dataclass(frozen=True)
class RobotTaskResult:
    task_id: str
    robot_id: str
    status: str
    started_at: str
    ended_at: str | None
    steps: list[dict[str, Any]]
    final_state: dict[str, Any]
    error: str | None = None
```

### 5.3 RobotEvent

建议统一事件结构：

```python
@dataclass(frozen=True)
class RobotEvent:
    event_id: str
    event_type: str
    timestamp: str
    robot_id: str
    task_id: str | None
    mission_id: str | None
    payload: dict[str, Any]
```

典型事件：

```text
task.received
task.planned
safety.decided
skill.started
skill.succeeded
skill.failed
action.requested
action.started
action.feedback
action.succeeded
action.failed
action.cancelled
task.completed
task.failed
task.cancelled
emergency_stop.activated
```

---

# 6. 具体重构计划

## Phase 0：冻结现状与建立基线

目标：在重构前保留当前可运行状态，避免越改越乱。

### 具体步骤

1. 确认当前 `master` 能运行测试：

```bash
.venv/bin/python -m pytest -q
```

2. 建议创建一个标签：

```bash
git tag v0.1-before-robotagent-refactor
git push origin v0.1-before-robotagent-refactor
```

3. 确认当前 README 中的运行命令仍然有效：

```bash
.venv/bin/python -m fireclaw_core "去二楼救人" --memory-path /tmp/fireclaw-demo-memory.jsonl
```

4. 保存当前架构说明，作为 legacy 参考，不要直接删除旧文档。

### 产出

- 当前测试基线；
- 当前 demo 基线；
- 一个重构前 tag；
- 当前主路径说明。

---

## Phase 1：统一命名与职责边界

目标：先不大改逻辑，只把架构概念收敛。

### 具体步骤

1. 在文档层明确四个核心概念：

```text
MissionCoordinator = 上位机任务协调器
RobotAgent = 单机器人具身智能体
SkillRuntime = 技能执行层
RobotAdapter = ROS / 仿真 / 硬件适配层
```

2. 保留当前类名，但在文档中建立映射：

```text
MissionAgent -> MissionCoordinator
FireClawAgent -> RobotAgentCore
FireClawGateway -> RobotAgentGateway
```

3. 后续代码重命名不要一次性完成，避免破坏测试。建议先增加 alias：

```python
RobotAgentCore = FireClawAgent
MissionCoordinator = MissionAgent
RobotAgentGateway = FireClawGateway
```

4. 更新 README：

- 第一段明确 FireClaw 是单机器人具身 Agent；
- 上位机只是 mission coordinator；
- 每台机器人常驻一个 FireClaw RobotAgent；
- 多机器人只是多个 RobotAgent 的调用和协调。

### 建议涉及文件

```text
README.md
src/fireclaw_core/agent.py
src/fireclaw_core/mission_agent.py
src/fireclaw_core/gateway.py
```

### 产出

- 统一术语；
- 避免“MainAgent 生成机器人 subagent”的错误表述；
- 明确机器人端才是 FireClaw 的核心。

---

## Phase 2：新增结构化任务协议

目标：减少上位机和机器人之间的自然语言二次解释。

### 具体步骤

1. 新增文件：

```text
src/fireclaw_core/task_contract.py
```

2. 定义核心数据结构：

```python
@dataclass(frozen=True)
class StructuredRobotTask:
    task_id: str
    task_type: str
    target: dict[str, Any]
    required_skills: list[str]
    constraints: dict[str, Any]
    priority: str = "normal"
    risk_level: str = "low"
    operator_id: str | None = None
    mission_id: str | None = None
```

3. 增加从 mission subtask 到结构化任务的转换函数：

```python
def structured_task_from_mission_subtask(
    *,
    mission_id: str,
    robot_id: str,
    subtask: MissionSubtask,
    operator_id: str | None = None,
) -> StructuredRobotTask:
    ...
```

4. 增加校验函数：

```python
def validate_structured_robot_task(task: StructuredRobotTask) -> list[str]:
    ...
```

5. 在 `MissionAgent.submit_subtask()` 中保留 `command`，但额外生成 `structured_task` 字段。

6. 在 `FireClawGateway` 中增加新入口，建议先兼容原有 `/tasks`：

```json
{
  "command": "去二楼搜索受困人员",
  "structured_task": {
    "task_type": "rescue_search",
    "target": {
      "floor": 2
    },
    "required_skills": [
      "navigate_to_floor",
      "search_for_victims",
      "report_status"
    ]
  }
}
```

### 产出

- `StructuredRobotTask`；
- 结构化任务校验；
- MissionAgent 到 RobotAgent 的结构化任务下发路径；
- 保留自然语言入口作为兼容和调试用途。

---

## Phase 3：收敛机器人端主执行链路

目标：让机器人端 FireClawAgent 成为真正的 RobotAgentCore。

### 当前问题

当前 `FireClawAgent.run(command: str)` 主要接收自然语言命令，然后用 `RuleBasedPlanner` 解析。

这适合 demo，但不适合作为上位机到机器人之间的正式协议。

### 具体步骤

1. 保留原来的：

```python
def run(self, command: str) -> dict[str, Any]:
    ...
```

2. 新增结构化任务入口：

```python
def run_structured_task(self, task: StructuredRobotTask) -> dict[str, Any]:
    ...
```

3. `run_structured_task()` 的逻辑应为：

```text
StructuredRobotTask
-> validate task
-> convert to Plan
-> read robot_state
-> read environment_state
-> SafetyGate.evaluate()
-> PlanExecutor.execute()
-> append memory
-> return RobotTaskResult
```

4. 新增转换函数：

```python
def plan_from_structured_task(task: StructuredRobotTask) -> PlanningResult:
    ...
```

5. 示例转换：

```json
{
  "task_type": "rescue_search",
  "target": {
    "floor": 2
  },
  "required_skills": [
    "navigate_to_floor",
    "search_for_victims",
    "report_status"
  ]
}
```

转换为：

```python
Plan(
    intent="rescue_search",
    steps=[
        PlanStep("navigate_to_floor", {"floor": 2}),
        PlanStep("search_for_victims", {"floor": 2}),
        PlanStep("report_status", {"floor": 2}),
    ],
)
```

6. 保留自然语言 planner，但将其定位为：

```text
本地调试入口 / 单机命令入口 / fallback 入口
```

### 建议涉及文件

```text
src/fireclaw_core/agent.py
src/fireclaw_core/planner.py
src/fireclaw_core/task_contract.py
tests/test_structured_task.py
tests/test_robot_agent_structured_task.py
```

### 产出

- RobotAgent 结构化任务执行能力；
- 自然语言入口与结构化入口并存；
- 上位机下发任务不再必须二次自然语言解析。

---

## Phase 4：修正 ROS1 Adapter 的未知状态问题

目标：避免真实 ROS1 adapter 因占位状态被 SafetyGate 错误拦截。

### 当前问题

真实 ROS1 adapter 中一些状态可能是占位值，例如：

```python
battery_percent = 0.0
reachable_floors = []
available_sensors = []
```

这些值容易被 SafetyGate 解释为：

```text
电量为 0
没有可达楼层
没有传感器
```

这会导致真实机器人还没执行就被拦截。

### 具体步骤

1. 修改 `RobotState`：

```python
battery_percent: float | None
current_floor: int | None
available_sensors: list[str] | None
```

2. 修改 `EnvironmentState`：

```python
reachable_floors: list[int] | None
hazards: list[str] | None
victims_by_floor: dict[int, int] | None
```

3. SafetyGate 中明确区分：

```text
None = 未知
0.0 = 明确为 0
[] = 明确为空
```

4. 对未知状态设置策略：

```text
critical 状态未知 -> require_confirmation 或 block
非 critical 状态未知 -> warn / allow_with_warning
```

5. 电量策略示例：

```python
if robot_state.battery_percent is None:
    warnings.append("Robot battery state is unknown.")
elif robot_state.battery_percent < 10.0:
    blocks.append("Robot battery is too low.")
```

6. 楼层可达策略示例：

```python
if environment_state.reachable_floors is None:
    warnings.append("Reachable floors are unknown.")
elif target_floor not in environment_state.reachable_floors:
    blocks.append("Target floor is not reachable.")
```

7. 更新 `Ros1RobotAdapter.get_robot_state()`：

```python
battery_percent=None
available_sensors=None
```

8. 更新 `Ros1RobotAdapter.get_environment_state()`：

```python
reachable_floors=None
```

### 建议涉及文件

```text
src/fireclaw_core/robot.py
src/fireclaw_core/safety.py
tests/test_safety_unknown_state.py
tests/test_ros1_adapter_state.py
```

### 产出

- 真实 ROS1 adapter 不再被占位状态误伤；
- SafetyGate 具备 unknown 状态处理能力；
- 后续可接入真实电量、传感器、地图状态。

---

## Phase 5：强化 SafetyGate 为机器人安全门

目标：让 SafetyGate 从字段检查升级为机器人运行安全检查。

### 当前 SafetyGate 已有能力

当前已经能检查：

- 是否需要澄清；
- 是否有可执行 plan；
- 技能是否存在；
- 传感器是否可用；
- retry 是否安全；
- dry-run / real-run 是否冲突；
- 高风险 skill 是否需要确认；
- 电量是否过低；
- 目标楼层是否可达。

### 需要新增的安全维度

建议扩展为：

```text
空间安全：目标点是否在可通行区域、是否靠近危险区域
运动安全：速度上限、角速度上限、急停距离、碰撞风险
任务安全：是否允许单机器人进入未知区域
感知安全：传感器置信度是否足够
通信安全：心跳是否正常、指令是否过期
执行安全：动作是否有超时、是否能中断、是否有回滚方案
人类安全：是否接近人员、是否执行喷淋/破拆等高风险动作
```

### 具体步骤

1. 新增 SafetyContext：

```python
@dataclass(frozen=True)
class SafetyContext:
    robot_state: RobotState | None
    environment_state: EnvironmentState | None
    task: StructuredRobotTask | None
    operator_confirmed: bool = False
    dry_run: bool = True
```

2. 将 SafetyGate 拆成多个检查函数：

```python
_check_plan_executable()
_check_skill_availability()
_check_sensor_availability()
_check_robot_state()
_check_environment_state()
_check_risk_confirmation()
_check_motion_constraints()
_check_task_expiry()
```

3. SafetyDecision 增加 warnings：

```python
@dataclass(frozen=True)
class SafetyDecision:
    status: str
    reasons: list[str]
    warnings: list[str] = field(default_factory=list)
```

4. 支持更多状态：

```text
allow
allow_with_warning
require_confirmation
clarify
block
```

5. 对高风险任务要求人工确认：

```text
risk_level = high / critical
-> require_confirmation
```

6. 对真实机器人动作必须明确：

```text
dry_run=false
-> 需要真实 adapter
-> 需要 operator confirmation
-> 需要 emergency_stop 可用
```

### 建议涉及文件

```text
src/fireclaw_core/safety.py
src/fireclaw_core/robot.py
src/fireclaw_core/task_contract.py
tests/test_safety_gate.py
```

### 产出

- 更完整的机器人安全门；
- 支持 unknown 状态；
- 支持 warnings；
- 支持真实机器人执行前确认。

---

## Phase 6：强化 Skill 合同

目标：将 skill 从“函数包装”升级为“机器人能力合同”。

### 当前 Skill 已有字段

当前 Skill 已有：

```text
name
description
handler
runtime
dry_run_only
max_attempts
idempotent
required_sensors
failure_categories
allow_real_robot
timeout_seconds
input_schema
risk_level
output_schema
domain
preconditions
degraded_mode_policy
```

这说明 skill metadata 已经有基础，但还需要强制执行。

### 具体步骤

1. 输入校验：

```python
validate_inputs(skill.input_schema, inputs)
```

2. 输出校验：

```python
validate_outputs(skill.output_schema, output)
```

3. 前置条件检查：

```python
check_preconditions(skill.preconditions, robot_state, environment_state, task)
```

4. degraded mode 策略落地：

```text
skip
fallback
retry
abort
escalate
```

5. 明确 skill 可中断性：

```python
cancel_supported: bool
```

6. 明确 skill 对应的 robot action：

```python
action_type: str
```

7. 明确真实机器人执行权限：

```python
allow_real_robot: bool
requires_operator_confirmation: bool
```

8. 对 subprocess skill 增加限制：

```text
工作目录限制
环境变量白名单
命令白名单
stdout 大小限制
stderr 大小限制
timeout 必填
禁止默认继承全部环境变量
```

### 建议涉及文件

```text
src/fireclaw_core/skills.py
src/fireclaw_core/runtime.py
src/fireclaw_core/skill_manifest.py
tests/test_skill_contract.py
tests/test_subprocess_skill_security.py
```

### 产出

- skill 输入/输出强校验；
- skill 风险等级真正生效；
- subprocess skill 更安全；
- 后续可安全扩展救援机器人技能库。

---

## Phase 7：重构上位机 MainAgent / MissionCoordinator

目标：让上位机成为任务协调器，而不是另一个具身 Agent。

### 具体步骤

1. 保留 `MissionAgent`，但文档和后续命名中称为：

```text
MissionCoordinator
```

2. 新增 RobotSelector：

```python
class RobotSelector:
    def select_robot(
        self,
        task: StructuredRobotTask,
        robots: list[RobotRegistryEntry],
    ) -> RobotRegistryEntry | None:
        ...
```

3. 选择机器人时考虑：

```text
是否在线
是否启用
能力是否匹配
工作区域是否匹配
当前任务负载
电量
传感器
距离目标区域
是否 stale
```

4. 修改 MissionPlanner 输出，不再只输出自然语言 `command`，而是输出：

```python
MissionSubtask(
    robot_id="robot-01",
    structured_task=StructuredRobotTask(...),
)
```

5. LLM planner 的职责调整为：

```text
理解任务意图
提出候选分解
不要直接拥有最终执行权
```

6. 新增 deterministic validator：

```python
class MissionPlanValidator:
    def validate(plan: MissionPlan, registry: RobotRegistry) -> list[str]:
        ...
```

7. 验证内容：

```text
robot_id 是否存在
robot 是否在线
capability_required 是否匹配
floor / zone 是否可达
任务风险等级是否合法
execution_group 是否合理
是否存在循环依赖
```

### 建议涉及文件

```text
src/fireclaw_core/mission_agent.py
src/fireclaw_core/mission_planner.py
src/fireclaw_core/llm_planner.py
src/fireclaw_core/robot_registry.py
src/fireclaw_core/task_contract.py
tests/test_robot_selector.py
tests/test_mission_plan_validator.py
```

### 产出

- 上位机职责收敛；
- 多机器人协调路径更清楚；
- LLM planner 输出经过确定性校验；
- MissionAgent 不再像第二个机器人 agent。

---

## Phase 8：打通最小可运行闭环

目标：先做一个真实可演示的完整闭环，而不是继续堆模块。

### 推荐最小 demo

任务：

```text
派 robot-01 去二楼搜索受困人员
```

完整链路：

```text
MainAgent 接收命令
-> 查询 robot-01 在线
-> 生成 StructuredRobotTask
-> 下发给 robot-01 FireClawGateway
-> RobotAgentCore 接收结构化任务
-> SafetyGate 检查
-> 执行 navigate_to_floor
-> 执行 search_for_victims
-> 执行 report_status
-> EventStream 返回进度
-> MainAgent 控制台显示状态
-> Memory 写入任务结果
```

### 具体步骤

1. 使用 simulator adapter 先跑通：

```bash
.venv/bin/python -m fireclaw_core.gateway \
  --adapter simulator \
  --robot-id robot-01 \
  --port 8765
```

2. 上位机提交任务：

```bash
curl -X POST http://127.0.0.1:8765/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "structured_task": {
      "task_id": "task-001",
      "task_type": "rescue_search",
      "target": {"floor": 2},
      "required_skills": [
        "navigate_to_floor",
        "search_for_victims",
        "report_status"
      ],
      "constraints": {},
      "priority": "normal",
      "risk_level": "low"
    }
  }'
```

3. 查询任务状态：

```bash
curl http://127.0.0.1:8765/tasks/task-001
```

4. 查看事件流：

```bash
curl http://127.0.0.1:8765/events
```

5. 验证 memory 是否写入任务结果。

### 产出

- 一个稳定可演示 demo；
- 一个最小闭环测试；
- 后续真实 ROS1 接入的基础。

---

## Phase 9：ROS1 真实机器人接入

目标：让 FireClaw 不只停留在 dry-run / simulator，而是具备真实 ROS1 执行能力。

### 具体步骤

1. 明确第一批真实 ROS action / service / topic：

```text
navigate_to_floor -> move_base action
search_for_victims -> perception service / topic
report_status -> operator report topic
emergency_stop -> std_srvs/Trigger
```

2. 编写 robot-specific YAML：

```yaml
robot_id: robot-01
namespace: /fireclaw/robot-01

transport:
  enabled: true
  wait_for_server_seconds: 5.0
  wait_for_result_seconds: 30.0

remap:
  navigate_to_floor:
    profile: move_base
    name: /move_base
    goal_template:
      target_pose:
        header:
          frame_id: map
        pose:
          position:
            x: "{{ targets.floor_${floor}.x }}"
            y: "{{ targets.floor_${floor}.y }}"
            z: 0.0
          orientation:
            yaw: "{{ targets.floor_${floor}.yaw }}"

  emergency_stop:
    profile: trigger_service
    name: /fireclaw/emergency_stop

targets:
  floor_2:
    frame_id: map
    x: 12.4
    y: -3.8
    yaw: 1.57
```

3. 先做 ROS1 mock / turtlesim smoke test。

4. 再接 Gazebo。

5. 最后接真实机器人。

### 验证顺序

```text
unit test
-> simulator test
-> mock ROS1 test
-> turtlesim smoke test
-> Gazebo test
-> real robot dry-run
-> real robot limited live run
```

### 产出

- ROS1 adapter 可用；
- move_base 任务能被 FireClaw 调用；
- 事件流能返回 action feedback；
- cancel 和 emergency stop 能真正生效。

---

## Phase 10：记忆系统从日志升级为任务经验

目标：让 memory 不只是记录 JSONL，而是服务于后续任务规划。

### 建议记忆分层

```text
短期任务状态：当前任务、当前目标、上一步失败原因
长期经验记忆：某楼层难以通行、某机器人热成像不稳定
地图/环境记忆：房间、楼层、危险区域、可通行路径
技能经验：哪个技能在什么场景下容易失败
操作员偏好：消防员/指挥员的任务习惯
```

### 具体步骤

1. 保留当前 JSONL memory。

2. 新增结构化 memory schema：

```python
@dataclass(frozen=True)
class TaskMemoryRecord:
    record_id: str
    robot_id: str
    task_type: str
    target: dict[str, Any]
    status: str
    failure_reason: str | None
    learned_hint: str | None
    created_at: str
```

3. 任务失败时记录：

```text
失败 skill
失败原因
机器人状态
环境状态
是否可重试
操作员修正
```

4. 下一次规划时检索相关记忆：

```text
相同楼层
相同任务类型
相同机器人
相同失败原因
```

5. 将检索结果提供给 MainAgent / RobotAgent 的 planner。

### 产出

- 任务经验记忆；
- 失败经验复用；
- 操作员修正可影响后续规划。

---

## Phase 11：人类操作员控制台

目标：让消防员或实验人员能直接从上位机下发任务和查看反馈。

### 建议功能

```text
机器人列表
机器人状态
任务输入框
任务确认
任务取消
急停按钮
事件流显示
任务历史
失败原因
人工修正记录
```

### 最小 CLI 版本

```bash
fireclaw mission submit "派 robot-01 去二楼搜索受困人员"
fireclaw mission state
fireclaw mission trace mission-001
fireclaw mission cancel mission-001
fireclaw mission emergency-stop --reason "operator request"
```

### 后续 Web UI 版本

```text
左侧：机器人列表
中间：任务流
右侧：当前任务 trace
底部：自然语言输入框
顶部：急停按钮
```

### 产出

- 操作员可用入口；
- 不再只靠 curl；
- 更适合演示和答辩。

---

## 7. 建议的代码提交顺序

不要一次性大改。建议拆成小 PR 或小 commit：

```text
Commit 1: docs: clarify FireClaw RobotAgent architecture
Commit 2: refactor: add RobotAgent/MissionCoordinator aliases
Commit 3: feat: add structured robot task contract
Commit 4: feat: support RobotAgent structured task execution
Commit 5: fix: handle unknown robot and environment state in safety gate
Commit 6: feat: validate skill input and output schemas
Commit 7: feat: add robot selector and mission plan validator
Commit 8: feat: add simulator structured-task demo
Commit 9: feat: improve ROS1 adapter state and remap validation
Commit 10: docs: update README with new architecture and demo
```

---

## 8. 最终项目表达建议

建议对外这样描述 FireClaw：

> FireClaw 是一个面向救援机器人的单机具身 Agent 框架。每台机器人运行一个常驻 RobotAgent，具备结构化任务理解、技能调用、安全门控、ROS 执行、执行反馈和任务记忆能力。上位机运行 MissionCoordinator，用于接收消防员自然语言任务、选择在线机器人并下发结构化任务，但不直接控制机器人硬件。该架构借鉴 OpenClaw 的 Agent / Skill / Memory / Subagent 思路，并针对现实机器人任务引入安全门控、结构化任务协议和 ROS 适配边界。

---

## 9. 当前最优先要做的三件事

### 第一优先级：结构化任务协议

不要继续让上位机给机器人下发自然语言。先把 `StructuredRobotTask` 做出来。

### 第二优先级：机器人端结构化执行入口

给 `FireClawAgent` 增加：

```python
run_structured_task()
```

让它能直接执行结构化任务。

### 第三优先级：修正 ROS1 状态 unknown 问题

不要让真实 ROS1 adapter 的占位值把 SafetyGate 误导成低电量或不可达。

---

## 10. 一句话总结

FireClaw 不应该被重构成一个大而全的多机器人平台，而应该被收敛为：

```text
上位机 MissionCoordinator 调用多个已注册在线的 FireClaw RobotAgent。
每个 RobotAgent 才是真正的单机器人具身 Agent。
```

这条主线更清楚，也更适合后续工程实现、实验演示、项目汇报和论文包装。
