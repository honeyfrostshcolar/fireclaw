# 友好错误提示 Implementation Plan

> 状态说明：本文是当时的设计/实施记录，不是当前产品能力声明。当前闭环边界与剩余缺口以 `memory/2026-08-19/seven-stage-ux-closure-audit.md` 及后续记录为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 FireClaw 全系统的错误提示从原始技术错误码改造为 4 段式中文友好提示（发生了什么 / 机器人是否安全 / 已采取措施 / 下一步），技术详情折叠隐藏。

**Architecture:** 建立统一的 `FriendlyErrorTemplate` 注册表（`src/fireclaw_core/errors/`），通过 `resolve_friendly_error()` 将任意错误码解析为 4 段结构化响应。CLI、Gateway API、Web Console 三端共用同一张注册表。Gateway 返回结构化 JSON 错误，CLI 输出带框中文 4 段，Web Console 增强 Toast 与模态框。

**Tech Stack:** Python 3.8+ (dataclasses, string.Template), 原生 JavaScript, CSS

## Global Constraints

- Python 3.8 兼容（`from __future__ import annotations`）
- 零外部依赖，纯标准库实现
- 向后兼容：Gateway 顶层 `message` 字段保留，CLI `--json` 输出保留原始结构
- 所有中文模板支持 `{variable}` 插值（使用 `str.format_map` 安全模式）
- 兜底模板：未注册错误码仍输出 4 段式（含通用安全状态）
- 不破坏现有 2280+ 项测试

---

### Task 1: FriendlyError 注册表与解析引擎

**Files:**
- Create: `src/fireclaw_core/errors/__init__.py`
- Create: `src/fireclaw_core/errors/friendly_errors.py`
- Create: `src/fireclaw_core/errors/error_registry.py`
- Test: `tests/test_friendly_errors.py`

**Interfaces:**
- Consumes: 无（基础模块）
- Produces:
  - `FriendlyErrorTemplate(error_code, severity, what_happened, robot_safe_status, action_taken, next_steps, suggested_actions)` dataclass
  - `FriendlyErrorResponse(error_code, severity, what_happened, robot_safe_status, action_taken, next_steps, suggested_actions, technical_details)` dataclass，`.to_dict() -> dict`
  - `resolve_friendly_error(error_code: str, context: dict | None, technical_details: str | None) -> FriendlyErrorResponse`
  - `format_friendly_error_cli(response: FriendlyErrorResponse, verbose: bool) -> str` — 返回带框 4 段中文文本
  - `FRIENDLY_ERROR_REGISTRY: dict[str, FriendlyErrorTemplate]` — 30-50 条错误码映射
  - `FALLBACK_TEMPLATE: FriendlyErrorTemplate` — 兜底模板

- [ ] **Step 1: 编写测试 `tests/test_friendly_errors.py`**

