# Robot Integration Boundary v1 Design

## Goal

Build the next FireClaw framework layer: a robot action boundary that separates agent planning and skill orchestration from robot-side execution systems such as simulator backends, dry-run backends, subprocess policies, and future ROS1 adapters.

This phase intentionally avoids ROS1 topic/service/actionlib details. The goal is to define the FireClaw-facing action lifecycle first, then plug ROS1 into that lifecycle later.

## OpenClaw Analogue

OpenClaw uses a control-plane shape built around:

- stable run ids;
- registered run context;
- sequenced agent events;
- abort controller registries;
- Gateway entrypoints that accept work, stream/provide events, and expose terminal run state.

Relevant OpenClaw symbols inspected with CodeGraph:

- `src/infra/agent-events.ts:139` `registerAgentRunContext(...)`
- `src/infra/agent-events.ts:209` `emitAgentEvent(...)`
- `src/gateway/chat-abort.ts:74` `registerChatAbortController(...)`
- `src/gateway/chat-abort.ts:170` `abortChatRunById(...)`

FireClaw should reuse that shape but change the domain object:

```text
OpenClaw: runId -> agent/tool events -> abort -> lifecycle final
FireClaw: task_id -> skill/action events -> cancel -> action/task final
```

## Current FireClaw State

FireClaw already has:

- `task_id` through Gateway async task submission;
- EventLedger JSONL traces;
- skill-level events such as `skill.started`, `skill.attempted`, `skill.succeeded`, `skill.failed`;
- task cancellation through `TaskControl.cancel_event`;
- subprocess cancellation through `SubprocessSkillRunner`;
- robot adapters for dry-run, simulator, and mock ROS-shaped behavior.

The missing framework boundary is the level between skill execution and robot execution:

```text
task -> plan -> skill step -> robot action -> feedback/final/cancel
```

Right now, a skill directly returns a `RobotActionResult`. For real embodied robots, this is too coarse because one skill may start a long-running navigation, search, manipulation, suppression, or rescue-assist action that needs feedback, cancellation, and audit events while it runs.

## Recommended Architecture

Introduce a robot action runtime boundary:

```text
FireClawAgent
  -> PlanExecutor
    -> Skill
      -> RobotActionRuntime
        -> DryRunActionBackend
        -> SimulatorActionBackend
        -> MockRos1ActionBackend
        -> future RealRos1ActionBackend
```

The core package should define the action contract. Backends implement that contract. FireClaw core should not import `rospy` or any robot vendor SDK.

## Core Concepts

### Robot Action Request

A normalized command from a skill to the robot execution layer.

Required fields at the framework level:

- `action_id`
- `task_id`
- `skill_name`
- `action_type`
- `inputs`
- `requested_at`
- `timeout_seconds`
- `risk_level`
- `dry_run`

The framework does not prescribe ROS1 message types in this phase.

### Robot Action Event

Structured events emitted by the action runtime and persisted by Gateway/EventLedger.

Initial event types:

- `action.requested`
- `action.started`
- `action.feedback`
- `action.cancel_requested`
- `action.cancelled`
- `action.succeeded`
- `action.failed`

Skill events remain useful, but action events provide robot-level audit detail.

### Robot Action Result

The terminal state of one robot action:

- `succeeded`
- `failed`
- `cancelled`
- `timeout`
- `blocked`

The result should include enough structured data to reconstruct what happened without reading operator-facing text.

## Data Flow

The intended execution flow is:

```text
1. Gateway accepts a natural-language task and creates task_id.
2. FireClawAgent plans and safety-checks the task.
3. PlanExecutor starts a skill step.
4. The skill creates a RobotActionRequest.
5. RobotActionRuntime assigns action_id and emits action.requested/action.started.
6. The selected backend performs or simulates the action.
7. Backend emits action.feedback events while running.
8. If task cancellation is requested, runtime emits action.cancel_requested and calls backend cancel.
9. Backend returns terminal action result.
10. PlanExecutor maps terminal action result back to the skill result.
11. Gateway records final task result.
```

## Cancellation Model

Cancellation should be layered:

```text
task cancel -> executor cancellation check -> action runtime cancel -> backend cancel
```

For this phase:

- dry-run backend can cancel immediately;
- simulator backend can cancel deterministically;
- mock ROS1 backend records what would be cancelled;
- real ROS1 backend is future work.

This avoids pretending ROS1 behavior exists before the action boundary is stable.

## State And Feedback

Keep state snapshots and feedback separate:

- `robot_state`: current robot/system state at a point in time;
- `environment_state`: current environment facts known to FireClaw;
- `action.feedback`: progress or intermediate robot execution updates.

This distinction matters for firefighting robots because safety gates use state snapshots, while operators and audit logs need feedback history.

## Safety Boundary

Safety remains before execution, but action runtime should preserve safety metadata in every action request:

- risk level;
- dry-run versus real-run mode;
- required sensors;
- timeout;
- cancellation support;
- emergency-stop assumptions.

Future safety work can block or cancel active actions based on emergency-stop state, degraded communication, or unsafe robot feedback.

## ROS1 Position

ROS1 is a backend, not the FireClaw core architecture.

Future `RealRos1ActionBackend` should translate FireClaw action requests into the user's actual ROS1 interfaces:

- actionlib actions when available;
- services for short operations;
- topics for command streams;
- subscribers for feedback/status;
- emergency-stop and robot-state channels.

Those mappings should be designed after this generic action lifecycle exists.

## Testing Strategy

This phase should be tested without real hardware:

- dry-run backend action lifecycle;
- simulator backend action feedback and terminal results;
- cancellation before action start;
- cancellation during active action;
- EventLedger receives action lifecycle events in order;
- skill result still matches existing PlanExecutor behavior;
- Gateway trace includes both skill-level and action-level events.

## Research Impact

This boundary moves FireClaw toward an embodied-agent execution framework rather than a one-shot skill demo.

The important research claim is not "FireClaw calls ROS1." The stronger claim is:

```text
FireClaw provides an auditable, cancellable, safety-aware action execution layer
between natural-language firefighting tasks and robot-side execution systems.
```

ROS1 integration will be evidence that the boundary can control a real or simulated robot stack, but the framework contribution is the action lifecycle, safety gate, memory, Gateway trace, and operator-control structure.

## Scope Exclusions

This design does not implement:

- real ROS1 topic/service/actionlib bindings;
- fleet routing;
- emergency priority scheduling;
- full operator authorization;
- semantic memory retrieval;
- learned planner policies;
- physical robot deployment scripts.

Those are later phases.
