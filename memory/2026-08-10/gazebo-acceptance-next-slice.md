# Gazebo acceptance lane 下一实施切片

## 时间

- 2026-08-10T00:21:47+08:00

## 任务上下文

用户在 generic Physical Action deadline/cancel/terminal contract 完成后，询问
Gazebo acceptance lane 下一步应做什么。本次只做恢复核对与实施范围判断，尚未
修改业务代码、启动 ROS/Gazebo、执行 commit/push。

## 当前进展

- `navigate_to_point` 已由 `fireclaw.navigation.move-base` Plugin 拥有；
- generic Runtime 已实际强制 monotonic deadline，并区分 `cancelled`、
  `timed_out`、`lost`；
- `Ros1MoveBaseBackend` 已实现 `cancel_goal()` 后的有界 actionlib 终态确认；
- 无 ROS 全量回归此前为 `1930 passed, 7 skipped`；
- 当前机器存在 ROS Noetic 的 `rosversion`、`roslaunch`、`roscore` 和 `gzserver`，
  Navigation 与 TurtleBot3 catkin workspace 均已有 `devel/setup.bash`。

## 当前缺口

- `extensions/navigation-move-base/launch/`、`config/`、`tests/` 仍只有
  `.gitkeep`；
- `pyproject.toml` 尚未注册 `gazebo_acceptance` marker；
- 当前 Plugin-owned `Ros1MoveBaseBackend -> /move_base` 尚无真实 Gazebo proof；
- 尚无 `results/gazebo-acceptance/<run-id>/` proof bundle；
- `docs/deployment/ros1-gazebo-debugging-guide.md` 中关于 timeout 尚未实现的描述
  已被 2026-08-09 的实现取代，实施 lane 时应同步校正。

## 下一推荐切片

先实现一个最小但纵向完整的 **live success lane**，不要立即加入 cancel、timeout
或 stall recovery：

1. 注册 `gazebo_acceptance` pytest marker，并以
   `FIRECLAW_RUN_GAZEBO_ACCEPTANCE=1` 显式开启、默认跳过；
2. 增加可信、固定参数的 TurtleBot3 world/navigation launch 与 acceptance config，
   固定 world、map、初始 pose、目标 `frame_id=map` 的有限 `x/y/yaw`；
3. 增加 ROS readiness fixture，至少检查 ROS master、`/move_base` action server、
   `map -> base_link` TF、`/scan` 与仿真时钟；
4. 经当前 FireClaw structured task / Mission Run 主链调用 Plugin-owned
   `navigate_to_point`，不得用独立 action client 代替最终验收；
5. 断言 owner 恰为 `fireclaw.navigation.move-base`，backend 为
   `Ros1MoveBaseBackend`，Adapter 同名 trap 调用次数为零；
6. 断言至少一个 feedback、action success、Robot/Mission success、final report，
   并按 action/task/mission ID 串联事件；
7. 写出最小 proof bundle：run manifest、plugin inventory、goal/feedback、Robot 与
   Mission events、trace、final report、ROS graph/log 和 JUnit。

只有这条成功基线稳定后，再依次加入 live cancel、timeout、abort/unreachable，
最后实现 diagnostics-first 的 recoverable/persistent stall 场景。

## 当前结论

下一步不是继续扩展 Runtime，也不是先做自动恢复，而是把已存在的安全合同接到
真实 `/move_base`，形成当前插件架构下第一个可重复、可判定、可审计的 Gazebo
成功样本。该建议尚待用户确认后实施。

---

## 2026-08-10T01:09:40+08:00 实施更新

### 本轮目标

用户确认开始实施最小纵向 live success lane。目标是让固定 TurtleBot3 Gazebo
环境中的导航请求经过当前 FireClaw Mission、Robot Gateway/Agent、Navigation
Plugin 与真实 `/move_base`，并生成可审计 proof bundle；不得使用独立 action
client 发送验收 goal。

### OpenClaw analogue 判断

Gazebo、ROS1 actionlib、TurtleBot3 world/map 与机器人位姿验收没有直接 OpenClaw
对应模块，因此未从 OpenClaw复制新的模块形状。授权处理复用 FireClaw 已有的
Gateway `/confirm`、一次性 `ExecutionAuthorization` 和 structured task 作用域合同，
没有为测试发明旁路授权协议。

### 新增/修改文件

- `pyproject.toml`
  - 注册 `gazebo_acceptance` pytest marker。
- `extensions/navigation-move-base/config/acceptance/success.yaml`
  - 固定 Burger、seed=0、world/map、初始 `map` pose `(-2.0,-0.5,0)`、目标
    `(2.0,0.0,0)`、owner、backend、action name、readiness、误差与 wall-clock
    timeout。
- `extensions/navigation-move-base/launch/fireclaw_acceptance_world.launch`
  - 固定启动 Gazebo、robot_state_publisher、map_server、AMCL 与 move_base；默认
    headless，LLM 不可传入任意 launch 命令。
- `extensions/navigation-move-base/tests/acceptance/`
  - 新增 scenario 校验、proof artifact 原子写入、ROS readiness/TF/odom/action
    证据采集、FireClaw 主链夹具、pytest、conftest 和可信 runner。
- `tests/test_gazebo_acceptance_harness.py`
  - 无 ROS 测试覆盖 repo-local asset 与 artifact path traversal 防护。
- `extensions/navigation-move-base/README.md`
  - 记录 live lane、正式确认握手、运行命令和当前 Mission lifecycle gap。
- `docs/deployment/ros1-gazebo-debugging-guide.md`
  - 删除已经过时的“deadline 未实现”说法，记录 success proof 与剩余矩阵。
- 删除上述 `config/`、`launch/`、`tests/` 中已被真实内容取代的 `.gitkeep`。

### 主链与断言

验收实际执行：

```text
MissionRunManager
  -> MissionAgent / MissionScheduler
  -> RobotSubagentClient HTTP /tasks
  -> Robot Gateway / Robot Agent
  -> awaiting_confirmation + authorization.requested
  -> authenticated HTTP /confirm
  -> exact one-time ExecutionAuthorization
  -> confirmed Robot Agent task
  -> owner=fireclaw.navigation.move-base
  -> Ros1MoveBaseBackend
  -> /move_base
```

测试还在 core `RobotAdapter` 上安装会抛错的 `navigate_to_point` trap，并断言调用
计数为零；Plugin handler closure 必须解析到 `Ros1MoveBaseBackend`，action name
必须为 `/move_base`。

### 首轮失败与诊断

- 首轮 live run：
  `results/gazebo-acceptance/20260809T170105Z-246399/`
- 结果：pytest 失败，Mission Run 为 `escalated`，机器人位移约 `3e-6 m`。
- 根因不是 Gazebo 或 move_base：structured task 委托 operator 为
  `gazebo-acceptance-operator`，而 loopback Gateway 认证 actor 为
  `local-loopback-operator`，capability policy 正确返回
  `delegated_operator_mismatch`；同时 non-dry physical action 正确要求精确授权。
- 修正：Mission 委托 identity 与受信 loopback principal 对齐，先断言首次任务达到
  `awaiting_confirmation`，再通过正式 HTTP `/confirm` 获取一次性精确授权并执行。
  未关闭 SafetyGate、未伪造 authorization、未直接调用 actionlib client。

### Live 成功证据

1. `results/gazebo-acceptance/20260809T170557Z-250175/`
   - `1 passed, 32 warnings in 36.97s`
   - 实际位移 `3.9660 m`
   - 目标位置误差 `0.01786 m`
   - 目标 yaw 误差 `0.01743 rad`
2. `results/gazebo-acceptance/20260809T170837Z-252659/`
   - `1 passed, 32 warnings in 36.92s`
   - 实际位移 `3.95173 m`
   - 初始位置误差 `0.05869 m`
   - 目标位置误差 `0.02184 m`
   - 目标 yaw 误差 `0.02493 rad`
   - 停车观测：linear `0.000120 m/s`，angular `0.0000241 rad/s`

两次均捕获 `/move_base/goal`、feedback、同 goal ID 的 actionlib
`SUCCEEDED(3)`、FireClaw `action.requested/started/feedback/succeeded`、
`task.completed`、Plugin owner/backend inventory 和 Adapter trap=0。

ROS Noetic 自带 Python 包产生 32 条 `DeprecationWarning`（invalid escape sequence 与
`notifyAll()`），不影响验收结果，未修改 vendored/system ROS 代码。

### 已运行命令

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_gazebo_acceptance_harness.py \
  extensions/navigation-move-base/tests/acceptance/test_gazebo_navigation.py
# 2 passed, 1 skipped（未显式 opt-in 时 live test 按设计跳过）

/home/lpp/miniconda3/envs/py310/bin/python -m compileall -q \
  extensions/navigation-move-base/tests/acceptance
bash -n extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
xmllint --noout \
  extensions/navigation-move-base/launch/fireclaw_acceptance_world.launch

