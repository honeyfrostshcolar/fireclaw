# UX 可发布交付基线审查修复记录

## 2026-08-17T00:12:00+08:00

### 任务目标

在用户明确授权“直接帮我修复”后，修复
`memory/2026-08-16/ux-delivery-baseline-review.md` 中的全部阻断项，保持现有脏工作树，
不 stage、不 commit，不启动 ROS、Gazebo 或机器人。

### 当前进展

第一阶段“可发布交付基线”的审查阻断项已经全部修复；最终真实 wheel、simulation bundle、
isolated installed smoke 和全量 pytest 均通过。工作树仍包含用户原有的大量 tracked 修改与 untracked
实现文件，尚未形成 Git 提交。

### 已完成

#### 1. Package resource ID 路径穿越

- `src/fireclaw_core/resources/__init__.py` 改为固定 ID 到文件名的 allowlist；不再对用户 ID 做
  `strip()`、`replace()` 后直接 `joinpath()`。
- `gazebo_turtlebot3` 与 `turtlebot3-burger-v1` 之外的 ID（包括 `/`、`..`、反斜杠、空串和带空格
  变体）均 fail closed。
- catalog JSON 拒绝重复 key，并在返回前执行 schema 校验。

#### 2. Simulation catalog 与 provenance/license 门禁

- 新增 `validate_simulation_bundle_catalog()`，校验：
  - schema version、canonical bundle ID、semantic bundle version；
  - 安全且唯一的 POSIX include/required/provenance paths；
  - required/provenance path 必须被 include allowlist 覆盖；
  - forbidden segment/suffix、正整数 size budget；
  - 每个 upstream source 的唯一 ID、HTTPS repository、40 字符 Git commit、license 标识和 evidence paths。
- `turtlebot3-burger-v1.json` 现在声明：
  - ROS Navigation commit `f44bb1fc2810399165115cc98b530fe4b9397c18`，BSD-3-Clause；
  - TurtleBot3 commit `4ae959ea6a52415c90bf752d2b76e3e28f8a87e2`，Apache-2.0；
  - TurtleBot3 Messages commit `76e78b0a34e07cf1dd16dafdc54c44f35c5b83eb`，Apache-2.0；
  - TurtleBot3 Simulations commit `e9d809ca8e3bf889c0275e4103b15a341ffab888`，Apache-2.0。
- archive inspector 会确认 repository 与 commit 文本实际存在于打包的 `*.repos` / `UPSTREAM.md`
  证据中，并要求相应 LICENSE/package/source license evidence 存在且非空。

#### 3. Simulation bundle builder/inspector 加固

- builder 拒绝 include path component、目录项或文件项中的 symlink，拒绝 special file，检查路径仍在
  repo root 内，按 catalog 显式过滤 forbidden path。
- 构建改为流式散列与 tar 写入；源文件在 hashing/packing 期间发生 size/mtime 变化会失败。
- 使用临时文件完成后原子替换输出；压缩/解压预算超限不留下半成品。
- inspector 使用 streaming gzip tar 读取，先检查 compressed size，再计算 SHA；逐 member 在读取内容前检查
  unpacked budget，限制 member 数、manifest 大小和 provenance capture 大小。
- 拒绝 unsafe path、duplicate member、link/device/FIFO/special type、非确定性 mtime/ownership/order。
- manifest 与 archive 文件集合必须双向相等，并逐文件校验 SHA-256、size、mode；同时核对
  schema/bundle ID/version/total_files/unpacked_bytes、required paths、provenance paths 和 include allowlist。

#### 4. Full release verdict fail closed

- `DistributionCheckReport` 新增 `gate_errors`。
- full verdict 现在强制要求 wheel、simulation bundle 与 installed smoke 三者均存在且通过。
- 缺 bundle、漏 catalog 或 `run_smoke=False` 不再返回成功；CLI 的 `--simulation-bundle` 改为 required，
  `--skip-smoke` 明确只用于诊断且最终 verdict 必须失败。
- simulation catalog 始终从“正在检查的 wheel”读取；调用方提供另一份 catalog 时必须逐值一致。
- wheel inspector 增加 compressed/unpacked/member/metadata budgets、canonical path、duplicate、特殊类型、
  encrypted member、CRC 和精确 console entry point 校验。

#### 5. 干净环境可执行 release tools

- 两个 release CLI 都会根据 `--repo-root` 显式把 `<repo-root>/src` 加入其进程路径，不再依赖 editable
  install 或 ambient `PYTHONPATH`。
- 新增无 pip、未安装 FireClaw 的 clean venv 回归：bundle builder 能成功构建；distribution checker 能
  正常给出失败报告而不是 `ModuleNotFoundError`。
- `docs/release/distribution-contract.md` 改为显式 `PYTHON_BIN`、Python >= 3.10、完整 gate 命令，且说明
  `--skip-smoke` 不构成发布通过。

#### 6. 取消三态物理安全语义

- `MissionGateway` 的 HTTP cancel 路由不再根据普通 `cancel_requested/cancelled/stopping` 返回值伪造
  `task.cancelling` / `stopping` 事件。
- Web Console：
  - `task.cancel_requested` / `mission.cancel_requested` / legacy `task.cancelling` 只能进入请求态；
  - 只有权威 `robot.stopping` 能进入 stopping；
  - `task.cancelled` / `mission.cancelled` 只表示任务控制终态，不进入物理停止态；
  - 只有 `task.stopped` / `robot.stopped_confirmed` 同时携带
    `physical_stop_confirmed=true` 与 object `stop_evidence` 才进入 `stopped_confirmed`。
