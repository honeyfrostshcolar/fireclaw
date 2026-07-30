# Physical Tool Plugin Runtime (Legacy API)

> Terminology note: the current Python names `Skill`, `SkillRegistry`, and
> `PhysicalSkillPlugin` predate FireClaw's OpenClaw-aligned terminology. They
> model executable Tool definitions and projections, not complete
> OpenClaw-style Skills. The canonical distinction is defined in
> [`plugin-skill-tool-terminology.md`](plugin-skill-tool-terminology.md).

## 目标

FireClaw 的 Agent 框架负责推理、权限、安全、生命周期和审计，不负责实现
导航、感知、机械臂或灭火算法。物理能力必须能够独立增加，而不要求在
Agent core 中增加按 Tool 名称判断的分支。

当前边界是：

```text
Mission Coordinator task contract
-> Robot Agent tool proposal
-> plugin-owned target binding and mutation guard
-> plugin-owned JSON input schema
-> SafetyGate
-> FireClawPluginHost tool projection
-> SkillRegistry compatibility view
-> RobotActionRuntime
-> Robot Adapter action
-> ROS / simulator / SDK / algorithm
```

## OpenClaw 对照

本实现通过 CodeGraph 检查了：

- `openclaw/src/plugin-sdk/tool-plugin.ts`
  `defineToolPlugin()`；
- `openclaw/src/plugins/plugin-api.types.ts`
  `OpenClawPluginApi.registerTool()`；
- `openclaw/src/agents/openclaw-tools.ts`
  工具集合构建；
- `openclaw/src/agents/agent-tools.policy.ts`
  统一 allow/deny policy。

复用的结构是：工具自己声明名称、描述、参数、输出和执行函数；插件启动时只
调用通用注册 API；宿主统一生成工具目录、应用 policy 并调用 execute，不在
核心循环中识别具体工具名称。

FireClaw 增加了 OpenClaw 软件工具不需要的具身约束：

- 任务目标到工具参数的权威绑定；
- LLM 不得改写的 protected inputs；
- Robot Adapter action binding；
- 传感器、风险、安全类别和前置条件；
- 资源锁、完成证据、超时、取消和反馈；
- real/simulation/dry-run 隔离；
- 物理结果未知时禁止盲目重放。

## 五个概念

### Plugin

Plugin 是可安装和可部署的生命周期单元。一个导航 Plugin 可以附带
Navigation Skill、注册多个导航 Tool，并包含 Adapter 配置与 move_base
Runtime。

### Skill

Skill 是 Agent-facing 的能力说明和工作流，可以指导 Agent 组合多个 Tool。
例如 Navigation Skill 说明如何选择目标、调用导航 Tool、解释反馈以及在堵塞
或超时时恢复。Skill 不等于某一个原子函数。

### Tool

Tool 是 LLM 可提出调用的原子接口，例如 `navigate_to_point`、
`get_navigation_status` 和 `cancel_navigation`。当前 legacy
`PhysicalSkillPlugin` 声明的是 physical Tool contract，包括 schema、任务
绑定、安全元数据、执行 action、资源和证据。LLM 不能直接得到 ROS handle
或 Adapter 对象。

### Adapter

Adapter 是算法和硬件实现边界。它通过 `supported_actions` 声明能力，并为
Tool 声明的 `action` 提供同名受信任 callable。`RobotActionRuntime` 从注册表
查找 handler，不包含导航、搜索或机械臂专用分支。

### Runtime / Algorithm

Runtime/Algorithm 是真正执行工作的系统，例如 ROS1 move_base、Nav2、覆盖
搜索规划器、感知节点或机器人 SDK。它不负责 Agent 工具选择、上下文管理和
任务规划。

MCP 是可选传输层。只有能力运行在独立进程、另一台计算机或远端服务时，才有
必要用 MCP、HTTP、ROS action 等方式承载 Tool/Adapter 调用。本机 ROS 或 SDK
能力无需为了形式统一而强制经过 MCP。

## 参数权限

适合交给 LLM 的参数：

- 目标点 `x / y / yaw / frame_id`；
- 搜索区域、对象 ID、任务容差；
- 从 allowlist 中选择的命名策略 profile。

不应直接交给 LLM 的参数：

- footprint、wheelbase、传感器 frame；
- 电机、制动、载荷和温度硬件极限；
- 未经验证的最大速度、最大加速度和原始 inflation 参数；
- ROS topic/service/action 地址和认证信息。

需要策略变化时，优先让 LLM 选择经过验证的
`smoke_conservative`、`normal_indoor` 等 profile，由宿主把 profile 映射为
只读算法参数。

## 新增 Plugin 和 Tool

新增 `place_safety_beacon` 这类能力时，只需要：

1. 在 Plugin 中使用 legacy `define_physical_skill_plugin()` 定义原子 Tool
   合同；
2. 在目标 Robot Adapter 中实现并声明 `deploy_beacon` action；
3. 通过 `FireClawPluginApi.register_physical_capability()` 贡献能力，并由
   `SkillRegistry.register_plugin()` 生成当前 Adapter 的兼容执行投影；
4. 根据需要附带说明完整操作流程的 Skill；
5. 增加 schema、安全、Adapter 和实机/仿真测试。

Agent loop、Robot Agent policy、SafetyGate、PlanExecutor、Operator projector
和 action backend 不需要新增 Tool 名称分支。测试
`tests/test_physical_skill_plugin.py` 用 Tool 名
`place_safety_beacon` 和不同的 Adapter action 名 `deploy_beacon` 验证了这条
扩展路径。

## 多楼层能力

“去二楼”不是给 `navigate_to_point` Tool 增加一个 `floor` 参数。它需要电梯/楼梯
状态、进入与退出、楼层切换、地图激活、重定位和失败恢复，应当实现为
`transition_to_floor` 等独立物理 Tool，再由分层任务图和 Skill 组合：

```text
navigate_to_point(transition_entry)
-> transition_to_floor(target_floor)
-> activate_map(map_id)
-> relocalize
-> navigate_to_point(target_pose)
```

当前 FireClaw 仍只正式支持单楼层二维地图。

## 已知边界

- Python 内置信任插件已有通用注册 API，但第三方物理插件的发现、签名、
  sandbox 和动态加载尚未完成；
- workspace JSON manifest 当前主要承载 legacy subprocess Tool，不会获得任意
  Robot Adapter 权限；
- `resource_locks` 已接入 SQLite WAL 支持的机器人本地资源租约；执行器在
  Adapter side effect 前原子获取，并在成功、失败或取消路径释放；
- `success_evidence` 已进入插件元数据，但仍需接入通用完成证据验证器；
- schema 校验覆盖 FireClaw 当前使用的 JSON Schema 子集，不是完整
  JSON Schema 实现；
- 每个真实 Adapter handler 仍必须单独进行安全审查、仿真验证和实机验证。
