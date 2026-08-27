# 仿真与实机配置隔离

## 任务目标

用户要求把 Gazebo 仿真配置与未来真实机器人配置彻底分开，并把启动方式写清楚，避免以后通过
修改同一个 `fireclaw.toml` 的 mode 而误用。用户明确要求继续直接运行本地源码，使用 conda
`py310`，不增加 FireClaw 安装步骤。

## 2026-08-22 00:13 +08：配置边界确认

- CodeGraph 检查了 `find_config`、`load_config`、`GatewayConfig`、
  `resolve_gateway_config_with_profile`、`validate_robot_deployment_binding` 和 Robot Gateway 启动流。
- 结论：FireClaw 不会通过 Gazebo、ROS Master 或设备自动判断运行模式；
  `deployment.mode` 与 `robot_gateway.embodied_runtime_mode` 都来自配置。
- 现有安全校验会阻止 `deployment.mode=simulation`、`dry_run=false` 却未显式声明
  `embodied_runtime_mode=simulation` 的启动。这正是用户此前异常的原因。
- OpenClaw 无对应的机器人仿真/实机物理安全边界；本次沿用 FireClaw 已有 deployment binding，
  没有引入新的上游模块/API 设计。
- 原根目录 `fireclaw.toml` 是 ignored 私有文件、权限 `0600`，含本地 Provider 凭据；只允许原地
  改名，绝不复制凭据到 tracked 模板、文档或日志。

## 2026-08-22 00:27 +08：已完成修改

### 私有配置

- 将 ignored `fireclaw.toml` 改名为 ignored `fireclaw.sim.toml`；改名后重新设置权限 `0600`。
- 保留用户原有 Provider/model 设置，不输出、不复制凭据。
- 显式加入：
  - `mission.embodied_runtime_mode = "simulation"`
  - `robot_gateway.embodied_runtime_mode = "simulation"`
  - `robot_gateway.embodied_memory_path = "data/robots/gazebo_turtlebot3/embodied-memory.jsonl"`
  - `robot_gateway.embodied_memory_index = "data/robots/gazebo_turtlebot3/embodied-memory-index.sqlite3"`
- 已确认现有关键时限：Provider LLM 60 秒、导航 Tool 360 秒、Robot Agent loop 600 秒、
  Mission group 720 秒。

### 可提交模板与防误用默认值

- `fireclaw.example.toml` 改为 `fireclaw.sim.example.toml`，固定为 simulation-only；模板内部
  profile 引用改成 `fireclaw.sim.toml`，补齐上述 runtime/memory/timeout 字段。
- `examples/deployment_profiles/navigation_robot.toml.example` 改为根目录
  `fireclaw.real.example.toml`，扩展为明确的实机 fail-closed 模板。
- 实机模板默认：`deployment.mode=real`、`robot_gateway.dry_run=true`、
  `robot_agent.enabled=false`、`hardware_safety_acceptance.profile_reviewed=false`，并保留必须替换的
  `/opt` 安装路径、TLS/网络和厂商硬件接口。
- `.gitignore` 显式忽略 `/fireclaw.sim.toml`、`/fireclaw.real.toml`；继续忽略旧
  `/fireclaw.toml` 仅用于防止旧凭据文件被误提交。
- 没有创建可运行的私有 `fireclaw.real.toml`：当前没有真实硬件参数，生成“看似可用”的实机
  文件反而不安全。未来必须从 fail-closed 模板复制并完成现场验收。

### 文档与 CLI 文案

- 新增 `docs/deployment/simulation-real-config-separation.zh-CN.md`，包含四终端 Gazebo 演示命令、
  两种配置矩阵、三条不可混用规则、实机复制/验收流程和启动前核对命令。
- README、TurtleBot3、Navigation Plugin、ROS1、部署、硬件安全、评测、RAG、安全审计文档中的
  现行命令已改成明确的 `fireclaw.sim.toml` 或 `fireclaw.real.toml`。
- Robot Gateway、Mission `serve`、doctor、安全审计和 LLM planning eval 的帮助/错误文案不再把
  通用 `fireclaw.toml` 描述成推荐配置。底层仍保留旧默认名作为兼容路径，但新文档要求显式
  `--config`；不会自动选择 sim/real。
- `mission --server http://127.0.0.1:8766` 仍不接收配置：它是操作员 CLI，连接由 `serve`
  启动的 Mission Gateway。

## 验证结果

