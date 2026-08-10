# Plugin boundary 残留清理与 Gazebo acceptance 续接记录

## 时间

- 最终更新：2026-08-09T23:13:51+08:00
- branch: `master`
- base HEAD: `a0ae671`
- worktree: 有本任务的未提交修改；未执行 commit/push

## 任务目标

清理 FireClaw 从内置物理 Tool / Adapter domain-action 架构迁移到
Plugin-owned Tool 架构后的残留，确认新增普通 Tool Plugin 不再要求修改 core，
并为当前架构的 ROS1/Gazebo navigation acceptance lane 固化真实完成条件。

用户明确要求先完成残留清理，再判断如何继续：

- 单楼层绝对 `map` 坐标；
- owner 必须是 `fireclaw.navigation.move-base`；
- Plugin-owned `navigate_to_point` 驱动真实 `/move_base`；
- 验证 feedback、success、cancel、timeout、终态传播、final report 和审计；
- stall/blocked 后调用 `navigation_diagnostics`，再恢复或升级；
- 明确禁止静默回退到 core `RobotAdapter` 的同名领域方法。

## 2026-08-09T23:13:51+08:00 暂停与下次续接决定

用户决定本次先暂停休息。本次决定之后不再修改业务代码、不运行测试、
不执行 commit/push；下次直接从本节继续，不重新扫描或重新设计已经完成的
Plugin boundary cleanup。

### 下次第一项工程任务

先闭合 **generic Physical Action cancel / deadline / terminal contract**，
暂不先启动 Gazebo，也不先实现 navigation stall recovery。原因是当前
`RobotActionRuntime.run()` 仅记录并转发 `timeout_seconds`，没有实际 deadline；
直接建立完整 Gazebo lane 时，卡住的 ROS Runtime 可能令验收本身无限等待。

首个实施切片应满足：

1. core 使用 monotonic deadline，把 operator cancellation 与 deadline expiry
   合成为通用执行控制信号，并保留取消原因；
2. 物理 Runtime 必须 cooperative cancel；禁止用“Python worker/thread 已停止等待”
   冒充机器人已经停止；
3. Plugin backend 收到通用信号后负责取消自己拥有的 Runtime，并进行有界的
   cancellation acknowledgement；
4. 明确区分 `cancelled`、`timed_out` 和取消未确认：
   - operator cancel 且 Runtime 确认停止：`cancelled`；
   - deadline expiry 且 Runtime 确认停止：`timed_out`；
   - Runtime 未确认停止：不得伪装为安全结束，应进入 `lost`/`escalated`
     等 fail-safe 路径，也不得静默释放运动控制权；
5. action 只能产生一个 terminal event；终态必须继续传播到 task、Mission、
   final report、audit evidence 和资源租约处理；
6. 先以 deterministic fake backend 覆盖 pre-start cancel、in-flight cancel、
   timeout、cancel/complete race、终态唯一性和取消未确认，再接 ROS/Gazebo。

### 不可破坏的架构边界

```text
FireClaw core
  -> generic deadline / cancellation / terminal-state contract
  -> Plugin-owned physical handler
  -> Navigation Plugin translates cancellation to actionlib cancel_goal()
  -> /move_base
```

- core 不得出现 `/move_base`、`navigate_to_point` 的特判或
  `fireclaw.navigation.move-base` 的依赖；
- `RobotAdapter` 不得重新增加 navigation domain action；
- `RegisteredActionBackend` 继续只分发显式 Plugin handler；
- Navigation Plugin 自己拥有 ROS action cancellation、反馈和 actionlib 终态翻译；
- live acceptance 必须再次断言 owner 为
  `fireclaw.navigation.move-base`，并用 Adapter trap 证明零 fallback 调用。

### 本次补充核对的 OpenClaw analogue

通过 CodeGraph 核对了 OpenClaw 的 abort/timeout/cleanup 形状，重点包括：

- `openclaw/src/agents/embedded-agent-runner/run/attempt-abort.ts`；
- `openclaw/src/agents/embedded-agent-runner/run/attempt-timeout-prepare.ts`；
- `openclaw/src/agents/embedded-agent-runner/run/attempt-execution-settle.ts`；
- `openclaw/src/agents/embedded-agent-runner/run/terminal-outcome.ts`；
- `openclaw/src/gateway/chat-abort.ts::registerChatAbortController`。

复用的结构原则是 parent/external abort 与 timeout reason 合并、终态拥有 cleanup、
cleanup 对当前 controller/entry 做身份保护。FireClaw 需要额外适配机器人约束：
core 发出通用取消信号还不代表物理 Runtime 已停止，必须由 Plugin 确认取消结果。

