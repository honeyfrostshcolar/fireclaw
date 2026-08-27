# FireClaw 与 OpenClaw 对齐架构

## 目的

这份文档是 FireClaw 对齐 OpenClaw 的工程架构基线。

目标不是逐行复制 OpenClaw，而是复用 OpenClaw 已经验证过的 agent、session、gateway、task、subagent、permission、memory、provider、plugin 等边界，然后针对消防机器人做工程化改造：

- 物理安全；
- 机器人本地自主权；
- 弱通信和断连场景；
- 可审计任务执行；
- 仿真与真机隔离；
- ROS / 机器人 SDK 集成；
- 多机器人协同。

## 目标系统形态

FireClaw 包含两种常驻 Agent：mission-level 的 Mission Coordinator 和多个
已注册的具身 Robot Agent。`subagent` 只表示未来可选的临时委派 worker，
不再作为机器人的架构名称。完整规范见
[`fireclaw-agent-terminology.md`](fireclaw-agent-terminology.md)。

```text
Operator
-> FireClaw Mission Coordinator
   -> Registered Robot Agent A
   -> Registered Robot Agent B
   -> Registered Robot Agent C
      -> Local Gateway
      -> Local Planner / Safety Gate / Skills
      -> ROS / Simulator / Robot SDK Adapter
```

Mission Coordinator 负责任务级推理与协调。每个 Robot Agent 拥有本机具身
执行权限。Mission Coordinator 不能绕过 Robot Agent 直接调用 ROS topic、
service、action、电机、水炮、机械臂或其他硬件接口。

## OpenClaw 概念映射

| OpenClaw 概念 | FireClaw mission 层 | FireClaw Robot Agent 层 | 当前 FireClaw 状态 |
|---|---|---|---|
| Agent | `MissionAgent` / Mission Coordinator | `FireClawAgent` / Robot Agent | 两种常驻 Agent 都已有实现。 |
| Session | mission/operator session | 机器人本地 task/session context | 机器人本地 session 已有；mission session 目前主要通过 mission ID 隐式表达。 |
| Subagent | 可选的临时委派 worker | 可选的本地诊断或推理 worker | 不是 Robot Agent 的架构名称。 |
| Gateway/control plane | 未来的 mission Gateway/API/CLI | `FireClawGateway` | 机器人本地 Gateway 已有；mission CLI 已有；mission HTTP API 缺失。 |
| Task registry | `JsonlMissionRegistry` | `JsonlTaskQueue` | 两层 registry/queue 都已有。 |
| Task runtime progress | mission trace aggregation | event ledger、task trace、action feedback | 机器人 trace 已有；mission trace 聚合已有；mission live stream 缺失。 |
| Permissions/scopes | mission-level authorization scopes | 机器人本地 operator authorization | 两层都有基础实现。 |
| Tool policy pipeline | mission 委派和 fleet 约束 | 有序 capability 投影与执行准入 | 已用一条可审计链统一身份、任务授权、插件所有权、机器人 profile、实时状态、安全和具体动作授权。 |
| Safety/sandbox | mission 调用策略和 Robot Agent 契约 | safety gate、emergency stop、ROS transport gating | 机器人本地安全已有；mission failure policy 还不完整。 |
| Memory | mission memory 和 fleet lessons | 机器人本地 task/environment memory | 机器人本地 memory 已有；mission memory 还没有完整设计。 |
| Provider runtime | mission-level model selection | 机器人本地/边缘模型 fallback | 已有共享 `ProviderRuntime` 和 fallback。 |
| Agent Harness | Mission prompt 和任务图语义解析 | Robot prompt 和单步操作语义解析 | 两种角色统一经过 `ProviderAgentHarness`，不再直接调用模型。 |
| Plugins/Skills/Tools | Mission Skill 组织规划 Tools | Robot Skill 组织导航、感知、控制等原子 Tools | 统一 `FireClawPluginHost` 管理 Plugin contribution；当前 `SkillRegistry` 是可执行 Tool 的 legacy compatibility projection。 |
| Config/doctor/onboarding | fleet 和 mission config 检查 | robot ROS/skill/config 检查 | robot doctor 已有；fleet doctor 缺失。 |

