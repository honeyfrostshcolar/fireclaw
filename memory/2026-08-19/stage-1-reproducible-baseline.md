# 阶段 1：clean、可复现、无过度声明的提交基线

## 2026-08-19T23:41:36+08:00

### 任务目标

把 2026-08-14 至 2026-08-17 已完成但仍散落在脏工作树中的 UX lifecycle、Web Console、配置助手、
FriendlyError、package resources、simulation bundle 与 release gate 固化为一个可审计的 Git 集成检查点。
验收要求：

1. 关键 Web/setup/catalog/release 文件进入版本控制，clean checkout 可重建；
2. wheel 包含 Web 与 setup/simulation catalog 资产；
3. wheel 可在隔离环境安装并执行 smoke；
4. 全量测试通过；
5. 运行时/现场数据、凭据与生成产物不进入提交；
6. CLI、Web、错误提示与用户文档不把进程状态、任务状态或静态模板误报为机器人物理安全事实。

### 启动状态

- 分支：`agent/embodied-evaluation-collision-calibration`；开始时 HEAD 为 `d6ba36b`，相对远端领先 6 个提交。
- 开始时有 29 个 tracked modified 文件、65 个未跟踪候选文件，以及
  `data/robots/gazebo_turtlebot3/memory-runtime.sqlite3` 本地运行时数据库。
- 最近基线证据为 2026-08-17 的 `2339 passed, 8 skipped`，但关键源码、测试、资源和 release tools 尚未
  进入 Git，因此 clean checkout 不可复现。

### CodeGraph / OpenClaw analogue

- 先用 CodeGraph 检查 FireClaw `handle_stop`、`MissionGateway.recover`、FriendlyError resolver/registry、
  Web readiness/dispatch/recovery/config 路径及测试 blast radius。
- 检查 OpenClaw `openclaw/src/gateway/server-methods/system-agent.ts`：Gateway surface 明确不得自行安装或
  重启 daemon；FireClaw 保持 CLI 拥有 lifecycle side effects、Web 只投影状态/提交受控请求的边界。
- OpenClaw 没有机器人物理停止证据的直接 analogue；FireClaw 在本阶段选择 fail-closed 的 `UNKNOWN`，不
  为后续 StopEvidence 或正式两阶段恢复发明半成品合同。

### 纳入与排除边界

纳入：

- 生命周期 CLI、daemon manager、Web Console MVP；
- 配置助手与 FriendlyError 组件；
- package-owned setup template、simulation catalog/resource loader；
- deterministic simulation bundle builder、distribution checker；
- 对应单元/HTTP/分发负向测试、发布契约、设计/实施记录和跨会话 memory；
- Python 3.10 clean-smoke 兼容调整与必要的 sandbox workspace 配置兼容。

排除/忽略：

- `data/robots/**/memory-runtime.sqlite3` 及其 WAL/SHM companions；
- 已由通用规则忽略的 `*.jsonl` 任务/事件/记忆日志；
- `.fireclaw/` 本地 runtime home、`dist/`、`build/`、wheel/tarball 和 ROS build outputs；
- 未发现真实凭据、私钥、大文件或机器人现场数据进入候选集。secret signature 扫描只命中
  `tests/test_config_diff_and_snapshots.py` 中明确的假 `sk-*` fixtures。

### 无过度声明修订

1. `fireclaw stop` 只报告守护进程退出，并明确机器人物理状态为 `UNKNOWN`；不再显示“机器人安全停机”。
2. FriendlyError 静态注册表不能再发布机器人物理状态或自动处置结论：所有 template/response/list/get
   统一投影 `UNVERIFIED_ROBOT_STATUS` 与 `UNVERIFIED_ACTION_STATUS`，并有反向测试防止“绝对静止”、
   “电机已关闭”等文案回归。
3. Web 初始 HTML/JS 不再硬编码 active robot、Simulation、Profile、在线机队、运行任务、Tool、进度、
   防跌落/雷达/急停正常值；缺失 Gateway 字段保持 `UNKNOWN`。
4. Web form submit 只生成预览，不再 `parseTaskIntent(true)` 自动确认；HTTP `accepted` 显示为
   `ACCEPTED`，不伪造 `RUNNING`、`navigate_to_point` 或运动进度。
5. Mission Gateway 原 `/recover` 明确降格为本地 admission projection reset：事件名为
   `mission.admission_projection_reset`，响应固定携带 `physical_stop_confirmed=false` 与
   `robot_physical_status=unknown`，不再声称正式 two-phase recovery 或物理安全恢复。
6. Web 配置助手在 Gateway 未提供 active Profile/原文时拒绝猜测保存、diff 或回滚目标；内容快照不再被
   描述为 startup-valid/known-good 或“安全回滚”。