执行环境：`/home/lpp/miniconda3/envs/py310/bin/python`。

1. `pytest -q tests/test_mode_specific_config_examples.py tests/test_turtlebot3_burger_deployment_assets.py tests/test_ros1_hardware_safety_plugin.py`
   - `17 passed in 0.73s`
2. `pytest -q tests/test_gateway_robot_agent_cli.py tests/test_mission_cli.py tests/test_llm_planning_eval.py tests/test_security_audit.py tests/test_doctor.py`
   - 沙箱内首次运行：`82 passed, 6 failed`；6 项全部是沙箱禁止创建 loopback socket 的
     `PermissionError: [Errno 1] Operation not permitted`，不是代码断言失败。
   - 允许本地 socket 后重跑：`88 passed in 38.91s`。
3. `pytest -q tests/test_distribution_release_check.py tests/test_simulation_bundle_release.py tests/test_user_setup.py tests/test_plugin_runtime_deployment.py`
   - `49 passed in 19.27s`
4. 实际短启 Robot Gateway：加载 ROS setup 后运行本地源码与 `fireclaw.sim.toml`，输出
   `status=starting`、adapter `ros1`、robot `gazebo_turtlebot3`、监听 `127.0.0.1:8765`；5 秒后
   用 `timeout --signal=INT` 主动终止。此前的 simulation binding ValueError 未再出现。
5. 实际短启 Mission Gateway：使用 `fireclaw.sim.toml` 进入 `127.0.0.1:8766`，planner `llm`；
   5 秒后正常打印 `Gateway stopped.`。
6. `git diff --check`、相关 Python `compileall`、新文件尾随空白检查通过；两个端口均无残留监听。

聚焦测试合计 154 项通过（17 + 88 + 49）。

## 当前结论

当前 Gazebo 演示只应运行 `fireclaw.sim.toml`。旧命令中的
`--config /home/lpp/fireclaw-master/fireclaw.toml` 必须全部替换为
`--config /home/lpp/fireclaw-master/fireclaw.sim.toml`。未来真机不能由仿真配置改 mode 得到，
必须从 `fireclaw.real.example.toml` 建立独立私有文件并完成硬件安全验收。

## 下一步建议

- 用户演示时按新文档依次启动 Gazebo、Robot Gateway、Mission Gateway、Mission CLI。
- 真机到位后再收集实际 ROS topic/service/type、TF、bringup launch、IP、TLS 证书和厂商安全链信息，
  创建 `/etc/fireclaw/fireclaw.real.toml`。
- 若后续决定彻底移除旧默认文件名自动发现，需作为单独兼容性迁移处理，并更新相关 CLI 测试；
  本次没有破坏旧用户的读取兼容性。

## 2026-08-22 00:34 +08：Mission CLI HTTP 409 诊断

### 用户现场现象

- `python -m fireclaw_core mission --server http://127.0.0.1:8766 --timeout 75`
  能进入控制台。
- `status` 显示 `robot-1: offline; navigation, patrol`。
- 输入任务后 `/plan-mission` 返回 `HTTP Error 409: Conflict`，CLI 未处理 `HTTPError`，直接输出 traceback
  并退出。

### 精确根因

- 正确的私有 `fireclaw.sim.toml` 在 `[mission].robot_profiles` 中声明
  `examples/robot_profiles/gazebo_turtlebot3.toml`，该 Profile 的机器人 ID 是
  `gazebo_turtlebot3`，不可能正常显示为 `robot-1`。
- 本机 `~/.fireclaw/data/robots.json` 中恰好存在默认 `robot-1`，base URL 为
  `http://localhost:8765`，能力为 navigation/patrol；它与用户 `status` 输出完全一致。
- 因而正在服务 8766 的 Mission Gateway 没有加载 `fireclaw.sim.toml`。最可能是仍使用了改名前的
  `--config fireclaw.toml`，或完全省略 `--config`。
- `find_config(explicit_path)` 对不存在的显式路径返回 `None`，调用方没有报错，而是静默进入默认 runtime
  `~/.fireclaw`。这是一个 fail-open/UX 缺口：显式写错路径应立即拒绝启动。
- `/plan-mission` 在调用 LLM 前先要求 authoritative runtime identity、当前 Profile revision 和 fresh online
  robot evidence。默认启动没有 authoritative active Profile，且 `robot-1` offline，因此安全地返回 409；
  `--timeout 75` 只控制 CLI HTTP 等待时长，与 409 无关。
