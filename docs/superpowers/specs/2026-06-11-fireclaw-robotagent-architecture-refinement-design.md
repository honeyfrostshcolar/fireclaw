# FireClaw RobotAgent Architecture Refinement Design

日期：2026-06-11

## 目标

本设计用于吸收外部工程评审中有价值的部分，同时避免把 FireClaw 重构成新的大平台。核心目标是把 FireClaw 的研究和工程主线重新收敛为：

```text
上位机 MissionCoordinator 负责理解消防员任务、选择在线机器人、下发结构化任务。
每台机器人运行常驻 RobotAgent，负责本机安全门控、技能执行、ROS/仿真适配、事件流和本机记忆。
```

FireClaw 现有 OpenClaw-style 模块已经覆盖 mission registry、task registry、subagent registry、provider runtime、plugin hooks、approval runtime、memory retrieval、proof bundle 和 validation sidecar。下一阶段不应重新设计这些模块，而应补齐上位机到机器人之间最薄弱的协议边界：结构化机器人任务、机器人端结构化执行入口、unknown-state 安全语义，以及 Gazebo/ROS1 proof gate。

## 外部建议筛选

### 采纳

1. **RobotAgent 作为核心研究对象**
   - 采纳“每台机器人常驻一个 FireClaw RobotAgent”的表述。
   - 上位机是 MissionCoordinator，不直接控制硬件。
   - 多机器人能力是多个 RobotAgent 的协调，不是动态生成物理机器人。

2. **结构化任务协议**
   - 当前 `MissionAgent.submit_subtask()` 通过 `RobotSubagentClient.submit_task()` 主要发送 `command` 字符串。
   - 机器人端 `FireClawGateway.submit_agent()` 再调用 `FireClawAgent.run(command)` 做自然语言解析。
   - 这会导致“上位机理解一次，机器人端再理解一次”的语义误差。
   - 新增 `StructuredRobotTask`，但保留 `command` 兼容路径。

3. **机器人端结构化执行入口**
   - 保留 `FireClawAgent.run(command: str)` 作为本地调试和自然语言 fallback。
   - 新增 `FireClawAgent.run_structured_task(task: StructuredRobotTask)` 作为上位机正式下发路径。
   - `run_structured_task()` 不再依赖自然语言 planner，而是将 `required_skills` 转换成 `PlanStep`。

4. **unknown-state 安全语义**
   - ROS1 真实适配器不能用 `0.0` 或 `[]` 表示未知状态。
   - `RobotState.battery_percent`、`RobotState.available_sensors`、`EnvironmentState.reachable_floors` 等字段需要允许 `None`。
   - `SafetyGate` 必须区分 `None=未知`、`0.0=明确为 0`、`[]=明确为空`。

5. **确定性校验边界**
   - LLM planner 可以提出候选计划，但结构化任务和 mission plan 必须经过确定性校验。
   - 校验内容包括 robot 是否存在、capability 是否匹配、risk_level 是否合法、required_skills 是否为空、target floor 是否可解释。

### 不采纳

1. **一次性重命名核心类**
   - 不重命名 `MissionAgent`、`FireClawGateway`、`RobotSubagentClient`。
   - 这些类已经有大量测试、文档和 OpenClaw 对齐语义。
   - 只在文档中建立术语映射，必要时增加轻量 alias。

2. **重做 memory、skill、operator console**
   - memory retrieval/eval、skill metadata、approval、serve、proof bundle 已有实现。
   - 新计划只增强这些模块与结构化任务的连接，不另起新系统。

3. **把当前目标扩成全量 ROS2/Web/dashboard 平台**
   - 当前目标仍是 ROS1/Gazebo 优先。
   - Web UI、ROS2 native adapter、插件市场、完整 dashboard 都不进入本轮。

## 术语映射

| 研究/架构术语 | 当前代码实体 | 说明 |
|---|---|---|
| MissionCoordinator | `MissionAgent` + `MissionGateway` | 上位机任务协调、机器人选择、mission lifecycle、operator approval。 |
| RobotAgent | `FireClawAgent` + `FireClawGateway` | 机器人本地常驻具身 agent 和 HTTP control plane。 |
| RobotAgentClient | `RobotSubagentClient` | 上位机调用机器人 Gateway 的 HTTP client。保留 subagent 名称用于 OpenClaw 对齐，但文档强调它是物理机器人 agent。 |
| StructuredRobotTask | 新增 `task_contract.py` | 上位机到机器人端的正式任务协议。 |
| RobotAdapter | `RobotAdapter` / `Ros1RobotAdapter` / simulator adapters | ROS、仿真、SDK 与 FireClaw 的边界。 |

## 目标架构

```text
消防员自然语言命令
-> MissionGateway / fireclaw mission
-> MissionAgent(MissionCoordinator)
-> MissionPlanner / LLMMissionPlanner
-> MissionPlanValidator
-> StructuredRobotTask
-> RobotSubagentClient
-> FireClawGateway(RobotAgentGateway)
-> FireClawAgent.run_structured_task()
-> SafetyGate
-> PlanExecutor / SkillRuntime
-> RobotAdapter
-> ROS1 / Gazebo / simulator / hardware
-> EventLedger / mission events / task-flow / memory / proof bundle
```