env FIRECLAW_PYTHON=/home/lpp/miniconda3/envs/py310/bin/python \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
# 连续两次 live pass

/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
# 1932 passed, 7 skipped in 165.28s
```

### 当前问题

工程正确性方面，真实 Robot execution success 已闭合；但完整 Mission success 尚未
闭合。Mission Scheduler 将首次 `awaiting_confirmation` child task 视作 terminal
`escalated` 并生成 escalated final report。Gateway `/confirm` 创建的 confirmed
replacement task 能成功，但目前不会关联回原 Mission 并触发 report regeneration。
因此不得把当前结果描述为“Robot 与 Mission 均成功”。

这不是 Navigation Plugin 的 actionlib 翻译问题，也不应在 Plugin 内修复；owner
与 backend 边界已通过。它属于 core Mission/Robot authorization lifecycle 的关联
问题。若修改，需要设计 replacement child task lineage、Mission 等待/恢复语义与
确认后的 final-report 重算，不能仅在测试里改写状态。

### 下一推荐步骤

1. 先设计并实现 Mission 对 authorization-pending child task 与 `/confirm` replacement
   task 的关联/恢复合同，使原 Mission 最终可真实完成；
2. 然后增加 live cancel，验证 cancel goal、停车、`cancelled` 与租约释放；
3. 增加 timeout 与 unreachable/abort；
4. 最后增加 diagnostics-first stall recovery/escalation；
5. 全部矩阵完成前，不宣称 Gazebo acceptance lane 整体完成。

---

## 2026-08-10T01:22:49+08:00 收工快照

### 用户决定

- 用户决定今天先停止，明天继续；本轮不再扩展实现。
- 用户已大致理解本轮 `harness` 的含义。此处专指外部的自动验收程序
  （test harness/测试夹具/测试台），不是 FireClaw Agent 的设计架构。
- harness 可以直接连接 ROS graph 做 readiness 和只读证据采集，但不得自己发送
  `MoveBaseGoal` 或 `/cmd_vel`；真正导航任务必须由 FireClaw Mission 入口提交。

### 当前进展

- 固定 TurtleBot3 world/map/初始位姿/目标的 success acceptance 已实现。
- FireClaw Mission -> Robot Gateway/Agent -> Navigation Plugin ->
  `Ros1MoveBaseBackend` -> `/move_base` 的真实执行链已经连续两次通过。
- 正式 `/confirm` 与一次性精确授权被保留，未通过测试代码绕过 SafetyGate。
- proof bundle、README、部署说明和本记录均已更新。

### 已完成

- live runs：`20260809T170557Z-250175`、`20260809T170837Z-252659` 均通过；
- 全量回归：`1932 passed, 7 skipped in 165.28s`；
- 无 ROS acceptance 合同：`2 passed, 1 skipped`；
- compile、Shell syntax、launch XML、diff whitespace 检查通过；
- 本轮没有 commit 或 push，工作区仍包含此前与本轮的未提交修改。

### 当前问题

- confirmed Robot task 已成功，但原 Mission 在 confirmation 前已终结为
  `escalated`；replacement child task 尚未回链到原 Mission，final report 不能变为
  success。
- 这是 core Mission/Robot authorization lifecycle 问题，不属于 Navigation
  Plugin backend，也不能由 acceptance harness 伪造修复。
- cancel、timeout、abort/unreachable、stall-recover、stall-escalate 的 live lane
  尚未实现。

### 明天第一步

先恢复本记录和当前 diff，不重复搭建 success lane。随后用 CodeGraph 检查：

```text
MissionScheduler terminal projection
MissionAgent child-task/subagent lineage
Robot Gateway /confirm replacement task creation
Mission final-report regeneration
```

目标是先形成一个最小、可审计的 confirmation replacement-task 关联设计，明确
哪些字段负责 original task、replacement task、Mission node 和 authorization
request 的 lineage。设计边界确认后再决定是否修改 core；不要先在测试中强行把
Mission 状态改成成功。

### 需要运行的命令

只有在改动 confirmation lifecycle 后才需要重跑：

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q

env FIRECLAW_PYTHON=/home/lpp/miniconda3/envs/py310/bin/python \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

---

## 2026-08-10T10:40:04+08:00 恢复核对与 confirmation lifecycle 判断

### 本次用户请求

用户要求继续此前工作，并询问下一步应做什么。本次完成了最近两天 memory、当前
worktree 和相关调用链的只读核对；尚未修改业务代码、运行测试、启动 Gazebo、
执行 commit 或 push。

### 当前进展

- worktree 仍是 2026-08-09/10 的大批未提交连续改动，没有新的外部提交；
- live success lane 仍有两次真实 `/move_base` 成功证据；
- 无 ROS 全量回归的最近结果仍为 `1932 passed, 7 skipped`；
- 唯一阻止“Mission 与 Robot 均成功”结论的近期 P0，是 authorization confirmation
  生命周期断链。

### 本次检查的当前实现

使用 CodeGraph 和定向源码核对了：

- `MissionScheduler.schedule()` / `_poll_group_terminal()`；
- `MissionAgent.submit_subtask()` / `mission_trace()`；
- `RobotSubagentClient.submit_task()` / `get_task_trace()`；
- `FireClawGateway._record_authorization_request_if_needed()`、
  `_authorize_confirmation()`、`_record_result_events()` 和 `/confirm`；
- `robot_task_status_from_trace()` 与 canonical terminal normalization；
- live acceptance harness 当前的 confirmation 两阶段断言；
- OpenClaw embedded approval broker 与 managed TaskFlow waiting/resume 形状。

定位到的精确断点：

1. Agent 返回 `awaiting_confirmation`；
2. `build_robot_task_terminal_outcome()` 当前把它映射为 terminal `escalated`；
3. Robot Gateway 将原 queue task 写成 `escalated` 并产生 `task.escalated`；
4. Mission Scheduler 只跟踪这一原始 child task ID，因此立即完成 escalated Mission
   和 final report；
5. `/confirm` 随后调用 `submit_agent()` 创建一个全新随机 task ID；
6. 新 task 能真实导航成功，但 Mission Registry/Scheduler 不知道它属于原 node，
   所以不会重算原 Mission。

### 推荐的最小设计

优先采用 **same-task suspend/resume**，不先引入 replacement-task lineage：

1. 将 `awaiting_confirmation` 定义为 Robot task 的显式非终态等待状态；
2. `_record_result_events()` 对该状态只持久化 pending result、
   `confirmation.pending`、`authorization.requested` 和
   `task.awaiting_confirmation`，不得产生 terminal outcome 或 `ended_at`；
3. `/confirm` 在审批、签发一次性精确授权后，恢复原 `AuthorizationRequest.task_id`，
   不创建第二个 Gateway task；
4. 恢复运行时保留最初提交者 `requested_by` 作为 delegated operator identity，
   将审批者只记录为 `approved_by`，避免把审批者误当成任务发起者；
5. 同一 task 上记录 `authorization.approved`、`task.resume_scheduled`、
   `task.resume_started`，最终只生成一个 canonical terminal outcome；
6. Mission Scheduler 继续轮询同一 child task，确认后自然观察到
   `running -> completed`，原 Mission 和 final report 真实变为 success；
7. 拒绝、过期、Mission cancel 和 coordinator group timeout 必须把等待任务收敛到
   明确终态，不能遗留永久 pending authorization 或非终态 queue record。

该设计复用 OpenClaw 的“审批等待并恢复同一 run”原则；FireClaw 的额外适配是
持久化 Robot task 等待态、精确授权、安全身份分离和 Mission 终态传播。它比创建
replacement task 再维护 original/replacement/node 三重映射更小，也更不易破坏
exact-once 与审计语义。

### 建议实施顺序

1. 先写无 ROS contract tests：pending 非终态、同 ID resume、审批者/发起者身份
   分离、重复确认、过期/拒绝/取消、唯一终态、Mission 最终 success；
2. 实现 Gateway/task-status 的最小改动；
3. 更新 acceptance harness，使其在 Mission 仍为 running 时观察原 child task 的
   `awaiting_confirmation`，确认后等待同一 Mission 完成；
4. 运行定向与全量无 ROS 回归；
5. 最后重跑一次 live Gazebo success，证明 Mission/Robot/action 三层均 success；
6. 基线闭合后再进入 live cancel、timeout、abort/unreachable。

### 下一步建议命令

先做 contract-first 的定向循环：

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_gateway_agent_tool_approval.py \
  tests/test_gateway_execution_authorization.py \
  tests/test_gateway_structured_task.py \
  tests/test_robot_terminal_outcome.py \
  tests/test_mission_scheduler.py \
  tests/test_mission_run.py \
  tests/test_embodied_mission_e2e.py \
  tests/test_gazebo_acceptance_harness.py
```

设计与 contract tests 成形前，不先启动 Gazebo。

---

## 2026-08-10T12:07:51+08:00 same-task suspend/resume 已实现并完成 live proof

### 本次目标与结论

