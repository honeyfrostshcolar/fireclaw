# UX-P0 本地 Web Console MVP 实施计划

> 状态说明：本文是当时的设计/实施记录，不是当前产品能力声明。当前闭环边界与剩余缺口以 `memory/2026-08-19/seven-stage-ux-closure-audit.md` 及后续记录为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现面向操作员的本地 Web 控制台（Web Console MVP），包含 5 大核心页面（首页、任务下发与意图确认、执行与取消三态可视化、恢复中心、设置中心），集成至 Gateway HTTP 服务并支持 SSE 事件流实时推流。

**Architecture:**
在 `src/fireclaw_core/web_console/` 组织纯原生单页应用（`index.html`、`style.css`、`app.js`），在 `MissionGateway` / Gateway 请求处理器中挂载 `/`、`/console` 及安全受控的 `/static/*` 静态资源路由；前端通过原生 ES Modules 进行状态管理、SSE 游标消费、自然语言意图预览与取消三态控制。

**Tech Stack:** Python 3.8+, `http.server`, HTML5, Modern Vanilla CSS (Rescue Ops Dark), ES Modules, EventSource (SSE), `pytest`

## Global Constraints

- 严守物理安全与三态取消：界面必须严格区分 `cancel_requested`、`stopping` 与 `stopped_confirmed`，严禁提前假宣称已停机。
- 零外部 Node 打包依赖：前端资源为纯原生 HTML/CSS/JS，离线自包含，开箱即用。
- 路径与网络安全：静态资源服务严格防范路径穿越（Path Traversal），禁止符号链接逃逸。
- 遵循 OpenClaw-first 架构模式：保持本地文件持久化状态、状态自愈与清晰的四段式错误提示。

---

### Task 1: Gateway 静态资源托管与安全路由接入

**Files:**
- Create: `src/fireclaw_core/web_console/index.html`
- Create: `src/fireclaw_core/web_console/style.css`
- Create: `src/fireclaw_core/web_console/app.js`
- Modify: `src/fireclaw_core/mission/mission_gateway.py`
- Test: `tests/test_web_console_routes.py`

**Interfaces:**
- Consumes:
  - `MissionGateway` HTTP request handling in `mission_gateway.py`
- Produces:
  - `GET /` & `GET /console` -> returns `index.html` (MIME: `text/html; charset=utf-8`)
  - `GET /static/<file>` -> returns CSS / JS / Assets with safe path confinement and correct MIME types
  - `GET /readiness` -> returns system readiness summary JSON

- [ ] **Step 1: 编写路由与安全测试 `tests/test_web_console_routes.py`**
  测试包括：`GET /` 和 `GET /console` 返回 200 与 HTML 内容；`GET /static/style.css` 与 `GET /static/app.js` 返回正确 MIME 类型；路径穿越攻击（`GET /static/../../etc/passwd`）严格返回 404/403。

- [ ] **Step 2: 运行测试验证失败**
  Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_web_console_routes.py`
  Expected: FAIL

- [ ] **Step 3: 实现静态资源文件与 Gateway 路由挂载**
  在 `src/fireclaw_core/web_console/` 创建骨架文件，并在 `MissionGateway` 中挂载静态路由与防逃逸校验。

- [ ] **Step 4: 运行测试验证通过**
  Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_web_console_routes.py`
  Expected: PASS

- [ ] **Step 5: 暂存或提交改动**

---

### Task 2: 前端 5 大页面结构、应急救援深色主题与响应式布局

**Files:**
- Modify: `src/fireclaw_core/web_console/index.html`
- Modify: `src/fireclaw_core/web_console/style.css`
- Test: `tests/test_web_console_ui.py`

**Interfaces:**
- Consumes:
  - 静态资源路由 (from Task 1)
- Produces:
  - 5 个 Tab 容器（`#tab-overview`, `#tab-dispatch`, `#tab-execution`, `#tab-recovery`, `#tab-settings`）
  - 应急救援深色主题（高对比度配色、状态指示灯、任务卡片、时间线、恢复中心两阶段表单）

- [ ] **Step 1: 编写前端 UI 结构测试 `tests/test_web_console_ui.py`**
  验证 HTML 包含 5 个核心页面的语义化容器、模式徽章（`#mode-badge`）、意图确认卡片（`#intent-preview-card`）、三态取消指示器（`#cancel-status-indicator`）、两阶段恢复确认框（`#recovery-confirm-check`）。

- [ ] **Step 2: 运行测试验证失败**
  Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_web_console_ui.py`
  Expected: FAIL

- [ ] **Step 3: 编写 `index.html` 页面骨架与 `style.css` 样式**
  构建完整的 5 大页面布局、Rescue Ops Dark 主题、响应式断点、卡片悬浮与时间线动效。

- [ ] **Step 4: 运行测试验证通过**
  Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_web_console_ui.py`
  Expected: PASS

- [ ] **Step 5: 暂存或提交改动**

---

### Task 3: 前端交互逻辑、意图确认、取消三态状态机与 SSE 事件流

**Files:**
- Modify: `src/fireclaw_core/web_console/app.js`
- Modify: `src/fireclaw_core/mission/mission_gateway.py` (完善 `/plan-mission` 与 `/readiness` 联动)
- Test: `tests/test_web_console_app.py`

**Interfaces:**
- Consumes:
  - `/readiness`, `/plan-mission`, `/tasks`, `/tasks/<id>/cancel`, `/recover`, `/events`
- Produces:
  - 单页 Tab 切换与状态同步
  - 自然语言任务解析 -> 渲染理解预览卡片 -> 确认提交
  - SSE 游标消费与断线重连
  - 取消三态严格状态机（`cancel_requested` -> `stopping` -> `stopped_confirmed`）
  - 两阶段安全恢复交互

- [ ] **Step 1: 编写交互与状态机测试 `tests/test_web_console_app.py`**
  测试包括：意图预览结构字段验证、取消三态状态机转移逻辑、两阶段恢复 API 联动。

- [ ] **Step 2: 运行测试验证失败**
  Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_web_console_app.py`
  Expected: FAIL

- [ ] **Step 3: 实现 `src/fireclaw_core/web_console/app.js` 交互逻辑**
  编写状态管理、API 请求封装、SSE 事件监听、三态取消渲染与恢复中心逻辑。

- [ ] **Step 4: 运行测试验证通过**
  Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_web_console_app.py`
  Expected: PASS

- [ ] **Step 5: 暂存或提交改动**

---

### Task 4: 端到端验证、全量回归与文档更新

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started/first-run-setup.md`
- Test: 全量 pytest 回归 + E2E 验证

- [ ] **Step 1: 更新文档**
  补充 Web Console 控制台的使用说明与界面功能截图/示意图。

- [ ] **Step 2: 运行全量测试回归**
  Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q`
  Expected: 全量用例通过（>2200 passed）

- [ ] **Step 3: E2E 隔离验证**
  启动后台守护进程，通过 HTTP 请求验证 Web Console 页面完整加载与接口响应。
