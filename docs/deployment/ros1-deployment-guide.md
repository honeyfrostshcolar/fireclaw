# ROS1 部署指南

更新时间：2026-08-09

## 适用范围

Core ROS1 Adapter 只负责：

- Robot state 与 runtime sensor discovery；
- environment snapshot；
- emergency stop；
- ROS1 transport 的通用可信基础设施。

导航、感知、机械臂等领域动作不在 core Adapter 配置中声明。每个领域 Plugin
注册 typed Tool，并由自己的 Adapter 把 Tool 参数转换为 ROS/SDK 请求。

## Core Adapter 配置

`examples/ros1_configs/gazebo_turtlebot3_move_base.yaml` 展示当前 schema：

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

旧的 `endpoints`、`remap` 和 `targets` 顶层表会被配置解析器拒绝。不要为新
Tool 修改 core YAML；在 Plugin 中定义固定 ROS 地址、类型、输入转换、feedback、
timeout、cancel 和终态映射。

## Plugin 配置

Gateway 只发现 Plugin manifest，并把对应 ID 下的 opaque 配置交给 Plugin：

```toml
[deployment]
mode = "simulation"

[plugins]
paths = ["extensions"]

[plugins.config."fireclaw.navigation.move-base"]
enabled = true
```

导航 Plugin owner 为 `fireclaw.navigation.move-base`，physical Tool
`navigate_to_point` 由 `Ros1MoveBaseBackend` 连接固定的 `/move_base`。
Gateway 和 `RobotAdapter` 都不包含导航名称分支或同名方法回退。

## 启动前检查

```bash
source /opt/ros/noetic/setup.bash
source extensions/navigation-move-base/ros_ws/devel/setup.bash

rostopic list
rosrun tf tf_echo map base_link
rostopic type /move_base/goal
rosservice type /move_base/clear_costmaps
```

先在 FireClaw 外验证机器人 bringup、TF、定位、传感器、导航和急停。随后使用
profile-backed Robot Gateway 和 Mission Gateway；配置示例见
`fireclaw.example.toml` 与
`examples/robot_profiles/gazebo_turtlebot3.toml`。

## 测试层级

普通测试不要求 ROS：

```bash
python -m pytest -q
```

通用 ROS1 transport smoke 仍由 `ros1_smoke` marker 显式开启。它验证
rospy/topic/service/actionlib 基础协议，不等于 Navigation Plugin 的 Gazebo
验收：

```bash
FIRECLAW_RUN_ROS1_SMOKE=1 \
  python -m pytest -q -m ros1_smoke
```

完整点导航仿真使用
[当前架构的 ROS1/Gazebo 导航验收](ros1-gazebo-debugging-guide.md)。实机验证使用
[ROS1 实机 Smoke Proof](ros1-hardware-smoke-proof.md)。

## Fail-closed 条件

出现以下任一情况都不得继续物理执行：

- Plugin manifest 未加载、Tool owner 不匹配或 physical handler 缺失；
- core Adapter 配置含旧领域 endpoint 表；
- ROS graph、TF、传感器 freshness 或 action server 不满足 Plugin 前置条件；
- simulation/real deployment mode 与目标平台不一致；
- timeout、cancel、急停或审计链无法证明；
- 系统尝试通过 `RobotAdapter` 同名方法执行领域动作。
