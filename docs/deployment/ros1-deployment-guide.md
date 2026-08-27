# ROS1 部署指南

更新时间：2026-08-13

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

推荐使用统一 Plugin Runtime 部署器完成 provider 探测、source fallback 构建、environment、
bringup 和 readiness：

```bash
fireclaw deploy plan --profile /opt/firebot/fireclaw.real.toml
fireclaw deploy apply --profile /opt/firebot/fireclaw.real.toml

/var/lib/fireclaw/deployments/firebot-01/current/bin/fireclaw-bringup
/var/lib/fireclaw/deployments/firebot-01/current/bin/fireclaw-gateway
```

完整 profile schema、生成目录和失败语义见
[Plugin Runtime 统一部署](plugin-runtime-deployment.md)。以下手工命令保留用于调试或尚未接入
Runtime descriptor 的旧部署。

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
`fireclaw.sim.example.toml` 与
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
[实机硬件安全验收](real-robot-hardware-safety-acceptance.md)，通过后再执行
[ROS1 实机 Smoke Proof](ros1-hardware-smoke-proof.md)。

## Fail-closed 条件

出现以下任一情况都不得继续物理执行：

- Plugin manifest 未加载、Tool owner 不匹配或 physical handler 缺失；
- core Adapter 配置含旧领域 endpoint 表；
- ROS graph、TF、传感器 freshness 或 action server 不满足 Plugin 前置条件；
- simulation/real deployment mode 与目标平台不一致；
- timeout、cancel、急停或审计链无法证明；
- 系统尝试通过 `RobotAdapter` 同名方法执行领域动作。

timeout 或 cancel 进入 `physical_runtime_stop_unconfirmed` 后，资源冻结会跨进程重启保留。
不要删除状态文件解锁。使用
[Plugin Runtime 统一部署：持久安全冻结恢复](plugin-runtime-deployment.md#持久安全冻结恢复)
中的两阶段流程；ROS bringup 必须保持在线，可信 witness 才能重新触发 stop 并采集 action、
速度和里程计证据。当前 move_base witness 仅适用于仿真，实机必须由硬件集成提供覆盖整机执行器
的停止证明。生产实机应启用 `fireclaw.safety.ros1-hardware`，并按
`fireclaw.real.example.toml` 绑定硬件 watchdog、急停、driver、
制动、完整 `JointState` 执行器清单和独立 `Odometry`。Gateway 只接受 `hardware_stop_v1` 的
结构化正证据；普通 ROS 节点存活、零 `cmd_vel` 或导航 action idle 都不足以解冻。
拿到真机前先运行 `fireclaw hardware-safety preflight --offline`；现场的
`preflight / accept / verify / report` 会把配置、ROS 合同、逐信号负向场景和证据摘要组成
一个闭环，但不会主动制造负向硬件状态。
