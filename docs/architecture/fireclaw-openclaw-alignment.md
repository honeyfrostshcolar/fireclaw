# FireClaw OpenClaw Alignment Architecture

## Purpose

This document is the engineering baseline for aligning FireClaw with OpenClaw while adapting the architecture to firefighting robots.

The target is not a direct copy of OpenClaw. FireClaw should reuse OpenClaw's proven agent, session, gateway, task, subagent, permission, memory, provider, and plugin boundaries, then specialize them for embodied robotics:

- physical safety;
- robot-local autonomy;
- degraded communication;
- auditable mission execution;
- simulator versus real-robot separation;
- ROS/robot SDK integration;
- multi-robot coordination.

## Target System Shape

FireClaw uses two persistent agent roles: a mission-level Mission Coordinator
and multiple registered embodied Robot Agents. The term subagent is reserved
for optional ephemeral delegated workers, not robots. See
[`fireclaw-agent-terminology.md`](fireclaw-agent-terminology.md).

```text
Operator
-> FireClaw Mission Coordinator
   -> Registered Robot Agent A
   -> Registered Robot Agent B
   -> Registered Robot Agent C
      -> Local Gateway
      -> Local Planner / Safety Gate / Skills
      -> ROS / Simulator / Robot SDK Adapter
```

The Mission Coordinator owns global mission reasoning. Each Robot Agent owns
local embodied execution authority. The Mission Coordinator must not bypass a
Robot Agent to directly call ROS topics, services, actions, motors, nozzles,
arms, or other hardware interfaces.

## OpenClaw Concept Mapping

| OpenClaw concept | FireClaw mission layer | FireClaw Robot Agent layer | Current FireClaw status |
|---|---|---|---|
| Agent | `MissionAgent` / Mission Coordinator | `FireClawAgent` / Robot Agent | Both persistent agent roles exist. |
| Session | Mission/operator session | Robot-local task/session context | Robot-local sessions exist; mission sessions are implicit through mission IDs. |
| Subagent | Optional ephemeral delegated worker | Optional local diagnostic or reasoning worker | Not the architectural name for a Robot Agent. |
| Gateway/control plane | Future mission Gateway/API/CLI | `FireClawGateway` | Robot-local Gateway exists; mission CLI exists; mission HTTP API missing. |
| Task registry | `JsonlMissionRegistry` | `JsonlTaskQueue` | Both exist. |
| Task runtime progress | Mission trace aggregation | Event ledger, task trace, action feedback | Robot trace exists; mission trace aggregation exists; live mission stream missing. |
| Permissions/scopes | Mission-level authorization scopes | Robot-local operator authorization | Both exist at baseline level. |
| Tool policy pipeline | Mission delegation and fleet constraints | Ordered capability projection and execution admission | One auditable pipeline now combines identity, task authorization, plugin ownership, robot profile, live state, safety, and exact action authorization. |
| Safety/sandbox | Mission call policy and Robot Agent contract | Safety gate, emergency stop, ROS transport gating | Robot-local safety exists; mission failure policy incomplete. |
| Memory | Mission memory and fleet lessons | Robot-local task/environment memory | Robot-local memory exists; mission memory is not fully designed. |
| Provider runtime | Mission-level model selection | Robot-local/edge model fallback | `ProviderRuntime` implemented and wired into `LLMMissionPlanner` as the main path. Supports model fallback boundary and error normalization. |
| Agent Harness | Mission prompt and task-graph semantics | Robot prompt and one-operation semantics | Both roles cross `ProviderAgentHarness`; neither calls the model directly. |
| Plugins/Skills/Tools | Mission Skills coordinate planning Tools | Robot Skills coordinate atomic navigation, perception, and control Tools | One `FireClawPluginHost` owns Plugin contributions; current `SkillRegistry` names an executable Tool compatibility projection. |
| Config/doctor/onboarding | Fleet and mission config checks | Robot ROS/skill/config checks | Robot doctor exists; fleet doctor missing. |

