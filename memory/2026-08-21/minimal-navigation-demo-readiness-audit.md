# FireClaw 最小导航演示就绪度审计

## 2026-08-21T19:13:45+08:00

### 任务目标

用户准备参加面试，希望做一个最小但可信的 FireClaw 演示：启动 Mission Agent 与 Robot Agent，操作员通过
Mission Agent 输入自然语言任务，Robot Agent 调用导航 Plugin/Tool，在 TurtleBot3 Gazebo 中完成简单二维
导航。本轮只审计、验证和记录缺口，不修改产品代码，不提交、不推送、不连接真实机器人。

### 恢复依据与启动状态

- 按根 `AGENTS.md` 读取最近两个日期目录：
  - `memory/2026-08-20/stage-2-3-first-use-authoritative-web.md`
  - `memory/2026-08-20/stage-4-sealed-plan-confirmation.md`
  - `memory/2026-08-19/stage-1-reproducible-baseline.md`
  - `memory/2026-08-19/resumption-audit.md`
  - `memory/2026-08-19/seven-stage-ux-closure-audit.md`
- 当前分支：`agent/embodied-evaluation-collision-calibration`；HEAD `6790489`，相对远端领先 7 个提交。
- 阶段 1 已形成提交；阶段 2–4 仍在工作树中，当前 tracked diff 为 45 个文件、
  `3377 insertions(+), 702 deletions(-)`，另有 `simulation_runtime.py`、`plan_artifact.py`、
  `runtime_identity.py` 及相应测试/记录未跟踪。
- `git diff --check`：PASS。
- 解释器：`/srv/lpp-extra/miniconda3/envs/py310/bin/python`，Python 3.10.4。
- 本机存在 `/opt/ros/noetic/bin/roscore`、`roslaunch`、`gazebo`、`gzclient`，`DISPLAY=:0`。
- 当前没有可复用的 `.fireclaw` runtime、active Profile 或 companion bundle；`fireclaw` console script 也未安装
  到当前 shell。源码入口 `PYTHONPATH=src:. ... python -m fireclaw_core` 可用。

### CodeGraph 调用链结论

`.codegraph/` 健康且 up to date：788 files、15043 nodes、46582 edges。先用 CodeGraph 检查：

- `infra/user_setup.py::handle_setup`、`complete_simulation_first_use`
- `infra/daemon_manager.py::DaemonRuntimeManager.start_daemon`
- `deployment/supervisor.py::RuntimeSupervisor._run_generation`
- `gateway/gateway.py::FireClawGateway`、`_build_robot_agent_runtime`
- `gateway/serve.py::start_server`
- `mission/mission_gateway.py::plan_mission`、`confirm_plan`
- `mission/interactive.py::run_interactive`、`_submit_and_follow`
- `mission/mission_planner.py::MissionPlanner.plan`
- `agent/robot_agent.py::RobotAgentRuntime.plan_structured_task`
- Navigation Plugin `entrypoint.py` 与 `move_base.py::Ros1MoveBaseBackend.navigate_to_point`
- physical Tool registry、SafetyGate 与 `RobotActionRuntime.run`

本轮没有设计或修改 OpenClaw 已有模块，因此没有重新打开 upstream source；阶段 2–4 memory 已记录此前检查的
OpenClaw lifecycle owner boundary 与 approval/token analogue。本轮复用当前已实现结构进行演示审计，没有引入新 API。

### 当前最小演示实际架构

默认生成的 simulation Profile 已配置：

- Mission Gateway：`127.0.0.1:8766`
- Robot Gateway：`127.0.0.1:8765`
- Mission Planner：`deterministic`
- Robot Agent：`enabled=true`，planner 为 `deterministic`
- Robot Adapter：`ros1`
- Navigation Plugin：`fireclaw.navigation.move-base`
- Robot capability：`navigation`、`patrol`
- primitive/LLM exposed Tool：`navigate_to_point`

Supervisor 按顺序托管三个服务：

1. `fireclaw-bringup`：ROS/Gazebo robot base + Navigation Plugin 固定 launch；
2. `fireclaw-gateway`：Robot Gateway + Robot Agent；
3. `fireclaw-mission-gateway`：Mission Gateway + Mission Agent。

操作员路径为：

```text
fireclaw mission
  -> POST /plan-mission
  -> canonical MissionPlanner
  -> immutable PlanArtifact + digest/token
  -> CLI 显示预览并要求 yes
  -> POST /plan-mission/confirm
  -> consume same sealed plan once
  -> MissionRunManager.submit_preplanned
  -> MissionAgent.execute_sealed_plan
  -> MissionScheduler
  -> Robot Gateway structured task
  -> deterministic RobotAgentRuntime
  -> navigate_to_point physical Tool
  -> Ros1MoveBaseBackend
  -> ROS1 /move_base
```

Simulation Profile 中 `robot_gateway.dry_run=true` 的含义是禁止把仿真权限误解释为实机动作权限，并跳过第二层
实机 execution authorization；它不会跳过 Gazebo backend。`RobotActionRuntime` 仍调用 ROS1
`navigate_to_point`，所以 Gazebo 中会真实移动。

### 自然语言边界

默认 Mission Planner 不是开放域聊天模型；它是为了演示稳定性准备的确定性 fallback，不需要 Provider key。
它同时要求：