用户明确要求先实现 confirmation lifecycle 修复。本次已完成
**same logical Robot task suspend/resume**：`awaiting_confirmation` 不再被映射成
terminal `escalated`，`/confirm` 不再调用 `submit_agent()` 创建 replacement task，
而是在原 Gateway task ID 上签发精确一次性授权、调度新的进程内 worker attempt，
最终由同一 task 产生唯一 canonical terminal outcome。

该选择复用了先前核对的 OpenClaw 原则：审批挂起与恢复不切断逻辑 run identity；
FireClaw 额外保留了分布式持久化等待态、Robot/mission/task/action 精确 scope、
一次性授权消费和请求者/批准者身份分离。

### 核心实现

修改的主要核心文件：

- `src/fireclaw_core/task/terminal_outcome.py`
  - 从 legacy terminal alias 删除
    `awaiting_confirmation -> escalated`；
  - 将 `awaiting_confirmation` 加入 Robot active statuses；
  - Mission/trace 投影不再把等待确认误判为终态。
- `src/fireclaw_core/gateway/gateway.py`
  - `_record_result_events()` 在存在匹配 `AuthorizationRequest` 时写入
    `task.awaiting_confirmation`、queue status 和 pending result，但不写
    `terminal_outcome`/`ended_at`；
  - 没有可恢复授权请求的 legacy/high-risk fallback 仍安全地终结为
    `escalated`，不会产生永久等待；
  - 新增 `confirm_task()`，在同一 SQLite transaction 和 task-capacity lock 下
    resolve request、persist signed authorization、把原 task 写回 `accepted`，并记录
    `authorization.approved`、`task.resume_scheduled`；
  - `_start_task_worker(..., resumed=True)` 在原 task 上记录
    `task.resume_started`，保留第一次 `started_at`；旧 worker cleanup 只移除与自己
    identity 相同的 control/thread，避免删除已经安装的新 resume attempt；
  - 恢复执行使用 `AuthorizationRequest.requested_by` 作为 delegated execution
    actor，批准者仅写入 `approved_by`；
  - 等待态 cancel 会原子 resolve pending authorization 为 `cancelled`，写
    `authorization.cancelled` 和 `task.cancelled`；
  - authorization expiry 在 `/confirm` 或 Gateway restart reconciliation 时将等待
    task 收敛为 terminal `escalated`；
  - Gateway restart 会保留仍有效的 pending waiting task；若 crash 发生在 durable
    `task.resume_scheduled` 之后、`task.resume_started` 之前，会用持久化授权安全恢复
    原 task；已开始且无可核对 checkpoint 的 side effect 不会盲目 replay；
  - `task_trace()` 对非终态返回 queue pending/running result，使 operator/Mission
    可观察等待态。
- `src/fireclaw_core/infra/runtime_state.py`
  - 新增按 task ID 读取 pending authorization，避免同一 session 多个 pending
    request 时只认最新一条而误终结较早 task。
- `src/fireclaw_core/task/task_state.py`
  - 投影 `task.awaiting_confirmation`、`task.resume_scheduled`、
    `task.resume_started`。
- `src/fireclaw_core/mission/revision_dispatcher.py`
  - `awaiting_confirmation` 加入 active/valid node runtime statuses，重启 checkpoint
    不再把它视为未知状态。

### 合同测试与验收更新

新增/更新的关键合同：

- terminal normalization 明确 waiting 非终态；
- Gateway queue 在 waiting 时 `ended_at is None` 且没有 terminal event；
- HTTP `/confirm` 返回的 task ID 必须等于首次提交 ID，queue 中只有一条 task；
- 同一 task 上必须依次可见 `authorization.requested`、
  `task.awaiting_confirmation`、`authorization.approved`、
  `task.resume_scheduled`、`task.resume_started`、最终 terminal event；
- Agent Tool 的一次性精确授权在同一 task ID 上只消费一次；
- requester 与 approver 身份分离；
- waiting cancel、authorization expiry 和 Gateway restart preservation；
- Mission Scheduler 首次看到 `awaiting_confirmation` 时继续轮询同一 task，之后
  看到 `completed` 才成功完成 Mission。

Gazebo acceptance 已同步修改：

- success scenario 的 confirmation boundary 从 terminal `escalated` 改为 active
  `awaiting_confirmation`；
- `run_mission_until_authorization()` 在 Mission Run 仍为 running 时观察 child task
  waiting，而不是等待错误的 terminal report；
- `/confirm` 后断言 `authorization_task_id == task_id`；
- Robot task 完成后继续等待原 Mission Run，并断言 Mission trace=`succeeded`、
  final report=`completed`。

文档已更新：

- `docs/architecture/agent-tool-approval-resumption.md`；
- `docs/deployment/ros1-gazebo-debugging-guide.md`；
- `extensions/navigation-move-base/README.md`。

### 实际运行与结果

contract-first 首轮确认旧行为按预期失败：

```text
8 failed, 14 passed
```

其中 4 个 HTTP 用例的失败来自 sandbox 禁止 loopback socket；随后经用户批准在
sandbox 外运行。实现后结果：

```text
18 passed                         # terminal/auth/agent-tool/Mission core
4 passed                          # HTTP confirm/expiry/cancel
180 passed                        # 较宽 Gateway/Mission 回归
1935 passed, 7 skipped in 154.42s # 完整无 ROS pytest
```

`git diff --check` 与相关 `compileall` 均通过。

随后运行新的 live lane：

