# Operator Safety Control Plane v2 Design

## Goal

Build the next FireClaw framework layer after the task/action state model: an operator and safety control plane that defines who may submit, confirm, cancel, or stop robot work, and how those decisions are represented in task traces.

This phase should stay at framework level. It should not implement full authentication, real ROS1 emergency-stop wiring, or deployment-specific credentials.

## Why This Comes Next

FireClaw now has:

- `task_id` and async Gateway task submission;
- skill and action lifecycle events;
- task/action state projection;
- operator confirmation for high-risk skills;
- task cancellation;
- safety gate blocks for robot/environment state.

The missing framework layer is explicit operator/control authority:

```text
operator -> command/confirm/cancel/emergency-stop -> control policy -> task/action execution
```

Without this layer, FireClaw can execute and cancel tasks, but cannot clearly answer:

- who requested this task?
- who confirmed a high-risk plan?
- who cancelled the task?
- did that operator have authority?
- did a pending approval expire?
- was the control channel degraded?
- was emergency stop active?

For firefighting robots, those questions are safety-critical.

## OpenClaw Analogue

OpenClaw has several relevant control-plane concepts:

- stable run ids;
- abort RPCs such as `chat.abort`;
- active abort registries;
- operator scopes such as read/write/approvals/admin;
- control channel connection states such as connected, disconnected, degraded;
- approval events and scoped control operations.

Relevant symbols inspected with CodeGraph:

- `src/gateway/operator-scopes.ts:8` `OperatorScope`
- `src/gateway/chat-abort.ts:170` `abortChatRunById(...)`
- `apps/android/app/src/main/java/ai/openclaw/app/chat/ChatController.kt:277` `abort()`
- `apps/macos/Sources/OpenClaw/ControlChannel.swift:42` `ControlChannel`

FireClaw should reuse the shape, but adapt it for embodied robotics:

```text
OpenClaw: operator scope -> run approval/abort -> chat/tool lifecycle
FireClaw: operator role -> task confirm/cancel/e-stop -> robot action lifecycle
```

## Core Concepts

### Operator Context

Every control-plane entrypoint should be able to carry an operator context:

- `operator_id`
- `display_name`
- `role`
- `control_scopes`
- `source`

Initial roles:

- `observer`
- `operator`
- `supervisor`
- `admin`

Initial scopes:

- `task.submit`
- `task.confirm`
- `task.cancel`
- `safety.override`
- `emergency.stop`
- `state.read`

V2 does not need real login. If no operator context is provided, Gateway can use a local default operator for dry-run development. The important part is that the data shape exists and is recorded.

### Control Policy

A small policy layer should decide whether an operator can perform a control action.

Initial checks:

- submit requires `task.submit`;
- confirm requires `task.confirm`;
- cancel requires `task.cancel`;
- emergency stop requires `emergency.stop`;
- safety override requires `safety.override`.

The policy should return structured decisions:

- `allow`
- `deny`
- `require_confirmation`

Each decision should include reasons and the operator context used.

### Pending Approval

Current high-risk confirmation is stored as pending memory records. V2 should make this explicit.

Pending approval fields:

- `approval_id`
- `task_id`
- `session_id`
- `operator_id`
- `plan`
- `risk_reasons`
- `created_at`
- `expires_at`
- `status`

Initial statuses:

- `pending`
- `confirmed`
- `cancelled`
- `expired`

Pending approvals should expire. A stale high-risk plan should not be confirmable indefinitely.

### Control State

Gateway should expose control-plane state in `/state`:

- current operator default;
- active task capacity;
- active tasks;
- emergency stop state;
- control channel health;
- pending approval count.

Initial control channel states:

- `connected`
- `degraded`
- `disconnected`

For local dry-run, control channel can default to `connected`.

### Emergency Stop

Emergency stop is separate from ordinary task cancellation.

Task cancellation means:

```text
stop this task as soon as the current cancellable boundary allows
```

Emergency stop means:

```text
block new execution and request immediate stop of active robot-side action
```

V2 should introduce the framework state and Gateway command path for emergency stop, even if real hardware wiring remains future ROS1 work.

Initial emergency stop fields:

- `active`
- `reason`
- `operator_id`
- `activated_at`
- `cleared_at`

While emergency stop is active:

- new task execution should be blocked by safety/control policy;
- confirmations should not launch execution;
- active task cancellation should be requested.

## Gateway Entry Points

Keep existing endpoints compatible. Add optional operator fields to existing POST bodies:

```json
{
  "command": "去二楼救人",
  "session_id": "operator-a",
  "operator": {
    "operator_id": "op-1",
    "role": "operator"
  }
}
```

Recommended future endpoints:

- `POST /tasks`
- `POST /confirm`
- `POST /cancel`
- `POST /tasks/<task_id>/cancel`
- `POST /emergency-stop`
- `POST /emergency-stop/clear`
- `GET /state`

## Event Types

Add control-plane events:

- `operator.identified`
- `control.decision`
- `approval.created`
- `approval.confirmed`
- `approval.cancelled`
- `approval.expired`
- `emergency_stop.activated`
- `emergency_stop.cleared`

Existing events remain:

- `confirmation.pending`
- `confirmation.confirmed`
- `task.cancel_requested`
- `task.cancelled`

V2 can keep both `confirmation.*` and `approval.*` temporarily. Later, confirmation can be treated as a compatibility alias over approval.

## Safety Interaction

SafetyGate should remain the low-level safety evaluator. Operator/Safety Control Plane v2 should sit above and around it:

```text
operator request -> control policy -> planner -> safety gate -> approval if needed -> executor
```

Hard safety blocks must not be bypassable by ordinary confirmation.

Examples:

- missing skill: block;
- robot offline: block;
- unreachable floor: block;
- high-risk but otherwise executable: approval required;
- emergency stop active: block.

## Task/Action State Interaction

The state model should eventually include control fields:

- `operator_id`
- `approval_id`
- `emergency_stop_active`
- `control_status`

For V2 implementation, it is enough for Gateway trace events to carry control-plane information. State projection can be extended afterward if needed.

## Testing Strategy

V2 should be implemented without real credentials or hardware.

Focused tests should cover:

- default local operator context is recorded;
- submit is denied when operator lacks `task.submit`;
- high-risk pending approval includes `approval_id` and `expires_at`;
- expired approval cannot be confirmed;
- cancel is denied when operator lacks `task.cancel`;
- emergency stop blocks new task execution;
- emergency stop requests cancellation of active task;
- `/state` includes emergency stop and control health.

## Research Impact

This layer is important for making FireClaw defensible as a firefighting robot agent framework.

It turns safety from a single static gate into an auditable control plane:

```text
who requested -> what was checked -> who approved -> what executed -> who cancelled/stopped
```

That audit chain is critical for robotics safety, incident replay, and publication-level evaluation of embodied-agent control.

## Scope Exclusions

This design does not implement:

- real user authentication;
- signed approvals;
- tamper-evident logs;
- ROS1 emergency-stop wiring;
- fleet-level operator routing;
- web UI;
- semantic safety reasoning.

Those are later phases.
