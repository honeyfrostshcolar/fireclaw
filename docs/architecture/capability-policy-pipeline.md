# Capability Policy Pipeline

## Purpose

FireClaw must distinguish three different statements:

1. a plugin installed a capability;
2. a planner may consider that capability for this task;
3. the robot may execute this exact action now.

Those statements were previously enforced by separate checks at different
call sites. `CapabilityPolicyPipeline` gives them one ordered policy and one
audit format without moving Tool implementation or physical safety logic
into the Agent.

## OpenClaw Analogue

The structure follows OpenClaw's ordered tool-policy implementation:

- `openclaw/src/agents/tool-policy-pipeline.ts`
  - named policy stages;
  - deterministic before/after filtering;
  - per-stage diagnostics.
- `openclaw/src/agents/conversation-tool-policy-pipeline.ts`
  - one canonical stage order;
  - conversation-specific tool projection.

FireClaw preserves that pipeline shape and adds robotics-specific evidence.
Unlike a software Tool, a physical Tool must also be authorized by the
delegated task, supported by the selected robot, valid under fresh robot
state, accepted by `SafetyGate`, and covered by an exact action
authorization.

## Policy Stages

`CapabilityPolicyPipeline` evaluates these stages in order:

| Stage | Question | Typical block |
|---|---|---|
| Identity | Is the actor known and allowed to submit work? | Missing `task.submit`, or delegated operator mismatch |
| Task delegation | Does the task contract allow this Tool and these protected inputs? | LLM changes the assigned target or map |
| Plugin ownership | Does an active Plugin Host own this exact tool contribution? | Stale or unowned registry entry |
| Robot profile | Is the capability enabled and exposed for this robot? | Robot lacks the configured Tool |
| Runtime state | Can the robot support it now? | Offline, emergency stop, low battery, unavailable required sensor |
| Safety | Did the deterministic `SafetyGate` allow this action? | Hazard, forbidden real-robot action, confirmation required |
| Authorization | Is there a valid authorization for this exact action hash? | Expired token or changed parameters |

Every stage produces a `CapabilityPolicyStageDecision` with a stable stage
name, status, reason, and evidence. The final
`CapabilityPolicyDecision` is `allow`, `block`, or
`require_authorization`.

## Two Evaluation Boundaries

### Planning Projection

Before a Robot Agent model turn, the Gateway evaluates every registered
physical tool:

```text
all active Plugin Host tools
-> identity
-> task delegation
-> plugin ownership
-> robot profile
-> current robot state
-> projected tool schemas
```

Safety and exact authorization are marked deferred here because no final
action arguments may exist yet. Only tools in the projection's `after` list
are supplied to the model. The context also contains a projection manifest
with excluded tools and reasons.

Example:

```text
Installed tools: navigate_to, inspect_thermal, operate_nozzle
Task allows: navigate_to, inspect_thermal
Robot profile lacks thermal camera

LLM receives: navigate_to
Manifest excludes:
  inspect_thermal -> required sensor unavailable
  operate_nozzle  -> not delegated by the task
```

This reduces invalid model choices, but it is not execution authority.

### Execution Admission

After the model supplies an exact skill and exact arguments, FireClaw checks
the full pipeline. It first records a preflight decision. Immediately before
the executor crosses the side-effect boundary, it fetches fresh robot state
and evaluates the policy again.

Example:

```text
Plan: navigate_to {"target": {"x": 4.0, "y": 2.0}}
Authorization: approved for the same input hash until 14:05:00
At 14:04:58: emergency stop becomes active

Result: blocked by runtime-state stage
Skill handler and robot adapter are not called
```

This second check closes the time gap between planning and action. A tool
visible during planning can still be denied when the physical situation
changes.

## Relationship to Other Boundaries

The capability pipeline coordinates existing authorities; it does not replace
them:

- `FireClawPluginHost` says who owns an installed contribution and whether it
  is active.
- `ProviderAgentHarness` validates the structure of one model/tool turn.
- `CapabilityPolicyPipeline` decides whether a tool is visible or an exact
  action is admissible in the current context.
- `SafetyGate` owns deterministic physical safety decisions.
- execution authorization proves that an operator approved the exact action,
  not merely a skill name.
- `ResourceLeaseManager` acquires exclusive physical resources after policy
  admission and before side effects.
- `SkillRegistry` dispatches the already-admitted skill implementation.

Keeping these responsibilities separate means adding a new skill contributes
metadata and an implementation through the Plugin Host. It does not require a
new branch in the Agent or policy pipeline.

## Audit Contract

Planning records:

- policy ID;
- candidate tools before projection;
- exposed tools after projection;
- excluded tools and complete stage decisions.

Execution records:

- `capability.policy_preflight`;
- `capability.policy_decided`;
- stage status, reason, evidence, skill name, and normalized inputs;
- the final policy manifest returned with the Agent result.

The executor fails closed when the policy callback raises an exception or
returns any status other than `allow`.

## Trust Boundaries and Current Limits

- Planning projection uses a current state sample; execution always rechecks
  the latest locally available state.
- Unknown sensor availability is not invented as a failure. It remains
  deferred to the authoritative state and `SafetyGate`; a known unavailable
  required sensor blocks.
- Resource acquisition remains a mutating executor operation, so it is not
  represented as a pure policy stage.
- The pipeline accepts FireClaw's current `OperatorContext`. Production
  deployments still need authenticated identity and scope claims from a
  trusted control-plane boundary.
- The pipeline improves engineering correctness and incident reconstruction.
  It is infrastructure, not by itself a novel task-planning algorithm.