1. 命令含受支持 intent 关键词；
2. 命令含明确二维坐标，格式如 `坐标 (2.0, 0.0)`；
3. 当前只接受 `frame_id=map`，不接受楼层任务。

当前最稳命令是：

```text
导航测试到坐标 (2.0, 0.0)
```

它匹配 `patrol`，而 simulation Profile 声明了 `patrol` capability。单独输入 `去坐标 (2.0, 0.0)` 会在
Mission Agent 层返回 clarify，因为当前 intent pattern 没有通用 `navigate`/`前往` intent；Robot Agent 自己虽能
解析点导航，但 operator 必须先通过 Mission Agent。这是面试前值得修的小型 UX 缺口。

### 聚焦回归结果

运行：

```bash
PYTHONPATH=src:. /srv/lpp-extra/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_user_setup.py tests/test_daemon_manager.py tests/test_interactive.py \
  tests/test_sealed_plan_gateway.py tests/test_sealed_plan_execution.py \
  tests/test_move_base_navigation_plugin.py tests/test_readiness_contract.py \
  tests/test_gateway_robot_profile_config.py tests/test_simulation_runtime.py \
  tests/test_embodied_gateway_e2e.py tests/test_embodied_mission_e2e.py
```

沙箱内结果：`73 passed, 3 failed`。三项失败都在创建 `127.0.0.1` socket 时得到
`PermissionError: [Errno 1] Operation not permitted`，没有进入产品逻辑。允许 loopback 后只重跑三项：

```text
3 passed in 2.69s
```

因此关键非 live 演示合同合计 76 项通过。

### Live Gazebo 验收：失败尝试与根因

第一次运行固定 success lane：

```bash
FIRECLAW_PYTHON=/srv/lpp-extra/miniconda3/envs/py310/bin/python \
FIRECLAW_GAZEBO_ACCEPTANCE_RESULTS_ROOT=/tmp/fireclaw-demo-readiness-20260821 \
bash extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

结果：`1 failed, 5 skipped`。ROS、TF、`/scan`、`/odom` 和 `/move_base` 已 ready，但测试在任何 goal 下发前
等待 `/fireclaw/acceptance/contacts` publisher 超时。失败证据：

```text
/tmp/fireclaw-demo-readiness-20260821/20260821T110845Z-129075
```

根因不是 Agent/navigation 逻辑。runner 依次 source：

1. `/opt/ros/noetic/setup.bash`
2. `extensions/navigation-move-base/ros_ws/devel/setup.bash`
3. `robots/turtlebot3_burger/ros_ws/devel/setup.bash`

第三步覆盖了第二个独立 Catkin workspace 的 `CMAKE_PREFIX_PATH`、`ROS_PACKAGE_PATH` 和 `LD_LIBRARY_PATH`；最终
环境里没有 Navigation workspace，也没有 `GAZEBO_PLUGIN_PATH`，Gazebo 因而找不到
`libfireclaw_gazebo_contact_monitor.so`。`ldd` 显示该 `.so` 自身依赖完整，世界文件也确实声明了插件。

### Live Gazebo 复验：PASS

不修改代码，显式保留插件路径后复跑：

```bash
GAZEBO_PLUGIN_PATH=/home/lpp/fireclaw-master/extensions/navigation-move-base/ros_ws/devel/lib \
FIRECLAW_PYTHON=/srv/lpp-extra/miniconda3/envs/py310/bin/python \
FIRECLAW_GAZEBO_ACCEPTANCE_RESULTS_ROOT=/tmp/fireclaw-demo-readiness-20260821-fixed-env \
bash extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

结果：

```text
1 passed, 5 skipped in 36.02s
evaluation status=pass
task_success_rate=1.0
contract_pass_rate=1.0
plugin_contract_rate=1.0
adapter_fallback_free_rate=1.0
event_reconstructability_rate=1.0
collision_free_rate=1.0
feedback_count=222
system_latency_ms=34007.995
goal_position_error_m=0.010068
actual_displacement_m=3.975084
```

目标 `(2.0, 0.0)`，最终位置约 `(2.000745, -0.010040)`；终态速度约
`linear=0.000091 m/s`、`angular=0.000094 rad/s`。完整证据：

```text
/tmp/fireclaw-demo-readiness-20260821-fixed-env/20260821T111136Z-131518
```

这证明完整 Mission→Robot→Plugin→ROS1 move_base 导航链路在当前机器上可用。该 acceptance harness 使用其
固定 operator authorization 流程，不等同于产品 `fireclaw mission` 的 sealed PlanArtifact HTTP rehearsal；后者
仍需面试前单独跑一次。

### 当前演示就绪度结论

**底层能力已具备，但当前仓库还不是“打开终端立即演示”的状态。**

P0（面试前必须完成）：

1. 建立稳定 demo artifact/runtime：当前没有 companion bundle、active Profile、`FIRECLAW_HOME` 或已安装的
   `fireclaw` 命令。需要构建固定 wheel + bundle，装入独立 demo venv，并在固定 demo home 完成一次
   `setup`/Catkin materialization；不要在面试现场首次构建。
2. 真实彩排产品路径：用生成的 Profile 启动 supervisor，确认 8765/8766、readiness、
   `fireclaw mission` preview/yes/sealed execution 和 Gazebo 最终位姿全部通过，再正常 `fireclaw stop`。
