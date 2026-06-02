# FireClaw Dry-Run Core Session

## 2026-06-02 10:18 CST

### Continued Work

Implemented the provider-agnostic LLM/tool-calling planner interface layer:

- Added `PlannerContext` to `src/fireclaw_core/planner.py`.
- Updated `RuleBasedPlanner.plan(command, context=None)` so existing deterministic planner remains compatible with richer planner context.
- Added `src/fireclaw_core/llm_planner.py`.
- Added `LLMToolCallingPlanner`, which:
  - sends command and context to an injected planning client;
  - validates structured planner responses;
  - converts valid responses into `PlanningResult`;
  - converts clarify responses into `PlanningResult(status="clarify")`;
  - falls back to `RuleBasedPlanner` when the client response is malformed or the client fails.
- Added `FireClawAgent(planner=...)` injection.
- Agent now builds and passes planner context containing:
  - `session_id`
  - `turn_index`
  - recent session records
  - skill metadata
- Updated README with the planner interface and future model-client boundary.

### Files Modified

- `src/fireclaw_core/planner.py`
- `src/fireclaw_core/llm_planner.py`
- `src/fireclaw_core/agent.py`
- `tests/test_planner.py`
- `tests/test_llm_planner.py`
- `tests/test_agent.py`
- `README.md`
- `docs/superpowers/specs/2026-06-02-llm-tool-calling-planner-interface-design.md`
- `docs/superpowers/plans/2026-06-02-llm-tool-calling-planner-interface.md`

### Verification

- `.venv/bin/python -m pytest tests/test_planner.py -v`: 11 passed.
- `.venv/bin/python -m pytest tests/test_llm_planner.py -v`: 3 passed.
- `.venv/bin/python -m pytest tests/test_agent.py -v`: 21 passed.
- `.venv/bin/python -m pytest -v`: 96 passed.
- `.venv/bin/python -m fireclaw_core "去二楼救人" --session-id llm-interface-demo --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned `status="succeeded"` with default rule-planner behavior intact.

### Current Conclusion

The LLM/tool-calling planner interface plan is complete. FireClaw now has an OpenClaw-like planner boundary that can accept future OpenAI, local model, or robotics planning clients without changing safety, execution, memory, or skill runtime code.

### Remaining Gaps

- No concrete OpenAI planner client yet.
- No prompt template or tool schema serialization beyond raw skill metadata.
- No streaming/planning trace.
- No planner confidence score or model error telemetry.
- No semantic memory retrieval for planner context yet.

## 2026-06-02 10:32 CST

### Continued Work

Implemented provider-agnostic planner tool schema serialization:

- Added `src/fireclaw_core/tool_schema.py`.
- Added `skill_metadata_to_tool_schema(...)`.
- Added `build_tool_schemas(...)`, sorted by tool/function name.
- Added `planner_response_schema()`.
- Added `build_planner_request(command, context)`.
- Updated `LLMToolCallingPlanner` to use the shared request builder.
- Planner request payload now includes:
  - `command`
  - `context`
  - `tools`
  - `response_schema`
  - `instructions`
- Tool schemas include permissive JSON object parameters and FireClaw-specific metadata under `x-fireclaw`.
- Updated README with planner tool schema and request shape.

### Files Modified

- `src/fireclaw_core/tool_schema.py`
- `src/fireclaw_core/llm_planner.py`
- `tests/test_tool_schema.py`
- `tests/test_llm_planner.py`
- `README.md`
- `docs/superpowers/specs/2026-06-02-planner-tool-schema-serialization-design.md`
- `docs/superpowers/plans/2026-06-02-planner-tool-schema-serialization.md`

### Verification

- `.venv/bin/python -m pytest tests/test_tool_schema.py -v`: 3 passed.
- `.venv/bin/python -m pytest tests/test_llm_planner.py tests/test_tool_schema.py -v`: 6 passed.
- `.venv/bin/python -m pytest -v`: 99 passed.
- `.venv/bin/python -m fireclaw_core "去二楼救人" --session-id schema-demo --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned `status="succeeded"` and default CLI behavior remained compatible.

