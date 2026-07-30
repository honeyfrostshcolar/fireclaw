# Physical Skill Plugin Runtime

> Terminology correction, 2026-07-29: this record uses the legacy FireClaw
> code names `Skill`, `SkillRegistry`, and `PhysicalSkillPlugin`. Canonically,
> these implemented executable Tool definitions/projections. A Skill is the
> broader Agent-facing workflow that may coordinate multiple Tools. See
> `memory/2026-07-29/plugin-skill-tool-terminology.md`.

更新时间：2026-07-29T16:51:28+08:00

## 任务目标

修正机器人物理能力与 FireClaw Agent core 的过度耦合。此前导航算法本身仍在
Robot Adapter/ROS 层，但接入 `navigate_to_point` 时需要同时修改：

- `execution/skills.py` 内置注册；
- `execution/action_runtime.py` action 名称分支；
- `task/task_contract.py` 目标输入映射名单；
- `agent/robot_agent.py` Robot policy 防篡改名单；
- `safety/sensor_policy.py` 技能类别名单；
- `infra/operator_projection.py` 操作员消息名单；
- `execution/execution_event_producer.py` 路线失败分类名单。

用户要求充分参考 OpenClaw 的成熟 tool/skill/plugin 设计，使导航、感知、
机械臂等算法继续独立，并通过 Skill/Tool/Adapter 合同接入。

## 恢复状态

- 分支：`master`，开始时与 `origin/master` 同步且工作区干净。
- 起始提交：`319c381 feat: unify mission and robot agent planning runtime`。
- 最近完整非 ROS 基线：`1799 passed, 6 deselected`。

## OpenClaw 对照

按 OpenClaw-first 要求使用 CodeGraph 检查：

- `openclaw/src/plugin-sdk/tool-plugin.ts`
  `defineToolPlugin()`；
- `openclaw/src/plugins/plugin-api.types.ts`
  `OpenClawPluginApi.registerTool()`；
- `openclaw/src/agents/openclaw-tools.ts`
  工具集合构建；
- `openclaw/src/agents/agent-tools.policy.ts`
  统一工具 allow/deny policy。

复用的结构：

- tool/plugin 自己声明 name、description、parameters、output schema 和 execute；
- 插件启动时只调用通用 register API；
- 宿主统一生成工具目录、应用 policy 并调用 execute；
- Agent loop 不识别每一种具体工具名称。

FireClaw 的具身适配：

- task target 到 tool input 的权威绑定；
- protected input 防止 Robot Agent/LLM 改写中央任务目标；
- Adapter action binding；
- 传感器、安全类别、风险和 precondition；
- resource lock、success evidence、timeout、retry、feedback、cancellation；
- real/simulation/dry-run 隔离和物理动作不可盲目重放。

## 实现

### 声明式物理插件

新增 `src/fireclaw_core/execution/skill_plugin.py`：

- `TaskInputBinding`
- `PhysicalSkillPlugin`
- `PhysicalSkillCatalog`
- `define_physical_skill_plugin()`
- 当前 FireClaw tool schema 所需 JSON Schema 子集校验

插件声明：

- LLM-facing `parameters` 与 `output_schema`；
- Skill 名与可不同名的 Adapter `action`；
- action-input builder；
- task target bindings、默认值与 protected 字段；
- domain、safety class、risk、sensor alternatives；
- resource locks、success evidence；
- 默认后续技能、Robot Agent supplemental 暴露和操作员消息。

自由 `metadata` 不能覆盖强类型保留字段的审计投影。

### 内置能力迁移

新增 `src/fireclaw_core/execution/builtin_physical_skills.py`，迁移：

- `navigate_to_point`
- legacy `navigate_to_floor`
- `search_for_victims`
- `assess_victim`
- `report_status`
- `return_to_safe_zone`

`create_default_skill_registry()` 现在只遍历插件目录并调用
`SkillRegistry.register_plugin()`。

### 通用 Adapter 分发

`RobotAdapterActionBackend` 从 Adapter `supported_actions` 注册 callable，并按
注册表执行，不再包含 action 名称 `if/elif`。

`RobotAdapter` Protocol 不再枚举所有物理动作。插件把受信任 action 绑定到
Adapter 暴露的同名 callable。默认 Adapter 和 ROS1 action 名单从内置物理
插件目录派生。

调用前通过函数签名判断 feedback/cancellation kwargs。删除了捕获
`TypeError` 后再次调用的旧逻辑，避免 handler 已产生物理副作用后内部
`TypeError` 导致动作重放。

### 合同和安全