7. README、first-run guide 与本轮纳入的历史 plan/spec 已标明当前边界：wheel-first quickstart、Web 权威
   Profile/mode、sealed preview、正式恢复、零手写配置仍属于后续阶段。

### 验证命令与结果

解释器：`/srv/lpp-extra/miniconda3/envs/py310/bin/python`（Python 3.10）。未启动 ROS、Gazebo 或机器人。

静态/定向检查：

```text
node --check src/fireclaw_core/web_console/app.js
python -m py_compile friendly_errors.py mission_cli.py mission_gateway.py
pytest FriendlyError/lifecycle/Web no-socket subset
53 passed in 1.42s

pytest FriendlyError/lifecycle/Web HTTP subset（允许 loopback）
66 passed in 8.59s
```

第一次在受限 sandbox 内运行 HTTP 子集时，14 个测试因创建 loopback socket 得到
`PermissionError: [Errno 1] Operation not permitted`；相同命令在允许本机 loopback 后全部通过，故该次失败
被判定为环境限制而非产品断言失败。

最终全量回归：

```text
2342 passed, 8 skipped in 231.82s
JUnit: /tmp/fireclaw-stage1-full.xml
```

最终发布产物目录：`/tmp/fireclaw-stage1-release.FkYlxD`

```text
fireclaw-0.1.0-py3-none-any.whl
888143 bytes, 246 files
SHA-256 07692ad38c9995930fdf03ef45e7c1c6dbe24ef97223ab5a39a99cd85aa7dae4

fireclaw-sim-turtlebot3-burger-v1.tar.gz
8749596 compressed bytes, 34482986 unpacked bytes, 652 payload files
SHA-256 5488b1e6d0f208bb309ea40a0b50b0620f06cf264b7e8a7f542b3b8c2f22b80e

Wheel PASS
Simulation Bundle PASS
Isolated Smoke PASS
Verdict: ALL PASSED
```

`git diff --check`、候选 untracked trailing-whitespace 扫描均无输出。

### 当前结论

阶段 1 的工程验收已经满足：当前候选集能构建完整 wheel/bundle、隔离安装、通过完整 release gate 与全量
测试，且产品界面在缺少权威证据时 fail closed。该结论只覆盖可发布提交基线，不代表阶段 2–7 已闭环，
也不代表完成 ROS/Gazebo/实机验收。

### 已知后续缺口

- wheel 安装后的 setup → start → open quickstart 与 bundle materialization；
- Gateway active Profile/runtime mode/readiness 权威合同；
- immutable PlanArtifact + one-use confirm/consume；
- Adapter StopEvidence producer 与 Robot Gateway 正式 request/confirm recovery 接入；
- 单一 ProfileService、Plugin-owned schema、原子保存/凭据引用/known-good rollback；
- support bundle、升级向导、移动端服务端只读、教学任务与用户研究。

### 提交与 Git-only clean-tree 复验

提交候选集按 95 个明确路径暂存，没有使用 `git add .`：

```text
95 files changed, 19456 insertions(+), 245 deletions(-)
unstaged diff: empty
untracked candidates: empty
staged banned paths: empty
staged files > 1 MiB: empty
git diff --cached --check: PASS
```

创建单个集成提交，subject 为 `chore: 固化可复现 UX 交付基线`；本记录更新通过 amend 纳入同一提交，
最终 commit ID 以 `git log -1` 为准，未推送远端。

随后使用 `git archive HEAD` 导出只包含已提交文件的 clean tree：

```text
Clean root: /tmp/fireclaw-stage1-clean.bBF3lj
distribution-focused pytest: 25 passed in 19.73s

wheel: 888143 bytes, 246 files
SHA-256 9cb4f5b836bcda40b211d4c0a044b834406f2cd2cd43ddaad23fc1b8406f7526

simulation bundle: 8749596 compressed bytes, 34482986 unpacked bytes, 652 payload files
SHA-256 5488b1e6d0f208bb309ea40a0b50b0620f06cf264b7e8a7f542b3b8c2f22b80e

Wheel PASS
Simulation Bundle PASS
Isolated Smoke PASS
Verdict: ALL PASSED
```

clean-tree bundle hash 与工作树构建完全相同。两个 wheel 均为 888143 bytes / 246 files 且合同、安装 smoke
通过，但 SHA-256 不同；差异来自普通 wheel 构建的 ZIP 时间元数据，本阶段不宣称 bit-for-bit wheel
reproducibility。当前证明的是：Git-only clean tree 能重建合同一致的 wheel/bundle 并在隔离环境安装运行。

### 下一步

进入阶段 2 前先保持本提交为 clean checkpoint。阶段 2 应优先实现 versioned simulation bundle
materialization 和 simulation-only setup → start → open orchestration；实机路径仍只允许被动发现和预检，
不得运动。
