# FireClaw

FireClaw 是面向消防机器人的 embodied-agent 框架。它借鉴 OpenClaw 的
Agent、Session、Plugin、Skill、Tool、Gateway 和本地持久化边界，并为机器人
增加物理安全、状态不确定性、执行监控、取消/超时、恢复与审计证据。

当前代码不是认证过的消防控制系统。任何真实机器人、传感器、急停链路和物理
Tool 都必须经过具体平台与现场验证。

## 当前架构

```text
operator
  -> Mission Coordinator
  -> StructuredRobotTask
  -> Robot Gateway / Robot Agent
  -> capability policy + SafetyGate
  -> Plugin-owned typed Tool
  -> Plugin-owned Adapter
  -> ROS / simulator / SDK Runtime
  -> feedback + terminal result + audit evidence
```

核心边界如下：

- `MissionAgent` 负责理解任务、选择机器人、生成和修订任务图；它不直接调用 ROS；
- `FireClawGateway` 负责会话、任务队列、插件生命周期、授权和事件流；
- `FireClawAgent` 负责本机规划/推理、安全门控和执行监控；
- `RobotAdapter` 只提供机器人状态、传感器状态和急停等核心可信边界；
- Plugin 拥有具体领域 Tool、Adapter 和 Runtime 绑定；
- Skill 是 Agent 面向能力区域的 `SKILL.md` 工作流，不是原子函数；
- Tool 是带类型 schema 的原子调用；
- Runtime/Algorithm 是 ROS `move_base`、Nav2、感知节点或机器人 SDK 等实际实现。

权威术语见
[`docs/architecture/plugin-skill-tool-terminology.md`](docs/architecture/plugin-skill-tool-terminology.md)。

## 插件隔离保证

FireClaw core 不按导航、感知或执行器名称进行 dispatch。新增普通 Tool Plugin
不需要修改 `FireClawAgent`、`FireClawGateway` 或 `RobotAdapter`：

1. Plugin 在 `fireclaw.plugin.json` 中声明身份、入口和附带的 Skill；
2. 入口通过 `PluginApi` 注册 typed Tool、physical Tool、service 或 hook；
3. Plugin Host 记录 owner、冲突、激活、回滚和 disposal；
4. Robot Agent 从 Plugin Host 投影可用 Tool，并经过通用 policy、SafetyGate、
   授权、资源锁和审计路径；
5. Plugin 自己把 Tool 调用翻译到 ROS/SDK/算法 Runtime。

如果新增的是一种新的 Mission 任务语义，而不只是一个原子 Tool，还需要提供或
配置相应的 intent/Skill 工作流、capability chain、completion contract 和安全
规则。这些通过可注入的 planner/profile/registry 配置完成，不应在 core 中新增
Tool 名称分支。

仓库已移除旧的 `*.skill.json` 可执行 manifest loader、workspace executable
Tool loader，以及 Adapter 上的领域动作回退。插件必须通过当前 Plugin API
显式注册；未知或未加载 Tool 会 fail closed。

## 当前导航插件

[`extensions/navigation-move-base`](extensions/navigation-move-base) 是当前导航
Plugin，owner ID 为 `fireclaw.navigation.move-base`。它包含：

- Navigation Skill：`skills/navigation/SKILL.md`；
- physical Tool：`navigate_to_point`；
- Agent Tools：`move_base_parameter_catalog`、`move_base_navigation_status`、
  `move_base_get_parameters`、`move_base_set_parameters`、
  `move_base_cancel_navigation`、`move_base_clear_costmaps`；
- Plugin-owned `Ros1MoveBaseBackend` 与仿真用 `InMemoryMoveBaseBackend`；
- vendored ROS1 navigation workspace 和相关配置。

ROS1 diagnostics 由独立的
[`extensions/ros1-diagnostics`](extensions/ros1-diagnostics) Plugin 提供，包括
`navigation_diagnostics`。Gateway 只注入一个通用 service bag；它不知道任何
导航 backend key 或 `/move_base` endpoint。

`DryRunRobotAdapter`、`SimulatorRobotAdapter` 和 `Ros1RobotAdapter` 都没有
`navigate_to_point` 方法。物理导航只能由导航 Plugin 注册的 handler 执行。

## 当前空间范围

当前 acceptance 范围是单楼层二维地图：