```python
"""Tests for FriendlyError registry, resolution, and CLI formatting."""
from __future__ import annotations
import unittest

class TestFriendlyErrorTemplate(unittest.TestCase):
    def test_template_fields_exist(self):
        from fireclaw_core.errors.friendly_errors import FriendlyErrorTemplate
        t = FriendlyErrorTemplate(
            error_code="test_code", severity="warning",
            what_happened="Something happened: {detail}.",
            robot_safe_status="Robot is safe.",
            action_taken="Stopped.", next_steps="Retry.",
            suggested_actions=[{"label": "Retry", "action": "retry"}],
        )
        self.assertEqual(t.error_code, "test_code")
        self.assertEqual(t.severity, "warning")

    def test_resolve_known_error_code(self):
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error
        resp = resolve_friendly_error("ros_master_unreachable", {"master_uri": "http://localhost:11311", "timeout": "2.0"})
        self.assertEqual(resp.error_code, "ros_master_unreachable")
        self.assertEqual(resp.severity, "critical")
        self.assertIn("localhost:11311", resp.what_happened)
        self.assertIn("2.0", resp.what_happened)
        self.assertTrue(len(resp.robot_safe_status) > 0)
        self.assertTrue(len(resp.action_taken) > 0)
        self.assertTrue(len(resp.next_steps) > 0)

    def test_resolve_unknown_code_uses_fallback(self):
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error
        resp = resolve_friendly_error("totally_unknown_xyz_123")
        self.assertEqual(resp.severity, "warning")
        self.assertIn("totally_unknown_xyz_123", resp.what_happened)
        self.assertTrue(len(resp.robot_safe_status) > 0)

    def test_resolve_with_technical_details(self):
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error
        resp = resolve_friendly_error("sensor_no_lidar_data", {"topic_name": "/scan"}, technical_details="Traceback ...")
        self.assertEqual(resp.technical_details, "Traceback ...")
        self.assertIn("/scan", resp.what_happened)

    def test_to_dict_contains_all_fields(self):
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error
        resp = resolve_friendly_error("gateway_connection_failed", {"gateway_url": "http://127.0.0.1:8765"})
        d = resp.to_dict()
        for key in ("error_code", "severity", "what_happened", "robot_safe_status", "action_taken", "next_steps", "suggested_actions"):
            self.assertIn(key, d)

    def test_format_cli_output_has_four_sections(self):
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error, format_friendly_error_cli
        resp = resolve_friendly_error("authorization_missing")
        text = format_friendly_error_cli(resp, verbose=False)
        self.assertIn("发生了什么", text)
        self.assertIn("机器人是否安全", text)
        self.assertIn("已采取的措施", text)
        self.assertIn("建议下一步", text)
        self.assertNotIn("Traceback", text)

    def test_format_cli_verbose_includes_technical(self):
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error, format_friendly_error_cli
        resp = resolve_friendly_error("config_save_failed", {"profile_path": "/tmp/test.toml"}, technical_details="PermissionError: [Errno 13]")
        text = format_friendly_error_cli(resp, verbose=True)
        self.assertIn("PermissionError", text)

    def test_registry_has_minimum_coverage(self):
        from fireclaw_core.errors.error_registry import FRIENDLY_ERROR_REGISTRY
        self.assertGreaterEqual(len(FRIENDLY_ERROR_REGISTRY), 30)

    def test_safe_interpolation_missing_key(self):
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error
        resp = resolve_friendly_error("ros_master_unreachable", {})
        # Should not raise KeyError, missing keys remain as {key}
        self.assertIsNotNone(resp.what_happened)

    def test_severity_values_valid(self):
        from fireclaw_core.errors.error_registry import FRIENDLY_ERROR_REGISTRY
        for code, tpl in FRIENDLY_ERROR_REGISTRY.items():
            self.assertIn(tpl.severity, ("critical", "warning", "info"), f"Invalid severity for {code}")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_friendly_errors.py`
Expected: FAIL (ImportError)

- [ ] **Step 3: 实现 `friendly_errors.py`、`error_registry.py` 与 `__init__.py`**

`friendly_errors.py`：
- `FriendlyErrorTemplate` dataclass（7 字段）
- `FriendlyErrorResponse` dataclass（8 字段 + `to_dict()`）
- `FALLBACK_TEMPLATE`：通用兜底模板
- `resolve_friendly_error(error_code, context, technical_details) -> FriendlyErrorResponse`：
  - 从 `FRIENDLY_ERROR_REGISTRY` 查找模板
  - 未找到则用 `FALLBACK_TEMPLATE`
  - 使用安全插值（`str.format_map` + `defaultdict(lambda: "{" + key + "}")`）
- `format_friendly_error_cli(response, verbose) -> str`：
  - 生成带 `┌─ ⚠/✖ 标题 ─┐` 框的 4 段中文文本
  - `verbose=True` 时追加技术详情块

`error_registry.py`：
- `FRIENDLY_ERROR_REGISTRY` 字典，包含 30-50 条映射，覆盖以下类别：
  - **传感器类** (~8 条)：`sensor_no_lidar_data`, `sensor_no_thermal`, `sensor_no_gas`, `sensor_degraded`, `sensor_timeout`, `sensor_unavailable`, `sensor_health_unknown`, `sensor_evidence_invalid`
  - **ROS 通信类** (~5 条)：`ros_master_unreachable`, `ros_topic_timeout`, `ros_action_aborted`, `ros_reconnection_exhausted`, `ros_graph_discovery_failed`
  - **网关与网络类** (~4 条)：`gateway_connection_failed`, `gateway_request_failed`, `network_disconnect`, `gateway_crash`
  - **安全门与授权类** (~6 条)：`authorization_missing`, `authorization_denied`, `safety_blocked`, `safety_requires_confirmation`, `real_robot_blocked`, `emergency_stop_active`
  - **执行与任务类** (~6 条)：`planner_failed`, `skill_execution_failed`, `action_failed`, `task_cancelled`, `task_timed_out`, `precondition_failed`
  - **配置类** (~4 条)：`config_save_failed`, `config_rollback_failed`, `config_validation_failed`, `snapshot_not_found`
  - **基础设施类** (~4 条)：`disk_full`, `database_lock`, `daemon_start_failed`, `checkpoint_error`
  - **机器人状态类** (~3 条)：`robot_offline`, `low_battery`, `target_unreachable`

