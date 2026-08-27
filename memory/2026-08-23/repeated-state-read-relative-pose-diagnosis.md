# 相对返航任务触发 repeated_state_read 的诊断

更新时间：2026-08-23 23:00 +0800（本轮诊断；持久化事件时间见下）

## 任务目标

诊断 Mission CLI 对以下任务返回 HTTP 422 的原因：

`请依次巡检这几个点：先去坐标(0.63, 0.54)，再前往(1.0, 0.0)，接着前往(-1.0, -0.5)，最后返回现在的位置`

错误为 `Mission deliberation repeated an unchanged state read without making progress`，确定性反馈为 `robot_state` 已查询、应改为 `propose_task_graph` 或 `request_clarification`。

## 当前进展

- 已沿 Mission deliberation 结构和本次持久化 trace 还原两轮模型决策。
- 已确认不是导航执行失败、坐标格式失败或 Robot Gateway 离线。
- 未修改产品代码；本轮属于诊断。

## 检查的代码和记录

- CodeGraph：`MissionDeliberationRuntime.deliberate`、重复状态读取门禁、LLM planning tools/prompt。
- `src/fireclaw_core/mission/mission_deliberation.py`
- `src/fireclaw_core/planner/llm_planner.py`
- `src/fireclaw_core/agent/robot.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/ros/ros1_transport.py`
- `extensions/navigation-move-base/plugin/move_base.py`
- `data/mission/missions.jsonl.agent-loops.jsonl`
- `data/mission/memory.jsonl`
- `data/mission/.cli-history`
- 近期记录 `demo-readiness-mission-agent-observation-policy-fix.md`、`mission-agent-multi-turn-clarification.md`、`antigravity-change-audit-and-safety-hardening.md`。

## 本次事实链

Mission id：`preview-af75d7a33e2e4951860f07e6f771cd0d`。

1. `2026-08-23T14:56:24.567899+00:00`：Mission snapshot 记录机器人 `gazebo_turtlebot3` online、具备 navigation/patrol，但 robot projection 中没有 `pose`。
2. iteration 1，`14:56:26.567729Z` 至 `14:56:36.050728Z`：LLM 调用 `inspect_mission_state(kind="robot_state", subject_id="gazebo_turtlebot3")`。
3. observation 成功返回，但 robot 数据仍没有 `pose`；它在 iteration 2 的 authoritative `observations` 中确实被传回，未被上下文裁剪（600 chars，1 item）。
4. iteration 2，`14:56:36.061817Z` 至 `14:56:54.957774Z`：LLM 没有调用 `request_clarification`，而是再次调用完全相同的 `inspect_mission_state`。
5. runtime 的 `seen_reads={(robot_state, gazebo_turtlebot3)}` 命中，按新加的 no-progress 门禁终止为 `blocked/repeated_state_read`，Gateway 映射为 HTTP 422。
6. 没有生成 PlanArtifact，没有确认，也没有物理 dispatch。

## 根因

### 上游集成根因：规划前没有可用 TF 位姿

- `Ros1RobotAdapter._lookup_pose()` 只有在 `rospy.core.is_initialized()` 为真时才建立 TF buffer/listener 并读取 `map -> base_footprint/base_link`；否则真实 ROS 模式安全返回 `None`。
- 这是 2026-08-23 安全审计中为避免“只读状态探针暗中 `rospy.init_node()` 并在无 ROS master 时无限阻塞”做的 fail-honest 修复。
- 但 `FireClawGateway.start()` 当前没有显式拥有 ROS1 node lifecycle。
- `Ros1MoveBaseBackend._rospy()` 和 `Ros1RuntimeModule.load()` 会懒初始化 ROS node，但通常只在工具/动作真正调用后发生。新启动 Robot Gateway、尚未执行动作时，Mission preview 因而拿不到 pose。
- 这形成生命周期缺口：相对目标规划需要位姿，但位姿数据源要等执行工具首次调用后才初始化。

### 下游策略问题：LLM 没按已给提示 fail-safe 追问