- `MissionGatewayClient._do_request()` 当前捕获 `HTTPError` 后原样抛出；interactive CLI 没有把 Gateway 的
  JSON `error_code/message` 转为可读提示，所以本应是正常可解释的冲突响应，却导致整个 REPL traceback 退出。

### 当前建议（本次仅诊断，未修改产品代码）

1. 停止当前 Mission Gateway 与 Robot Gateway。
2. 两个进程都显式使用绝对路径
   `/home/lpp/fireclaw-master/fireclaw.sim.toml` 重新启动。
3. Robot Gateway 启动行应显示 `robot_id=gazebo_turtlebot3`、adapter `ros1`；Mission CLI 的
   `status` 必须显示 `gazebo_turtlebot3: online` 后再提交导航任务。
4. 建议下一轮正式修复两点：显式 `--config` 不存在时启动即报错；CLI 解析 4xx JSON 并保留交互会话。

## 2026-08-22 01:48 +00：四终端命令现场彩排（等待安全恢复确认）

### 启动与底层验证

- 上一轮中断实际遗留两个进程；普通 sandbox process view 未显示，但 elevated `ss/lsof` 确认：
  - PID 329920：Robot Gateway，正确使用 `fireclaw.sim.toml`；
  - PID 330693：Mission Gateway，错误使用已不存在的 `fireclaw.toml`。
- 读取错误 Mission Gateway `/readiness` 留证：runtime identity freshness=`unknown`、active profile=null、
  registry=`robot-1`。停止 PID 330693 后按新绝对路径重启。
- 启动 `fireclaw_acceptance_world.launch gui:=true` 成功。已验证：
  - ROS nodes 包含 gazebo、gazebo_gui、map_server、amcl、move_base；
  - `/scan`=`sensor_msgs/LaserScan`；
  - `/odom`=`nav_msgs/Odometry`；
  - `/move_base/goal`=`move_base_msgs/MoveBaseActionGoal`；
  - `map -> base_footprint` TF 稳定，初始位置约 `(-1.981, -0.488)`。

### 新发现并修正的 authoritative Profile 缺口

- 即使 Mission Gateway 使用原私有 `fireclaw.sim.toml`，`/readiness` 仍报告 runtime identity UNKNOWN。
- CodeGraph 检查 `build_gateway_runtime_identity()`：active config 只有同时通过
  `load_robot_capability_profile()` 与 `load_runtime_deployment_profile()` 才能形成 fresh identity；只在
  `[mission].robot_profiles` 中引用另一个 Profile 不够。
- 已在 ignored 私有 `fireclaw.sim.toml` 中补齐与 tracked simulation template 一致的统一 Profile：
  - `[robot]`、capability chains、sensor discovery；
  - `[deployment].id`、ROS setup、robot launch、navigation bindings/limits/start pose；
  - `[plugins].selected=["fireclaw.navigation.move-base"]`，paths 改为 `extensions`。
- 未触碰/输出 Provider secret，修改后恢复文件权限 `0600`。
- 离线校验通过：robot=`gazebo_turtlebot3`、adapter=`ros1`、deployment ID=`gazebo-turtlebot3-burger`、
  mode=`simulation`、runtime identity freshness=`fresh`。
- 两个 Gateway 用新 revision 重启后，Mission `/readiness` 返回：
  runtime mode=`simulation`、active profile 为绝对 `fireclaw.sim.toml`、robot ID=`gazebo_turtlebot3`、
  identity freshness=`fresh`，robot readiness=`online/fresh`。

### Mission CLI 与 LLM 规划验证

- 真实启动：`python -m fireclaw_core mission --server http://127.0.0.1:8766 --timeout 75`。
- CLI 简化 `status` 仍显示 `gazebo_turtlebot3: offline`，但 `/readiness` 的主动 Robot Gateway probe 是
  online/fresh。这是旧 registry heartbeat 投影与 authoritative readiness 的显示不一致；计划安全门使用后者。
- 输入：
  `让 gazebo_turtlebot3 前往地图坐标 (1.8, -0.1)，保持朝向 yaw=-2.34 弧度并执行巡逻`
- LLM preview 成功，未再出现 409：
  - digest `sha256:0c6a65748a2d960ec554d120f4969d49fee7983509ce30c16aee697f050a3143`
  - robot `gazebo_turtlebot3`
  - risk `medium`
  - step `导航到指定位置并执行巡逻`
