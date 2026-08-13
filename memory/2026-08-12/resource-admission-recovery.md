# 默认 Robot Profile 持久安全冻结恢复

## 2026-08-12T17:11:30+08:00 — 实现完成并停在操作员确认门

### 任务目标

为默认 `gazebo_turtlebot3` Profile 的 `physical_runtime_stop_unconfirmed` 持久冻结实现
“可信现场停止证据 + 操作员显式确认 + 原子解冻 + 审计”流程。不得重启绕过、删除数据库或直接
修改 `runtime_flags`。

### OpenClaw analogue

先用 CodeGraph 检查了 OpenClaw 的持久审批记录：pending/allowed/denied/expired/cancelled 状态、
reviewer authorization、expiry、first-answer-wins 与事务更新。FireClaw 复用“持久请求 + 已认证
reviewer + 过期 + 原子决议”形状；新增 OpenClaw 不具备的机器人停止证据、资源租约校验和
fail-closed 约束。

### 已实现

- `SqliteAuthoritativeRuntimeStore` 新增持久
  `resource_admission_recovery_requests` 表；
- `SqliteResourceLeaseManager` 支持冻结 revision snapshot、创建/读取/过期/确认恢复请求；
- 新冻结会原子 supersede 旧 pending request；错误短语、过期、冻结 revision 变化、活动租约、
  stale/invalid evidence 全部拒绝；
- 确认时在同一个 `BEGIN IMMEDIATE` SQLite 事务中更新 admission、request 与事件账本；
- Gateway 新增独立 admin scope `emergency.recover` 以及：
  - `GET /resource-admission`
  - `POST /resource-admission/recovery/request`
  - `POST /resource-admission/recovery/confirm`
- 请求阶段和确认阶段都由服务器重新采集证据；payload 的 operator role、`stopped=true` 或
  `stop_evidence` 不构成证据；
- public state/audit 不暴露 confirmation phrase；
- public Plugin SDK 新增 trusted stop-evidence service contract；
- Navigation Plugin 只在 simulation mode 注册 witness；real mode 没有 hardware witness 时
  保持冻结；
- ROS1 witness 执行 `cancel_all_goals()`，持续向 `/cmd_vel` 发零 `Twist`，同时要求
  `/move_base/status` 无 active states，并观察 `/odom`：至少 3 个 fresh samples、连续至少
  0.75 秒、linear <= 0.01 m/s、angular <= 0.02 rad/s；证据有效 15 秒；
- 更新 Plugin Runtime/ROS1 部署文档与 Navigation Plugin README。

### 修改文件

- `src/fireclaw_core/infra/runtime_state.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/gateway/control.py`
- `src/fireclaw_core/gateway/method_scopes.py`
- `src/fireclaw_plugin_sdk/safety.py`（新增）
- `src/fireclaw_plugin_sdk/__init__.py`
- `src/fireclaw_plugin_sdk/tools.py`
- `extensions/navigation-move-base/plugin/move_base.py`
- `extensions/navigation-move-base/plugin/entrypoint.py`
- `extensions/navigation-move-base/fireclaw.plugin.json`
- `extensions/navigation-move-base/README.md`
- `docs/deployment/plugin-runtime-deployment.md`
- `docs/deployment/ros1-deployment-guide.md`
- `tests/test_resource_leases.py`
- `tests/test_move_base_navigation_plugin.py`
- `tests/test_gateway.py`
- `tests/test_control.py`
- `tests/test_method_scopes.py`

### 测试与命令

```text
/home/lpp/miniconda3/envs/py310/bin/python -m py_compile <changed modules/tests>
# passed

/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_resource_leases.py tests/test_move_base_navigation_plugin.py \
  tests/test_control.py tests/test_method_scopes.py tests/test_gateway.py
# 115 passed in 29.46s（Gateway socket lane 在沙箱外运行）
```

最初错误地尝试 `.venv/bin/python`，仓库没有该解释器；随后 `/usr/bin/python3` 也没有 pytest。
已改用当天既有环境 `/home/lpp/miniconda3/envs/py310/bin/python`。沙箱内 Gateway socket tests
因 `PermissionError: [Errno 1] Operation not permitted` 失败，按权限规则在沙箱外重跑后全部通过；
这不是业务代码失败。

### 正式部署与现场第一阶段

旧部署静态状态为 `stale`。正常 `deploy apply` 生成并原子切换新 release，旧 release 保留：

```text
fingerprint: 920c713e46feb43edfcce0d233bb82eb55db60222fee5669e7dfeedce7321c80
release: /home/lpp/.fireclaw/deployments/gazebo-turtlebot3-burger/releases/920c...c80
```

正式 `fireclaw-bringup` 已启动（exec session 40413），runtime readiness 9/9：package、3 nodes、
`/scan`、`/odom`、`/move_base` action、2 TF 全部通过。正式 `fireclaw-gateway` 已启动
（exec session 20837）。

冻结状态仍是：

```text
closed=true
reason=physical_runtime_stop_unconfirmed
task_id=task-2ebb389110dd492280a26dd1adaa617d
closed_at=2026-08-12T08:18:22.074605+00:00
revision=1
active_leases=[]
```

已调用第一阶段 request，结果 `pending_confirmation`，没有解冻：

```text
request_id=recovery-6e6e28cccb994ee2a65e4ce38c65ced8
expires_at=2026-08-12T09:15:51.881711+00:00
confirmation_phrase=RECOVER gazebo_turtlebot3 recovery-6e6e28cccb994ee2a65e4ce38c65ced8
```

现场证据：

```text
status=stopped
stop_reasserted=true
active_goal_count=0
stationary_samples=16
max_observed_linear_speed=6.996011362594747e-07 m/s
max_observed_angular_speed=1.0367741054888065e-05 rad/s
gateway_active_task_ids=[]
active_leases=[]
blockers=[]
```

### 当前结论与下一步

- **当前仍冻结**，未调用 confirm，未删除/绕过任何状态；
- 必须由用户明确回复完整 confirmation phrase；若超过 expiry，创建新 request 并重新采证，不能
  复用旧请求；
- 用户确认后调用 confirm；Gateway 会再次采集新的现场证据，再原子解冻；
- 随后验证 `GET /resource-admission`、提交一个安全短距离导航 smoke、检查 recovery/action/resource
  audit，运行全量 pytest、`git diff --check`，最后按 Gateway -> bringup 顺序正常停止本次进程；
- 若用户不确认或请求过期，应保持冻结并正常停止本次 ROS/Gateway，不得自行确认。

### 研究层面

这是安全工程与可审计执行基础设施，不是新的 Agent/机器人算法贡献。它提升后续实验的故障恢复
可重建性；若要形成研究贡献，还需定义 stop-evidence trust model、对抗/故障注入、恢复延迟与
误解冻率指标，并与人工运维或单一 ACK baseline 做系统评估。
