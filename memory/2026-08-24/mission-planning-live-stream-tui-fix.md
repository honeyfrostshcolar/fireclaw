# Mission 规划实时事件流与耗时 TUI 修复

## 时间

- 用户复测反馈：2026-08-24
- 最终全量验证：2026-08-24T10:11:10+08:00

## 任务目标

用户确认上一轮 TUI/ROS 可观测性改造仍有一个关键缺口：输入自然语言任务后，终端只显示一次“正在解析任务意图与规划动作...”，随后在 50 秒左右的 Mission Agent 多轮推理期间完全静默，最后才一次性打印 `deliberation_attempts`。用户要求：

1. 运行状态必须显示实时耗时；
2. Mission Agent 的真实中间动作和阶段结果必须在发生时立即出现；
3. 最终仍需给出规划结果/澄清/预览总结；
4. 显示链路必须并行，不阻塞模型规划；
5. 交互形态应接近 OpenClaw/Codex 的 `run -> incremental activity/tool updates -> final`，而不是只模仿外框样式。

## 恢复时读取的上下文

- `memory/2026-08-24/mission-tui-ros-log-observability-fix.md`
- `git status --short`：工作区已有大量 Antigravity/用户改动；本轮未重置、未覆盖无关修改、未创建 commit。

## OpenClaw analogue

先通过 CodeGraph 和定点源码检查：

- `openclaw/src/tui/tui-run-lifecycle.ts`
  - `createTuiRunLifecycle` 统一管理 active run、watchdog、终态和 render request；
  - run start/end 与 streaming activity 分离；
- `openclaw/src/tui/components/chat-log.ts`
  - `startAssistant/updateAssistant/finalizeAssistant`；
  - `startTool/updateTool`；
  - run/tool id 关联增量更新，而不是等待最终历史再整体打印；
- `openclaw/src/tui/tui-event-handlers.ts`
- `openclaw/src/tui/tui-stream-assembler.ts`

复用原则：一个明确 run 生命周期、结构化增量事件、单一渲染所有者、最终结果与中间 activity 分离。

FireClaw 适配：当前 Python CLI 是 append-only、依赖较轻，未引入 OpenClaw 的 TypeScript/pi-tui 全屏组件树；使用 durable `• / ├─ / └─` activity rows，同时由 `MissionTerminalRenderer` 保持终端原子写入。

## 根因

调用链：

`interactive._submit_and_follow`
→ `MissionGatewayClient.preview_mission`
→ 同步 `POST /plan-mission`
→ `MissionGateway.plan_mission`
→ `MissionAgent.deliberate_preview`
→ `MissionDeliberationRuntime.deliberate`
→ 每轮 `policy.decide()`。

每次模型调用约 15-25 秒。旧实现仅在整个 `deliberate()` 返回后把 `attempts` 与 `observations` 放进最终 JSON，因此客户端不可能在运行中得知“回合开始、选择探查、观察完成、计划校验失败、发起澄清”等真实状态。上一版 `LiveActivityIndicator` 只打印一次静态块，不是流式 TUI。

## 实施内容

### 1. Mission deliberation 结构化进度事件

文件：`src/fireclaw_core/mission/mission_deliberation.py`

- `deliberate(..., event_sink=None)` 新增可选观测 sink；
- 事件：
  - `mission_agent.turn.started`
  - `mission_agent.operation.started`
  - `mission_agent.attempt.completed`
- attempt 事件携带 iteration、max_iterations、operation、outcome、duration、reason、validator feedback；
- `inspect_state` 和 Agent Tool 的完成事件携带结构化 observation；
- sink 异常只记录 warning，不会让规划失败；Gateway 传入的实际 sink 是 `Queue.put_nowait` 边界。

文件：`src/fireclaw_core/mission/mission_agent.py`

- `deliberate_preview(..., event_sink=None)` 向 runtime 透传进度事件。

### 2. 有界异步规划 SSE

文件：`src/fireclaw_core/mission/mission_gateway.py`

- 新增：
  - `POST /plan-mission/stream`
  - `POST /plan-mission/clarification/stream`
- `_stream_planning_events`：
  - Mission planning 在独立 bounded daemon worker 中运行；
  - Agent callback 只做状态快照和有界 `put_nowait`；
  - HTTP request thread 独立写 SSE 和每秒 heartbeat；
  - 最终发送 `planning.result` 或 `planning.error`；
  - 慢 socket/终端不会阻塞模型决策；
  - 队列满时累计 `dropped_progress_events`，不伪装完整性；
  - SSE 序号分配与 enqueue 在同一锁内，保证心跳和 Agent 事件严格按序发送；
  - 客户端断开不会下发机器人动作：preview worker 是只读规划，最多生成一个会过期的未确认 artifact。

文件：`src/fireclaw_core/gateway/method_scopes.py`

- 两个流式 POST 端点注册为 `READ_SCOPE`，仍不具备 confirm/dispatch 权限。

### 3. 客户端流式 API 与旧 Gateway 协商

文件：`src/fireclaw_core/mission/mission_gateway_client.py`

- 新增：
  - `stream_preview_mission`
  - `stream_planning_clarification`
  - `_stream_post/_do_stream_request`
  - `MissionGatewayStreamUnsupported`
- 严格校验 `Content-Type: text/event-stream`；
- 旧 Gateway 返回 JSON/404/405 时，CLI 回退原同步 preview API；
- 真正已经启动但中途不完整的 SSE 不自动重试，避免重复创建 dialogue/artifact。

