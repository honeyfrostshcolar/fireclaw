# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本仓库中工作时提供指导。

## 项目概述

FireClaw 是一个面向消防机器人的 Python 优先具身智能体框架，受 OpenClaw 的智能体/技能/记忆架构启发。当前版本是纯 dry-run 核心——在不控制真实硬件、ROS 或机器人 SDK 的情况下验证主智能体循环。

核心循环：`自然语言指令 → 规划器 → 安全门控 → 技能执行器 → 记忆`

## 构建与测试命令

```bash
# 环境搭建（使用项目 venv，不要用系统 python）
uv venv --python /home/nankai/.local/bin/python3.11 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

# 运行全部测试
.venv/bin/python -m pytest -v

# 运行单个测试文件
.venv/bin/python -m pytest tests/test_agent.py -v

# 运行单个测试函数
.venv/bin/python -m pytest tests/test_agent.py::test_name -v

# 按关键字过滤运行
.venv/bin/python -m pytest -k "rescue" -v

# 直接运行 CLI
.venv/bin/python -m fireclaw_core "去二楼救人"

# 运行救援演示（端到端，通过 Gateway + mock ROS1）
.venv/bin/python -m fireclaw_core --demo rescue

# 运行 HTTP 网关
.venv/bin/python -m fireclaw_core.gateway --port 8765
```

## 架构

### 主智能体循环 (`agent.py`)

`FireClawAgent.run(command)` 编排完整流水线：
1. **指令路由** —— 在规划前检测记忆查询、技能列表、确认/取消等特殊指令
2. **会话上下文** —— 利用会话历史消解歧义指令（例如在 clarify 响应后"二楼"会被补全为"去二楼救人"）
3. **规划** —— `RuleBasedPlanner` 将中文自然语言指令分解为带有序 `PlanStep` 的类型化 `Plan` 对象
4. **安全门控** —— `SafetyGate.evaluate()` 根据技能注册表、传感器可用性、机器人状态、环境状态和风险等级评估计划。返回 `allow`/`block`/`clarify`/`require_confirmation`
5. **执行** —— `PlanExecutor` 顺序执行每个步骤，支持 `FailurePolicy` 驱动的重试逻辑、取消支持和事件发射
6. **记忆** —— 结果追加到 JSONL 记忆存储，用于会话连续性

### 机器人适配器 (`robot.py`)

基于 Protocol 的适配器层，将机器人硬件与智能体隔离：

| 适配器 | 模式 | 用途 |
|--------|------|------|
| `DryRunRobotAdapter` | `dry_run` | 默认。记录动作，始终成功。 |
| `MockRos1RobotAdapter` | `mock_ros1` | 生成 ROS1 风格的命令规格，不实际传输。 |
| `MockRos2RobotAdapter` | `mock_ros2` | 生成 ROS2 风格的话题命令。 |
| `SimulatorRobotAdapter` | `simulator` | 有状态仿真（跟踪楼层、受困者、电量）。 |
| `Ros1RobotAdapter` | `ros1` | 通过配置驱动端点的真正 ROS1 传输。 |

所有适配器实现 `RobotAdapter` 协议：`navigate_to_floor`、`search_for_victims`、`assess_victim`、`report_status`、`return_to_safe_zone`、`emergency_stop`、`get_robot_state`、`get_environment_state`。

### 网关 (`gateway.py`)

面向多任务编排的 HTTP 控制平面：
- `POST /tasks` —— 提交异步智能体任务（支持操作员认证、去重、容量限制）
- `POST /tasks/{id}/cancel` —— 取消运行中的任务
- `POST /emergency-stop` —— 急停所有任务 + 机器人
- `POST /confirm` —— 确认待安全门控的任务（支持授权过期）
- `GET /health`、`/state`、`/skills`、`/memory/recent`、`/events/recent`、`/tasks/{id}`
- 每任务一线程执行，基于 `threading.Event` 的取消机制
- 事件账本用于审计追踪（记录所有任务生命周期事件）

### 技能系统 (`skills.py`, `workspace_skills.py`)

技能是围绕机器人动作的类型化、元数据丰富的包装器：
- **内置技能** —— 由 `create_default_skill_registry()` 创建，映射到机器人适配器方法
- **工作区技能** —— 从 `skills/` 目录下的 `*.skill.json` 清单加载，可以子进程方式运行
- 每个技能声明：`input_schema`、`risk_level`、`dry_run_only`、`allow_real_robot`、`required_sensors`、`max_attempts`、`idempotent`、`timeout_seconds`
- `SkillRegistry` 提供查找、元数据列表和扩展功能

