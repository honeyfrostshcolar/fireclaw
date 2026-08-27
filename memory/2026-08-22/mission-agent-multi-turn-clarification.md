# Mission Agent 多轮澄清与安全规划

更新时间：2026-08-22 18:22:57 +0800

## 任务目标

- 让类似“随便往前走到一个没有障碍物的地方”的模糊自然语言真正进入 LLM 规划。
- LLM 应能根据不确定性选择追问、升级/拒绝或提出计划，而不是由确定性关键词规则直接返回 422。
- 消防机器人场景保持 fail-safe：模糊目标不能被 LLM 猜成坐标；只有形成可校验的封存计划并由操作员显式确认后才能下发 Robot Gateway。
- CLI 在预期的澄清或规划错误下不应打印 traceback 并退出。

## 用户决策与约束

- 用户明确要求整体规划经过 LLM，不接受用确定性规则替代智能规划。
- 对安全关键的模糊请求，应进行多轮追问；确认各方信息无误后才执行。
- LLM 单次思考时间目标约 60 秒；CLI 当前演示超时为 75 秒。
- 导航动作本身允许约 5 分钟以上，现有导航 action timeout 维持 360 秒；不要把 LLM 推理超时和机器人导航耗时混为一谈。
- 当前没有可信地图候选点接口时，不能伪装成 Agent 已经查看地图，也不能虚构“无障碍”坐标。

## OpenClaw analogue（先检查后适配）

通过 CodeGraph 检查了以下 OpenClaw 结构：

- `openclaw/src/agents/tools/ask-user-tool.ts`
  - Agent 可选择调用 typed `ask_user`。
  - 每个 session 只允许一个 pending question。
  - Gateway 持有问题状态，操作员回答被送回同一 Agent loop。
  - timeout/cancel 采取安全结束语义。
- `openclaw/src/agents/openclaw-tools.registration.ts`
- `openclaw/src/agents/openclaw-tools.ts`

FireClaw 复用了“Agent 决策 + Gateway 持有 pending question + 回答恢复同一会话”的形状，但针对实体机器人增加：

- 回答必须绑定同一 authenticated operator。
- 最多 3 轮澄清，TTL 15 分钟，每个 operator 同时只保留一个规划对话。
- Mission Agent 的问题文本不属于操作员授权，不能被写入最终封存命令；只有原始命令与操作员回答构成授权上下文。
- 未形成有效封存计划前不得下发任何物理动作；形成计划后仍需独立的 `yes` 确认。
- 当前预览阶段不自动执行主动感知或机器人动作。

## 原因定位

原来的 `POST /plan-mission` 直接调用 `planner.plan()`，绕过了已经存在的 `LLMMissionPlanner.decide()` 和 `MissionDeliberationRuntime`。因此：

- LLM 的 `request_clarification`、`escalate`、`request_observation` 决策没有进入 CLI 交互链路。
- 规划失败以 HTTP 422 传播，CLI 未把它当作可恢复的会话结果，最终显示 traceback 并退出。
- 没有 Gateway-owned pending question，也没有安全地把 operator answer 放回同一规划上下文。

## 已修改文件与行为

### 新增规划对话状态

- `src/fireclaw_core/mission/planning_dialogue.py`
  - 新增 thread-safe `PlanningDialogueStore`。
  - TTL 900 秒、最多 3 轮、单 operator 单 active dialogue、容量和文本长度均有边界。
  - `canonical_operator_command()` 只包含原始操作员命令和 authenticated operator answers，刻意排除 Agent questions。
  - 进程重启时 pending dialogue 不恢复，按 fail-safe 失效。

### LLM 与规划上下文

- `src/fireclaw_core/mission/mission_planner.py`
  - `MissionPlannerContext` 增加 `operator_clarifications`。
- `src/fireclaw_core/mission/planning_context.py`
  - 将澄清回答加入 authoritative critical context，而不是 advisory memory。
- `src/fireclaw_core/planner/llm_planner.py`
  - 明确要求 LLM 对模糊安全目标追问或升级。
  - 没有可信地图/路径证据时禁止虚构 `(0, 0)` 或其他物理目标。
  - 已回答的问题不应重复追问。
  - navigation capability 必须从可用 robot/tool enum 复制，避免硬编码成与 profile 不一致的 `navigate`。

### Mission deliberation 与封存计划

- `src/fireclaw_core/mission/mission_deliberation.py`
  - `deliberate()` 支持 per-call proposal validators。
  - validator identity 写入 checkpoint key/state，避免恢复 checkpoint 时静默切换安全策略。
- `src/fireclaw_core/mission/mission_agent.py`
  - 增加 planning-only `deliberate_preview()`，使用 Gateway 已取得的 readiness snapshot。
  - 预览采用 target binding 与 2D navigation validators；不自动执行 active observation。
- `src/fireclaw_core/mission/mission_gateway.py`
  - `/plan-mission` 改走 Mission Agent deliberation，而非直接 `planner.plan()`。
  - 新增同一会话的 clarification continuation。
  - 对 proposed plan 再做 command binding、target grounding、2D、registry 和 readiness 校验，再封存 artifact。
  - terminal outcome 清理对话；只有 pending clarification 被保留。