```bash
env FIRECLAW_PYTHON=/home/lpp/miniconda3/envs/py310/bin/python \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

结果：

```text
1 passed in 35.46s
proof bundle: results/gazebo-acceptance/20260810T040627Z-327534/
```

proof 中的关键证据：

- Mission ID：
  `gazebo-acceptance-turtlebot3-move-base-success-20260810T040634Z`；
- confirmation 前后均为同一 Robot task：
  `task-7cfcedf87b494b7880a805b7332e1713`；
- confirmation snapshot：status/result/queue 均为
  `awaiting_confirmation`，`ended_at=null`，且无 `task.escalated`；
- final Robot trace：同一 ID 为 `completed`，包含
  `authorization.approved -> task.resume_scheduled -> task.resume_started ->
  action.succeeded -> task.completed`；
- Mission trace：`succeeded`，唯一 child task 为上述同一 ID 且 `completed`；
- Mission final report：`completed`，`1 completed, 0 requiring attention`；
- 实际位移 `3.9745350952 m`；目标位置误差 `0.0046235353 m`；目标 yaw 误差
  `0.0292899875 rad`；最终速度满足 stopped 合同；
- Adapter trap 未触发，真实 Plugin-owned `Ros1MoveBaseBackend` 与 `/move_base`
  action success 断言通过。

live 输出包含 ROS Noetic Python API 的大量 `DeprecationWarning`（28653 条），不影响
本次结果，但后续可在 runner 层做 warning filter，避免掩盖真正诊断信息。

### 工程与研究层判断

- 工程正确性：原 Mission/Robot identity 断链已经闭合，proof 已同时覆盖
  Mission、Robot task、authorization、Plugin backend、ROS action 和位姿结果。
- 研究有效性：这是一项必要的 safety/audit execution semantics 修复，而不是独立
  方法创新；它使后续关于“可审计人工介入的具身 Agent 执行”实验具备可信基础。
- 发表层贡献：same-task approval resumption 本身不足以构成论文核心 novelty；可作为
  安全执行协议的一部分，需结合不确定性触发策略、多人授权、掉线恢复、形式化
  invariants 和系统性 failure-mode evaluation 才可能形成贡献。

### 仍存边界与下一步

- 当前没有独立 `/reject` endpoint；无权限的 confirm attempt 不会错误终结请求，
  但显式 operator rejection 仍需设计。
- expiry 在 confirm/restart 时收敛；若希望无访问也能准时终结，需要增加 durable
  expiry sweeper/timer，并验证与 confirm/cancel 的 CAS race。
- live cancel、timeout、abort/unreachable、stall-recover、stall-escalate 尚未实现；
  下一步优先做运行中 cancel 和 unreachable/abort，两者比继续扩展 success lane
  更能支撑 safety claim。
- 本轮没有 commit 或 push；工作区保留用户此前及本轮全部未提交修改。

## 2026-08-10T12:55:34+08:00：live cancel lane 已闭合

### 任务目标与设计依据

本轮目标是从 Mission control plane 触发一个正在真实 Gazebo `/move_base` 中
运动的导航任务取消，并证明取消请求、ROS action 停止确认、Robot task 终态、
Mission final report 和审计证据是同一条因果链，而不是 acceptance harness 另开
actionlib client 直接取消。

开始实现前使用 CodeGraph 复核了 Mission Run -> Mission Scheduler ->
RobotSubagentClient -> Robot Gateway task cancel -> RobotActionRuntime ->
Plugin-owned `Ros1MoveBaseBackend` 的调用路径。OpenClaw 类比继续沿用此前检查过的
abort/settle 结构：

- `openclaw/src/agents/embedded-agent-runner/run/attempt-abort.ts`；
- `openclaw/src/agents/embedded-agent-runner/run/attempt-execution-settle.ts`；
- `openclaw/src/agents/embedded-agent-runner/terminal-outcome.ts`；
- `openclaw/src/gateway/chat-abort.ts`。

复用原则是“发出 abort/cancel 请求不等于执行已经终止，必须等待 execution
settle”。FireClaw 的机器人适配额外要求真实 physical stop acknowledgement；仅让
Python wait/thread 退出不能作为机器人已停止的证据。

### 实现中发现并修复的安全语义缺口

原 `MissionScheduler._poll_group_terminal()` 一看到 Mission Run control 的 cancel
flag，就立即合成 child task=`cancelled`。与此同时，
`RobotSubagentClient.cancel_task()` 会把远端仅返回的 `cancel_requested` 直接写成
Subagent Registry 的终态 `cancelled`。这会导致 Mission final report 可能早于
actionlib 的真实停止确认，是安全关键的错误投影。

本轮改为：

- Mission 发出取消后继续轮询原 Robot task ID，不合成成功停止；
- 默认最多等待 `operator_cancel_settle_timeout_seconds=5.0`；
- 只有 Robot/Plugin/backend 返回真实 terminal `cancelled` 且带停止确认时，Mission
  才传播 `cancelled`；
- 等待窗口耗尽时传播 `lost`，并记录
  `cancellation_acknowledged=false`、`runtime_stopped=false`、
  `resource_release_safe=false` 和最后观察到的状态；
- Subagent Registry 保留远端实际返回的 `cancel_requested`，不再提前终态化；
- Mission Run 对 scheduler 的 canonical `cancelled` 使用 `mission.cancelled` 事件，
  不再误发通用 `mission.completed`。

核心修改文件：

- `src/fireclaw_core/mission/mission_scheduler.py`；
- `src/fireclaw_core/mission/mission_run.py`；
- `src/fireclaw_core/subagent/subagent_client.py`；
- `extensions/navigation-move-base/config/acceptance/cancel.yaml`；
- `extensions/navigation-move-base/tests/acceptance/scenario.py`；
- `extensions/navigation-move-base/tests/acceptance/fireclaw_harness.py`；
- `extensions/navigation-move-base/tests/acceptance/ros_harness.py`；
- `extensions/navigation-move-base/tests/acceptance/test_gazebo_navigation.py`；
- `tests/test_gazebo_acceptance_harness.py`、`tests/test_mission_scheduler.py`、
  `tests/test_mission_run.py`、`tests/test_subagent_registry.py`。

### live cancel 场景合同

`cancel.yaml` 继续使用固定单楼层绝对 `map` 坐标、相同 world/initial pose/goal，
并新增以下明确触发与断言：

- 至少收到 1 条本 goal feedback；
- 机器人实际位移至少 `0.15 m` 后才允许请求取消；
- 取消必须经 `MissionRunManager.cancel()`；
- actionlib 终态必须为 `PREEMPTED(2)` 或 `RECALLED(8)`；
- Robot action、Robot task、Mission 和 final report 均须为 canonical
  `cancelled`，不得出现 action success 或 task completed；
- action terminal 必须证明 `cancellation_acknowledged=true`、
  `runtime_stopped=true`、`resource_release_safe=true`；
- action/task terminal event 均唯一；
- 停车/终态等待上限 `5.0 s`，取消后最大位移 `0.50 m`；
- owner 固定为 `fireclaw.navigation.move-base`，backend 固定为
  `Ros1MoveBaseBackend`，Adapter navigation trap 调用次数必须为 0；
- cancel proof 额外生成 `cancellation-evidence.json`。

### 验证命令与结果

合同实现过程结果：

```text
43 passed                         # scenario/scheduler/subagent contracts
68 passed, 2 skipped              # focused acceptance/action/runtime regression
198 passed                        # sandbox 外 loopback Gateway/Mission regression
```

完整无 ROS 回归（部分 Gateway 测试需要绑定 loopback，已在 sandbox 外执行）：

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
```

结果：

```text
1939 passed, 7 skipped in 156.96s
```

同时通过：

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m compileall -q \
  src/fireclaw_core extensions/navigation-move-base/tests/acceptance
bash -n extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
git diff --check
```

### 真实 Gazebo proof

第一次 live cancel run：

```text
results/gazebo-acceptance/20260810T044821Z-351738/
```

物理取消实际成功，但测试曾错误要求跨线程的 `task.cancel_requested` 日志一定早于
`action.cancel_requested`。实际运行中 action runtime 在 Mission cancel flag 设置后
立即开始安全停机，action event 比 HTTP/Mission task event 早约 5 ms。这不是因果
错误，反而是更快的安全响应，因此删除了这个不成立的全局日志顺序断言；仍保留同一
action 内 `action.cancel_requested -> action.cancelled -> task.cancelled` 的必要顺序
以及所有 request/terminal 存在性和唯一性断言，没有人为延迟物理停止来迎合日志。

通过的 live cancel proof：

```bash
env FIRECLAW_PYTHON=/home/lpp/miniconda3/envs/py310/bin/python \
  FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO=/home/lpp/fireclaw-master/extensions/navigation-move-base/config/acceptance/cancel.yaml \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

```text
1 passed, 1 skipped, 33 warnings in 14.46s
proof: results/gazebo-acceptance/20260810T045030Z-353594/
```

关键证据：

- Mission ID：
  `gazebo-acceptance-turtlebot3-move-base-cancel-20260810T045036Z`；
- confirmation、执行与取消始终使用同一 Robot task：
  `task-87dcd0016d76456181b9aa0729eff9f7`；
- Robot action：`action-b0a99d6dc45b4a769469cc441bb132fa`；
- actionlib goal state=`2/preempted`；action/Robot/Mission/final report 均为
  `cancelled`；
- `cancellation_acknowledged=true`、`runtime_stopped=true`、
  `resource_release_safe=true`；
- cancel HTTP 请求耗时 `0.04679891 s`；Robot task 终态延迟
  `0.57467485 s`；停车延迟 `0.98305097 s`；Mission 终态延迟
  `0.98309295 s`；
- 触发时已有 11 条 feedback，实际位移 `0.15367457 m`；取消后位移
  `0.11208323 m`，低于 `0.50 m` 上限；
- 停车时线速度约 `0.0001215 m/s`、角速度约 `0.0002455 rad/s`；
- 取消时距目标仍约 `3.8207 m`，排除“恰好已经到达目标”的伪取消；
- Adapter trap=0；manifest=`passed`；runner 清理后无遗留 ROS/Gazebo 进程。

加入 cancel lane 后又重跑 success regression：

```text
1 passed, 1 skipped, 32 warnings in 34.75s
proof: results/gazebo-acceptance/20260810T045225Z-355322/
```

warning 均为 ROS Noetic Python API deprecation，不影响验收结论。

### 当前结论与下一步

- 工程正确性：Gazebo acceptance 的 live success 与 live cancel 两条纵向链路均已
  通过；cancel proof 已覆盖 Mission control、same-task Robot identity、Plugin-owned
  backend、真实 actionlib preemption、停止确认、终态传播、停车/位移边界和审计包。
- 研究有效性：该修复为“安全约束的执行与恢复”研究提供可信的 stop/settle 基线，
  但 cancel lane 本身仍是必要工程基础，不是独立论文创新。
- 下一步优先做 live timeout：deadline 后主动 cancel goal；有停止确认时必须投影为
  `timed_out`，无确认时必须为 `lost`。之后再做 unreachable/abort，最后进入
  diagnostics-backed stall-recover/stall-escalate。
- 本轮没有 commit 或 push；保留工作区既有和本轮未提交修改。

## 2026-08-10T13:52:06+08:00：live timeout lane 已闭合

### 任务目标与恢复快照

用户要求在已通过的 live success、same-task authorization resume 和 live cancel
基础上继续实现 live timeout lane。目标不是让测试代码或操作员主动取消 Mission，
而是证明 Plugin-owned physical action 的 monotonic execution deadline 在真实
Gazebo `/move_base` 执行中自动触发安全停止，并把停止确认后的原始原因沿同一
action/task 传播为 canonical `timed_out`。

开始时先按项目恢复策略读取 2026-08-09 与 2026-08-10 的最新 memory，并检查
dirty worktree；大量既有修改均为用户此前工作，本轮只增量修改 timeout 相关文件，
没有覆盖或清理其他改动。CodeGraph 索引存在且健康，先用它复核了
`RobotActionRuntime -> PhysicalToolSpec -> Plugin handler -> Ros1MoveBaseBackend`
以及 `MissionRun -> MissionScheduler -> MissionAgent.mission_trace ->
JsonlMissionRegistry` 的状态传播路径。

OpenClaw 类比继续采用同日已检查并记录的 abort/settle 结构：

