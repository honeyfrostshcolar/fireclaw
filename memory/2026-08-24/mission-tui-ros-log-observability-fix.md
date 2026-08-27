# Mission TUI 与 ROS 日志可观测性修复

## 时间

- 任务开始：2026-08-23（用户完成四点巡检实测后反馈）
- 最后验证：2026-08-24 00:13 +0800

## 任务目标

用户的真实 Gazebo 巡检任务暴露三个问题：

1. Mission CLI 的 spinner 与 SSE 事件并发写 stdout，复制出的终端记录含大量 `\r` 覆盖片段，角色与换行不清楚；
2. Robot Agent 的局部 `iteration` 每个子任务重新从 1 开始，但 UI 没有封存计划步骤上下文，无法判断“迭代 1”属于哪个巡检点；
3. `move_base` 发布到 ROS 的 costmap clearing、rotate recovery、abort 等重要 WARN/ERROR 没有进入 FireClaw Mission TUI。

用户明确要求：参照 OpenClaw 的 TUI 组织方式；同时显示 Agent 活动和关键 ROS 会话；日志采集必须并行，不进入 LLM 规划或物理动作关键路径。

## OpenClaw analogue（先检查后实现）

通过 CodeGraph 检查了：

- `openclaw/src/tui/components/chat-log.ts`
  - `ChatLog` 按 user/assistant/system/tool 组件保存状态；
  - streaming run/tool call 按 id 更新既有组件；
  - 连续 system message 可 coalesce；
- `openclaw/src/tui/components/user-message.ts`
- `openclaw/src/tui/components/assistant-message.ts`
- `openclaw/src/tui/tui-event-handlers.ts`
- `openclaw/src/tui/tui-run-lifecycle.ts`
- `openclaw/src/tui/tui-stream-assembler.ts`

复用的结构原则：后台事件只更新/提交结构化状态，单一 TUI renderer 拥有终端写入；user、assistant、system、tool 明确区分；run/task id 用于关联增量更新。

FireClaw 适配理由：当前是 dependency-light Python Mission CLI，不直接引入 OpenClaw 的 TypeScript `pi-tui` 全屏组件栈；改为 append-only 的完整块级 renderer，但严格保留单写入所有权、角色分区、事件关联和连续反馈 coalescing。

## 根因

### 1. 终端输出竞态

- `LiveExecutionMonitor._spin()` 每 100ms 使用 `\r\033[K` 写 stdout；
- SSE handler 同时通过 `print_line()` / 普通 `print()` 输出耐久事件；
- 屏幕上可能暂时看似覆盖成功，但 shell transcript/复制记录会保留中间 carriage-return 帧；stdout/stderr 还可能重排。

### 2. iteration 语义丢失

- `mission_scheduler._relay_robot_progress_events()` 只转发 Robot Agent payload 中的局部 `iteration`；
- `_SubmittedAttempt` 已有 `subtask/node_id/task_id/plan_id`，但没有计划步骤位置；
- UI 因而显示“迭代 1”，没有 `计划步骤 2/4`、命令、node、tool 等上下文。

### 3. ROS 日志链路缺口

- Robot Gateway 仅转发 FireClaw EventLedger/EventBus 事件；
- 没有 ROS `/rosout_agg` subscriber；
- Mission Scheduler 只能从 Robot task trace 看到 FireClaw 事件，无法看到 `move_base` 原生日志。

### 4. terminal/log 尾部竞态

- 即使 ROS callback 异步入队，最后一条 abort 也可能比 action terminal 状态晚几毫秒落库；
- Mission Scheduler 看到 terminal 后会停止轮询，存在漏掉最后 ROS ERROR 的风险。

## 实施内容

### `src/fireclaw_core/mission/interactive.py`

- 新增 `MissionTerminalRenderer`：
  - 模块级 `RLock` 统一拥有完整块写入；
  - 不再使用后台 spinner/carriage-return 动画；
  - 每个 durable event 输出完整 `┌─ / │ / └─` block；
  - 清理远端 ANSI/control sequence；
  - 按 terminal cell width 自动换行；
  - 长 task/mission id 只在 UI 中缩短，审计事件仍保留全值。
