# FireClaw 用户体验与健壮性复核

## 2026-08-12T17:21:57+08:00 — 产品化缺口与优先级判断

### 任务目标

在不讨论 FireClaw 算法创新性的前提下，从操作员实际使用体验和系统工程健壮性出发，复核当前
已完成能力，判断下一阶段最值得改进的产品闭环。本次仅做只读检查、分析与记录，没有修改业务
代码，没有解冻默认 Robot Profile，没有启动或控制机器人，也没有 commit/push。

### 已检查

- 最近两个日期目录下与当前工作相关的 memory：
  - `memory/2026-08-12/resource-admission-recovery.md`
  - `memory/2026-08-12/navigation-runtime-resume.md`
  - `memory/2026-08-11/navigation-runtime-lifecycle.md`
  - `memory/2026-08-11/embodied-evaluation-handoff.md`
- 当前 `git status --short`、`git diff --stat`、`git diff --check`；工作树仍有此前 Runtime deployment、
  Navigation Plugin、冻结恢复及测试等未提交改动，不能覆盖或拆散。
- 使用 CodeGraph 检查部署入口、Gateway task/state/cancel、resource admission recovery、事件与审计、
  operator projection 等运行链路。
- 正式 CLI 帮助：顶层、`deploy`、`robot-gateway`、`serve`、`mission`、`robot-profile`、`trace`。
- 交互控制台、Operator Event Projector、Mission SSE client、Fleet Doctor、部署文档和状态词映射。

### 当前工程基础

- Plugin Runtime 的 `plan/apply/status`、内容寻址 release、receipt/hash、ROS readiness 已存在；
- Mission Gateway、Robot Gateway、自然语言入口、任务/取消/审批/审计/回放已存在；
- 服务端 SSE 和带 cursor 的 typed client 已存在；
- Fleet Doctor、单机 doctor、sensor degraded policy 已存在；
- Navigation ROS/Gazebo 的 success/cancel/timeout/stall/collision 证据链已存在；
- `physical_runtime_stop_unconfirmed` 已有持久冻结与两阶段恢复；
- 最新冻结恢复聚焦测试为 `115 passed`，此前 Runtime slice 全量测试为
  `2033 passed, 7 skipped`；冻结恢复改动后的全量 suite 尚未运行。

### 用户体验与健壮性缺口

1. **入口碎片化**：顶层存在大量面向内部对象的命令；启动仍区分 bringup、Robot Gateway、Mission
   Gateway；冻结恢复只在文档中提供 raw `curl`。已有能力没有形成“一条主路径”。
2. **状态不够可操作**：`GET /health` 只返回固定 `status=ok` 与 adapter 元数据，不能代表 ROS、
   Plugin、传感器、定位、资源准入或运动能力 ready；真实 readiness 分散在 `deploy status`、
   `/state`、`/fleet/doctor` 和审计事件。
3. **状态词跨层不统一**：Mission、Robot task、Skill、Action 使用 `completed/succeeded/failed/
   timed_out/lost/blocked/escalated` 等不同集合；Mission trace 会把 `timed_out/lost/blocked` 压平为
   `failed`，用户容易丢失“失败原因”和“当前是否确认停止”的关键区别。
4. **交互链路未使用已有流能力**：`fireclaw mission` 每 0.5 秒轮询 events/trace；异常时打印错误并
   直接退出。虽然 typed SSE cursor/replay 已存在，但主交互流程没有使用，也没有自动重连、退避、
   离线提示或恢复后的无重复续播。
5. **恢复 UX 偏工程接口**：用户必须理解 request/confirm、复制完整短语、判断 TTL 和证据 JSON；
   pending request 的展示、过期倒计时、阻塞项和下一安全动作缺少引导式界面。
6. **进程生命周期仍需人工编排**：文档明确尚无 systemd target、Plugin Service 顺序启动/逆序停止、
   进程 ownership 与 crash restart；这会让开机、异常退出和现场换班依赖操作者经验。
7. **实机安全恢复尚未闭环**：当前 stop witness 只适用于 simulation；real mode 缺少底盘/硬件拥有
   的全执行器停止证明和独立 watchdog，因此实机一旦进入不确定停止状态只能保持冻结。
8. **部分故障隔离不足**：部署文档明确多 Plugin 部分 readiness 与按 Plugin 隔离暴露尚未完成；
   一个 Runtime 不 ready 时，用户难以明确知道哪些能力仍可安全使用。
9. **发布级故障注入还不完整**：已有丰富单测和 Gazebo lanes，但还需要覆盖 Gateway/ROS master/
   action server 中途崩溃、网络抖动、重复提交、数据库锁/损坏、磁盘满、时钟跳变、传感器 stale、
   重启 reconciliation 和 clean-machine install。
10. **诊断材料未形成一键支持包**：已有 audit/replay/proof bundle 基础，但日常用户还缺一个自动
    脱敏的 incident/support bundle，能直接汇总配置指纹、readiness、任务时间线、终态、停止证据
    和日志尾部。

### 推荐优先级

#### P0：操作员闭环 v1

- 增加统一的 `fireclaw status`/`doctor`/`recover` 用户入口，复用现有 deploy status、Fleet Doctor、
  Gateway state 和 recovery API；默认人类可读，保留 `--json`。