- `openclaw/src/agents/embedded-agent-runner/run/attempt-abort.ts`；
- `openclaw/src/agents/embedded-agent-runner/run/attempt-execution-settle.ts`；
- `openclaw/src/agents/embedded-agent-runner/terminal-outcome.ts`；
- `openclaw/src/gateway/chat-abort.ts`。

复用原则仍是“发出 timeout/cancel 请求不是终态，必须等待 execution settle”。
FireClaw 的机器人差异是必须额外证明 physical stop acknowledgement；只结束等待
线程不能释放 motion resource，也不能宣称机器人已停止。

### 实现前发现的缺口

CodeGraph/source 检查确认 `RobotActionRuntime` 与 `Ros1MoveBaseBackend` 原本已经
具备正确的底层机制：到达 deadline 后设置 cooperative cancellation signal，
backend 调用 `cancel_goal()` 并等待 actionlib terminal；有停止确认则 deadline
原因映射为 `timed_out`，无确认则为 `lost`。

真正缺少的是纵向验收和两个上层合同：

1. Navigation Plugin physical contribution 把 `timeout_seconds=120.0` 硬编码在
   工厂调用中，没有可信部署配置入口，无法在 acceptance 中用短而确定的 deadline；
2. `MissionFailurePolicy.on_timed_out` 默认是 `retry`，会盲目重试已经超时的物理
   导航；重试预算耗尽后 Scheduler 的 group 聚合还可能错误返回 `succeeded`；
3. Mission Run 对非成功结果统一发 `mission.completed`，使 timeout 的事件语义
   不可审计；
4. acceptance harness 只有 success/cancel scenario，没有自动 deadline、停止确认、
   唯一 terminal event、Mission propagation 和 timeout proof bundle 的 live 合同。

先补测试形成 red baseline，结果为 `5 failed, 32 passed`：缺少 timeout scenario、
Plugin config 未生效、默认策略仍 retry、Scheduler 错误 succeeded，以及 Mission
误发 `mission.completed`。实现后同组为 `37 passed`，扩大到 action/runtime、
Mission 和 acceptance 相关测试为 `192 passed`。

### 代码与合同修改

新增/修改的本轮核心文件：

- `extensions/navigation-move-base/config/acceptance/timeout.yaml`：固定单楼层
  `map` initial/goal，`execution_timeout_seconds=3.0`、停止确认窗口 `5.0 s`、
  feedback/位移触发、停止后最大位移 `0.50 m`，期望 `timed_out` 和 actionlib
  `PREEMPTED(2)`/`RECALLED(8)`；
- `extensions/navigation-move-base/tests/acceptance/scenario.py`：新增
  `TimeoutExpectation`、timeout/cancel 互斥和 deadline/observation 边界校验；
- `extensions/navigation-move-base/plugin/entrypoint.py`、`plugin/move_base.py`：
  physical contribution 接受可信 Plugin config
  `navigate_timeout_seconds` 与 `cancellation_ack_timeout_seconds`，生产默认仍为
  `120.0/2.0 s`；这两个值不是 LLM Tool inputs；
- `extensions/navigation-move-base/tests/acceptance/fireclaw_harness.py`：仅对
  timeout acceptance 注入 `3.0/5.0 s` 配置，inventory 记录实际 deadline，并新增
  Robot event 等待器；
- `extensions/navigation-move-base/tests/acceptance/ros_harness.py`：success 之外，
  cancel/timeout 均能等待本 goal 的 live feedback 与实际移动；
- 新增 `extensions/navigation-move-base/tests/acceptance/test_gazebo_timeout.py`：
  通过原 Mission/auth/Plugin path 执行，不创建独立 action client，不调用 Mission
  cancel；断言 same task ID、真实移动、`deadline_exceeded`、actionlib stop ack、
  唯一 `action.timed_out/task.timed_out`、无 success/cancel terminal、无 retry、
  Mission Run/final report=`timed_out`、`mission.timed_out` 事件、停车延迟/位移边界、
  Adapter trap=0，并写 `timeout-evidence.json`；
- `src/fireclaw_core/mission/mission_scheduler.py`：默认
  `on_timed_out="abort"`，abort 仅决定停止剩余计划，不改写根因；group abort 聚合
  保留 `lost/timed_out/escalated/blocked/failed` 原始终态；
- `src/fireclaw_core/mission/mission_run.py`：为各 canonical terminal status 建立
  明确事件映射，timeout 发 `mission.timed_out`，不再发 `mission.completed`；
- `tests/test_gazebo_acceptance_harness.py`、
  `tests/test_move_base_navigation_plugin.py`、`tests/test_mission_scheduler.py`、
  `tests/test_mission_run.py`：补齐上述确定性合同；
- `extensions/navigation-move-base/README.md` 与
  `docs/deployment/ros1-gazebo-debugging-guide.md`：记录 trusted timeout config、
  三条 live 命令、stop acknowledgement 语义、proof 和下一步。

### 真实 Gazebo timeout proof

执行命令：

```bash
env FIRECLAW_PYTHON=/home/lpp/miniconda3/envs/py310/bin/python \
  FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO=/home/lpp/fireclaw-master/extensions/navigation-move-base/config/acceptance/timeout.yaml \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

结果：

```text
1 passed, 2 skipped, 31 warnings in 16.48s
proof: results/gazebo-acceptance/20260810T054233Z-385083/
```

关键 ID 与量化证据：

- Mission：`gazebo-acceptance-turtlebot3-move-base-timeout-20260810T054239Z`；
- same Robot task：`task-ec4efe8917814be4885c6198069b195e`；
- Robot action：`action-08024b994742410dafd1f6c3503802ea`；
- deadline 前已收到 11 条本 goal feedback，实际位移 `0.1508521601 m`；
- 配置 deadline=`3.0 s`，action output elapsed=`3.1443902870 s`；
- `action.cancel_requested` 的原因明确为 `deadline_exceeded`，不是 operator/Mission
  cancel；Robot events 中不存在 `task.cancel_requested`；
- actionlib goal state=`2/preempted`，`cancellation_acknowledged=true`、
  `runtime_stopped=true`、`resource_release_safe=true`；
- Robot task terminal latency=`0.5494276090 s`；停车 latency=`0.8613257360 s`；
  Mission terminal latency=`0.8613652660 s`；
- timeout request 后位移=`0.0655292345 m`，停车线速度
  `0.0001486411 m/s`、角速度 `0.0006048410 rad/s`；
- action、Robot task、Mission Run、Mission event 和 final report 均保留
  `timed_out`；failure decision 唯一为 `abort`，没有重试；
- Adapter navigation trap=0，Plugin owner=`fireclaw.navigation.move-base`，backend
  为真实 `Ros1MoveBaseBackend`。

已知兼容边界：`JsonlMissionRegistry._mission_status()` 当前仍把 child
`timed_out` 聚合为 Mission trace 的粗粒度 `failed`；child、Mission Scheduler/Run、
`mission.timed_out` 事件与 final report 均保留 canonical `timed_out`。本轮不把这一
历史聚合接口扩成新状态，避免在 live lane 内隐式扩大到 trace stream、memory
consolidation 和多 child precedence 的迁移；该项已明确写入部署文档，后续
canonical terminal outcomes 工作应统一完成迁移。

### 回归、失败尝试与最终验证

timeout 改动后回归既有 live lane：

```text
cancel:  1 passed, 2 skipped in 14.69s
proof:   results/gazebo-acceptance/20260810T054452Z-387134/

success: 1 passed, 2 skipped in 34.65s
proof:   results/gazebo-acceptance/20260810T054558Z-388633/
```

一次无效尝试使用了不存在的 `single_floor.yaml`，fixture 在读取场景配置时即以
`FileNotFoundError` 失败，没有启动 ROS/Gazebo，也不是产品代码回归；失败 artifact
为 `results/gazebo-acceptance/20260810T054520Z-388086/`。确认实际文件为
`success.yaml` 后重跑通过。保留此记录以避免下次重复使用错误文件名。

完整无 ROS 回归（Gateway 测试需要 loopback，sandbox 外执行）：

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
```

```text
1943 passed, 7 skipped in 155.31s
```

同时通过：

```text
python -m compileall -q ...                  # pass
bash -n run_gazebo_acceptance.sh             # pass
git diff --check                             # pass
codegraph status                             # index up to date
```

ROS warnings均为 Noetic Python API deprecation，不影响本轮验收判断。本轮没有
commit 或 push。

### 当前结论与下一步

- 工程正确性：live success/cancel/timeout 三条纵向链均通过；timeout 证明了真实
  deadline、acknowledged physical stop、same-task terminal propagation、无盲重试、
  资源安全释放和可重建 proof bundle。
- 研究有效性：这建立了“受安全约束的诊断与恢复”研究所需的 timeout/settle
  control baseline，可用于后续比较恢复率、延迟、不安全提议与 stale-state error；
  但单独一条 timeout lane 仍是工程基础，不是论文 novelty。
- 发表层贡献：下一阶段必须加入真实 failure cause、typed diagnostics、bounded
  recovery/escalation 和对照实验，才能支撑分层诊断与恢复的研究主张。
