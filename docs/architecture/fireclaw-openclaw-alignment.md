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

FireClaw should be built as a mission-level main agent that coordinates multiple embodied robot subagents:

```text
Operator
-> FireClaw Main Mission Agent
   -> Robot FireClaw Subagent A
   -> Robot FireClaw Subagent B
   -> Robot FireClaw Subagent C
      -> Local Gateway
      -> Local Planner / Safety Gate / Skills
      -> ROS / Simulator / Robot SDK Adapter
```

The main agent owns mission reasoning. Each robot subagent owns local embodied execution authority. The main agent must not bypass robot subagents to directly call ROS topics, services, actions, motors, nozzles, arms, or other hardware interfaces.

## OpenClaw Concept Mapping

| OpenClaw concept | FireClaw main layer | FireClaw robot subagent layer | Current FireClaw status |
|---|---|---|---|
| Agent | `MissionAgent` | `FireClawAgent` | Main and robot-local agents exist. |
| Session | Mission/operator session | Robot-local task/session context | Robot-local sessions exist; mission sessions are implicit through mission IDs. |
| Subagent | Robot FireClaw node as callable subagent | Optional local diagnostic workers later | Basic robot subagent contract exists through `RobotSubagentClient`. |
| Gateway/control plane | Future mission Gateway/API/CLI | `FireClawGateway` | Robot-local Gateway exists; mission CLI exists; mission HTTP API missing. |
| Task registry | `JsonlMissionRegistry` | `JsonlTaskQueue` | Both exist. |
| Task runtime progress | Mission trace aggregation | Event ledger, task trace, action feedback | Robot trace exists; mission trace aggregation exists; live mission stream missing. |
| Permissions/scopes | Mission-level authorization scopes | Robot-local operator authorization | Both exist at baseline level. |
| Safety/sandbox | Mission call policy and subagent boundary | Safety gate, emergency stop, ROS transport gating | Robot-local safety exists; mission failure policy incomplete. |
| Memory | Mission memory and fleet lessons | Robot-local task/environment memory | Robot-local memory exists; mission memory is not fully designed. |
| Provider runtime | Mission-level model selection | Robot-local/edge model fallback | `ProviderRuntime` implemented and wired into `LLMMissionPlanner` as the main path. Supports model fallback boundary and error normalization. |
| Tools/skills/plugins | Mission tools: plan, assign, cancel, query, aggregate | Robot skills: navigate, search, assess, report, stop | Robot skill runtime exists; mission tools are still Python/CLI methods. |
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

### 2. Main Mission Agent Layer

Responsibilities:

- maintain mission-level state;
- plan missions into robot subtasks;
- check fleet presence;
- authorize mission-level operations;
- submit subtasks to robot subagents;
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

### 3. Fleet/Subagent Contract Layer

Responsibilities:

- store known robot subagents;
- describe robot capabilities, zones, enabled state, and presence;
- call robot-local Gateways through a stable API;
- keep transport concerns out of mission planning.

Current implementation:

- `RobotRegistry`
- `RobotRegistryEntry`
- `RobotSubagentClient`
- basic HTTP calls for state, submit, trace, cancel, and presence.

Missing:

- fleet config validation/doctor;
- heartbeat freshness threshold policy;
- robot pairing or enrollment flow;
- retry/backoff/circuit-breaker behavior;
- transport abstraction beyond HTTP.

### 4. Robot Subagent Control Plane

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
- async task execution and cancellation.

Missing:

- SSE/WebSocket or equivalent event streaming endpoint;
- stronger queue compaction/retention policy;
- process restart recovery beyond marking stale tasks lost;
- real deployment authentication.

### 5. Robot Agent, Planner, and Skill Runtime

Responsibilities:

- interpret robot-local commands;
- validate skill schemas and preconditions;
- run skills and robot actions;
- report action feedback and terminal outcomes;
- preserve planner/skill/adapter separation.

Current implementation:

- `FireClawAgent`
- local planner and safety gate;
- skill manifest loading;
- `RobotActionRuntime`;
- robot adapter boundary;
- action feedback and cancellation event handling.

Missing:

- richer robot-local planner for varied firefighting tasks;
- typed skill contracts for more real robot capabilities;
- stronger failure taxonomy;
- skill-level degraded-mode policies.

### 6. Robot Adapter and ROS Integration Layer

Responsibilities:

- isolate ROS, simulator, SDK, perception, navigation, manipulation, communication, and actuation APIs from agent code;
- render FireClaw actions into robot-specific payloads;
- enforce explicit config before live transport;
- propagate feedback, timeout, and cancellation.

Current implementation:

- `Ros1RobotAdapter`
- `Ros1Transport`
- `ros1_config`
- `ros1_template`
- mock, simulator, dry-run, and ROS1 adapter modes.

Missing:

- live ROS master smoke tests;
- real message construction/introspection beyond dictionary payloads;
- ROS2 adapter beyond mock/skeleton behavior;
- long-lived multi-action client registry;
- deployment-specific robot config examples.

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
-> subtask submission to robot subagents
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
-> trace response to main agent
```

### Cancellation

```text
operator cancel mission
-> mission authorization
-> mission registry lookup
-> skip terminal subtasks
-> call robot subagent cancel endpoints
-> robot-local cancel event
-> action runtime cancellation
-> ROS action cancel_goal when applicable
-> mission registry status update
```

## Current Build Status

Latest verified state recorded on 2026-06-11:

- branch: `master`, ahead of `origin/master` by ~120 commits;
- latest commit: embodied-agent field readiness roadmap tasks complete;
- verification: `.venv/bin/python -m pytest -q` -> `1020 passed, 6 skipped`.

Untracked planning/config artifacts existed at that point:

- `.claude/`
- `CLAUDE.md`
- `CLAUDE.zh-CN.md`
- `docs/superpowers/plans/2026-06-08-fleet-heartbeat-v1.md`
- `docs/superpowers/plans/2026-06-08-mission-authorization-v1.md`
- `docs/superpowers/plans/2026-06-08-mission-planner-v1.md`

## Framework Completion Roadmap

### Phase 1: Stabilize the Main/Subagent Skeleton

Goal: make the current architecture behave like a coherent multi-robot framework.

Tasks:

- Mission Scheduler v1: execute `MissionPlan.execution_group` in ordered groups.
- Mission failure policy: define stop, continue, retry, reassign, and escalate behavior.
- Mission trace stream: expose live mission progress from robot-local traces.
- Fleet doctor: validate registry, robot reachability, capabilities, and ROS config coverage.

This phase should come before adding more task types because it makes the core control loop reliable.

### Phase 2: Complete Robot-Local Embodied Runtime

Goal: make each robot subagent a credible local embodied agent.

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
2. Deployable mission runtime factory wiring (CLI paths for memory retriever, provider runtime, plugin runtime, approval stores).
3. End-to-end embodied mission scenario gate (operator command -> mission -> robot gateway -> events -> memory in one workflow).

ROS2 native adapter, full ACP/IDE platform parity, and full Web UI remain out of scope for the current embodied-agent roadmap.

## Experiment Readiness Claims

FireClaw can claim code-level and simulator-level embodied-agent readiness when the gateway-to-gateway e2e test, scenario eval, memory learning loop, and proof bundle all pass.

FireClaw cannot claim real firefighting robot validation until a ROS1 hardware or high-fidelity simulation run produces a proof bundle with doctor output, smoke artifacts, mission trace, event replay, and operator notes.