- 定义统一 operator-facing 状态 envelope：`phase`、`safe_state`、`reason_code`、`retryable`、
  `operator_action`、`evidence_id`；底层原始状态仍保留在审计中。
- 区分 liveness、readiness、degraded 与 capability availability；禁止把进程活着显示为机器人 ready。
- 让 `fireclaw mission` 使用已有 SSE cursor/replay，并增加自动重连、退避、离线横幅和恢复续播。
- 为冻结恢复提供引导式 request/confirm 流程，明确显示冻结原因、现场停止证据、阻塞项、TTL 和
  “恢复只开放未来运动资源，不恢复旧任务”。

#### P0：运行生命周期与实机安全

- 生成/管理一个 systemd target 或等价 supervisor，负责固定顺序启动、readiness gate、逆序停止、
  owned/adopted runtime、有限重启和 crash reconciliation。
- 实机接入硬件拥有的 watchdog、急停状态、驱动使能和全执行器静止 witness；在完成之前明确标记
  real-mode recovery unsupported，而不是让用户现场猜测。

#### P1：故障恢复与发布门禁

- 做 capability-level degraded isolation；一个 Plugin 不 ready 不应抹掉无关只读/感知能力。
- 为提交、取消、审批和恢复补齐默认 idempotency key、重复请求结果一致性和断线重试语义。
- 建立自动化 crash/fault-injection matrix 与 clean-machine Gazebo acceptance，并把 unsafe motion、
  orphan task、recovery latency、false-ready、audit completeness 设为发布门禁。
- 增加一键脱敏 incident bundle 和 SQLite/JSONL 持久层完整性、备份/恢复检查。

#### P2：进一步体验优化

- 对物理任务先展示标准化理解、目标、机器人、风险、前置条件和可取消性，再执行或要求确认；
- 提供任务阶段、当前位置/目标、最新反馈、剩余 deadline 和“取消已请求/机器人已确认停止”的
  明确区分；
- 在上述闭环稳定后再考虑完整 Web UI、移动端或语音，不应先做视觉壳层。

### 当前结论

FireClaw 的核心问题已经不是“缺少功能”，而是“已有功能尚未形成一致、可解释、可恢复的操作员
产品”。最值得开始的下一切片是 **Operator Readiness & Recovery UX**：它主要组合现有能力，
架构风险小，却能立刻改善用户感受，同时暴露状态模型和断线恢复中的真实缺陷。完成后再做
systemd/lifecycle 与实机 hardware witness；不建议此时继续扩展新的算法 Plugin。

### 本次命令与结果

```text
git diff --check
# passed

/home/lpp/miniconda3/envs/py310/bin/fireclaw --help
/home/lpp/miniconda3/envs/py310/bin/fireclaw deploy --help
/home/lpp/miniconda3/envs/py310/bin/fireclaw robot-gateway --help
/home/lpp/miniconda3/envs/py310/bin/fireclaw serve --help
/home/lpp/miniconda3/envs/py310/bin/fireclaw mission --help
/home/lpp/miniconda3/envs/py310/bin/fireclaw robot-profile --help
/home/lpp/miniconda3/envs/py310/bin/fireclaw trace --help
# all returned help successfully
```

### 下一建议步骤

先写一个小而明确的 Operator Readiness & Recovery UX 合同和回归测试，再实施：

1. 聚合 status snapshot 与 reason/action envelope；
2. 顶层 `status`/`doctor`/`recover` CLI；
3. interactive SSE reconnect；
4. 状态词与错误投影收口；
5. 运行冻结恢复相关 focused suite、完整 pytest 和 `git diff --check`。

## 2026-08-12T18:27:31+08:00 — P0 操作员闭环与受管 Runtime lifecycle 实施完成

### 本轮目标与用户决定

用户确认按上一次推荐顺序实施：先收口操作员 readiness/recovery UX，再处理进程生命周期；从实机
安全恢复角度，不能在没有底盘/驱动接口时伪造 hardware witness，必须继续 fail-closed。

### OpenClaw analogue 与 FireClaw 适配

使用 CodeGraph 检查了：

- `openclaw/src/plugins/services.ts::startPluginServices`：service 顺序启动、单项启动失败隔离、
  running handle、逆序 stop、stop failure isolation；
- `openclaw/src/plugins/plugin-registration.types.ts::OpenClawPluginService`；
- FireClaw `src/fireclaw_core/deployment/deployer.py::apply_deployment`、`_materialize_release`、
  `_write_wrappers`、`inspect_deployment_status` 和 Runtime/Profile shape。

复用顺序启动、显式 running ownership、逆序停止与结构化 lifecycle evidence。机器人适配差异：

- bringup 必须在 Robot Gateway 前通过 ROS node/topic/action/TF readiness；
- Gateway `/health` 还必须匹配 robot identity 与 dry-run/real mode；
- simulation 允许有限整组 restart；real 发生任何意外退出都禁止自动 restart；
- real 正常停止或异常退出若 admission 仍开放，会持久冻结为
  `managed_runtime_shutdown_unconfirmed` / `managed_runtime_failure`；已经存在的更早冻结不会覆盖；
