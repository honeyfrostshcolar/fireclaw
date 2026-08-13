# FireClaw 小白用户体验路线复核

## 2026-08-13T11:54:26+08:00 — 从健壮性转向低门槛产品体验

### 任务目标

用户确认此前 P0/P1 主要在补 FireClaw 健壮性，希望下一阶段从普通用户便利性出发：安装、配置、界面、
提示和任务操作都应简单，尽量不要求 ROS、Gateway、TLS、Profile 或 systemd 知识。本轮只做只读产品
复核和路线建议，不修改业务代码、不启动机器人、不解除默认 Profile 的持久安全冻结。

### 当前进展

- P0 已有统一 status/doctor/recover、SSE 任务续播、受管 Runtime 和无真机硬件安全准备；
- P1 已有七类软件/仿真故障矩阵；
- 这些能力使构建用户界面具备可信后端基础，但当前默认入口仍是工程 CLI，而非面向小白的产品主路径。

### 已检查

- 最近记录：`memory/2026-08-12/user-experience-robustness-review.md`、
  `memory/2026-08-13/fault-injection-acceptance.md`；
- 当前 `git status --short`，保留大量既有未提交改动；
- CodeGraph：Robot Profile 加载、Mission Gateway client、Mission/部署相关入口与用户旅程；
- `fireclaw --help`、`mission --help`、`status --help`、`recover --help`、`deploy --help`、
  `robot-profile --help`；
- README 安装与“快速验证”路径；
- 根 FireClaw（排除 `openclaw/`）没有独立 Web/frontend/package 入口。

一次先尝试了不存在的 shell 子命令 `codegraph context`，返回 `unknown command 'context'`；立即按根
AGENTS 指定方式改用 `codegraph explore` 并取得当前源码与关系。没有因此修改代码。

### 当前问题

1. 顶层 CLI 暴露超过 20 个命令，包含 `submit-subtask`、`trace`、`events`、`lifecycle-check`、
   `robot-gateway` 等内部概念；新手不知道主入口。
2. `status` 要求 `--profile`，帮助中暴露 Gateway/Mission Gateway、两套 token、两套 TLS 和 output root；
   这些适合管理员，不适合日常操作员。
3. `robot-profile` 只有 export/discover/diff/confirm，没有 `init`、模板选择、表单生成或测试连接向导；真实
   Profile 仍需手写 TOML 并理解 Plugin/ROS topic。
4. `mission` 仍假设 Mission Gateway 已经存在；用户必须先理解部署和服务关系。
5. README 的“快速验证”首先是全量 pytest、devtools demo 和底层 doctor，而不是从零到第一条仿真任务。
6. 当前没有 FireClaw 自己的操作员 Web UI；安全状态、任务、审批、恢复和配置能力虽已存在，但缺少一个
   统一可视入口。
7. 现有错误码和完整证据适合审计，默认界面仍需转换成“发生了什么、机器人现在是否安全、系统做了
   什么、用户下一步点哪里”的四段式提示。

### 推荐产品原则

- 默认只展示新手路径，专家功能放入“高级/开发者”层，保留现有 CLI/API 兼容性；
- progressive disclosure：先给结论和可执行动作，再允许展开 reason code、JSON、日志和证据；
- 简单化不能削弱物理安全：simulation/real 永远显式区分，确认不能被通用 `--yes` 绕过，取消请求不能
  冒充机器人已停止；
- 配置应由模板、自动发现和 schema 驱动表单生成，避免 UI 和 Plugin 配置各自维护一套事实；
- 每一个阻塞页面只能给安全动作，不显示会诱导用户删除状态、换 dedupe key 或强制重启的捷径。

### 推荐顺序

#### UX-P0：从零到第一条任务

1. 新增 `fireclaw setup`/首次启动向导：选择“仿真体验”或“连接真实机器人”；仿真模式自动创建 workspace、
   选 TurtleBot3 模板、部署和启动，真实模式只做发现/预检，不自动致动。
2. 建立单一用户配置与 active profile：日常命令不再重复 `--profile`、server、token/TLS；高级覆盖项仍保留。
3. 把 README 第一条路径改成用户 quickstart，目标是一条安装命令、一个 setup 流程、一条自然语言任务。
4. 新手默认只需要 `setup / start / open / status / stop`；其余命令归入高级文档或 `admin/dev` 导航。

