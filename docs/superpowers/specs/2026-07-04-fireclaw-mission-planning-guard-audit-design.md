# FireClaw Mission Planning Guard & Audit Design

**Date:** 2026-07-04
**Status:** Approved design draft
**Scope:** First version covers mission-level planning only.

## Goal

Build the first FireClaw guardrail and audit slice for mission planning, so LLM-generated mission plans are constrained by runtime robot state, checked deterministically before submission, and recorded with enough evidence to reconstruct why a mission plan was allowed or blocked.

The immediate bug this design fixes is that the LLM mission planner can theoretically fabricate robot IDs such as `robot_001` because `robot_id` is currently only a string in the tool schema and parser validation is skipped when no online robots are available.

## Non-Goals

- Do not redesign the full robot-local planning path in this phase.
- Do not replace `SafetyGate` or execution monitoring in this phase.
- Do not build a generic guard pipeline framework yet.
- Do not change ROS1 execution semantics or robot adapter APIs.
- Do not claim physical safety is solved by this phase alone.

## Current Problem

FireClaw already has several safety-related checks, but they are distributed across planner parsing, mission validation, robot-agent policy, `SafetyGate`, and execution. The mission-level LLM planner currently has three weaknesses:

1. The function-calling schema declares `robot_id` as a free string.
2. `_parse_response()` only rejects unknown robot IDs when `context.available_robots` is non-empty.
3. Planning failures do not consistently produce an audit record that captures the available robot snapshot, tool schema, LLM tool call, parser decision, and validator decision.

This is fail-safe at the later mission validator boundary, but it is not enough for a safety-critical firefighting robot system. A bad plan should be blocked close to the source and the reason should be auditable.

## Design Approach

Use a lightweight mission planning guard and audit model.

This is intentionally smaller than a generic policy pipeline. The first version creates clear data structures and helper functions for mission planning, then later phases can reuse the same shape for robot-local planning and execution safety.

The mission planning flow becomes:

```text
operator command
-> build available robot snapshot
-> preflight guard
-> build constrained function schema
-> provider.chat_completion()
-> parse tool call
-> parser guard
-> mission validator
-> audit record
-> submit or block
```

## OpenClaw Reference Pattern

OpenClaw's relevant pattern is not a direct file copy. The useful ideas are:

- construct tools from runtime context rather than hard-coding all availability;
- normalize and validate tool schemas before exposing them to the model;
- treat tool calls as untrusted structured input that must pass deterministic validation before execution.

FireClaw adapts this pattern for robotics by binding tool schema fields to live robot registry state and recording guard decisions for incident review.

## Data Structures

Add a small mission planning guard/audit module, likely under `src/fireclaw_core/mission/`.

```python
@dataclass(frozen=True)
class GuardDecision:
    layer: str
    status: str
    reason: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
```

Allowed `status` values for the first version:

- `allow`
- `block`
- `warn`

Recommended `layer` values:

- `preflight`
- `schema`
- `parser`
- `validator`

```python
@dataclass(frozen=True)
class MissionPlanningAuditRecord:
    command: str
    available_robots: list[dict[str, Any]]
    tool_schema: dict[str, Any] | None
    llm_tool_call: dict[str, Any] | None
    decisions: list[GuardDecision]
    final_status: str
    final_message: str
    created_at: str
    mission_id: str | None = None
```

The first version can store audit records through an injectable sink:

```python
class MissionPlanningAuditSink(Protocol):
    def record(self, record: MissionPlanningAuditRecord) -> None:
        ...
```

If no sink is configured, planning behavior should remain unchanged except for stricter blocking.

## Runtime Schema Constraint

Replace direct use of the static `MISSION_PLAN_TOOL` with a helper that deep-copies it and injects runtime constraints:

```python
build_constrained_mission_plan_tool(context: MissionPlannerContext) -> dict[str, Any]
```

When online robots are available, each subtask schema must constrain `robot_id`:

```json
{
  "robot_id": {
    "type": "string",
    "enum": ["gazebo_turtlebot3"]
  }
}
```

The enum should be derived from `context.available_robots`, which already represents mission-available robots after `MissionAgent` online checks.

This does not replace parser validation. It reduces invalid model outputs but remains a soft provider-level constraint because OpenAI-compatible providers vary in how strictly they enforce tool schemas.

## Preflight Guard

Before calling the model, `LLMMissionPlanner.plan()` should check whether there are any available robots.

If `context.available_robots` is empty:

- do not call the LLM provider;
- return a blocked/error mission planning result;
- record a `GuardDecision`:

```python
GuardDecision(
    layer="preflight",
    status="block",
    reason="no_available_robots",
    message="No available robots for mission planning.",
    details={"available_robot_count": 0},
)
```

