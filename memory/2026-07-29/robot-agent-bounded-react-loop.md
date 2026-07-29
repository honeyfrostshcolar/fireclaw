# Robot Agent Shared Bounded ReAct Loop

更新时间：2026-07-29T13:52:20+08:00

## 任务目标

解决 Robot Agent 与 Mission Coordinator 规划运行时割裂的问题。先抽取
可复用的有界 Agent loop 内核，再让 Robot Agent 的 LLM Gateway 路径从
“一次生成完整技能列表、随后静态执行”升级为：

```text
decision -> host validation -> one operation -> structured observation
-> next decision
```

本阶段不把 Robot Agent 直接继承或替换为 `MissionAgent`，也不立即重写已有
`MissionDeliberationRuntime`。

## OpenClaw 对照

通过 CodeGraph 检查：

- `openclaw/packages/agent-core/src/agent-loop.ts`
- `openclaw/packages/agent-core/src/types.ts`
- `openclaw/src/agents/tool-loop-detection.ts`

复用的结构是 model/tool-result/model 的继续执行形态、统一 lifecycle 和
tool result transcript。FireClaw 的差异：

- LLM 每轮只能提出一个操作；
- 物理技能必须经过任务合同、Robot Agent policy 和 `SafetyGate`；
- 本地安全阻断是终止状态，不能作为普通工具错误让 LLM 绕过；
- 机器人技能结果是结构化执行证据，而不是自由文本；
- Robot Agent 不能扩大中央下发的 target、risk 或 authority。

## 实现

### 共享 loop 内核

新增 `src/fireclaw_core/agent/bounded_loop.py`：

- `AgentLoopLimits`
- `AgentLoopTurn`
- `AgentLoopTransition`
- `AgentLoopAttempt`
- `AgentLoopResult`
- `BoundedAgentLoop`

统一处理：

- 最大轮数和总超时；
- 调用前后取消；
- policy 和 operation handler 异常的 fail-closed 终止；
- 每轮只能有一个 typed decision；
- `continue` 必须携带 observation，否则以 `no_progress` 阻断；
- completed / blocked / escalated / failed / cancelled / timed_out；
- 每轮 operation、outcome、duration、reason 和 observation trace。

### Robot Agent decision/runtime

新增 `src/fireclaw_core/agent/robot_deliberation.py`：

- `RobotAgentDecision`
- `RobotAgentExecutionObservation`
- `RobotAgentDeliberationRequest`
- `RobotAgentDeliberationLimits`
- `LLMRobotAgentDecisionPolicy`
- `RobotAgentDeliberationRuntime`

模型每轮只能选择：

- 一个 allowlisted robot skill；
- 一个只读 entity-memory context query；
- `complete_robot_task`；
- `report_robot_task_blocked`；
- `escalate_robot_task`。

默认预算：

- 8 个模型轮次；
- 6 次物理技能执行；
- 2 次 advisory context query；
- 30 秒总时间。

每轮 provider 调用继续使用共享 `ModelAwareContextManager`。任务合同、本机
状态、环境状态、传感器和物理执行结果为 authoritative/continuity；session
history 和 entity memory query 为 advisory。

### Policy 和安全

`RobotAgentPolicy` 新增 `validate_step()`，用于逐技能验证：

- skill 必须位于任务信封白名单；
- floor 不能越过中央 target；
- high/critical risk 必须升级确认。

宿主在 `SafetyGate` 返回非 `allow` 时直接终止或升级，不会继续下一轮让 LLM
尝试绕过。`complete` 只有在全部 `required_skills` 已成功后才接受。

### Gateway 和执行

`FireClawGateway`：

- deterministic 模式继续使用旧 `RobotAgentRuntime`；
- llm 模式改用 `RobotAgentDeliberationRuntime`；
- 每轮重新读取 robot/environment state；
- task `allowed_skills` 和 `required_skills` 都可成为候选工具；
- 物理 skill 仍由现有 registry、`SafetyGate`、`PlanExecutor` 和 adapter
  执行。

`FireClawAgent` 新增：

- `execute_deliberated_step()`：执行一个 SafetyGate-checked step，不提前
  产生 mission invalidation；
- `finalize_deliberated_task()`：把多轮 step results 汇总为一个终态任务
  结果，只在最终未恢复失败时产生计划失效事件。

因此一次 route failure 后本地恢复成功，不会错误通知 Mission Coordinator
旧计划已经失效。

### 兼容性

以下旧 API 保留：

- `LLMRobotAgentPlanner`
- `RobotAgentRuntime.plan_structured_task`
- deterministic Robot Agent Gateway 模式

它们仍可用于静态计划测试、兼容调用和 deterministic fallback。Gateway 的
LLM 模式使用新 deliberation runtime。

## 文件修改

- `src/fireclaw_core/agent/bounded_loop.py`
- `src/fireclaw_core/agent/robot_deliberation.py`
- `src/fireclaw_core/agent/robot_agent.py`
- `src/fireclaw_core/agent/agent.py`
- `src/fireclaw_core/gateway/gateway.py`
- `tests/test_bounded_agent_loop.py`
- `tests/test_robot_agent_deliberation.py`
- `tests/test_robot_agent_fireclaw_agent_execution.py`
- `tests/test_gateway_structured_task.py`
- `README.md`
- `docs/architecture/fireclaw-agent-terminology.md`

## 验证

共享 loop 单元测试：

```text
5 passed
```

Robot Agent 非 socket 聚焦测试：

```text
41 passed
```

Gateway + Mission deliberation 聚焦回归：

```text
71 passed in 6.60s
```

完整非 ROS 回归：

```text
1776 passed, 6 deselected in 129.05s
```

其他检查：

```text
python3.10 -m compileall -q ...  # passed
git diff --check                 # passed
```

## 工程结论

Robot Agent 现在具备实际的本地有界 ReAct/tool-result loop，而不再只是静态
技能序列生成器。它能够看见上一技能的成功/失败结果并选择下一步，同时所有
物理动作仍由宿主执行和约束。

本阶段建立了统一 loop 内核，但 `MissionDeliberationRuntime` 尚未迁移到
`BoundedAgentLoop`，因此“两个角色完全使用同一个 loop 实现”的目标还未
全部完成。下一步应做兼容迁移，而不是重新发明另一套中央循环。

## 研究判断

这是重要的工程与实验基础，但单独不构成论文级创新。可研究的贡献方向是：

- 层级任务合同约束下的本地 ReAct；
- safety-terminal 与 recoverable observation 的形式化区分；
- 本地恢复对中央重规划频率、任务成功率和不安全动作率的影响；
- static local plan、mechanical retry、bounded local ReAct、central-only
  replanning 的消融比较。

## 工作区说明

本次开始时已有 active-observation、context-evaluation 和术语统一相关的
未提交改动，以及不相关的
`memory/2026-07-28/codex-usage-reset-skill-check.md`。没有还原或覆盖这些
已有工作。

## 下一步

1. 将 `MissionDeliberationRuntime` 的生命周期控制迁移到
   `BoundedAgentLoop`，保持当前 decision、snapshot 和 audit 数据形状兼容。
2. 给 Robot Agent loop 增加 checkpoint/resume，处理机器人进程或 Gateway
   在技能间重启。
3. 用动态路线阻塞、传感器退化和部分成功场景建立模型驱动 benchmark。
