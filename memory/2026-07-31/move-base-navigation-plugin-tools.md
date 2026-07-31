# move_base 导航插件 Tool 化

## 时间

2026-07-31

## 任务目标

把 move_base 导航运行时接入 Robot Agent 的统一 Plugin Host，让 LLM 可以在任务执行中查询导航状态、读取参数、在仿真中调参、取消目标和清理 costmap；真实模式默认不暴露调参修改，便于后续逐项验证哪些能力可以开放。

## OpenClaw 对照

使用 CodeGraph 检查了 OpenClaw `src/plugins/plugin-api.types.ts` 的 `registerTool` 和 Agent Tool 投影/策略链。复用的结构是：插件通过宿主注入的注册 API 一次贡献多个 Tool，宿主统一生成 Tool schema、应用 mode/role allow/deny 策略、执行前拦截并记录调用。FireClaw 额外保留 ROS scope 固定映射、物理安全、真实模式精确授权和参数值域验证。

## 新增实现

### `extensions/navigation-move-base/plugin/move_base.py`

- `MoveBaseParameterSpec`：固定参数目录，包含 `move_base`、`dwa`、`local_costmap`、`global_costmap` 四个 scope、类型、最小值、最大值和说明；
- 目录包含 DWA 速度/加速度/采样/评分/容差、move_base 频率/恢复、costmap inflation/obstacle/raytrace/frequency 等调参；
- 明确排除 frame/topic、footprint、硬件极限、planner/plugin class、任意 ROS 参数服务器键和认证信息；
- `MoveBaseParameterPolicy`：simulation 允许目录内全部参数，real 默认禁止；real 只有 `real_mutation_enabled=true` 且参数在白名单时允许，白名单支持 `dwa/max_vel_x` 形式；
- 验证类型、有限值、范围、scope 归属和 `min_vel_* <= max_vel_*` 关系；
- `MoveBaseNavigationBackend`：固定的 status、get/set parameters、cancel、clear costmaps 适配器协议；
- `InMemoryMoveBaseBackend`：仿真/单元测试后端；
- `Ros1MoveBaseBackend`：延迟导入 ROS1 `dynamic_reconfigure`、`actionlib` 和 `std_srvs`，固定 scope 到 `/move_base`、`/move_base/DWAPlannerROS`、local/global costmap，不接受 LLM 提供 namespace 或 shell 命令；
- `register_move_base_navigation_plugin()`：在一个 Plugin Host 事务中注册：
  - `move_base_parameter_catalog`；
  - `move_base_navigation_status`；
  - `move_base_get_parameters`；
  - `move_base_set_parameters`；
  - `move_base_cancel_navigation`；
  - `move_base_clear_costmaps`。

`src/fireclaw_core/navigation/move_base_plugin.py` 现在只是旧导入路径兼容
shim，不再拥有 move_base 业务实现。

### Gateway 接入

此前 `GatewayConfig` 新增的 move_base 字段现在只作为迁移兼容：

- `move_base_navigation_tools_enabled`；
- `move_base_real_mutation_enabled`；
- `move_base_real_mutable_parameters`。

新配置应使用 `[plugins.config."fireclaw.navigation.move-base"]`。Robot Gateway
任务上下文构建时，通用 extension loader 读取 `fireclaw.plugin.json`，调用插件
自己的 `plugin/entrypoint.py`，随后 `AgentToolRuntime` 才生成当前 deployment
profile 可见的 Tool schema。后端选择、参数目录和 Tool 注册均由导航插件自己完成。

### 文档与配置

- `fireclaw.example.toml` 增加 move_base Tool 配置示例；
- 更新 `extensions/navigation-move-base/README.md`、`plugin/README.md`、`runtime/README.md`、`tools/README.md` 和 `skills/navigation/SKILL.md`；
- 新增 `docs/architecture/navigation-tool-flow.zh-CN.md`，记录从 LLM Tool call 到 ROS dynamic-reconfigure/action/service 的完整链路。

## 模式行为

### Simulation

当 `[deployment] mode = "simulation"`：

- 所有六个导航 Tool 投影给 Robot Agent；
- `move_base_set_parameters` 可使用固定目录的全部参数；
- cancel 和 clear costmaps 作为 bounded mutation 直接执行；
- 仍然不能写任意 ROS 参数或执行任意 shell。

### Real

默认只投影读取类 Tool。若开启 real mutation：

1. `move_base_real_mutation_enabled=true`；
2. 参数名加入 `move_base_real_mutable_parameters`；
3. `AgentToolRuntime` 仍将 bounded mutation 标记为 `require_approval`；
4. 没有覆盖最终 Tool 名和参数哈希的一次性授权时，后端不会调用 ROS。

## 调用流程示例

```text
LLM -> move_base_parameter_catalog({scope:"dwa"})
    -> move_base_get_parameters({scope:"dwa", names:["max_vel_x","sim_time"]})
    -> move_base_set_parameters({scope:"dwa", parameters:{max_vel_x:0.35, sim_time:2.0}})
    -> navigate_to_point({x:..., y:..., yaw:..., frame_id:"map"})
    -> move_base_navigation_status()
```

每一步都经过 schema、mode、role、插件状态和后端参数策略；Tool 结果进入 Robot Agent observation，LLM 可继续、重试、取消或上报。

## 验证

```text
tests/test_move_base_navigation_plugin.py: 4 passed
Robot Agent/ROS/physical plugin focused tests: 49 passed
Gateway + embodied Gateway integration tests: 32 passed
当时全量: 2001 passed, 7 skipped in 174.89s；后续扩展加载器改造后的全量见
`fireclaw-openclaw-extension-loader.md`（2006 passed, 7 skipped）。
```

## 后续建议

- 在 TurtleBot3 Gazebo 中运行动态调参实验，记录每个参数/Tool 对窄门通过率、导航耗时、碰撞和恢复次数的影响；
- 将真实实验中确认安全的参数逐个加入 `scope/name` 白名单；
- 进一步为参数组合增加后端事务/回滚和实验 profile（如 conservative、normal），避免真实环境连续写入多个互相冲突的参数；
- 真实 ROS 运行前确认 `dynamic_reconfigure` namespace 与 TurtleBot3 move_base launch 配置一致。