### 4. Codex/OpenClaw 风格规划 TUI

文件：`src/fireclaw_core/mission/interactive.py`

- `MissionTerminalRenderer.activity` 输出 durable activity row：`• / ├─ / └─`；
- `MissionPlanningMonitor` 显示：
  - 规划启动（0.0 秒）；
  - Agent 回合开始；
  - 每 5 秒从 Gateway heartbeat 更新总耗时；
  - 选择的真实 operation、Tool 和模型决策时长；
  - observation、validator reason/error、单回合时长；
  - 最终 `preview_ready / clarification_required / blocked / error` 总结和总用时；
- 流式阶段不再调用 `_display_thinking_trail`，避免结束后重复打印整段批量轨迹；
- `LiveActivityIndicator` 只用于旧 Gateway 兼容路径和短同步阶段，并以 5 秒 durable heartbeat 显示耗时，不使用 carriage-return spinner。

预期片段：

```text
• 🤖 Mission Agent · 规划已启动
  └─ 正在解析任务意图与规划动作...（已用时 0.0 秒）

• 🤖 Mission Agent · 回合 1（上限 4）
  └─ 正在推理下一步动作（总用时 0.1 秒）

• ⏱️ Mission Agent · 回合 1（上限 4）
  └─ 模型仍在生成本回合的结构化决策（总用时 5.2 秒）

• 🤖 Mission Agent · 回合 1（上限 4）
  ├─ 选择动作：🔍 探查机器人状态
  └─ 模型决策用时 16.2 秒

• 🤖 Mission Agent · 回合 1（上限 4）
  ├─ 🔍 探查机器人状态 → 已获得观察（本回合 16.2 秒）
  └─ 观察：获取到 gazebo_turtlebot3 实测位姿 (...)
```

## 测试与验证

新增/更新覆盖：

- `tests/test_interactive.py`
  - 启动、heartbeat、动作、观察、最终总结实时渲染；
  - 流式模式不重复打印 batch 思考轨迹；
- `tests/test_mission_gateway_client.py`
  - streaming POST path/body/Accept 和 SSE final 解析；
- `tests/test_mission_gateway.py`
  - 模拟 socket writer 阻塞，证明 planning worker 仍立即完成；
- `tests/test_sealed_plan_gateway.py`
  - 真实 Gateway/Client loopback：started → turn → operation → attempt → result；
  - sequence 单调、无 dispatch；
- `tests/test_fault_injection_scenarios.py`
  - 旧 Gateway 非 SSE JSON 响应回退后，原 mission SSE cursor 重连仍正常。

命令与结果：

- `python -m py_compile`（全部改动 Python 文件）：通过；
- 初始沙箱聚焦测试：47 passed，其余 70 个均因沙箱禁止创建 loopback socket 报 `PermissionError`，不是断言失败；
- 单项实时 Gateway SSE：1 passed；
- socket backpressure + TUI：2 passed；
- 首轮聚焦：254 passed；
- 全量首错定位发现旧 Gateway JSON compatibility 问题；修复后单项：1 passed；
- 修复后扩展聚焦：`260 passed, 1 skipped in 44.63s`；
- 最终全量：`2466 passed, 8 skipped in 294.86s`；
- `git diff --check`（目标 tracked 文件）：通过。

## 失败尝试与处理

1. 首次把四个大测试文件一起运行时工具等待/授权阶段被中断，后按文件隔离，确认各文件全部通过；不是 SSE hang。
2. 全量首次在 `test_network_disconnect_resumes_actual_sse_session` 失败：旧测试服务对未知 `/plan-mission/stream` 返回普通 JSON 202，而不是 404。拒绝用“流不完整后无条件重试”修复，因为真实 SSE 中断后重试可能重复生成预览。最终以 Content-Type 协商识别“根本不支持 SSE”并只在该情况下回退同步 API。

## 当前结论

- 用户指出的问题属实：上一版只有外观对齐，没有 run lifecycle 对齐；本轮已改成真实增量事件流。
- 规划期间每 5 秒有可见耗时更新；每轮真实 operation、observation、validation outcome 会即时出现；最后仍有阶段总结和澄清/预览块。
- Agent callback 不执行 socket I/O；终端背压不会增加模型规划时长。
- 未暴露模型隐藏 chain-of-thought，只显示可审计的结构化动作、工具、观察和校验结果。
- 未创建 commit；保留所有既有用户/Antigravity 修改。

## 操作员下一步

必须重启 Mission Gateway 和 Mission CLI 才会加载新端点与客户端：

```bash
python -m fireclaw_core serve ...
python -m fireclaw_core mission --server http://127.0.0.1:8766 --timeout 75
```

Robot Gateway/ROS/Gazebo 若仍在运行且本轮未改其代码，可保留；若希望同时复验上一轮 ROS `/rosout_agg` TUI，建议按原启动参数一并重启 Robot Gateway。

## 工程 / 研究判断

- 工程正确性：这是规划可观测性、协议兼容和背压隔离修复，全量回归提供强工程证据。
- 研究有效性：可作为跨层 causal trace 的基础，后续可量化 model turn latency、operation latency、operator intervention latency 和 dropped-event rate。
- Publication-level：实时 TUI/SSE 本身不是算法创新；若要形成研究贡献，需要把“机器人运行证据 → Agent belief/decision → safety validation → operator intervention”做成形式化、可评价的因果可解释机制，并与普通日志/最终结果 UI 做用户实验和故障定位对照。
