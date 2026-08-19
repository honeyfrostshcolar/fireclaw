# UX-P1：符合小白习惯的友好错误提示

> 状态说明：本文是当时的设计/实施记录，不是当前产品能力声明。当前闭环边界与剩余缺口以 `memory/2026-08-19/seven-stage-ux-closure-audit.md` 及后续记录为准。

## 问题背景

当前 FireClaw 的错误提示存在三个问题：

1. **技术术语直接暴露**：用户看到 `sensor_evidence_invalid`、`authorization_missing`、`checkpoint_error` 这类原始错误码，完全无法理解发生了什么。
2. **缺失安全状态反馈**：错误发生后，用户最关心的是"机器人现在是否安全？"，但当前提示没有回答这个问题。
3. **没有可操作的下一步**：用户不知道该做什么——是等待、重试、检查硬件还是联系维护人员。

## 设计原则

所有面向用户的错误信息必须固定回答 4 个问题：

| # | 问题 | 字段名 | 示例 |
|:--|:-----|:-------|:-----|
| 1 | **发生了什么？** | `what_happened` | 未检测到有效的激光雷达数据。 |
| 2 | **机器人现在是否安全？** | `robot_safe_status` | 机器人没有开始移动，当前处于安全阻塞状态。 |
| 3 | **FireClaw 已经采取了什么措施？** | `action_taken` | 已自动中止导航任务并锁定底盘执行器。 |
| 4 | **用户下一步做什么？** | `next_steps` | 请检查雷达连接，然后点击"重新检测"。 |

**技术详情**（错误码、JSON 原文、堆栈、日志路径）放在可折叠的"查看技术详情"区域，不作为默认内容。

---

## 架构设计

### 1. 统一 FriendlyError 注册表

新建 `src/fireclaw_core/errors/friendly_errors.py`，建立错误码 → 4 段中文模板的中央映射表。

```python
@dataclass
class FriendlyErrorTemplate:
    """一条友好错误的 4 段式中文模板。"""
    error_code: str                    # 机器内部码，如 "sensor_no_lidar_data"
    severity: str                      # "critical" | "warning" | "info"
    what_happened: str                 # 支持 {变量} 插值
    robot_safe_status: str
    action_taken: str
    next_steps: str
    suggested_actions: list[dict]      # [{"label": "重新检测", "action": "retry_sensor_check"}, ...]

# 注册表示例
FRIENDLY_ERROR_REGISTRY: dict[str, FriendlyErrorTemplate] = {
    "sensor_no_lidar_data": FriendlyErrorTemplate(
        error_code="sensor_no_lidar_data",
        severity="critical",
        what_happened="未检测到有效的激光雷达数据（话题: {topic_name}）。",
        robot_safe_status="机器人没有开始移动，当前处于安全阻塞状态。",
        action_taken="已自动中止导航任务并锁定底盘执行器。",
        next_steps="请检查雷达电源与数据线连接，然后点击「重新检测」。",
        suggested_actions=[
            {"label": "重新检测", "action": "retry_sensor_check"},
            {"label": "打开雷达设置", "action": "open_sensor_settings"},
            {"label": "查看技术详情", "action": "show_technical_details"},
        ],
    ),
    "ros_master_unreachable": FriendlyErrorTemplate(
        error_code="ros_master_unreachable",
        severity="critical",
        what_happened="无法连接到 ROS Master（地址: {master_uri}，超时: {timeout}s）。",
        robot_safe_status="机器人处于离线状态，所有执行器已锁定，无物理风险。",
        action_taken="已停止所有依赖 ROS 通信的任务，等待连接恢复。",
        next_steps="请确认 ROS Master 节点是否已启动（`roscore`），并检查网络连接。",
        suggested_actions=[
            {"label": "重新连接", "action": "retry_ros_connect"},
            {"label": "检查网络设置", "action": "open_network_settings"},
        ],
    ),
    "gateway_connection_failed": FriendlyErrorTemplate(
        error_code="gateway_connection_failed",
        severity="warning",
        what_happened="无法连接到 FireClaw Gateway（地址: {gateway_url}）。",
        robot_safe_status="机器人本体不受 Gateway 连接影响，底盘状态安全。",
        action_taken="已切换为离线模式，本地缓存的配置与状态仍然有效。",
        next_steps="请确认 Gateway 进程是否正在运行（`fireclaw status`），或检查防火墙设置。",
        suggested_actions=[
            {"label": "重试连接", "action": "retry_gateway"},
            {"label": "启动 Gateway", "action": "start_gateway"},
        ],
    ),
    "authorization_missing": FriendlyErrorTemplate(
        error_code="authorization_missing",
        severity="critical",
        what_happened="当前操作缺少安全授权许可。",
        robot_safe_status="操作已被安全门控拦截，机器人未执行任何物理动作。",
        action_taken="已阻止未授权操作并记录安全审计日志。",
        next_steps="请前往「恢复中心」完成两阶段安全恢复授权，或联系管理员。",
        suggested_actions=[
            {"label": "前往恢复中心", "action": "navigate_recovery"},
        ],
    ),
    "config_save_failed": FriendlyErrorTemplate(
        error_code="config_save_failed",
        severity="warning",
        what_happened="配置文件保存失败（路径: {profile_path}）。",
        robot_safe_status="机器人仍在使用上一份有效配置运行，当前状态安全。",
        action_taken="已保留当前运行配置不变，未产生任何副作用。",
        next_steps="请检查磁盘空间与文件权限，或尝试保存到其他路径。",
        suggested_actions=[
            {"label": "重试保存", "action": "retry_save"},
            {"label": "查看磁盘状态", "action": "check_disk"},
        ],
    ),
    # ... 更多错误码将在实施阶段补全（预计 30-50 条覆盖全部用户可见错误）
}
```