This prevents a no-context LLM call from inventing a robot.

## Parser Guard

The parser should treat the LLM tool call as untrusted input.

Parser checks should include:

- missing tool call;
- wrong tool name;
- invalid `intent`;
- missing or empty `subtasks`;
- subtask item is not an object;
- missing or empty `robot_id`;
- `robot_id` not in the known available robot set;
- missing or invalid `command`;
- missing or invalid `floor`;
- missing or invalid `capability_required`;
- invalid `execution_group`.

Unknown robot ID should block regardless of whether the known robot set is empty. In practice the preflight guard should prevent an empty known set from reaching parsing, but parser logic should still be fail-closed.

For the original bug, this condition:

```python
if known_robot_ids and robot_id not in known_robot_ids:
```

should become fail-closed logic equivalent to:

```python
if robot_id not in known_robot_ids:
    block unknown_robot_id
```

## Validator Decision

After the parser constructs a `MissionPlan`, the existing deterministic mission validator should still run. Its result should be converted into a guard decision:

- validator allow: `GuardDecision(layer="validator", status="allow", reason="mission_plan_valid", ...)`
- validator block: `GuardDecision(layer="validator", status="block", reason="mission_plan_invalid", details={"errors": [...]})`

This ensures later consistency checks remain visible in the audit record instead of appearing as a disconnected final error.

## Audit Record Behavior

The audit record should capture:

- original operator command;
- available robot snapshot, including robot ID, capabilities, enabled flag, and zone if available;
- constrained tool schema sent to the provider;
- raw LLM tool call name and arguments if a provider call occurred;
- ordered guard decisions;
- final planning status and message;
- creation timestamp.

The audit record should avoid secrets and provider API keys. It may include the model's structured tool arguments because those are necessary for incident review.

If the audit sink fails, mission planning should not execute unsafe work. First version behavior should be conservative:

- if planning is already blocked, return the original blocked result and include an audit warning where possible;
- if planning would be allowed but audit persistence fails, return a blocked/error result unless the sink is explicitly configured as best-effort.

The initial implementation may use an in-memory fake sink for tests and wire persistent storage in a later phase if no existing mission memory sink fits cleanly.

## User-Facing Errors

Operator-facing messages can stay concise, but internal reasons must be stable.

Recommended reason codes:

- `no_available_robots`
- `missing_tool_call`
- `unexpected_tool_name`
- `invalid_intent`
- `empty_subtasks`
- `invalid_subtask`
- `missing_robot_id`
- `unknown_robot_id`
- `invalid_floor`
- `missing_capability`
- `mission_plan_invalid`

These codes are useful for tests, logs, and future analysis.

## Testing Strategy

Use TDD for implementation.

Required tests:

1. No available robots:
   - planner does not call the provider;
   - result is blocked/error;
   - audit record contains `preflight/no_available_robots`.

2. Dynamic schema:
   - constrained tool schema contains `robot_id.enum` with only available robot IDs.

3. Unknown robot ID:
   - provider returns `robot_001`;
   - parser blocks with `unknown_robot_id`;
   - no mission plan is returned;
   - audit record includes the raw tool call.

4. Valid robot ID:
   - provider returns `gazebo_turtlebot3`;
   - parser allows the plan;
   - validator decision is recorded.

5. Validator rejection:
   - parser succeeds but mission validator rejects;
   - final result is blocked/error;
   - audit record includes validator errors.

6. Audit contents:
   - audit record includes command, available robots, tool schema, tool call, decisions, final status, and timestamp.

## Research Impact

This phase strengthens FireClaw's research direction. The system becomes less like a simple LLM wrapper and more like an auditable embodied-agent runtime for dangerous environments.

The contribution is not merely that an LLM can plan tasks. The stronger claim is that FireClaw can constrain model-generated plans against live robot capabilities and preserve a deterministic audit trail explaining why a plan was accepted or rejected.

This is still not enough for a publication-level safety claim. Later work must extend the same guard/audit structure to robot-local planning, `SafetyGate`, physical execution, emergency stop handling, and post-incident replay.

## Future Phases

Future phases should extend the framework in this order:

1. Robot-local planning audit:
   - record `allowed_skills`, robot-local tool schema, local plan, and `RobotAgentPolicy` decision.

2. SafetyGate audit:
   - record skill safety class, required sensors, sensor health, dry-run/real-robot mode, approval requirement, and safety decision.

3. Execution audit:
   - record each physical action request, ROS endpoint, rendered payload, result, timeout, cancellation, and emergency stop state.

4. Persistent append-only audit log:
   - store records in a dedicated audit log with stable IDs and no silent mutation.

5. Replay and evaluation:
   - support replaying planning decisions for tests, ablations, and safety case analysis.
