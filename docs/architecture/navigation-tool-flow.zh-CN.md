# move_base 导航 Tool 流程

## 一次调用如何到达 ROS

```text
Navigation Plugin
  -> FireClawPluginHost.register_tool
  -> AgentToolRuntime 根据 deployment profile 生成工具投影
  -> Robot Agent LLM 看到 Tool schema
  -> LLM 提出结构化参数
  -> AgentToolRuntime 校验角色、模式、参数 schema 和审批状态
  -> move_base 参数策略校验固定 scope、参数名和值域
  -> Ros1MoveBaseBackend / InMemoryMoveBaseBackend
  -> dynamic_reconfigure、move_base action 或 clear_costmaps
  -> 结构化结果写入 Robot Agent observation 和审计事件
  -> LLM 根据结果继续、重试、取消或上报
```

LLM 永远不会得到 ROS handle、任意 ROS namespace、任意 `rosparam` key 或
shell 命令。ROS namespace 和 service/action 名称由后端固定映射。

## Tool 分层

| Tool | 类型 | 作用 |
| --- | --- | --- |
| `navigate_to_point` | physical Skill/Tool | 发送一个受任务合同保护的目标点 |
| `move_base_navigation_status` | read Agent Tool | 读取当前 action 状态 |
| `move_base_parameter_catalog` | read Agent Tool | 查看当前模式允许的参数、类型和值域 |
| `move_base_get_parameters` | read Agent Tool | 读取一个固定 scope 的参数 |
| `move_base_set_parameters` | bounded mutation Agent Tool | 修改目录内参数 |
| `move_base_cancel_navigation` | bounded mutation Agent Tool | 取消当前导航目标 |
| `move_base_clear_costmaps` | bounded mutation Agent Tool | 请求清理 costmap |

Tool 不是 Skill。`navigation/SKILL.md` 描述何时查询、如何调参和如何解释
反馈；上面的每一个名字都是 Agent 可以原子调用的 Tool。

## 仿真模式

当 Robot Gateway 的 deployment mode 为 `simulation` 时：

- 参数目录中的全部参数对 `move_base_set_parameters` 可用；
- 仍然限制为四个固定 scope 和每个参数的类型/最小值/最大值；
- cancel、clear costmap 等 bounded mutation Tool 可以直接执行；
- `InMemoryMoveBaseBackend` 可用于契约测试，实际仿真时替换为 ROS1 后端。

“全部参数”指 FireClaw 明确收录的导航调参目录，不是任意参数服务器写入。
目录不包含 frame/topic、footprint、硬件极限、插件类名和认证信息。

## 真实模式

真实模式默认只暴露读取 Tool。若要测试某个修改能力，需要同时：

1. 设置 `move_base_real_mutation_enabled = true`；
2. 把参数名加入 `move_base_real_mutable_parameters`（可使用
   `dwa/max_vel_x` 这种 scope-qualified 名称）；
3. 为这一次工具名和最终参数生成精确操作授权。

即使三项都满足，`bounded_mutation` 仍会进入 `require_approval`，没有批准记录
就不会调用 ROS。这样可以逐项实验哪些调参在真实机器人上值得开放，而不会把
仿真权限自动带入真实部署。

## 典型卡住处理

```text
导航 Tool 超时
  -> Robot Agent 调用 move_base_navigation_status
  -> 调用 navigation_diagnostics / move_base_get_parameters
  -> LLM 判断是传感器、TF、costmap 还是局部规划器问题
  -> 仿真：修改目录内参数 -> 清理 costmap -> 重试导航
  -> 真实：若参数未被白名单和操作员授权覆盖，则 blocked/escalated
```

每次 Tool 调用都保留参数哈希、策略决定、后端结果和 Robot observation，便于
比较仿真中哪些 Tool/参数组合真正有效。