- 输入 `n`；计划没有下发，未产生本轮机器人动作。

### 历史安全冻结与当前阻塞点

- Robot Gateway 持久状态仍有 2026-08-12 的冻结：
  `physical_runtime_stop_unconfirmed`，task `task-2ebb389110dd492280a26dd1adaa617d`，admission closed。
- 没有删除 SQLite 或绕过 Safety Gate。使用官方命令执行 `recover --request-only --json`。
- stop evidence 通过：deployment=`simulation`、active move_base goals=0、active leases=0、
  stationary samples=16、linear max≈`8.1e-07`、angular max≈`1.21e-05`、blockers=[]。
- 待确认 recovery request：
  - ID `recovery-b924108a46804d3d87bc8f4a7c34cfb3`
  - phrase `RECOVER gazebo_turtlebot3 recovery-b924108a46804d3d87bc8f4a7c34cfb3`
  - 原始过期时间 `2026-08-22T01:52:56.071720+00:00`；若用户回复时已过期，重新生成请求。
- 本轮暂停等待用户明确授权解冻当前 Gazebo 仿真。未替用户输入 phrase。

### 当前运行会话（本线程工具 session）

- Gazebo/ROS：session `27597`
- Robot Gateway：session `15048`
- Mission Gateway：session `76160`
- Mission CLI：session `37156`

若用户确认后继续：先提交/重建 recovery confirmation，再检查 admission open；重新提交同一自然语言任务并让
用户确认 `yes`，观察 Robot LLM、move_base 和 Mission terminal result；最后依次退出 CLI、Mission Gateway、
Robot Gateway、Gazebo，并确认 8765/8766/11311 无残留监听。

## 2026-08-22 09:56 +08：安全恢复、LLM 实际导航与收尾核验完成

### 官方安全恢复

- 用户明确输入官方确认短语：
  `RECOVER gazebo_turtlebot3 recovery-b924108a46804d3d87bc8f4a7c34cfb3`。
- 使用 `python -m fireclaw_core recover --profile ... --request-id ... --confirmation-phrase ... --json`
  完成恢复，没有删除状态库或绕过 Safety Gate。
- 恢复结果：phase=`ready`、safe state=`motion_admitted_idle`、reason=`resource_admission_recovered`；
  `emergency_stop.active=false`、`resource_admission.admission.closed=false`，恢复时间
  `2026-08-22T01:49:52.667613+00:00`。
- 任务完成后的再次检查仍为：急停未激活、admission open、active leases=[]、active tasks=[]。

### 已实际执行的 LLM 规划与导航

- 再次向 Mission CLI 输入：
  `让 gazebo_turtlebot3 前往地图坐标 (1.8, -0.1)，保持朝向 yaw=-2.34 弧度并执行巡逻`，并在
  LLM preview 后输入 `yes`。
- 本次不是确定性 fallback：Mission Agent 生成计划，封存计划 artifact 后下发给 Robot Agent；Robot
  capability policy 最终授权并调用原子工具 `navigate_to_point`。
- 审计标识：
  - mission ID：`mission-ae27ae78e9b847acb8603a5ee1cc1b14`
  - robot task ID：`task-89f663026b06497fa985dcbdc24e5d64`
  - plan digest：`sha256:35d2680e5e86de443a73f393f8edc5adbb91bb135ed270c3f55e7754b15713c7`
  - plan source：`sealed_plan_artifact`
- Mission Gateway 终态只读核验：status=`completed`、run status=`completed`、terminal=`true`、
  result status=`succeeded`、message=`Sealed mission plan execution finished.`。
- `move_base` 日志明确出现 `Goal reached`。
- Gazebo `/gazebo/get_model_state` 最终实测：x=`1.8094666099451773`、
  y=`-0.10162110429201383`，姿态四元数 z=`0.9080247748210932`、
  w=`-0.4188987325820051`（yaw 约 `-2.28 rad`）；目标为 `(1.8, -0.1, -2.34)`，在配置容差内。

### 新确认的非阻塞 UX 问题

- Mission CLI `status` 读取旧 fleet registry heartbeat 投影，可能显示
  `gazebo_turtlebot3: offline`；同一时刻 authoritative `/readiness` 主动探测为 `online/fresh`，
  LLM 安全门和实际执行使用后者。本次任务成功证明不是 Robot Gateway 真的离线。
