# 操作员状态、诊断与冻结恢复

本页描述日常操作员入口。底层 REST、部署收据和审计账本仍是权威证据；CLI 只把它们投影成
一致、可操作的状态，不会把“进程活着”误报成“机器人可执行”。

## 状态检查

```bash
fireclaw status --profile /opt/firebot/fireclaw.real.toml
```

状态同时检查：

- Profile 与内容寻址部署是否一致；
- Plugin Runtime 的 ROS node/topic/action/TF readiness；
- Robot Gateway 是否在线且可认证读取；
- 急停、运动资源准入、pending recovery 和活动任务；
- 最近一次 managed runtime lifecycle 结果和日志目录（只作历史证据，不冒充当前 liveness）；
- systemd user unit 的内容一致性、installed/enabled/active 状态；
- 每个 Plugin 的 readiness 汇总；
- Mission Gateway 的 Fleet Doctor，包括机器人登记、可达性、能力、onboarding 与 lifecycle findings。

顶层 `phase` 为：

- `ready`：Gateway、Runtime、资源准入与 Fleet Doctor 都允许接收新任务；物理动作仍需逐项通过
  Safety Gate；
- `degraded`：只完成了部分检查，例如使用了 `--no-runtime-check`；
- `blocked`：已知条件禁止执行，例如 Runtime 未 ready、急停或资源冻结；
- `offline`：Gateway 不可达，无法确认当前机器人状态。

`safe_state=motion_admitted_idle` 只表示当前没有 Gateway 活动任务且资源准入开放，**不构成现场
静止证明**。Gateway 的 `GET /health` 只表示进程 liveness，也不代表机器人 ready。

默认输出面向操作员；自动化系统使用完整证据 envelope：

```bash
fireclaw status --profile /opt/firebot/fireclaw.real.toml --json
```

稳定字段包括 `phase`、`safe_state`、`reason_code`、`retryable`、`operator_action` 和
`evidence_id`；原始 deployment、Robot Gateway 与 Fleet Doctor evidence 保留在 `evidence` 中。

Fleet Doctor 默认使用 Profile 中受信任的 Mission Gateway probe URL；没有可解析配置时回退到
`http://127.0.0.1:8766`。外部托管或远程 Gateway 可显式覆盖：

```bash
fireclaw status \
  --profile /opt/firebot/fireclaw.real.toml \
  --server https://mission-gateway.example:8766 \
  --mission-api-token "$FIRECLAW_GATEWAY_TOKEN"
```

Robot Gateway 的 `--api-token` 与 Mission Gateway 的 `--mission-api-token` 故意分开；相应默认环境
变量分别是 `FIRECLAW_ROBOT_GATEWAY_TOKEN` 与 `FIRECLAW_GATEWAY_TOKEN`。Fleet Doctor 超时、认证
失败、不可达或报告畸形都会形成结构化 `degraded`/`blocked` 结果，不能留下 false READY。本地急停、
资源冻结与 Gateway 离线仍是更高优先级，不会被远端 Doctor 结果覆盖。

若 Gateway 离线，`status` 会根据部署与服务状态给出不同动作：release 缺失或 stale 时先提示
`deploy apply`；unit 未安装时提示 `deploy service install`；已安装但 inactive 时提示：

```bash
fireclaw deploy service start --profile /opt/firebot/fireclaw.real.toml
```

前台调试仍可运行 `<deployment-root>/current/bin/fireclaw-runtime`。两个入口都按 bringup -> ROS
readiness -> Robot Gateway -> Mission Gateway 顺序启动，按相反顺序停止。仿真允许有限整组重启；
实机意外退出不自动重启，并持久冻结仍开放的运动资源准入。

systemd `active` 只证明 supervisor 主进程存在，不会覆盖顶层安全判断。Runtime/Gateway 都 ready
但已有急停或 resource admission freeze 时，`status` 仍应为 `blocked`；重启服务不会解冻。

