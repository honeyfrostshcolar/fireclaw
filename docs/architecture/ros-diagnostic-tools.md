# ROS Diagnostic Tools

## Goal

Robot Agent 在导航超时或局部执行失败后，需要先取得现场证据，再决定重试、
改参数、切换方案或上报。它不能只收到一个 `timeout`，也不能获得任意 shell
或不受限的 `rostopic`。

FireClaw 因此提供机器人本地的 typed ROS1 diagnostic Tools：

| Tool | 只读结果 |
| --- | --- |
| `ros_topic_list` | 白名单内 topic 和 message type |
| `ros_topic_info` | 单个 topic 的 publishers、subscribers 和 type |
| `ros_topic_sample` | 最多若干条结构化消息样本 |
| `ros_topic_rate` | 短时间窗口内的发布频率 |
| `tf_lookup` | 一次 frame transform 观测 |
| `move_base_status` | 结构化 actionlib goal 状态 |
| `navigation_diagnostics` | move_base、scan、odom、cmd_vel 和 TF 的并行排查 |

这些 Tool 只投影给 `robot_agent`，在 `simulation` 和 `real` 模式中使用同一
合同。Mission Agent 不直接连接 ROS master。

## Execution Boundary

```text
Robot Agent LLM
  -> typed Tool Call
  -> AgentToolRuntime role/mode/allow-deny policy
  -> before_tool_call final-argument validation
  -> ROS topic/frame/action allowlist
  -> robot-local Ros1DiagnosticsBackend
  -> backend-owned fixed argv, no shell
  -> bounded structured advisory result
  -> next Robot Agent reasoning turn
```

LLM 只能填写 topic、frame、采样数和超时等 schema 字段。它不能填写 executable、
subcommand、shell 字符串、ROS publish payload、service request 或 parameter
mutation。

backend 内部可以使用 `rostopic` 和 `rosrun tf tf_echo`，但 argv 由代码固定
构造，`shell=False`。子进程同时受以下硬限制：

- topic、frame、action allowlist；
- 最多 topic 返回数；
- 最多消息样本数；
- 每次观测最大时间；
- stdout/stderr 合并后的最大字节数；
- 进程组超时终止；
- YAML alias 禁用、解析深度和集合大小限制。

## Authority

每个结果均包含：

- `robot_id`；
- `source: ros1_cli_diagnostics`；
- `authority: advisory`；
- `read_only: true`；
- `observed_at`、`duration_seconds` 和 `evidence_id`；
- timeout、output byte cap、截断和 exit code 信息。

这些结果会进入下一轮 Robot Agent 推理，也会写入
`agent_tool.execution` 审计事件，但不会自动成为权威现场状态。若某项诊断
需要影响 SafetyGate、任务合同或 Mission Agent 的正式 belief，必须经过单独的
observation ingestion、来源验证和状态更新流程。

## Navigation Triage

`navigation_diagnostics` 用一个 Tool Call 并行读取：

- `/move_base/status`；
- `/scan`；
- `/odom`；
- `/cmd_vel`；
- `map -> base_link` TF。

它可产生例如：

- `laser_stream_unavailable`；
- `odometry_stream_unavailable`；
- `robot_transform_unavailable`；
- `navigation_action_failed`；
- `planner_not_commanding_motion`；
- `possible_navigation_stall`。

`possible_navigation_stall` 的含义是：move_base 仍为 active，有限样本中存在
非零速度命令，但 odometry 位移接近零。它是有限窗口内的低置信度诊断线索，
不是机械故障的最终证明。

## Configuration

ROS1 adapter JSON 可配置：

```json
{
  "robot_id": "robot-1",
  "diagnostics": {
    "enabled": true,
    "topic_allowlist": [
      "/scan",
      "/odom",
      "/tf",
      "/tf_static",
      "/cmd_vel",
      "/move_base",
      "/move_base/*"
    ],
    "frame_allowlist": ["map", "odom", "base_link", "base_footprint"],
    "action_allowlist": ["/move_base"],
    "max_topics": 100,
    "max_samples": 3,
    "max_timeout_seconds": 3.0,
    "max_output_bytes": 32768
  }
}
```

allowlist 支持由可信配置给出的 glob pattern。LLM 不能修改 allowlist。YAML
兼容加载器当前可使用逗号分隔字符串表示这些列表。

## Non-Goals

本模块不提供：

- `rostopic pub`；
- `rosservice call`；
- `rosparam set/delete`；
- 任意 `roslaunch` 或 shell；
- 直接速度控制；
- 自动把 advisory 诊断提升为权威事实；
- Mission Agent 到 ROS master 的直接远程访问。

物理动作继续通过 Physical Skill、capability policy、SafetyGate、资源锁和
执行授权链。
