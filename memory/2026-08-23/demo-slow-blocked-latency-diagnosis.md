# 2026-08-23 演示调试：blocked 前长时间无进度的根因诊断

## 任务目标
用户反馈：封存计划确认后（"[事件] 封存计划已由操作员确认"之后）过了很久才有下一步动静，
问"这个数据流怎么过的？这么慢？"。目标：定位耗时归属并解释数据流。

## 取证命令与关键数据
- `grep <mission_id> data/mission/{missions.dispatch,missions,tasks,flows}.jsonl`
- `grep <mission_id> data/robots/gazebo_turtlebot3/tasks.jsonl` （机器人侧生命周期）
- 关键：`data/robots/gazebo_turtlebot3/tasks.jsonl.agent-loops.jsonl` 记录了
  每次 agent-loop run 的 started_at/updated_at/attempts[].duration_ms/operation。

mission-1daaf4b845ac4901b17959638a012ee3（导航到 (0,0)，2026-08-23T02:31Z 开始）：
- 02:31:19.384 操作员 y 确认；02:31:19.572 下发；02:31:20.833 机器人 accepted
  → 管线（Mission Gateway → Robot Gateway → 插件）仅 ~1.3 秒，不慢。
- 机器人侧 agent-loop：
  - 第 1 轮 run：navigate_to_point 29.3s → status=escalated, reason=safety_gate_terminal
  - 自动重开第 2 轮 run（02:31:51，共 196.7s）：it1 navigate 71.2s → it2
    move_base_clear_costmaps 8.6s → it3 navigation_diagnostics 16.3s → it4 navigate
    87.1s → status=failed reason=policy_error（02:35:07.8）
  - 能力策略执行判定 evaluated_at=02:34:11 落在 it4 窗口内（一致）
- 02:35:13.4 任务级完成契约评估 + LLM 最终报告（generated_by=llm）→ blocked

## 结论
1. 慢的不是数据流/管线，是机器人侧自主执行-恢复循环的真实工作量：
   (0,0) 不可达 → 每次 navigate 等 move_base 规划重试+恢复行为跑满（70~90s/次），
   agent 又自主调恢复工具并二次重试，加上每轮迭代的 LLM 决策调用，合计 ~226s。
2. 感觉"卡住"的原因是可观测性缺口：循环的 7 次真实迭代没有以 action.feedback
   进度事件转发，CLI 只看到 accepted 后长期沉默（CLI 的 [进度] 显示通道已支持）。
## 实施与验证（2026-08-23 02:56 +0800 更新）

### 实施内容
1. `src/fireclaw_core/mission/mission_run.py`:
   - `MissionRunControl` 新增 `set_event_sink` 与 `emit_event(event_type, payload)` 支持；
   - `MissionRunManager._execute` 在 run 启动时绑定 `run.control.set_event_sink(...)` 并在 finally 中释放。
2. `src/fireclaw_core/mission/mission_scheduler.py`:
   - 在 `MissionScheduler._poll_group_terminal` 轮询循环中维护 `seen_robot_event_ids: set[str]`；
   - 新增 `_relay_robot_progress_events(...)`，将 Robot Trace 中的 `robot_agent.decision`、`skill.started`、`robot_agent.observation`、`skill.failed` 和有效 `action.feedback` 转换为结构化中文进度事件，调用 `run_control.emit_event("action.feedback", ...)` 向上发布到 Mission Gateway EventBus。
   - 过滤高频无意义 `move_base feedback` 心跳，避免无意义刷屏。
3. `src/fireclaw_core/mission/interactive.py`:
   - `display_event` 支持从 payload 中直接提取 `robot_id` 和 `task_id`，正确显示 `[gazebo_turtlebot3]` 前缀。
4. 测试扩展：
   - `tests/test_mission_run.py`: 新增 `test_mission_run_relays_progress_events`；
   - `tests/test_mission_scheduler.py`: 新增 `test_scheduler_relays_robot_progress_events`（含事件去重校验）。

### 验证结果（/home/lpp/miniconda3/envs/py310/bin/python）
- `tests/test_mission_run.py tests/test_mission_scheduler.py tests/test_interactive.py`: 40 passed in 0.73s
- `tests/test_mission_gateway.py tests/test_sealed_plan_gateway.py tests/test_mission_agent.py tests/test_interactive.py tests/test_console_input.py tests/test_serve.py`: 160 passed in 26.15s
- 进程重启建议：用户需重启终端 3 的 `python -m fireclaw_core serve` 使新代码生效。

## 机器人实时位姿全链路感知与自动锚定实施（2026-08-23 15:05 +0800 更新）

### 动机
用户要求：
1. Robot Agent 能读取到 ROS 机器人的实时位姿与状态；
2. Mission Agent 能通过状态快照读取到机器人当前的实时位姿；
3. 当用户说“先去(0.63, 0.54)，然后再返回现在的位置”或“你读取一下当前位置”时，系统能自动识别当前位置（如 (-2.0, -0.5)）并顺利完成连续导航规划与执行。

### 实施内容
1. `src/fireclaw_core/agent/robot.py`:
   - `RobotState` 数据类新增 `pose: dict[str, Any] | None = None`；
   - `Ros1RobotAdapter` 增加 `_lookup_pose()`，通过 TF2 实时读取 `map -> base_footprint` 变换；
   - `DryRunRobotAdapter` 和 `MockRos1RobotAdapter` 增加 `initial_pose` 支持。