### Current Conclusion

The planner tool schema serialization plan is complete. FireClaw now has a stable, auditable planner request payload that future OpenAI, local model, or ROS-side planner clients can adapt without changing the agent loop.

### Remaining Gaps

- No concrete model client yet.
- Tool input schemas are still generic because skills do not yet declare typed input schemas.
- No prompt versioning or schema versioning yet.
- No planning trace or model telemetry yet.

## 2026-06-02 10:38 CST

### Continued Work

Implemented skill input schema metadata:

- Added `input_schema` to `Skill`.
- Added generic default input schema:
  - `{"type": "object", "additionalProperties": true}`
- Added explicit built-in skill input schemas:
  - `navigate_to_floor`, `search_for_victims`, `assess_victim`, `report_status` require integer `floor`;
  - `return_to_safe_zone` accepts an empty object with no additional properties.
- Added `input_schema` to `SkillRegistry.list_metadata()`.
- Added manifest support for `input_schema`.
- Added manifest validation requiring `input_schema` to be an object schema with `type="object"`.
- Updated planner tool schema generation so `function.parameters` uses the declared skill `input_schema`.
- Updated `skills/examples/echo_policy.skill.json` with a permissive input schema.
- Updated README examples.

### Files Modified

- `src/fireclaw_core/skills.py`
- `src/fireclaw_core/skill_manifest.py`
- `src/fireclaw_core/tool_schema.py`
- `tests/test_execution.py`
- `tests/test_skill_manifest.py`
- `tests/test_tool_schema.py`
- `skills/examples/echo_policy.skill.json`
- `README.md`
- `docs/superpowers/specs/2026-06-02-skill-input-schema-design.md`
- `docs/superpowers/plans/2026-06-02-skill-input-schema.md`

### Verification

- `.venv/bin/python -m pytest tests/test_execution.py -v`: 13 passed.
- `.venv/bin/python -m pytest tests/test_skill_manifest.py -v`: 9 passed.
- `.venv/bin/python -m pytest tests/test_tool_schema.py tests/test_workspace_skill_example.py -v`: 5 passed.
- `.venv/bin/python -m pytest -v`: 103 passed.
- `.venv/bin/python -m fireclaw_core "你有哪些技能" --session-id input-schema-demo --memory-path /tmp/fireclaw-demo-memory.jsonl`: returned `status="skills"` and skill metadata included `input_schema`.

### Current Conclusion

The skill input schema plan is complete. FireClaw tool schemas now expose typed skill parameters instead of only generic objects, which makes future LLM planner calls more reliable and auditable.

### Remaining Gaps

- Executor does not yet validate inputs against `input_schema` before invoking skills.
- Manifest validation only performs shallow schema checks, not full JSON Schema validation.
- No schema versioning yet.
- No generated OpenAI-specific tool adapter yet.

## 2026-06-02 10:54 CST

### Continued Work

Implemented Memory Retrieval v1:

- Added `JsonlMemoryStore.search_records(...)` for deterministic JSONL search.
- Search filters currently support:
  - `session_id`
  - `status`
  - `intent`
  - `target_floor`
  - `command_contains`
  - `limit`
- Search returns newest matching records first.
- Added agent-level structured retrieval commands before planner execution.
- Retrieval commands can answer examples such as:
  - `之前二楼救人成功了吗`
  - `上次失败原因是什么`
  - `查一下二楼救人记录`
- Retrieval returns `status="retrieved"`.
- Retrieval is read-only:
  - no robot actions;
  - no planner execution;
  - no safety evaluation;
  - no new memory append.
- Fixed the local protocol boundary in `agent.py` so `latest_records` belongs to the memory store protocol, not the planner protocol.
- Updated CLI exit behavior so `retrieved` is treated as a successful command result.
- Updated README with structured retrieval examples.

### Files Modified

