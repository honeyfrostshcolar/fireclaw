# 秋招演示准备：mission_agent observation 策略冲突修复

更新时间：2026-08-22 23:05 +0800

## 任务目标

为秋招面试演示验证四终端流程（Gazebo/move_base → Robot Gateway → Mission Gateway → Mission CLI）可跑通；排查演示过程中出现的报错。

## 四终端命令来源

用户此前让 codex（会话 `~/.codex/sessions/2026/08/21/rollout-2026-08-21T18-55-38-*.jsonl`，line 4384 起）生成过完整四终端命令。本次逐项核对均有效：

- `fireclaw.sim.toml` 存在且匹配（robot.id=gazebo_turtlebot3、adapter=ros1、8765/8766、mode=simulation）
- `extensions/navigation-move-base/launch/fireclaw_acceptance_world.launch` 含 Gazebo empty_world + turtlebot3_remote + map_server + amcl + move_base
- CLI 子命令 serve / mission / robot-gateway 及参数 --config/--server/--timeout 均存在
- 注意：从聊天窗口复制多行命令可能混入 `>` 等字符；曾误生成空文件 `--config`（已删除）。建议单行输入。

## 已完成（23:00 前）

- 清理残留进程：robot-gateway(598987)、roslaunch(29860) 及子进程 rosmaster/rosout；端口 8765/8766/11311 全部释放。
- 用户自行按序启动四个终端；`status` 显示 Runtime: simulation; ready，gazebo_turtlebot3 online。

## 现象与结论

### 现象 1：CLI 输入"你好"触发 `[Mission Agent 追问 1/3]`

**正常行为**，是同日 18:22 上线的 LLM 多轮澄清功能（见 [[mission-agent-multi-turn-clarification]]）。非任务语句走 request_clarification。演示时应直接下达明确任务。

### 现象 2：serve 终端 ValueError: Producer mission_agent cannot assert event type observation

**真实 bug（潜伏自 7 月 28 日），已修复。**

根因链条：

- 策略表 `MEMORY_PRODUCER_EVENT_TYPES["mission_agent"]`（src/fireclaw_core/memory/embodied_memory.py:85）只允许 {command, correction, mission, outcome, plan, subtask}；
- `_record_mission_state_snapshot()`（src/fireclaw_core/mission/mission_agent.py:710，e564a974 2026-07-28 引入）每次规划用 producer_type="mission_agent" 记录 event_type="observation"、evidence_kind="runtime_evidence" 的状态快照；
- 校验必然失败。异常被 `_record_embodied_memory` 捕获降级写普通 mission memory（force=True），故任务执行不受影响，但 embodied store 永远缺失 mission state snapshot 事件。

时间线证据：策略表建于 4af5712 (2026-07-18)，快照记录加于 e564a974 (2026-07-28)，自加入日起即矛盾。另两张表均已允许该组合（MISSION agent 允许 runtime_evidence；runtime_evidence 可描述 observation），唯独事件类型表遗漏——属遗漏而非设计意图。

## 已修改文件

- `src/fireclaw_core/memory/embodied_memory.py`：`MEMORY_PRODUCER_EVENT_TYPES["mission_agent"]` 增加 `"observation"`（一行修复）。

## 验证记录（/home/lpp/miniconda3/envs/py310/bin/python）

- `tests/test_embodied_memory.py`：10 passed
- `tests/test_planner_memory_context.py tests/test_relation_context.py tests/test_entity_memory.py`：95 passed
- `tests/test_sealed_plan_gateway.py tests/test_mission_gateway_client.py`：43 passed
- 无测试直接断言 MEMORY_PRODUCER_EVENT_TYPES 内容，无表枚举锁死风险。

## 下一步

- 用户需重启终端 3 的 `python -m fireclaw_core serve`（旧进程加载的是修复前代码），重启后重试规划，serve 终端不应再出现该 traceback。
- 若继续演示中暴露新问题，追加到本记录并带时间戳。

---