**核心 API**：

```python
def resolve_friendly_error(
    error_code: str,
    context: dict[str, str] | None = None,
    technical_details: str | None = None,
) -> FriendlyErrorResponse:
    """将原始错误码解析为 4 段式友好错误响应。

    - 如果 error_code 在注册表中：返回模板插值后的友好描述。
    - 如果 error_code 不在注册表中：返回通用兜底描述（仍然是 4 段式）。
    - technical_details 被包装进 "技术详情" 字段，不作为主要内容。
    """
```

### 2. Gateway API 结构化错误响应

**Before（当前）**：
```json
{"status": "error", "message": "sensor_evidence_invalid"}
```

**After（改造后）**：
```json
{
  "status": "error",
  "error": {
    "code": "sensor_no_lidar_data",
    "severity": "critical",
    "what_happened": "未检测到有效的激光雷达数据（话题: /scan）。",
    "robot_safe_status": "机器人没有开始移动，当前处于安全阻塞状态。",
    "action_taken": "已自动中止导航任务并锁定底盘执行器。",
    "next_steps": "请检查雷达连接，然后点击「重新检测」。",
    "suggested_actions": [
      {"label": "重新检测", "action": "retry_sensor_check"},
      {"label": "打开雷达设置", "action": "open_sensor_settings"}
    ],
    "technical_details": "LaserScan topic /scan: no messages received in 5.0s. Last known publisher: /gazebo (inactive). ROS Master status: connected."
  }
}
```

向后兼容：顶层 `message` 字段保留（设为 `what_happened` 的值），不破坏现有客户端。

### 3. CLI 4 段式中文输出

**Before（当前）**：
```
Error: sensor_evidence_invalid
```

**After（改造后）**：
```
┌─ ⚠ 安全警告 ─────────────────────────────────────────────────
│
│  ❶ 发生了什么
│     未检测到有效的激光雷达数据（话题: /scan）。
│
│  ❷ 机器人是否安全
│     机器人没有开始移动，当前处于安全阻塞状态。
│
│  ❸ 已采取的措施
│     已自动中止导航任务并锁定底盘执行器。
│
│  ❹ 建议下一步
│     请检查雷达连接，然后运行: fireclaw sensor check
│
│  技术详情: sensor_no_lidar_data (运行 --verbose 查看完整日志)
└───────────────────────────────────────────────────────────────
```

支持 `--json` 输出原始结构化 JSON（供自动化脚本消费），`--verbose` 展开完整技术详情。

### 4. Web Console 增强版 Toast + 4 段模态框

