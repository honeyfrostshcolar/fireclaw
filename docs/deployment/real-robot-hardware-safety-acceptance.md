# 实机硬件安全验收（无真机准备版）

更新时间：2026-08-13

这套流程的目标是：现在把接口、命令、门禁、验收场景和证据格式准备好；拿到
真实机器人后只填写一份 Profile，在厂商规定的安全测试环境中逐项执行。

当前没有真机时，最高只能得到 `PREPARED`，不能宣称实机安全闭环已经现场通过。
只有非致动实时预检为 `READY`，并且同一 Profile 摘要、同一固件版本下的所有
现场场景都通过，`report` 才返回 `PASSED`。

## 现在需要准备的文件

复制 `examples/deployment_profiles/navigation_robot.toml.example` 到部署机器的私有
配置目录，不要直接修改示例或把现场地址、凭证提交到仓库。逐项填写：

1. `[robot]`：真实 `id`、Gateway 地址、ROS1 adapter 配置和数据目录。
2. `[deployment.ros1]`：ROS distro 以及基础 ROS、厂商 workspace 的 setup 文件。
3. `[deployment.launch]`：厂商驱动、硬件 watchdog、传感器和 TF 的 bringup launch。
4. `stop`：底盘或安全控制器拥有的幂等停止服务及准确类型。它不能只是
   FireClaw 进程里的 `/cmd_vel=0` 包装。
5. `watchdog`：硬件 watchdog 的健康状态和“已独立断能/停车”状态字段。
6. `emergency_stop`、`driver`、可选 `brake`：权威状态 topic、消息类型和布尔字段。
7. `actuators.expected_names`：所有能产生危险运动或喷射的执行器；若厂商没有统一
   `JointState`，应在可信 robot-side adapter 中汇总，不能为了通过验收漏掉水炮、
   泵、机械臂或履带。
8. `independent_motion`：不依赖 FireClaw 命令通道的实际运动观测，优先来自底盘
   反馈、编码器/IMU 融合或独立安全控制器。
9. `[hardware_safety_acceptance]`：完成逐字段审查后才把
   `profile_reviewed=true`，并填写审查人、带时区时间和厂商手册/版本。

任何 `REPLACE_ME` 都表示未完成，不允许进入现场验收。

## 拿到机器人前可运行

```bash
fireclaw hardware-safety preflight \
  --profile /path/to/firebot.toml \
  --offline
```

该命令不连接 ROS，也不发送硬件命令。它验证真实模式、robot/deployment 身份、
文件、ROS adapter 配置、Plugin 所有权与信任级别、硬件安全配置，以及不可变部署
计划。成功结果是 `PREPARED`；这是“配置可以带到现场”的证明，不是实机证明。

## 第一次连接真机

先启动厂商 bringup，但不要启动自主任务。机器人应位于隔离测试区、轮子离地或
进入厂商规定的安全测试模式，现场必须有安全观察员和可直接操作的物理急停。

```bash
fireclaw hardware-safety preflight \
  --profile /path/to/firebot.toml
```

实时预检只读取 ROS master、服务/topic 类型和样本结构，不调用停止服务，也不
发布运动命令。类型、字段、关节清单、消息 freshness 任一不符都会返回
`BLOCKED`。

## 逐场景验收

每次只运行一个场景：

```bash
fireclaw hardware-safety accept \
  --profile /path/to/firebot.toml \
  --scenario stop_proof \
  --operator-id operator-01 \
  --firmware-version vendor-fw-1.2.3
```

命令会显示包含 robot ID、场景和 Profile 摘要的精确确认短语。不存在 `--yes`
快捷方式。确认代表操作员已经核实测试区清空、厂商安全测试模式、物理急停和独立
观察员。脚本模式可用 `--json` 先取得短语，再用
`--confirmation-phrase '<exact phrase>'` 重跑。

只有 `stop_proof` 会调用配置中的厂商硬件停止服务。其余场景只观察；操作员必须
按厂商规程建立条件，FireClaw 不会主动断 watchdog、释放急停、使能驱动、松开
制动或制造运动。

| 场景 | 现场建立的条件 | FireClaw 必须证明 |
|---|---|---|
| `stop_proof` | 允许安全停止，必要时先按下物理急停 | 停止被确认，watchdog stop 已断言，驱动关闭，制动锁定（如有），全执行器与独立运动均静止 |
| `watchdog_loss` | 按厂商方法隔离控制心跳 | watchdog 失去健康但独立 stop 生效，驱动关闭且机器人静止 |
| `emergency_stop_inactive` | 急停状态为未按下 | 安全门准确拒绝“急停已激活”的错误证明 |
| `driver_enabled` | 驱动仍使能但平台保持安全 | 安全门准确拒绝恢复 |
| `brake_disengaged` | 有制动的平台处于未锁定 | 安全门准确拒绝恢复；无制动平台不要求此项 |
| `actuator_motion --target-signal actuators` | 厂商安全模式下产生可控的关节反馈运动 | `JointState` 运动被检出 |
| `actuator_motion --target-signal independent_motion` | 厂商安全模式下产生可控的平台运动反馈 | 独立里程计运动被检出 |
| `signal_stale --target-signal <name>` | 仅隔离指定状态源 | 指定源陈旧/缺失被拒绝；每个必需状态源都要单独测 |
| `inventory_incomplete` | 测试 adapter 发出缺关节或多未知关节的样本 | 执行器清单不完整被拒绝 |

`signal_stale` 的 `<name>` 必须逐项使用：`watchdog`、`emergency_stop`、
`driver`、`actuators`、`independent_motion`；配置了硬件制动时还包括 `brake`。

每次确认后的尝试，无论通过还是失败，都会写入：

```text
results/hardware-safety/<robot_id>/<run_id>/
  .evaluation-run.json
  acceptance.json
  artifact-manifest.json
```

在发送唯一允许的硬件 stop 前，命令会先认领并落盘一次性 run 目录；若证据目录
不可写，硬件命令不会发送。`acceptance.json` 含内容摘要，`artifact-manifest.json`
再记录文件级摘要。验证时还会重新计算场景结果，不能只修改 `passed` 后重算摘要。
可单独验证：

```bash
fireclaw hardware-safety verify \
  --artifact results/hardware-safety/<robot_id>/<run_id>/acceptance.json
```

## 最终闭环判定

```bash
fireclaw hardware-safety report \
  --profile /path/to/firebot.toml \
  --firmware-version vendor-fw-1.2.3 \
  --artifact-dir results/hardware-safety
```

报告不会混用其他机器人、其他 Profile 摘要或其他固件版本的证据。Profile 的任意
字节或固件版本改变后，必须重新验收。缺一个目标信号、摘要不匹配、证据被改写或
最新一次场景失败，报告都保持 `BLOCKED`。

当前 SHA-256 用于内容完整性和误改检测，不是设备身份签名。正式生产若需要抵抗
有权限修改本机文件的攻击者，应再把证据摘要交给部署密钥、TPM/HSM 或远端不可变
审计服务签名；不要把本地普通摘要当成硬件认证。

通过验收只证明 FireClaw 能正确识别和保守处理这些硬件状态；它不替代平台的
功能安全认证、厂商维护规程或现场风险评估，也不会自动清除物理急停或持久化安全
冻结。
