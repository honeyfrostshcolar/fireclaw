# ROS2 / Nav2 Plugin 计划

更新时间：2026-08-09

> 当前状态：只有 `Ros2AdapterProtocol`，没有可用于实机或仿真的 ROS2
> transport，也没有 Nav2 Plugin。本文件是未来计划，不是已实现能力。

## 设计约束

ROS2 支持必须沿用当前 Plugin 边界：

```text
core RobotAdapter
  -> robot state / environment state / emergency stop

Nav2 Navigation Plugin
  -> Navigation Skill
  -> navigate_to_point physical Tool
  -> status / cancel / recovery Agent Tools
  -> Plugin-owned rclpy Action Adapter
  -> nav2_msgs/action/NavigateToPose Runtime
```

不得把 Navigation、perception 或 manipulation 的 endpoint 表重新放回 core
Adapter，也不得按 Tool 名称修改 Gateway。新的 ROS2 Plugin 通过
`fireclaw.plugin.json` 和公开 `fireclaw_plugin_sdk` 注册贡献。

## 分阶段实现

1. 实现 core `Ros2RobotAdapter` 的状态、环境观测、急停、node lifecycle 和
   QoS 基础边界。
2. 新建 `extensions/navigation-nav2`，由 Plugin 拥有
   `NavigateToPose` action client、feedback、cancel、timeout 和终态映射。
3. 为 Nav2 参数、costmap clear 与 lifecycle 状态定义有限 typed Tools；禁止
   LLM 提供任意 node、topic、service 或 parameter key。
4. 先做 mock rclpy 合同测试，再做 ROS2 action server 集成测试，最后做
   Gazebo/Ignition acceptance。
5. 复用 ROS1 lane 的 owner、Adapter trap、终态传播、final report、审计和
   stall diagnostics/recovery 场景矩阵。

## 兼容目标

Tool 语义保持
`navigate_to_point(x, y, yaw=0.0, frame_id="map")`。ROS1 move_base 与 ROS2
Nav2 是两个 Plugin/Adapter 实现，不要求 Mission Planner 或 Robot Agent 为
Runtime 类型增加分支。

ROS2 lane 必须使用独立 marker（例如 `ros2_acceptance`）和显式环境开关，
不能让缺少 ROS2 的普通开发环境误报通过。
