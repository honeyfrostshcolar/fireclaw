# move_base 导航 Tool 流程

## Physical Tool 如何到达 ROS

```text
extensions/navigation-move-base/fireclaw.plugin.json
  -> plugin/entrypoint.py
  -> PluginApi.register_physical_tool(navigate_to_point)
  -> FireClawPluginHost 记录 owner=fireclaw.navigation.move-base
  -> FireClawAgent 将 physical contribution 投影到 Tool registry
  -> Robot Agent 提出结构化 x/y/yaw/frame_id
  -> delegation policy 校验 Tool allowlist 和受保护目标参数
  -> SafetyGate / execution authorization / resource lease
  -> Plugin-owned physical handler
  -> Ros1MoveBaseBackend.navigate_to_point
  -> ROS1 /move_base action
  -> feedback / terminal result
  -> deadline 或 operator cancel 时 cancel_goal()
  -> 有界等待 PREEMPTED/RECALLED/其他已知 actionlib 终态
  -> execution events、最终报告和审计证据
```

`RobotAdapter` 不包含 `navigate_to_point`。`RegisteredActionBackend` 只执行显式
注册的 Plugin handler，不使用 `getattr(robot, action_name)`，因此不存在静默
Adapter 回退。

`navigate_to_point` 当前声明 `timeout_seconds=120` 和
`cancellation_ack_timeout_seconds=2`。`RobotActionRuntime` 使用 monotonic clock
强制前者；后者限定 `cancel_goal()` 后的确认等待。只有 actionlib 返回可确认的
终态，Plugin 才返回 `runtime_stopped=true`。若 action server 没有在窗口内确认，
结果为 `lost`，不能伪装成 `cancelled` 或 `timed_out`，运动 lease 也不会被正常
释放。

`move_base_cancel_navigation` 虽然是独立 bounded-mutation Agent Tool，也遵守同一
原则：调用 `cancel_all_goals()` 后必须等待终态确认，不能仅凭取消 API 已返回就
宣称机器人停止。

## Agent Tool 如何到达后端

```text
Navigation Plugin register_tool(...)
  -> AgentToolRuntime 根据 deployment profile 生成投影
  -> LLM 提出一个已注册 Agent Tool 调用
  -> schema / mode / effect / approval policy
  -> Plugin-owned backend
  -> 结构化 observation 与审计事件
```

Physical motion 与诊断/调参 Agent Tool 是两条不同路径。通用 Agent Tool 结果是
advisory，不能冒充 physical success 或传感器事实。

## 当前 Tool 分层

| Tool | 类型 | 作用 |
| --- | --- | --- |
| `navigate_to_point` | physical Tool | 发送受任务合同保护的单楼层目标点 |
| `move_base_navigation_status` | read Agent Tool | 读取 action 状态 |
| `move_base_parameter_catalog` | read Agent Tool | 查看允许的参数、类型和值域 |
| `move_base_get_parameters` | read Agent Tool | 读取固定 scope 的参数 |
| `move_base_set_parameters` | bounded mutation Agent Tool | 修改目录内参数 |
| `move_base_cancel_navigation` | bounded mutation Agent Tool | 取消当前导航目标 |
| `move_base_clear_costmaps` | bounded mutation Agent Tool | 清理 costmap |
| `navigation_diagnostics` | diagnostics Plugin Agent Tool | 汇总 TF、topic、action 与导航健康状态 |

Tool 不是 Skill。Navigation Skill 位于
`extensions/navigation-move-base/skills/navigation/SKILL.md`，描述何时查询、
如何恢复和何时升级。

## Backend 选择

- `simulation + dry-run/simulator/mock-*` 默认使用
  `InMemoryMoveBaseBackend` 做确定性合同测试；
- `adapter=ros1` 使用 `Ros1MoveBaseBackend` 连接真实 `/move_base`；
- acceptance harness 可通过通用 Plugin service bag 注入
  `fireclaw.navigation.move-base.backend`；
- Gateway 只转交通用 service bag，不识别该 key，也不创建导航 fallback。

## 仿真与真实模式

仿真模式允许目录内的 bounded parameter mutation，仍限制 scope、参数名、类型和
值域。真实模式默认隐藏 mutation Tool。显式开放时，插件配置必须设置：

```toml
[plugins.config."fireclaw.navigation.move-base"]
real_mutation_enabled = true
real_mutable_parameters = ["dwa/max_vel_x"]
```

真实 mutation 还需要精确后端授权和 operator approval。仿真权限不会自动进入
真实部署。

## Stall / blocked 恢复链

```text
navigate_to_point 无进展或超时
  -> move_base_navigation_status
  -> navigation_diagnostics
  -> 识别 TF / sensor / costmap / planner / action server 原因
  -> 仿真：受限调参或 clear_costmaps 后重试
  -> 真实：仅在 policy + authorization 允许时恢复
  -> 否则 blocked / escalated
```

验收必须保存每轮 feedback、Tool owner、调用参数哈希、policy 决定、诊断结果、
恢复动作、终态、最终报告和可关联的 evidence ID。
