# FireClaw 架构回顾与下一阶段建议

## 时间

- 2026-08-09T15:53:41+08:00

## 任务目标

回顾最近完成的工作、核对当前仓库真实状态、重新梳理 FireClaw 端到端架构，
并判断下一阶段应继续补平台能力，还是转向 ROS/Gazebo 系统验证与研究评测。

## 用户要求与偏好

- 用户希望先回顾之前的内容和整体架构，再决定下一步。
- 继续遵循 OpenClaw-first：有上游对应形状时先复核 OpenClaw，再保留 FireClaw
  的机器人安全、ROS、状态不确定性、执行证据和多机器人差异。
- 用户面向研究型消防机器人系统；结论必须区分工程正确性、研究有效性和
  论文级贡献，不能把通过单元测试等同于形成研究贡献。

## 恢复与仓库状态

按根 `AGENTS.md` 先读取了最近两个日期目录：

- `memory/2026-07-31/`
- `memory/2026-07-30/`

为避免重复已经做过的 Gazebo 工作，又定向读取了近期记录指向且与本次路线
判断直接相关的历史记录：

- `memory/2026-06-16/fireclaw-simple-planned-motion-gazebo-debug.md`
- `memory/2026-06-16/fireclaw-gazebo-mission-debug-run.md`
- `memory/2026-07-29/turtlebot3-noetic-import.md`
- `memory/2026-07-29/active-observation-and-context-evaluation.md`

开始本次记录前的 Git 状态：

- branch: `master`
- `HEAD` and `origin/master`: `a0ae6718d509aa8466a9ff24cbac0a7104f694e6`
- subject: `feat: move agent and physical tools into plugins`
- worktree/staging area: clean
- previous commits relevant to this review:
  - `29fe51b feat: harden agent runtime and gateway security`
  - `3daac5b feat: harden agent runtime and add ROS simulation stack`
  - `319c381 feat: unify mission and robot agent planning runtime`

The final July 31 memory entry said commit/push was still pending, but current Git state is
authoritative: the work was committed and pushed at 2026-07-31 19:17 +08.

## 已完成能力回顾

### Mission Coordinator

- `MissionGateway` authenticates/admit-checks operator requests and submits scheduler-backed
  missions to `MissionRunManager` by default.
- Mission planning has a bounded multi-turn loop, frozen snapshots, belief projection,
  advisory RAG, host-validated active observations, semantic task graphs, deterministic
  compilation/validation, scheduling, checkpoints, revision, and final reports.
- Background Mission Runs support query, pause, resume, correction, cancellation, terminal
  aggregation, and final report generation.
- Canonical Robot outcomes (`completed`, `blocked`, `escalated`, `failed`, `timed_out`,
  `cancelled`, and infrastructure `lost`) propagate through Robot Gateway, scheduler,
  Mission Registry, monitoring, and reports without turning a blocked task into success.

### Robot Agent

- Each robot owns `FireClawGateway + FireClawAgent` and remains authoritative for local
  physical execution.
- Structured tasks pass planning/tool projection, capability policy, `SafetyGate`, exact
  authorization, resource leases, action runtime, evidence validation, terminal persistence,
  and audit/memory recording.
- Non-physical `AgentToolRuntime` output remains advisory and cannot overwrite authoritative
  sensor/snapshot state.

### Plugin / Skill / Tool boundary

- Generic manifest-first extension discovery and activation is implemented in
  `src/fireclaw_core/plugin/extension_loader.py`.
- `FireClawPluginHost` owns plugin identity, contributions, conflicts, atomic activation,
  rollback, diagnostics, ownership, and disposal.
- Public `fireclaw_plugin_sdk` exposes `ToolSpec`, `PhysicalToolSpec`, and registration
  protocols without requiring third-party providers to import `fireclaw_core` internals.
- First-party extension manifests currently exist for:
  - `computer-tools`
  - `ros1-diagnostics`
  - `navigation-move-base`
  - `robot-legacy-physical`
- Concrete Tool schemas/handlers are extension-owned; core keeps policy, lifecycle,
  projection, safety, execution, and compatibility bridges.

### ROS/navigation and security

- Robot-local typed ROS diagnostics expose bounded read-only topic/TF/move_base checks.
- The move_base Plugin owns `navigate_to_point`, status, typed parameter catalog/get/set,
  cancel, and costmap-clear Tools. In ROS1 mode its entrypoint constructs
  `Ros1MoveBaseBackend` directly rather than calling a same-named core Adapter method.