- 下一步优先实现 live unreachable/abort，保留 move_base abort 的 planner/backend
  evidence 并正确映射 Robot/Mission 终态；之后做 progress watchdog +
  `navigation_diagnostics` 的 stall-recover/stall-escalate。

## 2026-08-10T14:28:18+08:00：live abort/unreachable lane 已闭合

### 任务目标与恢复状态

用户在 live success、same-task authorization resume、live cancel 与 live timeout
均已闭合后，确认继续下一步。按既定优先级，本轮实现真实 Gazebo
abort/unreachable lane：固定一个可证明确实位于 live map 数值边界外的目标，让
Plugin-owned `Ros1MoveBaseBackend` 驱动真实 `/move_base` 自己返回
actionlib `ABORTED(4)`，而不是由 operator/Mission cancel 或 Runtime deadline
制造失败；随后验证同一 Robot task、Mission Run、Mission event 和 final report
正确传播为 canonical `failed`，并保存 planner/backend proof。

开始工作前按恢复规则读取了 `memory/2026-08-09/` 和
`memory/2026-08-10/gazebo-acceptance-next-slice.md`，检查 dirty worktree，并保留
用户已有的大量未提交修改。没有 reset、checkout、删除、commit 或 push。

### CodeGraph 与 OpenClaw 类比

`.codegraph/` 存在且最终 `codegraph status` 为 up to date。CLI 当前没有
`codegraph context` 子命令（返回 `unknown command 'context'`），因此按项目规则
改用 `codegraph explore`，检查了：

- `Ros1MoveBaseBackend.navigate_to_point` 的 actionlib 状态映射与停止确认；
- `RobotActionRuntime._normalize_backend_terminal/_emit_terminal`；
- Robot task canonical terminal normalizer；
- `MissionScheduler._evaluate_group_failures` 和 abort 聚合；
- acceptance scenario/parser/harness；
- move_base C++ planner failure 分支。

OpenClaw analogue 实际检查文件：

- `openclaw/src/agents/embedded-agent-runner/run/attempt-abort.ts`；
- `openclaw/src/agents/embedded-agent-runner/run/attempt-execution-settle.ts`；
- `openclaw/src/agents/agent-run-terminal-outcome.ts`。

更正同日 timeout 记录中的一个旧路径：
`openclaw/src/agents/embedded-agent-runner/terminal-outcome.ts` 并不存在，真实 terminal
outcome owner 是 `openclaw/src/agents/agent-run-terminal-outcome.ts`。复用的结构原则
是：terminal owner 保留实际 cause，abort 是控制决策而非新的运行结果；settle 与
cleanup 由中心执行层负责。FireClaw 的机器人差异是还必须保存 actionlib 状态、
planner text、physical stop acknowledgement 和 motion resource release evidence。

### 实现前发现的真实缺口

1. `Ros1MoveBaseBackend` 已将 actionlib state `4` 映射为 `aborted`，也把它视为
   stop-confirmed，但没有保存 `SimpleActionClient.get_goal_status_text()`，也没有
   稳定的 backend error code，planner 失败原因只能从 ROS topic 旁路观察。
2. Robot action 已正确发 `action.failed(status=aborted)`；Robot Agent 再把失败 skill
   规范化为 task-level `failed`。因此 task terminal outcome 的 `raw_status` 实际是
   `failed`，不是 `aborted`；原生 cause 的权威位置是 `action.failed.output`。第一次
   live test 对 task trace 做了过强的 `raw_status=aborted` 假设，后续按真实分层修正，
   没有篡改产品状态语义。
3. 默认 `MissionFailurePolicy.on_failed="reassign"` 在单机器人环境找不到替代机器人
   时写 `decision=skipped, reason=no_alternative_robot`，随后 Scheduler 因没有 abort
   或 escalate 决策而错误返回 `succeeded`。这是会让真实 navigation abort 在 Mission
   层被静默吞成成功的工程缺陷。
4. acceptance contract 只有 success/cancel/timeout，没有 abort 场景、live map
   bounds、实际 planner 参数、native status text、无 cancel/timeout 反证、失败终态
   传播和 proof bundle。
5. 固定地图 YAML 为 resolution `0.05`、origin `[-10,-10]`、PGM `384x384`；数值
   上界约为 `(9.2,9.2)`。固定目标 `(9.5,9.5)` 可由 live `/map` OccupancyGrid 再次
   计算并证明位于地图外。move_base 源码对持续无有效 plan 的终态 text 为
   `Failed to find a valid plan. Even after executing recovery behaviors.`。

### Red baseline

先补三条确定性红测：

- `test_abort_scenario_requires_real_move_base_failure_proof`；
- `test_ros1_move_base_aborted_preserves_actionlib_failure_evidence`；
- `test_scheduler_aborts_failed_subtask_when_no_reassign_robot_exists`。

初次运行分别因为缺少 `abort.yaml`、缺少 `error_code`、以及 Scheduler 返回
`succeeded` 而失败。Scheduler 测试最初漏传 `MissionSubtask.floor`，先修正该测试
输入，再确认它确实以 `succeeded != failed` 捕获产品缺陷。实现后加上既有 reassign
正向回归为 `4 passed`；扩展到 acceptance/plugin/scheduler 聚焦组为 `17 passed`，
terminal/Mission Run/scheduler 组为 `39 passed`。

### 代码与合同修改

- `extensions/navigation-move-base/config/acceptance/abort.yaml`
  - `scenario_type=abort`，固定目标 `(9.5,9.5)`；
  - 期望 Robot/Mission `failed`、actionlib `[4]`；
  - trusted `planner_patience_seconds=2.0`、
    `recovery_behavior_enabled=false`；
  - 至少 1 条 feedback、terminal 上限 `15 s`、位移上限 `0.50 m`、required status
    substring=`valid plan`。
- `extensions/navigation-move-base/tests/acceptance/scenario.py`
  - 新增 `AbortExpectation`、manifest 序列化、互斥配置和边界校验；
  - 明确 abort 只能接受 canonical `failed` 与 actionlib `ABORTED(4)`。
- `extensions/navigation-move-base/plugin/move_base.py`
  - 保存 `goal_status_text`；
  - state 4/5 分别给出 `move_base_aborted`/`move_base_rejected`；
  - 保留既有 raw action status、`runtime_stopped` 与
    `resource_release_safe`。
- `src/fireclaw_core/mission/mission_scheduler.py`
  - reassign 找不到替代机器人或已耗尽 reassign budget 时，显式记录
    `decision=abort`、`requested_decision=reassign` 和原因并停止 Mission；
  - 原始 child status 仍由 `_aborted_group_terminal_status` 决定，普通 reassign
    有备选机器人时的成功路径不变。
- `extensions/navigation-move-base/launch/fireclaw_acceptance_world.launch`
  - 增加可信固定 launch args `planner_patience` 与
    `recovery_behavior_enabled`，默认仍为 `5.0/true`；
  - 仅 abort runner 覆盖为 `2.0/false`，不是 LLM Tool 参数。
- `run_gazebo_acceptance.sh`
  - 场景路径 canonicalize 并限制为 repository-local；
  - runner 始终导出实际场景路径；仅精确匹配固定 `abort.yaml` 时注入 abort 参数。
- `ros_harness.py`
  - 从 live `/map` 的 resolution、width、height、origin/yaw 计算四角和 AABB；
  - 从 ROS parameter server 读取并类型检查两个实际导航参数。
- 新增 `test_gazebo_abort.py`
  - 复用 Mission Run -> same-task `/confirm` -> Robot Agent -> Plugin ->
    `/move_base` 主链；
  - 断言 live map 外目标、实际参数、goal、feedback、`ABORTED(4)`、planner text、
    `move_base_aborted`、安全停止、近零位移、唯一 action/task terminal、Mission
    failure decision、final report 和 Adapter trap=0；
  - 明确禁止 action/task/Mission cancel、timeout、success、lost、retry、reassign；
  - 输出 `abort-evidence.json`、`map-evidence.json`、
    `navigation-parameters.json` 和既有完整 proof bundle。
- 更新 acceptance unit tests、Plugin backend tests、Scheduler tests、根 README、
  Navigation Plugin README 与 Gazebo debugging guide。

### 真实 Gazebo 执行与证据

执行命令：