### 安全门控 (`safety.py`)

从多个安全维度评估计划：
- 缺少技能或传感器 → 拦截
- 非幂等技能设置重试 → 拦截
- dry-run 模式下使用非 dry-run 技能 → 拦截
- 真实机器人执行未确认 → 要求确认
- 高/关键风险等级 → 要求确认
- 机器人离线或电量过低 → 拦截
- 目标楼层不可达 → 拦截

### 记忆 (`memory.py`)

基于 JSONL 的仅追加记忆存储。支持按会话范围查询和按状态/意图/楼层搜索。用于跨轮次的会话连续性。

### ROS1 集成 (`ros1_config.py`, `ros1_transport.py`, `ros1_template.py`)

配置驱动的 ROS1 适配器：
- `Ros1AdapterConfig` 定义机器人 ID、端点（话题/动作/服务）、目标和传输设置
- 模板根据技能输入 + 配置目标渲染 ROS1 消息负载
- 传输层支持带反馈和取消的动作目标

### 其他关键模块

- `action_runtime.py` —— 桥接技能执行到机器人适配器，带事件发射
- `control.py` —— 操作员授权、控制策略评估、基于风险的审批
- `task_queue.py` —— 基于 JSONL 的任务队列，支持状态跟踪和去重
- `task_state.py` —— 将事件序列投影为任务状态快照
- `event_ledger.py` —— 仅追加的 JSONL 事件日志，用于审计追踪
- `monitor.py` —— 失败策略（重试 vs 中止决策）
- `mission_agent.py`、`mission_registry.py`、`mission_cli.py` —— 任务控制层，用于多步骤任务编排
- `llm_planner.py` —— 基于 LLM 的规划器接口（扩展规则规划器）
- `doctor.py` —— 系统健康检查和诊断
- `subagent_client.py` —— 用于向子进程分发工作的客户端

## 编码规范

- **要求 Python 3.10+**。使用 `from __future__ import annotations` 进行前向引用。
- **优先使用数据类**。不可变类型使用 `@dataclass(frozen=True)`。
- **Protocol 类**用于接口定义（参见 `RobotAdapter`、`MemoryStore`、`Planner`）。
- **所有公共 API 需要类型注解**。内部辅助函数可省略。
- **用户面向消息使用中文**，代码/命令/路径/类名/API 使用英文。
- **禁止硬编码密钥、路径或凭据。** 使用配置文件或 CLI 参数。
- **确定性测试。** 模拟机器人适配器，不模拟 LLM 输出。分别测试 schema 验证、路由、安全门控和回退行为。

## OpenClaw 参考

`openclaw/` 是 OpenClaw 的本地参考副本。它已被 gitignore，作为架构参考资料，而非 FireClaw 源代码。

构建具有 OpenClaw 对应物的 FireClaw 模块时：
1. 先用 CodeGraph 检查 OpenClaw 源码（`codegraph_context` → `codegraph_explore`）
2. 在合适的地方复用已验证的模块结构
3. 针对消防机器人约束进行适配（安全门控、ROS、降级通信、审计日志）
4. 如果偏离 OpenClaw 设计，请记录原因

不要盲目复制 OpenClaw 代码。不要将 OpenClaw 的 pnpm/Vitest 命令应用到 FireClaw。

## 记忆与会话连续性

多日工作记录存放在 `memory/YYYY-MM-DD/` 下。恢复工作时：
1. 列出 `memory/` 下的日期目录
2. 默认只读取最近两个日期目录
3. 在同一日期目录内，优先使用每个主题的最新带时间戳条目
4. 总结：当前进展 / 已完成 / 当前问题 / 下一步 / 需要运行的命令

## CodeGraph

本项目配置了 CodeGraph MCP 服务器。用于结构性问题（调用图、符号查找、影响分析）。仅对字面量文本查询使用 grep/read。

关键命令：`codegraph_search`、`codegraph_context`、`codegraph_explore`、`codegraph_trace`、`codegraph_impact`、`codegraph_files`、`codegraph_status`。

## 安全与机器人约束

消防机器人在危险环境中运行。将所有机器人动作视为安全关键：
- 自然语言指令不足以授权不安全/不可逆动作
- 失败安全默认值：停止、保持位置、请求澄清或上报
- 技能元数据必须记录传感器、状态假设、超时和急停行为
- 记录所有决策以便事后重建
- 将仿真行为与真实机器人行为隔离——永远不要让仿真器默认值驱动真实硬件