- Gateway authentication, TLS/mTLS transport, Host/Origin/body/connection/SSE budgets,
  sandboxed legacy process Tools, canonical runtime/workspace paths, Docker lifecycle,
  response bounds, exact one-time authorization, and deployment security audit have been
  implemented and regression-tested.

## OpenClaw analogue reviewed

Recent work and this review checked the upstream shapes for:

- plugin registry, `registerTool`, manifest-first discovery, activation transactions;
- bounded agent/tool/result continuation;
- Gateway authentication before method authorization;
- normalized `AgentRunTerminalOutcome` construction and merge.

FireClaw correctly reuses those control-plane shapes. The deliberately FireClaw-specific
parts are structured Mission-to-Robot delegation, physical Tool separation, ROS backends,
live-state capability checks, `SafetyGate`, exact action authorization, resource leases,
completion evidence, and embodied terminal outcomes.

## 当前关键问题

### 1. 插件迁移后的真实导航主链尚未做 Gazebo E2E

Existing evidence is split across older architectures:

- 2026-06-16 proved a deterministic Mission -> Robot Gateway -> legacy ROS Adapter ->
  move_base success, using floor-oriented compatibility behavior.
- 2026-07-29 proved the pinned TurtleBot3/Gazebo/navigation stack, TF, costmaps, DWA, and a
  direct actionlib goal at the current absolute map pose.
- 2026-07-31 moved `navigate_to_point` ownership into the Navigation Plugin and stopped the
  production Agent from auto-scanning Adapter methods.

There is no recorded live Gazebo proof after that final ownership migration. The current
unit tests use an in-memory/injected backend, so they do not prove the new plugin-owned
`Ros1MoveBaseBackend` against a live `/move_base` action.

### 2. `gazebo_smoke.py` is preparation-only

`src/fireclaw_core/ros/gazebo_smoke.py` only creates an output directory and `robots.json`,
then prints `status=prepared`. It does not start or supervise Gazebo/navigation/Gateways,
submit a mission, poll terminal state, invoke diagnostics, verify cancellation, or produce a
pass/fail proof bundle. Its name and description currently overstate what it verifies.

### 3. Embodied evaluation assets are stale and can report misleading success

`src/fireclaw_core/devtools/embodied_eval.py` and its fixtures predate the current spatial,
terminal, scheduler, and plugin contracts:

- fixtures still use `去二楼救人`, `expected_floor=2`, while current scope is one-floor 2D
  `map` navigation and floor fields are compatibility-only;
- `ScenarioPlanner` is deterministic and fixture-driven, so the harness does not measure
  LLM planning quality;
- submission forces `use_scheduler=False`, bypassing the current default scheduler,
  background Mission Run, checkpoint/revision, and final-report path;
- terminal mission recognition includes only `succeeded/completed/failed`, omitting current
  `blocked/escalated/timed_out/cancelled/lost` outcomes;
- `dispatch_success` is true for any terminal subtask, including failed/blocked/lost;
- `min_memory_records` is present in fixtures but is not enforced in scenario pass/fail;
- the generated doctor report is derived from eval completion, not an actual ROS/Gateway
  readiness probe.

Therefore the current simulator eval is useful as a legacy HTTP smoke fixture, but it cannot
support publication-level claims and should not be used as the primary acceptance gate.

### 4. Background Mission Run control is process-local

`MissionRunManager` stores `_runs` and `MissionRunControl` only in memory. Registry events,
task checkpoints, outcomes, and final reports are persistent, but a coordinator restart does
not reconstruct active Run state, pause/cancel intent, or the Run query/control surface.
This matters for long missions and fault-injection claims.

### 5. Domain capability depth is much thinner than platform depth

The Navigation Plugin has a real ROS runtime boundary. In contrast, the first-party
`robot-legacy-physical` victim search/assessment/reporting contracts delegate to the legacy
`robot_action_dispatch` service. The TurtleBot3 ROS config maps `search_for_victims` to
publishing the string `search_current_area`; it does not implement a victim detector or
return evidence from an actual perception algorithm.

The framework therefore has strong agent/control infrastructure but does not yet have a
credible firefighting perception or rescue capability. A successful legacy topic publish
must not be presented as successful victim detection.