- [ ] **Step 4: 运行测试验证通过**

Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_friendly_errors.py`
Expected: PASS (10 tests)

- [ ] **Step 5: 暂存改动**

---

### Task 2: CLI 4 段式中文输出与 Gateway 结构化错误响应

**Files:**
- Modify: `src/fireclaw_core/mission/mission_cli.py`
- Modify: `src/fireclaw_core/mission/mission_gateway.py`
- Test: `tests/test_friendly_cli_and_api.py`

**Interfaces:**
- Consumes:
  - Task 1: `resolve_friendly_error()`, `format_friendly_error_cli()`, `FriendlyErrorResponse.to_dict()`
- Produces:
  - `print_friendly_error(error_code, context, technical_details, verbose)` — CLI 帮助函数
  - Gateway `_write_friendly_error(handler, status, error_code, context, technical_details)` — 结构化 JSON 错误响应
  - Gateway 错误响应格式：`{"status": "error", "message": "...", "error": {"code": "...", "severity": "...", "what_happened": "...", "robot_safe_status": "...", "action_taken": "...", "next_steps": "...", "suggested_actions": [...], "technical_details": "..."}}`

- [ ] **Step 1: 编写测试 `tests/test_friendly_cli_and_api.py`**

```python
"""Tests for CLI friendly error output and Gateway structured error responses."""
from __future__ import annotations
import unittest
import json

class TestCLIFriendlyOutput(unittest.TestCase):
    def test_print_friendly_error_returns_four_sections(self):
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error, format_friendly_error_cli
        resp = resolve_friendly_error("ros_master_unreachable", {"master_uri": "http://localhost:11311", "timeout": "2.0"})
        text = format_friendly_error_cli(resp, verbose=False)
        self.assertIn("❶", text)
        self.assertIn("❷", text)
        self.assertIn("❸", text)
        self.assertIn("❹", text)

    def test_cli_error_helper_captures_output(self):
        import io, contextlib
        from fireclaw_core.mission.mission_cli import print_friendly_error
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            print_friendly_error("sensor_no_lidar_data", {"topic_name": "/scan"})
        output = buf.getvalue()
        self.assertIn("发生了什么", output)
        self.assertIn("/scan", output)

