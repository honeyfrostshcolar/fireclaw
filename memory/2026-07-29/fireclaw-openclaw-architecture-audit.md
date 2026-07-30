# FireClaw / OpenClaw 架构复审

## 2026-07-29 17:10:36 +08

### 任务目标

在上一轮物理 Skill 解耦工作的基础上，重新从宿主架构层面审计
FireClaw，并与仓库内 `openclaw/` 的成熟实现逐项比较。目标不是把
OpenClaw 原样复制到机器人端，而是找出 FireClaw 中没有复用其成熟
结构、已经出现重复实现或不适合真实机器人运行的边界。

### 用户已确认的方向

- 中央 Mission Agent 与常驻 Robot Agent 是两个角色和部署单元，不再称
  Robot Agent 为中央 Agent 临时派生的子 Agent。
- 两者应共享 Agent 宿主、模型调用、上下文、工具投影、策略和审计等通用
  内核，但不共享相同权限和物理能力集合。
- 当前导航只支持单层内的点导航；跨楼层能力可以保留未来扩展接口，但不应
  进入当前默认能力和规划链。
- 物理算法应由 Skill/Tool 插件和 Robot Adapter 承载，不能把导航、搜救等
  具体算法或能力名继续写进 Agent 核心。

### 当前工作区状态

本轮开始时 `master` 与 `origin/master` 对齐，但有上一轮尚未提交的物理
Skill 插件改动。审计没有撤销或修改这些业务文件。

未提交改动包括：

- `src/fireclaw_core/execution/skill_plugin.py`
- `src/fireclaw_core/execution/builtin_physical_skills.py`
- `src/fireclaw_core/execution/skills.py`
- `src/fireclaw_core/agent/agent.py`
- `src/fireclaw_core/agent/robot.py`
- `src/fireclaw_core/agent/robot_agent.py`
- `src/fireclaw_core/execution/action_runtime.py`
- `src/fireclaw_core/execution/executor.py`
- `src/fireclaw_core/gateway/gateway.py`
- Safety、ROS、task contract、文档和对应测试等文件

上一轮记录报告全量测试结果为 `1805 passed, 6 deselected`。本轮只做架构
审计和记录，没有业务代码改动，因此没有重复运行测试。

### 已检查的 OpenClaw 对应结构

#### 统一插件宿主

- `openclaw/src/plugins/plugin-api.types.ts`
  - `OpenClawPluginApi`
  - `registerTool`
  - `registerContextEngine`
  - `registerCompactionProvider`
  - `registerAgentHarness`
  - `registerService`
  - `registerGatewayMethod`
- `openclaw/src/plugins/registry.ts`
  - `createPluginRegistry`
- `openclaw/src/plugins/registry-api.ts`
  - `createPluginApiFactory`
- `openclaw/src/plugins/plugin-registration-transaction.ts`
  - 注册事务、commit 和 rollback
- `openclaw/src/plugins/loader-records.ts`
  - `PluginRecord`、状态、失败阶段和诊断
- `openclaw/src/plugins/tools.ts`
  - `resolvePluginTools`
  - allow/deny、名称冲突、延迟加载、畸形工具隔离

OpenClaw 的关键结构不是“每种扩展各有一个 registry”，而是插件入口拿到
一个宿主注入的 API，通过同一注册事务贡献 tool、hook、service、context
engine、agent harness 等能力。宿主统一记录所有权、状态、冲突、失败和生命
周期。

#### Agent Harness 与工具策略

- `openclaw/src/agents/harness/types.ts`
  - `AgentHarness`
  - `supports`
  - `runAttempt`
  - `finalizeSettledTurn`
  - `compact`
  - session reset/dispose/fork/auth 等生命周期能力
- `openclaw/src/agents/harness/registry.ts`
  - harness 注册、所有权和清理
- `openclaw/src/agents/harness/selection.ts`
  - provider/model/runtime 兼容性选择
- `openclaw/src/agents/tool-policy-pipeline.ts`
  - 分层工具策略、过滤前后结果和审计

OpenClaw 的 Harness 是一次模型/工具回合的统一宿主边界，不只是一个 while
循环。provider route、auth、tools、context、transcript、abort、compaction、
result classification 和 lifecycle 都在该边界内组织。

#### 会话并发和生命周期

- `openclaw/src/sessions/session-lifecycle-admission.ts`
  - `runExclusiveSessionLifecycleMutation`
  - 按规范化 session identity 排序取得锁
  - admission、abort 和 active mutation 追踪
- `openclaw/src/gateway/server-methods/sessions-mutations.ts`
  - 锁内重新读取当前 row
  - `expectedSessionId` / `expectedLifecycleRevision`
  - 拒绝对已经替换的 session 进行 stale write

OpenClaw 自身仍使用本地优先存储，但其权威生命周期更新不是无条件
read-modify-append。

