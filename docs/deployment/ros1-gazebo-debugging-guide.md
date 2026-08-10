# 当前架构的 ROS1/Gazebo 导航验收

更新时间：2026-08-10

本文定义 FireClaw 当前 Plugin 架构的 Gazebo acceptance lane。固定 success
baseline、same-task Mission 成功链、运行中 cancel、物理 action deadline timeout
、原生 move_base abort/unreachable、diagnostics-first stall-recover 与
stall-escalate 均已通过 live proof。当前确定性工程验收纵向矩阵已经闭合；重复
运行/nightly 稳定性统计、LLM planning evaluation 与研究对比实验仍是独立工作，
不能由这些确定性 lane 替代。

## 验收边界

```text
single-floor absolute map pose
  -> Mission Run / StructuredRobotTask
  -> Robot Gateway / Robot Agent
  -> awaiting_confirmation / exact operator authorization
  -> authenticated Robot Gateway /confirm same-task resume
  -> owner=fireclaw.navigation.move-base
  -> physical Tool: navigate_to_point
  -> Plugin-owned Ros1MoveBaseBackend
  -> ROS1 /move_base
  -> feedback / result / cancel
  -> Robot terminal outcome
  -> Mission final report + audit artifacts
```

Core `RobotAdapter` 只提供状态、环境观测和急停边界。导航 action、goal
construction、状态映射和取消属于 Navigation Plugin。验收必须在加载贡献后断言：

- `navigate_to_point` 的 owner 是 `fireclaw.navigation.move-base`；
- backend 是 `Ros1MoveBaseBackend`，action name 是固定的 `/move_base`；
- `RobotAdapter` 没有导航领域方法；
- 即使测试临时给 Adapter 安装一个会抛错的同名 trap，也从未调用该 trap。

## 当前已有证据与剩余缺口

无 ROS 的单元/集成测试已经覆盖 Plugin 加载、owner、schema、显式 handler、
feedback 事件、cancel 合同、参数 policy、强制 monotonic deadline、取消确认、
actionlib abort 证据和禁止 Adapter fallback。live success、cancel、timeout 与
abort harness 现已证明真实 `/move_base`，而不再只依赖注入 backend。

2026-08-10 的连续通过记录：

- `results/gazebo-acceptance/20260809T170557Z-250175/`：`1 passed`；
- `results/gazebo-acceptance/20260809T170837Z-252659/`：`1 passed`；
- 第二次 run 实际移动 `3.9517 m`，目标位置误差 `0.0218 m`，目标 yaw 误差
  `0.0249 rad`，并捕获该 goal 的 actionlib `SUCCEEDED(3)`；
- 两次均断言 owner、backend、`/move_base` action name、Adapter trap=0、feedback、
  停车速度、一次且仅一次的 FireClaw action success 终态。
- `results/gazebo-acceptance/20260810T040627Z-327534/`：same-task Mission
  success lane `1 passed`；确认前后 Robot task ID 均为
  `task-7cfcedf87b494b7880a805b7332e1713`，Mission trace=`succeeded`、final
  report=`completed`；实际移动 `3.9745 m`，目标位置误差 `0.0046 m`，目标 yaw
  误差 `0.0293 rad`。
- `results/gazebo-acceptance/20260810T045030Z-353594/`：live cancel lane
  `1 passed, 1 skipped`；同一 Robot task ID
  `task-87dcd0016d76456181b9aa0729eff9f7` 从等待授权、恢复执行到
  `cancelled`，actionlib 返回 `PREEMPTED(2)`，`cancellation_acknowledged=true`、
  `runtime_stopped=true`，Robot task 终态延迟 `0.5747 s`，Mission 终态与停车
  延迟均约 `0.9831 s`，取消后位移 `0.1121 m`，Adapter trap=0。
- `results/gazebo-acceptance/20260810T045225Z-355322/`：加入 cancel lane 后重跑
  success baseline，`1 passed, 1 skipped`，证明场景选择与 Mission 取消收敛没有
  破坏成功路径。
- `results/gazebo-acceptance/20260810T054233Z-385083/`：live timeout lane
  `1 passed, 2 skipped`；Robot 在收到 11 条 feedback、实际移动 `0.1509 m` 后，
  由可信 Plugin 配置的 `3.0 s` monotonic deadline 触发
  `cancellation_reason=deadline_exceeded`。同一 action/task 经 actionlib
  `PREEMPTED(2)` 和显式停止确认后传播为 `timed_out`，action elapsed
  `3.1444 s`，Robot task 终态延迟 `0.5494 s`，Mission/停车延迟约
  `0.8614 s`，停止后位移 `0.0655 m`，Adapter trap=0。
