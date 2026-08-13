# P0 实机安全闭环：实体底盘验收接续

## 2026-08-13T09:56:22+08:00 — 现场资源发现与阻塞确认

### 任务目标

用户要求完成 P0 第五项剩余的真实厂商接口绑定和实体机器人正反例验收。此前已经完成
`hardware_stop_v1` Provider、Gateway fail-closed 校验、两阶段恢复、Profile 模板、自动反例测试和
全仓回归；本次目标不是重复模拟测试，而是取得特定实体底盘的真实硬件证据。

### 已读取和检查

- 最近两个 memory 日期：`2026-08-12`、`2026-08-11`；
- `memory/2026-08-12/user-experience-robustness-review.md`；
- `memory/2026-08-12/resource-admission-recovery.md`；
- `memory/2026-08-11/navigation-runtime-lifecycle.md`；
- 当前 `git status --short`，保留所有既有未提交修改；
- CodeGraph：`HardwareSafetyConfig`、`HardwareStopEvidenceProvider`、
  `Ros1HardwareSafetyObserver`、ROS1 smoke artifact 路径；
- `docs/deployment/ros1-hardware-smoke-proof.md`；
- `docs/deployment/ros1-hardware-smoke-artifact.schema.json`；
- `src/fireclaw_core/ros/ros1_smoke_artifacts.py`；
- workspace、本机 `/opt`、`/etc`、`/var/lib`、ROS process、设备节点和 home 下可能存在的 Profile。

### 运行命令与观察

```text
ROS_MASTER_URI=http://localhost:11311
rostopic list -> ERROR: Unable to communicate with master!
rosservice list -> ERROR: Unable to communicate with master!
rosnode list -> ERROR: Unable to communicate with master!
```

本机有 ROS Noetic CLI，但没有运行中的 `roscore`、`rosmaster`、Gazebo 或机器人节点。未发现：

```text
/opt/firebot
/etc/fireclaw
/var/lib/fireclaw
/home/lpp/.config/fireclaw
/dev/serial/by-id
/dev/ttyACM0
/dev/ttyUSB0
```

home 下找到的 FireClaw deployment 全部位于
`/home/lpp/.fireclaw/deployments/gazebo-turtlebot3-burger/`，属于 Gazebo 历史 release。仓库中唯一
具体 Robot Profile 是 `examples/robot_profiles/gazebo_turtlebot3.toml`；
`examples/deployment_profiles/navigation_robot.toml.example` 仍是 `/opt/firebot` vendor 占位模板。

根 `fireclaw.toml` 不是真实 Robot Profile：它没有 `[robot]` 表，`robot_gateway.profile_path` 仍指向
Gazebo Profile 且 `dry_run=true`。只读运行
`fireclaw status --profile fireclaw.toml --timeout 5 --json` 按设计返回
`status_command_invalid: robot profile requires a [robot] table`。

### 当前结论

当前机器没有可绑定或可操作的真实底盘。因此无法执行以下物理验收，也不能用 deterministic observer、
Gazebo 或手写 JSON 冒充：

- 硬件 stop service 实际触发与确认；
- watchdog 在 FireClaw/Gateway/网络失效时的独立 stop；
- 物理 e-stop、driver disable、brake engaged；
- 全执行器真实 `JointState` inventory 与连续静止；
- 独立 `Odometry` 连续静止；
- still-moving、stale/disconnected signal 等受控反例；
- 现场安全观察员签字和固件/ROS graph 证据归档。

本轮只执行只读发现，没有启动服务、调用 stop/recover、清除冻结、提交任务、接触硬件或修改用户配置。
默认 Gazebo Profile 的既有持久安全冻结继续保留。

### 恢复工作所需的最小输入

用户需要提供下列二选一：

1. 在真实机器人主机上打开同一 workspace/session，并给出已填写的 real Profile 路径；或
2. 把已填写的 real Profile 安全地放入本 workspace，并提供可达的 ROS master/厂商 setup 环境。