- `LiveActivityIndicator` 改为事件驱动的 Mission Agent 状态块，不启动 stdout thread。
- `LiveExecutionMonitor`：
  - 角色明确区分 `Mission Agent`、`Robot Agent`、`ROS WARN/ERROR`、连接状态；
  - 缓存 task context，使稍后到达的 task/ROS event 仍显示对应计划步骤；
  - 将完全相同且连续的 action feedback 只显示一次，EventLedger 不删事件；
  - 支持 `mission.sealed_plan_execution_started` 真实事件名；
  - 为 `subtask.submitted/status_changed` 输出可读标签，不再只显示 `accepted/blocked`。
- 操作员 prompt 改为单独的 `🧑 操作员` / `🧑 操作员 · 回答` / `🧑 操作员 · 安全确认` 角色。

### `src/fireclaw_core/mission/mission_scheduler.py`

- `_SubmittedAttempt` 新增：
  - `plan_step_index`
  - `plan_step_total`
- 主封存计划 dispatch 时按原始 `plan.subtasks` 顺序绑定步骤；retry/reassign 继承原步骤位置。
- Robot progress payload 现在携带：
  - `mission_id`
  - `robot_id`
  - `task_id`
  - `node_id`
  - `plan_id`
  - `plan_step_index/total/command`
  - `robot_agent_iteration`
  - `tool_name`
- 不再把 `迭代 N:` 拼进 message；UI 分别渲染 `计划步骤 N/M` 和 `Agent 回合 K · Tool ...`。
- 将 Robot trace 中的 `ros.log` 保持为独立 `ros.log` Mission event，经现有 MissionRun EventBus/SSE 并行转发。

### `src/fireclaw_core/ros/ros1_log_stream.py`（新增）

- `Ros1LogStream` 订阅 `/rosout_agg`，默认只采集 ROS WARN/ERROR/FATAL；
- ROS callback 只执行：有界清理、任务上下文快照、`Queue.put_nowait`；
- 独立 daemon worker 调用 Event sink，SQLite/JSONL 写入不阻塞 ROS callback、LLM planner 或 physical tool；
- 队列容量 512，记录 received/emitted/dropped；下一条成功记录携带 `dropped_before`；
- 最大消息 4000 字符、清理 ANSI/control、保留 node/ROS timestamp/file/function/line/topics；
- callback 时捕获 task binding，避免 task 刚结束后 worker 再查上下文而丢失关联；
- `flush()` 是工具返回后的有界 observability barrier：默认最多 200ms、通常约 20ms quiet period，仅保证尾部 rosout evidence 先于 task terminal 发布，不进入规划/动作路径。

### `src/fireclaw_core/gateway/gateway.py`

- ROS1 adapter 自动创建 `Ros1LogStream`；
- 生命周期顺序：
  - start：Robot ROS runtime -> ROS log stream -> HTTP intake；
  - stop：HTTP/tasks -> ROS log stream -> Robot ROS runtime；
- 单一 active task 时将 `ros.log` 持久化进该 task EventLedger；
- 0 个或多个 active task 时显式标记 `unbound/ambiguous`，只作为 robot-level stream telemetry，不虚假归因；
- task terminal 记录前调用有界 log flush；
- `/health` 与 `/state` 增加 `ros_log_stream` 状态/级别/topic/dropped count。

## 测试

新增/更新：

- `tests/test_ros1_log_stream.py`
  - 真实异步边界（sink 阻塞时 callback 仍立即返回）；
  - INFO 过滤；
  - WARN/ERROR 结构化记录；
  - task context callback-time capture；
  - ANSI 清理；
  - flush timeout/success；
  - 未初始化 ROS node 时诚实 degraded。
- `tests/test_gateway_runtime_lifecycle.py`
  - runtime/log/http start 顺序与反向 stop；
  - task-bound ROS log 进入 task trace。