- `results/gazebo-acceptance/20260810T054452Z-387134/` 与
  `results/gazebo-acceptance/20260810T054558Z-388633/`：timeout 改动后分别重跑
  cancel 与 success，均为 `1 passed, 2 skipped`，证明新 deadline lane 未破坏
  既有终态路径。
- `results/gazebo-acceptance/20260810T061445Z-409910/`：live abort lane
  `1 passed, 3 skipped`。实际 `/map` 边界为 `[-10.0, 9.2000003] m`，固定目标
  `(9.5, 9.5)` 位于地图外；实际 ROS 参数为 `planner_patience=2.0 s`、
  `recovery_behavior_enabled=false`。同一 goal 收到 23 条 feedback 后由 move_base
  返回 `ABORTED(4)` 与完整 planner status text，Plugin 输出
  `error_code=move_base_aborted`、`runtime_stopped=true`、
  `resource_release_safe=true`。Robot task、Mission Run/event/final report 均为
  `failed`，没有 cancel、timeout、retry 或 reassign，Adapter trap=0。
- `results/gazebo-acceptance/20260810T061546Z-411283/`、
  `20260810T061720Z-412795/`、`20260810T061916Z-414470/`：abort 改动后分别回归
  timeout、cancel、success，均为 `1 passed, 3 skipped`。
- `results/gazebo-acceptance/20260810T071645Z-455332/`：diagnostics-first
  stall-recover `1 passed, 4 skipped`。同一 Robot task
  `task-a282df4d72a5463abe4f83aefea79f94` 的首次真实 goal 在 `8.0693 s`
  deadline 后由 actionlib `PREEMPTED(2)` 确认停止；80 条 feedback 的最大位移仅
  `8.65e-06 m`。随后只调用一次 `navigation_diagnostics`，保留 evidence ID
  `rosdiag-dd0cc09caf47544877027d25` 与 `navigation_action_failed` finding；只执行
  一次 `move_base_set_parameters`，将 live DWA `max_vel_x` 从 `0.0` 恢复为
  `0.22`，再对完全相同的目标重试一次并在 `4.4854 s` 后 `SUCCEEDED(3)`。
  Robot task、Mission Run 和 final report 均完成，Adapter trap=0。
- `results/gazebo-acceptance/20260810T071900Z-457933/`：diagnostics-first
  stall-escalate `1 passed, 4 skipped`。首次 goal 同样在 `8.0648 s` 后
  `PREEMPTED(2)`，80 条 feedback 的最大位移为 `8.63e-06 m`；诊断 evidence ID
  为 `rosdiag-7612532dd2f35fe04bf7c470`，含 `navigation_action_failed`。该 lane
  没有参数 mutation、没有 clear costmap、没有第二个 goal，DWA 前后均为零；
  `persistent_navigation_stall` 和 evidence reference 被传播到 Robot task、
  Mission Run 与 final report 的 `escalated` 终态，Adapter trap=0。
- `results/gazebo-acceptance/20260810T072109Z-460225/`、
  `20260810T072157Z-461618/`、`20260810T072224Z-462569/`、
  `20260810T072625Z-465278/`：两条 stall lane 加入后依次回归 success、cancel、
  timeout、abort，均为 `1 passed, 4 skipped`。

当前主链已改为 same-task suspend/resume：首次非 dry-run 导航进入非终态
`awaiting_confirmation`，不写 `ended_at`；`/confirm` 为精确动作签发一次性授权，
并在原 Robot task ID 上恢复执行，不再创建 replacement task。Mission Scheduler
持续跟踪同一 ID，因此导航 `completed` 后能够生成成功 final report。旧的两次 live
proof 证明 Robot 执行链；第三次 proof 已同时证明 Mission、Robot task、授权、
Plugin backend 与 ROS action 的完整成功链。

运行中 cancel 采用同样的主链，不由 acceptance harness 直接调用 actionlib。
Mission control plane 发出取消后，Scheduler 最多等待 `5 s` 观察 Robot task 的
真实终态；只有 Plugin/backend 确认 actionlib 已停止才传播 `cancelled`，等待窗口
耗尽则传播 `lost`。`cancel_requested` 不再被 Subagent Registry 提前写成终态。