#### UX-P0：本地 Web Console MVP

1. 首页：机器人卡片、仿真/实机强标识、READY/阻塞、安全状态、唯一推荐动作；
2. 任务页：自然语言输入、理解预览（目标机器人、目标、步骤、风险、前置条件）、必要确认；
3. 执行页：阶段时间线、当前位置/目标、最近反馈、断线重连、暂停/取消；明确区分“取消已请求”和“机器人
   已确认停止”；
4. 恢复中心：复用现有两阶段 recover，页面显示冻结原因、现场证据、阻塞项和 TTL，按钮不能绕过确认；
5. 高级详情抽屉：reason code、原始 JSON、审计 ID、日志只在展开后出现。

Web UI 应调用现有 Mission Gateway/Robot Gateway 与 operator envelope，不创建第二套安全判断。推荐本地
优先、响应式浏览器界面；CLI 保留给部署和自动化。

#### UX-P1：零手写配置

1. Plugin 提供配置 schema、字段说明、默认值、敏感标记和验证规则，UI/CLI 共用；
2. 按机器人型号提供版本化模板，ROS graph 自动发现 topic/action/service 并预填；
3. 每一步提供“测试连接”，错误定位到具体字段并给修复按钮；
4. 保存前显示配置差异和影响，支持回滚上一份已验证配置；
5. token/证书不写入普通 TOML，交给系统凭据存储或权限受控 secret 文件。

#### UX-P1：提示、帮助与支持

1. 所有错误采用四段式：发生了什么；机器人是否已移动/是否确认停止；FireClaw 已采取什么保护；用户下一
   步做什么；
2. 错误页提供安全的直接按钮，例如“重新检测”“打开配置字段”“生成支持包”，不让用户复制 curl；
3. 新增自动脱敏 support bundle，包含版本、配置指纹、状态、任务时间线、证据 ID 和有限日志尾部；
4. 中文默认、术语悬浮解释、非仅颜色编码、大按钮和键盘可用性；移动端先做只读状态/告警，不急于开放
   危险操作。

### 建议的首个闭环与验收指标

先做“仿真首次使用闭环”，而不是直接铺满完整管理后台：

```text
安装 -> setup 选择仿真 -> 自动检查/启动 -> 打开 Web Console
     -> 输入“去前方一米” -> 查看理解预览 -> 执行 -> 看到完成/失败解释 -> 安全停止
```

关闭条件建议：

- 干净环境到第一条仿真任务不要求手写 TOML、curl、systemd 或 ROS 命令；
- 主路径不要求用户理解 Gateway、Plugin Runtime、dedupe key 或证据 JSON；
- setup 中断后可继续，不产生重复部署/任务；
- 任一失败页面都明确机器人当前安全状态和唯一下一动作；
- 仿真/实机不可混淆，真实模式永不自动执行首次运动；
- 用 5–8 名未参与开发的受试者测任务成功率、首次任务时间、求助次数、配置错误数和恢复时间。

### 当前结论

现在可以开始便利性建设，而且底层 P0/P1 已足以支撑一个可信的 Web Console。优先级不是先美化，而是先
建立 setup + active profile + 小白 quickstart，再做调用现有安全接口的 Web Console MVP。之后才扩展
零手写配置、支持包和多机器人高级界面。

工程易用性本身通常不构成论文创新。若希望形成 HRI/embodied-agent 研究贡献，需要把“风险自适应解释、
不确定状态下的人机协同确认、操作员认知负担与恢复正确率”定义为方法，并用对照用户实验和安全指标验证。

### 下一步

若用户确认实施，先写 UX-P0 的用户旅程、页面/CLI 合同和验收测试，然后按以下顺序逐项闭环：

1. `setup` + active profile + 仿真模板；
2. Web Console 首页与任务页；
3. 恢复中心与四段式错误；
4. 配置 schema/自动发现表单；
5. support bundle 和可用性测试。

### 需要运行的命令

本轮为只读评估，无需运行修改验证。若进入实施，先以现有仿真 Profile 做安全基线，并持续运行现有完整
pytest 与 P1 fault-test，确保便利性包装不削弱 fail-closed 行为。

## 2026-08-13T12:15:19+08:00 — UX 第一项 setup + active Profile 闭环完成

### 任务目标与用户决定

