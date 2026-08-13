# Plugin Runtime 统一部署

更新时间：2026-08-12

## 目标

FireClaw 现在把 Plugin 的“发现与调用”和 ROS Runtime 的“安装与启动”连接成一条显式、
可审计的部署链。目标体验是：

```text
新机/升级时：deploy plan -> deploy apply
每次启动时：fireclaw-runtime（受管 bringup -> Robot Gateway -> Mission Gateway）
```

用户不再进入每个 Plugin 目录分别 `catkin_make`、逐个 `source`、逐个启动节点。构建仍只发生
在部署/升级阶段，不发生在任务执行阶段。

## 当前边界

第一版支持两种 data-only provider：

- `system_ros1`：复用已在配置 ROS overlay 中可见的兼容 package；
- `ros1_catkin`：当 system package 不完整时，把 Plugin 声明的源码 package 汇入一个统一、
  独立的 Catkin workspace，并生成 install space。

Runtime descriptor 不允许携带 shell 命令、下载 URL、系统包安装命令或 LLM 提供的路径。
`apply` 只执行 FireClaw 内置的固定 Catkin argv。当前不会自动运行 `apt`、联网下载、修改
`systemd` user 配置，也不会替用户安装 ROS、底盘驱动或硬件 SDK。部署产物包含单进程
supervisor 和经过内容校验的 systemd user unit；安装/enable/start 仍是操作员显式动作。

部署器也拒绝把 `api_key`、`password`、`secret`、`token` 等内联凭据复制进生成的
`fireclaw.generated.toml`；使用环境变量引用或受保护的文件引用，并保持真实凭据不进入
profile、lock 和 receipt。

## Plugin 声明

Plugin manifest 可选引用 Runtime 描述文件：

```json
{
  "id": "fireclaw.navigation.move-base",
  "entrypoint": "plugin/entrypoint.py",
  "runtime": "runtime/fireclaw.runtime.json"
}
```

Navigation Plugin 的实际描述位于
`extensions/navigation-move-base/runtime/fireclaw.runtime.json`。它声明：

- ROS Noetic 与支持的 CPU 架构；
- system `move_base`、`map_server`、`amcl` 探测条件；
- bundled `ros_ws/src/navigation` fallback package 集合；
- 固定 production launch fragment；
- launch 引用的 Plugin-owned 默认 YAML assets 及其内容哈希；
- map、scan topic、TF frame、footprint、速度限制等 binding；
- `/move_base` action、ROS node/topic、TF readiness。

该 JSON 只含数据。Plugin discovery 不会因为读取它而导入 Plugin 代码或执行构建。

## 根目录单一配置

Runtime 部署配置、Robot capability profile 与 Gateway 配置共用 FireClaw 根目录的
`fireclaw.toml`。先复制 `fireclaw.example.toml`，换机器人或地图时修改这一份文件即可。
面向 `/opt/firebot` 安装布局的补充模板见
`examples/deployment_profiles/navigation_robot.toml.example`。核心部分如下：

```toml
[deployment]
id = "firebot-01"
mode = "real"
# output_root = "/var/lib/fireclaw/deployments"

[deployment.ros1]
distro = "noetic"
setup_files = [
  "/opt/ros/noetic/setup.bash",
  "/opt/firebot/vendor_ws/devel/setup.bash",
]

[deployment.launch]
robot_launch = "/opt/firebot/launch/robot_base.launch"

[deployment.bindings.navigation]
map_file = "/opt/firebot/maps/site.yaml"
scan_topic = "/scan"
scan_frame = "laser"
cmd_vel_topic = "/cmd_vel"
odom_topic = "/odom"
map_frame = "map"
odom_frame = "odom"
base_frame = "base_link"
footprint = [[-0.30, -0.24], [-0.30, 0.24], [0.30, 0.24], [0.30, -0.24]]
max_linear_velocity = 0.45
max_angular_velocity = 1.20
max_linear_acceleration = 0.75
max_angular_acceleration = 1.50
laser_max_range = 12.0

[plugins]
paths = ["../../extensions"]
selected = ["fireclaw.navigation.move-base"]
```

Robot Profile 的 `robot.ros1_config` 和 `robot.data_dir` 相对路径以该 TOML 文件所在目录为
基准；Runtime deployment 的 setup、Plugin、launch 和 typed path binding 也在 plan 阶段按
Profile 目录解析。因此生成的 Gateway 不依赖启动 shell 的当前目录。生产部署仍建议对
Robot Adapter 配置、data directory、地图、模型、vendor workspace 和 launch 使用稳定的
绝对安装路径，便于审计和迁移。