3. 提供可视化：产品 `robot_base.launch` 默认 `gui=false`，只启动 `gzserver`。本机有 `gzclient` 且
   `DISPLAY=:0`；演示前应单独启动 `gzclient`，否则机器人会动但面试官看不到。
4. 修 runner 环境：调整两个 Catkin workspace 的 source/extend 策略或为 acceptance 明确设置
   `GAZEBO_PLUGIN_PATH`，否则官方 rehearsal 命令会在碰撞证据前失败。
5. 固化工作树：阶段 2–4 是 45 个 tracked 修改加多个关键 untracked 文件。至少做审查后的 checkpoint；未获得
   用户授权前不得 stage/commit。

P1（强烈建议，工作量小、明显改善演示）：

1. 为 Mission Planner 增加明确 `navigation` intent（例如 `导航|前往|去坐标|移动到坐标`），映射到
   `navigation` capability，并加成功/歧义测试。这样用户不需要记住“导航测试”暗号。
2. 增加一个 simulation-only demo launcher/checklist，负责检查 runtime、启动 Gateway、报告 8765/8766、
   打开 Web 与 `gzclient`，但不自动确认或下发动作。
3. 更新 README/first-run 文档：当前文字仍称 preview 是 prototype、不是 immutable PlanArtifact，已经落后于阶段 4
   实现；面试材料不能照抄这段旧描述。
4. 预热并记录 startup 时间。最近记录显示重复 `setup` 仍约 175 秒；现场只运行 `start/open/mission`，不要重复
   `setup`。

### 建议的最小演示叙事

1. Web Console 展示 Gateway runtime identity、simulation mode、active Profile 与 Robot readiness；
2. 终端运行 `fireclaw mission`，输入 `导航测试到坐标 (2.0, 0.0)`；
3. 展示 Mission Agent 返回 immutable plan digest、机器人与步骤；
4. 输入 `yes`，说明同一个 sealed plan 被一次性消费，执行端不能重规划或换机器人；
5. `gzclient` 中展示 TurtleBot3 移动，同时终端展示 Mission/Robot/Tool 真实事件；
6. 成功后展示 final status 和记忆/审计记录。

面试 demo 只覆盖 simulation happy path。阶段 5 的物理 StopEvidence 与正式 recovery 尚未闭环，不演示
cancel/recovery，不把 `fireclaw stop` 说成机器人物理停止；Web 中急停或物理停止为 `UNKNOWN` 是安全设计，
不是在线状态失败。不要宣称当前支持“去二楼”或开放域自然语言。

### 本轮文件与外部状态

- 产品源码未修改。
- 新增本记录：`memory/2026-08-21/minimal-navigation-demo-readiness-audit.md`。
- 仅在 `/tmp` 写入两次 Gazebo acceptance evidence；所有 ROS/Gazebo 子进程均由 runner 正常清理。
- 未创建 wheel/bundle、未安装 `fireclaw`、未建立 demo Profile、未 stage/commit/push。

### 下一步建议

下一轮优先做“演示加固包”，范围控制为：

1. 修 acceptance runner workspace/plugin environment；
2. 增加 intuitive navigation intent；
3. 新建固定、可重复的 demo prepare/start/rehearse 文档或脚本；
4. 构建 bundle + demo venv + dedicated `FIRECLAW_HOME`；
5. 跑一次真实产品级 `setup -> start -> fireclaw mission -> move_base -> success -> stop`；
6. 用户审查后再决定是否形成 checkpoint commit。

研究层面：本次只证明工程链路和演示可复现性，不构成新方法贡献。面试中可重点讲 Plugin/Skill/Tool 边界、
Mission/Robot 两级 Agent、sealed operator confirmation、evidence-backed readiness 与 auditable execution；不要把
确定性 intent parser 或单场景成功包装成通用 embodied-agent 研究结论。

## 2026-08-21T23:11:39+08:00 — A2A 适用性核对

### 用户问题

用户追问 Mission Agent 与 Robot Agent 的跨机器通信是否可以采用 Agent2Agent（A2A）协议。

### 已执行检查

- 按 CodeGraph 结构结果复核当前边界：`RobotRegistryEntry`、`StructuredRobotTask`、
  `RobotAgentTaskEnvelope`、Mission 侧 `RobotSubagentClient` 与 Robot Gateway 任务生命周期。
- 运行 literal search：
  `rg -n -i '\bA2A\b|Agent2Agent|agent[-_ ]card|message/send|tasks/get' src tests docs ...`。
  产品源码和测试中未发现 A2A 实现；架构文档明确记录当前使用 hierarchical Gateway transport rather than A2A。
- 查阅 2026-08-21 可用的 A2A 官方规范、Core Concepts 与 Life of a Task。A2A 提供 Agent Card、
  Message/Task/Artifact、长任务状态、查询/取消、streaming/push notification、认证声明与扩展机制；正式规范支持
  JSON-RPC、gRPC、HTTP/REST 等 binding。

### 映射与结论

- A2A 适合放在 `Mission Agent -> Robot Gateway/Robot Agent` 的低频语义任务边界；当前自定义 HTTP/JSON
  `POST /tasks`、`GET /tasks/{id}`、`POST /tasks/{id}/cancel` 与 `/events` 已是 A2A-like 的专用协议。
- `RobotRegistryEntry` 可由/补充为 Agent Card；`StructuredRobotTask` 可由 A2A Message/Data Part 转换；Robot trace
  可映射为 A2A TaskStatus/Artifact；cancel 可映射为 A2A Cancel Task。
