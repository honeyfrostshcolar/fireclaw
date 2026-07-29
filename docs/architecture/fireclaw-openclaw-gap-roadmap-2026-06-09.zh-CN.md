# FireClaw 与 OpenClaw 功能核对和后续规划

> 术语说明（2026-07-29）：本文是历史差距分析。文中的主智能体现统一称为 `Mission Coordinator`（任务协调器），机器人侧子智能体现统一称为 `Robot Agent`（机器人智能体）。两者均为 FireClaw 架构中的常驻 Agent；`subagent` 仅指未来按需派生、完成认知任务后退出的临时工作单元。详见 [FireClaw Agent Terminology](./fireclaw-agent-terminology.md)。

日期：2026-06-09（最后更新：2026-06-10 02:00 CST）

## 结论

FireClaw 当前已经能完成一个基础的消防机器人 embodied-agent 闭环：

```text
operator command
-> mission planning (LLM tool-calling planner)
-> mission scheduler (execution group ordering, failure policy)
-> mission HTTP Gateway (submit/trace/events/cancel/approvals/fleet)
-> robot subagent selection
-> robot-local Gateway task queue
-> local planner/safety/skill runtime
-> robot adapter / ROS1 transport (real message conversion)
-> SSE event stream (robot-local + mission-level)
-> action feedback / cancel / emergency stop
-> mission trace / memory / replay (with action-level events)
```

Phase 6-10 全部已实现（v1 级别）。FireClaw 现在具备：scheduler 默认路径、mission Gateway、method scopes 默认拒绝、SSE 实时事件流、ROS1 smoke proof、SQLite FTS memory index、plugin descriptor v1。

当前默认全量测试结果：`.venv/bin/python -m pytest -q`，`1020 passed, 6 skipped`。
ROS1 smoke 显式验证：`FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py -q`，`6 passed`。

## 当前已经实现的能力