### 6. ROS diagnostic evidence is still robot-local only

Typed local diagnostics exist, but the planned Mission-to-Robot diagnostic request,
evidence lookup, bounded raw evidence archive, anomaly-triggered rolling buffer,
source-time/freshness calibration, privacy classification, and multi-robot namespace policy
are not complete.

### 7. Third-party Plugin execution is not an untrusted-code boundary

First-party/trusted Python plugins run in-process. Signatures/digests, independent plugin
processes, and a publish/version compatibility policy for the SDK remain future deployment
work. This should be deferred until a real need to install third-party executable plugins;
it is not the first blocker for the current research loop.

## Engineering correctness assessment

- The architecture is coherent and the major boundaries are explicit.
- The worktree is clean and focused current-architecture regression passed:

```text
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_extension_loader.py \
  tests/test_plugin_sdk.py \
  tests/test_move_base_navigation_plugin.py \
  tests/test_mission_run.py \
  tests/test_robot_terminal_outcome.py

34 passed in 0.43s
```

- The latest recorded full suite for commit `a0ae671` is `2013 passed, 7 skipped`; this
  review did not rerun the complete socket-dependent suite because no runtime code changed.
- The largest engineering risk is no longer unit correctness. It is integration drift among
  the current single-floor contract, plugin-owned ROS execution, Mission background lifecycle,
  and legacy evaluation fixtures.

## Research validity assessment

Current code is a strong systems substrate, but most evidence still demonstrates component
correctness. A defensible research question should be narrowed to one mechanism whose effect
can be measured. The most promising near-term direction is hierarchical, safety-bounded
diagnosis and recovery:

```text
compact authoritative snapshot
-> Robot-local typed diagnostics
-> structured diagnosis + evidence references
-> bounded recovery/replan
-> operator escalation when uncertainty remains
```

This can be compared against:

1. no diagnostic Agent (timeout/abort only);
2. structured summaries only;
3. hierarchical summary plus on-demand evidence;
4. unrestricted/raw ROS access as an unsafe/high-cost reference, if isolated for evaluation;
5. central-only diagnosis versus robot-local diagnosis.

Metrics should include task success, recovery success, diagnosis accuracy, recovery latency,
unsafe action proposal rate, collision/near-collision count, operator interventions,
bandwidth, storage, model tokens, stale-state errors, and incident reconstructability.

## Publication-level assessment

The current contribution is not yet a top-tier robotics/AI paper by architecture breadth
alone. Pluginization, Gateway security, memory plumbing, and Tool schemas are valuable
engineering but are individually incremental. A publishable claim needs:

- a precise failure/uncertainty problem;
- a method beyond generic ReAct/tool calling;
- controlled baselines and ablations;
- repeated scenarios/seeds, confidence intervals, and failure analysis;
- high-fidelity simulation and at least limited real-robot transfer evidence;
- safety/fault injection showing why the boundaries change outcomes.

The hierarchical diagnostic-evidence/recovery loop could become a conference-level systems
contribution if it demonstrably improves recovery and safety under bandwidth/context limits.
Without those experiments, the repository is better described as a substantial engineering
framework or workshop/demo platform.

## Recommended execution order

### P0 — Build the current-architecture Gazebo acceptance lane

1. Replace the preparation-only smoke path with a trusted operator-side harness/runbook.
2. Launch the pinned TurtleBot3 world, navigation stack, Robot Gateway, and Mission Gateway.
3. Use a single-floor absolute `map` target and the manifest-loaded
   `fireclaw.navigation.move-base` physical `navigate_to_point` Tool.
4. Prove success, feedback, cancel, timeout, terminal propagation, final report, event/audit
   artifacts, and Plugin owner ID.
5. Add one deterministic blocked/stall case and call `navigation_diagnostics` before recovery
   or escalation.

Acceptance must fail if execution silently falls back to a same-named
`Ros1RobotAdapter.navigate_to_point` compatibility method.

### P0 — Repair the embodied evaluation contract

1. Replace floor fixtures with point/area/entity targets.
2. Use the scheduler-backed background Mission Run path.
3. Recognize every canonical terminal outcome.
4. Define `dispatch_success` and mission success semantically, not as “any terminal state.”
5. Enforce completion evidence and memory thresholds.
6. Separate deterministic integration smoke, LLM planning evaluation, and ROS/Gazebo system
   evaluation into distinct suites.