- A2A 不能取代 Robot Agent 到 ROS1 `move_base` 的 Tool/adapter/runtime 链，也不能承载高频 LiDAR/图像流、替代
  急停、watchdog、安全门或 FireClaw 的精确一次性物理动作授权。
- 若后续接入，建议给 Robot Gateway 增加 A2A adapter，保留现有内部 `StructuredRobotTask`、SafetyGate 和审计链；
  通过 namespaced required extension 表达 `risk_level`、runtime mode、目标绑定、授权与 physical stop evidence。
- 当前面试最小导航演示不建议临时迁移 A2A：它不会显著改善画面，却会新增协议映射与回归风险。可在架构介绍中
  明确把它列为异构/第三方机器人互操作层。仅“用了 A2A”不是研究创新；安全约束、断网恢复与可验证物理执行扩展
  及系统评测才可能形成研究贡献。

### 文件与外部状态

- 产品代码未修改；未安装 A2A SDK，未启动网络服务。
- 仅追加本 memory 记录；没有 stage/commit/push。

## 2026-08-21T23:21:17+08:00 — 手工四终端演示入口核对

### 用户目标

用户需要一份不安装 `fireclaw` console script、直接使用本地 checkout 与 conda `py310` 的面试演示顺序，明确
ROS launch 和三个 `python -m fireclaw_core` 进程分别在哪个目录与终端运行。

### 本轮核对

- 重新检查 CLI 当前源码与 `--help`：
  - `python -m fireclaw_core robot-gateway --config ...` 启动 Robot Gateway + Robot Agent；
  - `python -m fireclaw_core serve --config ...` 启动 Mission Gateway + Mission Agent；
  - `python -m fireclaw_core mission --server ...` 只启动 operator CLI。
- 检查现有 `fireclaw.toml` 的非秘密运行项：Mission Planner=`llm`、Provider timeout=60s、Mission group=720s、
  navigation=360s、Robot Agent Planner=`llm`、Robot loop=600s。
- 检查 ROS/仿真依赖文件均存在：Noetic setup、Navigation 与 TurtleBot3 两个 Catkin devel setup、contact monitor
  `.so`、`fireclaw_acceptance_world.launch`、根 `fireclaw.toml`。
- 在 conda `py310` 中按“activate conda 后再 source ROS workspaces、最后追加 checkout `PYTHONPATH`”验证
  `rospy`、`actionlib`、`move_base_msgs` 导入成功；`rospack` 可定位 `turtlebot3_gazebo`、
  `turtlebot3_navigation` 和 system `move_base`。沙箱只因 `~/.ros` 只读出现 cache warning，不是宿主依赖缺失。
- 选定面试最小路径为已通过 live acceptance 的组合 launch：
  `extensions/navigation-move-base/launch/fireclaw_acceptance_world.launch gui:=true`。它在一个进程中启动 Gazebo、
  TurtleBot3、传感器/TF、map_server、AMCL 与 move_base，因而总计只需四个终端。显式设置
  `GAZEBO_PLUGIN_PATH=.../extensions/navigation-move-base/ros_ws/devel/lib`，避免此前确认过的 workspace overlay
  覆盖导致 contact monitor 插件找不到。

### 推荐顺序和边界

1. 终端 1（repo root）：source ROS/两个 workspace，设置 TurtleBot3/Gazebo 环境，启动组合 launch（GUI）。
2. 终端 2（repo root）：activate `py310`，再 source ROS/两个 workspace，追加 `PYTHONPATH`，启动 Robot Gateway。
3. 终端 3（repo root）：activate `py310`，追加 `PYTHONPATH`，启动 Mission Gateway。
4. 终端 4（repo root）：activate `py310`，追加 `PYTHONPATH`，检查 8765/8766 health 后启动 Mission CLI；输入明确
   二维任务 `请让 gazebo_turtlebot3 导航到地图坐标 (2.0, 0.0)，朝向 yaw=0.0。`，预览后输入 `yes`。

该组合 launch 是演示/验收便利入口；正式产品架构仍把 robot-owned base bringup 与 Navigation Plugin-owned
navigation launch 作为独立边界，由生成的 unified launch/supervisor 组合。不能把 `fireclaw stop` 用于这套手工
进程；应依次退出 CLI、Ctrl-C Mission Gateway、Ctrl-C Robot Gateway、最后 Ctrl-C ROS launch。

### 安全提示与状态

- 根 `fireclaw.toml` 当前含 Provider credential；演示时不要打开或投屏该文件，后续应轮换并迁移到
  `FIRECLAW_PROVIDER_API_KEY`。本记录未复制秘密值。
- 本轮未启动 ROS、Gateway 或外部 LLM 请求；只执行 help/import/package-resolution 检查。
- 产品代码未修改；仅追加本 memory；未 stage/commit/push。

## 2026-08-21T23:35:37+08:00 — Robot/Plugin/acceptance launch 所有权澄清

### 用户纠正

用户指出上一版四终端指引直接运行
`extensions/navigation-move-base/launch/fireclaw_acceptance_world.launch`，容易表现成用户需要在 Navigation Plugin
中启动甚至修改不同机器人的底层配置；质疑为什么机器人启动入口不在 `robots/`。

### 代码事实与结论

