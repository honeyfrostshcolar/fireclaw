# FireClaw 故障注入验收

这条验收通道用于在没有真实机器人时，重复验证 FireClaw 面对基础设施故障是否会安全失败、是否会产生重复任务，以及故障解除后能否有界恢复。它不发送机器人运动指令。

## 一键运行

在仓库根目录执行：

```bash
fireclaw fault-test run --live-ros
```

`--live-ros` 会启动一个使用随机本机端口的私有 `roscore`，依次验证可达、杀停后不可达和重启后恢复。它不会连接默认 ROS master，也不会启动 Gazebo 或机器人驱动。显式使用 `--live-ros` 但机器缺少 `roscore` 或 `rosnode` 时，该场景直接失败；省略该选项时只验证确定性 ROS 边界，整套报告标为 `prepared`，不能当作完整通过。

只运行一个或多个场景：

```bash
fireclaw fault-test run \
  --scenario database_lock \
  --scenario duplicate_command
```

默认产物写入 `results/fault-injection/<run_id>/`。每次运行占用全新的目录，不覆盖旧证据。验证产物：

```bash
fireclaw fault-test verify \
  --run-dir results/fault-injection/<run_id>
```

## 故障矩阵

| 场景 | 注入方式 | 必须证明的结果 |
|---|---|---|
| `network_disconnect` | 回环 HTTP SSE 连接在非终态事件后断开 | 客户端按 cursor 续播、不重复显示事件、不修改远端任务 |
| `gateway_crash` | 第一个真实 Gateway 测试子进程以非零码退出 | supervisor 发现退出、逆序清理；仿真有界重启，实机不自动重启并冻结准入 |
| `ros_master_restart` | ROS 图查询失败；可选私有 `roscore` 杀停和重启 | 断开期间状态降级，不产生假 readiness，重启后图查询恢复 |
| `disk_full` | 限制隔离 SQLite 的最大页数，触发真实 `SQLITE_FULL` | 返回 `runtime_storage_full`，不启动 worker，不留下半提交任务 |
| `database_lock` | 独立 SQLite 连接持有写锁 | 在短超时内返回 `runtime_database_locked`；解锁后相同 dedupe key 可安全重试 |
| `sensor_failure` | 必需 lidar 提供无有效量程的健康证据 | Safety Gate 在动作前阻塞导航 |
| `duplicate_command` | 八个线程同时提交同一个 dedupe key | 只产生一个 task ID、一个任务记录和一次 worker 启动 |

每个场景共同检查：故障被观察、危险执行未启动、没有重复或半提交状态、恢复有界或保持 fail-closed、证据可持久验证。

## 状态含义

- `passed`：所选场景全部通过；选择 ROS master 场景时，私有 `roscore` 进程测试也已执行。
- `prepared`：确定性 ROS 边界通过，但没有使用 `--live-ros` 做真实进程重启；不能当作完整 P1 验收。
- `failed`：至少一个场景失败或测试进程无法完成。

命令成功只表示软件/仿真层故障闭环成立。拥有真实机器人和部署网络后，仍应补做现场层验证：真实网卡分区与抖动、目标磁盘/文件系统耗尽、实际 ROS 主机重启、传感器拔线，以及底盘 watchdog/急停对运动的物理约束。现场结果不能由本通道推断。

## Gateway 存储故障行为

权威 SQLite 被锁或写满时，Gateway 对新任务返回 HTTP `503` 和结构化 `status=unavailable`。响应同时明确 `task_execution_started=false`，并在 `/state` 的 `runtime_storage` 中暴露阻塞原因。统一 `fireclaw status` 将该状态投影为 `blocked`，不会因为 `/health` 仍然存活而报告 READY。

故障解除后，操作员应使用原 `dedupe_key` 重试；禁止更换 key 来“绕过”不确定的提交结果。