## Layer Responsibilities

### 1. Operator Interface Layer

Responsibilities:

- accept natural-language mission commands;
- show mission status, subtask status, safety blocks, and required confirmations;
- allow mission submit, trace, cancel, and future approval operations;
- keep operator-facing output separate from machine-readable traces.

Current implementation:

- `fireclaw_core.mission_cli` exposes `submit-subtask`, `trace`, `cancel`, and `plan-mission`.
- `operator_console` projects robot-local events into Chinese status text.

Missing:

- mission HTTP API;
- mission live progress stream;
- approval workflow UI;
- operator console for multi-robot missions.

### 2. Mission Coordinator Layer

Responsibilities:

- maintain mission-level state;
- plan missions into robot subtasks;
- check fleet presence;
- authorize mission-level operations;
- submit subtasks to registered Robot Agents;
- cancel missions by propagating cancel requests to subtasks;
- aggregate robot-local traces into mission traces.

Current implementation:

- `MissionAgent`
- `MissionPlanner`
- `JsonlMissionRegistry`
- mission-level authorization through `ControlPolicy`
- fleet presence checks through `RobotSubagentClient.check_presence(...)`

Missing:

- execution scheduler that respects `MissionPlan.execution_group`;
- mission failure policy for denied, offline, failed, timed out, and unreachable subtasks;
- mission-level event stream;
- mission memory write/read path;
- mission HTTP Gateway.

### 3. Fleet/Robot Agent Contract Layer

Responsibilities:

- store known Robot Agents;
- describe robot capabilities, zones, enabled state, and presence;
- call robot-local Gateways through a stable API;
- keep transport concerns out of mission planning.

Current implementation:

- `RobotRegistry`
- `RobotRegistryEntry`
- `RobotSubagentClient`
- shared HTTP(S) client calls for state, submit, trace, cancel, and presence;
- verified TLS 1.3 transport with optional mTLS and loopback-only plaintext.

`RobotSubagentClient` is a legacy-named compatibility identifier. In FireClaw
it calls registered and online physical or simulated Robot Agents. The Mission
Coordinator does not create robots; it selects and calls persistent Robot
Agents.

Missing:

- fleet config validation/doctor;
- heartbeat freshness threshold policy;
- robot pairing or enrollment flow;
- retry/backoff/circuit-breaker transport behavior.

### 4. Robot Agent Control Plane

Responsibilities:

- receive robot-local tasks;
- enforce robot-local authorization and safety;
- run the local FireClaw agent;
- persist task queue state;
- expose task trace, events, state, cancel, and emergency stop.

Current implementation:

- `FireClawGateway`
- `JsonlTaskQueue`
- `EventLedger`
- robot-local authorization;
- emergency stop;
- async task execution and cancellation;
- append-only `BoundedAgentLoop` checkpoint/resume;
- startup recovery bound to persisted structured task contracts;
- stable physical-operation IDs with dispatch-started/finished evidence and
  fail-closed escalation for unknown outcomes.

Missing:

- SSE/WebSocket or equivalent event streaming endpoint;
- stronger queue compaction/retention policy;
- a durable waiting queue when recoverable tasks exceed local concurrency;
- real deployment authentication.

### 5. Robot Agent, Planner, Tool, and Skill Runtime

Responsibilities:

- interpret robot-local commands;
- validate Tool schemas and preconditions;
- run Tools and robot actions;
- report action feedback and terminal outcomes;
- preserve planner/skill/adapter separation.

Current implementation:

- `FireClawAgent`
- local planner and safety gate;
- manifest-first Plugin discovery with public typed Tool and physical Tool
  contracts; process Tools must be Plugin contributions projected through the
  deployment policy and `ComputerSandbox`;