- 用户质疑成立；上一版命令是为了压缩终端数量选择的已验收快捷入口，但没有充分区分测试夹具与产品入口。
- `robots/turtlebot3_burger/.../fireclaw_turtlebot3_burger/launch/robot_base.launch` 的源码注释明确声明
  robot-owned base bringup，只负责仿真硬件、URDF、传感器、里程计和 TF，并故意不启动 map_server、AMCL、
  move_base。
- `extensions/navigation-move-base/launch/fireclaw_navigation.launch` 的源码注释明确声明 Plugin-owned ROS1
  Navigation composition；其 robot/map-specific 值只能由可信 deployment Profile 注入。它拥有通用 map_server、
  AMCL、move_base 算法组合，不拥有具体机器人底盘。
- `extensions/navigation-move-base/launch/fireclaw_acceptance_world.launch` 明确标记为 trusted fixed TurtleBot3
  acceptance-test bringup，由 operator-side test runner 使用；它不是实机启动入口，也不应成为普通用户修改面。
- `deployer._write_unified_launch()` 已实现正确的组合层：先 include Profile 指定的 robot launch，再 include 每个
  Plugin runtime descriptor 声明的 launch，并注入由 Profile 验证的 bindings。

### 正式部署模型

普通操作员不应编辑 Plugin 或每次修改机器人底层配置。新增机器人应由集成人员一次性完成：

1. 在 `robots/<robot-family>/`（或厂商已有 ROS package）提供 robot-owned driver/base bringup；
2. 在机器人/deployment Profile 声明 ROS setup、robot launch、adapter、topics、frames、footprint、运动/传感器约束；
3. 选择兼容的 Navigation Plugin；
4. FireClaw deploy/setup 生成统一 launch 和 wrapper；机器人启动时运行生成物/系统服务及 Robot Gateway。

实机不能运行 Gazebo acceptance launch。实机底盘与传感器由 robot-owned bringup 或厂商 systemd/ROS launch 启动；
Navigation Plugin 保持不改，通过 Profile bindings 对接。若每接一台机器人都需要改
`extensions/navigation-move-base/` 源码，说明抽象边界失败。

### UX/目录判断

把 Plugin-owned 通用 navigation launch 放在 extension 中是合理的，因为 Plugin 本来可以携带 Runtime、Adapter、
配置、launch 和测试。令人突兀的是 acceptance world 目前位于生产感较强的 `launch/` 下，并被演示指引直接引用。
更清楚的后续整理是把 acceptance fixture 放到 `tests/acceptance/launch/`，并在 `examples/launch/` 或受管 deployment
输出中提供明确命名的 demo composition；不能简单把组合测试 launch 移进 `robots/`，否则又会让 robot package
拥有 Plugin runtime。

### 状态

- 本轮仅做架构核对和说明，没有修改产品代码、launch 或配置。
- 仅追加本 memory；未启动 ROS/Gateway，未 stage/commit/push。

## 2026-08-21T23:48:16+08:00 — Robot Gateway 仿真域启动拒绝诊断

### 用户现场错误

用户在 `/home/lpp/miniconda3/envs/py310` 中运行本地源码入口：

```text
python -m fireclaw_core robot-gateway --config /home/lpp/fireclaw-master/fireclaw.toml
```

Gateway 在构造期被 `validate_robot_deployment_binding()` 拒绝：simulation deployment + live adapter execution
要求 `robot_gateway.embodied_runtime_mode='simulation'`。

### 根因

- 当前根 `fireclaw.toml` 声明 `[deployment].mode="simulation"`、`[robot_gateway].dry_run=false`；Robot Profile
  使用 `adapter="ros1"`。
- `[robot_gateway]` 未配置 `embodied_runtime_mode`、`embodied_memory_path` 和 `embodied_memory_index`。
- 精确校验条件位于 `policy/deployment.py:616-620`：role 为 robot_agent，deployment mode 为 simulation，
  `dry_run` 为 false，且 embodied mode 不是 simulation 时 fail closed。
- ROS1 adapter 本身无法证明 `ROS_MASTER_URI` 后面是 Gazebo 还是真机；该二次显式声明用于防止 simulation Tool
  policy 静默连接真实硬件。这不是 ROS/LLM 错误。
- 只增加 `--embodied-runtime-mode simulation` 会紧接着触发 Gateway 的
  `embodied_runtime_mode requires embodied_memory_path`；因此应同时提供 evidence JSONL 和可重建 SQLite index。

### 即时与持久修正

保持 `dry_run=false`、实际向仿真 `/move_base` 下发 goal 的推荐即时命令，应追加：

```text
--embodied-runtime-mode simulation
--embodied-memory-path /home/lpp/fireclaw-master/data/robots/gazebo_turtlebot3/embodied-memory.jsonl
--embodied-memory-index /home/lpp/fireclaw-master/data/robots/gazebo_turtlebot3/embodied-memory-index.sqlite3
```

对应持久配置应放进 `[robot_gateway]` 同名三个字段。目录
`data/robots/gazebo_turtlebot3/` 已存在，现有普通 `memory.jsonl`、events/tasks 与 runtime SQLite 不应覆盖；采用新的
embodied 文件名隔离 evidence 和 projection。

另一种兼容 setup template 的 simulation 配置是 `dry_run=true`；当前 Physical Plugin handler 仍会调用 Gazebo
`/move_base`，但审计事件会标为 dry-run/simulation authority。为了当前用户希望明确展示 live ROS simulation 和
Robot embodied memory，本轮推荐保留 false 并补齐显式 domain + memory paths。

