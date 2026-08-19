# UX 路线第一步：可发布交付基线实施计划

> 状态说明：本文是当时的设计/实施记录，不是当前产品能力声明。当前闭环边界与剩余缺口以 `memory/2026-08-19/seven-stage-ux-closure-audit.md` 及后续记录为准。

> **Superpowers execution contract:** 按任务顺序逐项执行；每个行为变更先写失败测试，再做最小实现，再运行聚焦测试。当前会话未暴露 `superpowers:executing-plans` 或 `superpowers:subagent-driven-development` 可调用 Skill，因此本文件保留相同的 spec → plan → TDD → checkpoint 流程，实施时不得据此自动启用子代理、提交或推送。

**Goal:** 把现有 FireClaw UX 能力固化为可安装、可审计、可重复验证的发布基线：core wheel 正确携带 Web/setup 资源，同时产出确定性的 TurtleBot3 仿真 sidecar bundle，并用安装后 release check 阻止缺文件、误打包和源码树依赖。

**Architecture:** core wheel 只持有 Python、Web 静态资源、package-owned setup 模板和 simulation bundle catalog；ROS/Plugin/机器人源码由显式 allowlist 生成版本化 sidecar bundle。`importlib.resources` 负责 core 资源读取，独立 release checker 直接检查构建产物并在空工作目录做安装后 smoke。

**Tech Stack:** Python 3.10+、setuptools/PEP 517、`importlib.resources`、`tarfile`、`zipfile`、`hashlib`、`venv`、pytest。

设计依据：`docs/superpowers/specs/2026-08-16-ux-delivery-baseline-design.md`

## Global Constraints

- 保护当前 dirty worktree；禁止覆盖无关用户改动，禁止 `git add .`。
- 未获明确授权时不 stage、不 commit、不 push。
- 所有构建与安装 smoke 输出写入 `mktemp -d` 创建的临时目录，不写回仓库的 `dist/`、`build/` 或 `*.egg-info`。
- 不访问网络；不下载依赖或仿真资源。
- 不启动 ROS、Gazebo、Gateway 或机器人，不调用浏览器。
- 不把 `build/`、`devel/`、logs、runtime data、credentials、真实地图或 `openclaw/` 放进发布产物。
- 保持现有源码仓库运行方式兼容；安装后完整 simulation setup 明确留给下一阶段。
- 若任何安全测试或 artifact contract 失败，fail closed，不通过放宽禁入规则绕过。

## Execution Order

```text
Task 0 基线分类
  -> Task 1 package-owned setup 资源
  -> Task 2 Web package resource
  -> Task 3 simulation bundle
  -> Task 4 release check + 安装后 smoke
  -> Task 5 文档与工作树收口
  -> Task 6 全量验收
```

---

### Task 0: 冻结并分类当前基线

**Files:**

- Read: `memory/2026-08-16/ux-roadmap-completion-audit.md`
- Read: `pyproject.toml`
- Read: current `git status` / `git diff`
- Conditional modify: `.gitignore`
- Update: `memory/2026-08-16/ux-delivery-baseline-implementation.md`

- [ ] **Step 1: 记录开始时间、分支、HEAD、工作树和测试基线**

  Run:

  ```bash
  date -Is
  git branch --show-current
  git rev-parse HEAD
  git status --short
  git diff --check
  ```

- [ ] **Step 2: 建立显式文件分类表**

  至少分类为 `product-source`、`tests`、`user-docs`、`planning-memory`、`runtime-data`、`generated-artifact`。单独审查当前 untracked 的 `data/robots/`；它默认归入 runtime data，不进入发布或提交候选集。

- [ ] **Step 3: 只在现有 ignore 规则确有缺口时修改 `.gitignore`**

  覆盖本阶段会产生的 wheel、bundle、venv、`build/`、`devel/`、`*.egg-info` 和 release-check 临时输出。不得用 ignore 隐藏应当审查的产品源码或测试。

- [ ] **Step 4: 建立实施 memory 记录**

  记录文件分类、当前两处 `git diff --check` 问题、初始测试数和后续每个 checkpoint。此任务不 stage 文件。

**Checkpoint:** 有可复核的起点快照；尚未修改业务行为。

---

### Task 1: 建立 package-owned setup 资源

**Files:**

- Create: `src/fireclaw_core/resources/__init__.py`
- Create: `src/fireclaw_core/resources/setup_templates/gazebo_turtlebot3.toml`
- Create: `src/fireclaw_core/resources/simulation_bundles/turtlebot3-burger-v1.json`
- Modify: `pyproject.toml`
- Modify: `src/fireclaw_core/infra/user_setup.py`
- Keep as compatibility mirror: `examples/setup_templates/gazebo_turtlebot3.toml`
- Create: `tests/test_distribution_resources.py`