- 目标使用绝对 `map` 坐标；
- Tool 合同为 `navigate_to_point(x, y, yaw=0.0, frame_id="map")`；
- 当前不声称支持电梯、楼梯、跨层定位、地图切换或跨楼层路径规划。

详见
[`docs/architecture/fireclaw-spatial-scope.md`](docs/architecture/fireclaw-spatial-scope.md)。

## 仓库布局

```text
src/fireclaw_core/          FireClaw core：Agent、Gateway、policy、memory、mission
extensions/                 Plugin、Adapter、算法、ROS workspace、Plugin-owned tests
skills/                     仓库级 Agent 工作流（SKILL.md）
examples/                   deployment profile 与 core Adapter 示例配置
tests/                      core 与集成测试
docs/                       架构、部署与研究记录
memory/YYYY-MM-DD/          跨会话执行记录
openclaw/                   本地 OpenClaw 架构参考，不是运行时依赖
```

## 安装

需要 Python 3.10 或更新版本：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

## 快速入门与核心命令

当前基线提供 5 个独立的生命周期入口。它们便于在完整源码仓库中验证安装与运维边界，但尚未组成
wheel 安装后的“一条命令首次任务”闭环，也不代表已经实现零手写配置：

```bash
# 1. 首次准备（生成 TurtleBot3 Gazebo 仿真 Profile 并准备 Runtime release）
fireclaw setup

# 2. 启动后台守护进程（Supervisor 与 Gateway）
fireclaw start

# 3. 查看运行健康度、服务状态与 readiness 快照
fireclaw status

# 4. 打开 Web Console 仪表盘（可加 --no-browser 仅打印 URL）
fireclaw open

# 5. 停止后台守护进程与其托管服务（不等于确认机器人已物理停止）
fireclaw stop
```

`setup` 会生成无凭据的 TurtleBot3 Gazebo Profile、验证 ROS/Navigation Plugin、准备内容寻址 Runtime release，
并记住当前 Profile；不会启动 Gazebo、Gateway 或任何机器人动作。当前实现仍要求完整 FireClaw 源码仓库和
仓库内 ROS workspace，不能据此声称普通 wheel 安装后即可完成首次仿真任务。后续命令可省略
`--profile`；重复运行会复用 fingerprint 相同的 release，不覆盖已有 Profile。实机模式不会自动生成
配置、部署或运动，只接受显式提供且已经人工审查的 real Profile。`stop` 只证明守护进程退出；机器人
物理状态在没有 Adapter/硬件证据时仍为 `UNKNOWN`。详细边界见
[`docs/getting-started/first-run-setup.md`](docs/getting-started/first-run-setup.md)。

## Web Console 运维控制台

FireClaw 提供了专为应急救援场景打造的轻量化本地运维控制台 (Rescue Ops Dark 主题)，启动守护进程后通过 `fireclaw open` 即可在浏览器中访问（控制台地址为 `http://127.0.0.1:8766/console`）：

控制台是 Gateway 状态的投影，不是独立的硬件事实来源。页面在收到 readiness、急停或停止证据前显示 `UNKNOWN`；`ready` 只表示当前任务准入条件满足，不等同于机器人处于物理静止。

1. **首页概览 (Overview)**：投影 Mission Gateway 当前提供的 readiness 与 fleet 字段。Gateway 尚未提供
   active Profile、权威 deployment mode 与完整物理证据合同，因此缺失字段显示 `UNKNOWN`，不能把页面
   当作机器人本体事实来源。
2. **任务预览 (Task Preview Prototype)**：可显示自然语言解析结果并要求 Web 操作员显式点击确认；当前
   preview 不是不可变 plan artifact，`/tasks` 也尚未保证消费同一计划，因此不能声称“预览即执行合同”。
3. **执行事件与取消投影**：通过 Gateway SSE 显示事件，并区分 `cancel_requested`、权威
   `robot.stopping` 与带 `stop_evidence` 的 `stopped_confirmed`。HTTP `accepted` 只显示为请求已接受，
   不再伪造 `RUNNING`、Tool 或运动进度；默认 Adapter 未提供停止证据时第三态不会出现。
4. **恢复请求原型**：页面能显示 Gateway 阻断摘要并提交带操作员确认的 Mission Gateway 准入投影重置
   请求；这不是 Robot Gateway 的正式 request/confirm/TTL 两阶段恢复，也不证明机器人物理安全。