```bash
env FIRECLAW_PYTHON=/home/lpp/miniconda3/envs/py310/bin/python \
  FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO=/home/lpp/fireclaw-master/extensions/navigation-move-base/config/acceptance/abort.yaml \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

第一次 run：

```text
1 failed, 3 skipped in 15.31s
artifact: results/gazebo-acceptance/20260810T061242Z-407809/
```

该 run 的产品链已经正确得到 `ABORTED(4) -> action.failed -> task.failed ->
mission.failed`。唯一失败是测试错误要求 task trace `raw_status=aborted`；实际 native
cause 正确保存在 action output，而 task-level raw status 是 Robot Agent 的
`failed`。修正分层断言后通过：

```text
1 passed, 3 skipped, 33 warnings in 15.76s
proof: results/gazebo-acceptance/20260810T061445Z-409910/
```

通过 proof 的关键量化证据：

- same Robot task：`task-f4fb87be067e43ff9b8c5f989a39ee69`；
- actual map bounds：x/y 均为 `[-10.0, 9.2000002861]`，goal `(9.5,9.5)` 的
  `goal_inside_axis_aligned_bounds=false`；
- actual params：`planner_patience_seconds=2.0`、
  `recovery_behavior_enabled=false`；
- actionlib state=`4/aborted`，status text 完整保存；
- action output：`error_code=move_base_aborted`、`runtime_stopped=true`、
  `resource_release_safe=true`；
- feedback count=`23`；Robot 位移仅 `5.07e-06 m`；
- Robot task terminal latency=`3.7935 s`，Mission terminal latency=`4.0915 s`；
- Mission events 依次包含 `mission.run_accepted`、`mission.planned`、
  `mission.subtask_dispatched`、`mission.report_ready`、`mission.failed`；
- Adapter trap count=`0`；manifest=`passed`；artifact count=`45`。

abort 后回归原有三条 live lane：

```text
timeout: 1 passed, 3 skipped in 15.87s
proof:   results/gazebo-acceptance/20260810T061546Z-411283/

cancel:  1 passed, 3 skipped in 14.72s
proof:   results/gazebo-acceptance/20260810T061720Z-412795/

success: 1 passed, 3 skipped in 35.70s
proof:   results/gazebo-acceptance/20260810T061916Z-414470/
```

ROS warnings 仍是 Noetic Python API deprecation，不改变验收结论。

### 最终验证、时序抖动与当前结论

通过的静态/确定性检查：

```text
py_compile selected changed Python files       pass
bash -n run_gazebo_acceptance.sh               pass
ElementTree parse acceptance launch XML        pass
git diff --check                               pass
codegraph status                               index up to date
ordinary acceptance collection                 4 skipped
```

第一次全量 pytest 为：

```text
1945 passed, 7 skipped, 1 failed in 154.91s
```

唯一失败是既有
`test_gateway_admin_emergency_stop_cancels_active_task_and_records_audit_events`，一次将
预期 `cancelled` 观察为 `lost`。该测试与本轮 abort/scheduler 改动无调用路径关系；
隔离重跑立即 `1 passed in 1.28s`。没有为了掩盖该抖动修改 emergency-stop 代码。
随后第二次完整回归干净通过：

```text
1946 passed, 7 skipped in 156.44s
```

- 工程正确性：live success/cancel/timeout/abort 四条纵向链现均闭合，涵盖真实
  `/move_base` success、operator cancellation、trusted deadline、安全停止确认、
  native planner abort、same-task terminal propagation、Mission final report 和
  proof bundle。`on_failed=reassign` 的单机器人 silent-success 缺陷也已关闭。
- 研究有效性：abort lane 提供了真实 failure-cause/typed evidence 基线，可用于后续
  比较“无诊断、仅摘要、摘要+按需证据、中心诊断、机器人本地诊断”。但固定地图外
  目标本身是工程 acceptance，不是研究 novelty。
- 发表层贡献：下一步必须进入 diagnostics-first stall recovery/escalation，加入
  progress watchdog、权威状态快照、Robot-local typed diagnostics、evidence refs、
  bounded recovery/replan 和不确定时 operator escalation，才能支撑“受安全约束的
  分层诊断与恢复”主张。
- 下一推荐步骤：实现 `stall-recover`（诊断后一次有界 clear-costmaps/replan 成功）
  与 `stall-escalate`（持续堵塞/状态不确定，预算耗尽后 blocked/escalated）两条 live
  lane；不要先投入第三方沙箱、Web UI、ROS2 或多楼层。
- 本轮没有 commit 或 push。

## 2026-08-10 15:39 +08 — diagnostics-first stall-recover / stall-escalate

### 任务目标与用户决定

用户明确要求继续 Gazebo acceptance lane，先实现 diagnostics-first
`stall-recover`，随后实现 `stall-escalate`。本轮保持此前确定的优先级：只闭合
确定性 ROS/Gazebo engineering integration，不把它混入 LLM planning evaluation，
也不先投入第三方插件沙箱、Web UI、ROS2 或多楼层。

启动时按项目恢复规则只读取最近两日 memory，检查当前 dirty worktree，并沿用同日
success/cancel/timeout/abort proof。所有大量既有未提交修改均保留；没有 reset、
checkout、删除、commit 或 push。

### CodeGraph 与 OpenClaw analogue

在定位和修改结构性代码前使用 CodeGraph 检查：

- `RobotAgentDeliberationRuntime.run/skill_transition`；
- `BoundedAgentLoop` terminal/checkpoint/reconciliation；
- `FireClawAgent.finalize_deliberated_task`；
- `Gateway._run_robot_agent_structured_task`、授权请求生成和 result event 归一化；
- Mission deterministic intent detection 与 point target parsing；
- navigation/diagnostics Plugin 的 Tool 投影与 ROS backend。

检查的 OpenClaw analogue：

- `openclaw/src/agents/agent-run-terminal-outcome.ts`；
- `openclaw/src/agents/embedded-agent-runner/run/attempt-abort.ts`；
- `openclaw/src/agents/embedded-agent-runner/run/attempt-execution-settle.ts`；
- `openclaw/src/agents/embedded-agent-runner/run/attempt-timeout-prepare.ts`。

复用原则：一个 terminal owner、deadline/cleanup 有界、side-effect outcome 不确定时
禁止自动 replay。FireClaw 的额外机器人约束是 exact physical authorization、
actionlib stop acknowledgement、motion lease release、Robot-local typed diagnostics、
evidence references 与有界物理恢复预算。

### 方法边界与实现选择

两条 lane 都是 deterministic integration policy，不调用 LLM：

```text
Mission Run / point target
  -> same Robot task awaiting_confirmation
  -> authenticated /confirm on the same task ID
  -> Plugin-owned navigate_to_point -> real /move_base
  -> trusted 8 s action deadline -> acknowledged PREEMPTED/RECALLED
  -> Robot-local navigation_diagnostics
  -> recover: one parameter mutation + one exact-goal retry -> completed
  -> escalate: zero mutation/retry -> evidence-backed escalated
```

故障只由可信 launch 注入：精确选择 `stall-recover.yaml` 或
`stall-escalate.yaml` 时传 `inject_stall=true`，然后把 live
`/move_base/DWAPlannerROS/max_vel_x` 与 `min_vel_x` 固定为 `0.0`。该参数不由
Agent/LLM 提供，普通 scenario 不启用。recover 只恢复
`max_vel_x=0.22,min_vel_x=0.0`；escalate recovery budget 为零。

当前 fault 是可重复的速度参数 stall，不等同于真实 costmap obstacle、通道堵塞或
泛化故障诊断。`navigation_diagnostics` 必须至少包含
`navigation_action_failed`；其他有限采样 finding 会受 ROS 消息窗口影响。该 lane
证明的是诊断/证据/恢复顺序和终态传播，不是可泛化故障归因或论文 novelty。

### Red tests 与实现文件

先加入并观察红测：

- `test_stall_recover_scenario_requires_diagnostics_and_one_retry`；
- `test_stall_escalate_scenario_requires_diagnostics_without_recovery`；
- `test_trusted_runner_injects_stall_only_for_exact_stall_scenarios`；
- `test_robot_deliberation_escalation_preserves_evidence_references`；
- live 调试期间新增
  `test_robot_deliberation_preserves_physical_confirmation_boundary`；
- live 调试期间新增
  `test_deliberated_confirmation_preserves_exact_physical_plan`。

主要修改：

- `extensions/navigation-move-base/config/acceptance/stall-recover.yaml`；
- `extensions/navigation-move-base/config/acceptance/stall-escalate.yaml`；
- `extensions/navigation-move-base/tests/acceptance/scenario.py`：新增严格 typed
  `StallExpectation` 与互斥验证；
- `extensions/navigation-move-base/launch/fireclaw_acceptance_world.launch`：新增固定
  `inject_stall` group；
- `run_gazebo_acceptance.sh`：只对两个精确 repo-local scenario 启用 stall；
- `fireclaw_harness.py`：新增 observation-driven
  `DiagnosticsFirstStallPolicy` 与 bounded Robot Agent limits；
- `ros_harness.py`：通过 dynamic_reconfigure 读取 actual DWA 参数；
- 新增 `test_gazebo_stall.py`：完整 Mission/Gateway/Robot/Plugin/ROS proof；
- `robot_deliberation.py`：blocked/escalated 结果保留 `evidence_ids`；物理
  `require_confirmation` observation 规范化为 `approval_required`；
- `agent.py`：deliberated awaiting-confirmation 聚合结果保留待授权的 exact one-step
  plan，使 Gateway 能生成 action hash 和 authorization request；
- 更新 acceptance/unit tests、根 README、Navigation README 和 Gazebo guide。

### Live 调试中发现并修复的三个真实边界问题

1. 新 scenario 命令最初写成“导航到地图坐标…”，Mission deterministic planner 的
   patrol pattern 只识别“导航测试/简单移动”等关键词，导致
   `clarification_required`。改为“导航测试到地图坐标…”，不修改生产 intent API。
2. bounded Robot Agent 首次 physical Tool 遇到 `require_confirmation` 时，原
   observation 被 execution=`failed` 覆盖，loop/finalizer 将其当 terminal
   escalation。现在 observation 保持 `approval_required`，task result 投影为非终态
   `awaiting_confirmation`；确认后仍使用原 task ID。
3. `finalize_deliberated_task` 把 pending step 的 `planning.plan` 丢成 `None`，Gateway
   无法构造 exact authorized action，于是 pending authorization 不存在并 fail-safe
   `task.escalated`。现在仅在 awaiting-confirmation 时保存真实 single-step plan；
   授权规则没有放宽。

另有一个 acceptance 断言错误：Mission Registry 成功 aggregate trace 使用兼容
`succeeded`，Robot task/Mission Run/final report 使用 canonical `completed`。按既有
success lane 分层修正断言，没有改变产品状态。

### 真实 Gazebo proof

统一命令格式：

```bash
env FIRECLAW_PYTHON=/home/lpp/miniconda3/envs/py310/bin/python \
  FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO=<absolute scenario yaml> \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