## 分层职责

### 1. Operator Interface Layer

职责：

- 接收自然语言 mission command；
- 展示 mission 状态、subtask 状态、安全阻断和确认请求；
- 允许 mission submit、trace、cancel 和后续 approval 操作；
- 把面向人的输出和机器可读 trace 分开。

当前实现：

- MissionGateway 与 Web Console 已提供封存预览、一次性显式确认、权威事件流、trace 与 cancel。
- `fireclaw mission` 使用同一 preview/confirm 合同；旧 `plan-mission` 直接下发命令现在 fail closed。
- `submit-subtask` 仅保留为显式 atomic Tool 的高级诊断入口，不是自然语言 mission 提交路径。

缺失：

- 基于物理停止证据的确认与正式两阶段恢复 UI；
- 当前操作员显式确认之外的多操作员审批策略；
- 已完成的多机器人 operator 可用性验证。

### 2. Mission Coordinator Layer

职责：

- 维护 mission-level 状态；
- 把 mission plan 分解为机器人 subtask；
- 检查 fleet presence；
- 执行 mission-level authorization；
- 向已注册的 Robot Agent 提交 subtask；
- 通过传播 cancel 请求取消 mission；
- 把机器人本地 trace 聚合成 mission trace。

当前实现：

- `MissionAgent`
- `MissionPlanner`
- `JsonlMissionRegistry`
- 基于 `ControlPolicy` 的 mission-level authorization
- 基于 `RobotSubagentClient.check_presence(...)` 的 fleet presence check

缺失：

- 遵守 `MissionPlan.execution_group` 的 execution scheduler；
- 处理 denied、offline、failed、timeout、unreachable subtask 的 mission failure policy；
- mission-level event stream；
- mission memory 读写路径；
- mission HTTP Gateway。

### 3. Fleet/Robot Agent Contract Layer

职责：

- 存储已知 Robot Agent；
- 描述机器人能力、区域、启用状态和在线状态；
- 通过稳定 API 调用机器人本地 Gateway；
- 让 mission planning 不关心底层 transport 细节。

当前实现：

- `RobotRegistry`
- `RobotRegistryEntry`
- `RobotSubagentClient`
- 针对 state、submit、trace、cancel、presence 的基础 HTTP 调用。

`RobotSubagentClient` 是为了兼容保留的 legacy identifier。它实际调用的是
已经注册并在线的物理或仿真 Robot Agent。Mission Coordinator 不会生成机器人，
只会选择和调用常驻 Robot Agent。

缺失：

- fleet config validation / doctor；
- heartbeat freshness threshold policy；
- robot pairing 或 enrollment flow；
- retry/backoff/circuit-breaker 行为；
- HTTP 之外的 transport abstraction。

### 4. Robot Agent Control Plane

职责：

- 接收机器人本地任务；
- 执行机器人本地 authorization 和 safety；
- 运行本地 `FireClawAgent`；
- 持久化 task queue 状态；
- 暴露 task trace、events、state、cancel、emergency stop。

当前实现：

- `FireClawGateway`
- `JsonlTaskQueue`
- `EventLedger`
- 机器人本地 authorization
- emergency stop
- async task execution 和 cancellation
- `BoundedAgentLoop` 的 append-only checkpoint/resume；
- Gateway 重启后按结构化任务合同和 checkpoint 自动恢复；
- 物理技能使用稳定 `operation_id` 记录 dispatch started/finished，结果未知时
  升级且禁止自动重放。

缺失：

- SSE/WebSocket 或同类 event streaming endpoint；
- 更强的 queue compaction / retention policy；
- 多个可恢复任务超过本机并发容量时的持久化等待队列；
- 真实部署认证。

### 5. Robot Agent, Planner, Tool, and Skill Runtime

职责：

- 理解机器人本地命令；
- 验证 Tool schema 和 precondition；
- 执行 Tool 和 robot action；
- 上报 action feedback 和 terminal outcome；
- 保持 planner / skill / adapter 分离。

当前实现：

- `FireClawAgent`
- 本地 planner 和 safety gate
- manifest-first Plugin discovery，以及公开 typed Tool / physical Tool
  contract；进程 Tool 必须由 Plugin 注册并经过 deployment policy 与
  `ComputerSandbox`；