- “进程退出”从不作为现场静止证据。

### 已实现：操作员闭环 v1

新增：

- `src/fireclaw_core/infra/operator_readiness.py`
  - 统一 envelope：`phase/safe_state/reason_code/retryable/operator_action/evidence_id`；
  - 区分 deployment、Gateway liveness/access、Robot Adapter online、dry-run/real、急停、resource
    admission、活动任务、per-Plugin readiness、stop-evidence provider 和 supervisor history；
  - 对缺失的 `emergency_stop.active`、`admission.closed`、`robot_state.online`、Gateway mode 全部
    fail-closed，不能从空对象推断 READY；
  - real mode 缐少硬件停止证据 provider 时为 `degraded`，不是 READY；
  - `motion_admitted_idle` 明确不等于现场静止证明；
  - Fleet Doctor malformed report 不会被映射为健康。
- `src/fireclaw_core/infra/operator_cli.py`
  - `fireclaw status`：聚合 deployment、Gateway `/health`/`/state`、supervisor history；
  - `fireclaw doctor`：聚合 Mission Gateway Fleet Doctor；warning 映射为 degraded；
  - `fireclaw recover`：两阶段 request/confirm、完整短语、无 `--yes`、request-only、后置 admission
    复核；缺失 admission schema、认证失败和不完整 pending response 均保持冻结。
- `src/fireclaw_core/mission/interactive.py`
  - 主控制台从 polling 改为 SSE cursor/replay；断线指数退避，从最后 sequence 续播并抑制 event ID
    重复；
  - 保留 `timed_out/lost/blocked/escalated`，不压平成普通 failed；
  - `cancel_requested` 不显示为停止；Ctrl-C 只停止本地观察，不静默取消远端任务。
- `src/fireclaw_core/infra/operator_projection.py`
  - 统一取消、timeout/lost 与 `runtime_stopped/resource_release_safe` 的安全文案。
- `src/fireclaw_core/gateway/gateway.py` 与 `infra/runtime_state.py`
  - GET admission 时惰性持久过期 pending recovery，并只写一次
    `resource_admission.recovery_expired`；公共状态/审计不泄露确认短语或内部 transition 标记。
- `src/fireclaw_core/subagent/subagent_client.py`
  - typed health/admission/recovery client methods。
- CLI 正式注册 `status`、`doctor`、`recover`。

### 已实现：受管 Runtime lifecycle

新增 `src/fireclaw_core/deployment/supervisor.py` 和 `fireclaw deploy run`：

- 只执行 active content-addressed release 中已哈希、可执行的 generated wrappers；
- 固定 `bringup -> ROS readiness -> Gateway -> health identity/mode` 启动顺序；
- 固定 Gateway -> bringup 逆序停止；SIGINT grace 后有界升级到 SIGTERM/SIGKILL；
- child process 各自写日志，lifecycle JSONL 记录 process start/exit、readiness、restart、stop signal、
  safety freeze 和最终原因；
- simulation 完整 generation 最多默认自动重启两次并指数退避；real restart limit 强制为 0；
- real stop/crash 后直接通过 authoritative SQLite resource-admission boundary 建立安全冻结；如果状态
  已冻结则保留原始原因/revision；冻结写失败会把 supervisor 结果改为 failure，不会报告 clean stop；
- `inspect_runtime_supervisor_state` 有 1 MiB 读取上限、路径/符号链接边界，不把历史日志当 live
  liveness；`fireclaw status` 显示最近状态与日志路径；
- generated `bin/fireclaw-runtime` 是日常单一入口；可信 `--profile/--config/--robot-profile` 被放在
  wrapper argv 最后，尾随用户参数不能覆盖部署绑定；
- deployment generator 从 version 3 升为 4，避免复用缺少 supervisor 的旧 release。

对应文档：

- `docs/deployment/operator-readiness-recovery.md`
- `docs/deployment/plugin-runtime-deployment.md`
- `README.md`

### 测试与验证

新增或扩展测试：

- `tests/test_operator_readiness.py`
- `tests/test_operator_cli.py`
- `tests/test_runtime_supervisor.py`
- `tests/test_interactive.py`
- `tests/test_operator_projection.py`
- `tests/test_subagent_client.py`
- `tests/test_gateway.py`
- `tests/test_plugin_runtime_deployment.py`

关键结果：

```text
operator/recovery/interactive focused: 30 passed
runtime supervisor + deploy + mission CLI: 62 passed
latest no-socket operator/lifecycle focused: 54 passed
post-review timeout/restart-bound validation focused: 43 passed
full repository suite: 2076 passed, 7 skipped in 181.44s
python -m py_compile (all changed Python modules): passed
git diff --check: passed
```

测试环境说明：仓库没有 `.venv`，`/usr/bin/python3` 也没有 pytest；继续使用当天既有
`/home/lpp/miniconda3/envs/py310/bin/python`。`ruff` 未安装，因此没有声称运行 lint。沙箱内创建
回环 socket 的测试会出现 `PermissionError: [Errno 1] Operation not permitted`；在授权的本机环境
重跑后通过，不是代码 failure。