- `src/fireclaw_core/memory.py`
- `src/fireclaw_core/agent.py`
- `src/fireclaw_core/__main__.py`
- `tests/test_memory.py`
- `tests/test_agent.py`
- `tests/test_cli.py`
- `README.md`
- `docs/superpowers/specs/2026-06-02-memory-retrieval-v1-design.md`
- `docs/superpowers/plans/2026-06-02-memory-retrieval-v1.md`

### Verification

- `.venv/bin/python -m pytest tests/test_memory.py -v`: 6 passed.
- `.venv/bin/python -m pytest tests/test_agent.py -v`: 24 passed.
- `.venv/bin/python -m pytest tests/test_cli.py -v`: 15 passed.
- `.venv/bin/python -m pytest -v`: 109 passed.
- Manual CLI sequence:
  - `.venv/bin/python -m fireclaw_core "去二楼救人" --session-id retrieval-demo --memory-path /tmp/fireclaw-demo-memory-retrieval.jsonl`
  - `.venv/bin/python -m fireclaw_core "之前二楼救人成功了吗" --session-id retrieval-demo --memory-path /tmp/fireclaw-demo-memory-retrieval.jsonl`
- Manual CLI retrieval returned:
  - `status="retrieved"`
  - `message="找到 1 条匹配记忆记录。"`
  - query `{session_id: retrieval-demo, status: succeeded, intent: rescue_victim, target_floor: 2, command_contains: null, limit: 5}`
  - `planning=null`, `safety=null`, `execution=null`

### Current Conclusion

Memory Retrieval v1 is complete. FireClaw now has an OpenClaw-like explicit memory read path, but adapted to robotics auditability: retrieval is structured, scoped by session, read-only, and cannot accidentally trigger robot-side skills.

### Remaining Gaps

- Retrieval is still rule/template based, not semantic.
- No vector index, embeddings, ranking, or summarization yet.
- Failure-cause answers return full matching records rather than a concise natural-language explanation.
- Query parsing only covers a minimal Chinese command set.
- No cross-session operator preference retrieval yet.
- No memory compaction or long-term lesson extraction yet.

## 2026-06-02 11:07 CST

### Continued Work

Implemented Operator Confirmation / Safety Workflow v1:

- Added skill `risk_level` metadata with allowed values:
  - `low`
  - `medium`
  - `high`
  - `critical`
- Added `risk_level` to:
  - `Skill`
  - `SkillRegistry.list_metadata()`
  - subprocess skill manifests
  - planner tool schema `x-fireclaw` metadata
- Added manifest validation for invalid `risk_level` values.
- Extended `SafetyGate.evaluate(...)` with `operator_confirmed=False`.
- Added a new soft safety decision:
  - `SafetyDecision(status="require_confirmation", reasons=[...])`
- Confirmation is required when:
  - a skill is otherwise allowed for non-dry-run real robot execution;
  - a skill has `risk_level="high"` or `risk_level="critical"`.
- Hard safety failures still block and cannot be bypassed by confirmation:
  - missing skills;
  - missing required sensors;
  - unsafe retry metadata;
  - dry-run-only / real-robot allowance conflicts.
- Added agent-level pending confirmation workflow:
  - first turn returns `status="awaiting_confirmation"`;
  - pending plan is appended to JSONL memory;
  - no skill execution occurs while pending;
  - `确认执行` restores the latest unresolved pending plan from current-session memory and executes it with `operator_confirmed=True`;
  - `取消` records `status="cancelled"` and does not execute skills;
  - `确认执行` with no pending plan returns `status="clarify"`.
- CLI now treats `awaiting_confirmation` and `cancelled` as successful command results.
- README now documents risk metadata and the pending/confirm/cancel workflow.

### Files Modified

- `src/fireclaw_core/skills.py`
- `src/fireclaw_core/skill_manifest.py`
- `src/fireclaw_core/safety.py`
- `src/fireclaw_core/agent.py`
- `src/fireclaw_core/__main__.py`
- `src/fireclaw_core/tool_schema.py`
- `tests/test_safety.py`
- `tests/test_skill_manifest.py`
- `tests/test_execution.py`
- `tests/test_agent.py`
- `tests/test_cli.py`
- `tests/test_llm_planner.py`
- `tests/test_tool_schema.py`
- `README.md`
- `docs/superpowers/specs/2026-06-02-operator-confirmation-safety-workflow-v1-design.md`
- `docs/superpowers/plans/2026-06-02-operator-confirmation-safety-workflow-v1.md`

