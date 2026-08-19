# UX-P0 本地 Web Console MVP 设计方案

> 状态说明：本文是当时的设计/实施记录，不是当前产品能力声明。当前闭环边界与剩余缺口以 `memory/2026-08-19/seven-stage-ux-closure-audit.md` 及后续记录为准。

日期：2026-08-14
状态：Approved
主题：FireClaw 本地操作员 Web 控制台（Web Console MVP）设计规范

## 1. 背景与目标

在完成 `fireclaw setup`、Active Profile、`fireclaw start / stop / open` 及命令收敛后，FireClaw 已具备完整的本地受管运行时与操作员接口。
为彻底实现面向普通操作员（甚至无 ROS / Linux 命令行背景的救援指挥人员）的友好体验，本设计规范定义了 **本地 Web Console MVP** 的五大核心页面与前后端交互机制。

---

## 2. 页面规划与交互规范

Web Console 采用单页应用（SPA）架构，使用原生 HTML5 / Modern Vanilla CSS / ES Modules 构建，零外部 node 打包依赖，包含 5 个核心页面：

### 2.1 首页 (Overview)
- **机器人可用性与状态**：展示机器人 ID、运行状态（`ONLINE` / `OFFLINE` / `READY` / `BLOCKED`）。
- **仿真 / 实机强视觉区隔**：
  - 仿真模式展示青色徽章：`[SIMULATION 仿真模式]`；
  - 实机模式展示高亮金色徽章：`[REAL ROBOT 实机模式 - 物理急停已就绪]`。
- **全局安全状态**：正常 / 告警 / 安全冻结。
- **唯一推荐操作**：
  - 若系统就绪：按钮显示「下发新任务 ▶」；
  - 若处于安全冻结：按钮显示「前往恢复中心 ⚠️」；
  - 若未连接/离线：按钮显示「查看诊断与配置 ⚙️」。

### 2.2 任务下发页 (Dispatch)
- **自然语言输入区**：输入框与常用快捷指令卡片（如“前往二楼搜救人员”、“返回初始集结点”）。
- **任务理解与执行前确认卡片（强制拦截门）**：
  提交前必须渲染结构化意图卡片：
  ```text
  ┌──────────────────────────────────────────────────────────┐
  │ 🎯 我理解的任务：前往二楼搜索人员                         │
  │ 🤖 执行机器人：gazebo_turtlebot3                         │
  │ 📋 预计步骤：1. 导航至目标区域 → 2. 传感器扫描 → 3. 结果上报│
  │ ⚠️ 当前风险等级：中等（涉及未知区域与局部视线遮挡）          │
  │ [ 取消并修改 ]                      [ 确认并立即开始执行 ▶ ] │
  └──────────────────────────────────────────────────────────┘
  ```

### 2.3 实时执行页 (Execution)
- **当前步骤与反馈**：当前执行 Tool 名称、目标点坐标、耗时、实时反馈消息。
- **动态事件时间线**：通过 SSE 实时追加步骤节点。
- **取消与停止控制（严格三态可视化）**：
  - 🟡 `cancel_requested`（取消请求已发送，等待底盘响应）
  - 🟠 `stopping`（底盘制动减速中，动作尚未完全终止）
  - 🟢 `stopped_confirmed`（底盘传感器确认速度为 0，机器人已安全确认停止）

### 2.4 恢复中心 (Recovery Center)
- **安全冻结原因**：展示触发安全阻断的详细原因与相关传感器快照。
- **现场证据抽屉**：展示证据哈希、时间戳、上下文详情。
- **两阶段恢复交互**：必须勾选“现场物理安全已核验”复选框后，才点亮「解除冻结并恢复」按钮。

### 2.5 设置中心 (Settings)
- **Active Profile 视图**：展示当前 Profile 路径、模式、ROS Master URI、Gateway 端口。
- **连通性测试**：提供一键「测试 ROS Master 连接」和「测试 Gateway 连通性」检测按钮。

---

## 3. 技术架构与后端服务

### 3.1 前端资源组织
```text
src/fireclaw_core/web_console/
├── index.html        # 单页应用骨架
├── style.css         # 应急救援深色主题、高对比度样式、响应式布局
└── app.js            # 原生 ES Module：路由、SSE 消费、API 客户端、状态机
```

### 3.2 Gateway HTTP 路由挂载
在 `MissionGateway` / `FireClawGateway` 中增加路由：
- `GET /` 或 `GET /console` -> 返回 `index.html`（MIME: `text/html; charset=utf-8`）；
- `GET /static/<filename>` -> 托管静态资源（MIME 严格匹配，防路径穿越）；
- `GET /readiness` -> 返回系统就绪度与 Active Profile 信息；
- `POST /plan-mission` -> 接收自然语言任务，返回拆解步骤与风险预测；
- `POST /tasks` -> 确认并正式下发任务；
- `POST /tasks/<id>/cancel` -> 请求取消任务；
- `GET /events` -> SSE 实时事件游标流；
- `POST /recover` -> 执行两阶段安全恢复。

---

## 4. 安全与异常处理规范

1. **防路径逃逸（Path Traversal Prevention）**：静态文件服务器对所有 `/static/*` 请求进行正规化路径校验，禁止访问 `web_console/` 目录之外的任何文件。
2. **Fail-Closed 错误呈现**：任何 API 错误或安全阻断均通过结构化四段式通知卡片展示，明确标明机器人当前是否处于安全静止状态。
3. **断线自愈与游标续播**：SSE 连接断开后自动以指数退避重连，并携带最新接收的 `Event ID` 游标实现无损事件流续播。

---

## 5. 测试与验证策略

1. **后端路由测试 (`tests/test_web_console_routes.py`)**：
   - 验证 `GET /` 与 `GET /console` 响应；
   - 验证静态资源 MIME 类型与防路径穿越；
   - 验证 API 联动（`/readiness`、`/plan-mission`、`/tasks`、`/recover`）。
2. **端到端集成验证**：
   - 使用 `fireclaw start` 启动服务，运行 `fireclaw open --no-browser` 获取 URL 并通过自动化测试验证前端完整渲染与 5 大页面功能。
