# FireClaw Main/Subagent Architecture v1 Design

## Goal

Define FireClaw as an OpenClaw-aligned firefighting robotics agent system with a central mission-level main agent and one embodied robot subagent running on each robot.

The target architecture is:

```text
Operator
-> FireClaw Main Mission Agent
   -> Robot FireClaw Subagent A
   -> Robot FireClaw Subagent B
   -> Robot FireClaw Subagent C
```

The main agent coordinates missions. Robot subagents perform embodied execution and keep final authority over their own hardware.

## Architectural Position

FireClaw should not be a pure central controller that directly drives every robot's ROS topics. It should also not be only a set of isolated single-robot agents with no shared mission reasoning.

The intended architecture is a hybrid:

```text
central mission reasoning + decentralized embodied safety execution
```

The main agent can assign subtasks, aggregate state, and coordinate robots. Each robot subagent validates, plans, executes, cancels, stops, logs, and remembers locally.

## OpenClaw Alignment

OpenClaw is a local-first agent platform with agents, sessions, task registries, control channels, tools, plugins, model/provider configuration, permissions, pairing, memory, logs, and subagent task runtimes.

FireClaw should keep the same capability shape but robotics-specialize each part:

| OpenClaw concept | FireClaw main agent | FireClaw robot subagent |
|---|---|---|
| Agent | Mission-level reasoning agent | Embodied robot-local agent |
| Session | Mission/operator session | Robot-local operator/task session |
| Subagent | Robot FireClaw node as callable subagent | Not applicable; may later spawn local diagnostic workers |
| Task registry | Mission/subtask registry | Durable robot task queue |
| Task runtime progress | Aggregated mission/subtask progress | Local skill/action progress |
| Gateway/control channel | Operator and robot-subagent control plane | Robot-local control plane |
| Tools/skills/plugins | Mission tools: assign, cancel, query, aggregate | Robot tools: navigate, search, assess, report, return, stop |
| Memory | Global mission memory and cross-robot lessons | Local task, environment, outcome, and operator memory |
| Pairing/auth/scope | Operator to mission authorization | Operator/main-agent to robot authorization |
| Sandbox/security | Main-agent permission to call robot subagents | Local safety gate and dangerous skill permission |
| Provider runtime/model config | Mission-level model selection/fallback | Robot-local or edge model fallback |
| Logs/events/trace | Mission trace across robots | Local event ledger and incident trace |
| Config/doctor/onboarding | Fleet and mission-control config checks | Robot ROS/sensor/skill/config checks |

## Main Agent Responsibilities

The FireClaw Main Mission Agent is responsible for:

- accepting operator mission commands;
- maintaining mission sessions;
- decomposing mission-level tasks into robot subtasks;
- discovering or loading robot subagent registry entries;
- querying robot subagent state;
- choosing which robot subagents to call;
- submitting subtasks to robot subagents;
- aggregating subtask events, feedback, results, and failures;
- maintaining mission-level memory;
- enforcing operator authorization for mission-level actions;
- presenting mission trace and status to the operator.

The main agent should call robot subagents through stable control-plane APIs. In v1, this should be HTTP against each robot's local Gateway:

```text
POST /tasks
GET /state
GET /tasks/<task_id>
POST /tasks/<task_id>/cancel
POST /emergency-stop
```

Later transports may include WebSocket, gRPC, MQTT, NATS, DDS, or ROS2-native fleet channels, but the architecture should not depend on those in v1.

## Robot Subagent Responsibilities

Each robot runs a local FireClaw node with:

- `robot_id`;
- local Gateway/control plane;
- local `FireClawAgent`;
- local planner;
- local safety gate;
- local skill runtime;
- local ROS1/ROS2/SDK adapter;
- local durable task queue;
- local event ledger;
- local memory;
- local operator authorization;
- local emergency stop handling;
- local config/doctor checks.

The robot subagent can accept a subtask from the main agent, but it must not blindly obey. It may reject, block, ask for confirmation, cancel, or emergency-stop based on local state.

Examples of local rejection reasons:

- robot offline;
- emergency stop active;
- battery too low;
- required sensor unavailable;
- target floor unreachable;
- ROS action server unavailable;
- operator/main agent lacks required scope;
- command is ambiguous or physically unsafe;
- current local task queue is full.

## Authority Boundary

The main agent has mission authority. The robot subagent has embodied execution authority.

The main agent may request:

```text
robot-2: search floor 3
```

The robot subagent decides whether it can safely execute:

```text
allow -> plan and execute locally
block -> return safety decision and reason
clarify -> request missing detail
awaiting_confirmation -> require approval
cancelled -> stop before or during execution
failed -> report local failure
```

The main agent must not bypass the subagent and directly call low-level ROS topics, motor commands, manipulation commands, or firefighting actuators.

## Mission Flow

Example command:

```text
去二楼和三楼搜索受困人员
```

Main agent flow:

```text
operator command
-> mission planning
-> query robot subagent states
-> create mission task
-> create subtasks:
   robot-1: search floor 2
   robot-2: search floor 3
-> submit subtasks to robot subagents
-> aggregate feedback and trace
-> handle failures or requests for assistance
-> report mission status
-> write mission memory
```

Robot subagent flow:

```text
receive subtask
-> local operator/main-agent authorization
-> local safety gate
-> local planning
-> local skill execution
-> ROS action/service/topic calls
-> local feedback/cancel/timeout handling
-> local event trace
-> local memory update
-> return result to main agent
```

## Core Data Boundaries

### Robot Registry Entry

The main agent stores:

```json
{
  "robot_id": "robot-1",
  "base_url": "http://robot-1.local:8765",
  "capabilities": ["navigate", "search_for_victims", "report_status"],
  "zone": "building-a",
  "enabled": true
}
```