### HTTP、客户端与 CLI

- `src/fireclaw_core/gateway/method_scopes.py`
  - 注册 `POST /plan-mission/clarification` 为 READ_SCOPE；它只能继续规划，不能执行动作。
- `src/fireclaw_core/mission/mission_gateway_client.py`
  - 增加 `answer_planning_clarification(session_id, answer)`。
- `src/fireclaw_core/mission/interactive.py`
  - CLI 显示 `[Mission Agent 追问 n/3]`，接收回答并继续同一规划会话。
  - 支持 cancel/quit/EOF/Ctrl-C 安全放弃。
  - 只有拿到 `preview_ready` 后才进入原有 `yes` 封存计划确认。

### 超时

- `src/fireclaw_core/mission/mission_runtime.py`
- `src/fireclaw_core/gateway/serve.py`
  - Mission deliberation host budget 默认取 provider timeout + 5 秒；当前 provider timeout 60 秒时为 65 秒，适配 CLI `--timeout 75`。
  - 此设置不改变机器人导航 action 的 360 秒执行超时。

## 新增/更新测试

- `tests/test_planning_dialogue.py`
  - operator binding、supersession、expiry、round cap、Agent question 不进入 authorization command。
- `tests/test_sealed_plan_gateway.py`
  - vague request -> clarification -> operator answer -> preview -> yes -> exact dispatch。
  - 未确认前零 dispatch；Agent 示例坐标不能污染封存授权。
  - HTTP clarification endpoint 端到端。
  - invented target 被拒绝且不产生 artifact。
- `tests/test_interactive.py`
  - CLI 多轮回答后进入 preview，不因澄清退出。
- `tests/test_mission_gateway_client.py`
  - continuation request shape。
- `tests/test_mission_planning_context.py`
  - operator clarification 为 authoritative/critical。
- `tests/test_llm_deliberation_policy.py`
  - LLM 首先虚构 `(0,0)` 时，validator 将反馈送回 LLM；下一轮可改为 clarification。
- `tests/test_serve.py`
  - 默认 Mission deliberation timeout 为 65 秒。

## 验证记录

使用 `/home/lpp/miniconda3/envs/py310/bin/python`：

- 所有相关修改模块 `py_compile` 通过。
- `git diff --check` 通过。
- 规划对话、非 HTTP gateway、interactive、deliberation、LLM policy、planning context：57 passed，1 deselected。
- LLM deliberation policy 单组：11 passed。
- sealed gateway、Mission client、method scopes：83 passed。
- plan artifact、LLM planner、Mission Agent/runtime、profiles、planner builder：143 passed。
- serve/profile：6 passed。
- sealed gateway + serve 最新回归：14 passed。
- Mission gateway/CLI/readiness/runtime identity/sealed execution/interactive/dialogue 综合回归：124 passed。

socket 测试最初在 workspace sandbox 中因 `PermissionError: Operation not permitted` 失败；使用允许 loopback socket 的 elevated test invocation 后全部通过，不是产品代码失败。

本次没有启动 Gazebo、真实机器人，也没有请求外部 LLM provider；Agent 决策测试使用 fake provider，本地 HTTP 路径使用 loopback server。

## 当前结论

- 工程上已经形成真正的 LLM 多轮澄清链路；确定性部分只负责状态边界、provenance、schema、安全验证和执行授权，不能替代 LLM 决策。
- 对“随便到一个无障碍位置”，当前诚实可用的策略是 LLM 追问明确位置，或在无法消除不确定性时升级/拒绝。
- 当前不能实现“Agent 自己看地图选择安全点”，因为 Mission Agent 尚无可信、只读、带证据的地图候选工具。让 LLM 从语言模型参数中猜坐标不安全。

## 下一步建议

如需支持自动选择无障碍点，应由 navigation plugin 增加只读原子 Tool，例如：

`suggest_safe_navigation_targets(robot_id, sector, min_distance, max_distance, min_clearance, max_candidates)`

返回 bounded candidates，每个候选至少包含：

- `map` frame 下的 `x/y/yaw`
- costmap/occupancy clearance 与 footprint collision check
- `/move_base/make_plan` 可达性与预计 path length
- `observed_at`、map revision、evidence IDs、数据新鲜度

实现应在 Robot Gateway/ROS adapter 内结合 TF、occupancy/costmap、robot footprint 和 planner service 完成；不要把整张 raw grid 直接交给 LLM。随后把 target provenance 从当前 `operator_supplied` 扩展为显式的 `operator_supplied | trusted_tool_observed`。

## 研究层面判断

- 该改动显著提高工程安全性、可审计性与 human-in-the-loop 交互正确性。
- 单独的多轮澄清机制并不足以构成高水平论文创新；更有研究价值的方向是把 uncertainty、地图证据、新鲜度、可达性与 operator confirmation 统一成可评估的 grounded deliberation protocol，并对误执行率、澄清效率、安全违规、任务成功率和通信退化做系统实验/消融。
