# 2026-08-16 FireClaw 小白 UX 路线完成度审计

## 2026-08-16T22:29:24+08:00 — 当前工作区复核

### 任务目标

只读复核用户列出的 UX-P0 首次使用闭环、本地 Web Console、UX-P1 零手写配置、友好错误提示和后续体验项，判断当前工作区是否已经真正完成。重点区分：源码/页面骨架存在、单点测试通过、端到端产品闭环可用、以及已提交可复现交付。

### 当前进展

- 结论：不能把整份路线标记为完成。
- CLI 生命周期基础、active Profile、顶层帮助分层、五页 Web 骨架、配置/错误基础模块均已存在。
- `setup` 的仿真配置、部署 materialization 和幂等复用可用，但它不启动 daemon、不打开浏览器；实机 setup 也没有自动发现，只验证已有人工审查 Profile。
- Web Console 与零配置模块存在多个未被现有测试覆盖的前后端/安全语义断点，当前更接近 MVP 骨架或原型，不是可信的小白首次任务闭环。
- 绝大部分 2026-08-14 UX 新增实现仍是未提交、未跟踪文件；当前 wheel 不包含 UI 与 setup 仿真资产。

### 启动前检查

- 读取最近两日记录：
  - `memory/2026-08-14/*.md`
  - `memory/2026-08-13/novice-user-experience-roadmap.md`
- 检查 `git status --short --branch`：当前分支领先远端 6 个提交，且存在大量既有 tracked 修改和 untracked UX 文件；未覆盖或清理用户改动。
- `.codegraph/` 存在；先用 CodeGraph 检查 setup/active profile/lifecycle、Web Console、Mission Gateway、配置与友好错误调用链，再对已经定位的文件做定点读取和 literal `rg` 查询。

### OpenClaw analogue / CodeGraph 工作记录

本轮是现有实现审计，没有设计或修改 FireClaw 业务模块。CodeGraph 主要检查：

- `src/fireclaw_core/infra/user_setup.py`：`setup_fireclaw`, `handle_setup`, `load_active_profile`, `resolve_active_profile_path`；
- `src/fireclaw_core/infra/daemon_manager.py`：`start_daemon`, `stop_daemon`, `open_console`, `get_daemon_status`；
- `src/fireclaw_core/mission/mission_cli.py`：核心命令分组及 `handle_start/stop/open`；
- `src/fireclaw_core/web_console/app.js`：五页状态模型、任务预览、取消三态、恢复、配置；
- `src/fireclaw_core/mission/mission_gateway.py`：`readiness`, `plan_mission`, `/tasks`, `/recover`, `/config/*`；
- `src/fireclaw_core/config/*` 与 `src/fireclaw_core/errors/*`。

前一轮 setup 实现已经在 `memory/2026-08-13/novice-user-experience-roadmap.md` 记录了 OpenClaw onboarding analogue；本轮没有引入新 API，因此没有重新设计或另行复制 upstream 结构。

### 关键工程结论

#### 1. UX-P0 首次使用闭环：部分完成

- 已完成：
  - `fireclaw setup` 交互选择 simulation/real；
  - simulation 自动生成无凭据 TurtleBot3 Profile、生成 active Profile、materialize Runtime release；
  - 第二次 setup 返回 `reused=true`，不覆盖已有 Profile；
  - real 模式不自动 deploy/运动；
  - `start/open/status/stop` 可省略 `--profile`，顶层帮助默认只显示这 5 个核心命令。
- 未完成/断点：
  - `setup_fireclaw()` 的 docstring 与返回值明确“不启动 Gateway 或机器人动作”，成功后仍返回 `next_command = fireclaw deploy run`；不是 setup 后自动启动、自动打开页面的单命令闭环，且提示与新手 `start` 命令不一致；
  - real setup 要求用户先提供 reviewed Profile，没有调用 `RosGraphDiscoverer` 自动发现；
  - `status` 的某些下一步仍提示高级命令 `fireclaw deploy service install ...`；
  - 文档明确要求完整源码仓库运行 setup；普通 wheel 安装路径未闭环。

隔离验证目录 `/tmp/fireclaw-setup-audit.7DZyt8`：

- 第一次 setup：`status=ready_to_start`, `deployment.status=installed`, `reused=false`, `robot_action_started=false`；
- 第二次 setup：同 fingerprint，`reused=true`；
- `status --no-runtime-check` 在不传 profile 时正确使用 active Profile，但服务未启动，返回 `phase=offline`。

#### 2. 当前机器人配置与命令收敛：CLI 层基本完成，产品层部分完成

