# Shared Agent Loop Checkpoint and Physical-Action Reconciliation

更新时间：2026-07-29T15:12:00+08:00

## 任务目标

为中央 `MissionDeliberationRuntime` 和机器人端
`RobotAgentDeliberationRuntime` 共用的 `BoundedAgentLoop` 增加持久化
checkpoint/resume，并解决 Robot Agent 进程或 Gateway 在物理技能执行期间
重启时可能重复动作的问题。

## 恢复前状态

- 两种常驻 Agent 已共享 `BoundedAgentLoop` 的轮次、超时、取消和终止控制。
- Mission dispatch scheduler 已有节点图 checkpoint，但 model/tool loop
  本身不知道恢复到第几轮。
- Robot Agent 能逐技能 ReAct，但重启后无法区分技能“未开始、已完成、开始但
  结果未知”。
- Gateway 启动时会把所有旧的非终态 task queue record 标记为 `lost`。

## OpenClaw 对照

通过 CodeGraph 检查了 OpenClaw 的 agent session/transcript persistence、
terminal outcome、tool-loop detection 和幂等重试相关实现。复用的结构是：

- append-only 持久化记录；
- 由统一 loop owner 恢复 turn/attempt lifecycle；
- 使用稳定 ID 和幂等边界处理重试。

FireClaw 没有直接照搬软件工具的自动重试。原因是 OpenClaw 的文件或 API
工具通常能用错误结果继续推理，而机器人动作可能已经对物理世界产生不可逆
影响。FireClaw 因此增加了 OpenClaw 没有的 pending physical operation 和
宿主事件对账协议。

## 实现

### 共享 checkpoint store

新增 `src/fireclaw_core/agent/loop_checkpoint.py`：

- `AgentLoopPendingOperation`
- `AgentLoopCheckpoint`
- `AgentLoopCheckpointStore`
- `JsonlAgentLoopCheckpointStore`

JSONL store 每次 append 都 flush + fsync；读取时只容忍未提交的最后半行，
文件中部损坏会 fail closed。追加前会截断无效半行，避免崩溃尾部和新记录
拼接成中部损坏。

`BoundedAgentLoop` 现在持久化：

- run/checkpoint/role identity；
- next iteration、已用时间、attempts 和 observations；
- adapter-specific state；
- terminal result；
- 需要对账的 pending operation，包括稳定 `operation_id` 和 decision。

### Mission Coordinator 恢复

Mission checkpoint key 绑定：

- mission ID；
- frozen snapshot ID；
- plan revision；
- command SHA-256。

adapter state 保存已完成的 snapshot reads、mission attempts、validation
feedback、seen reads 和 last planning result 摘要。中央操作是只读的，因此
崩溃时从最后一个已提交轮次继续；未提交的只读计算可以安全重算。

`MissionRuntimePaths` 新增可选 `agent_loop_checkpoints`，未配置时从
mission registry 路径派生默认文件。

### Robot Agent 恢复

Robot checkpoint key 为：

```text
robot:{robot_id}:task:{structured_task_id}
```

adapter state 保存任务合同哈希、robot/task/mission identity、已成功技能和
技能/上下文查询预算计数。

每个物理技能在执行前先写 pending checkpoint，并携带
`RobotLocalPlanStep.operation_id`。Gateway 再写：

- `robot_agent.skill_dispatch_started`
- `robot_agent.skill_dispatch_finished`

两条事件使用同一个 operation ID，finished 事件包含结构化执行结果。

恢复规则：

```text
有 finished -> 吸收原结果，更新 succeeded_skills，继续下一轮
没有 started -> 记录 not_started，允许重新 deliberation
有 started 无 finished -> physical_action_outcome_unknown，升级且禁止重放
```

未知动作保持为 recoverable pending checkpoint，避免普通 terminal record
覆盖对账线索。

### Gateway 启动恢复

Gateway 默认在 task queue 文件旁创建 agent-loop checkpoint 文件。启动时：

- 只有 LLM Robot Agent runtime、结构化任务事件和 recoverable checkpoint
  同时存在时才调度恢复；
- 恢复 operator identity 和原 `TaskControl`；
- 写入 `task.resume_scheduled` / `task.resume_started`；
- 无有效 checkpoint 的旧任务仍标记 `lost`；
- 超过本机恢复并发容量的持久化等待队列尚未实现。

## 文件修改

- `src/fireclaw_core/agent/bounded_loop.py`
- `src/fireclaw_core/agent/loop_checkpoint.py`
- `src/fireclaw_core/agent/robot_agent.py`
- `src/fireclaw_core/agent/robot_deliberation.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/mission/mission_deliberation.py`
- `src/fireclaw_core/mission/mission_runtime.py`
- `src/fireclaw_core/monitoring/event_ledger.py`
- `tests/test_bounded_agent_loop.py`
- `tests/test_robot_agent_deliberation.py`
- `tests/test_mission_deliberation.py`
- `tests/test_gateway.py`
- `README.md`
- FireClaw/OpenClaw alignment and terminology docs

## 验证

已通过：

```text
9 shared-loop/checkpoint tests
19 shared-loop + Robot resume tests
35 shared-loop + Robot + Mission resume tests
28 Mission deliberation/runtime compatibility tests
3 Gateway stale/recovery/dispatch-boundary tests
186 selected Mission/Robot/Gateway tests before socket-only failures
42 Mission CLI tests after Path/string compatibility fix
1787 passed, 6 deselected: full non-ROS regression
py_compile / compileall
git diff --check
```

较宽测试首次在 sandbox 内运行时有 33 个 socket 权限失败；允许本地回环后
暴露出 8 个 Mission CLI 兼容失败，原因是 CLI 仍可能把
`MissionRuntimePaths.mission_registry` 传成 `str`。已在派生 checkpoint
路径前统一转换成 `Path`，42 个 Mission CLI 用例和完整 non-ROS suite
均通过。

## 当前结论

共享 loop 现在不仅统一运行时生命周期，也统一持久化和恢复语义。两种 Agent
仍保持不同权限：

- Mission Coordinator 恢复认知连续性和只读状态查询；
- Robot Agent 除认知连续性外，还必须证明物理技能结果；
- 无法证明时停住并升级，不以 LLM 推测或自动重试替代证据。

## 下一步

1. 建立 crash-injection benchmark，分别在 pending checkpoint、dispatch
   started、physical completion、dispatch finished 和 observation commit
   边界杀进程。
2. 实现超过本机并发容量时的 durable recovery waiting queue。
3. 为真实 ROS action 接口引入 adapter-native goal ID/status 查询，使 finished
   证据不仅依赖 Gateway event ledger。

## 研究判断

本次首先是安全关键的工程正确性，不应单独宣称论文创新。可形成研究贡献的
方向是把 evidence-bound embodied action recovery 形式化，并与 blind retry、
at-least-once tool execution、exactly-once software RPC 和 operator-only
recovery 做故障注入实验，比较重复物理动作率、任务恢复率、恢复延迟和人工
介入率。