- Web 初始 readiness、E-STOP、底盘通信和错误 fallback 都改为 `UNKNOWN`/“未收到证据”，不再在网络
  不可达时默认声称底盘静止或急停已释放。
- README 与 first-run 文档同步说明：readiness 是任务准入投影，不是物理静止证明；未接入权威
  Adapter 停止反馈时第三态不会出现。

#### 7. 测试与工作树卫生

- 补齐 resource traversal、catalog provenance、forbidden include、symlink、extra member、duplicate/link、
  manifest metadata mismatch、unsubstantiated upstream metadata、partial release verdict、clean venv 与取消
  状态回归；原空测试已变为实际断言。
- `git diff --check` 通过。
- 对本次相关 tracked/untracked 源码、测试、文档执行尾随空格扫描，无输出；修复了 Web CSS/JS 和
  resource 文件中的尾随空格。

### 验证命令与结果

使用解释器：

```text
/srv/lpp-extra/miniconda3/envs/py310/bin/python (Python 3.10.4)
```

聚焦验证（最终版本）：

```text
node --check src/fireclaw_core/web_console/app.js
python -m py_compile ...
pytest distribution/resources/bundle/Web contract subset
43 passed in 17.58s
```

UX 子集：

```text
186 passed in 37.22s
JUnit: /tmp/fireclaw-ux-regression.xml
```

Mission Gateway：

```text
49 passed in 21.53s
JUnit: /tmp/fireclaw-mission-gateway-regression.xml
```

最终全量回归（允许 loopback socket，未启动 ROS/Gazebo/机器人）：

```text
2339 passed, 8 skipped in 216.20s
JUnit: /tmp/fireclaw-final-full-regression.xml
```

最终发布产物：

```text
/tmp/fireclaw-final-validation.I9lugb/fireclaw-0.1.0-py3-none-any.whl
889897 bytes, 246 files
SHA-256 33b8a176c1d8e76108d507d08bb0b3c1eb8cbebd99d5165794adf592d68b433f

/tmp/fireclaw-final-validation.I9lugb/fireclaw-sim-turtlebot3-burger-v1.tar.gz
8749596 compressed bytes, 34482986 unpacked bytes, 652 payload files
SHA-256 5488b1e6d0f208bb309ea40a0b50b0620f06cf264b7e8a7f542b3b8c2f22b80e
```

最终 distribution report：

```text
Wheel PASS
Simulation Bundle PASS
Isolated Smoke PASS
Verdict: ALL PASSED
```

另以 `--skip-smoke` 实测得到 exit code 1、`Release Gate Errors` 与 `Verdict: FAILED`，确认 partial check
不会误报完成。

### 文件修改（本轮直接涉及）

- `src/fireclaw_core/resources/__init__.py`
- `src/fireclaw_core/resources/simulation_bundles/turtlebot3-burger-v1.json`
- `tools/release/simulation_bundle.py`
- `tools/release/distribution_check.py`
- `src/fireclaw_core/mission/mission_gateway.py`
- `src/fireclaw_core/web_console/app.js`
- `src/fireclaw_core/web_console/index.html`
- `src/fireclaw_core/web_console/style.css`
- `tests/test_distribution_resources.py`
- `tests/test_simulation_bundle_release.py`
- `tests/test_distribution_release_check.py`
- `tests/test_web_console_app.py`
- `tests/test_mission_gateway.py`
- `README.md`
- `docs/getting-started/first-run-setup.md`
- `docs/release/distribution-contract.md`

### 当前问题

- 工作树在本轮开始前已经很脏，且 UX/config/errors/resources/Web/tools/tests/docs 中有大量 `??` 文件。
  本轮按约定没有 stage/commit；因此当前本机验证通过不代表 clean clone 已包含这些实现。
- 目前默认机器人 Adapter 没有被本轮改造成权威 stop-evidence producer。Web 第三态因此会安全地保持未确认，
  直到后续 Adapter 发出符合契约的事件；这不是本轮 release gate 的伪完成项。
- 未进行 ROS1/Gazebo acceptance 或真实机器人测试；本轮只验证发布物、CLI/package resources、HTTP
  loopback 与 Python/JS 测试。

### 当前结论

`ux-delivery-baseline-review.md` 列出的五个必须修复问题和测试/卫生缺口均已有代码、负向测试与真实
产物证据。第一阶段“可发布交付基线”可以从 `CHANGES REQUIRED` 更新为“本地实现与验证完成，待建立
明确 Git change set/commit”。这不代表原始 UX-P0/P1 全部路线已经完成，也不代表实机物理停止证明已经
接入。

### 下一步

1. 由用户确认本次应纳入提交的 untracked UX/resources/tools/tests/docs 范围。
2. 在不混入 `data/robots/`、私密配置或无关用户改动的前提下建立 proposed change set，再决定是否提交。
3. 下一阶段单独实现 robot Adapter 的 `robot.stopping` 与带 `stop_evidence` 的
   `robot.stopped_confirmed`/`task.stopped` producer，并做仿真/实机证据契约测试。

### 需要运行的命令

当前没有必须补跑的命令。若建立提交前 change set，应再次运行：

```bash
git status --short
git diff --check
/srv/lpp-extra/miniconda3/envs/py310/bin/python -m pytest -q
```

并按 `docs/release/distribution-contract.md` 重新生成产物与执行完整 distribution check。