- active Profile state 生效；日常 CLI 不要求反复输入 profile/server/gateway/token，后者从 Profile/环境变量解析；
- 顶层 `fireclaw --help` 只显示 `setup/start/open/status/stop`，`--all` 保留高级命令兼容；
- Web Console 没有读取 active Profile：`app.js` 默认仍硬编码 `profiles/turtlebot3_burger.json`，与 setup 生成的 `.toml` Profile 不一致，因此“当前配置”尚未贯通 CLI 与 Web。

#### 3. Web Console：五页骨架存在，但关键行为未闭环

- 五页 HTML、静态路由、自然语言输入、预览卡、执行时间线、恢复页、设置页都存在；专项测试通过。
- 首页模式徽标在 `renderOverview()` 中硬编码为 `SIMULATION`，`/readiness` 也没有提供 mode/profile，因此连接实机时仍会显示仿真，违反仿真/实机强区分。
- `/plan-mission` 使用关键词硬编码预览；提交 `/tasks` 时 Gateway 忽略前端传入的 `target_robot` 和 `plan`，会重新按 command 规划，预览不构成实际执行合同。
- 页面有“直接下发”按钮；表单首次 submit 会调用 `parseTaskIntent(true)` 并在解析后自动执行，绕过显式确认卡要求。
- 取消三态只在 UI 文本/状态机上存在：
  - 点击后在网络确认前即显示 `cancel_requested`；
  - Gateway 对 `cancel_requested/cancelled/stopping` 都发布 `task.cancelling`；
  - 前端把 `task.cancelled` 或 `mission.cancelled` 直接映射成 `stopped_confirmed`；
  - 全仓没有 UI 之外的 `robot.stopping`/`task.stopped` 生产路径，且没有速度归零/独立停止证据检查；不能可信声称机器人已经确认停止。
- `/recover` 只检查一个 boolean、清空 report 并发布 `safety.recovered`，没有复用现有 resource-admission recovery request/confirm、TTL 和 stop evidence 流程；“两阶段”目前主要是页面勾选 + 单次请求。

#### 4. UX-P1 零手写配置：基础组件存在，端到端未完成

- 已有 5 个模板、ROS graph probe、Schema dataclass、diff、snapshot、SecretManager 和 CLI/API/页面骨架。
- `TemplateManager` 生成的 TurtleBot3 TOML 能被 robot profile loader 读取，但缺少 `[deployment]`，不能被 runtime deployment loader 使用；不是可直接 start 的完整 Profile。
- Plugin manifests 已含 `config_schema`，但 `/config/schema` 返回的是另一套硬编码 `get_core_config_schemas()`，CLI/Web 尚未消费 Plugin-owned schema。
- `/config/discover` 返回 `active_topics`/`matched_topics`，前端读取 `discovered_topics`/`suggested_rules`，自动映射数量会保持 0。
- Web 没有加载真实 current Profile；diff 使用硬编码 simulation 旧配置，生成字段全部塞入 `[robot]` 且字符串化；默认保存路径还是 `.json`。
- `/config/test-field` 对大多数 topic/action 只验证以 `/` 开头或回显 configured value，并不真正检查 topic/action/service 连通性。
- `/config/save` 没有 Schema/Profile 校验、没有路径边界/原子写入，也没有调用导入的 `SecretManager`；原始 token/cert 仍可被写入普通 Profile。
- snapshot/rollback 能恢复历史文本，但保存前不验证“可用”，所以尚不是“恢复上一份可用配置”。

#### 5. UX-P1 友好错误：展示框架完成，可信语义和覆盖率未完成

- 已有集中注册表、四段式 CLI formatter、Web Toast/modal、折叠技术详情和推荐动作按钮；结构测试通过。
- Gateway 仍有大量 `_write_error()` 只返回 `status/message`，并非所有错误都结构化。
- Web fallback 在网络/未知错误时固定声称“底盘保持安全锁定/执行器保持静止/已拦截”，没有权威状态证据。
- 多个 registry 模板声称已经关电机、降速、切换历史可信帧等动作，但解析器只格式化文本，没有证明这些措施真实执行。
- 许多 suggested action（例如 restart_lidar、resync_frames）没有对应 handler，点击只会关闭弹窗。

#### 6. 后续体验项

- 用户侧自动脱敏支持包：未实现；仓库有实验/ROS proof bundle，不是 `fireclaw support` 或 Web 支持包流程。
- 安装/升级/应用回滚向导：未实现；仅配置 snapshot rollback 存在。
- 中文默认、文字/图标辅助非纯颜色表达：已有明显进展，但仍有大量英文状态与缺少术语 tooltip，属于部分完成。
- 移动端只读状态/告警：未实现；只有响应式 CSS，危险控制没有移动端只读限制。
- 新手教学任务/仿真沙盒：有 simulation setup 和任务模板 chip，但没有引导式教学闭环，属于部分完成。
- 真实新用户可用性测试：未发现受试者、首次任务时间、配置错误数或求助次数记录，未完成。