### 完成通用合同后的 Gazebo 顺序

1. 建立最小真实 success lane：固定单楼层 `map` 点、真实 `/move_base`、owner、
   Adapter trap、feedback、success、final report 和 audit artifact；
2. 增加 live cancel、timeout、abort/unreachable；
3. 增加 recoverable stall：diagnostics -> authorized recovery -> one retry -> success；
4. 增加 persistent stall：diagnostics -> blocked/escalated；
5. 固化 `results/gazebo-acceptance/<run-id>/` proof bundle，再考虑 nightly CI。

### 下次建议先看的文件与测试

- `src/fireclaw_core/execution/action_runtime.py`；
- `src/fireclaw_core/execution/skills.py`；
- `extensions/navigation-move-base/plugin/move_base.py`；
- `extensions/navigation-move-base/plugin/entrypoint.py`；
- `tests/test_action_runtime.py`；
- `tests/test_move_base_navigation_plugin.py`；
- task/Mission terminal outcome 与 resource lease 的传播测试。

先写 contract tests，再实现最小通用改动。定向验证至少运行：

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_action_runtime.py \
  tests/test_move_base_navigation_plugin.py \
  tests/test_gateway_structured_task.py
```

当前 cleanup worktree 很大但已经通过 `1918 passed, 7 skipped`。开始新阶段前
建议保持它作为独立 checkpoint 边界；仍然只有用户明确要求后才能 commit。

## 当前进展

架构残留清理已经完成并通过完整无 ROS 回归。当前 active source、examples 和
tests 中没有旧五个消防 Tool 名、workspace executable loader、旧 core
navigation module、legacy physical extension 或 Adapter navigation fallback。

Gazebo acceptance 的实施规范已经写入
`docs/deployment/ros1-gazebo-debugging-guide.md`，但尚未在本次会话启动
ROS/Gazebo，也没有产生 live `/move_base` proof bundle。因此当前状态是：
架构与测试前置已收口，Gazebo lane 仍待实现，不能宣称验收完成。

## OpenClaw analogue reviewed

本轮设计沿用此前通过 CodeGraph 核对的 OpenClaw 结构：

- `openclaw/src/plugins/plugin-api.types.ts`：注入 Plugin API 与多类 contribution；
- `openclaw/src/plugins/registry.ts` / `registry-api.ts`：统一 owner 和注册表；
- `openclaw/src/plugins/plugin-registration-transaction.ts`：原子 stage/commit/rollback；
- `openclaw/src/plugins/tools.ts`：Tool 冲突、投影与 owner；
- Agent Harness 的 attempt / selection / lifecycle 边界。

FireClaw 复用 Plugin Host、registration transaction、Tool projection 与 Harness
形状；针对具身机器人增加 physical Tool、任务目标保护、SafetyGate、精确授权、
资源租约、ROS backend、反馈/取消/终态和事故审计。

## 已完成

### 1. Core 与 Plugin 解耦

- `RobotAdapter` 只保留 robot/environment state 和 emergency stop。
- `RegisteredActionBackend` 只调用显式注册的 Plugin handler，不再
  `getattr(robot, action_name)`。
- `PhysicalToolSpec.handler` / internal `PhysicalSkillPlugin.action_handler`
  为必需项，缺失时 fail closed。
- Gateway 只传 generic Plugin service bag，不识别导航 backend key。
- Navigation Plugin 自己选择 `Ros1MoveBaseBackend` 或
  `InMemoryMoveBaseBackend`，服务 ID 为
  `fireclaw.navigation.move-base.backend`。
- `navigate_to_point` physical contribution owner 为
  `fireclaw.navigation.move-base`。
- Mission/Robot capability catalog、intent pattern、completion/observation mapping
  改为可注入/由 Plugin projection 派生，不再依赖旧五个 Tool 名。
- `RobotAgentRuntime` 与 deliberation runtime 使用同一个 Plugin-projected
  catalog，避免 planner/policy inventory 漂移。

### 2. 删除的旧实现与误导入口

删除了：

- `src/fireclaw_core/navigation/` core navigation shim；
- `extensions/robot-legacy-physical/`；
- `src/fireclaw_core/execution/builtin_physical_skills.py`；
- legacy runtime/workspace executable Tool loader 与 manifest：
  `execution/runtime.py`、`infra/workspace_skills.py`、
  `infra/skill_manifest.py`；
- legacy executable example 和对应 tests；
- core domain ROS diagnostics wrapper，保留 Plugin-owned diagnostics；
- 旧 ROS domain endpoint examples；
- preparation-only `src/fireclaw_core/ros/gazebo_smoke.py` 及其 test；
- 仍使用“去二楼”的 Gazebo eval fixture；
- 已失效的 executable Tool sandbox 文档。

旧 `gazebo_smoke` 只生成 `robots.json`，没有启动 Gazebo、发送任务或判定
结果。删除它避免把 preparation 误报为 acceptance。

### 3. 当前文档收口

重写或更新了：

- root `README.md`；
- Plugin/Skill/Tool 权威术语；
- extension loader、Plugin SDK、physical Tool runtime；
- navigation Tool flow；
- ROS1 deployment、Gazebo acceptance、hardware smoke；
- ROS2 future Plugin plan；
- security audit 和 Docker sandbox 文档；
- FireClaw/OpenClaw alignment。

2026-06 的 roadmap/refactor plan 保留为历史记录，并加了“已被
2026-08-09 cleanup 取代”的醒目标记。历史文档和 memory 中仍可出现旧名字，
但不能作为当前 API 或验收依据。

### 4. Deterministic embodied eval 去误报

`src/fireclaw_core/devtools/embodied_eval.py` 现在明确是 deterministic
Mission/Robot Gateway contract harness，不是 Gazebo、ROS readiness 或论文证据：

- 使用单楼层绝对 point target；
- 同步走 scheduler-backed 路径；
- blocked/escalated/failed/timed_out/cancelled/lost 不再计为 dispatch success；
- 检查 structured target/capability contract；
- 强制 `min_memory_records`；
- 生成的历史 `doctor-report.json` 固定为 warn，并记录
  `readiness_not_probed`。

### 5. 验证证据

执行过：

```text
codegraph explore <current Plugin ownership and compatibility projections>
codegraph explore <security audit checks>
codegraph explore <navigation Plugin -> Ros1MoveBaseBackend -> /move_base>
codegraph explore <Mission Gateway background Run and terminal propagation>