live timeout 不调用 Mission cancel API，也不由独立 action client 取消 goal。
Robot action 的 monotonic deadline 到期后，Runtime 请求 Plugin/backend 停止；
只有收到 actionlib 终态和 `runtime_stopped=true` 才发出唯一
`action.timed_out`、`task.timed_out`，否则收敛为 `lost`。Mission Scheduler 对
`timed_out` 的默认决策为 `abort`，即停止剩余计划但保留原始原因，不盲目重试
物理目标；Mission Run、`mission.timed_out` 事件和 final report 均保留 canonical
`timed_out`。Mission Registry 的聚合 trace 目前仍使用兼容失败类 `failed`，但其
child task 保留 `timed_out`；该聚合状态细分属于后续 canonical outcome 迁移范围。

live abort 不由 Mission/operator 取消，也不依赖 action deadline。可信 runner 只对
固定 `abort.yaml` 将 planner patience 设为 `2.0 s` 并关闭原生 recovery，测试再从
ROS 参数服务器读取实际值；目标是否在地图外由 live `/map` OccupancyGrid 计算，而
不是仅相信 YAML。action 层保留原生 `aborted`、actionlib `ABORTED(4)`、status text
和稳定 error code；Robot/Mission 层规范化为 canonical `failed`。默认
`on_failed=reassign` 在单机器人且无替代执行者时现在显式 `abort`，不再以
`skipped` 结束后误报 Mission success。

两条 live stall lane 使用同一固定 world，并且只有可信 runner 在精确选择
`stall-recover.yaml` 或 `stall-escalate.yaml` 时才向 launch 传入固定
`inject_stall=true`。launch 将真实 `/move_base/DWAPlannerROS/max_vel_x` 与
`min_vel_x` 设为零；该参数不来自 Agent 输入。首次 Plugin-owned goal 必须先因
`8 s` deadline 安全停止，然后 Robot-local bounded deliberation 才能调用只读
`navigation_diagnostics`。recover lane 只有一次受 policy 约束的参数恢复预算和
一次完全相同目标的重试；escalate lane 没有 mutation/retry 权限，只能携带诊断
evidence reference 升级。该设计验证“诊断先于恢复/升级”的工程链路，不声称已经
实现可泛化的 LLM 故障归因策略。

## 固定测试环境

首条 lane 使用单楼层二维静态地图、TurtleBot3 Burger、ROS1 Noetic、
Gazebo Classic 11 和真实 `move_base_msgs/MoveBaseAction`。地图、world、初始
pose、目标 pose、导航参数和随机种子必须进入版本控制或 proof manifest。

基础检查：

```bash
source /opt/ros/noetic/setup.bash
source extensions/navigation-move-base/ros_ws/devel/setup.bash
source robots/turtlebot3_burger/ros_ws/devel/setup.bash
export TURTLEBOT3_MODEL=burger

rospack find move_base
rospack find move_base_msgs
rostopic type /move_base/goal
rosrun tf tf_echo map base_link
```

在 FireClaw 介入前，固定目标必须先通过 RViz 或独立 action client 成功。接受
`examples/ros1_configs/gazebo_turtlebot3_move_base.yaml` 作为 core ROS 配置，
但不得向该文件重新加入领域 Tool endpoint；`/move_base` 由 Plugin 固定管理。

当前固定环境可由可信 runner 一次启动、验收和清理：

```bash
FIRECLAW_PYTHON=/path/to/python \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

运行 cancel 场景：

```bash
FIRECLAW_PYTHON=/path/to/python \
FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO="$PWD/extensions/navigation-move-base/config/acceptance/cancel.yaml" \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

运行 timeout 场景：

```bash
FIRECLAW_PYTHON=/path/to/python \
FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO="$PWD/extensions/navigation-move-base/config/acceptance/timeout.yaml" \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

运行 abort/unreachable 场景：

```bash
FIRECLAW_PYTHON=/path/to/python \
FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO="$PWD/extensions/navigation-move-base/config/acceptance/abort.yaml" \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

运行 diagnostics-first stall-recover 场景：

```bash
FIRECLAW_PYTHON=/path/to/python \
FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO="$PWD/extensions/navigation-move-base/config/acceptance/stall-recover.yaml" \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

运行 diagnostics-first stall-escalate 场景：

```bash
FIRECLAW_PYTHON=/path/to/python \
FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO="$PWD/extensions/navigation-move-base/config/acceptance/stall-escalate.yaml" \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