## 结构化任务协议

新增文件：

```text
src/fireclaw_core/task_contract.py
```

核心数据结构：

```python
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
```

配套函数：

```python
def structured_task_from_mission_subtask(
    *,
    mission_id: str,
    subtask: MissionSubtask,
    operator_id: str | None = None,
    task_id: str | None = None,
) -> StructuredRobotTask:
    ...

def validate_structured_robot_task(task: StructuredRobotTask) -> list[str]:
    ...

def planning_result_from_structured_task(task: StructuredRobotTask) -> PlanningResult:
    ...
```

设计约束：

- `command` 保留为人类可读说明，不再是机器人端正式执行依据。
- `required_skills` 是机器人端执行计划的来源。
- `target.floor` 会映射到 `PlanningResult.target_floor` 和每个需要 floor 的 `PlanStep.inputs`。
- `task_type` 使用低耦合字符串，不引入复杂枚举，避免早期扩展受阻。
- `risk_level` 限定为 `low | medium | high | critical`。
- `priority` 限定为 `low | normal | high | emergency`。

## 数据流变更

### 当前兼容路径

```text
MissionSubtask.command
-> RobotSubagentClient.submit_task(command=...)
-> POST /tasks {"command": "..."}
-> FireClawGateway.submit_agent(command)
-> FireClawAgent.run(command)
```

该路径保留，用于调试、兼容旧测试、自然语言本机入口。

### 新正式路径

```text
MissionSubtask
-> structured_task_from_mission_subtask()
-> RobotSubagentClient.submit_task(command=..., structured_task=...)
-> POST /tasks {"command": "...", "structured_task": {...}}
-> FireClawGateway.submit_agent(..., structured_task=...)
-> FireClawAgent.run_structured_task(task)
```

## SafetyGate unknown-state 语义

`RobotState` 和 `EnvironmentState` 改为允许 unknown：

```python
@dataclass
class RobotState:
    battery_percent: float | None
    current_floor: int | None
    available_sensors: list[str] | None

@dataclass
class EnvironmentState:
    reachable_floors: list[int] | None
    hazards: list[str] | None = None
    victims_by_floor: dict[int, int] | None = None
```

`SafetyDecision` 增加 warnings：

```python
@dataclass(frozen=True)
class SafetyDecision:
    status: str
    reasons: list[str]
    warnings: list[str] = field(default_factory=list)
```

状态策略：

- `battery_percent is None`：真实机器人非 dry-run 时 `require_confirmation`，dry-run 时 warning。
- `battery_percent < 10.0`：block。
- `available_sensors is None`：涉及 required sensor 时 `require_confirmation` 或 block，取决于 dry-run 和 risk。
- `reachable_floors is None`：真实机器人移动任务需要 confirmation，simulator/dry-run warning。
- `reachable_floors == []`：明确无可达楼层，block。

## Mission Plan Validator

新增轻量 validator，不做全量调度重构：

```text
src/fireclaw_core/mission_plan_validator.py
```

职责：

- 校验 `MissionPlan.subtasks` 非空。
- 校验每个 `robot_id` 在 registry 中存在且 enabled。
- 校验 `capability_required` 与 robot capabilities 匹配。
- 校验 `floor` 是正整数。
- 校验 `execution_group` 非负。
- 校验转换后的 `StructuredRobotTask` 没有 schema 错误。

该 validator 在 `MissionAgent.plan_mission()` 或提交前运行。LLM planner 输出不直接获得执行权。

## Gazebo/ROS1 验证主线

结构化任务协议完成后，下一步不是增加更多平台功能，而是建立证明链：

```text
fireclaw serve
-> fireclaw mission
-> MissionGateway
-> RobotSubagentClient
-> FireClawGateway(adapter=ros1, ros1_config=gazebo)
-> Ros1Transport
-> Gazebo ROS nodes
-> proof bundle
```

本轮架构 refinement 的验收重点：

- simulator structured-task e2e 通过；
- existing command path 仍通过；
- SafetyGate unknown-state tests 通过；
- embodied eval/proof bundle 可记录 structured task metadata；
- ROS1/Gazebo runbook 指向结构化任务路径。

## 非目标

- 不实现完整 Web dashboard。
- 不实现 ROS2 native adapter。
- 不引入第三方 plugin marketplace。
- 不重写已有 memory system。
- 不移除 `command` 兼容路径。
- 不把所有 OpenClaw `subagent` 命名从代码中删除。

## 研究影响

该 refinement 对论文/项目叙事有实质价值：

- 使研究对象从“多机器人调度平台”收敛为“单机器人具身 Agent + 上位机协调”。
- 减少自然语言二次解析导致的不可控误差。
- 将安全论证从字段检查推进到 unknown-state 语义和结构化任务门控。
- 为 Gazebo/ROS1 实验提供更清晰的可复现 proof gate。

但它本身不是新的算法贡献。若面向高水平论文，还需要后续实验支持：

- 结构化协议相比自然语言二次解析的失败率下降；
- unknown-state safety gate 在 ROS1/Gazebo 场景中的拦截/确认有效性；
- memory retrieval 对重复救援任务的改进；
- 与无 RobotAgent 本地门控的 central-control baseline 对比。