rg <removed symbol/path scan> src extensions examples tests
git diff --check
python -m compileall -q src extensions
python -m pytest -q <targeted architecture cleanup group>
```

Targeted group covered `test_embodied_eval.py`,
`test_move_base_navigation_plugin.py`, `test_plugin_sdk.py`,
`test_extension_loader.py`, `test_ros1_config.py`, `test_robot.py`, and
`test_action_runtime.py`.

结果：

```text
58 passed in 6.90s
```

最终完整回归：

```text
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
1918 passed, 7 skipped in 152.83s
```

最终 active source/examples/tests 残留扫描无匹配；`git diff --check` 和
`compileall` 均通过。环境没有安装 `ruff`，未运行 ruff。

## 刻意保留的兼容边界

以下不是 Tool Plugin 耦合残留，本轮没有破坏性迁移：

- Python legacy identifiers：`Skill`、`SkillRegistry`、
  `PhysicalSkillPlugin`、`required_skills/allowed_skills`；
- 历史/未来多楼层数据字段：`MissionTarget.floor`、
  `PlanningResult.target_floor`、`RobotState.current_floor`、
  `EnvironmentState.reachable_floors`、`victims_by_floor`；
- ROS1 core transport 的 generic endpoint/template 类型，当前只用于 core
  emergency-stop 等可信基础设施，不提供领域 action catalog。

这些兼容项不向当前 Navigation Plugin 自动提供跨楼层能力，也不能形成
Adapter domain-action fallback。后续若迁移序列化字段，需要单独设计 checkpoint、
memory、task contract 和 audit event 版本升级。

## 当前问题

### 1. Physical timeout 尚未真正强制

`PhysicalToolSpec.timeout_seconds` 当前只有元数据语义；
`navigate_to_point` 尚未设置具体 deadline；`RobotActionRuntime.run()` 不启动
watchdog；`Ros1MoveBaseBackend.navigate_to_point()` 会循环等待 result，只有
外部 cancellation callback 才 cancel goal。

因此 Gazebo timeout 场景当前可能无限等待。这是 acceptance lane 的第一个
实际工程 blocker，必须先闭合：

```text
deadline -> cancellation signal -> actionlib cancel_goal
-> bounded cancel acknowledgement -> timed_out RobotActionResult
-> action/task/Mission terminal propagation -> resource lease release
```

### 2. 还没有真实 acceptance harness

需要在 Navigation Plugin 自己的测试范围创建：

```text
extensions/navigation-move-base/
  launch/fireclaw_acceptance_world.launch
  config/acceptance/
  tests/acceptance/conftest.py
  tests/acceptance/test_gazebo_navigation.py
  tests/acceptance/artifacts.py