- Mission 已完成后，CLI 事件跟随仍停在 `accepted` 并从 cursor=5 重连，没有及时打印终态；按
  `Ctrl+C` 只停止本地观察，不取消远端任务。反复断开的 HTTP 客户端还会使 Mission Gateway 打印
  `ConnectionResetError`/`BrokenPipeError`。这是显示/事件流恢复问题，不是导航失败。
- Robot Gateway 收到 `Ctrl+C` 后进程会退出，但当前会打印 `KeyboardInterrupt` traceback；属于退出体验问题。

### 清理结果

- 依次退出 Mission CLI，停止 Mission Gateway、Robot Gateway、Gazebo acceptance launch。
- 端口 `8765`、`8766` 均已无监听；本轮的 `gzserver`、`gzclient`、`move_base`、`amcl`、
  `map_server` 均已退出。
- 本机此前已有一个从 2026-08-21 运行的无关 ROS 会话：PID 29860
  `/opt/ros/noetic/bin/roslaunch run.launch`，其 rosmaster PID 29884 监听 `11311`。它不是本轮
  `fireclaw_acceptance_world.launch` 的子进程，按“不破坏用户已有进程”原则保留，未擅自停止。

### 当前结论与下一步

- 四终端 Gazebo 演示链路已实测通过：ROS/Gazebo → Robot Gateway/Robot Agent → Mission
  Gateway/Mission Agent → Mission CLI；LLM 规划、人工确认、安全授权、实际 move_base 导航和审计终态
  均已闭环。

## 2026-08-22 16:33 +08：模糊导航指令的 LLM 决策边界

### 现场现象与证据

- 明确坐标命令已通过 Mission LLM、sealed plan、Robot Agent 和 `navigate_to_point` 完成真实 Gazebo 导航。
- 对 `随便往前走到一个没有障碍物的地方` 做了两次只到 preview 的验证：
  - 一次模型没有返回 Tool call，`/plan-mission` 返回 HTTP 422，原 CLI 因未处理 `HTTPError` 而退出；
  - 另一次模型在没有地图证据的情况下猜测 `(0, 0, 0)`。操作员输入 `n`，计划未下发，机器人未动作。
- 已修复 Mission CLI 的 4xx 处理：增加保留 `HTTPError` 兼容性的 typed request error，并在 interactive
  preview/status/cancel/confirm 路径显示可读错误而不退出。聚焦测试
  `tests/test_mission_gateway_client.py tests/test_interactive.py` 为 `44 passed in 13.79s`，`py_compile` 与
  `git diff --check` 通过。

### CodeGraph 结论

- `MissionGateway.plan_mission()` 当前直接调用 canonical planner 的 `plan()`。
- `LLMMissionPlanner.plan()` 确实把原始用户命令作为 user message 发给模型，但只暴露一个
  `create_mission_plan` Tool，要求且只允许一次 Tool call。因此这条 preview 路径不能正式返回 clarification、
  escalation 或 map observation 决策。
- `LLMMissionPlanner.decide()` 和 deliberation tools 已经存在：`request_clarification`、`escalate`、
  `request_observation`、state inspection、graph/plan proposal；但 `/plan-mission` preview 尚未接入这条 loop。
- 现有 `request_observation` 是对已建模 unresolved belief 的宿主调度请求，本身不会直接访问传感器；当前
  Navigation Plugin 也没有 `suggest_safe_navigation_targets`、occupancy/costmap free-space query 或 reachability
  candidate Tool。现有 Agent Tools 主要是 move_base status/parameter/cancel/clear-costmaps，物理 Tool 是
  `navigate_to_point`。

### 用户确认的设计方向

- 模糊意图的选择应由 LLM Agent 完成：可以请求操作员澄清、调用只读地图/状态 Tool 后提出有证据的目标，或在
  证据不足/危险时拒绝或升级；不能用确定性关键词规则替代 Agent 决策。
- 确定性 runtime 仍必须负责 Tool schema、权限、地图/姿态证据来源与 freshness、clearance/reachability、
  frame、sealed digest、一致性复核和 one-shot operator authorization。它验证 LLM 的建议，不替 LLM 做语义规划。
- 中断前加入的“导航坐标必须原样出现在 operator command”校验只能视为当前没有地图 Tool 时的临时 fail-safe，
  不能作为最终架构。最终应改为 provenance-aware binding：目标可以来自 `operator_supplied`，也可以来自受信任
  map Tool 的 `tool_observed` evidence；无任一来源时才拒绝。