### Subtask Request

The main agent sends:

```json
{
  "command": "去二楼搜索受困人员",
  "session_id": "mission-001",
  "dedupe_key": "mission-001-robot-1-search-floor-2",
  "operator": {
    "operator_id": "mission-agent",
    "role": "system",
    "scopes": ["task.submit"]
  },
  "mission": {
    "mission_id": "mission-001",
    "parent_task_id": "mission-task-001"
  }
}
```

### Subtask Result

The robot subagent returns existing Gateway task/result/trace data plus robot-local metadata:

```json
{
  "robot_id": "robot-1",
  "task_id": "task-...",
  "status": "accepted"
}
```

The main agent later retrieves the task trace and records it into the mission trace.

## Existing FireClaw Code Reinterpretation

Current implemented modules remain useful:

| Current module | New role |
|---|---|
| `FireClawAgent` | Robot subagent local embodied agent |
| `FireClawGateway` | Robot subagent local control plane |
| `Ros1RobotAdapter` | Robot-local ROS adapter |
| `RobotActionRuntime` | Robot-local action event/cancel boundary |
| `JsonlTaskQueue` | Robot-local durable task queue |
| `EventLedger` | Robot-local auditable trace |
| `ControlPolicy` | Robot-local operator/main-agent authorization |
| `EmergencyStopState` | Robot-local emergency safety state |
| memory store | Robot-local memory |

New main-agent modules should be added rather than turning `FireClawGateway` into a central direct robot controller.

## New Modules

### `RobotRegistry`

Stores configured robot subagents:

- `robot_id`;
- `base_url`;
- `capabilities`;
- `enabled`;
- static deployment metadata.

V1 can load JSON/YAML-like config. Later versions can add discovery, pairing, and heartbeats.

### `RobotSubagentClient`

HTTP client for robot-local Gateways:

- `get_state(robot_id)`;
- `submit_task(robot_id, command, session_id, dedupe_key, operator, mission)`;
- `get_task_trace(robot_id, task_id)`;
- `cancel_task(robot_id, task_id)`;
- `emergency_stop(robot_id, reason)`.

### `MissionTaskRegistry`

Main-agent equivalent of OpenClaw's task registry:

- mission id;
- parent task id;
- subtask ids;
- assigned robot ids;
- subtask status;
- mission terminal status;
- failure/cancel reasons.

### `MissionAgent`

Coordinates robot subagents:

- parses mission command;
- creates mission plan;
- assigns explicit or rule-based subtasks;
- calls `RobotSubagentClient`;
- aggregates traces and results;
- updates mission memory.

V1 should support explicit subtask assignment first. Autonomous decomposition can be added after the transport and trace contracts are reliable.

## Roadmap

### Phase 1: Main/Subagent Contract v1

- Write architecture docs and API contracts.
- Add `RobotRegistry`.
- Add `RobotSubagentClient`.
- Use local test Gateways as robot subagents.
- Support explicit calls such as:

```python
mission.submit_subtask("robot-1", "去二楼搜索")
```

### Phase 2: Mission Registry and Trace v1

- Add mission/subtask records.
- Aggregate robot task traces into mission trace.
- Preserve robot-local trace as source of truth.
- Add dedupe keys per mission/subtask.

### Phase 3: Main Agent Planning v1

- Add deterministic mission planner for simple multi-floor/multi-robot commands.
- Keep robot subagents responsible for local safety.
- Do not split one physical robot action across multiple robots yet.

### Phase 4: Pairing and Auth Alignment

- Pair operator to main agent.
- Pair main agent to robot subagents.
- Add scopes for subagent calls:
  - `robot.task.submit`;
  - `robot.task.cancel`;
  - `robot.emergency.stop`;
  - `robot.state.read`;
  - `robot.trace.read`.

### Phase 5: Fleet Presence

- Add robot heartbeat/status cache.
- Mark stale robots unavailable.
- Expose mission board state.

### Phase 6: Peer Coordination

- Allow robot subagents to request assistance from the main agent or peers.
- Share hazards, victims, blocked paths, and local map facts.
- Keep local safety gates authoritative.

### Phase 7: OpenClaw Capability Completion

Add robotics-specialized versions of:

- plugin marketplace / skill packs;
- provider runtime and model fallback;
- sandbox and permission model;
- richer memory retrieval/compaction;
- operator console;
- telemetry export;
- config doctor/onboarding;
- deployment/pairing flows.

## Research Framing

The research contribution should not be described as simply "a central multi-robot dispatcher." A stronger framing is:

```text
An OpenClaw-inspired main/subagent architecture for firefighting robots, combining centralized mission reasoning with decentralized embodied safety execution.
```

Potential claims:

- The main agent improves mission-level coordination and operator workload.
- Robot subagents improve safety by preserving local embodied authority.
- Durable local queues and traces make failures auditable.
- OpenClaw-like task registries and subagent runtimes transfer well to robotics when adapted with safety gates, local memory, ROS adapters, and emergency-stop semantics.

Required evidence:

- single-robot execution correctness;
- multi-robot subtask dispatch success;
- rejection of unsafe or impossible subtasks by robot subagents;
- recovery behavior under robot disconnect/restart;
- trace completeness for incident review;
- comparison against pure central control and isolated single-robot agents.

## Immediate Next Implementation Step

Implement **Main/Subagent Contract v1**:

1. `RobotRegistry`;
2. `RobotSubagentClient`;
3. tests using two local `FireClawGateway` instances as robot subagents;
4. explicit subtask submission from a minimal `MissionAgent`;
5. mission-level result aggregation without autonomous decomposition yet.