5. **配置助手原型**：模板、core Schema、发现、diff、内容快照与回滚组件可供开发验证，但尚未统一到
   Plugin manifest 权威 Schema，也未形成启动可用、原子保存、凭据隔离和 known-good 回滚合同。Web 在
   Gateway 未提供 active Profile 时禁止猜测保存/回滚目标。
6. **4 段式友好错误**：保留错误原因、安全证据、处置回执和下一步四段结构。当前没有 typed evidence/
   action receipt 接口，因此安全证据固定显示 `UNKNOWN`，处置栏明确表示没有可验证回执；推荐按钮只是
   建议，不能假定对应动作已经注册或执行。

## 配置辅助 CLI（实验性）

FireClaw 提供 `fireclaw profile` 原型工具链用于模板、发现、diff 和内容快照验证。当前生成结果必须人工
审查并通过完整 deployment/Profile 校验，不能直接视为可启动或实机安全配置：

```bash
# 1. 查看 5 类内置预设机器人能力模板
fireclaw profile list-templates

# 2. 探测 ROS 1 计算图并输出推荐话题映射
fireclaw profile discover --master-uri http://127.0.0.1:11311

# 3. 对比模板与当前配置文件差异，评估物理影响级别 (CRITICAL/WARNING/INFO)
fireclaw profile diff --from gazebo_turtlebot3_burger --to profiles/active.toml

# 4. 查看配置历史快照版本记录
fireclaw profile history --profile profiles/active.toml

# 5. 回滚至指定历史快照
fireclaw profile rollback --snapshot <SNAPSHOT_ID> --profile profiles/active.toml
```

## 友好错误提示与操作员引导 (Friendly Errors)

FireClaw 提供分级结构化错误提示，并保留可展开的技术详情。该机制改善呈现，但不会把静态模板当作
机器人物理状态或自动处置证据：

### 1. 标准化 4 段式中文结构

已接入该机制的异常会格式化为以下 4 段信息：

1. ❶ **发生了什么 (What Happened)**: 简明扼要地解释异常原因与涉及的主题/节点/文件；
2. ❷ **机器人安全证据 (Robot Safety Evidence)**：当前统一显示 `UNKNOWN`；在后续 typed evidence 合同
   完成前，不从错误类型推断机器人已经停止、安全或急停已释放；
3. ❸ **自动处置回执 (Action Receipt)**：当前明确显示没有可验证回执，不把注册表中的建议性文案当作
   已执行动作；
4. ❹ **建议下一步 (Next Steps)**: 提供操作员现场处置建议与恢复指引（如检查物理接线、重试指令或提交安全审批）。

### 2. 多终端交互与渐进披露

- **CLI 命令行终端**:
  - **默认模式**: 输出带框线与状态图标的 4 段中文提示盒；
  - **调试模式 (`--verbose`)**: 在框体底部展开完整底层技术堆栈与异常详情 (`technical_details`)；
  - **自动化模式 (`--json`)**: 输出标准化的机器可读 JSON，包含全部 4 段结构与元数据。
- **Web Console 运维控制台**:
  - **CRITICAL 严重错误**: 弹出全屏 4 段式模态框和建议项，并附带可折叠的**查看技术详情**
    (`<details>`) 面板；建议项不代表存在可执行 handler；
  - **WARNING 警告**: 弹出双行增强型 Toast 提示（含错误摘要与机器人安全状态副标题），附带 `查看详情 →` 快速直达模态框，停留时长扩展至 8 秒；
  - **INFO 提示**: 右下角弹出轻量化紧凑 Toast 通知。

### 3. 错误码注册表与 CLI 查询

系统内置覆盖 8 大分类（传感器、ROS 通信、网关网络、安全门授权、任务执行、配置管理、基础设施、机器人状态）共 40+ 条预设错误码。可通过命令行快速查询：

```bash
# 1. 列出所有注册的友好错误码及其处置模板
fireclaw errors list

# 2. 按严重度或分类过滤错误码
fireclaw errors list --severity critical
fireclaw errors list --category sensor

# 3. 查看特定错误码的 4 段式结构与推荐操作
fireclaw errors get sensor_no_lidar_data

# 4. 以 JSON 格式输出错误注册表定义
fireclaw errors list --json
```


## 开发者验证

运行全量测试：

```bash
.venv/bin/python -m pytest -q
```