### 发布/可复现状态

- 已提交：setup 基线在 commit `ee8b2be feat: add guided first-run simulation setup`。
- 当前仍 untracked 的关键 UX 文件包括：
  - `src/fireclaw_core/infra/daemon_manager.py`
  - `src/fireclaw_core/web_console/`
  - `src/fireclaw_core/config/`
  - `src/fireclaw_core/errors/`
  - 对应 lifecycle/Web/config/friendly-error tests。
- 本地构建 wheel：`fireclaw-0.1.0-py3-none-any.whl` 共 240 entries；UX asset 命中只有 `fireclaw_core/web_console/__init__.py`，没有 `index.html/style.css/app.js`、setup template 或 TurtleBot3 ROS assets。安装包不支持声称的首次使用闭环。
- `git diff --check` 当前还有两处既有 trailing whitespace：`README.md:146`、`src/fireclaw_core/mission/mission_gateway.py:365`。

### 验证命令与结果

1. CLI 帮助：

```text
PYTHONPATH=src python3 -m fireclaw_core --help
PYTHONPATH=src python3 -m fireclaw_core {setup,start,open,status,stop} --help
```

结果：核心命令分层正确，Profile 可选；status 仍暴露高级覆盖参数。

2. UX 专项测试：

```text
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q \
  tests/test_user_setup.py tests/test_daemon_manager.py \
  tests/test_user_lifecycle_cli.py tests/test_cli_help_grouping.py \
  tests/test_web_console_routes.py tests/test_web_console_ui.py \
  tests/test_web_console_app.py tests/test_config_core.py \
  tests/test_config_diff_and_snapshots.py tests/test_config_cli_and_api.py \
  tests/test_config_web_integration.py tests/test_friendly_errors.py \
  tests/test_friendly_cli_and_api.py tests/test_friendly_web_integration.py
```

结果：沙箱内首次为 141 passed / 34 failed，34 个失败均是禁止创建 localhost socket 的 `PermissionError`；按权限流程在沙箱外重跑后 `175 passed in 20.84s`。

3. 完整回归：

```text
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q
```

结果：`2313 passed, 8 skipped in 196.10s`。

4. wheel 审计：

```text
python -m pip wheel --no-deps --no-build-isolation --wheel-dir <tmp> .
```

结果：构建成功，但 UI/setup/robot assets 未进入 wheel。构建过程产生的 workspace `build/` 已移动到 `/tmp/fireclaw-build-artifact-20260816-ux-audit`，未删除用户文件。

### 当前问题

现有测试绿灯主要证明单点 Python 模块与静态 DOM/字符串合同没有回归，不证明：安装包闭环、setup 自动启动/打开、Web 读取 active Profile、预览与实际计划一致、ROS 自动发现预填、真实停止证据、真实两阶段恢复、秘密隔离或配置可启动性。当前文档与 memory 中“全量完成”的说法高于实际端到端成熟度。

### 当前结论

可以把当前状态称为“CLI 基础闭环 + Web/配置/友好错误 MVP 骨架已实现并通过现有测试”，不能称为用户所列所有 UX-P0/P1 和后续体验项已经完成。最优先的工程风险是：停止确认和恢复语义、simulation/real 模式显示、预览-执行一致性、active Profile/配置保存贯通、安装包资产。

### 下一步建议

1. 先补 P0 产品级 E2E：wheel install -> setup -> start -> open -> preview -> explicit confirm -> task -> stop，并让 setup 至少输出/调用统一 `start/open` 路径；
2. 修复 Web 权威状态：readiness 提供 mode/active profile，去掉硬编码 simulation；预览产生不可变 plan token，提交时校验并执行同一计划；
3. 取消/恢复只消费权威物理证据事件，不把 mission/task cancelled 当成 stopped confirmed，并复用 resource-admission 两阶段恢复；
4. 统一 Plugin manifest schema、CLI 与 Web，读取 active Profile，做完整 Profile 验证、原子写入、verified snapshot 和 SecretManager 强制隔离；
5. 加浏览器级 E2E/contract tests，覆盖真实 JSON 字段，而不是只检查 JS 中存在字符串；
6. 完成 wheel/package-data 后，再做支持包、移动端只读、教学流程与 5–8 名新用户可用性实验。

### 需要运行的命令

若进入修复阶段，优先新增并运行：

```text
pytest -q tests/<new-wheel-first-run-e2e>.py
pytest -q tests/<new-web-contract-e2e>.py
pytest -q tests/<new-cancel-stop-evidence>.py
pytest -q tests/<new-config-active-profile-e2e>.py
pytest -q
```

本轮没有修改业务代码、没有启动 Gazebo 或真实机器人、没有改变任何安全冻结或机器人状态。