```

首版建议由可信 operator/test runner 启动固定 launch，pytest attach 到 ROS graph；
不允许 LLM 构造任意 shell/roslaunch 参数。

### 3. Stall diagnostics/recovery 尚未纵向接通

已有 `navigation_diagnostics`、status、cancel、clear costmaps Tool，但还没有
固定 Gazebo world 与可审计恢复状态机把它们串成：

```text
progress watchdog
-> cancel active goal
-> status + navigation_diagnostics
-> classify cause
-> one authorized recovery
-> one retry
-> success / blocked / escalated
```

需要分别建立 recoverable costmap 与 persistent blockage 两个场景。

## 下一步

按以下顺序继续，不要先写大而全的 Gazebo orchestration：

1. 实现并单元测试 physical deadline/cancel/`timed_out` 合同。
2. 添加 `gazebo_acceptance` marker、显式环境开关、ROS readiness fixture、
   Plugin owner 与 Adapter trap 断言。
3. 先打通一个真实 `/move_base` success 场景并归档 feedback/final report/audit。
4. 增加运行中 cancel、unreachable/abort 和 timeout。
5. 增加 recoverable/persistent stall worlds、diagnostics-first 恢复/升级。
6. 固化 `results/gazebo-acceptance/<run-id>/` artifact schema，再考虑 nightly CI。

## 需要运行的命令

无 ROS 快速前置：

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_action_runtime.py \
  tests/test_move_base_navigation_plugin.py \
  tests/test_gateway_structured_task.py
```

未来 lane 实现后的显式入口：

```bash
source /opt/ros/noetic/setup.bash
source extensions/navigation-move-base/ros_ws/devel/setup.bash
source robots/turtlebot3_burger/ros_ws/devel/setup.bash
export TURTLEBOT3_MODEL=burger

FIRECLAW_RUN_GAZEBO_ACCEPTANCE=1 \
  /home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  -m gazebo_acceptance \
  extensions/navigation-move-base/tests/acceptance
```

## 当前结论

对“新增一个普通 Tool Plugin 是否还需要修改 FireClaw core”的回答是：不需要。
Plugin 通过 manifest + public SDK 注册 Tool/physical Tool，core 只做统一 owner、
policy、安全、授权、生命周期、资源和审计。新增一种新的 Mission 任务语义时，
仍需提供 Skill、intent/capability chain、completion contract 和 safety policy，
但应通过 Plugin contribution 或可注入 registry/profile 完成，不能重新增加
Gateway/RobotAdapter 的 Tool 名称分支。

工程层面已经完成 boundary cleanup；研究层面仍没有 Gazebo failure/recovery
数据。下一项工作应是 timeout contract 与第一个真实 `/move_base` success
acceptance，而不是继续扩展 Plugin 数量。

## 2026-08-09T23:57:15+08:00 Physical Action deadline/cancel 合同完成

### 用户决定与任务目标

用户确认先实现通用物理 Tool 的可控执行监督：规定执行 deadline、规范取消
过程，并确保 Agent/core 不能把“停止等待 Python 调用”误判成“机器人已经停止”。
本轮实现范围是 generic Physical Action contract 及 move_base Plugin 的 ROS1
取消确认，不启动 Gazebo、不执行 commit/push。

### OpenClaw analogue 与 FireClaw 适配

实现前通过 CodeGraph/源码复核：

- `openclaw/src/agents/embedded-agent-runner/run/attempt-abort.ts`；
- `attempt-timeout-prepare.ts`；
- `attempt-execution-settle.ts`；
- `terminal-outcome.ts`；
- `openclaw/src/gateway/chat-abort.ts`。

复用了 external abort + timeout reason 合并、deadline owner、settled cleanup、
terminal projection 的结构。FireClaw 额外要求：物理 Runtime 必须 cooperative
cancel 并明确确认 safe stop；没有确认时不能释放运动控制资源。

### 实现内容

1. `src/fireclaw_core/execution/action_runtime.py`
   - 新增线程安全 `ActionCancellationSignal`，用 monotonic clock 合并 operator
     cancellation、deadline expiry 和 control-check failure，并保留首个原因；
   - `RobotActionRuntime` 在 daemon supervisor worker 外监控 deadline；取消后只
     有界等待 acknowledgement，不尝试杀死 Python 线程并伪装底层 Runtime 停止；
   - Plugin 必须明确返回 `cancellation_acknowledged=true` 与
     `runtime_stopped=true`；
   - operator cancel + ack -> `cancelled`；deadline + ack -> `timed_out`；未确认
     -> `lost` + `resource_release_safe=false`；
   - cancel/complete race 按 monotonic request/completion 时间仲裁；一次 action 只
     产生一个 terminal event，terminal 后的 late feedback/result 被抑制。