### Verification

- `.venv/bin/python -m pytest tests/test_safety.py tests/test_skill_manifest.py tests/test_execution.py -v`: 39 passed.
- `.venv/bin/python -m pytest tests/test_agent.py -v`: 28 passed.
- `.venv/bin/python -m pytest tests/test_cli.py -v`: 16 passed.
- `.venv/bin/python -m pytest -v`: 120 passed.
- Manual CLI confirmation demo:
  - created temporary high-risk `smoke_entry` subprocess skill under `/tmp/fireclaw-confirm-skills`;
  - ran `.venv/bin/python -m fireclaw_core "运行 smoke_entry" --session-id confirm-demo --skills-dir /tmp/fireclaw-confirm-skills --memory-path /tmp/fireclaw-confirm-memory.jsonl`;
  - first output returned `status="awaiting_confirmation"`, `safety.status="require_confirmation"`, `execution=null`, `confirmation.status="pending"`;
  - ran `.venv/bin/python -m fireclaw_core "确认执行" --session-id confirm-demo --skills-dir /tmp/fireclaw-confirm-skills --memory-path /tmp/fireclaw-confirm-memory.jsonl`;
  - confirmation output returned `status="succeeded"`, `confirmation.status="confirmed"`, `pending_turn_index=1`, and executed `smoke_entry`.

### Current Conclusion

Operator Confirmation / Safety Workflow v1 is complete. FireClaw now has an OpenClaw-like control-plane checkpoint for safety-critical plans, adapted to firefighting robots by keeping hard safety blocks separate from operator-confirmable risks and by recording pending, confirmed, and cancelled decisions in append-only memory.

### Remaining Gaps

- No operator identity, authentication, or authorization yet.
- No signed plan hash or tamper-evident confirmation record yet.
- No timeout or expiry for stale pending plans.
- No emergency-stop state integration.
- Confirmation command parsing is minimal and Chinese-template based.
- Real robot adapters are still not implemented; non-dry-run confirmation only applies to skill metadata and test doubles today.

## 2026-06-02 15:13 CST

### Continued Work

Implemented ROS2 / Simulator Adapter v1:

- Added structured adapter state dataclasses:
  - `RobotState`
  - `EnvironmentState`
- Extended the `RobotAdapter` protocol with:
  - `get_robot_state()`
  - `get_environment_state()`
- Added state snapshot methods to:
  - `DryRunRobotAdapter`
  - `MockRos2RobotAdapter`
- Added `SimulatorRobotAdapter`.
- Simulator behavior:
  - tracks `current_floor`;
  - supports configured `reachable_floors`;
  - supports configured `victims_by_floor`;
  - reports deterministic victim search results;
  - fails navigation to unreachable floors without changing current floor;
  - returns `mode="simulator"` in action outputs.
- Extended `SafetyGate.evaluate(...)` with optional:
  - `robot_state`
  - `environment_state`
- Added hard safety blocks for:
  - robot offline;
  - battery below 10%;
  - target floor not reachable in environment state.
- Agent now captures pre-execution `robot_state` and `environment_state` snapshots.
- Agent passes state snapshots into the safety gate.
- Agent result and JSONL memory records now include state snapshots for audit/replay.
- CLI now supports:
  - `--adapter dry-run`
  - `--adapter simulator`
  - `--adapter mock-ros2`
- README documents adapter selection, simulator usage, and state snapshot semantics.

### Files Modified

- `src/fireclaw_core/robot.py`
- `src/fireclaw_core/safety.py`
- `src/fireclaw_core/agent.py`
- `src/fireclaw_core/__main__.py`
- `tests/test_robot.py`
- `tests/test_safety.py`
- `tests/test_agent.py`
- `tests/test_cli.py`
- `README.md`
- `docs/superpowers/specs/2026-06-02-ros2-simulator-adapter-v1-design.md`
- `docs/superpowers/plans/2026-06-02-ros2-simulator-adapter-v1.md`