**Interfaces:**

- `load_setup_template(template_id: str) -> str`
- `load_simulation_bundle_catalog(bundle_id: str) -> Mapping[str, Any]`
- `setup_fireclaw()` 读取 package-owned 模板，但仍从显式/source checkout root 解析 ROS/Plugin/robot assets。

- [ ] **Step 1: 先写失败测试**

  覆盖：模板可通过 `importlib.resources` 读取、catalog schema 合法、未知 ID fail closed、package 模板与兼容 example 字节一致、源码根定位不再以 example 模板作为 marker。

- [ ] **Step 2: 验证测试按预期失败**

  Run:

  ```bash
  pytest -q tests/test_distribution_resources.py tests/test_user_setup.py
  ```

  Expected: FAIL，原因是 package resource/catalog 尚不存在。

- [ ] **Step 3: 实现最小资源 API 与 package-data 配置**

  使用 `importlib.resources.files()` 读取文本/JSON；catalog 做严格字段、ID、相对路径和体积预算校验。`pyproject.toml` 显式包含这些非 Python 文件，不依赖 setuptools 的隐式发现。

- [ ] **Step 4: 让 `user_setup.py` 使用 package-owned 模板**

  保持现有 `source_root`、Profile schema、幂等性和实机禁自动部署行为不变。此任务不实现 bundle 下载或 ROS 构建。

- [ ] **Step 5: 运行聚焦测试**

  Run:

  ```bash
  pytest -q tests/test_distribution_resources.py tests/test_user_setup.py tests/test_turtlebot3_burger_deployment_assets.py
  ```

  Expected: PASS。

**Checkpoint:** setup 模板有明确 package 所有权，source checkout 行为无回归。

---

### Task 2: 让 Web Console 成为真实的 wheel resource

**Files:**

- Modify: `src/fireclaw_core/web_console/__init__.py`
- Modify: `src/fireclaw_core/mission/mission_gateway.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_web_console_routes.py`
- Create or modify: `tests/test_distribution_resources.py`

**Interfaces:**

- `read_web_console_asset(name: str) -> tuple[bytes, str]`
- 只接受 `index.html`、`style.css`、`app.js`；未知名、目录和路径穿越统一返回 not found。

- [ ] **Step 1: 先补资源读取与路由失败测试**

  测试不直接读取源码绝对路径；通过公共资源接口和 Gateway 路由验证三项静态资源。加入 `../`、URL encoded traversal、目录、未知扩展和缺失资源用例。

- [ ] **Step 2: 运行测试并确认失败原因**

  Run:

  ```bash
  pytest -q tests/test_web_console_routes.py tests/test_distribution_resources.py
  ```

- [ ] **Step 3: 实现 allowlisted package resource loader**

  `mission_gateway.py` 不再用 `Path(__file__).parent` 推导静态目录；Content-Type 由固定映射返回。禁止把任意 URL path 直接传给 resource loader。

- [ ] **Step 4: 显式声明 Web package data**

  在 `pyproject.toml` 中明确列入 `*.html`、`*.css`、`*.js`。

- [ ] **Step 5: 运行 Web 聚焦回归**

  Run:

  ```bash
  pytest -q tests/test_web_console_routes.py tests/test_web_console_ui.py tests/test_web_console_app.py tests/test_config_web_integration.py tests/test_friendly_web_integration.py
  ```

  Expected: PASS。

**Checkpoint:** Web Console 不再依赖源码目录，路径安全保持 fail closed。

---

### Task 3: 构建确定性的 TurtleBot3 simulation bundle

**Files:**

- Create: `tools/__init__.py`
- Create: `tools/release/__init__.py`
- Create: `tools/release/simulation_bundle.py`
- Use: `src/fireclaw_core/resources/simulation_bundles/turtlebot3-burger-v1.json`
- Create: `tests/test_simulation_bundle_release.py`

**Interfaces:**

- `build_simulation_bundle(repo_root: Path, output_dir: Path, catalog: Mapping[str, Any]) -> BundleResult`
- `inspect_simulation_bundle(path: Path, catalog: Mapping[str, Any]) -> BundleInspection`
- CLI:

  ```bash
  python -m tools.release.simulation_bundle --repo-root . --output-dir <tmp-dir>
  ```