2. `extensions/navigation-move-base/plugin/move_base.py`:
   - `InMemoryMoveBaseBackend` 和 `Ros1MoveBaseBackend` 的 `get_status()` 返回值中包含 `current_pose`；
   - 新增只读 Agent Tool `navigation_get_pose`（`move_base_get_pose`）。
3. `src/fireclaw_core/mission/mission_state.py`:
   - `MissionRobotState` 新增 `pose` 字段并在 `to_dict()` 中输出；
   - `MissionStateSnapshotBuilder._robot_state` 解析并透传 `pose`，使 Mission Agent 在 `inspect_mission_state(kind="robot_state" 或 "fleet_state")` 时可直接读取到实测坐标。
4. `src/fireclaw_core/mission/plan_artifact.py`:
   - `validate_plan_target_binding` 增强：当命令中包含相对代词（“返回现在的位置”、“起点”、“原位”等）且子任务坐标匹配快照中的机器人实测位姿证据（`evidence_points`）时，判定为合法权威锚定（Authoritative Grounding），通过校验。
5. `src/fireclaw_core/planner/llm_planner.py`:
   - `build_deliberation_system_prompt` 增强：明确指引 Planner 在遇到“返回现在的位置/起点”时，可通过 `inspect_mission_state` 查看实测位姿 `pose`，将其作为合法的导航目标节点或在追问中向操作员展示。
6. 测试扩展：
   - `tests/test_mission_state.py`: 新增 `test_snapshot_preserves_robot_pose`；
   - `tests/test_llm_deliberation_policy.py`: 新增 `test_validate_plan_target_binding_allows_relative_keywords_with_pose_evidence`。

### 验证结果
- `tests/test_mission_state.py tests/test_llm_deliberation_policy.py tests/test_planning_dialogue.py tests/test_sealed_plan_gateway.py tests/test_mission_gateway.py tests/test_interactive.py tests/test_mission_run.py tests/test_mission_scheduler.py tests/test_serve.py`: **129 passed in 26.41s** 全部通过。

## TUI 实时动态反馈与思考过程流式指示器（2026-08-23 18:00 +0800 更新）

### 动机
用户反馈在执行自然语言任务或回答追问时，控制台由于等待远端 LLM 推理（10~20s）会出现“长时间完全静默无输出”的卡顿感，希望像 OpenClaw 一样能够实时看到 Agent 的工作与思考动态。

### 实施内容
1. `src/fireclaw_core/mission/interactive.py`:
   - 引入非阻塞线程安全的 `LiveActivityIndicator`（动态旋转 Spinner + 实时秒数计时 + 多阶段任务状态推演）；
   - 在 `preview_mission`、`answer_planning_clarification`、`confirm_plan` 等耗时等待中包裹指示器，屏幕实时刷新：
     - `⠋ 🤖 Mission Agent 正在解析任务意图... (1.2s)`
     - `⠙ 🔍 正在连接机器人并探查实测位姿与状态... (3.4s)`
     - `⠹ 🧠 正在调用 LLM 进行因果绑定与任务图规划... (7.8s)`
     - `⠸ 🛡️ 正在进行确定性物理安全门禁校验... (12.1s)`
   - 响应到达后平滑擦除 Spinner 并无缝输出结构化 `[Mission Agent 思考轨迹]` 和 `📋 任务预览`。
2. 验证结果：全量测试全部通过。

## 计划封存就绪哈希伪漂移修复与断线无缝重新接入（2026-08-23 22:00 +0800 更新）

### 1. HTTP 409 `plan_readiness_drift` 伪漂移根因与修复
- **现象**：多步巡检或经过追问澄清的复杂任务，在操作员确认执行（`y`）时触发 `HTTP 409: Robot or admission readiness changed after preview; create a new preview`。
- **根因**：`plan_artifact.py` 中 `build_readiness_binding` 在计算就绪指纹哈希时，从机器人端拉取的 `/state` 探针中包含了毫秒级高频变化的动态时间戳（`state.robot_state.timestamp`），导致预览时刻与确认时刻计算的哈希发生伪漂移。
- **修复**：在 `plan_artifact.py` 中纯化就绪快照，仅保留稳定的语义就绪属性（`status: online`、`declared_capabilities`、`freshness: fresh`），彻底剔除易变高频遥测。

### 2. TUI 列宽自适应与单行原地刷新防换行
- **现象**：当机器人反馈的进度字符串较长时，终端发生自动折行，`\r` 只能回到第二行行首，导致每次旋转帧都在新行刷屏。
- **修复**：在 `LiveExecutionMonitor` 与 `LiveActivityIndicator` 中加入 50 字符安全宽度截断保护，单行原地擦除与旋转。

### 3. 断开观察与断线重连（`follow` / `cancel` 命令）
- **现象**：操作员按 `Ctrl+C` 断开控制台本地观察后，任务在远端机器人上自主执行，但操作员不知道如何重新接入监控。
- **修复**：
  - 在 CLI 中记忆 `last_mission_id`；
  - 新增 `follow [mission_id]` 命令（可省略 ID 自动接入最近活跃任务），基于 SSE 游标断点续传重新流式监听；
  - `status` 命令输出最近活跃任务；`cancel` 支持省略 ID 一键取消当前活跃任务；
  - 按 `Ctrl+C` 中断时给出友好引导提示：`💡 提示：输入 follow 继续追踪该任务进度，或输入 cancel 取消该任务`。