- system prompt 已明确要求：相对目标缺少实测 pose 时必须 `request_clarification`，并禁止同一状态 kind 重读。
- 本轮模型仍重复读取，说明这是 provider/model 的工具选择不服从，而不是 observation 没回传。
- repeated-read 门禁按设计阻止了无效循环，但当前直接 422 的操作员体验不理想；可以考虑把首次重复读取确定性转换为 clarification，或在 policy/tool schema 层直接移除已经使用过的 inspect 选项。

## 为什么此前偶尔成功

较早 trace 中同类任务的 robot observation 曾包含实时 pose，例如 `(-1.9457, -0.5016, yaw≈0.007)`。当进程内已有 navigation backend/ROS transport 调用并初始化 ROS node 后，adapter 可以读 TF；Robot Gateway 重启后、首次动作前则重新暴露该缺口。因此表现会像“有时能规划，有时不能”。

## 临时可验证规避方式

把相对目标改为操作员明确坐标，例如：

`请依次巡检坐标(0.63, 0.54)、(1.0, 0.0)、(-1.0, -0.5)，最后返回坐标(-1.946, -0.502)。`

这样 target binding 不依赖缺失的当前 pose。不要用重启作为修复；重启会再次回到 ROS node 尚未初始化的状态。

## 正确修复方向

1. Robot Gateway 在启动/预检阶段显式、可超时地拥有 ROS1 node lifecycle；ROS master/TF 不可用时 readiness 诚实降级，不在任意只读 getter 内隐式初始化。
2. 让 pose 采集成为 Gateway-owned 的稳定 observation source，并带 freshness/frame/evidence，而不是依赖某个导航动作先运行。
3. 对“relative target + authoritative robot_state observation 无 pose”增加确定性 clarification fallback，避免依赖 LLM 自觉选择正确 tool。
4. 增加回归：全新 Robot Gateway 进程、执行任何导航动作之前，规划“返回现在的位置”；有 TF 时生成预览，无 TF 时追问，均不得 repeated read 或 dispatch。

## 工程与研究结论

- 工程上这是 ROS lifecycle 与 planning observation 的真实集成 bug；防循环门禁本身工作正确，但暴露了上游 pose acquisition 缺口和下游 LLM policy 的不稳定性。
- 研究上说明单靠 prompt 约束工具循环不足；安全关键具身 Agent 应把“已读状态集合”和“缺证据后的允许动作集合”编码为确定性 action mask/transition contract。

## 下一步

- 若用户授权修复，先检查 OpenClaw agent-loop 对已消费工具/重复动作的 tool availability 形态，再实现 FireClaw 的 ROS lifecycle/preflight 与 deterministic clarification fallback。
- 修改前保留当前大规模 dirty worktree，不覆盖无关 Antigravity/用户更改。

---

## 修复实施（2026-08-23 23:25 +0800）

用户明确要求立即修复。本轮保持原有大规模 dirty worktree，不重置、不删除也不提交无关改动。

### OpenClaw analogue

- 通过 CodeGraph 检查 `openclaw/src/gateway/server-runtime-startup-services.ts` 等 Gateway runtime service 生命周期。
- 复用其边界：进程级依赖在接受请求前启动，Gateway 持有生命周期，并在启动失败或停止时统一清理。
- OpenClaw 没有 ROS node/TF 对应物；ROS Master 预检、`rospy` node ownership 和 TF warmup 是 FireClaw 面向实体机器人的必要适配，不照搬 OpenClaw 的 channel/chat 假设。

### 已修改实现

- 新增 `src/fireclaw_core/ros/ros1_runtime.py`
  - `Ros1RuntimeLifecycle` 对 ROS Master 做带 socket timeout 的 XML-RPC `getPid` 预检；
  - 在单独 daemon startup thread 中调用 `rospy.init_node("fireclaw_gateway", anonymous=True, disable_signals=True)`，等待有界；
  - 区分自己创建的 node 与进程中已存在的 node，只 shutdown 自己拥有的 node；
  - 暴露结构化 `ready/degraded/stopped` 状态和稳定 reason code；
  - 对公开 snapshot 中的 ROS Master URI userinfo 做脱敏。