Profile 必须给出真实 robot ID、vendor setup/bringup，以及
`fireclaw.safety.ros1-hardware` 的 stop service、watchdog、physical e-stop、driver、brake、完整 actuator
names 和 independent odometry 绑定。不要把凭据写入消息或提交仓库。真正触发硬件 stop/故障注入前，
还需操作员明确确认机器人已进入厂商规定的安全测试模式、测试区清空、物理急停可用且安全观察员在场。

### 下一步

取得真实 Profile 和现场安全确认后：先执行只读 ROS graph/Profile preflight；再按
`ros1-hardware-smoke-proof.md` 从轮子离地/安全模式开始；依次做正证据、still-moving、stale signal、
Gateway/network loss、两阶段 recover；保存非敏感 artifact；最后运行回归并更新本记录。任何一项不满足
都保持 fail-closed，不能以重试或删状态通过。

## 2026-08-13T11:06:30+08:00 — 无真机阶段的可填写、可执行验收闭环完成

### 用户决定与本轮目标

用户确认当前只有仿真、尚无真实机器人，希望现在完成所有可准备工作，使未来取得底盘后能够填写真实
接口并直接测试。由此把“实体底盘验收接续”拆成两个可审计结论：

1. 当前完成工程侧的 Profile、非致动预检、引导式现场场景、证据和汇总工具；
2. 真实硬件/固件上的物理结论仍必须等底盘到场，不能以仿真或假 ROS 宣称通过。

OpenClaw 没有实体底盘 watchdog、急停、驱动断能和全执行器静止证明的对应模块。本轮没有为已有
OpenClaw 功能重新发明结构；沿用 FireClaw 已有 CLI、Plugin service、content-addressed artifact 和
Gateway `hardware_stop_v1` 边界，现场硬件验收部分作为消防机器人特有扩展。

### 已实现行为

- 新增顶层命令 `fireclaw hardware-safety`：
  - `preflight --offline`：只做静态 Profile/文件/Plugin/部署计划验证，最高返回 `PREPARED`；
  - `preflight`：读取 ROS master、stop service 类型、topic 类型、字段、freshness、最少样本、保持窗口和
    actuator inventory，不调用 stop，全部通过才返回 `READY`；
  - `accept`：一次只验收一个场景，要求 operator ID、精确固件版本和绑定 robot/scenario/Profile SHA 的
    完整确认短语，不提供 `--yes`；
  - `verify`：校验 artifact SHA、必填字段、身份、实时 preflight，并重新计算场景结果；
  - `report`：只汇总同一 robot、Profile SHA、固件版本，要求所有目标信号分别通过。
- 唯一致动场景是 `stop_proof`，只调用 Profile 中厂商/底盘拥有的硬件 stop service。
- 所有负向场景均为 observe-only；FireClaw 不主动断 watchdog、释放急停、使能 driver、松 brake 或制造
  运动：
  - `watchdog_loss`；
  - `emergency_stop_inactive`；
  - `driver_enabled`；
  - `brake_disengaged`（只在 Profile 声明 brake required 时）；
  - `actuator_motion`，分别要求 `actuators` 和 `independent_motion`；
  - `signal_stale`，分别要求 watchdog、e-stop、driver、actuators、independent motion，以及可选 brake；
  - `inventory_incomplete`。
- watchdog loss 不只要求 safety gate 拒绝，还要求 fresh watchdog stop asserted、driver disabled、完整
  actuator 与 independent odometry 在至少 3 个样本和 0.75 秒窗口内静止。
- `Ros1HardwareSafetyObserver` 现在会按既有 ROS Runtime 模式安全初始化 `rospy` 节点；新增
  `collect_hardware_state()` 非致动采集与 `preflight()`。
- observe-only 证据使用 `hardware_safety_observation_v1`，不会被 Gateway 误当成可解除冻结的
  `hardware_stop_v1`。
- 在任何允许的硬件 stop 调用前，先认领一次性 artifact run 目录；目录不可写时不发送硬件命令。若采集后
  最终落盘失败，保留 incomplete run claim 并明确要求重复测试，不能报告成功。