用户确认按路线逐项实施，并要求一项闭环后再进入下一项。本轮只完成第一项：小白首次 setup、内置仿真
模板、active Profile 和部署准备；不提前实现 Web Console，也不启动 Gazebo/机器人动作。

### 当前进展

- `fireclaw setup` 已成为正式顶层命令；
- 无参数交互默认推荐 simulation，非交互/JSON 默认 simulation；
- simulation 自动生成无凭据 TurtleBot3 Profile、做完整部署 plan、默认 materialize Runtime release；
- active Profile 已持久化，`status`、`recover`、所有 `deploy`/`deploy service` 子命令可省略
  `--profile`；
- 完整回归和 P1 fault matrix 均通过；
- 没有进入下一 UX 项。

### OpenClaw analogue 与适配

先尝试 CodeGraph 查询 OpenClaw onboarding，但当前索引仍未返回 `openclaw/` TypeScript onboarding 源码，
随后按仓库规则直接读取：

- `openclaw/src/commands/setup.ts::setupCommand`；
- `openclaw/src/commands/setup.test.ts`；
- `openclaw/src/commands/onboard-types.ts::OnboardOptions`；
- `openclaw/src/commands/onboard-config.ts::applyLocalSetupWorkspaceConfig` 与 workspace conflict；
- `openclaw/src/commands/onboard-interactive-runner.ts::runInteractiveOnboarding`。

复用的结构：setup 是公开 CLI；配置损坏时不覆盖；只写 setup 自己拥有的路径/默认；重复运行幂等；区分
interactive/non-interactive；完成后给明确下一步。FireClaw 的差异是 Profile 同时控制 ROS、Plugin Runtime
和物理安全，因此新增 simulation/real 强边界、完整部署 plan、`robot_action_started=false`、active
Profile 模式复核，以及禁止把内置 simulation Profile 改 mode 后当作 real Profile。

没有照搬 OpenClaw 的 model/channel/auth wizard，因为 FireClaw 当前首要对象是 ROS 仿真与机器人 Profile。
active Profile 是启动前的非秘密用户配置偏好，不属于任务/租约/安全冻结等权威 Runtime 状态，因此采用
权限受控的 JSON 配置 artifact；权威机器人状态仍只走原 SQLite store。

### 已实现

- 新增 `src/fireclaw_core/infra/user_setup.py`：
  - `setup_fireclaw`、`handle_setup`；
  - `write_active_profile`、`load_active_profile`、`resolve_active_profile_path`；
  - 稳定错误码与四段式人类错误提示；
  - runtime root/path escape/symlink/regular-file 校验；
  - setup-owned 目录 `0700`、Profile/state 文件 `0600`；
  - 原子 Profile create-if-absent 和 active state replace；
  - plan/apply 成功后才激活；plan 失败保留 Profile 供同一命令续接，但不写 active state；
  - real setup 要求已有人工审查 Profile，永不自动 deploy；内置 simulation template provenance 禁止用于
    real。
- 新增 `examples/setup_templates/gazebo_turtlebot3.toml`：
  - 只使用 deterministic Mission/Robot planner；
  - 不含 API key/token；
  - `deployment.mode=simulation`、`robot_gateway.dry_run=true`；
  - 路径在 setup 时生成，避免提交私人绝对路径；
  - 复用当前 ROS Noetic、TurtleBot3、Navigation Plugin 与地图资产。
- 修改 CLI：
  - `src/fireclaw_core/__main__.py` 注册 setup；
  - `src/fireclaw_core/mission/mission_cli.py` 增加 setup parser/dispatch；
  - `status`、`recover`、`deploy`、`deploy service` 的 `--profile` 改为可选，显式参数保持原命令拥有的验证
    边界；省略时严格加载 active state。
- 修改 operator projection：
  - 未 setup 时 status 返回 `active_profile_missing` 和“先运行 fireclaw setup”；
  - recover 未 setup 时 `safe_state=unknown`，不因无法读取机器人就虚假宣称 motion blocked；
  - real/robot safety 判断没有放宽。
- 新增 `tests/test_user_setup.py`（14 tests）和
  `docs/getting-started/first-run-setup.md`；README 的首次路径从 pytest/devtools 改成 setup，原测试说明移到
  “开发者验证”。

### 安全观察