`robot_launch` 负责硬件基础层，例如底盘驱动、watchdog、激光雷达、里程计、
`robot_state_publisher` 和基础 TF。Navigation Plugin 自己的
`fireclaw_navigation.launch` 统一启动 map_server、AMCL 和 move_base，并加载 Plugin 内的
AMCL/costmap/DWA 默认 YAML。机器人不再提供 `navigation_stack.launch`；地图、topic、TF、
footprint、速度/加速度、传感器范围等差异通过上述 typed binding 注入。两层会被组合成一个
生成的顶层 launch。

## 命令

先只读解析方案：

```bash
fireclaw deploy plan --profile /opt/firebot/firebot.toml
```

`plan` 会验证 manifest/descriptor、ROS distro/CPU、binding、重复 package 和 provider 选择，
并输出内容指纹与动作清单；它不会创建部署目录或编译代码。

生成的 Gateway wrapper 固定使用执行 `deploy apply` 时的 Python 解释器，通过
`python -m fireclaw_core` 调用 readiness 与 Gateway 命令，不依赖开机 shell 的 `PATH` 中是否
存在裸 `fireclaw`。解释器路径和生成器版本都会进入 deployment fingerprint；生成逻辑变化后
不会静默复用旧 wrapper。

显式应用：

```bash
fireclaw deploy apply --profile /opt/firebot/firebot.toml
```

若 system overlay 已提供全部要求 package，`apply` 不重复构建 Navigation Stack。否则它在
内容寻址 release 下构建统一 Catkin install space。失败日志和失败收据保留在 `failures/`，
不会切换 `current`。

静态核验安装产物：

```bash
fireclaw deploy status \
  --profile /opt/firebot/firebot.toml \
  --no-runtime-check
```

日常启动使用生成的统一 supervisor：

```bash
/var/lib/fireclaw/deployments/firebot-01/current/bin/fireclaw-runtime
```

它固定执行以下生命周期：

1. 核对 active release 的 receipt 与 artifact hash，并取得部署级单实例锁；
2. 启动前探测已有 ROS graph；健康或部分响应的旧/manual Runtime 会以 generation 0 拒绝，
   不会把域冲突耗成自动重启；
3. 启动 `fireclaw-bringup`，等待 ROS package、声明的 node/topic/action 和 TF readiness 全部通过；
4. 启动 `fireclaw-gateway`，并核对 `/health` 的 robot identity 与 dry-run/real mode；
5. Profile 含 `[server]` 时再启动 `fireclaw-mission-gateway`，核对其轻量 `/health`；可用
   `[deployment.supervisor.mission_gateway] enabled = false` 明确改为外部托管；
6. generation ready 后持续检查三层 readiness；例如 `move_base` 消失而 roslaunch 仍活着也会触发
   `runtime_readiness_lost`；
7. 任一进程意外退出时，按 Mission Gateway -> Robot Gateway -> bringup 的逆序关闭当前
   generation；roslaunch 主进程暴力退出时会按先前追踪的 `/proc` session 排空孤儿 ROS/Gazebo
   子进程，排空失败禁止继续自动重启；
8. `simulation` 可对整组进程做有限指数退避重启；`real` 不自动重启，要求操作员检查现场；
9. 收到 `SIGINT/SIGTERM` 时也按逆序做有界停止，必要时升级为 `SIGTERM/SIGKILL`。

实机的“进程已退出”不等于“机器人已被证明静止”。因此 real supervisor 在意外退出或正常停止后，
会把仍然开放的 `resource_admission` 持久冻结为 `managed_runtime_failure` 或
`managed_runtime_shutdown_unconfirmed`；已存在的更早冻结不会被覆盖。下一次可以先启动整套基础设施，
再通过可信硬件停止证据和操作员确认执行 `fireclaw recover`。冻结不会续跑旧任务。

如需调试，仍可分别运行 `fireclaw-bringup`、`fireclaw-gateway` 与
`fireclaw-mission-gateway`；这种方式不具备统一 ownership、
逆序停止、有限重启和 supervisor lifecycle audit，不作为日常主路径。Gateway wrapper 自身仍会先
执行完整 `deploy status`，不会降级为未经检查的物理执行。

也可以不使用生成 wrapper，直接运行同一 supervisor：

```bash
fireclaw deploy run --profile /opt/firebot/firebot.toml
```

超时、仿真重启次数和停止 grace period 都有显式 CLI 参数；`--simulation-restarts` 在 real mode
中不会开启自动重启。

### systemd user service

release 生成不等于修改宿主服务。操作员可先渲染/检查，再显式安装：

```bash
fireclaw deploy service render --profile /opt/firebot/firebot.toml
fireclaw deploy service install \
  --profile /opt/firebot/firebot.toml \
  --no-enable \
  --no-start
```

不带 `--no-enable/--no-start` 的 `install` 会 enable 并立即 restart。日常控制与核对使用：