运行单机器人点导航 demo：

```bash
.venv/bin/python -m fireclaw_core.devtools.demo
```

运行本机 doctor：

```bash
.venv/bin/python -m fireclaw_core.devtools.doctor \
  --adapter mock-ros1 \
  --robot-id doctor-demo \
  --memory-path /tmp/fireclaw-doctor-memory.jsonl \
  --event-path /tmp/fireclaw-doctor-events.jsonl
```

部署后的操作员 readiness、Fleet Doctor 与安全冻结恢复使用统一入口：

```bash
fireclaw deploy apply --profile fireclaw.toml
fireclaw deploy service install --profile fireclaw.toml --no-enable --no-start
fireclaw deploy service start --profile fireclaw.toml
fireclaw status --profile fireclaw.toml
fireclaw recover --profile fireclaw.toml
fireclaw deploy service stop --profile fireclaw.toml
```

默认输出面向人类；自动化可增加 `--json`。`status` 在一个快照中聚合部署/ROS、Robot Gateway、
安全冻结、托管服务与 Fleet Doctor，不会把 Gateway 进程存活误报为 Runtime 或机器人 ready；
`doctor` 仍保留为只查看 Fleet Doctor 的详细入口。`recover` 也没有绕过完整操作员确认短语的快捷
参数。部署后的日常主入口也可直接
使用 `<output-root>/<deployment-id>/current/bin/fireclaw-runtime` 做前台调试：它与 systemd 入口共用
同一 supervisor，负责 bringup、Robot Gateway、Mission Gateway 的顺序启动、持续 readiness、
孤儿进程排空、逆序停止与生命周期日志；real mode 的异常退出不会自动重启，并会保持或建立资源
准入冻结。服务安装是显式动作，`deploy apply` 不会静默修改 systemd。

实机冻结恢复由默认关闭的 `fireclaw.safety.ros1-hardware` Plugin 提供。它不暴露 LLM Tool；只有
厂商硬件 stop 获得确认，并同时取得 watchdog、物理急停、driver disable、制动策略、完整执行器
清单和独立 odometry 的连续静止证据时，Gateway 才接受 `hardware_stop_v1` 报告。配置模板见
`examples/deployment_profiles/navigation_robot.toml.example`；逻辑解冻不会清除物理急停或续跑
旧任务。无真机时可先完成离线准备；现场使用以下闭环命令：

```bash
fireclaw hardware-safety preflight --profile fireclaw.toml --offline
fireclaw hardware-safety preflight --profile fireclaw.toml
fireclaw hardware-safety accept --profile fireclaw.toml --scenario stop_proof \
  --operator-id operator-01 --firmware-version vendor-fw-1.2.3
fireclaw hardware-safety report --profile fireclaw.toml \
  --firmware-version vendor-fw-1.2.3 --artifact-dir results/hardware-safety
```

详细填写项、非致动/致动边界和必须逐项验证的故障场景见
[`docs/deployment/real-robot-hardware-safety-acceptance.md`](docs/deployment/real-robot-hardware-safety-acceptance.md)。

无真机时也可以完成软件/进程层故障矩阵。下面的命令会验证断网续播、Gateway 崩溃、私有
`roscore` 重启、SQLite 满页、数据库锁、传感器失效和重复命令，并生成不可覆盖、可校验的证据：

```bash
fireclaw fault-test run --live-ros
fireclaw fault-test verify --run-dir results/fault-injection/<run_id>
```

边界和每项通过条件见
[`docs/deployment/fault-injection-acceptance.md`](docs/deployment/fault-injection-acceptance.md)。

当前 ROS1 Adapter 配置只负责 core transport、急停和 diagnostics，例如：

```yaml
robot_id: gazebo_turtlebot3
namespace: ""
transport:
  enabled: true
  wait_for_server_seconds: 10.0
  wait_for_result_seconds: 120.0
emergency_stop:
  interface: service
  name: /fireclaw/emergency_stop
  type: std_srvs/Trigger
```

导航 action 名称、goal 格式和结果映射属于导航 Plugin，不写入 core Adapter
配置。示例见
[`examples/ros1_configs/gazebo_turtlebot3_move_base.yaml`](examples/ros1_configs/gazebo_turtlebot3_move_base.yaml)
和
[`examples/robot_profiles/gazebo_turtlebot3.toml`](examples/robot_profiles/gazebo_turtlebot3.toml)。