### 下一步建议

1. 将 sealed preview 接到已有 Mission deliberation loop，使 clarification/escalation 成为正常响应而非 HTTP 422。
2. 在 Navigation Plugin/Robot Gateway 增加只读、边界化的安全候选目标 Tool：根据当前 pose、前向扇区、
   occupancy/costmap、机器人 footprint clearance 与 `move_base/make_plan` reachability 返回少量候选及证据，不把
   整张地图直接交给 LLM。
3. 让 LLM 从候选中选择并解释，把 target provenance、map revision、observed_at、clearance 和 path evidence 封入
   PlanArtifact；preview 后仍需操作员 `yes` 才执行。
4. preview 与 confirmation 之间若地图、定位或 reachability evidence 漂移，则使旧 artifact 失效并重新规划。
- 正式演示继续使用绝对路径 `/home/lpp/fireclaw-master/fireclaw.sim.toml`；不要回退到旧
  `fireclaw.toml`。
- 下次正常启动无需再次执行本次 recovery；只有 Safety Gate 产生新的 freeze 时，才按新 request ID
  重新执行官方恢复流程。
- 后续产品修复优先级：Mission CLI event terminal/reconnect、`status` authoritative readiness 投影、
  Robot Gateway 优雅处理中断；这些不阻塞当前导航演示，但会影响面试现场观感。

## 2026-08-22 10:28 +08：CLI 演示体验修复并完成第二轮端到端回归

上一节列出的三项演示 UX 问题已在本次继续处理中修复并实际复测，不再作为遗留项。

### OpenClaw analogue 与 FireClaw 适配

- 先用 CodeGraph 检查 OpenClaw：
  - `openclaw/packages/sdk/src/event-hub.ts` 的 `EventHub` 使用 bounded replay，并允许 stream 在终态关闭；
  - `openclaw/src/gateway/server-methods/health.ts` 会将 cached health 与 live runtime snapshot 对比，
    operator status 不盲信陈旧缓存。
- FireClaw 复用这两个结构原则，但保留机器人安全适配：Mission SSE 仍使用 sequence cursor 与 bounded
  replay；operator `status` 改读包含 Robot Gateway 主动 probe/evidence envelope 的 `/readiness`；完整
  传感器与事故报告仍保存在 report/audit API，不塞入实时控制事件。

### 根因与代码修复

1. CLI 终态重连死循环：
   - `mission.report_ready` 与 terminal event 原先把完整 `final_report` 放进 SSE；本轮真实报告包含大量
     sensor findings，超过 Mission Client 的 `max_sse_event_bytes=256 KiB`。
   - cursor replay 每次都先遇到同一个超大 report event，因此永远读不到后续 `mission.completed`；full
     `/trace` 兜底也过大，造成客户端复连和服务端 BrokenPipe/ConnectionReset。
   - `MissionGateway._publish_run_event()` 现在仅发布有界 report 摘要和 `report_available=true`；完整报告
     仍通过 report/run 审计接口读取。SSE 写出 mission terminal event 后主动结束连接。
   - 新增 `/missions/{id}/run?view=status` 有界状态投影和
     `MissionGatewayClient.get_mission_run_status()`；CLI 在事件流断开时优先用这个小响应确认终态，不再
     下载完整 trace。
2. `status` 误报 offline：
   - `run_interactive` 不再读取只含 registry heartbeat 的 `/fleet/state`，改读 authoritative
     `/readiness.robot_readiness`。
   - 输出已实测为：`Runtime：simulation; ready`，机器人为
     `gazebo_turtlebot3: online; navigation, patrol, emergency_stop`。
   - Mission 层没有独立 safe-state evidence 时不再显示无意义的 `/unknown`，但不会伪造 safe state。
3. Fleet Doctor safety warning：
   - Robot Gateway 实际提供 emergency-stop control plane，但 Gazebo Profiles 只声明 navigation/patrol，
     导致 `No enabled robot declares emergency_stop capability` 和 degraded。
   - 私有 `fireclaw.sim.toml`、根 simulation example、robot profile、setup template 与 packaged resource
     统一声明 `emergency_stop` capability；它没有加入 `enabled_skills` 或 `llm_exposed_skills`，LLM 仍不能
     自行调用急停。