```bash
fireclaw deploy service start   --profile /opt/firebot/firebot.toml
fireclaw deploy service status  --profile /opt/firebot/firebot.toml
fireclaw deploy service stop    --profile /opt/firebot/firebot.toml
fireclaw deploy service restart --profile /opt/firebot/firebot.toml
fireclaw deploy service uninstall --profile /opt/firebot/firebot.toml
```

安装器只接受 active、完整性校验通过且与当前 Profile 精确一致的 unit；原子写入 0600 文件，拒绝
符号链接、非普通文件和非 FireClaw 管理的同名 unit。`simulation` unit 只在 supervisor 本身意外
崩溃时使用 `Restart=on-failure`；配置拒绝（exit 2）和 supervisor 已耗尽内部恢复预算（exit 78）
不会形成第二层无限重启。`real` unit 固定 `Restart=no`。systemd 发送 SIGINT，并给 supervisor
120 秒完成反向停止和必要的孤儿进程排空。

可选环境文件位于 `<deployment-root>/state/runtime.env`，unit 不会自动创建它，也不会把 secret
写入生成产物。若部署方使用该文件，应限制为 0600，并只放受运维管理的环境引用。

也可独立检查 live ROS graph：

```bash
fireclaw deploy status --profile /opt/firebot/firebot.toml
```

面向日常操作员，优先使用聚合部署、ROS readiness、Robot Gateway、资源准入、托管服务、能力
readiness 与 Fleet Doctor 的单一入口：

```bash
fireclaw status --profile /opt/firebot/firebot.toml
```

该命令默认输出人类可读的 `READY/DEGRADED/BLOCKED/OFFLINE` 判断；自动化使用 `--json`。Fleet
Doctor 的 URL 默认从 Profile 解析，外部托管场景可使用 `--server` 覆盖；Mission Gateway 探测失败
会阻止 false READY，但不会覆盖更高优先级的本地急停或资源冻结原因。
`GET /health` 只代表 Gateway 进程存活，不能代替该 readiness 判断。完整合同见
[操作员状态、诊断与冻结恢复](operator-readiness-recovery.md)。

## 生成目录

默认根目录是 `~/.fireclaw/deployments/<deployment-id>/`；生产环境通常在 profile 中覆盖为
受运维管理的路径。

```text
<deployment-id>/
├── current -> releases/<fingerprint>
├── deployment-receipt.json
├── state/
│   ├── runtime-supervisor.lock          # advisory 单实例锁；文件存在不等于锁仍被持有
│   └── runtime-supervisor/run-*/
│       ├── lifecycle.jsonl              # 启停、readiness、退出与安全冻结审计
│       ├── 01-bringup.log
│       ├── 01-gateway.log
│       └── 01-mission_gateway.log
├── failures/
└── releases/<fingerprint>/
    ├── install/                         # 仅 source fallback 时存在
    ├── workspace/                       # 统一 Catkin staging workspace
    ├── setup.bash                       # 单一 ROS environment
    ├── fireclaw_bringup.launch          # robot + selected Plugin launch
    ├── fireclaw.generated.toml           # 绝对 Plugin paths，且不写入凭据
    ├── deployment-lock.json
    ├── runtime-inventory.json
    ├── deployment-receipt.json
    ├── systemd/
    │   └── fireclaw-<deployment-id>.service
    ├── logs/
    └── bin/
        ├── fireclaw-bringup
        ├── fireclaw-gateway
        ├── fireclaw-mission-gateway
        └── fireclaw-runtime            # 日常主入口
```

Release 由 profile、manifest、Runtime descriptor、launch、声明的 Plugin Runtime asset、
system package evidence 或 bundled source hash 共同确定。重新 `apply` 相同指纹时直接复用；
默认 YAML、配置或源码变化会生成新 release，旧 release 保留，`current` 最后原子切换。

## 状态与失败语义

- `ready`：静态产物与 live ROS readiness 均通过；
- `installed`：使用 `--no-runtime-check` 时，静态产物通过；
- `installed_not_ready`：部署有效，但节点/action/topic/TF 尚未 ready；
- `stale`：当前 profile/Plugin/source 与已部署指纹不同，需要重新 `apply`；
- `invalid`：收据、artifact hash 或 ROS package visibility 不一致；
- `not_deployed`：尚无对应部署收据。

`plan/apply/status` 都输出 JSON；`installed_not_ready` 返回退出码 1，配置/产物错误返回退出码 2。
`deploy run` 是前台长驻命令，生命周期事件写 stderr，结束时在 stdout 输出结果 JSON；操作员停止返回
0，supervisor 策略/恢复预算失败返回 78，配置或部署拒绝返回 2。`fireclaw status` 会显示最近一次 supervisor 状态和日志目录，
但历史日志本身不被当作当前进程 liveness 证据。

## 持久安全冻结恢复