## Gazebo 验收状态

现有测试已经覆盖 Plugin owner、typed schema、Plugin-owned handler、feedback、
cancel、参数 policy、Gateway 事件和“不回退到 RobotAdapter”的单元/集成边界。
旧的 preparation-only `gazebo_smoke` helper 已删除，避免把“生成配置”误报成
仿真验收。当前受控 Gazebo world 已有 live success、cancel、timeout、
abort/unreachable、diagnostics-first stall-recover 与 stall-escalate 六条真实
ROS1 `/move_base` proof。两条 stall lane 由可信 launch 将 DWA 前进速度界限固定为
零；首次导航安全超时后，Robot Agent 必须先调用 `navigation_diagnostics`，再按
场景执行恰好一次有界参数恢复和同目标重试，或携带 evidence ID 升级给操作员。
普通导航场景不会启用该故障注入。

当前导航 Plugin 自有验收链覆盖：

- 单楼层绝对 `map` 坐标；
- owner 必须为 `fireclaw.navigation.move-base`；
- `navigate_to_point` 必须进入真实 ROS1 `/move_base` action；
- feedback、success、cancel、timeout、abort 和对应终态传播；
- 最终报告、事件账本和审计 artifact；
- blocked/stall 场景调用 `navigation_diagnostics` 后恢复或升级；
- 显式 trap 断言，证明任何 Adapter domain 方法都没有被调用。

这套 lane 是确定性的工程正确性验收，不是 LLM planning 质量评测，也不能单独
支撑研究有效性结论。它现在会自动生成独立的 `ros_gazebo_system` 统一评测 bundle；
可信 Gazebo `ContactManager` observer 会覆盖首个 goal 前到终态停止后的完整窗口，
保留原始 contact 流、固定分类策略和加载的插件二进制哈希。六条任务 lane 的 dirty
development smoke 均已验证该闭环。`extensions/navigation-move-base/config/acceptance/frozen-suite.yaml`
现在冻结六个 task scenario、单楼层绝对 `map` point target、seed `0`、共享 world/map/
robot/navigation asset hashes 和 validation/test 的 repeat index；trusted runner 会在
启动 Gazebo 前校验 suite/scenario SHA-256，并把校验结果写入 `frozen-suite.json`。
在 clean commit `fe807376a2c6c20bab36b2d3e8d63c16fb3b7b53` 上，validation 与 test
各重复六条 lane 均已完成，两个 aggregate bundle 都是 `status=pass` 且
`paper_evidence_complete=true`；后续可在相同冻结协议下继续做 nightly 稳定性统计。

## Embodied evaluation 状态

`deterministic_integration`、`llm_planning` 与 `ros_gazebo_system` 三层评测均已
接入版本化统一协议：场景显式
声明 `point`、`area` 或 `entity` target、数据划分、seed、重复编号和预期 canonical
结果。其中 deterministic 执行必须走 scheduler-backed background Mission Run，并从
`/missions/{mission_id}/run` 读取不折叠的终态；LLM planning 则明确禁止 dispatch。
deterministic 基线仍只包含两条
point-navigation 场景。独立 LLM runner 已提供 point/area/entity development cases，
但 scripted-provider 测试只证明评测主链正确，不能冒充真实模型性能。第三层会把
六条 live Gazebo proof 规范化为统一 point target、canonical terminal、条件指标与
content-addressed 原始证据。

每次 deterministic run 要求一个新的空目录，自动保留成功、失败和 runner error，
并生成 `run-manifest.json`、`scenario-suite.json`、`scenarios.jsonl`、指标定义及统计、
Plugin/Tool inventory、Git/runtime provenance、Mission/Robot 原始事件、final report、
paper summary 和带 SHA-256 的 `artifact-manifest.json`。`contract_passed` 表示预期行为
是否满足，`task_success` 才表示任务真正完成；预期失败场景不会再被误算为任务成功。