| 能力 | FireClaw 当前状态 | 代表文件 |
|---|---|---|
| 主任务 agent | 已实现 `MissionAgent`，可提交、追踪、取消、记录修正、请求 approval、回放 incident | `src/fireclaw_core/mission_agent.py` |
| Robot subagent contract | 已实现 HTTP client，支持 state、submit、trace、cancel、events、presence | `src/fireclaw_core/subagent_client.py` |
| Robot registry / heartbeat | 已实现 registry、enabled/stale filter、presence update | `src/fireclaw_core/robot_registry.py` |
| Mission planner | 已实现 deterministic planner；已实现 LLM tool-calling planner | `src/fireclaw_core/mission_planner.py`, `src/fireclaw_core/llm_planner.py` |
| Mission registry / trace | 已实现 JSONL mission registry、trace aggregation、event aggregation、polling stream | `src/fireclaw_core/mission_registry.py`, `src/fireclaw_core/mission_trace_stream.py` |
| Mission scheduler | 已实现 scheduler/failure policy/retry/reassign；`plan_and_submit` 默认使用 scheduler | `src/fireclaw_core/mission_scheduler.py`, `src/fireclaw_core/mission_agent.py` |
| Mission Gateway | 已实现 mission-level HTTP Gateway（submit/trace/events/cancel/approvals/fleet state/fleet doctor） | `src/fireclaw_core/mission_gateway.py` |
| Robot-local Gateway | 已实现 task submit/run/cancel/emergency stop/state/events/trace/API token/SSE stream | `src/fireclaw_core/gateway.py` |
| Method scopes | 已实现 22 endpoint descriptor table、默认拒绝、admin bypass、write-implies-read | `src/fireclaw_core/method_scopes.py` |
| Realtime event stream | 已实现 StreamEvent schema、EventBus pub/sub、SSE endpoints（robot-local + mission-level）、TelemetryTracker | `src/fireclaw_core/stream_events.py` |
| Durable task queue | 已实现 JSONL queue、dedupe、lost reconciliation、compaction | `src/fireclaw_core/task_queue.py` |
| Task registry | 已实现 OpenClaw 式 TaskRecord、delivery state、notify policy、JSONL store、queue-to-registry 转换、`project_task_state()` 幂等 projection、owner/session listing helpers | `src/fireclaw_core/task_registry.py` |
| Subagent run registry | 已实现 SubagentRunRecord、parent/child lineage、JSONL store、可选 client wiring、`mark_terminal()` 幂等 terminal update | `src/fireclaw_core/subagent_registry.py` |
| Lifecycle reconciliation | 已实现 `LifecycleReconciler`（orphan detection、stale task detection、mission-projected subtask matching）和 `LifecycleMaintenanceRunner`（explicit maintenance invocation、report output） | `src/fireclaw_core/lifecycle_reconciler.py`, `src/fireclaw_core/lifecycle_maintenance.py` |
| Safety gate | 已实现 planning safety、sensor/risk/dry-run/robot-state/environment-state checks | `src/fireclaw_core/safety.py` |
| Skill runtime | 已实现 manifest、input schema、risk metadata、subprocess skill、workspace skill loading、plugin descriptor v1、plugin runtime hooks（provider/memory/tool_approval）、hook policy enforcement、unknown plugin rejection、persistent audit trail、control-plane fingerprints | `src/fireclaw_core/skills.py`, `src/fireclaw_core/skill_manifest.py`, `src/fireclaw_core/plugin_descriptor.py`, `src/fireclaw_core/plugin_runtime.py`, `src/fireclaw_core/plugin_policy.py`, `src/fireclaw_core/plugin_control_plane.py` |
| Action runtime | 已实现 action lifecycle、feedback、cancel callback、ROS1 feedback path | `src/fireclaw_core/action_runtime.py` |
| Robot adapters | 已实现 dry-run、simulator、ROS1 transport（含 dict-to-message 转换）、ROS2 adapter protocol boundary | `src/fireclaw_core/robot.py`, `src/fireclaw_core/ros1_transport.py`, `src/fireclaw_core/ros2_adapter.py` |
| Authorization / approval | 已实现 role scopes、task/mission scope checks、high-risk approval stores、method-level scope enforcement、approval runtime tokens（token 创建/expiry/resolution/pending projection）、JSONL 持久化、`ApprovalRelay` Protocol + InMemory/Console/Webhook adapters | `src/fireclaw_core/control.py`, `src/fireclaw_core/approval_store.py`, `src/fireclaw_core/method_scopes.py`, `src/fireclaw_core/approval_runtime.py`, `src/fireclaw_core/approval_relay.py` |
| Memory / replay | 已实现 robot memory、mission memory、corrections、incident replay（含 action-level events）、memory index v1（SQLite FTS）、memory retrieval v2（embedding protocol、rank fusion、transcript ingestion）、memory evaluation（thresholded eval、doctor integration） | `src/fireclaw_core/memory.py`, `src/fireclaw_core/mission_memory.py`, `src/fireclaw_core/incident_replay.py`, `src/fireclaw_core/memory_index.py`, `src/fireclaw_core/memory_retrieval.py`, `src/fireclaw_core/memory_eval.py` |
| Provider runtime | 已实现 OpenAI-compatible provider、model catalog、LLM trace store、provider runtime protocol（model fallback boundary、error normalization） | `src/fireclaw_core/provider.py`, `src/fireclaw_core/model_catalog.py`, `src/fireclaw_core/llm_trace.py`, `src/fireclaw_core/provider_runtime.py` |
| Diagnostics | 已实现 robot doctor 和 fleet doctor 基础检查、`--fix` 模式修复 stale queue records、plugin descriptor 检测、memory index 检测、fleet onboarding report（enrolled/enabled robots、stale heartbeats、missing ROS1 remaps、missing emergency stop、unresolved approval relay） | `src/fireclaw_core/doctor.py`, `src/fireclaw_core/fleet_doctor.py` |
| Log redaction | 已实现 secret pattern redaction（sk-*, Bearer, api_key, password, token） | `src/fireclaw_core/log_redaction.py` |
| ROS1 smoke proof | 已实现 topic/service/action/cancel/timeout smoke tests；默认跳过，需 `FIRECLAW_RUN_ROS1_SMOKE=1` 显式启用 | `tests/test_ros1_smoke.py`, `tests/conftest.py` |
| Mission Gateway client | 已实现 typed HTTP client（7 端点），支持 Bearer auth 和 operator scopes | `src/fireclaw_core/mission_gateway_client.py` |
| SSE cursor replay | 已实现 EventBus ring buffer、`get_recent_events(after_sequence)`、SSE `id:` 字段、`Last-Event-ID` / `?after_sequence=` 重连语义 | `src/fireclaw_core/stream_events.py`, `src/fireclaw_core/gateway.py`, `src/fireclaw_core/mission_gateway.py` |