### 默认 Profile 部署验收

执行：

```text
/home/lpp/miniconda3/envs/py310/bin/python -m fireclaw_core deploy apply \
  --profile fireclaw.example.toml
```

沙箱第一次因 `/home/lpp/.fireclaw` 只读失败；获得明确授权后重跑成功。结果：

- status=`installed`，reused=`false`；
- fingerprint=`e374766495682afa9d938cf37d3f965561e39aa08e8e0bb19c2ee3c356042d10`；
- static artifact/hash/ROS package check 全部通过；
- `current/bin/fireclaw-bringup`、`fireclaw-gateway`、`fireclaw-runtime` 均可执行；
- 没有启动 ROS、Gazebo、Gateway 或机器人，没有执行 recover。

默认 Robot Profile 的持久安全冻结仍为：

```text
revision=1
reason=physical_runtime_stop_unconfirmed
task_id=task-2ebb389110dd492280a26dd1adaa617d
closed_at=2026-08-12T08:18:22.074605+00:00
```

部署 apply 没有删除、覆盖或解除它。

### 当前结论

P0 操作员闭环 v1 和等价 supervisor 已完成工程实现与全量回归。实机 stop witness 仍不能凭空实现；
现在 status 会把 real mode 无 provider 显式标为 degraded，并在冻结时提示先接入 hardware-owned
witness，避免反复运行必然失败的 recover。

### 仍需处理 / 下一步

1. 获取实际底盘/驱动接口后实现 hardware-owned witness：watchdog、硬件急停、驱动 enable、所有
   执行器速度/制动状态和独立静止观测；随后跑实机 acceptance，才允许 real recovery READY。
2. 为 supervisor 增加真实 Gazebo crash acceptance（中途杀 Gateway、roslaunch、move_base），验证
   进程树、日志、重启 generation 与 admission freeze 的端到端行为；本轮已有 deterministic unit
   fault injection，但没有启动 Gazebo。
3. 完成剩余 fault matrix：ROS master/action server/network 抖动、SQLite busy/corrupt、磁盘满、
   时钟跳变、传感器 stale、重启 reconciliation；把 false-ready/orphan motion/audit completeness
   作为 release gate。
4. 增加脱敏 incident bundle、supervisor 日志轮转/保留策略，以及可选 systemd unit install/enable；
   当前 supervisor 可被外部 systemd/vendor service 托管，但不会修改系统 service 配置。
5. SSE cursor 当前依赖 sequence/event ID 与 trace fallback；未来可增加显式 runtime epoch，进一步
   区分 Mission Gateway 重启后的 event sequence domain。

## 2026-08-12T20:31:38+08:00 — P0 第 4 项“一键启动和停止”完成

### 用户决定与完成边界

用户明确要求“一个个来，先把没完成的完成，再继续往下”。本轮只收尾 P0 第 4 项，不进入第 5 项
实机安全闭环。完成标准不是只有 supervisor 单元测试，而是：

1. bringup、Robot Gateway、Mission Gateway 由一个 owner 顺序启动并持续监测；
2. 有明确 readiness、逆序停止、有限仿真恢复和 real fail-closed 策略；
3. 有可安装、可检查、可 start/stop/restart/uninstall 的 systemd user service；
4. 在真实 ROS/Gazebo 子进程上完成崩溃注入；
5. 最终无残留进程/端口，完整回归通过，既有安全冻结不变。

### OpenClaw analogue

按 AGENTS.md 要求先使用 CodeGraph 检查：

- FireClaw `RuntimeSupervisor.run/_run_generation`、deployment Profile、Mission Gateway health/auth、
  deployment status/readiness flow；
- OpenClaw `openclaw/src/daemon/service.ts::GatewayService`；
- OpenClaw `openclaw/src/daemon/systemd.ts::installSystemdService` 与 service control/status 分层；
- OpenClaw `openclaw/src/daemon/systemd-unit.ts::buildSystemdUnit`。

复用 render/stage/install/control/status 分离、显式安装、无 shell argv、受管 unit ownership 和反向
lifecycle。FireClaw 的机器人差异是：三阶段 readiness、simulation/real 不同 restart policy、
策略失败 exit 78、物理资源冻结、持续 ROS graph 检查，以及 roslaunch leader 暴力退出后的孤儿
ROS/Gazebo session 排空。`deploy apply` 不自动修改 systemd。

### 已实现

- `RuntimeDeploymentProfile` 新增受管 Mission Gateway：存在 `[server]` 时默认纳入 supervisor；
  可用 `[deployment.supervisor.mission_gateway] enabled=false` 明确外部托管；probe URL/TLS 文件和
  client cert/key 配对均验证。
- Mission Gateway 新增公开、轻量 `GET /health`，只表示进程 liveness；`GET /fleet/doctor` 保留
  下游 readiness/诊断语义；client 增加 `get_health()`。
- deployment generator version 最终升为 6，release 生成：
  - `fireclaw-bringup`
  - `fireclaw-gateway`
  - `fireclaw-mission-gateway`
  - `fireclaw-runtime`
  - `systemd/fireclaw-<deployment-id>.service`