LLM planning runner 只运行生产 `LLMMissionPlanner` 和只读
`MissionDeliberationRuntime`，不会构造 Gateway、scheduler、Robot Adapter 或 ROS。
它保存冻结 state、完整 prompt/Tool schema、模型 Tool calls、requested/actual model、
temperature、逐 case seed、token、延迟、可选 cost、编译后的 task graph、安全拒绝和
no-dispatch 证明。area case 必须先读取 `passage_open`/`structural_stable` belief 再提案。
planning protocol v2 对缺省 point `yaw` 按可执行 schema 规范化为 `0.0`，并在模型违反
“每轮恰好一个 Tool call”时允许一次不执行原响应的受控重试。报告会分开记录
`first_try_clean_rate`、`tool_protocol_valid_first_try_rate` 和条件
`planning_recovery_rate`，因此修复后的成功不会冒充模型首轮成功。

```bash
/home/lpp/miniconda3/envs/py310/bin/python \
  -m fireclaw_core.devtools.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json \
  --output-dir results/embodied-eval/<unique-run-id> \
  --adapter simulator
```

```bash
cp fireclaw.example.toml fireclaw.toml  # fill the shared [provider] table
/home/lpp/miniconda3/envs/py310/bin/python \
  -m fireclaw_core.devtools.llm_planning_eval \
  --config fireclaw.toml \
  --scenarios tests/fixtures/embodied_eval/planning_scenarios.json \
  --output-dir results/embodied-eval/<unique-llm-run-id> \
  --temperature 0
```

`llm_planning_eval` 与 Gateway 共用 `fireclaw.toml` 的 `[provider]` 配置；显式 CLI
参数仍可覆盖 TOML。API key 可以放在 `[provider].api_key`，或放在
`[provider].api_key_env` 指定的环境变量中，均不会进入 artifact。seed 会转发给兼容
provider，但不能保证后端确定性；正式论文数据仍应记录实际 response model 并执行多
seed 重复实验。

真实 Provider 的冻结 development 重复实验可改用
`tests/fixtures/embodied_eval/planning_scenarios_multiseed_development.json`；它展开为
point/area/entity × seeds `0,17,42,123,999` 共 15 cases，仍不是 held-out test set。

运行任一 trusted Gazebo acceptance scenario 后，runner 会自动在
`results/gazebo-acceptance/<run-id>/evaluation/` 生成第三层 bundle。也可以对既有 proof
离线聚合：

```bash
/home/lpp/miniconda3/envs/py310/bin/python \
  -m fireclaw_core.devtools.ros_gazebo_system_eval \
  --source-proof results/gazebo-acceptance/<run-id> \
  --output-dir results/embodied-eval/<unique-system-run-id> \
  --split development
```

第三层分别报告 `contract_pass_rate` 和 `task_success_rate`：预期 cancel、timeout、
failed 或 escalated 可以通过行为合同，但不会被计作任务完成。新 proof 会记录完整
collision contact stream；旧 proof 或不完整采集仍记为 missing，而不是零碰撞。
当前 dirty development smoke 仍不能冒充论文结果。

碰撞测量另有独立的 `gazebo_collision_calibration` positive-control：它不创建 Mission、
Robot task 或导航 goal，而是在静止 Burger 旁通过 Gazebo 服务生成一个固定浅重叠 SDF，
要求原始 ContactManager 流检出 prohibited collision 后再删除模型，并验证机器人仍停止。
独立 scorer 会重新分类原始 collision pair；该 lane 固定
`task_metrics_applicable=false`，故已知碰撞不会污染任务成功率或 collision-free 分母。

详细数据合同与三层运行方式见
[`docs/evaluation/embodied-evaluation.md`](docs/evaluation/embodied-evaluation.md)。

## 关键架构文档

- [Plugin / Skill / Tool 术语](docs/architecture/plugin-skill-tool-terminology.md)
- [Extension loader](docs/architecture/extension-loader.zh-CN.md)
- [Navigation Tool flow](docs/architecture/navigation-tool-flow.zh-CN.md)
- [Capability policy pipeline](docs/architecture/capability-policy-pipeline.md)
- [Deployment Tool policy](docs/architecture/deployment-tool-policy.md)
- [操作员状态、诊断与冻结恢复](docs/deployment/operator-readiness-recovery.md)
- [ROS1 实机部署与硬件停止证据](docs/deployment/ros1-deployment-guide.md)
- [实机硬件安全验收（无真机准备版）](docs/deployment/real-robot-hardware-safety-acceptance.md)
- [故障注入验收](docs/deployment/fault-injection-acceptance.md)
- [ROS diagnostics](docs/architecture/ros-diagnostic-tools.md)
- [OpenClaw alignment](docs/architecture/fireclaw-openclaw-alignment.md)