## 追加（2026-08-23 00:10 +0800）：六项演示问题诊断与修复

用户实测四终端流程后发现 6 个问题，全部完成根因定位，5 项已修复：

### 根因结论（基于 data/mission/*.jsonl 证据链）

1. **澄清重复自我介绍/重答**：deliberation 是无状态"system+单条 JSON"调用；问答历史其实已在 `operator_clarifications` payload 中（含 question+answer），但系统提示词未告知模型这是延续轮 → 模型每轮重新开场。属提示词缺陷。
2. 同上。
3. **3 轮上限**：`DEFAULT_MAX_CLARIFICATION_ROUNDS = 3`（planning_dialogue.py:12），构造时可配 1-10 但无配置接线。与 OpenClaw 的差异是刻意设计（安全规划对话 vs 开放聊天），已改为可配置。
4. **单轮澄清后直接 escalate**：系统提示词明确要求"无地图证据时必须 clarification 或 escalate"，LLM 在第 2 轮选择升级——诚实 fail-safe，符合设计哲学（memory 记录 8/22 18:22 有该决策）。非 bug。
5. **导航成功却 blocked**：两层原因。(a) 完成度契约 `target_pose_confirmed` 要求输出含嵌套 `pose` 字典；(b) 关键：`Ros1MoveBaseBackend.navigate_to_point` 输出的 x/y/yaw 是**指令回显不是实测位姿**（move_base.py:655-657）。若直接放宽校验器会形成"自证到达"循环论证，违背项目证据哲学。正确修法是回读 TF 实测位姿。
6. **(0,0) 规划失败且持续**：坐标绑定正则 `_COMMAND_COORDINATE_RE` 要求完整括号 `[（(]num[,，]num[)）]`。用户第一次输入"0，0"无括号、第二次"（0，0目标位置"缺右括号（data/mission/memory.jsonl 存储的原始命令证实）→ 绑定失败 → validator 反馈 → LLM 只会提 (0,0) → 4 轮耗尽 → time/iteration limit。后续其他命令正常，非持久故障。另发现 15:11/15:15 UTC 用户补测的两条命令均成功。

### 已实施修复

- `extensions/navigation-move-base/plugin/move_base.py`：新增 `_measured_pose()`（tf2_ros 懒加载 Buffer/TransformListener 缓存，lookup map→base_footprint，超时 1.5s），goal reached 时结果附加实测 `"pose"` 字段；TF 无数据则不加（fail-honest）。新增类属性 pose_map_frame/pose_base_frame/pose_lookup_timeout_seconds。
- `src/fireclaw_core/planner/llm_planner.py` build_deliberation_system_prompt 新增 3 行引导：延续轮不重新自我介绍；操作员可补充信息时优先 clarification 而非 escalate；坐标绑定失败时要求操作员用带括号格式重述而非反复提案。
- `src/fireclaw_core/mission/mission_gateway.py`：(a) MissionGatewayConfig 增加 planning_dialogue_max_rounds/planning_dialogue_ttl_seconds，__init__ 用其构造 PlanningDialogueStore；(b) deliberation 失败响应把 validation_errors 前 3 条附加到 message 并带回 validation_errors 字段，操作员能看见"坐标未绑定"的具体原因。
- 配置接线：gateway/config.py 扁平化 [mission].planning_dialogue_max_rounds/ttl_seconds → mission_cli.py serve 传参 → serve.py run_server_blocking/start_server → MissionGatewayConfig。
- `fireclaw.sim.toml` [mission] 设 planning_dialogue_max_rounds = 5。

### 验证

- tests/test_move_base_navigation_plugin.py：18 passed（含 3 个新测试：成功附实测 pose、TF 无数据不含 pose、失败不含 pose）
- test_planning_dialogue/test_sealed_plan_gateway/test_serve/test_llm_deliberation_policy/test_mission_gateway_client：64 passed
- test_interactive/test_mission_runtime/test_mission_agent/test_plan_artifact/test_cli：127 passed
- load_config 验证 sim.toml 读出 max_rounds=5