**分级策略**：

| 严重度 | 表现形式 |
|:------|:---------|
| `critical` | 自动弹出完整 4 段式模态框（带操作按钮），Toast 不显示 |
| `warning` | 增强版 Toast：中文描述 + 安全状态一句话 + 可点击「查看详情」→ 展开 4 段模态框 |
| `info` | 普通 Toast 保持不变（已经是正常提示） |
| `success` | 普通 Toast 保持不变 |

**增强版 Toast 结构**：
```
╭──────────────────────────────────────────────────╮
│ ⚠ ROS 探测失败: 无法连接到 ROS Master             │
│   机器人处于离线安全状态。         [查看详情 →]    │
╰──────────────────────────────────────────────────╯
```

点击「查看详情」→ 弹出已有的 4 段式 `#error-modal`，填充完整内容与操作按钮。

**4 段式模态框增强**：在现有 `showErrorModal` 基础上增加：
- `#modal-suggested-actions`：动态操作按钮区（如 `[重新检测]` `[打开雷达设置]`）
- `#modal-technical-details`：可折叠的技术详情面板（默认收起）

### 5. 兜底与未注册错误码

对于注册表中不存在的错误码，提供通用 4 段兜底模板：

```python
FALLBACK_TEMPLATE = FriendlyErrorTemplate(
    error_code="unknown",
    severity="warning",
    what_happened="发生了一个未预期的问题（错误码: {error_code}）。",
    robot_safe_status="FireClaw 已自动进入保护模式，底盘执行器已锁定。",
    action_taken="已中止当前操作并保存状态快照。",
    next_steps="请尝试重新操作。如问题持续，请运行 `fireclaw diagnostics` 并联系技术支持。",
    suggested_actions=[
        {"label": "重试", "action": "retry"},
        {"label": "查看技术详情", "action": "show_technical_details"},
    ],
)
```

---

## 文件变更计划

### 新增文件
| 文件 | 说明 |
|:-----|:-----|
| `src/fireclaw_core/errors/__init__.py` | 错误模块包导出 |
| `src/fireclaw_core/errors/friendly_errors.py` | `FriendlyErrorTemplate`、`FriendlyErrorResponse`、`resolve_friendly_error()`、CLI 格式化输出 |
| `src/fireclaw_core/errors/error_registry.py` | 30-50 条覆盖全系统的错误码 → 4 段中文模板映射 |
| `tests/test_friendly_errors.py` | 注册表解析、插值、兜底、序列化测试 |

### 修改文件
| 文件 | 改动 |
|:-----|:-----|
| `src/fireclaw_core/mission/mission_gateway.py` | 错误响应改为结构化 4 段 JSON（保留 `message` 兼容字段） |
| `src/fireclaw_core/mission/mission_cli.py` | `print(f"Error: ...")` 替换为 `print_friendly_error()` 4 段中文框 |
| `src/fireclaw_core/web_console/app.js` | 增强 Toast 组件 + 动态操作按钮 + 技术详情折叠面板 |
| `src/fireclaw_core/web_console/index.html` | 模态框增加 `#modal-suggested-actions` 和 `#modal-technical-details` |
| `src/fireclaw_core/web_console/style.css` | 增强 Toast、技术详情折叠、操作按钮样式 |
| `tests/test_config_cli_and_api.py` | 验证 CLI 和 API 错误响应格式 |
| `tests/test_web_console_ui.py` | 验证模态框新元素 |

---

## 验证计划

### 自动化测试
- `PYTHONPATH=src pytest -q tests/test_friendly_errors.py`：注册表覆盖率、插值正确性、兜底行为
- `PYTHONPATH=src pytest -q tests/test_config_cli_and_api.py`：CLI 错误输出格式验证
- `PYTHONPATH=src pytest -q`：全量回归

### 人工验证
- 在 Web Console 中触发典型错误（ROS 断连、网关不可达、配置保存失败），确认 Toast 和模态框显示 4 段中文内容
- 在 CLI 中运行 `fireclaw profile discover`（ROS 离线时），确认看到 4 段中文框而非技术错误码

### 故障矩阵
- `fireclaw fault-test run --live-ros`：7 类故障注入全部验证友好提示输出