- declarative `PhysicalSkillPlugin` definitions and generic
  `SkillRegistry.register_plugin()` compatibility APIs, following OpenClaw's
  `defineToolPlugin/registerTool` shape for atomic Tools;
- OpenClaw-aligned `SKILL.md` terminology for broader Agent workflows that can
  coordinate multiple Tools;
- `FireClawPluginHost`, following OpenClaw's plugin API and registration
  transaction shape, for ownership and lifecycle of tools, physical
  capabilities, hooks, services, context engines, and Agent Harnesses;
- one `ProviderAgentHarness` for Mission and Robot model turns, enforcing
  context budgets, tool schemas, provider invocation, cancellation, error
  classification, and basic tool-call validation;
- plugin-owned tool schemas, task-target bindings, mutation guards, safety
  classification, resources, evidence, and operator projection;
- `RobotActionRuntime` with explicitly registered Plugin handlers and no
  per-Tool action switch or Adapter method fallback;
- SQLite WAL authoritative runtime state with revisioned task writes and
  transactional task/event commits;
- short-lived signed execution authorization bound to the command, structured
  task, robot identity, and exact skill input hashes;
- one OpenClaw-shaped ordered capability policy pipeline for planning-time tool
  projection and execution-time admission, with stage-by-stage audit evidence;
- executor-enforced persistent robot resource leases, with emergency stop
  closing resource admission first;
- robot adapter boundary;
- action feedback and cancellation event handling.

Missing:

- richer robot-local planner for varied firefighting tasks;
- typed Tool contracts and broader Skills for more real robot capabilities;
- externally authenticated actor identity and scope claims for production
  deployment;
- signing and out-of-process isolation for third-party physical Plugins;
- full wiring from success-evidence metadata into a generic completion
  validator.

### 6. Robot Adapter and ROS Integration Layer

Responsibilities:

- keep core `RobotAdapter` limited to state, environment observation, and
  emergency stop;
- let each domain Plugin own ROS/SDK translation, feedback, timeout,
  cancellation, and terminal-state mapping;
- enforce explicit deployment and Plugin configuration before live transport.

Current implementation:

- `Ros1RobotAdapter` for state, sensor discovery, and emergency stop;
- `Ros1Transport`, `ros1_config`, and `ros1_template` as core transport
  infrastructure;
- Plugin-owned `Ros1MoveBaseBackend` for navigation;
- mock, simulator, dry-run, and ROS1 Adapter modes.

Missing:

- recorded current-architecture Gazebo and real-robot acceptance;
- complete physical deadline enforcement across every Plugin Adapter;
- a real ROS2 core Adapter and Nav2 Plugin.

### 7. Memory and Audit Layer

Responsibilities:

- record missions, tasks, traces, observations, outcomes, operator corrections, and reusable lessons;
- keep robot-local incident logs auditable;
- support mission-level retrieval without hiding robot-local source of truth.

Current implementation:

- JSONL memory store for agent runs;
- `EventLedger`;
- `JsonlTaskQueue`;
- `JsonlMissionRegistry`.

Missing:

- first-class mission memory model;
- retrieval filters by mission, robot, location, capability, outcome, and operator;
- cross-robot lessons;
- retention and sensitive-log policy.

### 8. Model and Provider Runtime Layer

Responsibilities:

- select LLM/provider backends for mission planning and robot-local reasoning;
- support deterministic fallback when model calls are unavailable;
- isolate provider-specific request/response behavior.

Current implementation:

- deterministic planners and Python logic dominate current behavior.
- `OpenAICompatProvider` with `httpx` for OpenAI-compatible API calls.
- `ModelCatalog` for model metadata (context window, max tokens, cost).
- `LLMMissionPlanner` with tool calling for structured mission plan output.
- `LLMTraceStore` for replayable LLM traces (JSONL).

Missing:

- offline/degraded fallback policy;
- multi-provider support beyond OpenAI-compatible API.

## Core Data Flow

### Mission Planning and Execution