- `tests/test_mission_scheduler.py`
  - Robot Agent 回合字段与消息解耦；
  - `ros.log` 携带 sealed plan step context。
- `tests/test_interactive.py`
  - 计划步骤、Agent 回合、Tool 与 ROS block；
  - ANSI/carriage-return 不进入输出；
  - 连续反馈 presentation coalescing；
  - 两线程完整 block 原子写入。

执行结果（`/home/lpp/miniconda3/envs/py310/bin/python`）：

- 聚焦最终状态：`48 passed in 1.24s`；
- 全量最终状态：`2462 passed, 8 skipped in 308.86s`；
- `py_compile`：通过；
- `git diff --check`：通过（最终仍需在 memory 更新后再检查一次）。

## 真实 ROS 冒烟

为避免连接用户 Gazebo，使用隔离端口：

1. `ROS_MASTER_URI=http://127.0.0.1:11931 roscore -p 11931`
2. 临时 Python ROS node 初始化 `Ros1LogStream`；
3. 实际执行 `rospy.logwarn("FireClaw ROS log bridge smoke warning")`；
4. `/rosout_agg` 成功收到并结构化：
   - `status=ready`
   - `observed=true`
   - `severity=WARN`
   - task=`smoke-task`
   - mission=`smoke-mission`
   - received=1 / emitted=1 / dropped=0
5. 临时 ROS Master/rosout 已 Ctrl-C 完整关闭。

首次沙箱内启动因 ROS 要写 `/home/lpp/.ros/*.pid/log` 得到 read-only filesystem；获用户工具授权后在沙箱外完成。不是代码错误。

## 当前结论

- 用户列出的三类问题均已实现修复；
- 预期终端结构示例：
  - `🤖 Robot Agent · gazebo_turtlebot3`
  - `计划步骤 2/4 · 前往第二个巡检点 ...`
  - `Agent 回合 1 · Tool navigate_to_point`
  - `⚠️ ROS WARN · /move_base`
  - `🛑 ROS ERROR · /move_base`
- 不再有 spinner 线程与 SSE 输出互相覆盖；
- ROS 日志采集并行，不增加 LLM 规划时间或物理工具执行时间；仅在工具已经返回后增加约 20ms、上限 200ms 的证据排空窗口。
- 未创建 commit；保留了工作区既有 Antigravity/用户修改。

## 工程 / 研究判断

- 工程正确性：这次是 operator observability、安全审计和事件关联修复；真实 ROS 与 2462 项回归提供了较强工程证据。
- 研究有效性：可作为后续“可解释自主恢复/人机态势感知”实验基础，可量化 ROS fault 到 Agent decision 的显示延迟、日志丢弃率、操作员故障定位时间。
- 论文贡献：单独看不是新算法或 publication-level contribution；若要形成研究贡献，需要把跨层 causal trace（ROS evidence -> belief update -> recovery decision -> operator intervention）形式化并与普通日志/TUI baseline 做受控实验。

## 操作员下一步

1. 保留或重新启动 Gazebo/`roslaunch`；
2. 必须重启 Robot Gateway，使 ROS node 与 `/rosout_agg` subscriber 加载新代码；
3. 必须重启 Mission Gateway，使新的 scheduler relay payload 生效；
4. 重启 Mission CLI；
5. 可先检查 Robot Gateway `/health`：
   - `robot_runtime.status=ready`
   - `ros_log_stream.status=ready`
   - `ros_log_stream.topic=/rosout_agg`
   - `ros_log_stream.minimum_level=WARN`
6. 重跑原四点巡检；第二步受阻时应在同一 TUI 中看到 plan step、Agent round、costmap/rotate recovery 与最终 abort。

剩余不确定性：本轮没有用户真实 Gazebo/move_base 进程在线，因此只对隔离真实 ROS `/rosout_agg` 做了 live smoke；最终仍需用户按原场景复测一次 move_base recovery 序列。