### 环境注意

用户实际 interpreter 是 `/home/lpp/miniconda3/envs/py310`，而先前本机验收使用
`/srv/lpp-extra/miniconda3/envs/py310`。本次 traceback 已成功导入 FireClaw，故与当前校验错误无关；继续前仍应在
activate conda 后 source ROS workspaces，并运行 `python -c 'import rospy, actionlib, move_base_msgs.msg'` 验证该
解释器的 ROS Python 可见性。

### 状态

- 未修改用户 `fireclaw.toml` 或产品代码；只提供诊断和修正命令。
- 未启动 Gateway/ROS，未 stage/commit/push；仅追加本 memory。

## 2026-08-21 21:30 +08:00 更新：本地源码双 LLM 产品链路实测

### 用户决定与更正

- 用户明确要求演示 **LLM 整体规划**，不能用 deterministic Mission Planner；Robot Agent 也必须由 LLM 选择 Tool。
- 用户明确不要安装 `fireclaw` 命令、wheel、bundle 或新环境；直接运行当前仓库源码。
- 因此上文“必须先安装 `fireclaw`/构建 wheel + bundle”的 P0 建议不再适用于本次面试 demo。当前源码入口已验证可用：

  ```bash
  PYTHONPATH=src:. /srv/lpp-extra/miniconda3/envs/py310/bin/python -m fireclaw_core ...
  ```

### LLM 与配置实测

1. 当前 `[provider]` 的 OpenAI-compatible endpoint 可访问；鉴权 `/models` 返回 200，`mimo-v2.5-pro` 存在，最小推理成功。
   未在日志或文件中复制/输出 API key。
2. Mission Agent 真实 LLM 调用成功，把中文坐标巡逻命令解析为 `patrol` subtask 和 typed target pose。
3. Robot Agent 真实 LLM 隔离调用成功，返回：

   ```json
   {
     "operation": "execute_skill",
     "tool_name": "navigate_to_point",
     "inputs": {"x": 2.0, "y": 0.0, "yaw": 0.0, "frame_id": "map"}
   }
   ```

4. 首次完整 Robot 调用在 Provider 请求前被 context manager fail-closed：安全关键上下文和 schema 需要 6243 tokens，
   默认 model descriptor 只提供 5888 input tokens（8192 context - output reserve - safety margin）。
5. 新增 `examples/model_catalogs/mimo-v2.5-pro.json`，本地 descriptor 使用 `context_window=32768`、
   `max_tokens=4096`；这只修正 FireClaw 本地预算元数据，不安装模型。
6. 本地 `fireclaw.toml` 已调整为本次 demo profile：Mission planner=`llm`、deployment=`simulation`、
   plugin path 仅 `extensions/navigation-move-base`、Robot Gateway `dry_run=false`，并引用上述 catalog。

### 完整成功链路

成功命令：

```text
让 gazebo_turtlebot3 前往地图坐标 (1.8, -0.1)，保持朝向 yaw=-2.34 弧度并执行巡逻
```

关键证据：

- Mission ID：`mission-b2f556833ce246279838b6e5c39daec9`
- sealed plan digest：`sha256:b54e2eb3802171303e7d0cf60fadae4a3cd38aab7923bab923a31a4fcd352147`
- Robot task：`task-c1268bedc5a742f5891fd80986e4c5d8`
- Mission LLM 生成 `patrol` plan，目标 `(1.8, -0.1, yaw=-2.34)`；CLI 展示 preview/digest/robot/risk 并由操作员输入 `yes`。
- Robot LLM 第 1 轮选择 `navigate_to_point`；该轮总耗时 `28493.15 ms`，其中 move_base 导航约 `7.15 s`，
  observation=`succeeded`、goal_state=`succeeded`。
- Robot LLM 第 2 轮读取成功 observation 后返回 `complete`，耗时 `16526.164 ms`。
- Robot task：`completed`；execution：`succeeded`；Robot deliberation：`completed`。
- Mission Gateway `/missions/<id>/run`：`status=completed`、`terminal=true`；sealed mission execution：`succeeded`。
- Gazebo `/gazebo/get_model_state` 最终 world pose：`x=1.8022001550`、`y=-0.0363178246`，机器人近似静止。

这证明的链路是：

```text
operator natural language
  -> Mission Agent LLM plan
  -> sealed plan + human confirmation
  -> Robot Agent LLM Tool selection
  -> Capability/Safety policy
  -> Navigation Plugin navigate_to_point
  -> ROS1 move_base
  -> Robot Agent LLM completion judgment
  -> Mission terminal success
```

move_base 的局部/全局控制算法仍然是确定性 runtime；“双 LLM 演示”指两个 Agent 层都由 LLM 推理，而不是让 LLM
直接输出电机控制，这正是正确的安全边界。

### 实测发现的面试前缺口

P0（最小 demo 必须规避或修复）：

1. **Robot Agent 30 秒默认上限过短且无 config/CLI 入口。** 一次成功流程包含两次约 13–16 秒 LLM 调用以及物理导航，
   即使短距离也天然超过 30 秒。成功彩排在进程内把 `RobotAgentDeliberationLimits.timeout_seconds` 临时设为 180。