物理 Runtime 在 timeout/cancel 后无法确认停止时，Gateway 会持久关闭
`resource_admission`。重启 Gateway、重启 ROS 或恢复网络都不会自动开闸，也不应删除
`runtime.sqlite3`、`runtime_flags` 或 Robot Profile 的 `data_dir`。恢复采用两个独立步骤：

1. 管理员请求恢复；Gateway 从可信 Plugin Service 主动采集现场停止证据，并创建 5 分钟有效的
   持久恢复请求；
2. 管理员核对证据和冻结来源，提交该请求专属的完整确认短语；Gateway 再次采集新证据，随后在
   一个 SQLite 事务中校验冻结 revision、请求状态、有效期、活动任务和资源租约，最后才恢复
resource admission 并写入审计事件。

操作员首选引导式入口：

```bash
fireclaw recover --profile /opt/firebot/firebot.toml
```

它会显示冻结来源、可信证据、阻塞项、有效期和完整确认短语；没有 `--yes` 快捷方式，确认后还会
重新读取准入状态。下方 REST 示例保留给集成与故障诊断。

先保持 bringup 与 Gateway 运行，读取冻结状态：

```bash
curl -sS http://127.0.0.1:8765/resource-admission | python -m json.tool
```

请求服务器采集停止证据：

```bash
curl -sS -X POST http://127.0.0.1:8765/resource-admission/recovery/request \
  -H 'Content-Type: application/json' \
  -d '{"reason":"operator inspected the scene and requests admission recovery"}' \
  | python -m json.tool
```

只有返回 `status=pending_confirmation` 才会包含本次 `request_id`、证据、过期时间和
`confirmation_phrase`。不要自己构造或缩短短语。确认示例：

```bash
curl -sS -X POST http://127.0.0.1:8765/resource-admission/recovery/confirm \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id":"recovery-...",
    "confirmation_phrase":"RECOVER gazebo_turtlebot3 recovery-..."
  }' \
  | python -m json.tool
```

恢复接口要求独立的 `emergency.recover` scope；当前本机 loopback 或有效 Gateway 管理员 token
会映射为 admin principal。JSON payload 中伪造的 operator role、`stopped=true` 或自带
`stop_evidence` 都不会被采用。错误短语、过期请求、新的冻结 revision、仍在运行的 Gateway
任务、未到期资源租约、证据源异常、观测到运动或证据过期都会保持冻结。

当前 `move_base` 仿真 Plugin 的可信 witness 会：

- 对 `/move_base` 执行 `cancel_all_goals()`；
- 在观测窗口持续向 `/cmd_vel` 发布零 `Twist`；
- 要求 `/move_base/status` 没有 active/pending/preempting/recalling goal；
- 要求 `/odom` 至少 3 个新鲜样本，并连续至少 0.75 秒满足线速度不超过 `0.01 m/s`、角速度
  不超过 `0.02 rad/s`；
- 返回最长 15 秒有效的服务器时间戳证据；确认步骤会重新采集，不能复用请求阶段的旧证据。

该 navigation witness 只在 `simulation` mode 注册。实机使用默认关闭的
`fireclaw.safety.ros1-hardware` Plugin，并必须在 Profile 中显式绑定厂商硬件拥有的 stop、watchdog、
物理急停、driver、制动、完整 `JointState` 执行器清单和独立 `Odometry` 接口。Gateway 只承认
hardware-owned `hardware_stop_v1` 正证据；仅观察 `move_base`、零 `cmd_vel`、缺失/陈旧信号或
不完整执行器清单均保持 fail-closed。完整模板和复位语义见
[操作员状态、诊断与冻结恢复](operator-readiness-recovery.md)。

审计账本会记录 `resource_admission.recovery_requested`、
`resource_admission.recovery_confirmation_denied`、`resource_admission.recovery_blocked` 和
`resource_admission.recovery_expired`、`resource_admission.recovered`。读取状态时如果发现请求已经
超过确认 deadline，会持久转为 `expired` 并只记录一次审计事件，不再把它显示为 pending。
确认短语不会出现在 GET 状态或审计事件中。

## 尚未包含

这是 Navigation 的首个纵向切片，不是最终通用包管理器。后续仍需增加：

- prebuilt deb/install-space/OCI provider 与离线 artifact registry；
- GPU、CUDA、模型权重和 ABI 的 typed compatibility；
- 多 Plugin 部分 readiness 与按 Plugin 隔离暴露；
- systemd system unit、login linger 与发行版/fleet 级安装策略（当前提供的是显式 user unit）；
- 外部 vendor daemon 的 adopted-runtime ownership 与连续 capability health isolation；
- clean-host 重装与实机 deployment acceptance。

这些属于部署工程基础，不应作为算法或 Agent 方法创新来宣传。