## 与 OpenClaw 的对应关系

| OpenClaw 能力 | FireClaw 对应 | 覆盖判断 |
|---|---|---|
| Agent/session/task runtime | `MissionAgent`, `FireClawAgent`, `JsonlMissionRegistry`, `JsonlTaskQueue`, `TaskRegistry`, `LifecycleReconciler`, `LifecycleMaintenanceRunner` | 部分覆盖。v1 implemented: TaskRecord、owner/session listing、lifecycle reconciliation（orphan detection, stale task detection）、maintenance runner。剩余缺口是跨进程 reconciliation 调度和 session store。 |
| Subagent spawn/runtime | `RobotSubagentClient`, `JsonlSubagentRegistry` + robot-local Gateway | 部分覆盖。v1 implemented: SubagentRunRecord、completion routing（terminal robot events → `mark_terminal()`）、mission-projected subtask matching。剩余缺口是 orphan recovery 自动修复调度。 |
| Gateway protocol/control plane | `FireClawGateway` HTTP endpoints + `MissionGateway` + method scopes | 部分覆盖。已有方法描述符和 scope 执行；缺 WebSocket/control channel 和 classified method 的动态扩展。 |
| Method scopes / least privilege | `method_scopes.py` with 22 endpoints, default-deny | 部分覆盖。已有 descriptor table 和 scope enforcement；缺 dynamic/plugin scope 和 runtime scope resolution。 |
| Pairing/enrollment | `JsonlEnrollmentStore` | 基础覆盖。缺 endpoint/CLI/fleet registry 自动写入和 operator approval 整合。 |
| Provider runtime / model catalog | `OpenAICompatProvider`, `ModelCatalog`, `ProviderRuntime` | 部分覆盖。v1 implemented: provider runtime protocol、model fallback boundary、error normalization。剩余缺口是 multi-provider catalog、health/status dashboard、transport normalization。 |
| Tools / skills / plugins | `SkillRegistry`, manifests, workspace skills, `FireClawPluginDescriptor` v1, `PluginRuntime`, `PluginPolicy`, `PluginControlPlaneContext` | 部分覆盖。v1 implemented: callable hook 注册/执行、hook policy enforcement、unknown plugin rejection、persistent audit trail、control-plane fingerprints。剩余缺口是第三方插件 sandbox loading 和 dynamic hook registration。 |
| Memory | `MissionMemoryStore`, `SqliteMemoryIndex` (FTS), `MemoryRetriever`, `memory_eval.py` | 部分覆盖。v1 implemented: embedding provider protocol、rank fusion retrieval、transcript ingestion、thresholded evaluation、doctor integration。剩余缺口是 embedding provider lifecycle automation、session transcript indexing policy、retrieval 质量回归阈值集成 CI。 |
| Approval / permissions | `ApprovalStore`, `ControlPolicy`, method scopes, `ApprovalRuntime`, `ApprovalRelay`, `ConsoleApprovalRelay`, `WebhookApprovalRelay` | 部分覆盖。v1 implemented: approval runtime tokens（creation/expiry/resolution/pending projection）、JSONL 持久化、relay Protocol + InMemory/Console/Webhook adapters。剩余缺口是部署环境的实际 operator channel 适配器。 |
| Sandbox / process permissions | subprocess skill constraints + dry-run/live gate | 不足。缺系统化 sandbox policy、filesystem/network/process permission model。 |
| Config/doctor/migration | `doctor.py`, `fleet_doctor.py`, config skeletons, fleet onboarding report | 部分覆盖。v1 implemented: `--fix` 模式修复 stale queue、fleet doctor onboarding report（enrolled/enabled robots, stale heartbeats, missing ROS1 remaps, missing emergency stop, unresolved approval relay）。剩余缺口是 interactive fleet onboarding wizard 和 config migration flow。 |
| Realtime streaming | `StreamEvent`, `EventBus`, SSE endpoints, `TelemetryTracker`, cursor replay | 已实现 v1。已有 SSE 实时流、统一事件 schema、cursor-based reconnect；缺 WebSocket 支持。 |