- supervisor 启动顺序固定为 bringup -> ROS readiness -> Robot Gateway identity/mode health ->
  Mission Gateway health；停止顺序严格相反。
- generation ready 后持续重查 Runtime、Robot Gateway、Mission Gateway readiness；连续失败计数
  可恢复清零，达到阈值触发整代 failure。这样 `move_base` 消失但 roslaunch 仍活着时不会 false-ready。
- 新增部署级 advisory lock；第二个 supervisor 返回 `supervisor_already_running`、generation 0。
- 启动前检查 live ROS evidence；发现健康或部分响应的旧/manual ROS domain 时返回
  `runtime_domain_in_use`、generation 0，不消耗重启预算。没有 node/topic/action/TF live evidence
  的空 readiness 集合不会误判占用。
- 真实 subprocess 模式通过 `/proc` 在父进程存活时追踪其 descendant sessions。roslaunch leader
  被 SIGKILL 后，按已拥有 session 对孤儿进程组分阶段 SIGINT/SIGTERM/SIGKILL 并确认排空；失败
  返回不可自动重启的 `supervisor_process_tree_cleanup_failed`。
- `fireclaw deploy service` 新增 `render/install/status/start/stop/restart/uninstall`：
  - unit 只从 active、完整性校验通过且与 Profile 精确一致的 release 读取；
  - 原子 0600 写入，拒绝 symlink、非普通文件和非 FireClaw 管理的同名 unit；
  - simulation `Restart=on-failure`，real `Restart=no`；
  - `RestartPreventExitStatus=2 78`，阻止配置拒绝或 supervisor 恢复预算失败形成外层无限重启；
  - `KillMode=mixed`、`KillSignal=SIGINT`、`TimeoutStopSec=120`；
  - 可选 env 文件固定为 deployment state 下 `runtime.env`，不会自动创建或嵌入 secret。
- `fireclaw status` evidence 增加 managed systemd service；Gateway 离线时能区分 not-installed、inactive、
  stale/invalid 并给出对应命令。unit active 不覆盖 emergency stop/resource freeze 的 blocked 判断。
- `deploy run` 操作员停止返回 0，supervisor policy failure 返回 78，配置/部署拒绝返回 2。

主要新/改文件：

- `src/fireclaw_core/deployment/profile.py`
- `src/fireclaw_core/deployment/deployer.py`
- `src/fireclaw_core/deployment/supervisor.py`
- `src/fireclaw_core/deployment/systemd_service.py`（新增）
- `src/fireclaw_core/deployment/__init__.py`
- `src/fireclaw_core/mission/mission_gateway.py`
- `src/fireclaw_core/mission/mission_gateway_client.py`
- `src/fireclaw_core/mission/mission_cli.py`
- `src/fireclaw_core/infra/operator_cli.py`
- `src/fireclaw_core/infra/operator_readiness.py`
- `tests/test_runtime_supervisor.py`
- `tests/test_systemd_service.py`（新增）
- `tests/test_plugin_runtime_deployment.py`
- `tests/test_mission_gateway.py`
- `tests/test_mission_gateway_client.py`
- `tests/test_operator_readiness.py`
- `docs/deployment/plugin-runtime-deployment.md`
- `docs/deployment/operator-readiness-recovery.md`
- `README.md`

### 真实验收与发现的失败

第一次启动 v5 supervisor 时，generation 1--5 都出现 `bringup_exited_before_ready`。不是新代码的
随机错误；宿主中遗留了 17:08 启动的旧 release：

```text
roslaunch PID 1478753
gzserver PID 1478986
Robot Gateway PID 1479659
ports 11311 and 8765 occupied
spawn_model: entity already exists
```

最初只按进程文本扫描，因工具所见进程命名空间差异误报“无进程”；改用宿主 `ss` + ROS graph +
精确 PID 后定位。核对命令行确认属于旧 FireClaw release，按 Gateway -> bringup 精确 SIGINT 停止，
没有使用 `pkill` 或广泛匹配。这次失败促成单实例 lock 和 ROS-domain preflight。

第二次 v5 真实注入，前三项成功：

- generation 1：SIGKILL Mission Gateway -> `mission_gateway_exited` -> generation 2 ready；
- generation 2：SIGKILL Robot Gateway -> `gateway_exited` -> generation 3 ready；
- generation 3：`rosnode kill /move_base`，roslaunch 仍活着 -> 连续 2 次 Runtime readiness failure ->
  `runtime_readiness_lost` -> generation 4 ready。

generation 4 SIGKILL roslaunch leader 后，第 5 代因 orphan rosmaster/rosout/gzserver 和重复 entity
失败。这证明仅 kill 主进程组不够；ROS 子节点会各自创建 session，父 leader 死后 reparent 到
systemd user manager。加入 `/proc` descendant-session ownership 后生成 v6，再次验收：

```text
run: run-20260812T120942Z-94faf45c
generation 1 roslaunch PID: 1628058
events: bringup_exited -> process.descendants_remaining
        -> process.descendant_stop_signal
        -> process.descendants_stopped
generation 2: ROS ready -> Robot Gateway ready -> Mission Gateway ready
final Ctrl-C: Mission Gateway -> Robot Gateway -> bringup SIGINT
result: status=stopped, reason_code=sigint, generation_count=2, exit=0
```