4. Robot Gateway 退出：
   - `_run_robot_gateway()` 捕获 `KeyboardInterrupt`，现在 `Ctrl+C` 输出
     `Robot Gateway stopped.` 并以 0 退出，不再打印 traceback。
5. 封存计划预览：
   - Gateway 的 preview step 现在携带经过验证的 structured target；CLI 在自然语言 step 下方显示
     `目标：map (x=..., y=..., yaw=...)`。即使 LLM 将 step 文案概括成 `Navigate to target position`，
     操作员仍能在输入 `yes` 前核对精确坐标和朝向。

### 第二轮真实 Gazebo 结果

- 继续使用本地源码 `/home/lpp/miniconda3/envs/py310/bin/python -m fireclaw_core ...`，没有安装
  `fireclaw` 命令。
- 先测试目标 `(0.0, -0.1, yaw=0.0)`：该点在当前地图上无法形成有效路径，move_base 执行完 recovery
  后明确 `Aborting because a valid plan could not be found`。Mission
  `mission-4b706d6cd6db440b95597754162517d8` 正确终止为 blocked；CLI 直接显示
  `[完成] 已阻塞（blocked）` 并回到提示符。这不是 360 秒导航超时。
- 随后使用已知可达目标 `(1.8, -0.1, yaw=-2.34)`：
  - Mission ID：`mission-977e51857b554a258759aab442f1432c`
  - plan digest：`sha256:22843491b8e215c9125313dc18fff2b97a1533fe4846e81fc9049496accbc80a`
  - plan source：`sealed_plan_artifact`
  - created=`2026-08-22T02:20:32.687751+00:00`，updated=`2026-08-22T02:22:02.676044+00:00`
  - bounded run status：status/run_status=`completed`、terminal=`true`、result=`succeeded`、
    report status=`completed`
  - move_base：`Goal reached`
  - CLI：依次显示 terminal events 和 `[完成] 已完成（succeeded）`，自动回到 `fireclaw>`；没有重连
    或服务端异常。
  - Gazebo 最终位姿：x=`1.8144294393174243`、y=`-0.06138046804320275`，quaternion
    z=`-0.9264378761514744`、w=`0.3764279215682499`（yaw 约 `-2.37 rad`），在目标容差内。
- 失败目标之后 safety 状态仍正确：emergency stop false、resource admission open、active tasks=[]；没有因
  普通不可达目标错误冻结机器人。

### 验证与清理

- 最终聚焦回归（含 sealed-plan preview）：
  `pytest -q tests/test_interactive.py tests/test_mission_gateway_client.py tests/test_mission_gateway.py tests/test_gateway_robot_agent_cli.py tests/test_sealed_plan_gateway.py tests/test_mode_specific_config_examples.py tests/test_turtlebot3_burger_deployment_assets.py tests/test_simulation_bundle_release.py tests/test_user_setup.py`
  → `147 passed in 50.23s`。
- 相关 Python `py_compile` 和 `git diff --check` 通过；私有 `fireclaw.sim.toml` 权限保持 `0600`。
- 最终配置只读核验：planner=`llm`、Provider timeout=`60s`、navigation Tool timeout=`360s`、
  Robot Agent loop=`600s`、Mission group=`720s`；LLM 暴露项仍只有 `navigate_to_point`。第一次核验误用
  Python 3.11 才内置的 `tomllib`，在本机 Python 3.10 得到 `ModuleNotFoundError`；改用项目
  `fireclaw_core.infra.tomllib_compat` 后成功，未修改配置。
- 清理顺序：Mission CLI → Mission Gateway → Robot Gateway → acceptance Gazebo launch；8765/8766 无监听，
  本轮 fireclaw/Gazebo/move_base/amcl/map_server 进程无残留。
- 仍只保留用户原有 2026-08-21 `roslaunch run.launch` 的 rosmaster PID 29884（11311），未擅自终止。

### 最终结论

- 面试所需四终端最小演示现在形成完整闭环：LLM preview → plan digest → operator `yes` → sealed plan →
  Robot Agent/Tool policy → move_base → 成功或失败终态 → CLI 返回提示符。
- 演示建议使用已验证可达目标 `(1.8, -0.1, yaw=-2.34)`；不要使用本轮确认不可达的 `(0.0, -0.1)`。

## 2026-08-22 15:40 +08：用户现场任务失败——Robot Gateway ROS Python 环境缺失

### 现场现象与权威证据

