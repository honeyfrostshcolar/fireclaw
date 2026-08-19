# UX-P1 零手写配置 (Zero-Handwritten Configuration) 实施计划

> 状态说明：本文是当时的设计/实施记录，不是当前产品能力声明。当前闭环边界与剩余缺口以 `memory/2026-08-19/seven-stage-ux-closure-audit.md` 及后续记录为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现面向操作员的“零手写配置”系统，包含机器人型号模板库、ROS 图谱智能自发现与预填、统一 Plugin Config Schema、单项测试连接、保存前 Diff 与物理影响评估、多版本快照一键回滚以及敏感凭据隔离存储。

**Architecture:**
在 `src/fireclaw_core/config/` 构建配置核心引擎（`templates.py`, `discovery.py`, `schema.py`, `diff_engine.py`, `snapshots.py`, `secrets.py`），在 CLI（`fireclaw profile` / `setup`）与 Gateway REST API（`/config/*`）暴露服务，并在 Web 控制台 Settings 模块提供动态表单、自动发现、Diff 预览与回滚交互。

**Tech Stack:** Python 3.8+, ROS1 图谱探测, TOML (via `tomllib_compat`), HTML5/CSS3/ES Modules, `pytest`

## Global Constraints

- 严守物理安全与凭据隔离：普通 Profile 绝不记录明文 API Token 或私钥，必须通过环境变量或 `0600` 私有凭据库隔离。
- 零外部 Node 打包依赖：Web 控制台表单为原生 JavaScript 动态渲染，离线自包含。
- 遵循 OpenClaw-first 架构模式：保持向导式渐进发现、Schema 校验、快照版本归档与四段式友好错误提示。

---

### Task 1: 配置核心引擎（型号模板库、ROS 自发现探针与统一 Schema）

**Files:**
- Create: `src/fireclaw_core/config/__init__.py`
- Create: `src/fireclaw_core/config/templates.py`
- Create: `src/fireclaw_core/config/discovery.py`
- Create: `src/fireclaw_core/config/schema.py`
- Test: `tests/test_config_core.py`

**Interfaces:**
- Produces:
  - `TemplateManager`: 提供 5 种型号模板（`gazebo_turtlebot3_burger`, `gazebo_turtlebot3_waffle`, `real_firefighting_tracked`, `real_quadruped_rescue`, `real_generic_diff_drive`）
  - `RosGraphDiscoverer`: 探测 ROS Master 活跃 Topics / Actions 并提供智能映射字典
  - `PluginConfigSchema`: 提供标准字段定义（`ConfigField`）、中文解释、示例与校验逻辑

- [ ] **Step 1: 编写单元测试 `tests/test_config_core.py`**
  测试模板完整性、ROS 图谱自发现模式匹配（含模拟 ROS Graph）、Schema 字段校验与默认值解析。

- [ ] **Step 2: 运行测试验证失败**
  Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_config_core.py`
  Expected: FAIL

- [ ] **Step 3: 实现 `templates.py`, `discovery.py`, `schema.py`**
  实现模板管理、基于特征消息类型的 ROS 图谱探测器以及统一配置 Schema。

- [ ] **Step 4: 运行测试验证通过**
  Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_config_core.py`
  Expected: PASS

- [ ] **Step 5: 暂存或提交改动**

---

### Task 2: 差异影响评估、多版本快照与凭据隔离引擎

**Files:**
- Create: `src/fireclaw_core/config/diff_engine.py`
- Create: `src/fireclaw_core/config/snapshots.py`
- Create: `src/fireclaw_core/config/secrets.py`
- Test: `tests/test_config_diff_and_snapshots.py`

**Interfaces:**
- Produces:
  - `ConfigDiffEngine`: 计算配置两栏 Diff，并匹配物理影响评估规则
  - `ProfileSnapshotManager`: 自动归档滚动快照（保留 20 份），提供回滚到指定快照方法
  - `SecretManager`: `0600` 私有凭据存储与环境变量解析，确保 Profile 无明文密钥

- [ ] **Step 1: 编写测试 `tests/test_config_diff_and_snapshots.py`**
  测试 Diff 计算与影响规则匹配、快照生成与回滚恢复、凭据隔离存储与权限校验。

- [ ] **Step 2: 运行测试验证失败**
  Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_config_diff_and_snapshots.py`
  Expected: FAIL

- [ ] **Step 3: 实现 `diff_engine.py`, `snapshots.py`, `secrets.py`**
  实现差异影响评估、版本快照管理器与凭据隔离库。

- [ ] **Step 4: 运行测试验证通过**
  Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_config_diff_and_snapshots.py`
  Expected: PASS

- [ ] **Step 5: 暂存或提交改动**

---

### Task 3: CLI 命令集成 (`fireclaw profile`) 与 Gateway REST 接口挂载

**Files:**
- Modify: `src/fireclaw_core/mission/mission_cli.py`
- Modify: `src/fireclaw_core/mission/mission_gateway.py`
- Modify: `src/fireclaw_core/__main__.py`
- Test: `tests/test_config_cli_and_api.py`

**Interfaces:**
- Consumes:
  - Task 1 & Task 2 引擎
- Produces:
  - CLI 子命令：`fireclaw profile list-templates`, `fireclaw profile discover`, `fireclaw profile diff`, `fireclaw profile history`, `fireclaw profile rollback`
  - Gateway REST 端点：`/config/templates`, `/config/discover`, `/config/schema`, `/config/diff`, `/config/save`, `/config/history`, `/config/rollback`, `/config/test-field`

- [ ] **Step 1: 编写 CLI 与 REST API 测试 `tests/test_config_cli_and_api.py`**
  测试 CLI 各子命令解析与执行，测试 Gateway 8 个配置端点响应与错误处理。

- [ ] **Step 2: 运行测试验证失败**
  Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_config_cli_and_api.py`
  Expected: FAIL

- [ ] **Step 3: 实现 CLI 路由与 Gateway 接口挂载**
  在 `mission_cli.py` 注册子命令处理器，在 `mission_gateway.py` 挂载 `/config/*` 路由。

- [ ] **Step 4: 运行测试验证通过**
  Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_config_cli_and_api.py`
  Expected: PASS

- [ ] **Step 5: 暂存或提交改动**

---

### Task 4: Web 控制台 Settings 页面动态表单、连通性探测、全量回归与文档

**Files:**
- Modify: `src/fireclaw_core/web_console/index.html`
- Modify: `src/fireclaw_core/web_console/app.js`
- Modify: `src/fireclaw_core/web_console/style.css`
- Modify: `README.md`
- Modify: `docs/getting-started/first-run-setup.md`
- Test: `tests/test_config_web_integration.py` + 全量回归

- [ ] **Step 1: 编写 Web 控制台配置交互测试 `tests/test_config_web_integration.py`**
  测试模板切换、自动发现渲染、单项测试连接、Diff 模态框与快照回滚前端契约。

- [ ] **Step 2: 运行测试验证失败**
  Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_config_web_integration.py`
  Expected: FAIL

- [ ] **Step 3: 完善 Web 控制台 Settings 页面与交互**
  构建模板选择下拉框、自动发现与预填、字段级“测试连接”指示器、Diff 与影响分析弹窗、历史快照回滚选择器。

- [ ] **Step 4: 更新文档与指南**
  更新 `README.md` 与 `first-run-setup.md` 中的零手写配置说明。

- [ ] **Step 5: 运行全量测试回归与 P1 故障矩阵**
  Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q`
  Expected: 全量用例通过（>2230 passed）