结束后 lifecycle 两代六个主 PID 均退出，11311/8765/8766（另核对 8000）无残留监听。

### systemd 实机环境验收

v6 release：

```text
fingerprint=6917a088071c86af2ada86e798cec3ab9032128de072f362ff705fc1f50cb77f
unit sha256=db3a1ef40fee1db731f0c497d625d085bddc738e1e31f5358854da3634e65b8e
```

`systemd-analyze verify` exit 0。沙箱输出的宿主 system unit 权限/varlink 警告不是 FireClaw unit
语法错误。通过真实 user bus 执行：

```text
deploy service install --no-enable --no-start -> installed
deploy service status -> inactive, enabled=false, generated hash == installed hash
deploy service start -> systemctl --user start returncode 0
fireclaw status -> deployment ready (9/9), Robot Gateway online, supervisor generation ready,
                   managed service active; top-level blocked only because revision-1 safety freeze
Mission GET /health -> ok
Mission GET /fleet/doctor -> healthy, error_count=0, warning_count=1
deploy service stop -> systemctl --user stop returncode 0
lifecycle -> Mission Gateway, Robot Gateway, bringup SIGINT; supervisor stopped
final status -> inactive, enabled=false; managed ports free
```

unit 当前保留在 `~/.config/systemd/user/fireclaw-gazebo-turtlebot3-burger.service`，内容完整性一致，
但明确保持 **disabled + inactive**；没有留下后台仿真，也没有让下次登录自动启动。默认 install
不带 `--no-enable/--no-start` 时才会 enable + restart。

### 测试结果

```text
systemd-analyze verify generated v6 unit: exit 0
python -m py_compile changed deployment/mission/operator modules and tests: passed
git diff --check: passed
final focused suite: 180 passed in 59.06s
post-boundary lifecycle/deployment/systemd suite: 38 passed in 2.07s
final full repository suite: 2097 passed, 7 skipped in 173.81s
```

曾先运行裸 `python`，沙箱外 login shell 返回 `python：未找到命令`；仓库也没有 `.venv`，
`/usr/bin/python3` 没有 pytest。随后统一使用当天既有
`/home/lpp/miniconda3/envs/py310/bin/python`。这是命令环境错误，不是代码失败。

### 安全状态与结论

最终只读 SQLite 核对仍为：

```text
closed=true
revision=1
reason=physical_runtime_stop_unconfirmed
task_id=task-2ebb389110dd492280a26dd1adaa617d
closed_at=2026-08-12T08:18:22.074605+00:00
```

没有删除、覆盖、确认或绕过冻结；systemd start 后顶层状态仍正确显示 blocked，证明重启不会解锁。

P0 第 4 项“一键启动和停止”现已达到工程完成边界：三服务统一 ownership、持续 readiness、单实例、
明确启动/停止命令、systemd user service、仿真有限恢复、real no-restart、真实四类崩溃验收和完整
回归均完成。按用户要求停在这里。

下一项才是 P0 第 5 项“实机安全闭环”：需要真实底盘拥有的 watchdog、硬件急停/驱动 enable、所有
执行器静止和独立观测证据。当前没有获得这些硬件接口，不应在本轮伪造或提前实现。

## 2026-08-12T20:53:29+08:00 — P0 第 1 项“统一状态入口”严格闭环

### 用户决定与完成边界

用户要求先把前四项中尚未严格闭环的内容完成，再进入第 5 项。复核确认原实现仍把 Robot/Runtime
状态放在 `fireclaw status`、Fleet Doctor 放在 `fireclaw doctor`，因此此前称“统一入口完成”的口径
过宽。本阶段将以下条件作为验收边界：

1. 一条 `fireclaw status --profile ...` 默认采集 deployment/ROS、Robot Gateway、急停、资源冻结、
   active tasks、supervisor/systemd、per-Plugin readiness 和 Fleet Doctor；
2. Mission Gateway 超时、认证失败、不可达或畸形报告不得留下 false READY；
3. Robot 本地急停、冻结与离线保持更高优先级，不能被 Fleet Doctor 覆盖；
4. URL/TLS 从可信 Profile 解析，支持显式 CLI 覆盖，Robot/Mission token 不混用；
5. 人类输出、JSON envelope、退出码、单测、网络测试和真实 Gazebo 只读验收全部通过。

### OpenClaw analogue 与 FireClaw 适配

先尝试使用 CodeGraph 查询 OpenClaw status/doctor；当前索引未返回 `openclaw/` TypeScript source，随后
在已定位文件中检查：

- `openclaw/src/commands/status-all.ts::statusAllCommand`；
- `openclaw/src/commands/status-runtime-shared.ts::resolveStatusGatewayHealthSafe`、
  `resolveStatusRuntimeSnapshot`、`resolveStatusServiceSummaries`；
- `openclaw/src/commands/doctor-gateway-health.ts::checkGatewayHealth`。

复用“一条 status 编排多个有界 probe、局部失败转结构化结果、保留详细 doctor”的形状。FireClaw
增加机器人特有的安全优先级：Fleet Doctor 可以阻止 READY，但不能掩盖急停、资源冻结或 Robot
Gateway 离线；`safe_state` 继续表达本地物理资源事实，不伪造成 Fleet Doctor 的结论。