- 用户在 Mission CLI 提交并确认目标 `(1.8, -0.1, yaw=-2.34)`：
  - mission ID：`mission-3a7850606b28430ea9e8b3752feb00a6`
  - plan digest：`sha256:f12b64586ccbea5886d12e5321efbdac316bd2d44541e38aaeed0d40205e00a6`
  - plan source：`sealed_plan_artifact`
- CLI 显示连续 `failed` 并终止。Mission bounded run status 确认
  `status=failed`、`terminal=true`、`report_available=true`；这不是 CLI 假报。
- 完整 report 中 Robot task 为 `task-e4f8b5d3188d4e82ac6385b7b19a5706`，失败发生在 Robot Agent
  planning 阶段，`execution.steps=[]`、`attempts=[]`，没有向 `move_base` 下发 goal，也不是导航/Agent loop 超时。
- capability policy 的精确阻断证据：`required_sensor_unavailable`，缺少 `lidar`；Robot state 的传感器发现错误为
  `ROS1 topic discovery failed: Command '['rostopic', 'list']' returned non-zero exit status 1.`。

### 精确根因

- Gazebo、`map_server`、AMCL、`move_base`、`/scan` 和 ROS Master 均仍正常运行。
- 原 Robot Gateway PID `400959` 的 `ROS_MASTER_URI=http://localhost:11311` 正确，但 `PYTHONPATH` 未设置。
- 使用该进程原环境复现 `rostopic list`，stderr 为：
  `ModuleNotFoundError: No module named 'rostopic'`。
- 因此根因是 Robot Gateway 终端没有形成正确的 conda → ROS setup 环境（漏 source 或 source/activate 顺序错误），
  不是 LLM、sealed plan、坐标、`move_base` 或 360/600/720 秒 timeout。
- Mission `status` 的 `online` 仅证明 Robot Gateway HTTP 可达；本次更细的 capability policy 正确地阻止了在 lidar
  无权威证据时执行导航，但 CLI 顶层汇总 `Agent decision policy failed` 过于含糊。

### 运行时修复与验证

- 在 active tasks 为空、resource admission open、emergency stop false 的状态下，对 PID `400959` 发送 SIGINT，
  只重启 Robot Gateway；Mission Gateway、Mission CLI、Gazebo 和 ROS 未停止。
- 正确环境顺序：activate conda `py310`，再 source `/opt/ros/noetic/setup.bash`、Navigation workspace、Robot
  workspace，最后把 checkout `src` 前置到已由 ROS setup 建立的 `PYTHONPATH`。
- 启动前只读验证通过：`rospy`、`actionlib`、`rostopic`、`move_base_msgs.msg` 均可导入；`rostopic list`
  包含 `/scan`、`/imu`、`/move_base/status`。
- 新 Robot Gateway 已在 `127.0.0.1:8765` 启动；`GET /state` 现在报告
  `available_sensors=["imu", "lidar"]`，两者 health 均为 healthy，active tasks 为空、admission open。
- Mission `/readiness` 随后报告 runtime=`simulation`、phase=`ready`、robot=`gazebo_turtlebot3`、
  status=`online`、freshness=`fresh`。
- 未修改产品源码或配置；仅修复当前运行进程的启动环境。旧 artifact 已消费且 failed，不复用旧 `y`；下一步由用户在
  现有 Mission CLI 重新输入相同命令，并对新 digest 再次明确输入 `y`，随后核验 Robot LLM、move_base 和终态。

### 2026-08-22 15:47 +08：临时进程占用端口的交接修正

- 用户按正确环境顺序完成验证：ROS Python imports、`/scan` type 和 `/move_base/goal` type 均通过。
- 用户启动 Robot Gateway 时得到 `OSError: [Errno 98] Address already in use`；原因是上一节由本代理为验证修复而
  启动的临时 Robot Gateway 仍监听 `127.0.0.1:8765`，不是用户命令或 ROS 环境错误。
- 已向本代理持有的临时 Gateway session 发送 SIGINT，进程正常输出 `Robot Gateway stopped.`；随后 `lsof` 返回无
  监听，确认 8765 已释放。未停止用户的 Gazebo、Mission Gateway 或 Mission CLI。
- 用户现在只需在同一已正确 source 的终端重新执行 `python -m fireclaw_core robot-gateway --config
  /home/lpp/fireclaw-master/fireclaw.sim.toml`，无需重做前面的环境命令。