### Verification

- `.venv/bin/python -m pytest tests/test_robot.py -v`: 8 passed.
- `.venv/bin/python -m pytest tests/test_safety.py -v`: 17 passed.
- `.venv/bin/python -m pytest tests/test_agent.py tests/test_cli.py -v`: 46 passed.
- `.venv/bin/python -m pytest -v`: 129 passed.
- Manual CLI simulator demo:
  - `.venv/bin/python -m fireclaw_core "去二楼救人" --adapter simulator --session-id sim-demo --memory-path /tmp/fireclaw-sim-memory.jsonl`
  - returned `status="succeeded"`;
  - execution step outputs used `mode="simulator"`;
  - result included `robot_state.mode="simulator"`;
  - result included `environment_state.reachable_floors=[1,2,3]` and `victims_by_floor={"2": 1}`.

### Current Conclusion

ROS2 / Simulator Adapter v1 is complete. FireClaw now has a more robotics-shaped adapter boundary: adapters expose action methods plus state snapshots, safety can block based on robot/environment state, and CLI can run through a deterministic simulator without importing ROS2 or touching real hardware.

### Remaining Gaps

- No real ROS2 node/client implementation yet.
- No simulator configuration file or scenario loader yet.
- No map graph, path planner, occupancy grid, or building topology model yet.
- Simulator state is process-local, so CLI invocations do not persist simulator world state across runs.
- Agent records pre-execution snapshots only; it does not yet record post-execution state snapshots.
- Safety thresholds such as battery percentage are hard-coded.

## 2026-06-02 15:41 CST

### Continued Work

Implemented FireClaw Gateway v1:

- Added `src/fireclaw_core/gateway.py`.
- Added a local-first HTTP control plane around the existing `FireClawAgent`.
- Gateway direction is:
  - HTTP in -> FireClawAgent -> SkillExecutor -> RobotAdapter -> simulator / mock ROS2 / future real ROS2.
- Added `GatewayConfig`.
- Added `FireClawGateway`.
- Gateway uses Python standard library `ThreadingHTTPServer`; no new web framework dependency.
- Gateway binds to `127.0.0.1` by default.
- Added shared runtime adapter factory:
  - `src/fireclaw_core/runtime_config.py`
  - `ADAPTER_CHOICES`
  - `create_robot_adapter(...)`
- Updated CLI to use shared adapter factory.
- Gateway endpoints:
  - `GET /health`
  - `GET /state`
  - `GET /skills`
  - `GET /memory/recent?session_id=<id>&limit=<n>`
  - `POST /tasks`
  - `POST /confirm`
  - `POST /cancel`
- Gateway keeps one resident robot adapter per process.
- Gateway creates request-scoped `FireClawAgent` instances over the shared robot and memory store, allowing per-request `session_id`.
- `POST /tasks` accepts natural-language commands and routes them through existing planner/safety/executor/memory logic.
- `POST /confirm` maps to `确认执行`.
- `POST /cancel` maps to `取消`.
- README now documents Gateway startup, task submission, confirmation/cancel, state, skills, and memory endpoints.

### Files Modified

- `src/fireclaw_core/gateway.py`
- `src/fireclaw_core/runtime_config.py`
- `src/fireclaw_core/__main__.py`
- `tests/test_gateway.py`
- `README.md`
- `docs/superpowers/specs/2026-06-02-fireclaw-gateway-v1-design.md`
- `docs/superpowers/plans/2026-06-02-fireclaw-gateway-v1.md`

### Verification