`stall-recover` 最终通过：

```text
1 passed, 4 skipped, 42 warnings in 41.19s
proof: results/gazebo-acceptance/20260810T071645Z-455332/
```

关键证据：

- same task ID：`task-a282df4d72a5463abe4f83aefea79f94`；
- 第一次 action elapsed `8.0692876 s`，actionlib `PREEMPTED(2)`；
- `cancellation_acknowledged=true`、`runtime_stopped=true`、
  `resource_release_safe=true`；
- first feedback count=`80`，max displacement=`8.646e-06 m`；
- diagnostics evidence ID=`rosdiag-dd0cc09caf47544877027d25`，含
  `navigation_action_failed`；
- recovery attempt count=`1`，DWA before `max_vel_x=0.0`，after `0.22`；
- 第二个 goal 与第一个目标完全相同，elapsed `4.4854437 s`，
  actionlib `SUCCEEDED(3)`；
- Robot task/Mission Run/final report completed；Adapter trap=0。

`stall-escalate` 最终通过：

```text
1 passed, 4 skipped, 37 warnings in 28.37s
proof: results/gazebo-acceptance/20260810T071900Z-457933/
```

关键证据：

- same task ID：`task-e9ab73dcc3814545bb7106f1a86d5185`；
- 唯一 action elapsed `8.0648354 s`，actionlib `PREEMPTED(2)`；
- first feedback count=`80`，max displacement=`8.627e-06 m`；
- diagnostics evidence ID=`rosdiag-7612532dd2f35fe04bf7c470`，含
  `navigation_action_failed`；
- recovery attempt count=`0`，goal count=`1`，DWA before/after 均为零；
- 没有 `move_base_set_parameters`、clear-costmap 或第二次导航；
- `persistent_navigation_stall` 与 evidence ID 传播到 Robot task、Mission Run 和
  final report 的 `escalated` 终态；Adapter trap=0。

加入 stall 后四条既有 live lane 回归：

```text
success: 1 passed, 4 skipped in 35.30s
proof:   results/gazebo-acceptance/20260810T072109Z-460225/

cancel:  1 passed, 4 skipped in 14.44s
proof:   results/gazebo-acceptance/20260810T072157Z-461618/

timeout: 1 passed, 4 skipped in 16.35s
proof:   results/gazebo-acceptance/20260810T072224Z-462569/

abort:   1 passed, 4 skipped in 15.98s
proof:   results/gazebo-acceptance/20260810T072625Z-465278/
```

### 当前测试状态、失败尝试与下一步

无 ROS 聚焦组在首次 live 前为 `45 passed, 5 skipped`；confirmation 修复后的
相关组为 `17 passed`。静态检查已通过 selected Python `py_compile`、runner
`bash -n` 与 launch XML parse。

第一次本轮完整 pytest：

```text
1953 passed, 7 skipped, 1 failed in 157.33s
```

唯一失败仍是既有 emergency-stop 并发测试：全量负载下把期望
`cancelled` 瞬时观察成 `lost`。隔离重跑立即：

```text
tests/test_gateway.py::test_gateway_admin_emergency_stop_cancels_active_task_and_records_audit_events
1 passed in 1.28s
```

没有为隐藏竞态修改 emergency-stop 产品代码。最终工作树还需再次运行完整 pytest；
若干净通过，记录最终结果。还需完成 `git diff --check`、最终 selected compile、
shell/XML check、CodeGraph pending 状态检查和 plan completion。

工程结论：六条确定性 Gazebo 纵向链均已有最新 live proof，same-task authorization、
真实 move_base terminal、diagnostics evidence、有界恢复/升级、Mission final report
和 Adapter trap 已闭合。

研究结论：这使“受安全约束的分层诊断与恢复”拥有可信 system substrate，但当前
policy 和 fault injection 仍是确定性 fixture，不能作为方法 novelty 或 LLM quality
证据。下一优先级应转入 embodied evaluation 重构：分离 deterministic integration、
LLM planning、ROS/Gazebo system 三套评测，使用 point/area/entity target、
scheduler-backed Mission Run、全部 canonical terminal outcomes，并记录 model、seed、
Tool inventory、Plugin version、map、navigation parameters 和完整 proof bundle。

发布层结论：若要支撑顶会主张，仍需对比无诊断、仅摘要、摘要+按需证据、中心诊断、
机器人本地诊断，测量成功率、恢复率、延迟、不安全提议、碰撞、带宽、token、陈旧
状态错误和事件可重建性；还需真实 costmap/堵塞故障和多次重复统计。

### 2026-08-10 15:48 +08 — 最终验证补记

最终工作树第二次完整 pytest 干净通过：

```text
1954 passed, 7 skipped in 156.97s
```

最终聚焦/静态验证：

```text
git diff --check                                      pass
selected Python py_compile                            pass
bash -n run_gazebo_acceptance.sh                      pass
ElementTree parse fireclaw_acceptance_world.launch    pass
focused acceptance/Robot/diagnostics/Plugin tests     51 passed, 5 skipped
CodeGraph status                                      index up to date
```

第一轮全量的 emergency-stop `lost` 瞬时失败在隔离测试和第二轮全量中均未复现；
仍按安全相关 flaky signal 记录，不修改产品语义掩盖它。本轮目标已完成，下一工作项为
重构 embodied evaluation 三层评测。没有 commit 或 push。

### 2026-08-10 — 用户确认的论文数据记录要求

用户明确要求：后续重做 embodied evaluation 和开展实验时，必须持续保存足以直接
用于论文分析的数据，避免以后只能依赖聊天记录、终端摘要或人工回忆。

后续每个 evaluation run 必须：

- 使用唯一、不可覆盖的 run ID 和明确时间戳；成功、失败、超时、取消、升级和 flaky
  run 都保留，禁止只保存成功样本；
- 保存 scenario/version、target type、seed、重复次数、训练/开发/测试划分；
- 保存 provider、model、temperature、模型 seed（若支持）、prompt/template hash、
  context policy、token usage、模型延迟和成本；
- 保存完整 Tool inventory、Tool schema、Plugin ID/version/digest、Agent/Gateway 版本
  和 Git commit；
- 保存地图/world hash、初始姿态、ROS/Gazebo 版本、传感器配置、导航参数、故障注入
  和 recovery budget；
- 保存 Mission、Robot task、Tool、action、diagnostics、authorization、operator
  intervention、terminal outcome、final report 和 evidence references 的原始事件；
- 同时输出 machine-readable raw records、per-run metrics、跨 run aggregate、均值、
  方差/标准差、置信区间、样本数和排除规则；
- 明确定义每个指标的计算口径与分母，记录失败分类和 missing-data 原因，避免论文阶段
  改口径后无法复算；
- 保存碰撞/near-collision、不安全提议、恢复成功率、诊断准确率、恢复延迟、带宽、
  token、陈旧状态错误和事件可重建性等论文候选指标；
- 对 no-diagnostics、summary-only、summary+on-demand evidence、central diagnosis、
  robot-local diagnosis 和 bounded recovery 等 baseline/ablation 使用同一 scenario
  split、seed 与评分脚本；
- 生成 paper-ready summary 表，同时保留能从原始 proof bundle 完整重算该表的脚本与
  provenance manifest。

执行习惯：每完成一个实验切片，就在当日 `memory/YYYY-MM-DD/` 记录命令、配置、输出
目录、关键指标、失败尝试、异常值、当前解释和下一步；不能等到论文写作阶段再补记。