### 已检查的 FireClaw 对应结构

- `src/fireclaw_core/plugin/plugin_runtime.py`
  - `PluginRuntime`
  - 只管理 descriptor 和 `provider` / `memory` / `tool_approval` 三类固定 hook
  - 无效 descriptor 被静默跳过
- `src/fireclaw_core/execution/skills.py`
  - `Skill`
  - `SkillRegistry`
  - `create_default_skill_registry`
- `src/fireclaw_core/execution/skill_plugin.py`
  - `PhysicalSkillPlugin`
  - `PhysicalSkillCatalog`
  - `resource_locks`
- `src/fireclaw_core/infra/workspace_skills.py`
  - `load_workspace_skills`
- `src/fireclaw_core/agent/bounded_loop.py`
  - `BoundedAgentLoop`
- `src/fireclaw_core/planner/llm_planner.py`
  - `LLMMissionPlanner.plan`
  - `LLMMissionPlanner.decide`
- `src/fireclaw_core/agent/robot_agent.py`
  - `LLMRobotAgentPlanner.plan`
- `src/fireclaw_core/agent/robot_deliberation.py`
  - `LLMRobotAgentDecisionPolicy.decide`
- `src/fireclaw_core/context/manager.py`
  - `ModelAwareContextManager`
- `src/fireclaw_core/safety/safety.py`
  - `SafetyGate`
- `src/fireclaw_core/agent/agent.py`
  - `FireClawAgent.run_planning_result`
  - `FireClawAgent.execute_deliberated_step`
- `src/fireclaw_core/task/task_contract.py`
  - `StructuredRobotTask`
- `src/fireclaw_core/task/task_queue.py`
  - `JsonlTaskQueue`
- `src/fireclaw_core/memory/memory.py`
  - `JsonlMemoryStore`
- `src/fireclaw_core/agent/loop_checkpoint.py`
  - `JsonlAgentLoopCheckpointStore`
- `src/fireclaw_core/monitoring/event_ledger.py`
  - `EventLedger`
- `src/fireclaw_core/gateway/gateway.py`
  - `FireClawGateway`
- `src/fireclaw_core/mission/mission_agent.py`
  - `MissionAgent`

### 审计发现

#### P0：审批事实被普通布尔值替代

`SafetyGate` 对所有真实机器人动作以及 `high` / `critical` Skill 要求确认，
但 `FireClawAgent.run_planning_result` 和本地逐步执行路径直接传入
`operator_confirmed=True`。

`StructuredRobotTask` 和 `RobotAgentTaskEnvelope` 只携带 `operator_id`，没有
approval request id、批准者、范围、计划/参数哈希、有效期、nonce 或签名。
仓库虽然已有 `ApprovalRuntimeToken`，它没有贯穿中央任务合同到机器人执行。

因此 Robot Agent 目前无法证明“这个具体动作和参数确实被某个操作员批准”，
只能相信调用者传来的布尔值。这是物理安全边界漏洞，不只是代码风格问题。

#### P0：权威运行状态缺少事务和并发控制

`JsonlTaskQueue.update` 是无锁的 `get -> append`；`JsonlMemoryStore.append`
没有锁和 `fsync`；`EventLedger` 虽然 `fsync`，但与 task、checkpoint、
approval 和 memory 更新彼此独立。`JsonlAgentLoopCheckpointStore` 只有进程内
`RLock`，不能形成跨 store 的原子提交或跨进程 lease。

一次动作可能出现以下不一致：

1. checkpoint 已写为待执行；
2. 机器人动作已经开始；
3. task queue 或 evidence 写入失败；
4. 重启后不同文件对动作状态给出不同答案。

JSONL 可保留为审计导出格式，但不宜继续承担真实机器人权威控制状态。

#### P0：资源锁只是声明，没有执行语义

`PhysicalSkillPlugin.resource_locks` 已声明，例如 `robot_motion`、
`local_navigation`、`perception_pipeline`，但调用图中没有执行器取得、续租
或释放这些锁。Gateway 只用进程内活动任务数量限制，配置提高到 1 以上时，
同一机器人上的动作可能并发进入 Adapter。

需要真正的 per-robot execution lane 和资源 lease，并让 emergency stop 走
独立最高优先级通道。

#### P1：物理 Skill 解耦形成了并行注册系统，而非统一插件宿主

FireClaw 当前至少有：

1. `PluginRuntime` 的 descriptor/hook registry；
2. `SkillRegistry` 的可执行 Skill registry；
3. `PhysicalSkillCatalog` 的物理插件 registry；
4. `load_workspace_skills` 的工作区 manifest loader。

上一轮消除了部分 action `if/elif`，但没有把物理 Skill 注册到现有插件宿主。
这意味着新增能力仍需理解并同步多套目录、投影和加载逻辑。OpenClaw 的成熟
做法是一个插件宿主 API 和一个所有权/诊断/lifecycle registry。