- [ ] **Step 1: 写 manifest 和 archive 安全测试**

  覆盖 allowlist、required paths、forbidden segments、绝对路径、`..`、symlink/hardlink/device、单文件/总大小、digest mismatch、许可证缺失和重复归档确定性。

- [ ] **Step 2: 运行测试验证失败**

  Run:

  ```bash
  pytest -q tests/test_simulation_bundle_release.py
  ```

- [ ] **Step 3: 实现纯函数式 catalog 校验和 bundle builder**

  归档条目排序，固定 mtime/uid/gid/uname/gname，规范化 mode；manifest 为每个文件记录 SHA-256 和 size。输入发生越界或出现未声明文件时直接失败。

- [ ] **Step 4: 设置首版显式资源清单**

  Include 至少覆盖：

  - `examples/ros1_configs/gazebo_turtlebot3_move_base.yaml`；
  - Navigation Plugin manifest、entrypoint、Skill、Runtime descriptor、launch、default config；
  - `extensions/navigation-move-base/ros_ws/src/` 与 provenance；
  - `robots/turtlebot3_burger/ros_ws/src/`、launch/map/model 与 provenance。

  Exclude 至少覆盖：`build`、`devel`、`logs`、acceptance artifacts、cache、`.git`、credentials、runtime data。

- [ ] **Step 5: 构建两次并验证 byte-for-byte 一致**

  在两个不同临时输出目录运行 builder，比较 SHA-256。预期候选 bundle 约 8.5 MiB，硬上限 16 MiB compressed / 64 MiB unpacked。

- [ ] **Step 6: 运行聚焦测试**

  Run:

  ```bash
  pytest -q tests/test_simulation_bundle_release.py tests/test_turtlebot3_burger_deployment_assets.py tests/test_move_base_navigation_plugin.py tests/test_plugin_runtime_deployment.py
  ```

  Expected: PASS。

**Checkpoint:** 仿真资源有确定性、可审计的发布物，但尚未被安装后 setup 自动消费。

---

### Task 4: 建立 OpenClaw-style release check 与安装后 smoke

**Files:**

- Create: `tools/release/distribution_check.py`
- Create: `tests/test_distribution_release_check.py`
- Create: `docs/release/distribution-contract.md`
- Modify: `pyproject.toml`（仅在需要 release/dev command metadata 时）

**Interfaces:**

- `inspect_wheel(path: Path) -> ArtifactInspection`
- `collect_required_path_errors(...) -> list[str]`
- `collect_forbidden_path_errors(...) -> list[str]`
- `collect_size_errors(...) -> list[str]`
- `run_installed_smoke(wheel: Path, work_root: Path) -> SmokeResult`
- CLI:

  ```bash
  python -m tools.release.distribution_check --wheel <wheel> --simulation-bundle <bundle>
  ```

- [ ] **Step 1: 写 release contract 的失败测试**

  用小型合成 wheel/tar fixture 覆盖：required path 缺失、forbidden path、危险 archive member、metadata 版本不匹配、超预算、bundle checksum 不一致、多个候选 artifact 和不安全文件名。

- [ ] **Step 2: 运行并确认失败**

  Run:

  ```bash
  pytest -q tests/test_distribution_release_check.py
  ```

- [ ] **Step 3: 实现 artifact inspector**

  检查实际 archive，不根据源码树推断成功。错误输出分组为 `missing`、`forbidden`、`integrity`、`size`、`installed_smoke`，一次展示全部可修复问题。

- [ ] **Step 4: 实现隔离安装 smoke**

  - 使用临时 venv；
  - wheel 以 `--no-deps` 安装，依赖来自测试环境；
  - 工作目录为空且不在仓库内；
  - 移除 `PYTHONPATH`，设置独立 `FIRECLAW_HOME`；
  - 运行六个只读 CLI help 命令；
  - 用已安装解释器读取 Web/setup package resources；
  - 不运行任何 start/open 副作用。

- [ ] **Step 5: 构建真实 artifact 并执行 release check**

  使用 `mktemp -d` 输出目录：

  ```bash
  python -m pip wheel . --no-deps --no-build-isolation --wheel-dir <tmp-dir>
  python -m tools.release.simulation_bundle --repo-root . --output-dir <tmp-dir>
  python -m tools.release.distribution_check --wheel <tmp-wheel> --simulation-bundle <tmp-bundle>
  ```

  Expected: PASS，并输出 artifact SHA-256、文件数、压缩/解压体积和 smoke 命令结果。