- `.venv/bin/python -m pytest tests/test_cli.py -v`: 17 passed.
- `.venv/bin/python -m pytest tests/test_gateway.py -v`: 4 passed.
- `.venv/bin/python -m pytest -v`: 133 passed.
- Manual Gateway demo:
  - started `.venv/bin/python -m fireclaw_core.gateway --host 127.0.0.1 --port 8765 --adapter simulator --robot-id robot-demo --memory-path /tmp/fireclaw-gateway-demo.jsonl`;
  - `curl http://127.0.0.1:8765/health` returned `status="ok"`, `robot_id="robot-demo"`, `adapter="simulator"`;
  - `curl -X POST http://127.0.0.1:8765/tasks -H 'Content-Type: application/json' -d '{"command":"去二楼救人","session_id":"manual-demo"}'` returned `status="succeeded"` and simulator execution outputs.
- Manual Gateway process was stopped with Ctrl-C after verification.

### Current Conclusion

FireClaw now has the first OpenClaw-like Gateway/control-plane layer. It is still local and minimal, but it changes the system shape from one-shot CLI execution to a robot-side resident HTTP service that can receive natural-language tasks and route them through the existing agent core.

### Remaining Gaps

- No authentication or operator identity yet.
- No request ids, trace ids, or structured event ledger yet.
- No streaming task progress endpoint yet.
- No daemon/service installer yet.
- No remote exposure safety runbook yet.
- No real ROS2 adapter behind the gateway yet.
- No multi-robot fleet routing; one gateway process still represents one robot adapter.

## 2026-06-02 15:49 CST

### Continued Work

Implemented Gateway Event Ledger / Task Trace v1:

- Added `src/fireclaw_core/event_ledger.py`.
- Added append-only JSONL event records.
- Event shape includes:
  - `event_id`
  - `task_id`
  - `session_id`
  - `type`
  - `timestamp`
  - `payload`
- Added `EventLedger.append(...)`.
- Added `EventLedger.list_events()`.
- Added `EventLedger.events_for_task(...)`.
- Added `EventLedger.latest_events(...)`.
- Extended `GatewayConfig` with `event_path`.
- Gateway now creates one `task_id` for each:
  - `POST /tasks`
  - `POST /confirm`
  - `POST /cancel`
- Gateway task/confirm/cancel responses include `task_id`.
- Gateway records derived events from completed agent results.
- First event types:
  - `task.received`
  - `task.planned`
  - `safety.decided`
  - `confirmation.pending`
  - `confirmation.confirmed`
  - `skill.succeeded`
  - `skill.failed`
  - `task.completed`
  - `task.cancelled`
- Added HTTP endpoints:
  - `GET /tasks/<task_id>`
  - `GET /tasks/<task_id>/events`
  - `GET /events/recent?session_id=<id>&limit=<n>`
- README now documents `--event-path`, `task_id`, event types, and trace endpoints.

### Files Modified

- `src/fireclaw_core/event_ledger.py`
- `src/fireclaw_core/gateway.py`
- `tests/test_event_ledger.py`
- `tests/test_gateway.py`
- `README.md`
- `docs/superpowers/specs/2026-06-02-gateway-event-ledger-v1-design.md`
- `docs/superpowers/plans/2026-06-02-gateway-event-ledger-v1.md`

### Verification

- `.venv/bin/python -m pytest tests/test_event_ledger.py -v`: 4 passed.
- `.venv/bin/python -m pytest tests/test_gateway.py -v`: 4 passed.
- `.venv/bin/python -m pytest -v`: 137 passed.
- Manual Gateway event demo:
  - started Gateway on `127.0.0.1:8766` with simulator adapter and explicit `--event-path`;
  - submitted `POST /tasks` with `{"command":"去二楼救人","session_id":"event-demo-session"}`;
  - response included `task_id=task-1df017941b414e778db62e46108d1da3`;
  - queried `GET /tasks/<task_id>/events`;
  - returned events included `task.received`, `task.planned`, `safety.decided`, five `skill.succeeded` events, and `task.completed`.
- Manual Gateway process was stopped after verification.

### Current Conclusion

Gateway Event Ledger v1 is complete. FireClaw Gateway now has task ids and inspectable task traces, which makes it closer to OpenClaw's control-plane style and gives a foundation for future progress streaming, UI monitoring, ROS2 long-running action tracking, and incident replay.