```text
operator command
-> mission authorization
-> fleet presence check
-> mission planning
-> mission registry record
-> subtask submission to registered Robot Agents
-> robot-local task queue and execution
-> robot-local event/task traces
-> mission trace aggregation
-> operator status
```

### Robot-Local Execution

```text
subtask request
-> robot-local authorization
-> safety gate
-> planner
-> skill runtime
-> robot adapter
-> ROS/simulator/SDK call
-> feedback/cancel/result
-> event ledger and task queue
-> trace response to Mission Coordinator
```

### Cancellation

```text
operator cancel mission
-> mission authorization
-> mission registry lookup
-> skip terminal subtasks
-> call Robot Agent cancel endpoints
-> robot-local cancel event
-> action runtime cancellation
-> ROS action cancel_goal when applicable
-> mission registry status update
```

## Current Build Status

Latest verified state recorded on 2026-06-11:

- branch: `master`, ahead of `origin/master` by ~120 commits;
- latest commit: experiment-readiness roadmap tasks complete;
- verification: `.venv/bin/python -m pytest -q` -> `1033 passed, 6 skipped`.

Untracked planning/config artifacts existed at that point:

- `.claude/`
- `CLAUDE.md`
- `CLAUDE.zh-CN.md`
- `docs/superpowers/plans/2026-06-08-fleet-heartbeat-v1.md`
- `docs/superpowers/plans/2026-06-08-mission-authorization-v1.md`
- `docs/superpowers/plans/2026-06-08-mission-planner-v1.md`

## Framework Completion Roadmap

### Phase 1: Stabilize the Mission Coordinator/Robot Agent Contract

Goal: make the current architecture behave like a coherent multi-robot framework.

Tasks:

- Mission Scheduler v1: execute `MissionPlan.execution_group` in ordered groups.
- Mission failure policy: define stop, continue, retry, reassign, and escalate behavior.
- Mission trace stream: expose live mission progress from robot-local traces.
- Fleet doctor: validate registry, robot reachability, capabilities, and ROS config coverage.

This phase should come before adding more task types because it makes the core control loop reliable.

### Phase 2: Complete Robot-Local Embodied Runtime

Goal: make each Robot Agent a credible local embodied agent.

Tasks:

- richer skill metadata for firefighting operations;
- typed skill input/output contracts;
- adapter-specific capability declarations;
- ROS1 smoke tests with a real ROS master (**done** — Phase 9);
- simulator/real-robot separation checks;
- stronger local failure taxonomy.

### Phase 3: Add Mission Memory and Operator Workflow

Goal: make missions recoverable, inspectable, and useful across runs.

Tasks:

- mission memory records;
- cross-robot event aggregation;
- operator correction recording;
- approval workflow for high-risk mission operations;
- incident replay from mission and robot-local traces.

### Phase 4: Add Model Provider Runtime

Goal: introduce LLM planning without making the framework depend on model quality.

Status: **Complete** (2026-06-08)

Implemented:

- `OpenAICompatProvider` with `httpx` (no openai SDK dependency);
- `ModelCatalog` for model metadata management;
- `LLMMissionPlanner` with tool calling for structured mission plan output;
- `LLMTraceStore` for replayable LLM traces (JSONL);
- CLI integration with `--planner llm` flag and provider configuration;
- deterministic fallback preserved via `--planner deterministic` (default).

Remaining:

- offline/degraded fallback policy;
- multi-provider support beyond OpenAI-compatible API.

### Phase 5: Deployment Hardening

Goal: prepare for real robot or high-fidelity simulation deployments.

Status: **Partial** (2026-06-09) — 5 of 6 tasks completed

Implemented:

- Gateway API token authentication (`api_token` in GatewayConfig, Bearer header check);
- HTTPS with verified hostname/CA, optional mTLS, and remote plaintext rejection;
- shared Mission/Robot network admission with pre-thread total/per-IP
  connection budgets, header/body deadlines, early body-size rejection,
  Host/Origin validation, bounded failed-auth lockout, and bounded SSE
  connections/subscriber queues;
