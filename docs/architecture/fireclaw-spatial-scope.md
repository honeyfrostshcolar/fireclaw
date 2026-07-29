# FireClaw Current Spatial Scope

更新时间：2026-07-29

## Current Contract

FireClaw 当前只支持单楼层二维运行环境。每个 Robot Agent 在一个已激活的
地图坐标系中工作，不负责楼梯、电梯、跨楼层地图切换或跨层定位。

当前导航合同是：

```text
navigate_to_point(x, y, yaw=0.0, frame_id="map")
```

- `x`、`y` 是目标点坐标；
- `yaw` 是目标朝向，单位为弧度；
- `frame_id` 明确坐标系，默认是 `map`；
- Robot Agent 不得修改中央任务合同中的目标 pose；
- Robot Adapter 负责把目标转换成 ROS action 或机器人 SDK 命令。

## Compatibility Boundary

以下字段和接口暂时保留，以便读取历史记录以及以后扩展多楼层能力：

- `navigate_to_floor`
- `MissionTarget.floor`
- `PlanningResult.target_floor`
- `RobotState.current_floor`
- `EnvironmentState.reachable_floors`
- `EnvironmentState.victims_by_floor`

它们不是当前能力声明。当前默认 Robot Agent profile 不向 LLM 暴露
`navigate_to_floor`，ROS1/Gazebo 示例也不再配置该 endpoint。

## Planning And Execution

当前目标优先表示为：

```json
{
  "frame_id": "map",
  "pose": {
    "x": 2.0,
    "y": 1.5,
    "yaw": 0.0
  }
}
```

执行路径为：

```text
MissionTarget.pose
-> StructuredRobotTask.target
-> Robot Agent target-envelope validation
-> navigate_to_point tool proposal
-> SafetyGate
-> Skill runtime
-> RobotAdapter
-> ROS move_base / robot SDK
```

`RobotAgentPolicy` 会逐项比较 `x`、`y`、`yaw` 和 `frame_id`。模型提出的
导航目标只要偏离中央合同，宿主就拒绝执行。

## Future Multi-Floor Extension

未来启用多楼层能力时，不能只重新暴露 `navigate_to_floor`。至少需要补充：

- 楼梯、电梯和坡道等跨层设施模型；
- 楼层与地图 frame 的切换协议；
- 跨层定位连续性和失效恢复；
- 电梯门、载重、通信中断等安全状态；
- 跨层任务图条件分支和完成证据；
- 仿真、故障注入和实机安全验证。

## OpenClaw Analogue

OpenClaw 没有对应的物理空间范围合同。这是具身机器人特有的安全和部署边界，
因此这里只复用 OpenClaw 的 Tool/Skill 暴露与 Agent loop 结构，不复用其
设备控制假设。