- [ ] **Step 6: 运行 release-check 单元测试**

  Run:

  ```bash
  pytest -q tests/test_distribution_resources.py tests/test_simulation_bundle_release.py tests/test_distribution_release_check.py
  ```

  Expected: PASS。

**Checkpoint:** 发布正确性由实际 artifact 和安装后行为证明，不再由源码测试代替。

---

### Task 5: 收敛用户文档、文件清单与工作树卫生

**Files:**

- Modify: `README.md`
- Modify: `docs/getting-started/first-run-setup.md`
- Create: `docs/release/distribution-contract.md`（若 Task 4 尚未创建）
- Update: `memory/2026-08-16/ux-delivery-baseline-implementation.md`
- Conditional modify: `.gitignore`

- [ ] **Step 1: 删除或降级未经 artifact 验证的完成声明**

  明确区分：

  - 源码仓库中已验证的行为；
  - wheel 安装后已验证的行为；
  - 下一阶段才会完成的 `setup -> start -> open` 自动闭环。

- [ ] **Step 2: 文档化发布物边界**

  说明 core wheel 与 simulation bundle 的内容、许可证、版本耦合、体积预算和验证命令。不要指导普通用户手填空白 TOML。

- [ ] **Step 3: 修复所有 `git diff --check` 问题**

  只修当前任务触及或已确认的 whitespace，不格式化无关用户改动。

- [ ] **Step 4: 复核 untracked 文件分类**

  确认所有本阶段产品源码、测试和用户文档已列入 proposed change set；runtime data 和构建物未列入。输出清单供用户决定是否 stage/commit，本任务本身不执行 stage/commit。

- [ ] **Step 5: 运行文档相关聚焦测试与 diff 检查**

  Run:

  ```bash
  pytest -q tests/test_cli_help_grouping.py tests/test_user_setup.py tests/test_web_console_routes.py
  git diff --check
  ```

  Expected: PASS。

**Checkpoint:** 用户文档与真实发布能力一致，工作树没有打包污染。

---

### Task 6: 全量验收与阶段交接

**Files:**

- Update: `memory/2026-08-16/ux-delivery-baseline-implementation.md`
- No product changes unless a failed gate exposes a defect

- [ ] **Step 1: 运行所有新增聚焦测试**

  ```bash
  pytest -q \
    tests/test_distribution_resources.py \
    tests/test_simulation_bundle_release.py \
    tests/test_distribution_release_check.py \
    tests/test_user_setup.py \
    tests/test_web_console_routes.py
  ```

- [ ] **Step 2: 运行现有 UX 回归集**

  ```bash
  pytest -q \
    tests/test_user_lifecycle_cli.py \
    tests/test_cli_help_grouping.py \
    tests/test_daemon_manager.py \
    tests/test_web_console_ui.py \
    tests/test_web_console_app.py \
    tests/test_config_core.py \
    tests/test_config_cli_and_api.py \
    tests/test_friendly_errors.py \
    tests/test_friendly_cli_and_api.py
  ```

- [ ] **Step 3: 运行全量测试**

  Run:

  ```bash
  pytest -q
  ```

  Expected: 全部通过；既有基线为 `2313 passed, 8 skipped`，新增测试后 passed 数不应低于该基线，任何 skip 增加都需说明。

- [ ] **Step 4: 重新构建并检查最终 artifact**

  在全新的临时目录构建 core wheel 和 bundle，执行完整 distribution check 与 installed smoke。记录最终 SHA-256、文件数和体积。

- [ ] **Step 5: 最终静态检查**

  ```bash
  git diff --check
  git status --short
  ```

  确认仓库内没有新增 `dist/`、`build/`、`devel/`、venv、`*.egg-info` 或 bundle 二进制。

- [ ] **Step 6: 写交接记录**

  memory 至少记录：命令、结果、artifact 路径与 digest、修改文件、未解决风险、失败尝试、第二阶段入口。明确写出“未启动 ROS/Gazebo/机器人”。

- [ ] **Step 7: 用户授权门**

  向用户汇报 proposed change set。只有用户明确要求后，才执行 stage、commit、push 或进入第二阶段。

## Definition of Done

- [ ] core wheel 中存在并可读取 Web Console、setup template、simulation catalog；
- [ ] simulation bundle 可确定性构建且不含生成/敏感文件；
- [ ] release check 的 required、forbidden、integrity、size、installed smoke 全部通过；
- [ ] 全量 pytest 与 `git diff --check` 通过；
- [ ] 文档没有把第二阶段能力写成已完成；
- [ ] 没有启动机器人、ROS 或 Gazebo；
- [ ] 没有未经用户授权的 stage/commit/push。