class TestGatewayStructuredErrors(unittest.TestCase):
    def test_gateway_error_response_has_error_detail(self):
        """Verify Gateway returns structured 4-part error in JSON."""
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error
        resp = resolve_friendly_error("config_save_failed", {"profile_path": "/tmp/test.toml"}, "PermissionError")
        d = resp.to_dict()
        body = {"status": "error", "message": d["what_happened"], "error": d}
        self.assertIn("error", body)
        self.assertIn("what_happened", body["error"])
        self.assertIn("robot_safe_status", body["error"])
        self.assertEqual(body["message"], d["what_happened"])

    def test_gateway_preserves_backward_compat_message(self):
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error
        resp = resolve_friendly_error("gateway_connection_failed", {"gateway_url": "http://127.0.0.1:8765"})
        d = resp.to_dict()
        body = {"status": "error", "message": d["what_happened"], "error": d}
        self.assertIn("message", body)
        self.assertIsInstance(body["message"], str)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_friendly_cli_and_api.py`
Expected: FAIL

- [ ] **Step 3: 实现 CLI `print_friendly_error` 与 Gateway `_write_friendly_error`**

在 `mission_cli.py` 中：
- 添加 `print_friendly_error(error_code, context=None, technical_details=None, verbose=False)` 帮助函数
- 替换现有 15+ 处 `print(f"Error: ...", file=sys.stderr)` 为 `print_friendly_error(...)` 调用
- 保留 `--json` 模式原始输出，仅人类可读模式使用 4 段框

在 `mission_gateway.py` 中：
- 添加 `_write_friendly_error(handler, status, error_code, context=None, technical_details=None)` 方法
- 构造 `{"status": "error", "message": what_happened, "error": response.to_dict()}`
- 替换关键路径的 `_write_error()` 调用为 `_write_friendly_error()`（优先覆盖 `/plan-mission`、`/recover`、`/config/*` 端点）

- [ ] **Step 4: 运行测试验证通过**

Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_friendly_cli_and_api.py tests/test_friendly_errors.py tests/test_config_cli_and_api.py tests/test_mission_gateway.py`
Expected: PASS

- [ ] **Step 5: 暂存改动**

---

### Task 3: Web Console 增强 Toast 与 4 段模态框

**Files:**
- Modify: `src/fireclaw_core/web_console/app.js`
- Modify: `src/fireclaw_core/web_console/index.html`
- Modify: `src/fireclaw_core/web_console/style.css`
- Test: `tests/test_friendly_web_integration.py`

**Interfaces:**
- Consumes:
  - Task 2: Gateway 结构化 `{"error": {...}}` JSON 响应
- Produces:
  - `showFriendlyError(errorData)` — 根据 severity 自动选择增强 Toast 或完整模态框
  - `showEnhancedToast(message, safeStatus, errorData)` — 带安全状态 + "查看详情" 的增强 Toast
  - `showErrorModal` 增强：动态操作按钮区 `#modal-suggested-actions`、可折叠技术详情 `#modal-technical-details`

- [ ] **Step 1: 编写测试 `tests/test_friendly_web_integration.py`**

测试 HTML 结构中是否包含新增的模态框元素（`#modal-suggested-actions`、`#modal-technical-details`、折叠按钮），测试 `app.js` 中是否定义 `showFriendlyError` 方法，测试增强 Toast 是否包含"查看详情"按钮结构。

- [ ] **Step 2: 运行测试验证失败**

Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_friendly_web_integration.py`
Expected: FAIL

- [ ] **Step 3: 增强 Web Console 前端**

`index.html`：
- 在 `#error-modal` 内 `#modal-next-steps` 后增加：
  - `#modal-suggested-actions`：动态操作按钮容器
  - `#modal-technical-details`：可折叠技术详情面板（`<details><summary>查看技术详情</summary><pre id="modal-tech-content"></pre></details>`）

`app.js`：
- `showFriendlyError(errorData)`：根据 `errorData.severity` 分流：
  - `critical` → 直接调用增强版 `showErrorModal`
  - `warning` → 调用 `showEnhancedToast`
  - `info` → 普通 `showToast`
- `showEnhancedToast(message, safeStatus, errorData)`：
  - Toast 内容：`⚠ {message}\n{safeStatus}  [查看详情 →]`
  - 点击"查看详情"触发 `showErrorModal(errorData)`
  - 持续时间加长至 8s（给用户阅读时间）
- 增强 `showErrorModal`：
  - 接受 `errorData` 对象（含 `suggested_actions` 和 `technical_details`）
  - 动态渲染操作按钮到 `#modal-suggested-actions`
  - 填充 `#modal-technical-details` 并默认折叠
- 全局错误处理器 `handleApiError(resp, fallbackCode)`：
  - 解析 Gateway 返回的 `resp.json()`
  - 优先读取 `error` 字段，调用 `showFriendlyError`
  - 无 `error` 字段时用 `fallbackCode` 本地解析
- 替换现有 `catch` 块中的 `showToast(..., 'danger')` 为 `handleApiError`

`style.css`：
- `.toast-enhanced`：增强版 Toast 样式（双行布局 + 查看详情链接）
- `#modal-suggested-actions`：操作按钮行样式（flex gap 排列）
- `#modal-technical-details`：折叠面板样式（等宽字体、深色背景、圆角滚动）
- `.btn-suggested-action`：操作按钮样式（与主题一致的边框按钮）

- [ ] **Step 4: 运行测试验证通过**

Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q tests/test_friendly_web_integration.py tests/test_web_console_ui.py tests/test_web_console_routes.py`
Expected: PASS

- [ ] **Step 5: 暂存改动**

---

### Task 4: 全量回归、故障矩阵与文档更新

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started/first-run-setup.md`
- Test: 全量回归 + 故障矩阵

**Interfaces:**
- Consumes: Task 1-3 所有产出
- Produces: 更新的文档、全量测试通过证明、memory 记录

- [ ] **Step 1: 运行全量测试回归**

Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/pytest -q`
Expected: >2290 passed

- [ ] **Step 2: 运行故障矩阵验证友好提示**

Run: `PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python -m fireclaw_core fault-test run --live-ros`
Expected: 7/7 passed

- [ ] **Step 3: 更新文档**

在 `README.md` 和 `docs/getting-started/first-run-setup.md` 中添加友好错误提示说明：
- 用户遇到错误时会看到 4 段中文提示
- 技术详情通过 `--verbose` (CLI) 或"查看技术详情"(Web) 查看
- 所有错误码的含义可通过 `fireclaw errors list` 查看

- [ ] **Step 4: 写入 memory 记录**

写入 `memory/2026-08-14/friendly-errors-completed.md`

- [ ] **Step 5: 暂存改动**