只读检查现有本地 `fireclaw.toml` 时发现其中存在明文 Provider credential。本轮没有复制、修改或再次记录
该值；生成模板完全不读取该文件，只支持环境变量式凭据。已经提示用户后续轮换该凭据。默认 Gazebo
Profile 的持久安全冻结未删除、未绕过、未恢复。

### 运行命令与结果

专项与扩展回归：

```text
pytest -q tests/test_user_setup.py
  -> 14 passed

pytest -q tests/test_user_setup.py tests/test_mission_cli.py
          tests/test_operator_cli.py tests/test_plugin_runtime_deployment.py
          tests/test_runtime_supervisor.py tests/test_systemd_service.py
          tests/test_hardware_safety_acceptance.py tests/test_runtime_path_security.py
  -> 127 passed

pytest -q
  -> 2152 passed, 8 skipped in 178.78s

git diff --check
  -> pass
python -m py_compile ...
  -> pass
```

真实 setup/部署验证使用隔离目录 `/tmp/fireclaw-setup-validation.gErc3D`：

```text
fireclaw setup --mode simulation --runtime-root <isolated> --no-deploy --json
  -> status=configured
  -> fingerprint=f121d815ef319ac89724e284c5d776ac92e8a34e4d75a19e3289bf4a5e215a3b
  -> robot_action_started=false

fireclaw setup --mode simulation --runtime-root <isolated> --json
  -> status=ready_to_start, deployment.status=installed, reused=false

同一命令再次运行
  -> status=ready_to_start, deployment.status=installed, reused=true

FIRECLAW_HOME=<isolated> fireclaw deploy status --no-runtime-check
  -> status=installed, static_checks.ok=true
  -> ROS packages amcl/map_server/move_base 全部 found
```

模板 SHA-256：
`6bb1fbcff0b3fc52bb357e2ae5cdcecb604081fb80c5ef78b243fbd1af2465a3`。

P1 门禁重跑：

```text
fireclaw fault-test run --live-ros ...
  -> 7/7 passed
fireclaw fault-test verify .../fault-injection-20260813T041445.026149Z-889cf491
  -> status=valid, suite_status=passed, errors=[]
```

### 失败尝试与修正

1. setup 专项首次为 `8 passed, 1 failed`：失败原因是测试依赖默认绑定的 `sys.stdout`，`capsys` 没拿到
   输出；改为显式 `StringIO` 后通过，不是业务行为失败。
2. 扩展回归首次为 `33 passed, 1 failed`：active resolver 对显式 `--profile` 提前做文件存在校验，破坏了
   旧 mock 的 command-owned validation seam。修正为：只有持久 active preference 在 resolver 层严格
   验证；显式参数仍由各命令原 loader 校验。随后 34/34 和全量均通过。
3. 前一轮只读 UX 复核曾误用不存在的 shell `codegraph context`；本轮正确使用 `codegraph explore`，并在
   OpenClaw 子树未被索引时读取 scoped guide/source。没有伪称 CodeGraph 返回了 upstream source。

### 已完成

第一项“setup + active Profile + 仿真模板/部署准备”已闭环。用户从完整仓库首次运行时不需要复制
`fireclaw.example.toml`、填 Provider key、理解两个 Gateway 或重复输入 Profile；setup 不启动任何机器人
动作，失败可安全续接。

### 当前问题与边界

- 当前产品仍从完整源码仓库安装；wheel/独立安装包尚未携带完整 ROS workspace、Plugin 与地图资产，setup
  会以 `simulation_assets_missing` 明确失败，不会下载未知内容或使用残缺模板。
- setup 完成后当前仍需运行 `fireclaw deploy run`；面向小白的 `fireclaw start/stop` 是下一项，未在本轮
  偷跑。
- Web Console、自然语言任务预览和零手写 real Profile 尚未开始。
- 工程易用性实现不是论文创新；以后如形成研究工作仍需用户实验与认知负担/安全决策指标。

### 下一步

按用户要求停在第一项。下一项建议实现“仿真一键启动/停止”：`fireclaw start` 使用 active Profile 启动
受管 Runtime，`fireclaw stop` 做逆序停止并给出清晰安全终态；必须复用现有 supervisor/service，不创建
第二套生命周期逻辑。该项闭环后再进入 Web Console。

### 需要运行的命令

当前用户路径：

```bash
fireclaw setup
fireclaw deploy run
```

下一项实施时继续保留全量 pytest 和 live fault-test 作为发布门禁。
