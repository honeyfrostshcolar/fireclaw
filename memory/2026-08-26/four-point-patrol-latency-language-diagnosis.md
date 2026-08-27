# 四点连续巡检耗时、语言漂移与可观测性诊断

## 记录时间

- 2026-08-26T11:51:56+08:00
- 本轮仅诊断；用户明确要求“先不要做修改”。除本诊断记录外，未修改产品代码、配置或运行数据。

## 任务目标

对四点连续巡检 Mission 的真实运行记录做只读取证，回答：

1. Mission 规划 63.5 秒和澄清后 26.2 秒分别花在哪里，是否为后端异常；
2. 第一个 Robot 子任务中文报告、后三个英文报告的原因；
3. `规划并尝试前往目标点` 到 `正在前往 map 坐标系中的目标点` 之间为何长时间无输出；
4. 检查同一日志中其他明显异常，但不实施修复。

## 运行标识

- Mission: `mission-7ecc072ed1a8434998938573db587db1`
- Planning preview: `preview-2e5b7c8df4424c249e3ae9c1c292db70`
- Robot: `gazebo_turtlebot3`
- Robot tasks:
  - `task-6f8763d8cdb44a8694493d33318713fe` / `patrol_point_1`
  - `task-9ce8b2ba90224e2e89ae9617561cd5c6` / `patrol_point_2`
  - `task-a021d565189d40a1bd4e315d0073d7c0` / `patrol_point_3`
  - `task-e222ddf5d63d48cdbaaf7c42cae6086f` / `return_to_origin`

## 当前进展

- 已从 Mission/Robot JSONL、Agent loop checkpoint、Robot event ledger 和 embodied memory 还原完整时间线。
- 已用 CodeGraph 核对 Mission planning、sealed-plan authorization relay、Robot deliberation、SafetyGate、memory hot path、TUI relay 与语言生成链路。
- 已执行只读 JSONL 扫描基准；未运行物理动作，未启动 Gateway，未改代码。

## 已完成

### 1. Mission 规划耗时拆分

首次规划的 bounded Mission loop：

- runtime: `03:09:53.806669Z` -> `03:10:50.212436Z`
- loop elapsed: `56.4055s`
- iteration 1 `inspect_state`: `15.7718s`
- iteration 2 `propose_plan`: `27.0628s`，被 `invalid_plan_proposal` 拒绝
- iteration 3 `request_clarification`: `13.5418s`
- 三次模型决策相加约 `56.38s`，因此 56.4 秒几乎全部为模型/Harness 回合，不是 ROS 或导航后端。
- TUI 总耗时为 63.5 秒，比 loop 多约 `7.1s`。这段在 bounded loop 之前，属于 FireClaw 同步前置链：readiness、Robot `/state`、Mission snapshot/memory 写入及 planner memory context 构建。当前日志没有更细的子阶段 timer，不能把 7.1 秒无歧义地分到某一个函数。

澄清后规划：

- runtime: `03:11:37.247970Z` -> `03:11:56.363807Z`
- loop elapsed: `19.1110s`
- 唯一 `propose_plan` 模型回合: `19.1004s`
- TUI 总耗时 26.2 秒，仍比 loop 多约 `7.1s`，证明前置开销可重复存在。

结论：63.5 秒不能整体算作后端卡顿；其中约 56.4 秒是三次模型调用，约 7.1 秒是后端前置链。模型实测并非“最多 10 秒”，本次 Mission 回合为 13.5--27.1 秒。

### 2. `返回现在的位置` 被错误追问

- `validate_plan_target_binding(plan, state_snapshot=None)` 本身支持相对位置关键词与 live pose 绑定。
- 但 `MissionAgent.deliberate_preview()` 把 `validate_plan_target_binding` 作为普通单参数 proposal validator 传入。
- `MissionDeliberationRuntime._validate_plan_proposal()` 实际调用 `validator(accepted_plan)`，没有传入 frozen `state_snapshot`，也没有把 `inspect_state` 返回的 pose observation 合并进验证证据。
- 因而模型即使查询到 `(-1.945687, -0.501886, yaw=0.00172)` 并把它用于 `return_to_origin`，校验器仍以 `state_snapshot=None` 执行，必然报 “not bound to an explicit coordinate”。
- 单元测试只覆盖了直接显式传入 `state_snapshot` 的正例，没有覆盖 `deliberate_preview -> proposal validator` 的集成正例。

结论：这次澄清不是必要的安全确认，而是 validator 接线缺口。它额外消耗了 `13.54s` 的澄清模型回合、一次人工往返和澄清后的 `19.10s` 模型回合。

### 3. 四个 Robot task 的真实时间线

| 子任务 | task 总时长 | 首次决策后到首次 SafetyGate | 授权后第二次决策到 SafetyGate | TUI 首次“规划并尝试”到 `skill.started` | 实际 `move_base` 动作 |
|---|---:|---:|---:|---:|---:|
| point 1 | 166.947s | 44.748s | 46.934s | 111.834s | 21.692s |
| point 2 | 175.190s | 50.542s | 51.593s | 119.118s | 22.691s |
| point 3 | 189.946s | 54.464s | 55.972s | 132.201s | 20.501s |
| return | 199.088s | 57.523s | 61.512s | 140.961s | 21.248s |

每个子任务的实际流程均为：

1. Robot Agent 第一次 LLM 决策选择 `navigate_to_point`（约 9--13 秒）；
2. 同步读取 Robot/environment state，并在物理执行热路径写完整 memory snapshot；
3. SafetyGate 因无 exact `ExecutionAuthorization` 返回 `require_confirmation`；
4. 第一个 Robot Agent run 以 `safety_gate_terminal` / `awaiting_confirmation` 结束；
5. sealed-plan scheduler 轮询到 pending 状态，再调用 Robot Gateway `/confirm`；批准本身约 2.2--3.5 秒；
6. Robot task 重新启动，Robot Agent 再做一次相同技能的 LLM 决策（约 6.8--11.4 秒）；
7. 再写一次完整 snapshot，SafetyGate 此次 `allow`；
8. `skill.started` 后约 0.06 秒进入 `action.started`，真正执行 `move_base` 约 21--23 秒；
9. 动作成功后，Robot Agent 再做一次 `complete_robot_task` LLM 回合（约 12.4--17.4 秒）才形成终态报告。

结论：ROS navigation 本身正常且稳定。用户指出的长空窗发生在 `action.started` 之前，是后端同步准备、授权恢复和重复 Robot Agent 回合，不是机器人在慢速规划路径或移动。

### 4. Memory 热路径根因

相关调用路径：

`Gateway execute_skill -> FireClawAgent.execute_deliberated_step -> _record_robot_snapshot -> RobotMemoryRecorder.record_snapshot -> EmbodiedMemoryStore.append_event -> SafetyGate.evaluate_with_memory_event -> add_relation`

本 Mission 的 Robot embodied memory 增量：

- 总记录 `1,072` 条，正好每个子任务约 `268` 条；
- 8 次 snapshot（每个子任务因授权恢复写两次）；
- 504 条 sensor discovery observation，即每次 snapshot 63 条；
- 每次 63 条中，61 条是 `sensor=unknown,status=rejected` 的无匹配 ROS topic，只有 1 条 IMU 和 1 条 lidar 为 verified；
- 另有 8 body state、8 environment observation、8 safety decision、12 skill invocation 和 532 relation。

同步放大机制：

- 每个 event append 都通过 `_assert_record_id_unused()` 全量解析 JSONL；
- 每个 observation 即使没有 `payload.entities`，仍运行 entity extraction，并通过 `_existing_extraction_keys()` 再全量扫描 JSONL；
- 每个 safety decision 对约 65 条证据逐条写 `supports` relation；
- 每条 relation 同时执行一次全量 ID 扫描和一次 `list_events()` 全量端点扫描；
- SQLite projection 对每条 event 默认单独 `commit()`。

当前文件：

- `data/robots/gazebo_turtlebot3/embodied-memory.jsonl`: 9,112 行，7,945,530 bytes。
- 只读 5 次基准：
  - `list_records`: mean `0.159312s`
  - mission-scoped empty `entity_mention` query: mean `0.161640s`
  - `_assert_record_id_unused` with unused diagnostic id: mean `0.139484s`

一次 snapshot 约触发 260 次全量 JSONL 扫描，再加逐条 SQLite commit。仅按当前扫描均值估算就达到约 36--42 秒，加入 SQLite、ROS state 和 Python 序列化后，与实测 45--61 秒高度一致。由于每个 task 又新增约 268 条记录，延迟随任务顺序增长，解释了 point 1 到 return 的单调恶化。

结论：这是明确的后端性能缺陷，属于 physical execution critical path 上的 O(findings × memory_size) / 近似二次放大，不是正常 LLM 等待。

### 5. Sealed-plan 授权重复执行

- `MissionAgent.execute_sealed_plan()` 传入 `relay_confirmed_plan_authorizations=True`。
- `MissionScheduler._relay_confirmed_plan_authorizations()` 只有看到 Robot task 已处于 `awaiting_confirmation` 才调用 `confirm_task()`。
- Robot Gateway 在 `/confirm` 时才依据 pending request 签发 exact one-shot `ExecutionAuthorization`，将授权放入 structured task 并重新启动 worker。
- 所以当前实现是“先让 Robot Agent 运行到 SafetyGate 拒绝，再自动批准并重跑”，而不是由已确认的 sealed plan 在首次 Robot run 前提供 plan-bound authorization。
- `tests/test_mission_scheduler.py::test_confirmed_sealed_plan_relays_exact_robot_task_authorization` 明确固化了这种 reactive relay，但没有覆盖真实 LLM + large-memory 条件下的时延代价。