## Fleet Doctor 详细视图

`status` 已默认包含 Fleet Doctor 摘要和 findings。需要只查看 Fleet Doctor 的兼容/详细视图时，可以
执行：

```bash
fireclaw doctor --server http://127.0.0.1:8766
```

Doctor 聚合机器人登记、可达性、能力声明、onboarding 和 lifecycle findings。即使底层 Doctor
使用 `status=healthy`，只要仍有 warning，操作员 projection 就显示 `phase=degraded`，避免把
警告隐藏成 READY。远程部署应配置 token 和 TLS；凭据优先通过环境变量提供。

## 引导式冻结恢复

超时或取消后如果无法确认物理 Runtime 已停止，运动资源准入会跨重启冻结。保持 bringup 和
Robot Gateway 在线，然后运行：

```bash
fireclaw recover --profile /opt/firebot/fireclaw.real.toml
```

交互流程会：

1. 读取当前冻结状态；
2. 要求操作员说明恢复原因；
3. 由 Gateway 重新触发停止并采集可信现场证据；
4. 显示 request ID、证据、阻塞项、过期时间和本次专属完整短语；
5. 只有操作员原样输入完整短语才提交确认；
6. Gateway 再采集一次新证据并原子解冻；
7. CLI 再次读取资源准入，确认确实已经打开。

没有 `--yes` 或缩写确认。直接回车、输入不匹配、请求过期、证据异常、活动任务或资源租约都会
继续保持冻结。恢复只允许**未来的新任务**重新申请运动资源，不恢复或重跑旧任务。

非交互系统可以只创建请求并保存完整 evidence：

```bash
fireclaw recover \
  --profile /opt/firebot/fireclaw.real.toml \
  --reason "operator inspected the scene and requested recovery" \
  --request-only \
  --json
```

之后由操作员在五分钟有效期内确认既有请求；省略 `--confirmation-phrase` 时会安全地使用终端
输入，避免把短语写入 shell history：

```bash
fireclaw recover \
  --profile /opt/firebot/fireclaw.real.toml \
  --request-id recovery-...
```

Navigation stop witness 只在 `simulation` mode 注册。实机使用独立的
`fireclaw.safety.ros1-hardware` Plugin；它不暴露 LLM Tool，只注册短时有效的
`hardware_stop_v1` 证据 Provider。Gateway 会拒绝仅有 `runtime_stationarity_v1`、未标记为
hardware-owned 或证据结构不完整的 Provider；所以“有一个服务”本身不能让实机变成 READY。

实机恢复要求硬件 stop 服务确认后，同时证明：watchdog 健康且 stop asserted、物理急停有效、
driver disabled、声明存在的制动器 engaged、显式列出的全部执行器连续静止，以及独立 odometry
连续静止。默认下限是 3 个样本和 0.75 秒；任何缺失、陈旧、未知执行器或运动样本都会保持冻结。
配置模板见 `fireclaw.real.example.toml`，字段必须绑定厂商驱动拥有
的接口，不能绑定 FireClaw 自己发布的命令回显。

逻辑准入恢复后硬件急停仍保持有效；FireClaw 不清除物理安全链，也不会恢复旧任务。只有现场另行
完成厂商规定的复位步骤并提交新任务后，未来运动才可能再次经过 Safety Gate 获得授权。没有合格
witness 时 `status` 为 `degraded` 而不是 READY；`recover` 报告阻塞并保持冻结。

## 任务事件流

```bash
fireclaw mission --server http://127.0.0.1:8766
```

交互控制台通过 SSE 接收实时事件，记录 sequence cursor；连接中断后指数退避重连并从最后
cursor 续播，event ID 用于抑制重复显示。`cancel_requested` 只表示取消请求已发送，界面不会把它
显示成已经停止；`timed_out`、`lost` 和 `failed` 也保持各自语义。按 `Ctrl-C` 只停止本地观察，
不会静默取消远端任务。