#### P1：`BoundedAgentLoop` 共享了循环外壳，但不是统一 Agent Harness

`BoundedAgentLoop` 正确共享了 iteration、timeout、cancel、checkpoint-before-
physical-operation、pending reconciliation 和 fail-safe termination。这部分
是 FireClaw 面向机器人必须保留的优势。

但中央和机器人端仍分别手写：

- messages 和 tool schema 组装；
- provider `chat_completion` 调用；
- tool call 个数和名称解析；
- context fitting；
- trace 和错误映射。

`LLMMissionPlanner.plan/decide`、`LLMRobotAgentPlanner.plan`、
`LLMRobotAgentDecisionPolicy.decide` 是多条模型入口。它们共享的是循环控制，
不是模型/工具回合的宿主。需要在 `BoundedAgentLoop` 下增加统一
`AgentHarness` / `AgentTurnRuntime`。

#### P1：上下文管理不是不可绕过的模型调用边界

Robot Agent 的新 deliberation 路径会调用 `ModelAwareContextManager`，但中央
`LLMMissionPlanner.plan` 仍直接构造 messages 并调用 provider；
`LLMMissionPlanner.decide` 在 `context_envelope is None` 时也绕过 manager。
交互式 `FireClawAgent` 还按“旧记录超过 5 条”自行压缩，而不是统一按模型
token budget 处理。

上下文工程模块本身已经具备有价值的 authoritative / continuity / advisory
分层，但必须由 Harness 在每一次 provider 调用前强制执行，不能依赖调用者
自愿接入。

#### P1：具体能力名仍散落在 Planner、Safety、Mission 和 Adapter 之外

`navigate_to_point`、`navigate_to_floor`、`search_for_victims`、
`rescue_victim` 仍出现在：

- `planner/planner.py`
- `mission/mission_planner.py`
- `mission/active_observation.py`
- `mission/completion_contract.py`
- `mission/mission_agent.py`
- `safety/safety.py`
- `agent/agent.py`
- Gateway 默认配置

Adapter 内出现动作名是合理的实现细节；Planner、Safety 和 Mission 核心按
具体名字分支则仍然违反插件边界。尤其默认物理插件中仍存在
`navigate_to_floor`，与“当前只做单层点导航”的最新产品约束冲突。

应让核心依赖 capability metadata / safety class / evidence contract /
task composition，而不是依赖某个技能名字。

#### P1：权限、工具投影和安全校验不是一条有来源的策略流水线

FireClaw 有 `ControlPolicy`、`PluginPolicy`、`RobotAgentPolicy` 和
`SafetyGate`，但它们由不同调用点手工组合，没有统一的 effective capability
decision，也没有完整记录每一层过滤前后的工具集合和批准来源。

建议顺序为：

1. identity/scope；
2. mission delegation envelope；
3. plugin/tool exposure；
4. robot capability profile；
5. current state and SafetyGate；
6. scoped approval token。

任何一层未知都应 fail closed，并输出可审计 provenance。

#### P2：Gateway 和 MissionAgent 承担过多职责

`FireClawGateway` 约 2131 行，同时负责 HTTP transport、route dispatch、
auth、task workers、stores、memory、telemetry、robot construction 和
emergency stop。`MissionAgent` 约 2458 行，其构造函数接收二十余个服务并
负责规划、快照、主动观测、审批、分发、修订和记忆。

OpenClaw 的 Gateway 本身也很大，因此“文件长”不是结论。真正的问题是
FireClaw 尚无可注册 command handler/service/runtime supervisor 边界，新增
能力仍容易修改组合根和 HTTP `if/elif`。

中央 Gateway 与 Robot Gateway 可以保持不同部署，但应共享 host kernel 和
协议类型，并各自组合 transport、application handlers、runtime supervisor
和 stores。

#### P2：Skill、Tool、Action 和算法绑定仍有概念混合

当前 `Skill` 同时承载：

- LLM tool schema；
- 可执行 handler；
- retry/idempotency；
- safety metadata；
- action binding；
- operator message。

OpenClaw 中 Skill 更接近提示/工作流知识，可执行能力是 Tool。FireClaw 可以
继续在产品层使用“物理 Skill”这个名称，但内部应区分：

- `PhysicalCapabilityDefinition`
- `AgentToolProjection`
- `ActionBinding`
- execution middleware

这样同一物理能力可按中央 Agent、Robot Agent、仿真和真实机器人生成不同的
工具投影及权限，而不复制定义。

### 应保留而不是照搬 OpenClaw 的 FireClaw 设计

- 中央 Mission Agent 和 Robot Agent 是不同权限角色，不应像 OpenClaw 临时
  subagent 一样获得相同工具面。