### 未做/待办

- environment_beliefs/facts 为空的 grounding 缺口仍在：地图/costmap 候选点只读 Tool（suggest_safe_navigation_targets）尚未实现（见 8/22 18:22 记录的下一步建议），"随便找个无障碍点"仍会被诚实拒绝。
- 用户需重启 Mission Gateway 与 Robot Gateway（plugin 代码变更需重启 robot-gateway 进程才生效；插件由 gateway 加载）。
- 若演示前时间允许：给 sealed-plan blocked 路径补一条端到端回归测试。

---

## 追加（2026-08-23 01:05 +0800）：导航失败不报原因问题

### 现象

(0,0) 导航物理失败（move_base ERROR: 找不到可行路径，恢复行为耗尽，error_code=move_base_aborted），但 CLI 只显示"已阻塞（blocked）"无任何原因；LLM 最终报告也写"任务失败原因未指定"。对照：同会话 (0.63,0.54) 导航 succeeded——证明实测位姿修复生效。

### 根因

机器人侧原因存在于 subtask_results 的 step output 中，但 execute_sealed_plan 响应只带调度器级笼统消息（"Mission stopped by failure policy..."），未提取物理原因；CLI 终态只打印状态标签。内部状态投影不一致（dispatch 节点记录=failed，terminal_transition=failed，运行终态事件=mission.blocked）——run manager 状态在内存中已随重启丢失，具体是哪个分支把 blocked 标签置于 failed 之上未能完全钉死（_aborted_group_terminal_status 优先级元组 blocked 先于 failed 是嫌疑点），但修复不依赖该结论：无论标签如何，原因现在都会透出。

### 已实施修复

- mission_agent.py：新增 `_sealed_plan_failure_reasons()`（从终态 subtask 的 execution steps output 提取 error_code/goal_status_text，上限 5 条）与 `_sealed_failure_message()`；execute_sealed_plan 非 success 时 message 附"机器人侧原因：…"，response 增加 failure_reasons 字段。
- interactive.py：新增 `_print_terminal_reason()`；非成功终态后打印 `[原因] …`（优先 failure_reasons，退回 message，再退回 final_report.needs_attention）。
- serve.py：MissionDeliberationLimits max_observations 3→6（追问"发生什么了"类查询合法多次 inspect 撞了旧上限）。

### 验证

- test_mission_agent.py：80 passed（含新增 move_base_aborted 提取测试）
- test_interactive/test_sealed_plan_gateway/test_serve：26 passed
- 待办：用户重启 serve 后复测 (0,0) 应显示 [原因] …move_base_aborted…；(0,0) 本身在当前地图对 move_base 不可行属正常物理事实，演示应避开。

---

## 追加（2026-08-23 01:35 +0800）：CLI 交互修复（readline）

用户反馈：中文删除错位出空格、无法全删、上下键无历史。根因：interactive.py 三处裸 input()，无 readline。

OpenClaw analogue：OpenClaw 终端交互的 readline 式行编辑 + 持久历史。复用该形态，未引入 TUI/补全（超出演示需要）。

修复：新增 src/fireclaw_core/mission/console_input.py（ConsoleInput：懒加载 readline、历史持久化到 data/mission/.cli-history、上限 500、无 readline 平台优雅降级）；interactive.py 三处 input() 改走 _prompt()，quit/EOF 时保存历史。

验证：tests/test_console_input.py（3 新测试）+ test_interactive.py 共 15 passed。需重开 CLI 终端生效。

## 追加（2026-08-23 02:40）：[原因] 不显示的真凶

`_mission_run_status_projection`（mission_gateway.py:2850）只放行控制面字段，剥掉了 result.failure_reasons/message。已修复：投影透传 failure_reasons(≤5)/message(≤500)。另：首次 422 为 mimo provider 60s 超时（瞬时），重试即成功。(0,0) 再次 blocked 属真实物理失败（move_base_aborted，地图上不可达）。需重启 serve。