7. Record seeds, model/provider, prompt/Tool inventory hashes, plugin versions, map/world,
   navigation parameters, and complete proof artifacts.

### P1 — Close diagnostic evidence and recovery

1. Add Mission-to-Robot typed diagnostic request/evidence lookup over existing HTTPS.
2. Add robot-local bounded evidence storage with hashes and anomaly freeze.
3. Add source/receipt time and freshness policies plus multi-robot namespace handling.
4. Add separately authorized recovery Tools and transaction/rollback for parameter profiles.
5. Evaluate the narrow-door/stall failure matrix with controlled seeds and ablations.

### P1 — Make Mission Runs restart-safe

Persist Run snapshots/control intents and rebuild or mark active Runs `lost` after restart.
Test coordinator crash during planning, active observation, dispatch wait, pause, cancellation,
revision, and final reporting.

### P2 — Add one real firefighting-domain capability

Implement one non-placeholder Plugin such as thermal/RGB victim detection or smoke/heat
monitoring with typed evidence and completion contracts. Do not expand to many superficial
Tools. One real algorithm integrated end to end is more valuable than a larger placeholder
catalog.

### Deferred

- third-party Plugin signatures and out-of-process hosting;
- operator Web UI/WebSocket;
- ROS2 native runtime;
- multi-floor navigation;
- marketplace/general platform parity.

## Commands already executed

```text
find memory -mindepth 1 -maxdepth 1 -type d -printf '%f\n'
rg --files memory/2026-07-31 memory/2026-07-30
git status --short --branch
git log -8 --oneline --decorate
git diff --stat
git diff --cached --stat
git show --stat --summary --format=fuller a0ae671
CodeGraph exploration of Mission/Robot/Tool/Safety/plugin/terminal paths
CodeGraph exploration of OpenClaw plugin, loop, gateway auth, and terminal analogues
focused pytest command shown above
```

## Files inspected

- recent and specifically relevant historical memory files listed above;
- `README.md`;
- `src/fireclaw_core/mission/mission_gateway.py`;
- `src/fireclaw_core/mission/mission_run.py`;
- `src/fireclaw_core/mission/mission_agent.py`;
- `src/fireclaw_core/mission/mission_scheduler.py`;
- `src/fireclaw_core/agent/agent.py`;
- `src/fireclaw_core/agent/tool_runtime.py`;
- `src/fireclaw_core/gateway/gateway.py`;
- `src/fireclaw_core/plugin/extension_loader.py`;
- `src/fireclaw_core/task/terminal_outcome.py`;
- `src/fireclaw_core/ros/gazebo_smoke.py`;
- `src/fireclaw_core/devtools/embodied_eval.py`;
- `extensions/navigation-move-base/README.md` and Plugin entrypoint/backend;
- `extensions/robot-legacy-physical/plugin/entrypoint.py`;
- `robots/turtlebot3_burger/README.md`;
- `examples/robot_profiles/gazebo_turtlebot3.toml`;
- `examples/ros1_configs/gazebo_turtlebot3_move_base.yaml`;
- current architecture and gap-roadmap documents.

## Files modified

- Added this memory record only.

## Current conclusion

FireClaw should now switch from platform breadth to vertical validation. The immediate next
task is a reproducible, current-plugin-owned TurtleBot3 Gazebo end-to-end acceptance lane,
followed immediately by repair of the stale embodied evaluation contract. Only after this
baseline is trustworthy should the project implement and evaluate hierarchical diagnostic
evidence/recovery as the main research mechanism.

## Next recommended command set

After creating a trusted bringup/acceptance script or documented terminal orchestration:

```text
source /opt/ros/noetic/setup.bash
source extensions/navigation-move-base/ros_ws/devel/setup.bash
source robots/turtlebot3_burger/ros_ws/devel/setup.bash
export TURTLEBOT3_MODEL=burger
roslaunch turtlebot3_gazebo turtlebot3_world.launch
roslaunch turtlebot3_navigation turtlebot3_navigation.launch \
  map_file:=$(rospack find turtlebot3_navigation)/maps/map.yaml
```

Then start the profile-backed Robot/Mission Gateways with deployment mode `simulation`,
submit a single-floor point-navigation mission, and archive trace/events/report/plugin
inventory/ROS diagnostics. Exact Gateway commands should be finalized only after updating the
current eval configuration so the run cannot fall back to legacy floor semantics.
