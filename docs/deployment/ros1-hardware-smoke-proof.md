# ROS1 实机 Smoke Proof

更新时间：2026-08-13

本文只适用于经过现场审批的真实机器人。Gazebo acceptance 通过并不自动授权
实机运行；现场安全观察员、独立急停和平台厂商限制仍是硬前提。

在导航 smoke 之前，必须先按
`docs/deployment/real-robot-hardware-safety-acceptance.md` 完成硬件安全验收；
`hardware-safety report` 未达到 `PASSED` 时，不得用导航测试代替安全证明。

## 当前架构边界

```text
FireClaw core RobotAdapter
  -> state / environment / emergency stop only

domain Plugin
  -> typed physical Tool
  -> Plugin-owned Adapter
  -> ROS action/service/topic or robot SDK
```

不要在 core ROS 配置中重新建立按 Tool 名称的 `endpoints`、`targets` 或
`remap` 表。导航由 `fireclaw.navigation.move-base` Plugin 的
`Ros1MoveBaseBackend` 固定连接 `/move_base`；其他领域能力由各自 Plugin
拥有。

## 前置门

- [ ] 相同 Plugin、地图和参数已经通过 Gazebo acceptance。
- [ ] 机器人厂商 bringup、定位、TF、传感器和 `move_base` 在 FireClaw 外独立通过。
- [ ] 物理急停经过实际测试，安全观察员可直接触发。
- [ ] 使用 `deployment.mode="real"`，没有仿真默认值静默进入实机。
- [ ] Plugin inventory 中 `navigate_to_point` owner 精确为
      `fireclaw.navigation.move-base`。
- [ ] core `RobotAdapter` 不存在导航领域方法，fallback trap 测试通过。
- [ ] Gateway 认证、TLS/网络边界、执行授权和审计存储已配置。
- [ ] real mode 的 bounded mutation 默认关闭；任何开放项均有精确 allowlist 和审批。

## ROS 检查

```bash
rostopic type /move_base/goal
rostopic type /move_base/feedback
rostopic type /move_base/result
rosservice type /move_base/clear_costmaps
rosrun tf tf_echo map base_link
```

`examples/ros1_configs/gazebo_turtlebot3_move_base.yaml` 只能作为 schema 示例，
不能直接当作实机地址、机器人 ID 或安全配置。

## 最小实机顺序

1. 轮子离地或平台进入厂商规定的安全测试模式，仅验证 Gateway 状态、Plugin
   inventory、diagnostics 和急停。
2. 在隔离空场以极低的审核速度发送一个近距离绝对 `map` pose。
3. 验证 feedback 后立即执行一次 cancel，确认机器人停车且终态为
   `cancelled`。
4. 再执行一个成功 goal，核对 Robot outcome、Mission final report 和审计事件。
5. 不在首次 smoke 中做自动调参、拥堵恢复或感知/救援动作。

任何 TF 跳变、传感器 freshness 异常、急停不确定、goal cancel 无响应或审计缺失
都必须停止测试并标记 blocked，不得通过重试掩盖。

## 必需证据

归档 Plugin owner/version、commit、机器人固件版本、ROS graph、目标 pose、
feedback、cancel/result、急停检查、Robot/Mission terminal outcome、final report
和事件账本。真实地图、网络地址、凭证和现场日志应按敏感数据策略单独保存，
不得直接提交到仓库。