- `src/fireclaw_core/agent/robot.py`
  - `Ros1RobotAdapter` 新增 `start_runtime/stop_runtime/runtime_state`；
  - Gateway 启动阶段预热 TF，默认最多等待 2 秒；
  - 首次任务前即可创建 TF buffer/listener 并读取 `map -> base_footprint/base_link`；
  - ROS runtime 启动失败时真实 adapter 报 `online=False`；
  - 保留只读 `get_robot_state()` 不暗中初始化 ROS node 的安全合同。
- `src/fireclaw_core/gateway/gateway.py`
  - `FireClawGateway.start/serve_forever` 在创建 HTTP server 前启动 adapter runtime；
  - HTTP server 创建失败、blocking server 退出或显式 `stop()` 时清理 runtime；
  - `/state` 增加完整 `robot_runtime`，公开 `/health` 只投影非敏感 status/reason/node_initialized。
- `src/fireclaw_core/mission/plan_artifact.py`
  - 提取统一的 `command_has_relative_pose_reference()`，避免相对位姿语义在校验和 runtime 间漂移。
- `src/fireclaw_core/mission/mission_deliberation.py`
  - 保留 generic repeated state read 的 fail-closed 门禁；
  - 仅当命令含相对返航目标、已返回的 authoritative `robot_state` 确实没有 pose、模型又重复读取时，确定性转为 `clarification_required/coordinate_grounding_required`，不再直接 422。

### 新增/增强测试

- `tests/test_ros1_runtime.py`
  - master ready 后初始化；master 缺失时不调用 init；adopt 既有 node 不越权 shutdown；URI credentials 不外泄。
- `tests/test_ros1_adapter_state.py`
  - 新进程首次任务前 runtime startup 即产生实测 pose；runtime 失败时真实 adapter offline。
- `tests/test_gateway_runtime_lifecycle.py`
  - threaded `start/stop` 与 blocking `serve_forever` 都按先启动 runtime、后开放 HTTP、退出清理的顺序执行。
- `tests/test_llm_deliberation_policy.py`
  - 相对目标缺 pose 且模型重复读取时得到澄清，不是 HTTP 422/no-progress blocked。

### 验证结果

- 静态 `py_compile`：通过。
- 聚焦生命周期/规划/位姿回归：`76 passed`。
- 沙箱外 Gateway + ROS + deliberation 组合：`111 passed`。
- 更广 Mission/Robot Gateway/readiness/sealed-plan/serve 集成：`132 passed`。
- 真实 ROS/Gazebo 只读探针（临时匿名 node，无动作下发）：
  - runtime `ready/ros_node_initialized`；
  - pose `(-1.9411184009944797, -0.4972893194147391, yaw=0.0159701149605015, frame_id=map)`；
  - 临时 node 随后 shutdown。
- 全量沙箱外套件：`2455 passed, 8 skipped in 309.68s`。
- `git diff --check`：通过。
- 环境未安装 Ruff（`No module named ruff`）；不影响 pytest/compile/diff-check 结果。

### 当前结论

- 用户启动的 `roslaunch` 一直正常；缺口是独立 Robot Gateway 进程此前没有初始化自己的 `rospy` node。
- 修复后 Robot Gateway 在第一次 Mission preview 之前即可读取 TF，不再要求先执行一次导航动作来“暖机”。
- 若 ROS Master 或 TF 真不可用，系统会诚实 offline/追问坐标，而不是伪造 pose、无限重复读取或直接返回本次 422。
- 本轮没有创建 commit。

### 操作员复测步骤

1. 保留 Gazebo/`roslaunch` 终端运行。
2. 重启 Robot Gateway 进程以加载 `Ros1RuntimeLifecycle`。
3. 重启 Mission Gateway 进程以加载 deliberation clarification fallback。
4. 可先检查 Robot Gateway `/state`：`robot_runtime.status=ready`、`pose_status=ready`，且 `robot_state.pose.frame_id=map`。
5. 重新输入原始四点巡检加“最后返回现在的位置”命令；正常路径应生成带实测返航点的预览。若 TF 当时不可用，应得到坐标澄清问题，不应再出现 `repeated_state_read` 422。