## 阶段完成状态

### Phase 6：Mission Execution Control Plane — 已实现

- `MissionAgent.plan_and_submit()` 默认 `use_scheduler=True`，委托给 `MissionScheduler` 执行
- Execution group ordering 和 failure policy (retry/reassign/abort/escalate) 已接入主路径
- Mission HTTP Gateway 已实现 7 个 endpoint：submit、trace、events、cancel、approvals、fleet state、fleet doctor
- CLI `--use-scheduler` / `--no-use-scheduler` flag 已添加

### Phase 7：Gateway Method Scopes and Security Review — 已实现

- `method_scopes.py` 定义 22 个 endpoint descriptor，覆盖 robot-local、mission-level、enrollment
- 默认拒绝未分类 endpoint；admin bypass；write-implies-read 语义
- `X-Operator-Scopes` header 统一认证
- 安全评审文档：`docs/security/gateway-endpoint-security-review.md`

### Phase 8：Realtime Event Stream and Telemetry — 已实现

- `StreamEvent` 统一事件 envelope（event_id, event_type, source, timestamp, sequence, mission_id, robot_id, task_id, payload）
- `EventBus` 线程安全 in-process pub/sub，支持 event_type filtering
- Robot-local SSE: `GET /events/stream`（15s heartbeat，client disconnect detection）
- Mission-level SSE: `GET /missions/{id}/events/stream`（历史事件 + 实时流）
- `TelemetryTracker`：task latencies, action durations, cancel latencies, failure reasons, heartbeat ages
- Incident replay 支持 action-level events from EventLedger

### Phase 9：Real Robot / ROS1 Integration Proof — 已实现（本地 smoke proof）

- ROS1 smoke test infrastructure: session-scoped fixtures for roscore/turtlesim/fibonacci_server
- 6 个 smoke tests: infrastructure start, topic publish, service call, action goal, action cancel, action timeout
- 所有 smoke tests 使用 `@pytest.mark.ros1_smoke` 标记，默认全量测试跳过；需要 `FIRECLAW_RUN_ROS1_SMOKE=1` 显式启用
- YAML config examples: turtlesim_teleop, fibonacci_action, fireclaw_robot
- ROS2 adapter protocol boundary 定义（`Ros2AdapterProtocol`，无实现）
- Message introspection enhancement: `__slots__` support, better error messages, `validate_payload_against_type`
- ROS dict-to-message 转换: `_build_ros_message` 支持 topic message、service request、action goal 的递归构建
- Deployment guide: `docs/deployment/ros1-deployment-guide.md`

**剩余未来工作：** 真实机器人硬件 smoke proof（当前仅 ROS1 本地 roscore + tutorials stack）

### Phase 10：Memory and Plugin Runtime — 已实现 v1