结论：安全边界本身是有意的，自动审批没有越权；但授权交接时机导致每个子任务重复一次 LLM 和整套昂贵 snapshot，是严重的架构/性能问题。

### 6. TUI 为何看不到中间发生了什么

- `MissionScheduler._relay_robot_progress_events()` 只把部分 Robot events 投影为 operator stream：`robot_agent.decision`、`skill.started`、失败/feedback、`ros.log`。
- 它不转发 `robot_agent.step_planned`、`safety.decided`、`authorization.requested`、`task.awaiting_confirmation`、`authorization.approved`、`task.resume_started` 等关键阶段。
- `MissionProgressView` 又按 `(task, plan step, iteration, tool, message)` 去重连续 `action.feedback`。授权恢复后的第二次 Robot Agent 决策与第一次完全相同，因此被去重。
- 所以屏幕只留下第一次“规划并尝试前往目标点”和很久后的 `skill.started` “正在前往...”，中间约 112--141 秒被完全隐藏。

结论：这是可观测性缺陷；终端展示没有反映真实状态机，用户合理地会判断为后端卡死。

### 7. 中英文完成报告漂移

- 四条终态文字来自 Robot Agent 动作成功后的第二个 LLM 回合，模型调用 `complete_robot_task(message=...)`。
- `LLMRobotAgentDecisionPolicy` 的 system prompt 虽为中文，但没有规定输出语言；`decision_rules` 与 `complete_robot_task` schema/description 均为英文。
- `message` 字段没有 locale contract，也没有中文校验、翻译或 deterministic renderer；代码直接 `str(call.arguments["message"])` 原样保留。
- Mission Scheduler 与 TUI 又原样转发这个 message。
- 当前测试只验证 tool 名与单次 operation，不验证 `message` 的语言。

结论：第一条中文只是模型采样结果，后三条英文也是当前契约允许的结果；这是语言契约缺失，不是终端编码问题。

### 8. Mission 最终报告额外时延

- 最后 Robot task 完成约 `03:24:42.800Z`，dispatch checkpoint 于 `03:24:48.041Z` 完成。
- final report 的 `generated_at` 为 `03:24:50.415Z`，Mission record 更新并发出 report-ready 于 `03:25:15.790Z`。
- `generated_by=llm`，因此末尾还有约 25.4 秒只读 LLM 报告调用；它不影响已完成的物理动作，但影响用户看到最终 Mission 完成状态的时间。

## 当前问题与优先级

1. P0：Robot memory snapshot 和 relation 写入位于物理动作 critical path，发生数百次全量 JSONL 扫描与逐条 commit；延迟会随历史增长。
2. P0：sealed-plan confirmation 到 Robot exact authorization 的交接是 reactive，导致每个子任务先失败再重跑，放大 LLM 与 memory 开销。
3. P1：关键 SafetyGate/authorization/resume/memory 阶段未向 TUI 投影，且重复决策被内容去重，形成 112--141 秒假死窗口。
4. P1：relative-pose validator 在 preview 集成链路没有收到 state snapshot，造成不必要澄清。
5. P1：Robot terminal message 无 locale/language contract，导致中英文随机漂移。
6. P2：Mission final report 的 LLM 调用约 25 秒，当前把 run 保持在 reporting 状态；可作为 UX 延迟单独评估。

## 研究与评测影响

- 当前端到端 Mission latency 不能用于评价 planner 或 navigation 方法：后端 memory I/O 和重复授权占据主导，并随历史数据规模变化。
- 在修复前，连续任务实验存在明显的顺序偏差；后执行的点天然更慢，不能把差异解释为路径难度或模型推理复杂度。
- 应分别记录 `LLM decision latency`、`state acquisition`、`memory persistence`、`safety evaluation`、`authorization wait`、`action runtime`、`terminal reporting`，否则工程瓶颈会污染研究结论。

## 文件检查