- 冻结状态快照、belief/evidence 校验和主动观测是具身任务需要的。
- 物理动作前 checkpoint、重启后 reconciliation 必须保留。
- SafetyGate、任务合同、完成证据和急停独立通道必须比 OpenClaw 的普通工具
  执行更严格。
- authoritative context 放不下时调用前阻断，是合理的机器人安全策略。

目标是复用 OpenClaw 的宿主形状，不是复制其“LLM 可直接调用大量电脑工具”
的风险边界。

### 推荐迁移顺序

1. **先修安全授权链**
   - 定义带 scope、action/parameter hash、issuer、expiry、nonce 的
     `ExecutionAuthorization`。
   - 贯穿 Mission dispatch、`StructuredRobotTask`、Robot Gateway、
     `RobotAgentPolicy` 和 `SafetyGate`。
   - 删除所有无来源的 `operator_confirmed=True`。

2. **落实执行串行化和权威状态事务**
   - 引入 SQLite WAL 或等价本地事务 store。
   - task state、pending physical operation、evidence/outbox 原子提交。
   - 加 per-robot lane、resource lease、idempotency key 和 CAS/revision。

3. **建立统一 FireClaw Plugin Host**
   - 参考 `OpenClawPluginApi`，提供 `register_tool`、
     `register_physical_capability`、`register_hook`、`register_service`、
     `register_context_engine`、`register_agent_harness`。
   - 物理 Skill、工作区 Skill 和当前 PluginRuntime 迁入同一 registry。
   - 增加 API version、所有权、冲突诊断、activation transaction、rollback、
     dispose 和可查询状态。

4. **建立真正共享的 Agent Harness**
   - `BoundedAgentLoop` 继续负责机器人安全循环和 checkpoint。
   - Harness 统一 provider route、context fit、tool normalization/policy、
     transcript、abort、trace 和错误分类。
   - Mission/Robot 角色只提供 prompt、允许的 capability profile 和
     operation executor。

5. **清除核心中的能力名和跨楼层默认链**
   - 用 metadata predicate、task composition 和 completion contract registry
     替代字符串分支。
   - 当前默认只启用单层 `navigate_to_point`；跨层能力作为未激活扩展保留。

6. **最后拆分 Gateway/MissionAgent**
   - transport、application handler、supervisor、store/service composition
     分层。
   - 不应在统一注册中心和事务 store 之前进行大规模文件搬迁。

### 研究层面判断

#### 工程正确性

当前 FireClaw 已有较强的任务图、状态快照、belief、主动观测、计划修订、
Robot Agent bounded loop 和重启 reconciliation，但宿主级边界仍未成熟到
真实机器人可放心扩展的程度。尤其审批 provenance、资源 lease 和权威状态
事务属于部署前阻断项。

#### 研究有效性

单纯模仿 OpenClaw 插件系统不是研究贡献，只是必要工程基础。FireClaw 可形成
研究价值的方向是：在统一 Agent Harness 上，将证据化状态、风险作用域授权、
checkpoint-before-actuation、恢复 reconciliation 和多机器人资源 lease
组合成可验证的 embodied-agent execution contract。

#### 发表级贡献

目前更接近高质量研究原型，而不是已经完成的方法贡献。若要支持机器人/AI
会议论文，需要对以下主张做实验：

- 有无 provenance-bound authorization 的越权率；
- 有无 transactional checkpoint/outbox 的重启不一致率；
- 有无 resource lease 的动作冲突率；
- 统一 Harness 与分散模型入口的故障注入恢复率；
- snapshot/evidence gate 相比直接 ReAct 工具调用的安全性与任务成功率。

### 已运行命令

```bash
git status --short --branch
date '+%Y-%m-%d %H:%M:%S %Z'
wc -l src/fireclaw_core/gateway/gateway.py \
  src/fireclaw_core/mission/mission_agent.py \
  src/fireclaw_core/mission/mission_runtime.py \
  src/fireclaw_core/plugin/plugin_runtime.py \
  src/fireclaw_core/agent/agent.py
rg -n --glob '!execution/builtin_physical_skills.py' \
  '(navigate_to_floor|navigate_to_point|search_floor|search_for_victims|rescue_victim)' \
  src/fireclaw_core
rg -n 'resource_locks|operator_confirmed=True|PluginRuntime\(|SkillRegistry\(|PhysicalSkillCatalog\(' \
  src/fireclaw_core
```

此外使用 CodeGraph 检查了上述 FireClaw 和 OpenClaw 符号的源码、调用路径和
blast radius。

### 下一步建议

不要继续添加新的物理 Skill。下一项实现应先完成
`ExecutionAuthorization` 的端到端授权链，因为当前无来源的
`operator_confirmed=True` 会直接弱化已经建立的 SafetyGate。