2. **sealed plan 确认没有自动转成 Robot Gateway exact-action authorization。** 首次 `yes` 后任务停在
   `awaiting_confirmation`；本次通过 Robot Gateway `POST /confirm` 对同一个 session 做第二次确认后恢复。面试前应把
   该 pending authorization 明确显示并由 Mission CLI/Web Console relay，不能暗中绕过 SafetyGate。
3. **`SqliteMemoryIndex` 跨 Gateway worker thread 复用 SQLite connection。** 原始进程反复报
   `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread`。成功彩排只在进程内
   使用 per-thread SQLite connections；正式修复需要线程安全连接生命周期和并发测试。

P1（强烈建议）：

1. Robot LLM authoritative context 包含所有 ROS sensor discovery rejected findings，payload 极大；应保留 verified sensors、
   runtime fingerprint 和必要拒绝摘要，避免完整复制几十个无关 topic finding。
2. Mission CLI 的 SSE 曾提示从 cursor 自动重连，但没有打印已经完成的最终事件；Mission API 实际状态为 completed。
   面试前修复或至少准备一个精简的 `mission run status` 查询。
3. 正常 `serve` 路径不会解析 `provider_api_key_env`，而是要求 literal `provider_api_key`；当前本地配置能跑，但不适合作为
   长期 secret-management 方案。
4. Navigation 目标要选已知可达、姿态差较小的位置。第一次 `(2.3,0,yaw=0)` 因大角度姿态调整在 move_base 120 秒后
   preempt，记录为 `physical_action_lost`；第二次沿当前朝向移动约 0.3 m 成功。

### 本轮文件与临时状态

- 新增：`examples/model_catalogs/mimo-v2.5-pro.json`。
- 修改本地 demo 配置：`fireclaw.toml`（可能被 ignore；未输出 secret）。
- 未修改 `src/` 产品源码，未安装 `fireclaw`，未安装依赖，未 commit/stage/push。
- 成功彩排使用的进程级临时 runner：`/tmp/fireclaw_robot_gateway_demo.py`；临时状态：
  `/tmp/fireclaw-demo-v11/`。该 runner 的 180 秒和 per-thread SQLite override 不是正式产品修复。
- OpenClaw analogue：本轮没有新增产品模块/API；只复用现有 FireClaw Mission/Robot Agent、sealed plan、Tool runtime、
  session 和 safety boundaries，未引入替代架构。

### 下一步最小范围

只需做三个小而明确的产品加固项，就能把本次成功彩排变成面试现场的一次流畅交互：

1. 给 Robot Agent limits 增加 typed config + CLI wiring，demo 设置 180 秒；
2. 在 Mission CLI/Web Console 显示并 relay Robot exact-action confirmation；
3. 修复 `SqliteMemoryIndex` 的线程安全连接管理，并加一个双 LLM + live Gazebo 回归。

不需要先安装 `fireclaw` 命令。

### 2026-08-21 21:34 +08:00 收尾

- `mimo-v2.5-pro` catalog 已由 `ModelCatalog` 成功加载：`32768 / 4096 / supports_tools=true`。
- 本轮启动的 Mission CLI、Mission Gateway、Robot Gateway 和 ROS/Gazebo 均已停止；8765/8766 和 ROS demo
  不再由本轮进程占用。

## 2026-08-21 22:23 +08:00 更新：面试导航链路产品加固完成

### 当前进展

- 用户决定直接使用 conda `py310` 中已有的依赖运行本地源码，不安装 wheel，也不要求系统中存在 `fireclaw` 命令。
- 双 LLM 演示所需的超时分层、Mission `yes` 到 Robot 精确动作授权的中继，以及 SQLite memory index 跨线程连接问题均已形成正式产品代码，不再依赖 `/tmp` runner monkeypatch。
- 最终全仓测试：`2392 passed, 8 skipped, 0 failed`，耗时 `298.97s`。

### 已完成

1. 超时分层已明确并开放配置：

   - Mission/Robot 单次 LLM provider request：`60s`；这是单次模型 HTTP 请求的预算，不是导航时间。
   - operator Mission HTTP client：`75s`；使一次最多 60 秒的模型请求不会先被客户端截断。
   - Navigation Plugin physical Tool：默认 `360s`；ROS1 `move_base` result wait：默认 `360s`。这是一次导航动作等待终态的时间。
   - Plugin SDK 只把 physical Tool 上限扩为 `1800s`；普通非物理 Tool 仍保持 `300s` 上限。
   - Robot Agent 完整 LLM/Tool loop：`600s`；包含多轮模型请求及物理 Tool。
   - Mission execution group：`720s`；保持大于 Robot Agent 外层预算，不会在 Robot 仍正常执行时先投影为 Mission timeout。

   相关配置已经落到 `fireclaw.toml`、两个 Gazebo setup template、两个 ROS1 example config 和 Navigation Plugin manifest/default。新增 CLI 参数包括：

   - Mission：`--provider-timeout-seconds`、`--mission-group-timeout-seconds`
   - Robot：`--robot-agent-provider-timeout-seconds`、`--robot-agent-loop-timeout-seconds`