### 实现

- `collect_operator_status` 现在默认调用 Fleet Doctor，并把 probe endpoint/source 放入证据；
- Mission Gateway URL 优先级为 `--server` override -> Profile managed probe -> 默认 loopback `:8766`；
- Profile TLS 自动复用；新增独立 `--mission-api-token` 和 Mission TLS 参数；既有 `--api-token` 明确
  只用于 Robot Gateway；
- `build_operator_status_snapshot(..., fleet_doctor=...)` 与
  `merge_operator_status_with_doctor` 实施 fail-closed 合并；
- 本地 `blocked/offline` 保持主 reason/action；本地 ready + Doctor warning/offline -> degraded；Doctor
  errors -> blocked；畸形 Doctor snapshot -> degraded；
- 人类输出直接显示 Fleet Doctor phase、error/warning 计数与 findings；JSON 保留完整 raw report；
- `fireclaw doctor` 保留为兼容的详细视图，不再是完成日常状态判断的必需第二条命令；
- README 与 deployment/operator 文档已改为单一 status 主路径。

主要修改：

- `src/fireclaw_core/infra/operator_cli.py`
- `src/fireclaw_core/infra/operator_readiness.py`
- `src/fireclaw_core/mission/mission_cli.py`
- `tests/test_operator_cli.py`
- `tests/test_operator_readiness.py`
- `docs/deployment/operator-readiness-recovery.md`
- `docs/deployment/plugin-runtime-deployment.md`
- `README.md`

### 验证

```text
pre-change operator baseline: 23 passed
post-change no-socket operator suite: 28 passed
operator + mission CLI/client authorized network suite: passed (99 tests)
python -m py_compile changed modules/tests: passed
targeted git diff --check: passed
```

真实 systemd/Gazebo 验收：

- 启动既有 disabled unit 后，deployment ROS checks `9/9` ready；
- Robot Gateway online，可认证读取；Mission Gateway Fleet Doctor 实际返回
  `status=healthy, error_count=0, warning_count=1, total_count=4`；
- 同一 `fireclaw status --json` 同时显示：
  - 顶层 `blocked`，本地 `emergency_stop_active`；
  - resource admission `physical_runtime_stop_unconfirmed`, revision 1；
  - `components.fleet_doctor.phase=degraded` 及完整 findings；
  - endpoint `http://127.0.0.1:8766`, source `profile`；
- 顶层没有被 Doctor warning 覆盖，证明本地安全优先级生效；
- 验收后正常 stop；unit 最终 `disabled + inactive`，11311/8765/8766/8000 均无监听。

没有执行 recover、没有提交运动任务、没有删除或绕过冻结。现场读取仍显示原 task/revision，说明
统一状态验收未改变安全状态。

### 结论与下一步

P0 第 1 项现已达到严格工程闭环；前四项的软件闭环全部完成。下一步按用户要求进入 P0 第 5 项
“实机安全闭环”。实现必须建立硬件拥有的证据合同和可配置适配边界；在没有实际 vendor topic/
service 名称时，只能完成通用 production implementation、fixture/fault injection 与 fail-closed
验收，不能声称某台真实底盘已经现场通过。

## 2026-08-12T21:22:44+08:00 — P0 第 5 项“实机安全闭环”软件与集成边界完成

### 任务目标和完成口径

用户要求在前四项闭环后继续第 5 项。此次不把“存在一个 stop evidence service”视为完成，而把
工程闭环定义为：实机恢复证据必须来自硬件拥有的 Provider；Gateway 必须独立验证结构、时效、来源
和每一项物理条件；ROS1 适配必须能主动重新触发厂商 stop 并采集连续样本；配置缺失、信号陈旧、
执行器漏报、检测到运动或伪造 `stopped` 都保持冻结；两阶段操作员确认与第二次重新采证仍然成立。

现场 acceptance 另行定义：必须把模板中的 vendor topic/service/message field 绑定到真实底盘，在实体
机器人上做急停、watchdog、driver、brake、全执行器与独立 odometry 的正反例测试。本仓库没有这些
厂商接口，因此本次没有伪称某台实机已完成现场认证。

### OpenClaw analogue 与差异原因

此前已检查 OpenClaw 的 approval/status 持久化编排，并继续复用显式请求、短期有效证据、专属确认和
审计的形状。OpenClaw 不控制实体执行器，也没有硬件 watchdog/急停/driver/actuator inventory 边界；
因此 `hardware_stop_v1` 是 FireClaw 必需的机器人安全扩展，不适合照搬 chat/gateway API。核心仍保持
Plugin service discovery，不把 vendor ROS 名称写进 Gateway。

### 实现内容

新增 trusted、service-only、默认关闭的 Plugin：

- `extensions/ros1-hardware-safety/fireclaw.plugin.json`
- `extensions/ros1-hardware-safety/plugin/entrypoint.py`
- `extensions/ros1-hardware-safety/plugin/hardware_safety.py`
- `extensions/ros1-hardware-safety/README.md`