## 测试分层

### 1. 快速 Plugin 合同 lane

保留现有无 ROS 测试，用注入 backend 验证 owner、schema、policy、事件和 trap。
该层每次提交都运行，但不计为 Gazebo E2E。

### 2. ROS action 协议 lane

使用受控 action server 注入 rare terminal state，确定性覆盖
`PREEMPTED/ABORTED/REJECTED/RECALLED/LOST` 的映射。该层证明 actionlib 协议
归一化，不代替物理仿真。

### 3. Gazebo acceptance lane

pytest 只连接固定、已启动的 ROS/Gazebo graph；首版不让 LLM 生成
`roslaunch` 命令。建议目录：

```text
extensions/navigation-move-base/
  launch/fireclaw_acceptance_world.launch
  config/acceptance/success.yaml
  config/acceptance/cancel.yaml
  config/acceptance/timeout.yaml
  config/acceptance/abort.yaml
  config/acceptance/stall-recover.yaml
  config/acceptance/stall-escalate.yaml
  tests/acceptance/conftest.py
  tests/acceptance/test_gazebo_navigation.py
  tests/acceptance/test_gazebo_timeout.py
  tests/acceptance/test_gazebo_abort.py
  tests/acceptance/test_gazebo_stall.py
  tests/acceptance/artifacts.py
  tests/acceptance/fireclaw_harness.py
  tests/acceptance/ros_harness.py
  tests/acceptance/run_gazebo_acceptance.sh
```

测试由显式环境变量开启，默认跳过：

```bash
FIRECLAW_RUN_GAZEBO_ACCEPTANCE=1 \
  python -m pytest -q -m gazebo_acceptance \
  extensions/navigation-move-base/tests/acceptance
```

## 必须覆盖的场景

| 场景 | 物理布置/操作 | 关键断言 |
| --- | --- | --- |
| success | 可达绝对 `map` pose | 收到 feedback；`/move_base` 与 confirmed Robot task 成功；Mission 回填完成后才可要求 Mission success |
| cancel | 机器人运动中取消 Mission | 发布 action cancel；机器人停止；终态为 `cancelled`，不得改写为成功 |
| timeout | 可达 goal 执行中超过固定 action deadline | Runtime 主动 cancel goal；经停止确认后终态为 `timed_out`；不盲目重试；线程和资源租约释放 |
| abort/unreachable | 固定目标落在 live `/map` 数值边界外 | move_base `ABORTED(4)`、status text 与安全停止被保留；同一 Robot task 和 Mission 规范化为 `failed`；不得 cancel/timeout、重试、重分配或伪装成成功 |
| stall-recover | 可信 launch 将 live DWA 前进速度界限设为零 | 首次 goal 超时并确认停止；先调用 `navigation_diagnostics`；只恢复一次参数并对完全相同目标重试一次；最终 `completed` |
| stall-escalate | 与 recover 相同的确定性 stall，但不给恢复预算 | 诊断后零 mutation、零 retry；终态 `escalated`；报告含 `persistent_navigation_stall` 与 evidence ID |

每个场景还必须断言：

- 目标始终是 `frame_id="map"` 的有限 `x/y/yaw`，没有楼层切换语义；
- startup/inventory、physical contribution 和执行事件中的 owner 一致；
- `action.requested/started/feedback/terminal` 可按 `action_id/task_id` 关联；
- cancel、timeout、abort、blocked、escalated 不会在 Mission 层被吞成 success；
- final report 与事件账本引用相同 task、action 和 evidence ID；
- Adapter trap 调用计数为零。

## Stall 诊断与恢复约束

恢复链必须是可审计状态机，而不是无限 ReAct 重试：

```text
Plugin action deadline fires
  -> cancel active goal and confirm runtime stopped
  -> navigation_diagnostics
  -> require navigation_action_failed evidence
  -> recover lane: one policy-bounded parameter mutation
  -> recover lane: one exact-goal retry
  -> completed or evidence-backed escalated
```

当前第一版使用同一固定 world 和两种 data-only scenario：

- `stall-recover.yaml`：可信 launch 注入 `max_vel_x=min_vel_x=0`，诊断后只允许
  `move_base_set_parameters(max_vel_x=0.22,min_vel_x=0)` 和一次同目标重试；