2. SDK / compatibility projection
   - `PhysicalToolSpec`、`PhysicalSkillPlugin`、`Skill` 新增
     `cancellation_ack_timeout_seconds`，默认 2 秒，公共 SDK 最大 30 秒；
   - 字段通过 `sdk_adapter.py`、metadata 和 `skill_from_physical_plugin()` 进入
     `RobotActionRuntime`；
   - `timeout_seconds` 现在是实际执行合同，不再只是元数据。

3. `src/fireclaw_core/execution/executor.py`
   - 精确保留 `cancelled/timed_out/lost/escalated/blocked`，不再全部折叠成
     `failed`；
   - safe-stop 未确认时不释放 resource lease；写入 `resource.retained`，并通过
     `SqliteResourceLeaseManager.close_admission()` 持久关闭后续资源准入；
   - timeout/cancellation lifecycle 继续进入 memory/audit event。

4. 终态传播
   - Agent message 可展示 timed_out/lost 等最后一步错误；
   - `build_robot_task_terminal_outcome()` 保留 operator cancellation 的 sticky
     语义，但 `lost` 优先于 cancel request，避免把未确认停止伪装成安全取消；
   - 现有 Robot Gateway、Mission Scheduler/Registry/report 的 canonical terminal
     链继续使用精确状态。

5. Navigation Plugin
   - `navigate_to_point` 声明 120 秒执行 deadline 与 2 秒取消确认窗口；
   - `Ros1MoveBaseBackend` 收到信号后调用 `cancel_goal()`，有界等待 actionlib
     terminal state；PREEMPTED/RECALLED 及其他已知停止终态才算确认，LOST 不算；
   - explicit `move_base_cancel_navigation` 的 `cancel_all_goals()` 也必须等待确认；
   - 未确认返回 `move_base_cancel_unacknowledged`、`lost`、
     `runtime_stopped=false`；
   - core 仍无 `/move_base` 或 Navigation Plugin 特判，Adapter fallback 没有恢复。

### 新增/更新测试合同

- pre-start operator cancel；
- in-flight cooperative cancel；
- monotonic deadline -> confirmed `timed_out`；
- cancel/complete race 与 terminal uniqueness；
- uncooperative backend -> bounded `lost`；
- backend 自报 cancelled 但没有 safe-stop ack -> `lost`；
- PlanExecutor 精确传播 `timed_out`；
- unconfirmed stop 保留 lease 并关闭 admission；
- cancellation request 不能覆盖 `lost`；
- ROS1 `cancel_goal()` ack/unack；
- explicit `cancel_all_goals()` acknowledgement；
- SDK 字段端到端投影。

### 验证命令与结果

```text
/home/lpp/miniconda3/envs/py310/bin/python -m compileall -q \
  src/fireclaw_core src/fireclaw_plugin_sdk \
  extensions/navigation-move-base/plugin

focused contract/tests: 86 passed in 1.21s
Gateway/Robot/Mission propagation group: 171 passed in 33.18s
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
1930 passed, 7 skipped in 166.31s
git diff --check: passed
```

Gateway 测试在普通沙箱内因禁止创建 loopback socket 得到 `PermissionError`；按
既有环境约束在批准的提权测试环境重跑后全部通过，不是代码失败。

### 当前结论

通用 Physical Action deadline/cancel/terminal contract 已闭合到无 ROS 回归层面。
Agent/core 现在能够有界监督工具任务，但不会把 worker 停止等待当成物理安全
证据。未确认停止会 fail safe 为 `lost` 并 fence 后续资源。

### 已知边界与下一步

- 尚未在真实 Gazebo `/move_base` 上验证 120 秒 deadline、2 秒 actionlib cancel
  acknowledgement、feedback 和终态传播；
- unacknowledged worker 是 daemon 且可能晚到结束，core 会忽略其 late result；
  资源准入会保持关闭，当前没有新增自动 reopen API，必须由后续显式恢复/操作员
  流程处理；
- actionlib “终态即停止确认”仍需用 TurtleBot3/Gazebo 观察 `/cmd_vel` 和 goal
  status 做 live 验证；
- cleanup + 本轮合同仍是同一个巨大未提交 worktree，base HEAD 为 `a0ae671`。

下一项工程任务恢复为 Gazebo acceptance 第一个真实 success lane：固定单楼层
`map` 点、owner=`fireclaw.navigation.move-base`、Adapter trap、feedback、success、
final report 与 audit artifact。随后增加 live cancel、timeout、abort/unreachable。