- 参考 OpenClaw `defineToolPlugin/registerTool` 的声明式
  `PhysicalSkillPlugin` 和通用 `SkillRegistry.register_plugin()` legacy
  compatibility API，用于原子 Tool；
- 对齐 OpenClaw 的 `SKILL.md` 语义，用于能够组织多个 Tool 的 Agent 工作流；
- 参考 OpenClaw plugin API/registration transaction 的
  `FireClawPluginHost`，统一 tool、物理 capability、hook、service、
  context engine 和 Agent Harness 的所有权及生命周期；
- Mission 与 Robot LLM 路径共享 `ProviderAgentHarness`，统一上下文预算、
  tool schema、provider 调用、取消、错误分类和基础 tool-call 校验；
- 插件驱动的 tool schema、任务目标绑定、防篡改、安全分类、资源、
  完成证据和操作员投影；
- 只调度显式 Plugin handler、无按 Tool 名分支且无 Adapter 方法回退的
  `RobotActionRuntime`；
- SQLite WAL 权威运行时存储、版本化任务写入和事务内 task/event 提交；
- 绑定具体命令、结构化任务、机器人和 skill 输入哈希的短期签名执行授权；
- 参考 OpenClaw 有序 tool policy 的统一 capability policy pipeline，同时负责
  规划期 tool 投影和执行期准入，并记录逐阶段审计证据；
- 执行器强制获取插件声明的持久化机器人资源租约，急停先关闭资源准入；
- robot adapter boundary
- action feedback 和 cancellation event handling

缺失：

- 面向更多消防任务的机器人本地 planner；
- 更多真实机器人能力的 typed Tool contracts 和完整 Skills；
- 生产部署中由外部认证系统签发的 actor identity 和 scope claims；
- 第三方物理 Plugin 的签名和独立进程隔离；
- success evidence 元数据到通用完成验证器的完整接线。

### 6. Robot Adapter and ROS Integration Layer

职责：

- core `RobotAdapter` 只负责状态、环境观测和 emergency stop；
- 每个领域 Plugin 自己负责 ROS/SDK 转换、feedback、timeout、cancel 和终态映射；
- 在 live transport 前强制显式 deployment 与 Plugin 配置。

当前实现：

- 用于状态、sensor discovery 和 emergency stop 的 `Ros1RobotAdapter`；
- 作为 core transport 基础设施的 `Ros1Transport`、`ros1_config` 和
  `ros1_template`；
- Navigation Plugin 自己的 `Ros1MoveBaseBackend`；
- mock、simulator、dry-run 和 ROS1 Adapter modes。

缺失：

- 当前架构的 Gazebo 与实机验收记录；
- 跨所有 Plugin Adapter 的完整物理 deadline 强制；
- 真实 ROS2 core Adapter 与 Nav2 Plugin。

### 7. Memory and Audit Layer

职责：

- 记录 mission、task、trace、observation、outcome、operator correction 和可复用经验；
- 保持机器人本地 incident log 可审计；
- 支持 mission-level retrieval，同时不隐藏机器人本地 source of truth。

当前实现：

- agent run JSONL memory store；
- `EventLedger`；
- `JsonlTaskQueue`；
- `JsonlMissionRegistry`。

缺失：

- first-class mission memory model；
- 按 mission、robot、location、capability、outcome、operator 的 retrieval filters；
- cross-robot lessons；
- retention 和 sensitive-log policy。

### 8. Model and Provider Runtime Layer

职责：

- 为 mission planning 和机器人本地 reasoning 选择 LLM/provider backend；
- 当模型不可用时支持 deterministic fallback；
- 隔离 provider-specific request/response 行为。

当前实现：

- 当前主要依赖 deterministic planner 和 Python 逻辑。
- 尚无独立 provider runtime。

缺失：

- model provider abstraction；
- 面向 mission planner 的 tool-calling runtime；
- prompt/runtime configuration；
- 可 replay 的 LLM traces，用于测试；
- rule-based planning fallback。

## 核心数据流

### Mission Planning and Execution