- `planning_result_from_structured_task()` 从插件 task bindings 生成输入；
- point target 自动增加导航 skill 由插件 `auto_include_for_target` 驱动；
- 搜索的默认 report follow-up 和 supplemental skills 由插件元数据驱动；
- deterministic Robot Agent planner 与 `RobotAgentPolicy` 使用同一插件合同；
- protected input 校验不再读取 floor/point 技能名集合；
- `SafetyGate` 和 `PlanExecutor` 都强制校验输入 schema；
- sensor policy 使用 safety class 与 sensor alternatives，不按技能名分类；
- execution invalidation 使用 skill domain/safety class 判断 route blocked；
- PlanExecutor 把插件操作员消息写入事件，Projector 通用读取。

### 扩展性验收

新增 `tests/test_physical_skill_plugin.py`，定义仓库此前不存在的：

```text
Skill: place_safety_beacon
Adapter action: deploy_beacon
```

验证：

- Skill 名与 action 名可不同；
- 无 Agent core 分支即可注册和执行；
- task target 自动生成 `zone/color` 参数；
- Robot Agent 改写 zone 被通用 protected binding 拒绝；
- 非 allowlist 颜色在 Adapter 调用前被 schema 阻断；
- 操作员消息不需要 Projector 增加技能名分支；
- 自由 metadata 不能伪装 action/safety/risk 合同。

`tests/test_action_runtime.py` 新增 handler 内部 `TypeError` 只调用一次的回归。

## 文件修改

- `src/fireclaw_core/execution/skill_plugin.py`
- `src/fireclaw_core/execution/builtin_physical_skills.py`
- `src/fireclaw_core/execution/skills.py`
- `src/fireclaw_core/execution/action_runtime.py`
- `src/fireclaw_core/execution/executor.py`
- `src/fireclaw_core/execution/execution_event_producer.py`
- `src/fireclaw_core/task/task_contract.py`
- `src/fireclaw_core/agent/agent.py`
- `src/fireclaw_core/agent/robot.py`
- `src/fireclaw_core/agent/robot_agent.py`
- `src/fireclaw_core/safety/safety.py`
- `src/fireclaw_core/safety/sensor_policy.py`
- `src/fireclaw_core/infra/operator_projection.py`
- `src/fireclaw_core/ros/ros1_config.py`
- `src/fireclaw_core/gateway/gateway.py`
- focused tests、README 和 OpenClaw alignment docs
- `docs/architecture/physical-skill-plugin-runtime.md`

## 验证

中间聚焦回归：

```text
102 passed
136 passed
69 passed
```

Gateway/Robot Agent 回归需要本地回环 socket。沙箱内失败为
`PermissionError: Operation not permitted`；沙箱外运行后只有一个真实兼容
问题：省略具有 schema default 的 `yaw` 被 protected binding 当成缺失。
修正为允许省略 schema-default 字段后该用例通过。

首次完整非 ROS 回归：

```text
1803 passed, 6 deselected in 141.38s
```

完成 TypeError 单次调用与 metadata 保留字段修复后的最终回归：

```text
1805 passed, 6 deselected in 141.00s
```

其他：

```text
python3.10 -m compileall -q src tests  # passed
git diff --check                       # passed
```

六个 deselected 用例是需要 live ROS 环境的 `ros1_smoke`。

## 工程结论

导航算法没有进入 Agent core。当前接入边界已经从“修改多处核心技能名称分支”
转为：

```text
one PhysicalSkillPlugin
+ one Adapter handler/capability declaration
+ tests
```

Legacy `PhysicalSkillPlugin` 是 physical Tool contract，Tool 是 LLM-facing
原子接口，Skill 是组合多个 Tool 的 Agent 工作流，Adapter 是算法/ROS/SDK
边界。MCP 只是独立进程或远端 Runtime 可选的传输层，不是本机物理 Tool 的
必经层。

## 研究判断

这是必要的工程架构正确性和可控实验基础，不应单独宣称论文创新。它减少了
不同具身能力接入时的实现混杂，使后续可以公平比较不同 local planner、
安全策略和算法 profile。

可能形成研究贡献的方向仍需要实验方法，而不是插件 API 本身，例如：

- constrained tool parameterization 对 unsafe proposal rate 的影响；
- 命名 profile 与开放连续参数的安全/任务成功率消融；
- plugin-declared evidence contract 对错误完成率和事故可重建性的影响；
- centralized plan / local ReAct / deterministic safety policy 的组合消融。

## 已知边界和下一步

- 第三方物理插件尚无发现、签名、sandbox 和动态加载；
- workspace JSON manifest 仍主要是 subprocess skill；
- `resource_locks` 与 `success_evidence` 已声明，但尚未完整接入本机通用并发
  调度器和完成验证器；
- schema 校验是当前所需 JSON Schema 子集，不是完整标准实现；
- 每个真实 Adapter handler 仍需独立仿真、实机和安全验证；
- 当前不实现多楼层；未来应增加 `transition_to_floor`、地图激活和重定位等
  独立插件，再由分层任务图组合。

当前改动未提交；用户尚未要求 commit/push。
