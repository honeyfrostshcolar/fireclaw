# FireClaw Agent Terminology

日期：2026-07-29

本文定义 FireClaw 当前架构的统一术语。README、当前架构文档、用户界面、
CLI 帮助和后续设计应遵循本文。

## Canonical Terms

### Mission Coordinator

中文：中央任务协调器。

当前实现：`MissionAgent` + `MissionGateway`。

职责：

- 面向消防员和指挥平台接收任务；
- 维护全局任务状态、任务图和计划版本；
- 选择已注册且在线的 Robot Agent；
- 下发 `StructuredRobotTask`；
- 汇总证据并执行跨机器人重规划；
- 管理操作员确认、全局取消和任务审计。

Mission Coordinator 不直接调用 ROS topic、service、action、机器人 SDK
或硬件执行器。

### Robot Agent

中文：机器人智能体、机器人本地智能体或机器人端智能体。

当前实现：每台机器人上常驻的
`FireClawGateway` + `FireClawAgent` + `RobotAgentRuntime`。

职责：

- 维护本机状态、技能、传感器和具身记忆；
- 在中央下发的任务信封内进行本地规划；
- 经过本地 policy、`SafetyGate` 和执行器调用机器人算法；
- 处理不改变任务目标的局部恢复；
- 返回结构化执行结果、证据和计划失效事件；
- 在通信中断或中央状态过期时执行本地故障安全策略。

Robot Agent 是独立部署、长期运行的具身智能体。它不是 Mission
Coordinator 临时创建的进程，也不是 OpenClaw `sessions_spawn`
意义上的 subagent。

### Delegated Worker Subagent

中文：临时委派子智能体或认知工作子智能体。

这是未来可选的运行时角色，对应 OpenClaw 的临时派生 session。它可以由
Mission Coordinator 或 Robot Agent 创建，用于有明确交付物的认知工作，
例如比较路线候选、整理日志或检索知识。它不天然拥有机器人物理执行权，
完成任务后可以终止。

FireClaw 当前的 Robot Agent 不属于这一类。

## Relationship

FireClaw 是一个共同开发和发布的分层具身智能体框架：

```text
operator
  -> Mission Coordinator
       -> StructuredRobotTask
            -> registered Robot Agent
                 -> local planner / SafetyGate / skills / robot adapter
```

Mission Coordinator 和 Robot Agent：

- 属于同一个 FireClaw 架构和代码仓库；
- 共享 provider、上下文管理、tool-call 协议、记忆事件、审计和取消等
  基础设施，并以 `BoundedAgentLoop` 作为统一循环内核；Robot Agent 和
  `MissionDeliberationRuntime` 均已接入；
- 共享 append-only loop checkpoint；中央规划恢复已完成的只读查询和校验，
  Robot Agent 还必须按 `operation_id` 对账物理技能，未知结果不得自动重放；
- 使用不同的工具目录、状态来源、权限、安全策略和运行生命周期；
- 通过正式任务、状态、事件和证据协议交互，不通过内部 planner 实现互相调用。

它们是职责不同、关系紧密的两种常驻 Agent，不是一个 Agent 的主从副本。

## Preferred Wording

使用：

- `Mission Coordinator selects and dispatches to registered Robot Agents.`
- `中央任务协调器向已注册的机器人智能体下发任务。`
- `Robot Agent owns local embodied execution authority.`
- `机器人智能体拥有本机具身执行和安全拒绝权。`
- `robot-agent run registry`
- `robot-agent client`

避免：

- `main agent spawns robot subagents`
- `主智能体生成机器人子智能体`
- `robot subagent`
- `机器人子智能体`
- 用 `main agent` 泛指 `Mission Coordinator`

只有在讨论 OpenClaw、临时派生认知 worker，或引用现有 legacy identifier
时，才使用 `subagent`。

## Legacy Identifiers

以下现有 Python 标识符和持久化字段形成了兼容接口，本次术语统一不直接重命名：

- `RobotSubagentClient`
- `SubagentRunRecord`
- `JsonlSubagentRegistry`
- `subagent_client`
- `subagent_registry`
- `subagents.jsonl`

在当前文档中引用它们时，应明确说明它们是 legacy-named identifiers，实际
服务对象是常驻 Robot Agent。后续代码迁移应单独设计兼容窗口、导入别名和
持久化迁移，不应在纯文档改动中静默破坏现有配置、测试或运行数据。