- canonical runtime roots and role-specific Docker workspace path validation;
- named Docker invocation lifecycle, bounded pipe capture, forced cleanup, and
  immutable image identity verification;
- Robot enrollment with one-time pairing codes (`JsonlEnrollmentStore`);
- Heartbeat expiration with stale robot exclusion (`heartbeat_timeout_seconds`, `is_stale()`);
- Queue compaction (`compact(keep_terminal=N)`);
- Log redaction for secrets (`redact_secrets()`, `redact_dict()`, `LLMTraceStore.redact_all()`).
- Deployment config examples (`examples/ros1_configs/`).

Remaining:

- security review for robot control endpoints.

## Near-Term Engineering Priority

`Mission Scheduler v1` is **already implemented**. `MissionAgent.plan_and_submit()` delegates to `MissionScheduler` by default, supporting execution group ordering, failure policy (retry/reassign/skip/escalate/abort), and multi-robot parallel/sequential scheduling.

The next engineering priorities are:

1. Real ROS1 robot hardware smoke test (requires physical robot or high-fidelity simulation).

Already implemented (updated 2026-08-10):

- ✅ Real gateway-to-gateway embodied e2e proof (`test_embodied_gateway_e2e.py`).
- ✅ Versioned deterministic-integration runner with scheduler-backed background
  Mission Runs, canonical outcomes, typed targets, and reproducible artifacts
  (`devtools/embodied_eval.py`, `evaluation/`).
- ✅ Offline LLM-planning runner using the production bounded Mission
  deliberation policy, frozen point/area/entity fixtures, model seed
  forwarding, complete prompt/Tool/usage proof, deterministic proposal
  scoring, and an explicit no-dispatch boundary
  (`devtools/llm_planning_eval.py`, `evaluation/planning.py`).
- ✅ ROS/Gazebo system proof collector and scorer for success, cancel, timeout,
  native abort, diagnostics-first bounded recovery, and operator escalation;
  it preserves canonical Mission Run outcomes, same-task resume, Plugin-owned
  dispatch, safe-stop evidence, raw proof hashes, and explicit missing-data
  semantics (`devtools/ros_gazebo_system_eval.py`, `evaluation/system.py`).
- ✅ Memory learning closed-loop proof (`test_memory_learning_loop.py`).
- ✅ Embodied experiment proof bundle (`embodied_proof_bundle.py`).
- ✅ Deployable mission runtime factory wiring (`build_mission_agent_from_paths`).
- ✅ End-to-end embodied mission scenario gate.

ROS2 native adapter, full ACP/IDE platform parity, and full Web UI remain out of scope for the current embodied-agent roadmap.

## Next External Validation

The next external validation step is repeated ROS1/Gazebo collection with
collision instrumentation on a frozen test split, followed by a limited
hardware proof run. This is not additional OpenClaw platform parity; it is
robotics validation for FireClaw's embodied-agent claims.

## ROS1/Gazebo Unknown-State Semantics

ROS1/Gazebo adapter state must distinguish unknown values from explicit unsafe values. `None` means FireClaw does not yet know the battery, sensors, or reachable floors; `0.0` and `[]` mean the adapter has positively observed zero battery or no reachable floors. SafetyGate uses that distinction to warn or require operator confirmation instead of blocking valid Gazebo bring-up.

## Experiment Readiness Claims

FireClaw can claim code-level and simulator-level embodied-agent readiness when the gateway-to-gateway e2e test, scenario eval, memory learning loop, and proof bundle all pass.

FireClaw cannot claim real firefighting robot validation until a ROS1 hardware or high-fidelity simulation run produces a proof bundle with doctor output, smoke artifacts, mission trace, event replay, and operator notes.