它只在 `mode=real + role=robot_agent + adapter=ros1` 且 Profile 显式 `enabled=true` 时注册，不贡献
任何 LLM Tool。ROS1 observer：

1. 调用配置的厂商硬件 stop service（仅允许 `std_srvs/Trigger` 或 `std_srvs/SetBool`）；
2. 订阅 watchdog healthy/stop asserted、物理 e-stop、driver enabled、可选 brake engaged；
3. 订阅显式清单的 `sensor_msgs/JointState` 和独立 `nav_msgs/Odometry`；
4. 每个硬件状态与运动通道至少采 3 个新鲜样本；执行器和独立运动同时覆盖至少 0.75 秒；
5. 精确核对 expected/ignored actuator names，未分类或缺失名称即阻塞；
6. 在所有句柄 finally unregister 后返回最长 30 秒、模板默认 15 秒有效的证据。

SDK 新增证据类：

- `runtime_stationarity_v1`：仿真/导航 Runtime 静止证据；
- `hardware_stop_v1`：实机硬件整机停止证据。

Gateway discovery 现在公开并使用 `provider_id`、`evidence_class`、`hardware_owned`、
`qualified_for_real`。实机正证据必须同时满足：

- Provider 静态声明 `hardware_owned=true` 且 evidence class 为 `hardware_stop_v1`；
- report 的 provider/class 与注册 descriptor 精确一致；
- `dry_run=false`、robot/deployment mode 一致、服务器时间戳新鲜且 validity <= 30 秒；
- stop 已重新触发并被硬件确认；
- watchdog healthy + stop asserted；物理 e-stop active；driver disabled；
- brake policy 显式，required 时 fresh + engaged；
- 硬件状态、执行器和独立运动均至少 3 个样本；
- hold >= 0.75 秒；全部 expected actuators 存在，无 unclassified names；
- actuator threshold <= 0.02，independent linear <= 0.02、angular <= 0.05，实测不越界；
- blockers 为空。

不合格 Provider 仍展示在状态中用于诊断，但不能恢复 real admission。Operator readiness 新增合格实机
Provider 计数；“服务存在但只有 navigation/runtime witness”会显示
`hardware_stop_evidence_provider_unqualified`，不再产生 false READY。

模板和文档已更新：

- `examples/deployment_profiles/navigation_robot.toml.example` 显式选择 safety Plugin 并列出全部 vendor
  绑定；
- `docs/deployment/operator-readiness-recovery.md`
- `docs/deployment/plugin-runtime-deployment.md`
- `docs/deployment/ros1-deployment-guide.md`
- `README.md`

逻辑恢复只打开未来任务的资源申请；不会清除硬件 e-stop、重新 enable driver 或续跑旧任务。现场按
厂商程序另行复位物理链路，然后新任务仍需经过 Safety Gate。

### 测试、命令和结果

使用 `/home/lpp/miniconda3/envs/py310/bin/python`。

```text
new hardware/plugin/gateway/readiness focused suite: 32 passed
broader affected integration suite before final refinements: 100 passed
profile-backed real Gateway discovery: passed
template TOML parsing + Plugin activation with deterministic observer: passed
real two-stage request/confirm with two fresh hardware evidence collections: passed
unqualified runtime witness, malformed watchdog, missing actuator, measured motion,
single latched hardware samples, invalid config and simulation registration negatives: passed
final full repository suite: 2113 passed, 7 skipped in 175.71s
python -m py_compile changed modules/tests: passed
python -m json.tool new manifest: passed
git diff --check: passed
```

`fireclaw deploy plan --profile examples/deployment_profiles/navigation_robot.toml.example` 已实际解析到部署
检查，并按模板预期在 `/opt/firebot/vendor_ws/devel/setup.bash` 不存在处返回
`ros_setup_missing`；这是需要现场替换的厂商占位路径，不是 Plugin manifest/config schema 错误。另有
不依赖 ROS 安装的模板 TOML + real Plugin activation 测试证明新配置可以被 loader 正确激活。

### 安全状态、失败尝试与当前结论

本次没有启动现场 Gateway/Gazebo、没有调用现存 robot 的 recover、没有修改 SQLite freeze、没有提交
运动任务。之前记录的默认 Robot Profile 安全冻结继续保留。新实机测试全部使用 tmp SQLite 和注入的
确定性 hardware observer；它验证软件信任边界，不冒充真实传感器。

曾在新 real end-to-end 测试里使用默认 operator context，请求按设计被
`emergency.recover` scope 拒绝；改用真实 policy 定义的 admin principal 后通过。这证明权限边界未因
硬件 Provider 集成而放宽。第一次完整全量回归为 `2110 passed, 7 skipped`；增加模板/真实 Profile
discovery 和连续硬件状态样本测试后，最终全量为 `2113 passed, 7 skipped`。

结论：P0 第 5 项的通用软件实现、配置合同、fail-closed 信任边界、两阶段恢复集成、状态投影、文档
和非 ROS 自动验收已经闭环。剩余不是仓库代码缺口，而是特定实体机器人部署 acceptance：替换 vendor
占位接口并在现场运行正反例。未取得该证据前，不应称“真实 firebot-01 已通过实机安全认证”。
