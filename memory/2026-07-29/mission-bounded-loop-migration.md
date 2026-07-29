# Mission Coordinator Shared Bounded Loop Migration

更新时间：2026-07-29T15:05:00+08:00

## 任务目标

把中央 `MissionDeliberationRuntime` 的通用生命周期控制迁移到已经供
Robot Agent 使用的 `BoundedAgentLoop`，消除两个独立 `for iteration`
实现，同时保持中央规划公开 API、状态名、快照语义和审计数据兼容。

## 恢复时状态

- Robot Agent 已使用 `BoundedAgentLoop` 执行本地有界 ReAct。
- Mission Coordinator 仍在 `MissionDeliberationRuntime.deliberate()` 内
  自行处理轮次、超时、取消、policy 异常和终止。
- 工作区已有 active-observation、context evaluation、术语统一和 Robot
  Agent loop 的未提交改动；本次未还原这些改动。

## OpenClaw 对照

通过 CodeGraph 检查：

- `openclaw/src/agents/agent-run-terminal-outcome.ts`
- `openclaw/src/agents/tool-loop-detection.ts`
- 前一阶段已检查的
  `openclaw/packages/agent-core/src/agent-loop.ts`
  和 embedded runner `run-loop.ts`

复用的结构是由单一 loop owner 处理 attempt lifecycle、取消、超时和
terminal projection。FireClaw 没有复用 OpenClaw 的通用软件工具权限：
中央 Mission adapter 仍然只能读取冻结快照、请求宿主批准的主动观测，并
提交待验证的任务图。

## 实现

修改 `src/fireclaw_core/mission/mission_deliberation.py`：

- `MissionDeliberationRuntime.deliberate()` 现在调用
  `BoundedAgentLoop.run()`。
- `BoundedAgentLoop` 统一拥有：
  - 最大规划轮数；
  - 总超时；
  - policy 调用前后取消；
  - decision/operation 异常的 fail-closed 终止；
  - 继续执行必须产生进展标记；
  - loop terminal trace。
- 新增内部 mission adapter 数据：
  - `_MissionLoopDecision`
  - `_MissionLoopObservation`
  - `_MissionLoopTerminal`
- Mission 特有逻辑仍留在 adapter：
  - tokenizer-aware 上下文组装；
  - snapshot read 和重复读取限制；
  - unresolved belief 检查；
  - active-observation 请求校验；
  - graph proposal 编译；
  - plan、revision evidence 和 belief assumption 校验。
- `_validate_plan_proposal()` 从旧循环主体抽出，行为保持不变。
- `_mission_terminal_from_loop()` 将共享 lifecycle 状态映射回现有中央状态：
  `proposed`、`observation_required`、`clarification_required`、
  `blocked`、`escalated`、`cancelled`、`timed_out`。
- `_append_unexecuted_loop_attempts()` 保留 policy 返回后发生取消或超时时的
  Mission attempt、context manifest 和 operation 审计。
- `MissionDeliberationResult` 和 `MissionDeliberationAttempt` 的公开形状
  未改变。

## 测试

新增回归覆盖：

- 中央 runtime 确实经过 `BoundedAgentLoop.run()`；
- policy 返回后取消仍产生兼容的 Mission attempt。

已执行：

```text
py_compile mission_deliberation.py bounded_loop.py
34 passed: bounded loop + mission deliberation/context/LLM policy
161 passed: mission integration/revision/recovery/active observation
1 passed: embodied mission E2E with local loopback socket permission
1778 passed, 6 deselected in 131.84s: full non-ROS regression
```

聚焦测试中 E2E 首次失败原因是沙箱禁止创建本地 socket：
`PermissionError: [Errno 1] Operation not permitted`。在允许本地回环端口后
通过，不是代码回归。

## 当前结论

Mission Coordinator 和 Robot Agent 现在真正共享同一套有界循环生命周期
实现，但不是同一个角色：

- Mission adapter 做任务级推理、证据约束和图验证，不执行机器人动作。
- Robot adapter 做本机逐技能推理，并经过任务信封、Robot policy 和
  `SafetyGate` 执行物理工具。
- 安全策略没有被塞进通用 loop；共享的是生命周期机制，不是权限。

## 下一步

1. 给共享 loop 增加可恢复 checkpoint/resume 时，分别定义中央 continuation
   和 Robot 技能间 continuation，禁止自动重放物理动作。
2. 建立两层 loop 的动态路线阻塞、传感器退化和部分成功 benchmark。

## 研究判断

本次是必要的工程统一，不单独构成论文贡献。它降低了两层规划生命周期差异
造成的混杂变量，使后续比较 central-only replanning、local ReAct 和层级
协同规划时，能够把性能差异归因于策略与权限边界，而不是不同的循环实现。