- `stall-escalate.yaml`：注入相同 stall，但 recovery budget 为零，必须直接升级。

诊断必须先于恢复动作或升级；缺少 diagnostics evidence、恢复超过一次、目标改变、
escalate lane 出现任何 mutation/retry 时，测试都必须失败。未来可在独立 scenario
中增加真实 costmap 障碍与持续堵塞，不应把当前速度参数 fault 等同于所有障碍类型。

## Proof artifacts

每次 run 写入 `results/gazebo-acceptance/<run-id>/`：

```text
run-manifest.json
plugin-inventory.json
ros-graph.json
goal-and-feedback.jsonl
authorization-events.jsonl
authorization-task-trace.json
robot-events.jsonl
mission-events.jsonl
robot-task-trace.json
mission-trace.json
final-report.json
pose-evidence.json
cancellation-evidence.json  # cancel lane
timeout-evidence.json       # timeout lane
abort-evidence.json         # abort lane
map-evidence.json           # live map dimensions/bounds and goal relation
navigation-parameters.json  # live planner/recovery values
readiness.json
navigation-diagnostics.json
stall-evidence.json         # stall lanes
navigation-parameters-before.json
navigation-parameters-after.json
junit.xml
ros-logs/
```

`run-manifest.json` 至少记录 commit、Plugin version/owner、world/map hash、目标
pose、ROS distribution、导航参数 hash、scenario、seed、开始/结束时间和结果。
测试地图不得包含真实现场敏感信息。

## 实施顺序

1. 已完成：在 `RobotActionRuntime` 与 `Ros1MoveBaseBackend` 闭合强制 timeout、
   goal cancel 和 `timed_out` 结果合同，并加无 ROS 单元测试。
2. 已完成：建立 acceptance marker、固定 launch/config、ROS readiness fixture、
   Plugin owner/trap 断言、正式授权握手、proof bundle 和 live success 场景。
3. 已完成：确认等待采用 same-task suspend/resume，Mission 持续跟踪原 child task
   并在其成功后生成 final report；已重跑 live success proof。
4. 已完成：运行中 cancel 从 Mission control plane 触发，验证 feedback 后取消、
   actionlib `PREEMPTED`、显式停止确认、唯一 action/task 终态、Mission/final
   report 传播、停车延迟、取消后位移与完整 proof bundle；未确认停止收敛为
   `lost` 的无 ROS 合同也已覆盖。
5. 已完成：live timeout 在实际移动和 feedback 后由可信 Plugin action deadline
   触发，不调用 Mission cancel；验证 actionlib `PREEMPTED`、显式停止确认、唯一
   `action/task.timed_out`、默认无盲重试、Mission Run/event/final report 传播、
   停车延迟、停止后位移与 `timeout-evidence.json`。未确认停止仍收敛为 `lost`。
6. 已完成：live abort 使用 live `/map` 证明目标位于地图外，并验证可信 planner
   参数、真实 actionlib `ABORTED(4)`、planner status text、唯一
   `action.failed/task.failed`、Mission `failed`、安全停止、无 cancel/timeout、
   无 retry/reassign 和 `abort-evidence.json`；无替代机器人时不再静默成功。
7. 已完成：两条 diagnostics-first stall lane 使用可信零速 DWA fault injection；
   首次真实 action deadline 后先运行 `navigation_diagnostics`。recover lane 只允许
   一次参数恢复与一次同目标重试并最终成功；escalate lane 零 mutation/零 retry，
   携带 `persistent_navigation_stall` 和 evidence ID 升级。两条 lane 均验证
   same-task confirmation、事件顺序、参数前后快照、唯一 action 终态、Mission/final
   report 传播和 Adapter trap=0。
8. 下一步固化 artifact schema、做多次本地重复与 nightly 稳定性统计；再增加真实
   costmap 障碍/持续堵塞 scenario。该 lane 保持 opt-in，不放入普通快速 CI。

## Definition of done

当前六种终态/恢复路径的纵向工程闭环已各有一次最新 live proof，并且无 Adapter
fallback；stall 恢复/升级均保留诊断 evidence 和可重放 proof bundle。把它提升为
稳定的 release/nightly acceptance 仍要求固定环境多次重复全部通过、统计 flaky
rate，并解决任何与安全终态相关的并发竞态。该完成标准不等同于 LLM planning 或
研究方法有效性。
