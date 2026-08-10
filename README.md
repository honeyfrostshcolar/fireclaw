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

## 快速验证

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

```bash
/home/lpp/miniconda3/envs/py310/bin/python \
  -m fireclaw_core.devtools.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json \
  --output-dir results/embodied-eval/<unique-run-id> \
  --adapter simulator
```

```bash
export FIRECLAW_PROVIDER_API_KEY='<secret>'
/home/lpp/miniconda3/envs/py310/bin/python \
  -m fireclaw_core.devtools.llm_planning_eval \
  --scenarios tests/fixtures/embodied_eval/planning_scenarios.json \
  --output-dir results/embodied-eval/<unique-llm-run-id> \
  --provider-base-url https://<provider>/v1 \
  --provider-name <provider-name> \
  --model <model-id> \
  --temperature 0
```

API key 只从指定环境变量读取，不进入 artifact。seed 会转发给兼容 provider，但不能
保证后端确定性；正式论文数据仍应记录实际 response model 并执行多 seed 重复实验。

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
- [ROS diagnostics](docs/architecture/ros-diagnostic-tools.md)
- [OpenClaw alignment](docs/architecture/fireclaw-openclaw-alignment.md)