2. sealed Mission plan 的 `yes` 会安全地中继到 Robot：

   - 仅 `MissionAgent.execute_sealed_plan` 开启中继；普通未确认计划不能使用该路径。
   - Scheduler 只观察实际返回 `awaiting_confirmation` 的已分派 Robot task，并使用精确 `(robot_id, robot task_id, mission_id)` 调用 Robot `/confirm`。
   - Robot Gateway 按具体 `task_id` 查询 pending authorization，同时再次验证 session/task binding；同一 session 下另一个待确认任务不会被批准。
   - Robot 端原有 protected inputs 校验、SafetyGate 和 signed one-shot action authorization 均保留；该改动不是绕过 Robot 安全门。
   - 保留旧的 session-only `/confirm` 兼容路径；HTTP 集成回归发现并修复了新增解析调用的 `_optional_string` 拼写遗漏。

3. `SqliteMemoryIndex` 跨线程问题已修复：

   - 每个 Gateway/worker thread 使用自己的默认 thread-affine SQLite connection，不再把创建于主线程的 connection 交给 worker。
   - 共享数据库继续使用 WAL，并配置 `busy_timeout=5000ms`；schema 初始化使用进程内 lock 串行化。
   - 增加 calling-thread `close()` 和跨线程写入/查询回归。
   - 首次访问 `rtree_available` 会触发惰性 schema 初始化；完整测试发现并修复了初始布尔值导致 RTree 误报不可用的问题。

4. 导航配置变更后的可复现发布元数据已同步：

   - `frozen-suite.yaml` 的 ROS1 config SHA-256 更新为 `744bfe093df42033c63314fe36b045806d58ac819f32c440df214b02e8f69105`。
   - 重新确定性构建 652-file simulation bundle；catalog SHA-256 更新为 `679e60dd362acfb9a15af899a445c9525abb12790c67de5d1315bc7c492c99e5`。

### OpenClaw analogue 与 FireClaw 适配

- Agent/Tool 取消与超时结构检查了 `openclaw/src/agents/agent-command.ts`、`openclaw/src/agents/agent-tools.ts` 和 `wrapToolWithAbortSignal` 路径；沿用“上层 agent lifecycle + 下层 tool cancellation”的分层思路。FireClaw 额外保留物理导航 deadline、ROS cancellation acknowledgement 和 lost outcome，因为机器人动作不能只靠模型请求超时处理。
- Memory SQLite 结构检查了 `openclaw/extensions/memory-core/src/memory/manager-db.ts` 的 `openMemoryDatabaseAtPath`、WAL maintenance、`busyTimeoutMs=5000` 和显式 close。FireClaw 使用 Python `sqlite3`，因此适配为 per-thread connection，而不是共享一个 thread-affine connection。
- CodeGraph 对显式 OpenClaw 路径的探索曾错误返回同名 FireClaw symbols，因此随后只对已明确的 upstream 文件做了本地只读检查；没有修改 `openclaw/`。

### 验证命令与结果

- 本地解释器：`/home/lpp/miniconda3/envs/py310/bin/python`。`conda run -n py310` 因 conda 要在只读 env 目录创建临时文件而失败；直接调用该解释器成功，不是依赖缺失。
- `python -m compileall -q src/fireclaw_core src/fireclaw_plugin_sdk extensions/navigation-move-base/plugin tests`：通过。
- timeout/config/navigation/memory/authorization 首轮：`105 passed`。
- Robot loop/provider/Mission runtime/embodied memory：`87 passed`。
- preview -> yes -> sealed plan -> Mission/Web/CLI/Gateway：`204 passed`。
- authorization/SafetyGate/action runtime/ROS navigation：`162 passed, 7 skipped`。
- 第一次全仓回归为 `2387 passed, 8 skipped, 5 failed`；5 项分别是 RTree lazy init、frozen ROS config hash 和 3 个 stale simulation bundle catalog lifecycle tests。修复后定点重跑 `5 passed`。
- 最终全仓回归：`2392 passed, 8 skipped in 298.97s`。
- `git diff --check`：通过。
- 使用 `PYTHONPATH=/home/lpp/fireclaw-master/src` 的 `serve --help`、`robot-gateway --help`、`mission --help`：全部通过，证明无需安装 console script 即可使用本地入口。

### 当前问题

- 本轮没有在修改后再次启动 live Gazebo；修改前已有真实双 LLM + live `move_base` 成功证据，本轮用全仓及导航专项回归验证正式修复。面试前仍应按相同目标做一次最终彩排，以确认外部 ROS/Gazebo/LLM 服务当时可用。
- 8 项测试按外部运行环境条件跳过；这不影响本次本地源码、Gateway、Mission/Robot、SafetyGate 和 navigation adapter 单元/集成回归结论。
- 工作区原本包含大量未提交的用户改动；本轮没有覆盖、reset、stage、commit 或 push 它们。

### 下一步

1. 先启动已准备好的 Gazebo/ROS `move_base` 仿真。
2. 分三个终端直接运行下列本地源码命令。
3. 使用上一轮成功目标 `(1.8, -0.1, yaw=-2.34)` 完成一次 preview -> `yes` -> Robot LLM navigation -> Mission success 彩排。
4. 检查演示结束后 8765/8766、ROS/Gazebo 进程是否按预期停止。

### 需要运行的命令

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python -m fireclaw_core serve --config fireclaw.toml
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python -m fireclaw_core mission --server http://127.0.0.1:8766
```

交互命令继续使用：

```text
让 gazebo_turtlebot3 前往地图坐标 (1.8, -0.1)，保持朝向 yaw=-2.34 弧度并执行巡逻
```

看到 immutable plan preview、digest、robot 和 risk 后输入 `yes`。现在不应再需要对 Robot Gateway 做第二次手工 `/confirm`。