- `SqliteMemoryIndex`: SQLite FTS5 索引，支持 text search + structured filters (mission_id, robot_id, floor, capability, outcome, operator, risk_level)
- `MissionMemoryStore` 可选集成 index；无 index 时回退到 JSONL keyword search
- Operator corrections 进入 planner context: `MissionPlannerContext` 新增 `retrieved_memories` 和 `operator_corrections` 字段
- LLM planner prompt 包含检索到的 memories/corrections（带 redaction）
- `FireClawPluginDescriptor` v1: capability, preconditions, risk_level, required_sensors, adapter_bindings, approval_scope, provider_hooks, memory_hooks
- `descriptor_from_skill_manifest()` 从现有 skill manifest 转换

**剩余未来工作：** embedding provider lifecycle、session transcript indexing 策略、retrieval 质量评估集

## 重要缺口（更新后）

### P1：插件/工具生态

v1 implemented: skill manifest, plugin descriptor v1, callable hook 注册/执行（provider/memory/tool_approval 三种 hook 类型，异常隔离，payload copy），hook policy enforcement，unknown plugin rejection，persistent audit trail，control-plane fingerprints。已接入 MissionAgent planner context 和 MissionGateway approval flow。Remaining gap is production automation: 第三方插件加载、安全沙箱、runtime activation boundaries。

### P1：记忆系统

v1 implemented: SQLite FTS, structured filters, rank-fusion `MemoryRetriever`, transcript ingestion API, `memory_eval.py` thresholded evaluation, doctor integration for retrieval health checks。Remaining gap is deployment proof: embedding provider lifecycle automation, session transcript indexing policy 集成 CI、retrieval 质量回归阈值。

### P1：TaskRegistry/SubagentRegistry lifecycle

v1 implemented: `project_task_state()`, `mark_terminal()`, `LifecycleReconciler`（mission-projected subtask matching, orphan detection, stale task detection），`LifecycleMaintenanceRunner`（explicit maintenance invocation with report），owner/session listing helpers。Remaining gap is production automation: 跨进程 reconciliation runner 和 orphan recovery 自动修复调度。

### P1：ApprovalRuntime 持久化和 relay

v1 implemented: JSONL-backed token hash 持久化（restart-resilient），relay-ready pending projection（channel + operator_id），`ApprovalRelay` Protocol + `InMemoryApprovalRelay`，`ConsoleApprovalRelay`，`WebhookApprovalRelay`（stdlib urllib, fail-safe timeout），idempotent delivery。Remaining gap is external adapter: 部署环境的实际 operator channel 适配器（如 specific webhook URL, operator UI integration）。

### P1：真实机器人硬件验证

ROS1 smoke proof runbook and artifact schema complete, execution pending hardware。未在真实消防机器人硬件上验证。

### P2：部署配置和 security hardening

v1 implemented: deployment checklist, ROS1 deployment guide, fleet doctor with onboarding report（enrolled/enabled robots, stale heartbeats, missing ROS1 remaps, missing emergency stop, unresolved approval relay）。Remaining gap is production automation: migration/repair flow、interactive fleet onboarding wizard、完整 secret handling policy。

## 推荐下一步

1. 真实 ROS1 机器人硬件 smoke test（需要物理机器人或高保真仿真环境）
2. 跨进程 lifecycle reconciliation runner（当前仅 in-process reconciliation；需要独立进程/容器级 orphan recovery 自动修复）
3. Memory retrieval 质量回归验证（embedding provider lifecycle、session transcript indexing policy、retrieval 质量回归阈值集成 CI）

### 以下为**平台可选工作**

以下项目超出当前 ROS1-first embodied roadmap 范围，仅在平台成熟度需要时推进：

- ROS2 native adapter（当前仅 protocol boundary 和实施计划）
- Full ACP/IDE session parity（OpenClaw control-plane 级别的 session lineage、task-flow registry、observer）
- 第三方插件 marketplace（sandbox loading、install policy、audit enforcement）
- Full operator web UI（mission SSE 接入、实时 dashboard、approval 交互）
- WebSocket 支持（当前仅 SSE + HTTP polling）