- artifact 目录/文件分别收紧为 `0700`/`0600`；`acceptance.json` 和 manifest 均有 SHA-256。verify 会
  重新判定 evidence，不能仅修改 `passed` 后重算摘要。普通本地 SHA 不是设备签名，正式部署仍可接
  deployment key、TPM/HSM 或远端不可变审计服务。
- Profile 模板新增 `[hardware_safety_acceptance]` 人工审查门：`profile_reviewed`、reviewer、带时区时间和
  vendor manual/revision；占位值不能通过 preflight。

### 主要文件

- 新增 `src/fireclaw_core/infra/hardware_safety_acceptance.py`；
- 修改 `extensions/ros1-hardware-safety/plugin/hardware_safety.py`；
- 修改 `src/fireclaw_core/mission/mission_cli.py`、`src/fireclaw_core/__main__.py`；
- 修改 `examples/deployment_profiles/navigation_robot.toml.example`；
- 新增 `docs/deployment/real-robot-hardware-safety-acceptance.md`；
- 新增 `docs/deployment/hardware-safety-acceptance-artifact.schema.json`；
- 更新 `README.md`、ROS1 deployment/smoke 文档和 Plugin README；
- 新增 `tests/test_hardware_safety_acceptance.py`，扩展
  `tests/test_ros1_hardware_safety_plugin.py`。

### 验证结果

```text
python -m py_compile ...                                      -> pass
python -m json.tool hardware-safety-acceptance-artifact...   -> pass
git diff --check                                             -> pass
pytest -q tests/test_hardware_safety_acceptance.py
          tests/test_ros1_hardware_safety_plugin.py          -> 19 passed
pytest -q tests/test_mission_cli.py                           -> 42 passed
pytest -vv tests/test_[a-e]*.py                               -> 291 passed
pytest -q                                                    -> 2124 passed, 7 skipped in 176.85s
```

一次较早的分组回归中，既有 Gateway admin emergency-stop 并发用例把 task terminal 短暂读成 `lost`，该
用例单独复跑通过，最终全量回归也通过。未修改该 task terminal 路径；保留此观察作为既有竞态信号，不把
首次失败删除或误称从未发生。

最初对长 pytest 输出的轮询方式遗漏了内部 PTY session，曾误判测试在约 10% 被外部终止；用正确
`write_stdin` 会话轮询后取得上述完整退出码 0 和最终统计。

### 当前结论与剩余边界

“无真机阶段可准备的软件闭环”已经完成。现在可以复制 real Profile、填写厂商接口和审查信息，并运行
`hardware-safety preflight --offline`；取得机器人后按文档直接运行 live preflight 和逐场景 accept，最后
report。

P0 第五项仍不能标成“实体现场已闭合”：当前没有真实底盘、厂商固件、物理安全观察员和现场反例证据。
工程实现通过不等于功能安全认证，也不构成研究创新性或论文级安全结论。最终关闭条件是目标实体在同一
Profile SHA/固件版本下得到 `hardware-safety report: PASSED`，随后再执行 ROS1 导航 smoke。默认 Gazebo
Profile 的持久安全冻结未删除、未绕过、未通过本轮命令恢复。

### 未来拿到真机后的第一组命令

```bash
fireclaw hardware-safety preflight --profile /path/to/firebot.toml --offline
fireclaw hardware-safety preflight --profile /path/to/firebot.toml
fireclaw hardware-safety accept --profile /path/to/firebot.toml \
  --scenario stop_proof --operator-id <id> --firmware-version <exact-version>
fireclaw hardware-safety report --profile /path/to/firebot.toml \
  --firmware-version <exact-version> --artifact-dir results/hardware-safety
```

其余场景和 `--target-signal` 矩阵严格按
`docs/deployment/real-robot-hardware-safety-acceptance.md` 执行；任何失败都保持 blocked，不删除状态或跳过
场景。