### Remaining Gaps

- Events are derived after `agent.run(...)` completes; there is no live event streaming yet.
- No executor callback events for `skill.started` yet.
- No request id / trace id propagation yet.
- No operator identity or auth binding in events.
- No event compaction, retention policy, or indexing beyond JSONL scans.

## 2026-06-02 15:51 CST

### Final Check

- Removed one duplicated README line in the Gateway inspection section.
- Re-ran `.venv/bin/python -m pytest -q`: 137 passed in 2.96s.
- Rechecked `docs/superpowers/plans`: no unchecked `- [ ]` items found.
- Rechecked long-running processes: no active `pytest` or `fireclaw_core.gateway` process remained after the checks.
- `git status --short` still reports the repository contents as untracked root-level directories/files, consistent with earlier observations in this project.

## 2026-06-02 16:08 CST

### Continued Work

Implemented Gateway Live Progress / Streaming v1.

The goal was to move from "derive skill events after `agent.run(...)` finishes" to "emit and persist task progress while the agent is executing." The first version uses a synchronous event callback and keeps HTTP polling as the transport. No WebSocket, SSE, async task queue, or ROS2 feedback subscription was added in this phase.

### Files Modified

- `src/fireclaw_core/executor.py`
- `src/fireclaw_core/agent.py`
- `src/fireclaw_core/gateway.py`
- `tests/test_execution.py`
- `tests/test_agent.py`
- `tests/test_gateway.py`
- `README.md`
- `docs/superpowers/specs/2026-06-02-gateway-live-progress-streaming-v1-design.md`
- `docs/superpowers/plans/2026-06-02-gateway-live-progress-streaming-v1.md`

### Implementation Details

- Added `ExecutionEventSink = Callable[[str, dict[str, Any]], None]`.
- `PlanExecutor` now accepts an optional `event_sink`.
- `PlanExecutor` emits:
  - `skill.started`
  - `skill.attempted`
  - `skill.succeeded`
  - `skill.failed`
- Event sink exceptions are swallowed so observability failures do not mask robot skill execution.
- `FireClawAgent` now accepts an optional `event_sink`.
- `FireClawAgent` emits:
  - `task.planned`
  - `safety.decided`
- Gateway creates a task-scoped sink that writes live events to `EventLedger` with the current `task_id` and `session_id`.
- Gateway result-event derivation now skips duplicate `task.planned`, `safety.decided`, and final skill events when live events are already present.
- README now documents polling live progress through:
  - `GET /tasks/<task_id>/events`
  - `GET /events/recent?session_id=<id>&limit=<n>`

### Verification So Far

- `.venv/bin/python -m pytest tests/test_execution.py -q`: 16 passed.
- `.venv/bin/python -m pytest tests/test_agent.py -q`: 30 passed.
- `.venv/bin/python -m pytest tests/test_gateway.py -q`: 4 passed.
- `.venv/bin/python -m pytest -q`: 140 passed in 3.42s.
- `rg -n "^- \[ \]" docs/superpowers/plans || true`: no unchecked plan steps found.
- `ps -ef | rg 'pytest|fireclaw_core.gateway|fireclaw_core' || true`: no active Gateway or pytest process after verification; output only showed the check command itself.

### Current Conclusion

Gateway now has a real live-progress event substrate. A client can poll task events while execution is ongoing and see planning, safety, skill start, attempt, success, failure, and final task events in audit order.

### Remaining Gaps

- Still no async task submission endpoint; current HTTP requests block until completion.
- Still no SSE/WebSocket streaming endpoint.
- Still no executor-level in-flight cancellation.
- Still no ROS2 action feedback mapping into intermediate progress percentages or action states.

## 2026-06-02 16:32 CST

### Continued Work

Implemented Operator Console Projection v1 after re-checking OpenClaw's human-facing chat layer with CodeGraph.

CodeGraph findings from `openclaw-main`:

- `src/tui/gateway-chat.ts:189`: `GatewayChatClient.sendChat(...)` sends `chat.send` and returns a `runId`.
- `src/gateway/server-methods/chat.ts:2291`: `chatHandlers["chat.send"]` is the structured Gateway chat entrypoint.
- `src/gateway/chat-abort.ts:74`: `registerChatAbortController(...)` tracks in-flight runs by `runId`.
- `src/gateway/server-methods/chat.ts:2029`: `broadcastChatFinal(...)` broadcasts structured final chat events.
- `src/infra/agent-events.ts:209`: `emitAgentEvent(...)` emits sequenced agent/tool/lifecycle events.
- `src/tui/tui-event-handlers.ts:57`: `createEventHandlers(...)` projects structured events into TUI state.
- `src/tui/components/chat-log.ts:266`: `ChatLog.updateAssistant(...)` updates human-facing assistant text.
- `src/tui/components/chat-log.ts:324`: `ChatLog.startTool(...)` turns tool events into a tool execution component.

The FireClaw implementation follows that pattern at a smaller Python scale: structured Gateway/EventLedger data remains machine-facing, while a new projector and console produce Chinese operator progress text.

### Files Modified

- `src/fireclaw_core/operator_projection.py`
- `src/fireclaw_core/operator_console.py`
- `tests/test_operator_projection.py`
- `tests/test_operator_console.py`
- `README.md`
- `docs/superpowers/specs/2026-06-02-operator-console-projection-v1-design.md`
- `docs/superpowers/plans/2026-06-02-operator-console-projection-v1.md`
- `memory/2026-06-02/fireclaw-dry-run-core.md`

### Implementation Details

- Added `OperatorEventProjector.project(event)` returning `str | None`.
- Projector maps these structured events into Chinese:
  - `task.received`
  - `task.planned`
  - `safety.decided`
  - `confirmation.pending`
  - `confirmation.confirmed`
  - `skill.started`
  - `skill.attempted`
  - `skill.failed`
  - `task.completed`
  - `task.cancelled`
- Added rescue-specific skill text for:
  - `navigate_to_floor`
  - `search_for_victims`
  - `assess_victim`
  - `report_status`
  - `return_to_safe_zone`
- Added `run_operator_command(...)`, which runs `FireClawGateway.run_agent(...)` in a background thread, polls EventLedger, and prints new projected messages.
- Added CLI:
  - `.venv/bin/python -m fireclaw_core.operator_console "去二楼救人" --adapter simulator`

### Verification So Far

- `.venv/bin/python -m pytest tests/test_operator_projection.py -q`: 3 passed.
- `.venv/bin/python -m pytest tests/test_operator_console.py -q`: 1 passed.
- `.venv/bin/python -m pytest tests/test_operator_projection.py tests/test_operator_console.py -q`: 4 passed.
- `.venv/bin/python -m pytest -q`: 144 passed in 3.79s.
- `rg -n "^- \[ \]" docs/superpowers/plans || true`: no unchecked plan steps found.
- `ps -ef | rg 'pytest|fireclaw_core.operator_console|fireclaw_core.gateway|fireclaw_core' || true`: no active pytest, operator console, or Gateway process remained; output only showed the check command itself.
- Manual CLI run:
  - `.venv/bin/python -m fireclaw_core.operator_console "去二楼救人" --adapter simulator --robot-id operator-demo --session-id operator-demo --memory-path /tmp/fireclaw-operator-demo-memory.jsonl --event-path /tmp/fireclaw-operator-demo-events.jsonl --no-workspace-skills --poll-interval 0.001`
  - Output showed Chinese progress lines from task receipt through completion.

### Current Conclusion

FireClaw now has the missing human projection layer. Operators no longer need to inspect JSON for the basic rescue flow; JSON remains available for machines, audit logs, ROS2 integrations, and future UI clients.

### Remaining Gaps

- Operator console is line-oriented, not full-screen TUI.
- Operator console uses local in-process Gateway rather than a remote Gateway client.
- Gateway HTTP `/tasks` is still synchronous; true OpenClaw-style accepted/background run remains the next architectural step.
- No SSE/WebSocket or ROS2 action feedback projection yet.