```text
operator command
-> mission authorization
-> fleet presence check
-> mission planning
-> mission registry record
-> subtask submission to registered Robot Agents
-> robot-local task queue and execution
-> robot-local event/task traces
-> mission trace aggregation
-> operator status
```

### Robot-Local Execution

```text
subtask request
-> robot-local authorization
-> safety gate
-> planner
-> skill runtime
-> robot adapter
-> ROS/simulator/SDK call
-> feedback/cancel/result
-> event ledger and task queue
-> trace response to Mission Coordinator
```

### Cancellation

```text
operator cancel mission
-> mission authorization
-> mission registry lookup
-> skip terminal subtasks
-> call Robot Agent cancel endpoints
-> robot-local cancel event
-> action runtime cancellation
-> ROS action cancel_goal when applicable
-> mission registry status update
```

## 当前构建状态

2026-06-08 最近一次记录的验证状态：

- branch: `master`，领先 `origin/master` 8 个提交；
- latest commit: `4cf1120 feat: add fleet heartbeat v1 with presence-based robot filtering`；
- verification: `.venv/bin/python -m pytest -q` -> `265 passed in 15.22s`。

当时存在的未跟踪 planning/config artifacts：

- `.claude/`
- `CLAUDE.md`
- `CLAUDE.zh-CN.md`
- `docs/superpowers/plans/2026-06-08-fleet-heartbeat-v1.md`
- `docs/superpowers/plans/2026-06-08-mission-authorization-v1.md`
- `docs/superpowers/plans/2026-06-08-mission-planner-v1.md`

## 框架完成路线图

### Phase 1: 稳定 Mission Coordinator/Robot Agent 契约

目标：让当前架构真正表现为一个一致的多机器人框架。

任务：

- Mission Scheduler v1：按顺序执行 `MissionPlan.execution_group`。
- Mission failure policy：定义 stop、continue、retry、reassign、escalate。
- Mission trace stream：从机器人本地 trace 暴露 live mission progress。
- Fleet doctor：验证 registry、robot reachability、capabilities 和 ROS config coverage。

这一阶段应该先于增加更多任务类型，因为它让核心控制闭环可靠。

### Phase 2: 完成机器人本地具身运行时

目标：让每个 Robot Agent 成为可信的本地具身 agent。

任务：

- 更丰富的消防 skill metadata；
- typed skill input/output contracts；
- adapter-specific capability declarations；
- 使用真实 ROS master 的 ROS1 smoke tests；
- simulator / real-robot separation checks；
- 更强的 local failure taxonomy。

### Phase 3: 增加 Mission Memory 和 Operator Workflow

目标：让 mission 可恢复、可检查，并能跨运行积累经验。

任务：

- mission memory records；
- cross-robot event aggregation；
- operator correction recording；
- 高风险 mission 操作 approval workflow；
- 基于 mission trace 和 robot-local trace 的 incident replay。

### Phase 4: 增加 Model Provider Runtime

目标：引入 LLM planning，但不让框架正确性依赖模型质量。

任务：

- provider abstraction；
- tool-calling mission planner；
- prompt/runtime configuration；
- replayable LLM traces；
- deterministic fallback 到 rule-based planning。

### Phase 5: 部署加固

目标：为真实机器人或高保真仿真部署做准备。

任务：

- authentication 和 signed operator approvals；
- robot pairing / enrollment；
- heartbeat freshness 和 degraded network policy；
- queue retention 和 log redaction；
- deployment config examples；
- robot control endpoints 安全审查。

## 近期工程优先级

下一步工程任务应该是 `Mission Scheduler v1`。

原因：

- planner 已经生成 `execution_group`；
- mission agent 已经能 submit、cancel、authorize、check presence 和 aggregate traces；
- 如果没有 scheduler，多机器人计划只是部分实现；
- scheduler 是定义 failure policy、retry、reassign、escalation 的自然位置。

最低验收标准：

- 同一个 execution group 的 subtasks 能作为一个 scheduling batch 提交；
- 后续 groups 只有在前序 groups 到达可接受状态后才启动；
- denied / offline / failed / cancelled subtasks 会产生明确的 mission-level decision；
- mission registry 会记录 scheduling decisions；
- 测试覆盖多机器人并行计划和单机器人顺序计划。