- `src/fireclaw_core/mission/mission_gateway.py`
- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/mission/mission_deliberation.py`
- `src/fireclaw_core/mission/plan_artifact.py`
- `src/fireclaw_core/mission/mission_scheduler.py`
- `src/fireclaw_core/mission/mission_run.py`
- `src/fireclaw_core/mission/mission_report.py`
- `src/fireclaw_core/mission/interactive.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/agent/agent.py`
- `src/fireclaw_core/agent/robot.py`
- `src/fireclaw_core/agent/robot_deliberation.py`
- `src/fireclaw_core/safety/safety.py`
- `src/fireclaw_core/memory/robot_memory.py`
- `src/fireclaw_core/memory/embodied_memory.py`
- `src/fireclaw_core/memory/entity_extraction.py`
- `src/fireclaw_core/memory/memory_index.py`
- `src/fireclaw_core/ros/ros1_sensor_discovery.py`
- `src/fireclaw_core/devtools/fleet_doctor.py`
- `tests/test_plan_artifact.py`
- `tests/test_llm_deliberation_policy.py`
- `tests/test_mission_scheduler.py`
- `tests/test_robot_agent_deliberation.py`
- `tests/test_robot_agent_fireclaw_agent_execution.py`

## 数据检查

- `data/mission/missions.jsonl`
- `data/mission/missions.dispatch.jsonl`
- `data/mission/missions.jsonl.agent-loops.jsonl`
- `data/mission/tasks.jsonl`
- `data/mission/memory.jsonl`
- `data/robots/gazebo_turtlebot3/tasks.jsonl`
- `data/robots/gazebo_turtlebot3/events.jsonl`
- `data/robots/gazebo_turtlebot3/tasks.jsonl.agent-loops.jsonl`
- `data/robots/gazebo_turtlebot3/embodied-memory.jsonl`
- `data/robots/gazebo_turtlebot3/embodied-memory-index.sqlite3`

## 已运行命令（概括）

- 列出最近两日 `memory/` 记录并读取相关记录。
- `git status --short --branch`
- CodeGraph explore：planning/readiness、relative pose validator、sealed-plan authorization、Robot deliberation、TUI relay、memory/SafetyGate 调用链、final report。
- 使用只读 Python 脚本解析 Mission/Robot JSONL，按 task 重建事件时间线和 memory 记录分类。
- 使用只读 Python 基准测试当前 JSONL 的 `list_records`、`list_events` 与 unused-id 检查。
- 使用 `rg` 检查语言、relative-pose、authorization 和 performance 测试覆盖。

## 下一步

用户当前要求不修改，因此没有实施修复。若用户确认进入修改阶段，建议按 P0 -> P1 顺序，先建立可复现性能测试与阶段计时，再分别处理 memory batching/index lookup、plan-bound authorization handoff、TUI 状态投影、relative-pose validator 接线与中文 locale contract。不要在同一个大改动中混合这些独立问题。

## 需要运行的命令

当前诊断已完成，无必须运行的命令。进入修复阶段前建议先运行针对性基线测试并保存耗时基线；具体命令应在确定首个修复范围后给出。

## 2026-08-26T12:40:05+08:00 P0 memory 热路径修复

用户确认实施 P0：消除 physical execution critical path 中对
`embodied-memory.jsonl` 的逐 event 全量扫描。

### 实施内容

- `src/fireclaw_core/mission/mission_memory.py`
  - 保留 JSONL 为 authoritative audit log。
  - 新增按 `stat` snapshot token 失效的进程内 record projection：首次使用某个 authority 版本时扫描一次，之后 `has_record_id()`、`get_record()` 和 `list_records()` 不再逐次解析 JSONL。
  - 同一进程 append 会 O(1) 更新 projection；检测到其他进程改变文件时自动按新 token 重载。
  - `purge_mission()` 会主动失效 projection。
  - JSONL 缺失/损坏行、过滤语义和重启后从 authority 重载语义保持不变。
- `src/fireclaw_core/memory/embodied_memory.py`
  - duplicate record ID 校验改为 `has_record_id()`。
  - relation endpoint 校验改为按 ID 取两个记录，不再构造全量 `list_events()`。
- `src/fireclaw_core/memory/entity_extraction.py` 与
  `src/fireclaw_core/memory/robot_memory.py`
  - 没有结构化 `payload.entities` 的 sensor/state observation 不再触发历史 entity 扫描。
  - 有实体的 observation 仍执行原有 extraction、去重、mention relation 和错误报告逻辑。
- 新增测试覆盖：缓存命中、外部 append 失效、relation 无全量列表扫描、无实体 observation 短路。

### 验证结果

- focused memory/safety/entity/relation tests：`78 passed`。
- 沙箱内全量测试受本地 loopback socket 权限限制，首个失败为
  `PermissionError: [Errno 1] Operation not permitted`，不是断言失败。
- 沙箱外全量回归：`2470 passed, 8 skipped in 317.81s`。
- 用当前约 9,112 行、7.6 MB embodied memory 的只读副本基准：
  - 首次 cache load：约 `0.123s`；
  - 后续 300 次 duplicate checks：约 `0.0015s`；
  - 后续 300 次 endpoint lookups：约 `0.0014s`。
- 模拟一次 65 条证据 snapshot 加 65 条 safety relations：约 `0.75s`，不再出现原先约 45--61 秒的 JSONL 扫描级开销。

### 剩余问题

- 这次只处理 memory persistence 的 P0；sealed-plan 授权后 Robot Agent 重跑仍会造成第二套 snapshot，这是独立的 P0/P1 架构问题，尚未修改。
- 每条 SQLite projection 仍默认单独 commit；当前 benchmark 已低于秒级，但后续可以在不改变 JSONL 审计语义的前提下增加 snapshot/task 级 batch commit。
- 进程内 projection 会随 authority 文件记录数增加而占用内存；如果未来 memory 达到更大规模，应进一步改为轻量 ID/offset 索引或 SQLite 主键查找，而不是无限缓存完整 record payload。

### 本轮修改文件

- `src/fireclaw_core/mission/mission_memory.py`
- `src/fireclaw_core/memory/embodied_memory.py`
- `src/fireclaw_core/memory/entity_extraction.py`
- `src/fireclaw_core/memory/robot_memory.py`
- `tests/test_mission_memory.py`
- `tests/test_embodied_memory.py`
- `tests/test_entity_memory.py`

未创建 commit；保留工作区既有用户/Antigravity 修改。

## 2026-08-26T12:59:39+08:00 授权后 Robot Agent 重跑优化

用户确认继续处理授权恢复阶段的重复 Robot Agent / snapshot 问题。

### 根因

- 原来的 `Gateway.confirm_task()` 在签发 `ExecutionAuthorization` 后只把
  `structured_task` 放回新的 `TaskControl`。
- `_start_task_worker(resumed=True)` 随后重新进入
  `_execute_agent_task()`，构造新的 Robot Agent，并再次调用
  `RobotAgentDeliberationRuntime.run()` 或 deterministic planner。
- 这会重新生成相同的决策；physical step 再次调用
  `FireClawAgent._record_robot_snapshot()`，导致第二套 body/environment/sensor
  snapshot 和重复的 safety/memory 热路径。

### 实施内容

- `AuthorizationRequest` 增加可选的 `pending_execution`：持久化首次已选中的
  exact `planning`、`operation_id` 和原始 snapshot 的
  `evidence_event_ids`，保留旧请求的向后兼容（缺少该字段时走旧恢复路径）。
- 所有会触发物理确认的 Agent 结果现在暴露 `memory_snapshot`；deliberated
  step 额外保存 `operation_id`，最终 awaiting-confirmation 结果把它们带到授权请求。
- `TaskControl`、`task.resume_scheduled` 和 gateway stale-resume 恢复链路传递
  `pending_execution`。
- 新增 `FireClawAgent.execute_authorized_pending_step()`：
  - 不调用 Robot Agent planner/LLM；
  - 从持久化 plan 重建 `PlanningResult`，重新校验签名授权和 action hash；
  - 重新读取 live robot/environment state，重新运行 SafetyGate，防止审批等待期间
    状态变化；
  - SafetyGate 仅写一条新的审计 decision，并复用原 snapshot 的 evidence IDs，
    不再调用 `record_snapshot()`；
  - 通过原 operation ID（无则使用 authorization ID）执行 exact plan。
- `Gateway._execute_agent_task()` 在有 pending continuation 时直接调用上述方法，
  包括无 `structured_task` 的普通计划；有 agent-tool 授权的请求仍保留原路径，避免
  把 advisory/tool side effect 错当作物理计划恢复。
- 恢复结果增加 `authorization_resume.snapshot_reused=true`、
  `robot_agent_reinvoked=false`、`llm_reinvoked=false`，并发出
  `task.authorized_plan_resumed` 事件，便于后续 TUI/trace 观测。

### 验证

- 新增 `tests/test_robot_agent_fireclaw_agent_execution.py`：计数 recorder，确认首次
  snapshot 只写一次，授权恢复不再写第二次。
- 新增 gateway 授权测试：确认 request 持久化 exact resume material，恢复执行走
  `execute_authorized_pending_step` 而不进入 planner。
- focused tests：`24 passed`（gateway authorization、Robot Agent execution/
  deliberation、agent-tool approval）；另外控制模型相关测试合计 `28 passed`。
- `compileall` 通过。
- 需要 loopback 的 gateway HTTP tests 在当前沙箱中仍受
  `PermissionError: [Errno 1] Operation not permitted` 限制；该失败来自环境 socket
  权限，不是本轮断言失败。此前已在沙箱外完成全量基线回归 `2470 passed, 8 skipped`。

### 结果与边界

- 授权后不再重新进行一次 Robot Agent LLM/decision loop，也不再重新创建第二套
  embodied snapshot；仅保留一次最终执行结果 memory record 和一次授权后的 SafetyGate
  decision，满足审计与审批等待期间 live-state revalidation。
- 旧版本已经落盘、但没有 `pending_execution` 的授权请求不会被强行猜测，继续走旧
  兼容恢复逻辑；新任务走无重跑路径。
- 尚未提交 commit；保留工作区已有修改。

## 2026-08-26T13:26:57+08:00 P1 相对位置校验接线修复

### 用户目标

修复 `返回现在的位置` 在 Mission preview 中已经获得机器人实时 pose，
却仍被 `validate_plan_target_binding` 判定为未绑定坐标的问题；要求保留无
TF/pose 证据时的 fail-closed 行为，并覆盖确认阶段的位姿漂移。

### 根因与改动

- `MissionAgent.deliberate_preview()` 原先把裸的
  `validate_plan_target_binding` 传给 `MissionDeliberationRuntime`。
  Runtime 的通用校验器契约只传 `plan`，因此 preview 已构建的冻结
  `MissionStateSnapshot` 没有进入相对 pose 校验。
- 改为在 preview 内创建一参数闭包，显式调用
  `validate_plan_target_binding(plan, state_snapshot=state_snapshot)`。
  这样相对目标只能绑定到同一 robot 的 map pose，yaw 也继续受实时证据约束。
- Gateway 在封存计划 preview 和 confirm 两个独立边界都增加只读适配：从
  authoritative readiness 的 `robot_readiness[*].readiness.value.state` 提取
  `robot_id + map pose`，转换为 validator 所需的 `robots` 视图。
- confirm 阶段使用当前 live pose 再校验；preview 后 pose 发生变化、或确认时
  pose/TF 不可用，计划会失效且不 dispatch，避免执行过期的相对目标。

### 测试

- 新增 `tests/test_sealed_plan_gateway.py` 覆盖：
  - 有实时 map pose 时 preview 成功并可 confirm；
  - 确认等待期间 pose 漂移时 fail closed，返回 `plan_registry_drift`；
  - 无 map pose 时不创建 artifact、不 dispatch。
- `/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
  tests/test_sealed_plan_gateway.py tests/test_mission_gateway.py`：
  `64 passed in 23.29s`（沙箱外，包含 loopback HTTP）。
- `tests/test_llm_deliberation_policy.py tests/test_plan_artifact.py
  tests/test_mission_agent.py`：`117 passed`。
- `compileall` 与目标文件 `git diff --check` 通过。
- 沙箱内运行需要 loopback 的测试会因 `PermissionError: [Errno 1]
  Operation not permitted` 失败；已在沙箱外复跑确认不是代码断言失败。

### 当前结论

P1 相对位置校验接线已修复。正常流程不再因 validator 丢失冻结 pose 而产生
不必要的坐标澄清；安全边界未放宽，缺少 pose 或确认期间发生位姿漂移仍会
阻止计划执行。尚未创建 commit，保留工作区既有用户/Antigravity 修改。

### 下一步

按优先级处理 P1 的中文终态消息契约、TUI 授权/恢复阶段可观测性与阶段计时，
然后再评估 P2 final report 的异步化。

## 2026-08-26T13:43:49+08:00 P1 终态语言契约修复

### 用户目标

修复 Robot Agent 终态消息没有语言契约的问题：同一批巡检子任务中，首个
终态报告为中文，后续报告可能被 LLM 采样为英文；要求终态对操作员始终输出
简体中文，且不依赖模型是否遵守提示词。

### 根因

- `complete_robot_task`、`report_robot_task_blocked`、`escalate_robot_task`
  的 `message/reason` 原先是任意字符串，没有 locale 字段或 schema 约束。
- LLM 返回的终态文本直接进入 `RobotAgentDeliberationRuntime`、
  `FireClawAgent.finalize_deliberated_task()` 和 Gateway terminal event，
  因而模型的语言选择会泄漏到 TUI/API。
- 仅修改 system prompt 不足以形成可靠契约；必须由宿主在终态出口渲染。

### 实施内容

- `src/fireclaw_core/task/terminal_outcome.py` 新增统一 `zh-CN` locale 和
  按 canonical status 渲染的确定性中文消息；未知状态 fail-closed 为失败文案，
  legacy `succeeded`、`clarify`、`awaiting_confirmation` 也有明确映射。
- Robot Agent 三个终态 Tool schema 新增必填 `message_locale`，枚举唯一值
  `zh-CN`，并在 LLM prompt/decision rules 中声明终态语言契约。
- `RobotAgentDecision` 持久化 `message_locale`；LLM policy 和 runtime 对
  LLM、测试 policy、插件/恢复 checkpoint 返回的终态英文文案统一替换为宿主
  渲染的中文，保留 `reason_code`、`evidence_ids` 等机器字段。
- `FireClawAgent` 的 deliberated final result、普通执行结果、授权恢复结果、
  取消/确认结果在写 memory 和 execution event 前统一补充 `message_locale` 并
  渲染终态文案；`robot_agent_deliberation.message` 同步归一化。
- Gateway 在 awaiting-confirmation、terminal event、worker exception、
  waiting-task terminalization、restart-lost 等边界再次兜底；异常详细文本
  放在结构化 `error/message_detail`，不再占用操作员终态 `message`。

### 验证

- Robot Agent、FireClawAgent、terminal outcome、Agent Harness、bounded loop
  等 focused tests：`89 passed`。
- 需要 loopback 的 Gateway 集成测试在沙箱外运行：`48 passed in 34.86s`。
- `compileall -q src/fireclaw_core` 与 `git diff --check` 通过。

### 当前结论

终态 `message` 现在是宿主拥有的 `zh-CN` 协议字段，不再信任 LLM 的自由文本；
模型仍可通过结构化 reason/evidence 提供审计信息，操作员 UI/API 则得到稳定的
中文终态。尚未创建 commit，保留工作区已有修改。

### 下一步

继续处理 P1 的 TUI 授权/恢复阶段可观测性与阶段计时，再评估 P2 final report
异步化；不应通过重新调用 Robot Agent 来补语言信息。

## 2026-08-26T14:00:30+08:00 P1 TUI 可观测性修复

### 用户目标

补齐 Mission CLI/TUI 对执行阶段、授权等待、授权恢复和长时间事件间隔的
可观测性，使操作员能区分 LLM/Agent 决策时间、上下文组装时间、网关事件链路
时间和物理执行时间。

### 根因

- `MissionScheduler._relay_robot_progress_events` 原先只把少数 Robot trace
  压缩成 `action.feedback`，授权/确认/恢复事件被丢弃，TUI 无法显示
  `authorization.requested`、`task.resume_scheduled`、快照复用等阶段。
- relay 没有保留 Robot Ledger 原始 `event_id/timestamp`，轮询间隔可能被误看作
  机器人或后端执行耗时。
- `LiveExecutionMonitor` 只有通用事件标签，没有阶段计时、相邻事件间隔和终态
  阶段汇总；Robot Agent decision 事件也没有 context/decision duration 字段。
- Mission SSE 首次回放从历史记录重建 `StreamEvent` 时丢失历史 timestamp、
  task/robot identity，导致阶段计时不可靠。

### 实施内容

- `mission_scheduler.py` 新增授权/确认/恢复/待恢复操作/Robot Agent
  deliberation 生命周期的结构化 relay；保留 `source_event_type`、
  `source_event_id`、`source_timestamp` 及有限 timing/authorization 字段，避免
  仅靠自由文本推断阶段。
- Robot Agent decision 记录 `context_duration_ms` 和 `decision_duration_ms`；
  TUI 分开显示上下文组装、Agent 决策、后端处理和事件链路间隔。
- `interactive.py` 增加规划、计划确认、执行授权、排队/下发、物理执行、
  恢复/重试、终态收敛阶段归类；显示授权请求/执行授权 ID、相邻事件间隔、
  快照复用及 Robot Agent/LLM 是否重跑；任务结束时输出阶段耗时与总时长，并
  标记计时来源（Robot/网关事件 timestamp 或本地接收时间）。
- `mission_gateway.py` 历史 SSE 回放保留 `event_id`、timestamp、task_id 和
  robot_id，避免回放时所有事件被赋予当前时间。
- `task.authorized_plan_resumed` 事件显式携带 snapshot/Robot Agent/LLM 重跑
  布尔证据，便于 TUI 呈现授权后没有重复规划的事实。

### 验证

- `/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
  tests/test_interactive.py tests/test_mission_scheduler.py
  tests/test_robot_agent_deliberation.py tests/test_robot_agent_fireclaw_agent_execution.py`：
  `65 passed`。
- `compileall` 和目标文件 `git diff --check` 通过。
- `tests/test_mission_gateway.py` 在当前沙箱直接启动 loopback HTTP 会被环境以
  `PermissionError: [Errno 1] Operation not permitted` 拦截，未据此判断代码失败；
  本次历史回放改动已通过编译和非网络聚焦测试。

### 当前结论

TUI 现在能把“长时间没有新输出”落到相邻事件间隔和阶段累计耗时，并能明确显示
授权/恢复是否发生、哪个请求/授权在等待，以及授权后是否复用了原快照、是否重跑
Robot Agent/LLM。计时优先使用生产者事件 timestamp，不把调度器轮询延迟冒充物理
执行耗时。尚未创建 commit，保留工作区已有用户/Antigravity 修改。

### 下一步

在真实 Gazebo 四点巡检上验证新的 TUI 阶段摘要和 source timestamp 是否与后端
事件账本一致；再处理 P2 final report 异步化和更细的并行子任务时间线。

## 2026-08-26T14:14:45+08:00 P2 Mission final report 异步化

### 用户目标

物理动作完成后立即向操作员输出确定性 Mission 终态，不再同步等待
`generate_mission_final_report()` 的 LLM 调用；报告继续生成，但不能阻塞动作完成。

### 根因

`MissionRunManager._execute()` 在得到终态动作结果后依次执行：获取 Mission trace、
调用 `generate_mission_final_report()`、持久化报告、最后才 `_set_status()` 和发出
`mission.completed/failed/cancelled`。因此报告模型的约 25 秒延迟被错误地计入物理
任务完成延迟。

### 实施内容

- `MissionRunManager` 新增 `_complete_and_schedule_report()`：先设置 canonical
  terminal status，发出 `mission.report_generation_started` 和终态事件；终态 payload
  明确包含 `report_status=pending`、`report_pending=true`，不再携带未生成的报告。
- final report 使用 gated daemon thread 异步生成；gate 保证事件顺序为
  `report_generation_started -> mission.<terminal> -> mission.report_ready`，同时
  `shutdown(wait=True)` 可以安全等待报告线程。
- `MissionRun.to_dict()`、`GET /missions/{id}/run?view=status` 和
  `GET /missions/{id}/report` 暴露 `report_status`、`report_pending` 及报告开始/结束
  时间、`report_duration_ms`；报告未完成时接口返回 pending，Mission run 本身已经
  是 terminal。
- 报告线程完成后持久化并发出 `mission.report_ready`；Gateway 同时继续发出
  `mission.final_report_ready`。报告事件带 ready 状态和生成耗时，但大型报告仍由
  SSE projection 截断为摘要。
- 报告生成或持久化发生异常时不回滚物理终态；生成异常使用确定性兜底报告并保留
  `report_error`，持久化异常记录 `persistence_error`。
- TUI 增加“物理终态已确定；报告生成不阻塞任务完成”和报告后台生成/耗时展示。

### 验证

- 聚焦回归：
  `/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
  tests/test_mission_run.py tests/test_interactive.py tests/test_mission_scheduler.py
  tests/test_robot_agent_deliberation.py tests/test_robot_agent_fireclaw_agent_execution.py`：
  `72 passed`。
- Gateway/Run loopback 集成：
  `/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
  tests/test_mission_gateway.py tests/test_mission_run.py`（沙箱外）：`59 passed in
  22.15s`。
- 新增慢报告测试验证：终态在报告 LLM 延迟期间已可读、`report_pending=true`，且
  `mission.report_ready` 只在后台报告完成后出现。
- `compileall` 与目标文件 `git diff --check` 通过。
- 沙箱内直接运行 Gateway HTTP 测试仍会因 loopback socket 权限报
  `PermissionError: [Errno 1] Operation not permitted`；已用沙箱外命令完成同一套
  集成验证。

### 当前结论

Mission 的物理完成延迟不再包含 final-report LLM 延迟。操作员先收到确定性终态和
报告 pending 状态；报告随后异步生成、持久化并通过 ready 事件/报告接口可取。尚未
创建 commit，保留工作区已有用户/Antigravity 修改。

### 下一步

在真实 Gazebo 四点巡检上比较 terminal event 与 report_ready 的时间差，确认 TUI
显示和报告接口轮询策略符合操作员预期；继续保留 deterministic terminal state 与
advisory report 的边界。

## 2026-08-26T14:21:30+08:00 规划 59.6 秒复测

### 运行证据

- Preview：`preview-f1b0445f514245a3b50708eb361fb024`。
- Mission Agent deliberation 从 `06:20:08.682846Z` 到
  `06:21:03.211365Z`，loop elapsed `54.5285s`。
- 回合 1 `inspect_state`：`21.0901s`，完成于 `06:20:29.778003Z`。
- 回合 2 `propose_plan`：`33.4212s`，完成于 `06:21:03.211171Z`。
- 语义计划图/封存 artifact 于 `06:21:03.258944Z` 创建；说明模型返回后本地
  编译、校验、artifact 写入约为几十毫秒量级。
- 规划上下文仅使用约 `3980/4190` 个输入 token，model 为
  `mimo-v2.5-pro`，output reserve 为 `4096` token；没有看到工具协议修复或
  额外重试。

### 结论

- TUI 的 `59.6s` 不是单一后端阻塞：约 `54.5s` 是两次串行远程 LLM 回合，
  当前模型服务实测并非 5 秒级。
- 首个 Mission Agent 回合在 TUI 总计约 `4.0s` 后才出现，来自规划前的
  readiness 链：`_current_plan_readiness -> FleetDoctor._check_reachability
  -> Robot Gateway /state -> ROS1 sensor discovery`。仿真配置的传感器发现
  `message_timeout_seconds=2.0`，匹配 topic 会顺序探测；这是真实后端开销，
  但不是 54 秒的主因。
- 当前预览的 command/state memory 记录分别在 `06:20:08.587403Z` 与
  `06:20:08.586109Z`，deliberation 在 `06:20:08.682846Z` 开始；P0 索引改造后
  memory/context 前置没有占据长时延。
- 第二回合之后到最终 TUI 行约有 1 秒级尾延迟，符合 SSE writer 的
  `Queue.get(timeout=1.0)` 心跳轮询边界，而非 LLM 或物理后端。

### 尚未修改

- 本轮只读诊断，未修改产品代码。若要进一步定位 provider 内部，应新增
  `readiness_ms / sensor_discovery_ms / memory_context_ms / context_assembly_ms /
  provider_http_ms / deterministic_validation_ms / sse_finalize_ms` 分阶段计时；
  当前生产记录只有每个 deliberation attempt 的总时长。

## 2026-08-26T14:56:43+08:00 规划阶段分层计时与 TUI 展示

### 用户目标

把规划阶段的总耗时拆成可审计的后端阶段，并通过 SSE/TUI 让操作员实时看到每个
阶段的耗时，区分 readiness、memory/context、LLM、确定性校验和 SSE 收尾。

### 实施内容

- `ProviderAgentHarness` 为每次模型回合发出 `context_fit` 与 `provider_request`
  阶段事件，包含 `duration_ms`、输入 token、output reserve、model 和错误状态。
- `MissionDeliberationRuntime` 记录并 checkpoint `context_assembly`、
  `state_observation`、`plan_validation` 及 Harness 子阶段；结果增加 `timing`。
- `MissionAgent.deliberate_preview` 记录任务状态快照、两次 memory 写入、规划
  memory/context、规划上下文对象和规划轨迹持久化阶段。
- `MissionGateway` 记录 readiness 与 plan artifact seal，将阶段清单和聚合结果放入
  `planning_timing.stages/by_stage_ms`；流式规划在最终结果前发送 `sse_finalize`
  阶段事件，最终结果仍是最后一条事件。
- SSE 事件类型为 `mission_agent.stage.completed`；TUI 显示阶段中文名称、回合号、
  阶段耗时、累计总用时以及 model/token/record kind/outcome 等诊断字段；最终行显示
  阶段汇总。非流式兼容路径在任务预览后显示同一汇总。

### 验证

- 聚焦回归：`62 passed`，覆盖 Harness、Mission deliberation、LLM policy、TUI、
  相对位姿预览和 SSE 队列/事件顺序。
- 本机回环 HTTP SSE：`test_http_streams_live_planning_events_before_final_preview`、
  `test_http_contract_requires_preview_then_one_time_confirm` 均通过（沙箱外运行）。
- `py_compile` 与 `git diff --check` 通过。

### 当前结论

下一次真实四点巡检会直接显示 readiness、每个回合的 context fit/LLM request、状态
观察、确定性计划校验、artifact seal 和 SSE 收尾的毫秒级耗时；可以用这些数值判断
长延迟来自远程模型、机器人状态探查还是本地后端，而不是只看一个总计时。

### 下一步

重启 Mission Gateway 后跑一次真实/仿真规划，保存 TUI 阶段汇总，并将各阶段耗时与
provider 服务端日志对齐；如仍需更细，再把 readiness 内部 FleetDoctor、sensor
discovery 和 `/state` 请求继续拆分。

## 2026-08-26T15:08:21+08:00 新计时实测：LLM 三回合导致内部 deadline

### 运行证据

- 操作员以 `python -m fireclaw_core mission --server http://127.0.0.1:8766
  --timeout 75` 发起同一四点巡检预览。
- readiness `3.491s`；任务状态快照 `0.504s`；两次 memory 写入合计
  `0.051s`；规划 memory/context `0.037s`；上下文组装/预算适配均为毫秒级。
- 三次远程模型请求分别为 `18.371s`、`27.722s`、`20.236s`，合计
  `66.329s`；模型均为 `mimo-v2.5-pro`。本地轨迹持久化 `0.021s`，SSE 收尾
  `0.005s`，TUI 总耗时 `70.5s`。
- 回合 1 的 `inspect_state` 成功；回合 2 生成的计划被确定性校验拒绝，反馈为
  `return_to_origin pose.yaw=0` 与实时 yaw `-0.654839` 不一致；因此进入回合 3
  修正，但回合 3 返回后没有进入 `plan_validation`，直接以 `timed_out` 结束。

### 根因判断

- Mission CLI 的 `--timeout 75` 是客户端 HTTP/SSE 等待时间，不是 Gateway 内部
  `BoundedAgentLoop` 的规划预算；本次客户端确实在 75 秒内收到后端结果。
- `gateway/serve.py` 当前把 Mission deliberation 全局预算设置为
  `max(5, provider_timeout_seconds + 5)`。配置中的 provider timeout 为 `60s`，
  所以内部规划预算约 `65s`。三次串行 LLM 请求从第一次回合开始累计超过该预算，
  第三次决策返回后在执行前被 loop timeout 截断。
- TUI 的“无法生成任务预览”是对所有非 `preview_ready` 状态的通用提示；本次不是
  HTTP 崩溃，而是后端 fail-closed 的 `timed_out/loop_timeout`，没有封存计划，也
  没有下发机器人任务。
- 当前相对位姿接线已经读到了实时 pose（否则会报“未绑定显式坐标”）；剩余问题是
  `validate_plan_target_binding` 把未在命令中指定的默认 `yaw=0` 当成显式 yaw，进而
  与“返回现在的位置”实时 yaw 比较并拒绝。这是相对位置语义/可选 yaw 契约问题。

### 本轮状态

- 仅完成证据分析，未修改产品代码。
- 后续应分别修复：1) 将 per-request provider timeout 与 multi-turn planning
  budget 解耦，并让 TUI 显示明确的 timeout reason；2) 对未显式指定 yaw 的相对位置
  目标采用“保持当前朝向/由宿主绑定实时 yaw”的安全契约，避免默认 0 触发误拒绝。

## 2026-08-26T15:35:23+08:00 解耦 Mission planning timeout 与相对目标 yaw 绑定

### 用户目标

同时修复两个 P1/P2 级运行问题：单次 provider 请求 timeout 不应决定整个多回合
Mission planning 的 deadline；“返回现在的位置”等相对目标未指定 yaw 时，不应把
planner/schema 的默认 `0` 当成操作员要求的朝向。

### 实施内容

- `gateway/serve.py` 新增 `mission_planning_timeout_seconds`，默认
  `180.0s`，单独传给 `MissionDeliberationLimits.timeout_seconds`；
  `provider_timeout_seconds` 仍只传给 `build_planner` 的单次 provider 请求。
  `run_server_blocking`、`mission_cli serve` 和 TOML `[mission]` 配置均已接线。
- `fireclaw.sim.toml`、仿真/真实示例及 Gazebo setup template 增加
  `planning_timeout_seconds = 180`；`[provider].timeout_seconds = 60` 保持单请求
  语义。新增 CLI 参数 `--mission-planning-timeout-seconds`。
- `plan_artifact.bind_relative_target_yaws()` 在预览封存前读取同一份冻结 readiness
  pose evidence：相对命令中省略 yaw 或仍为 schema 默认 `0` 时，将目标 yaw 绑定为
  实时 map yaw；显式 operator yaw 仍走严格匹配，planner 擅自给出的非零未声明 yaw
  仍会被拒绝。
- `MissionGateway.confirm_plan()` 在授权确认时重新执行绑定/校验。若等待确认期间
  相对目标的实时朝向发生漂移，会使 artifact 失效并要求重新生成，不会把旧朝向下发。
- `LLMMissionPlanner` 的 target schema/system prompt 明确标注 `yaw` 为可选：相对
  当前位置未指定朝向时应省略，而不是主动填 `0`；宿主归一化仍是最终安全边界。
- TUI/规划 timing 增加 `relative_target_binding` 阶段和“绑定目标数”字段，方便审计
  宿主归一化了哪些目标。

### 验证

- `python3 -m compileall -q src tests`：通过。
- `git diff --check`：通过。
- 11 个改动文件 AST parse：通过。
- 在当前容器中无法执行 pytest/完整 Gateway：系统 Python 为 `3.8.10`，项目要求
  `>=3.10` 且未安装 `pytest`；直接导入 Gateway 还会在现有 ROS1 runtime 的
  `str | None` 运行时类型表达式处失败。使用项目的 py310 环境应运行：
  `python -m pytest -q tests/test_serve.py tests/test_gateway_robot_agent_cli.py
  tests/test_plan_artifact.py tests/test_sealed_plan_gateway.py`。
- 使用 Python 3.8 可独立执行的 smoke check 已确认：相对目标 live yaw `0.4`、
  planner yaw `0.0` 会绑定为 `0.4`；绑定后确定性校验无错误，显式非零错误 yaw 仍会
  被拒绝。

### 当前结论与下一步

后端不再把 `[provider].timeout_seconds + 5` 隐式当成 Mission loop 总预算；当前仿真
配置允许 180 秒多回合规划。Mission CLI 的 `--timeout` 仍是客户端 HTTP/SSE 等待
窗口，做真实复测时应设置为大于 Gateway planning budget（例如 `--timeout 210`），
否则可能出现客户端先放弃、后端仍在继续规划的观测混淆。重启 Mission Gateway 后，
先复测原四点巡检，再检查 TUI 是否显示 `相对目标朝向绑定` 阶段和最终封存 yaw。

## 2026-08-26T15:43:08+08:00 使用 conda py310 完成回归

### 环境纠正

此前误用系统 `/usr/bin/python3`（Python 3.8）导致错误报告“无法运行 pytest”。
项目约定环境为 `/home/lpp/miniconda3/envs/py310`，版本 Python 3.10.4、pytest 8.4.2；
后续测试均使用该解释器。

### 测试结果

- 针对性套件：
  `tests/test_serve.py`、`tests/test_gateway_robot_agent_cli.py`、
  `tests/test_plan_artifact.py`、`tests/test_sealed_plan_gateway.py`：
  **56 passed in 7.22s**。
- 关联回归：
  `tests/test_llm_planner.py`、`tests/test_mission_cli.py`、
  `tests/test_interactive.py`、`tests/test_mission_gateway.py`、
  `tests/test_mission_deliberation.py`、`tests/test_planner_builder.py`、
  `tests/test_mode_specific_config_examples.py`：
  **167 passed in 56.03s**。
- 首次在默认沙箱执行时有 9 个 HTTP 测试因禁止创建本地 socket 报
  `PermissionError`；切换到允许本地回环 socket 的沙箱外执行后，这 9 个测试全部通过，
  因此不是产品断言失败。

### 当前结论

两个修复已在项目指定 py310 环境通过新增用例和关联回归；剩余工作是重启实际 Mission
Gateway，使用足够大的客户端 `--timeout` 做一次真实/仿真四点巡检验证。

## 2026-08-26T15:47:44+08:00 MiMo thinking 模式延迟诊断

### 检查结果

- `OpenAICompatProvider` 当前发送的请求字段只有 `model/messages/temperature/max_tokens`
  （以及可选的 `tools/seed`），没有显式发送 `thinking`、`reasoning`、
  `reasoning_effort` 或 `extra_body`。
- `SimpleProviderRuntime` 的请求也没有 thinking/reasoning 字段。
- `fireclaw.sim.toml` 使用 `mimo-v2.5-pro`，catalog 仅配置模型 ID、上下文窗口、
  `max_tokens=4096`，没有 thinking 开关。
- provider 解析器只保留 `message.content` 和普通 usage，丢弃了 MiMo 可能返回的
  `message.reasoning_content` 及 `usage.completion_tokens_details.reasoning_tokens`，
  因此当前 TUI 不能直接证明请求是否实际使用了 thinking。

### 结合官方文档的判断

MiMo OpenAI-compatible Chat Completions 文档说明 `mimo-v2.5-pro`/`mimo-v2.5` 的
`thinking.type` 默认是 `enabled`；深度思考说明也明确提示开启后复杂任务延迟会上升。
所以 FireClaw 虽然没有主动“打开” thinking，但目前也没有主动关闭；若实际 endpoint
遵循 MiMo 默认值，thinking 很可能是当前 18--28 秒单次 LLM 请求的主要原因之一。
实际配置使用的是 `token-plan-cn.xiaomimimo.com` 代理端点，仍需通过原始响应确认其
是否沿用官方默认。

### 现有运行证据

用户日志中的三次串行 LLM 请求为 18.371s、27.722s、20.236s，总计 66.329s；就绪、
memory/context、确定性校验和持久化均为毫秒到约 4 秒量级。因此本次慢主要发生在远端
模型请求，且三回合串行放大了 thinking（或模型整体生成）延迟，而不是 JSONL 扫描。

### 待验证动作

对完全相同的消息和工具 schema 做 A/B：保持当前默认请求与显式
`thinking: {type: disabled}` 请求，比较每次 LLM latency、总 planning latency、
`reasoning_content`/`reasoning_tokens` 和计划正确率。不要在拿到原始字段前把 thinking
断言为唯一原因；若关闭后明显下降，再增加可配置的 provider thinking 开关。

## 2026-08-26T15:53:45+08:00 MiMo thinking A/B 实测

### 测试方法

- 使用 `/home/lpp/miniconda3/envs/py310/bin/python`，读取当前 `fireclaw.sim.toml`
  的模型和兼容端点；请求仅用于诊断，不启动 Gateway、Mission 或机器人动作。
- 三轮请求均使用完全相同的 system/user messages、`navigate_to_point` tool schema、
  `temperature=0`、`max_tokens=4096`。
- 对照组省略 `thinking`（当前 FireClaw 行为）；实验组发送
  `thinking: {"type": "disabled"}`。

### 结果

- 第 1 轮：默认 8.177s，关闭 2.993s；默认返回 `reasoning_content`（223 字符），关闭
  不返回；两者均 HTTP 200 且各返回 1 个 tool call。
- 第 2 轮：默认 5.337s，关闭 2.958s；默认 `reasoning_content` 136 字符，关闭无；
  两者均返回 1 个 tool call。
- 第 3 轮：默认 4.789s，关闭 2.995s；默认 `reasoning_content` 201 字符，关闭无；
  两者均返回 1 个 tool call。
- 后两轮均值：默认 5.063s，关闭 2.976s，约减少 41.2%；加上第 1 轮总体均值：
  默认 6.101s，关闭 2.982s，约减少 51.1%。
- 该端点的 `completion_tokens_details.reasoning_tokens` 返回 0，但默认响应明确带有
  `reasoning_content`；不能仅依赖 reasoning_tokens 判断。

### 结论

当前 MiMo 兼容端点确实在省略 `thinking` 时返回思考内容；显式关闭后延迟稳定在约
3 秒，而默认约 4.8--8.2 秒。因此 thinking 是当前单次请求延迟的实证主因之一，且
远端模型请求而非 FireClaw 本地 memory/backend 扫描导致这部分差异。仍需在真实 Mission
上下文上做一次关闭后的端到端复测，以评估多回合计划正确率是否下降。

## 2026-08-26T16:05:25+08:00 增加 TOML thinking 开关并默认关闭

### 用户决定

先关闭 MiMo thinking，以降低 Mission/Robot Agent 的 LLM 延迟；保留以后按配置重新
开启的能力。

### 实施内容

- `OpenAICompatProvider` 新增可选构造参数 `thinking: bool | None`：`False` 发送
  `thinking: {"type": "disabled"}`，`True` 发送 `{"type": "enabled"}`，`None`
  不发送字段以保持其他 OpenAI-compatible provider 的原有默认行为。
- `planner_builder.build_planner/build_provider_runtime`、Mission Gateway `serve`、
  Robot Gateway `GatewayConfig` 和配置加载链路均已接线。
- `[provider].thinking` 加入 TOML；`[robot_agent.provider].thinking` 可覆盖公共
  provider 设置。当前仿真本地配置 `fireclaw.sim.toml`（被 `.gitignore` 忽略）已写入
  `thinking = false`；仿真/真实示例模板也写入 false。
- 配置加载会拒绝非 TOML boolean；LLM planning evaluation 读取共享 provider 配置。

### 验证

- `python -m compileall -q src tests`、`git diff --check`：通过。
- py310 targeted provider/planner/config/eval suite：**78 passed**；其中初次运行的
  `test_serve.py` 6 个用例仅因沙箱禁止本地 socket 被拒绝，沙箱外重跑为 **6 passed**。
- `tests/test_mission_cli.py tests/test_mode_specific_config_examples.py`：沙箱外
  **46 passed in 33.95s**（沙箱内 6 个 socket 用例受限，其余 40 个通过）。
- 当前 `fireclaw.sim.toml` 实际加载验证：`provider_thinking=False`，构造出的
  `OpenAICompatProvider.thinking=False`；后续请求会显式禁用 thinking。

### 当前操作

修改后需重启 Mission Gateway/Robot Gateway，现有进程不会热加载 TOML。重启后再跑四点
巡检，比较 TUI 中三回合 LLM 请求耗时和规划正确率；若复杂任务质量下降，再把配置改为
`thinking = true` 做对照。

## 2026-08-26T16:10:41+08:00 解释 Mission CLI `--timeout`

- `python -m fireclaw_core mission --timeout N` 设置的是 `MissionGatewayClient`
  的客户端 HTTP/SSE transport timeout；每次 health/readiness、规划预览、澄清、确认或
  事件流请求都使用该值（秒）。它不是单次 LLM provider timeout、Mission 多回合规划
  budget，也不是导航/物理执行 timeout。
- 当前 TOML `[provider].timeout_seconds = 60` 是单次远端 LLM 请求；`[mission].planning_timeout_seconds = 180`
  是服务端完整多回合规划预算。客户端 `--timeout` 太短时可能先放弃等待，而网关仍在
  继续规划，产生观测混淆。
- 当前测试命令使用 `--timeout 75`，低于 180 秒服务端规划预算。建议真实复测改为
  `--timeout 210`（或更保守的 240），尤其用于多轮澄清/LLM 规划；thinking 关闭后简单
  任务可能小于 75 秒，但 75 仍不是可靠上限。

## 2026-08-26T16:26:44+08:00 规划重复状态读取与 TUI 分层显示

- 新一轮四点巡检在 11.3 秒结束，原因不是 HTTP/LLM timeout：回合 1 的
  `inspect_state(robot_state)` 成功返回了实时位姿；回合 2 模型再次选择同一状态读取。
  `MissionDeliberationRuntime` 的 `seen_reads` 确定性门禁将其判为
  `repeated_state_read`，并 fail-closed 返回 `blocked`，要求模型直接调用
  `propose_task_graph` 或请求澄清。机器人没有开始物理执行。
- 这说明当前确定性安全策略防止了重复读取死循环，但模型在已有 observation 后没有推进到
  计划提案；后续可独立优化为宿主在下一回合禁用已完成的 `inspect_state`，或把反馈作为一次
  可恢复的规划错误重新交给模型，而不是立即终止。
- 对照本地 OpenClaw TUI：其 `showThinking`、`toolsExpanded` 默认关闭，内部 thinking/tool
  参数不占用主对话层，活动状态通过一个可更新的 dim status 行显示。FireClaw 本次将默认
  视图调整为同类分层：规划结果、动作选择、观察/校验结果和终态保持正常突出显示；阶段计时、
  5 秒心跳、完整 planning timing 汇总以及 Robot Agent 内部 deliberation 事件默认隐藏或
  dim。`mission --verbose`（别名 `--show-details`）恢复完整调试细节。
- 代码变更：`MissionTerminalRenderer.block/activity` 增加 `muted`；
  `MissionPlanningMonitor` 增加 `show_details`；`LiveExecutionMonitor` 默认不渲染
  `robot_agent.deliberation_*`/decision/observation；同步回退模式只显示主要耗时摘要；CLI
  `mission` 增加 verbose 开关。
- 验证：`tests/test_interactive.py` **24 passed**；py310 `py_compile` 与
  `git diff --check` 通过。`tests/test_mission_cli.py` 中需监听本地 socket 的用例仍需在
  沙箱外运行；CLI help 用例通过。

## 2026-08-26T17:03:10+08:00 Rich 分级活动流与确认阶段误报 409 修复

### 任务与现场证据

- 用户最新四点巡检规划在 24.9 秒内成功，确认时返回 HTTP 409：
  `Robot registry or capability state changed after preview.`。
- 对应封存 artifact 为 `sha256:e1434ba5402ced5984119b45ac28793aad85242da3217348a310e083c0966999`，
  store 终止原因为 `registry_or_capability_drift`。artifact 中四个目标均存在；前三个显式
  巡检点只有 `x/y`、未指定 `yaw`，返回点已正确冻结为预览时实时位姿
  `(-1.9333667, -0.5283910, -0.6551168)`。
- TUI 未显示前三个目标是独立显示错误：`_format_plan_target` 过去要求 `x/y/yaw` 三者都
  是数字，导致合法的“未指定 yaw”目标整行被隐藏。

### 409 根因与安全语义

- Preview 已用权威 live pose 对“返回现在的位置”做 grounding、确定性校验和 yaw 绑定，
  随后将 canonical plan 纳入服务端 artifact digest 并展示给操作员。
- `confirm_plan` 却再次读取当前实时位姿，重新调用 `bind_relative_target_yaws` 和
  `validate_plan_target_binding`。数值绑定使用 `abs_tol=1e-6`；Gazebo/定位系统在确认前的
  正常里程计抖动即可让已冻结目标不再匹配当前证据，随后被错误归类为 registry/capability
  drift。
- 修复后的边界：操作员目标 grounding 是 preview-time、sealed-artifact invariant；确认
  不再重新解释或改写目标。确认仍 fail-closed 复检 runtime identity、Active Profile、
  robot/admission readiness、registry/capability、2D plan contract 和 artifact token/digest。
  这保留动态安全门禁，同时确保执行内容与操作员看到并确认的内容完全一致。
- 更新回归用例验证：确认前即使实时位姿发生明显变化，也调度 preview 中的原始冻结目标；
  profile/readiness drift 仍会拒绝。

### Rich Hierarchical Activity Stream

- 新增 `src/fireclaw_core/mission/terminal_renderer.py`，核心类
  `TerminalAgentRenderer` 提供：
  - `render_thought()`：dim/faint 的安全推理状态摘要（不暴露 private chain-of-thought）；
  - `render_tool_call()`：dim 的 `• Called` / `• Ran` / `• Explored` 工具与环境反馈；
  - `render_final_response()`：bold/bright 的最终结果；
  - `render_primary_status()`：关键但非最终的状态；
  - Rich `Status`：持续等待时只更新一条 transient dim 行。
- `MissionTerminalRenderer` 保留为兼容 facade，旧 `block/activity` 调用也由 Rich 单一 console
  和原子渲染锁输出；规划 monitor、执行 monitor、任务预览、Gateway 错误、任务提交和终态
  已接入新的语义接口。
- 流式规划等待期间使用一条 Rich `Status` transient dim 行，根据 heartbeat 更新；工具动作、
  规划结果或异常到达时立即收起，避免把每次 heartbeat 固化成终端历史。
- 默认界面将 planning turn/status 与工具反馈弱化；规划完成、预览、授权/任务关键状态和终态
  保持主视觉层。`--verbose` 仍只控制是否显示完整 timing/内部事件，而不是丢弃后台审计事件。
- `_format_plan_target` 现在只要求合法 `x/y`；缺失 yaw 明确显示 `yaw=未指定`。
- `pyproject.toml` 增加运行依赖 `rich>=13.7`。当前 py310 已有 Rich 14.3.2；宿主未安装
  `uv`，未重写仓库中原本已与当前 pyproject 不同步的旧 `uv.lock`。

### OpenClaw analogue

- 对照 OpenClaw 的 typed item activity stream：事件携带 tool/command/search/analysis kind、
  start/update/end phase 和 running/completed/failed status，TUI 决定是否折叠或 dim，而不是让
  后端预先拼 ANSI 文本。
- FireClaw 复用了“语义事件与呈现策略分离、tool/thinking 默认弱化、状态行可更新”的形状；
  保留机器人特有的 authorization、物理终态、ROS 错误和安全证据为关键状态。

### 修改文件与验证

- 新增：`src/fireclaw_core/mission/terminal_renderer.py`、
  `tests/test_terminal_renderer.py`。
- 修改：`src/fireclaw_core/mission/interactive.py`、
  `src/fireclaw_core/mission/mission_gateway.py`、`tests/test_interactive.py`、
  `tests/test_sealed_plan_gateway.py`、`pyproject.toml`。
- py310 renderer/interactive：**28 passed**。
- py310 sealed artifact/plan artifact：**36 passed**（沙箱外本地回环 HTTP 测试）。
- py310 Mission CLI/Gateway/client 聚焦回归：**195 passed in 70.29s**。
- py310 distribution/CLI/serve：**55 passed in 37.50s**。
- `py_compile`、`git diff --check`：通过。

### 下一步

- 重启 Mission Gateway 以加载 `confirm_plan` 修复；Mission CLI 也需重新启动以加载 Rich TUI。
- 用原四点命令复测，预期前三个步骤显示 `yaw=未指定`，确认阶段不再因实时 pose 抖动报 409。
- 本次未启动或操控 Gazebo/机器人；端到端物理复测由操作员在当前仿真环境执行。

## 2026-08-26T17:17:42+08:00 四点端到端复测评估

### 实际结果

- Mission `mission-1fbeb7837058409b8851e4d2c2b1268e` 已成功通过 preview、确认、
  四个串行导航和异步 final-report 边界；此前的确认 409 未再出现。
- 持久化 `data/mission/missions.jsonl` 证明四个 Robot task 的 `raw_status` 均为
  `succeeded`，终态均为 `completed`，而不只是 TUI 聚合层报告成功。
- 目标与最终位姿：
  - 点 1：目标 `(0.56, 1.80)`，最终 `(0.53695, 1.79497)`，平面误差约 `0.0236m`，
    导航 `22.3s`；
  - 点 2：目标 `(1.48, -1.58)`，最终 `(1.51391, -1.55459)`，误差约 `0.0424m`，
    导航 `25.3s`；
  - 点 3：目标 `(-0.58, 0.54)`，最终 `(-0.56720, 0.52804)`，误差约 `0.0175m`，
    导航 `21.5s`；
  - 返回点：目标 `(-1.93337, -0.52840)`，最终 `(-1.94924, -0.51021)`，误差约
    `0.0241m`，导航 `27.5s`。
- 四段纯导航累计 `96.6s`；从首个 subtask accepted 到第四个 completed 的 wall time
  为 `173.6s`，非导航链路约 `77.0s`，包含 Robot Agent planning/context、authorization、
  resume、poll/dispatch 间隔等。
- 返回点出现两次 `DWA planner failed to produce path`，随后清理 costmap 并最终成功；
  属于恢复成功而不是未报告失败。

### 仍存在的 TUI/事件问题

1. 默认输出仍过度详细。每个 subtask 把 authorization requested/pending/approved、resume
   scheduled/started/resumed 等逐条作为 primary block 展示；更合理的默认视图应合并为一个
   “执行授权通过，复用原快照”关键状态，完整审计过程只在 `--verbose` 展示。
2. `Explored navigate_to_point` 的语义不准确。Mission Scheduler 把
   `robot_agent.decision` 和 `skill.started` 包装为外层 `action.feedback`，TUI 仅按外层类型
   归类为 explored；应优先按本条 payload 自带的 `source_event_type` 映射为 Called/Ran/
   Explored。
3. 成功终态证据没有实时投影到 TUI。Scheduler 目前只 relay failed/aborted 的
   `robot_agent.observation`，`_DIRECT_TRACE_RELAY_EVENTS` 也不包含 `skill.succeeded`、
   `task.completed`；所以界面没有逐点显示最终位姿和 `target_pose_confirmed`，尽管持久化
   Robot trace 中证据完整。
4. TUI 的 task context cache 通过 `{**cached, **payload}` 缓存整个 payload，导致
   `source_event_type`、`source_timestamp`、`reason_code=safety_gate_terminal`、Agent timing
   和 authorization ID 泄漏到后续无关事件。结果是 `mission.subtask_dispatched` 被错误显示为
   `confirmation.confirmed` 来源和计划确认阶段，并在每行重复旧 timing/authorization 字段。
   cache 应只白名单保留 plan-step/node/robot/task identity，不应缓存事件事实字段。
5. 阶段计时汇总不可信：TUI 报 `320.0s`，而真实 subtask wall time 为 `173.6s`。缓存污染
   的旧 `source_timestamp/source_event_type` 以及按阶段切换累计、未处理延迟批量 relay 的方法
   产生重叠计时。应使用未污染的事件时间、单调顺序，并优先采用后端显式 start/end duration，
   而不是从任意相邻事件推断互斥阶段。
6. `mission.subtask_dispatched` 在同步 `execute_sealed_plan` 全部返回后才由 MissionRun 批量
   发出，事件名和时序具有误导性；应在真实 dispatch 边界实时发出，或将事后事件重命名为
   `mission.subtask_result_recorded`。
7. Rich 换行仍可改善：完整 digest、长 pose 和 ROS 文本换行后缺少 hanging indent；默认
   可缩短 digest/pose 精度，使用 Rich Tree/Table/Padding 保持续行缩进，完整值留给 verbose。

### 当前结论与建议优先级

- 工程执行正确性：本轮通过，导航精度和恢复行为良好。
- 安全与审计：封存目标、逐任务终态、授权复用证据均存在，核心安全链路通过。
- 操作员可观测性：仍不应视为最终完成；建议下一项先做“事件上下文白名单 + 成功终态 relay +
  可信阶段计时”，再做授权事件折叠和 Rich hanging-indent 美化。

## 2026-08-26T17:54:17+08:00 TUI 事件语义、成功证据与可信计时修复

### 任务目标

- 修复 17:17 四点端到端复测中确认的七项 TUI/事件投影问题，同时保留后端完整安全审计，
  不改变 SafetyGate 的授权规则，也不削弱物理动作的安全边界。
- 默认终端只展示操作员需要关注的语义事件；`--verbose` 继续提供 authorization、resume、
  task lifecycle、事件来源、上下文耗时和 ID 等完整追踪信息。

### OpenClaw analogue

- 先检查了本地 `openclaw/` 的 `src/tui/components/chat-log.ts`、
  `src/tui/tui-event-handlers.ts` 和 `src/tui/tui-stream-assembler.ts`。
- 复用其结构：后端保留结构化 lifecycle event 和稳定 ID，TUI 根据语义更新或折叠展示，
  默认弱化 tool/thinking 细节。FireClaw 保留其特有的 SafetyGate、ROS、物理终态和
  final pose 证据，而不是照搬聊天产品的 channel/transport 假设。

### 具体修改

1. `src/fireclaw_core/mission/interactive.py`
   - task context cache 改为严格白名单，只保存 mission/robot/task/node/plan-step identity；
     不再把 `source_event_type`、`source_timestamp`、`reason_code`、authorization ID、
     duration 或 message 泄漏到后续事件。
   - 事件分类优先使用当前 payload 的 `source_event_type`：Robot Agent 决策显示 `Called`，
     动作成功显示 `Ran`，观察/ROS 显示 `Explored`。
   - 默认折叠正常 authorization/resume/task lifecycle；一项动作只显示一次工具调用、一次
     “等待执行授权”和一次“执行授权通过（复用原快照；Robot Agent/LLM 未重跑）”。
     拒绝、失败和异常不折叠；`--verbose` 仍可查看完整事件。
   - 默认合并连续重复的 action feedback 和 ROS 日志；verbose 模式保留全部记录。
   - `skill.succeeded` 使用确定性中文生成逐点成功证据：目标位姿、最终位姿、平面误差、
     物理动作耗时。该行不调用 LLM。
   - 阶段统计改为显式 start/end 关联，物理动作优先使用后端 `elapsed_seconds`；Robot Agent
     planning 使用明确的 context/decision duration。事件链路总时长取首末生产者时间戳，
     不再把可重叠阶段相加为虚假的 `320s`。
2. `src/fireclaw_core/mission/mission_scheduler.py`
   - 实时 relay `skill.succeeded` 和 Robot terminal event，包括 `task.completed`。
   - Mission SSE 只投影有界的操作员证据：tool/skill、target/final pose、elapsed、goal state、
     status/outcome；完整 result、memory 和 runtime snapshot 仍只保存在 Robot ledger 中供审计。
3. `src/fireclaw_core/mission/mission_run.py`
   - `mission.sealed_plan_loaded` 改为在真实执行前发出。
   - 删除同步执行全部结束后才批量发出的误导性 `mission.subtask_dispatched` 事件；真实子任务
     进度由 Scheduler 的实时 relay 负责。
4. `src/fireclaw_core/mission/terminal_renderer.py`
   - Rich 行按语义字段独立 wrap，长 digest、pose 和 ROS 文本续行使用 hanging indent，
     避免续行顶格和 Table 尾部空白。

### 默认输出契约

- 每个正常导航步骤默认约四个关键块：`Called navigate_to_point`、紧凑 Safety 等待、
  `执行授权通过`、`Ran navigate_to_point`（带到达证据）。
- Robot/task/node、事件来源、授权 ID、上下文/决策耗时、相邻事件间隔等审计字段仅在
  `--verbose` 展示。
- 阶段汇总明确标注“显式起止，各项可能重叠”；链路总时长不再等于各阶段的简单求和。

### 修改文件

- `src/fireclaw_core/mission/terminal_renderer.py`
- `src/fireclaw_core/mission/interactive.py`
- `src/fireclaw_core/mission/mission_scheduler.py`
- `src/fireclaw_core/mission/mission_run.py`
- `tests/test_terminal_renderer.py`
- `tests/test_interactive.py`
- `tests/test_mission_scheduler.py`
- `tests/test_mission_run.py`

### 验证结果（conda `py310`）

- renderer/interactive/scheduler/run 聚焦测试：**66 passed in 1.22s**。
- Gateway/SSE/sealed/fault/terminal outcome：**128 passed, 1 skipped in 38.74s**。
- CLI/serve/distribution/web console：**69 passed in 40.44s**。
- 完整 Mission/Sealed/TUI 回归：**445 passed in 73.80s**。
- authorization/Robot Agent/embodied Mission/fault：**33 passed, 1 skipped in 4.61s**。
- 新增的重复 ROS 合并与相关显示测试：**5 passed**。
- 所有本次修改文件的 `py_compile` 与 `git diff --check` 通过。

### 当前结论与下一步

- 工程正确性：事件语义、默认信息层级、成功证据和时间统计已按现有日志问题修复；SafetyGate
  与完整审计事件仍保留，没有为了简化 TUI 绕过授权。
- 研究影响：这为后续评估 operator workload、causal trace fidelity 和 latency attribution
  提供了可信的观测基线；但 UI/事件清理本身不是论文级方法创新，需要正式用户研究、任务
  成功率/响应时间指标及消融实验才能形成研究贡献。
- 本轮没有启动或控制 Gazebo。由于 Scheduler/MissionRun/TUI 均有代码变化，物理复测前应
  重启 Mission Gateway、Robot Gateway 和 Mission CLI，再运行原四点巡检命令，确认真实 SSE
  输出与自动化测试一致。

## 2026-08-26T18:04:55+08:00 原地转十圈任务失败原因诊断（未改代码）

### 现象

- 第一次澄清后，LLM 生成了 `spin_10_times`，并把当前位姿写成 map pose
  `(-1.9747, -0.474881)`；确定性目标绑定校验拒绝：该 pose 不是操作员命令中的显式坐标。
- 操作员确认“是当前位置”后，第一次 `inspect_state` 成功返回实时位姿
  `(-1.97109, -0.476845, -0.52504)`，但下一回合 LLM 又对同一个 `(robot_state, robot)`
  发起一次完全相同的 `inspect_state`。deliberation guard 以 `repeated_state_read` 终止，
  没有生成任务预览。

### 根因判断

1. 当前 TurtleBot3 capability projection 只暴露 `navigate_to_point`；模板中的
   `enabled_skills`、`llm_exposed_skills` 和 `primitive_skills` 都只有该工具。没有面向
   操作员的 `rotate_in_place`/`spin` 物理 Tool。move_base 的内部 `rotate_recovery` 不是
   可下发的十圈动作。
2. `navigate_to_point` 的 `target.pose` 只表达最终地图位姿，不表达“原地连续旋转 N 圈”这
   类轨迹/角度累计约束。十圈结束后最终朝向可能与开始相同，不能用一个普通 yaw goal 表达
   中间经过 `20π` 的旋转过程；LLM 因而错误地把 spin 任务投影成了一个导航 pose。
3. `validate_plan_target_binding` 有意阻止 LLM 自己发明 map 坐标。用户说“当前位置”在当前
   设计中只对已支持的相对导航目标有宿主绑定路径；它不是一个已实现的旋转动作授权。
4. 第二次失败与物理能力无关：同一状态已经观察过，LLM 没有调用 `propose_task_graph`，而是
   重复读状态；`mission_deliberation.py` 的保护逻辑为防止无进展循环而直接 block。

### 结论

- 这次没有进入 SafetyGate、Robot Agent 或 Gazebo 执行阶段，因此不是“机器人不会转”或
  “转圈执行失败”，而是当前能力目录/任务表示不支持该任务，加上 LLM 在澄清后的回合没有
  推进到计划生成。
- 本次诊断没有修改代码；后续若要支持，需要独立的旋转 Tool、角度/圈数/速度/超时及安全
  完成契约，并让 planner 将其建模为旋转轨迹而非 map pose。

## 2026-08-26T18:08:37+08:00 “对当前环境巡检”失败原因诊断（未改运行代码）

### 现象

- 能力问答阶段把当前机器人描述为具备 navigation、patrol、emergency_stop；随后用户要求
  “对目前的环境进行一个巡检”。
- LLM 第一次读取 `environment_beliefs`，得到空列表；下一回合对同一状态再次调用
  `inspect_state`，触发 `repeated_state_read`，规划被阻塞，没有进入任务预览或执行。

### 根因判断

1. `patrol` 是 Mission intent，不是独立的环境扫描 Tool。当前 TurtleBot3 的 capability
   projection 将 `patrol` 映射到 `navigate_to_point`；已有四点巡检之所以成功，是因为用户
   明确提供了多个 map 坐标，planner 将 patrol 分解为有序导航节点。
2. “对当前环境巡检”没有给出区域/路线/巡检点，也没有检查目标（障碍物、火情、人员、温度等）
   和成功标准。当前没有泛化的 `scan_environment`/自主探索 Tool，不能仅凭 patrol intent
   安全地产生物理动作。
3. `environment_beliefs: []` 表示 Mission 冻结快照中没有已登记的环境 belief，不表示
   激光雷达已经执行了一次现场扫描，也不表示环境为空。`inspect_mission_state` 只是读取
   快照，不会主动驱动传感器。
4. `request_observation` 是补充现场证据的受控入口，只有先查询到未解决 belief 且宿主开放
   对应 active-observation capability 时才会暴露。当前 belief 为空，所以 planner 不能把
   “巡检”自动变成现场观测；LLM 反而重复读同一状态，于是被无进展保护终止。

### 结论

- 巡检并非完全不可用：明确的坐标/区域路线可以继续用导航节点执行，之前四点任务已经证明
  这一点。
- 本次失败的是“开放式环境巡检”这个未充分定义、且当前没有对应现场扫描 Tool 的请求，
  不是导航器或机器人执行失败。能力问答中的 `patrol` 应理解为“可由导航点组成巡检”，
  不是“可自主扫描任意当前环境”。
